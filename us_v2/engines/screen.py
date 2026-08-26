"""Stock Screening Engine — ARCHITECTURE.md §5.

Stage 0 Universe -> Stage 1 Hard Gate -> Stage 2 Composite Score ->
Stage 3 Regime Filter -> Stage 4 Event Gate -> Stage 5 Ranking(+상관 제약).

범위 밖:
- entry/stop/r_multiple_target/strategy 필드(§5-6 예시에 있지만 S1 Trend Pullback
  같은 전략별 진입조건이 있어야 나옴) -> engines/signal.py(다음 작업)의 몫
- 유니버스는 S&P500(503종목)만. §5-2 목표(S&P500+NASDAQ100+MidCap400, ~850)는
  NASDAQ100/MidCap400 리스트를 아직 수집하지 않아 축소 운영 -> 다음에 확장
- Stage 3 "허용 전략의 진입조건 충족 여부"는 전략 로직이 아직 없어 레짐별 최종
  후보수 상한(§5-5)과 G5 스위치로만 구현. RISK_OFF/CRISIS는 신규 진입 금지이므로
  후보 0개 처리(§3-2)
- 실적 캘린더는 §17이 유료(Nasdaq/Finnhub/FMP)를 명시했지만 Phase 2를 막지 않으려
  yfinance 무료 조회로 대체(최선 노력, 실패 시 unknown 처리) — 문서가 예상한 정확도
  아님
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import STORE_DIR, load  # noqa: E402
from data.ingest import SECTOR_ETFS  # noqa: E402  ({ETF: GICS Sector 공식명})

REPORTS_DIR = Path(__file__).parent.parent / "reports"
MIN_HISTORY_DAYS = 260  # 252일 ROC/EMA200 계산 워밍업

FACTOR_WEIGHTS = {"F1": 0.30, "F2": 0.20, "F3": 0.15, "F4": 0.15, "F5": 0.10, "F6": 0.10}
CANDIDATE_COUNT_BY_REGIME = {
    "STRONG_BULL": 10, "BULL": 8, "NEUTRAL": 5, "CAUTION": 3, "RISK_OFF": 0, "CRISIS": 0,
}
NO_ENTRY_REGIMES = {"RISK_OFF", "CRISIS"}
CAUTIOUS_REGIMES = {"CAUTION", "RISK_OFF", "CRISIS"}  # G5를 200EMA->50EMA로 강화


# ---------- Stage 0: Universe ----------

def load_universe() -> list[str]:
    return json.loads((STORE_DIR / "sp500_constituents.json").read_text())["tickers"]


def load_sectors() -> dict[str, str]:
    return json.loads((STORE_DIR / "sp500_sectors.json").read_text())["sectors"]


def latest_regime() -> dict:
    history = pd.read_csv(REPORTS_DIR / "regime_history.csv", index_col=0, parse_dates=True)
    return history.iloc[-1].to_dict()


# ---------- 지표 계산 ----------

def _atr_pct(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()  # Wilder's ATR(14)
    return atr / df["close"] * 100


def _roc_blend(close: pd.Series) -> float:
    """F1 근거식: 0.4*ROC63 + 0.3*ROC126 + 0.3*ROC252."""
    return (0.4 * close.pct_change(63).iloc[-1]
            + 0.3 * close.pct_change(126).iloc[-1]
            + 0.3 * close.pct_change(252).iloc[-1])


def compute_ticker_snapshot(ticker: str) -> dict | None:
    try:
        df = load(ticker).sort_index()
    except FileNotFoundError:
        return None
    if len(df) < MIN_HISTORY_DAYS:
        return None

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    ema50, ema200 = close.ewm(span=50, adjust=False).mean(), close.ewm(span=200, adjust=False).mean()
    atrp = _atr_pct(df)
    up_vol = vol.where(close.diff() > 0, 0.0)
    down_vol = vol.where(close.diff() < 0, 0.0)

    return {
        "ticker": ticker,
        "close": close.iloc[-1],
        "addv20": (close * vol).rolling(20).mean().iloc[-1],
        "atr_pct": atrp.iloc[-1],
        "above_200ema": close.iloc[-1] > ema200.iloc[-1],
        "above_50ema": close.iloc[-1] > ema50.iloc[-1],
        # F1
        "rs_excess": _roc_blend(close),  # SPY 대비 초과분은 caller가 뺀다(횡단면 공통 항 상쇄)
        # F2 서브컴포넌트
        "f2_close_over_ema50": close.iloc[-1] / ema50.iloc[-1],
        "f2_ema50_over_ema200": ema50.iloc[-1] / ema200.iloc[-1],
        "f2_ema200_slope20": (ema200.iloc[-1] / ema200.iloc[-21]) - 1,
        # F3
        "pct_of_52w_high": close.iloc[-1] / close.iloc[-252:].max(),
        # F4 서브컴포넌트
        "rvol_5d_avg": (vol / vol.rolling(20).mean()).iloc[-5:].mean(),
        "updown_vol_ratio": up_vol.iloc[-20:].sum() / max(down_vol.iloc[-20:].sum(), 1.0),
        # F6
        "atr_pct_for_f6": atrp.iloc[-1],
    }


def build_snapshot_table(universe: list[str]) -> pd.DataFrame:
    rows = [r for r in (compute_ticker_snapshot(t) for t in universe) if r is not None]
    return pd.DataFrame(rows).set_index("ticker")


# ---------- Stage 1: Hard Gate ----------

def hard_gate(snap: pd.DataFrame, regime_name: str) -> pd.Series:
    """G1~G5. G5는 레짐 의존(§5-3): CAUTION 이하는 200EMA 대신 50EMA."""
    g1 = snap["addv20"] >= 30_000_000
    g2 = snap["close"] >= 10
    g3 = snap["atr_pct"] >= 1.2
    g4 = snap["atr_pct"] <= 9.0
    g5 = snap["above_50ema"] if regime_name in CAUTIOUS_REGIMES else snap["above_200ema"]
    return g1 & g2 & g3 & g4 & g5


# ---------- Stage 2: Composite Score ----------

def sector_rs_percentile(sector_etfs: dict[str, str]) -> dict[str, float]:
    """11개 섹터ETF의 RS(SPY 대비 초과)를 구해 백분위(0~100)로 변환, {GICS Sector명: 백분위}."""
    spy_roc = _roc_blend(load("SPY")["close"].sort_index())
    rs = {}
    for etf, sector_name in sector_etfs.items():
        close = load(etf)["close"].sort_index()
        rs[sector_name] = _roc_blend(close) - spy_roc
    ranked = pd.Series(rs).rank(pct=True) * 100
    return ranked.to_dict()


def _zscore(s: pd.Series) -> pd.Series:
    return (s - s.mean()) / s.std(ddof=0)


def composite_score(snap: pd.DataFrame, sectors: dict[str, str],
                     sector_pct: dict[str, float]) -> pd.DataFrame:
    df = snap.copy()
    spy_roc = _roc_blend(load("SPY")["close"].sort_index())

    f1 = _zscore(df["rs_excess"] - spy_roc)
    f2 = _zscore(df["f2_close_over_ema50"]).add(
        _zscore(df["f2_ema50_over_ema200"])).add(_zscore(df["f2_ema200_slope20"])) / 3
    f3 = _zscore(df["pct_of_52w_high"])
    f4 = _zscore(df["rvol_5d_avg"]).add(_zscore(df["updown_vol_ratio"])) / 2
    df["sector"] = df.index.map(sectors)
    f5 = _zscore(df["sector"].map(sector_pct).astype(float))
    f6 = _zscore(-df["atr_pct_for_f6"])  # 낮은 변동성일수록 고득점

    factor_z = {"F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6}
    for name, z in factor_z.items():
        df[f"{name}_display"] = z.rank(pct=True) * 100  # 화면 표시용 0~100

    composite_z = sum(FACTOR_WEIGHTS[k] * v for k, v in factor_z.items())
    df["score"] = composite_z.rank(pct=True) * 100
    return df


# ---------- Stage 4: Event Gate ----------

def _days_to_nearest_earnings(ticker: str, today: pd.Timestamp) -> float | None:
    try:
        dates = yf.Ticker(ticker).earnings_dates.index.tz_localize(None)
    except Exception:
        return None
    if len(dates) == 0:
        return None
    nearest = min(dates, key=lambda d: abs((d - today).days))
    return (nearest - today).days


def event_gate(tickers: list[str], today: pd.Timestamp, max_workers: int = 15,
                total_timeout: float = 300.0) -> tuple[pd.Series, pd.Series]:
    """T-5~T+1 배제(§6-1). 캘린더일 근사(문서는 거래일 기준) — 무료 소스라 §17이
    요구하는 유료 실적캘린더 정확도는 아님(best-effort, 조회 실패 시 통과 처리).

    yfinance는 요청 타임아웃을 걸지 않는다. 커스텀 세션을 넘겨 타임아웃을 주입해
    봤지만 그러면 yfinance의 crumb 캐시가 매번 새로 초기화되어 즉시 레이트리밋에
    걸린다(실측) — 그래서 개별 호출은 그대로 두고, 전체 대기시간에 상한만 건다.
    시간 내 응답 없는 종목은 unknown(게이트 통과) 처리."""
    days_to_earnings: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_days_to_nearest_earnings, t, today): t for t in tickers}
        done, not_done = wait(futures, timeout=total_timeout)
        for fut in done:
            days_to_earnings[futures[fut]] = fut.result()
        for fut in not_done:
            days_to_earnings[futures[fut]] = None
        pool.shutdown(wait=False, cancel_futures=True)

    blocked = {t: (d is not None and -2 <= d <= 7) for t, d in days_to_earnings.items()}
    return pd.Series(days_to_earnings, name="days_to_earnings"), pd.Series(blocked, name="event_blocked")


# ---------- Stage 5: Ranking + 상관 제약 ----------

def _correlation_ok(candidate: str, kept: list[str], returns: pd.DataFrame, threshold: float = 0.75) -> bool:
    if not kept:
        return True
    corr = returns[kept].corrwith(returns[candidate])
    return bool((corr.abs() <= threshold).all())


def _greedy_select(ranked: pd.DataFrame, returns: pd.DataFrame, max_n: int) -> list[str]:
    """§5-5 규칙(섹터 최대 3, 상관>0.75 탈락)을 점수 내림차순으로 그리디 적용.
    순수 함수 — I/O 없음, 테스트하기 쉽게 rank_and_constrain에서 분리."""
    kept: list[str] = []
    sector_counts: dict[str, int] = {}
    for ticker, row in ranked.iterrows():
        if len(kept) >= max_n:
            break
        sector = row["sector"]
        if sector_counts.get(sector, 0) >= 3:
            continue
        if not _correlation_ok(ticker, kept, returns):
            continue
        kept.append(ticker)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    return kept


def rank_and_constrain(scored: pd.DataFrame, max_n: int) -> pd.DataFrame:
    if max_n == 0 or scored.empty:
        return scored.iloc[0:0]

    ranked = scored.sort_values("score", ascending=False)
    returns = pd.DataFrame({t: load(t)["close"].sort_index().pct_change().iloc[-60:] for t in ranked.index})
    kept = _greedy_select(ranked, returns, max_n)
    return ranked.loc[kept]


# ---------- 파이프라인 ----------

def run() -> dict:
    universe = load_universe()
    sectors = load_sectors()
    regime = latest_regime()
    regime_name = regime["confirmed_regime"]
    today = pd.Timestamp(datetime.now().date())

    print(f"[1/5] {len(universe)}종목 스냅샷 계산...")
    snap = build_snapshot_table(universe)

    print("[2/5] Hard Gate...")
    passed = snap[hard_gate(snap, regime_name)]

    print(f"[3/5] Composite Score ({len(passed)}종목 통과)...")
    sector_pct = sector_rs_percentile(SECTOR_ETFS)
    scored = composite_score(passed, sectors, sector_pct)

    print("[4/5] Event Gate (실적 조회, 수 분 소요)...")
    if regime_name in NO_ENTRY_REGIMES or scored.empty:
        days_to_earnings = pd.Series(dtype=float)
        event_blocked = pd.Series(dtype=bool)
    else:
        days_to_earnings, event_blocked = event_gate(list(scored.index), today)
    scored["days_to_earnings"] = days_to_earnings
    scored["event_blocked"] = event_blocked.reindex(scored.index).fillna(False)
    eligible = scored[~scored["event_blocked"]]

    print("[5/5] Ranking + 상관/섹터 제약...")
    max_n = CANDIDATE_COUNT_BY_REGIME.get(regime_name, 0)
    final = rank_and_constrain(eligible, max_n)

    result = {
        "date": today.date().isoformat(),
        "regime": regime_name,
        "universe_size": len(universe),
        "gate_passed": len(passed),
        "event_blocked": int(scored["event_blocked"].sum()) if not scored.empty else 0,
        "candidates": [
            {
                "ticker": t, "score": round(row["score"], 1), "rank": i + 1,
                "sector": row["sector"],
                "atr_pct": round(row["atr_pct"], 2),
                "days_to_earnings": (None if pd.isna(row["days_to_earnings"])
                                      else int(row["days_to_earnings"])),
                "factor_breakdown": {
                    f: round(row[f"{f}_display"], 1) for f in FACTOR_WEIGHTS
                },
            }
            for i, (t, row) in enumerate(final.iterrows())
        ],
    }
    return result


if __name__ == "__main__":
    result = run()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"candidates_{result['date'].replace('-', '')}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "candidates"}, ensure_ascii=False, indent=2))
    print(f"candidates: {len(result['candidates'])} -> {out_path}")
