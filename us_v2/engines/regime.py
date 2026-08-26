"""Market Regime Detection System — ARCHITECTURE.md §3.

4개 Pillar(Trend/Breadth/Volatility/RiskAppetite) 0~25점씩 합산 0~100점 ->
6단계 레짐. 2일 확인 규칙 + CRISIS 3일 회복 규칙 + SPY 급락 오버라이드까지 포함.
전일 종가 확정 데이터만 쓴다(look-ahead 차단) — 오늘 행의 점수는 그 날 종가
기준이므로 "당일 신호"로 쓰려면 다음 거래일에 적용해야 한다(브리핑 단계 책임).

미구현(§6 매크로 이벤트 캘린더 필요, 다음 세션): VIX 1일 +40% 신규진입 금지는
계산해서 컬럼으로 남기지만, FOMC/CPI 발표일 오버라이드는 이벤트 캘린더 소스가
없어 이번 작업 범위 밖 — brief 엔진 작업 때 추가.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import STORE_DIR, load  # noqa: E402

REPORTS_DIR = Path(__file__).parent.parent / "reports"

REGIME_PARAMS = {
    # 레짐: (총노출상한, 종목당상한, 허용전략)
    "STRONG_BULL": (1.00, 0.15, ["Momentum", "Breakout", "Pullback", "RS"]),
    "BULL":        (0.80, 0.12, ["Momentum", "Breakout", "Pullback", "RS"]),
    "NEUTRAL":     (0.50, 0.08, ["Pullback", "RS", "SectorRotation"]),
    "CAUTION":     (0.25, 0.05, ["Pullback_LargeCapOnly", "MeanReversion_Small"]),
    "RISK_OFF":    (0.10, 0.05, []),  # 기존 보유만, 청산 전용
    "CRISIS":      (0.00, 0.00, []),
}
REGIME_ORDER = ["CRISIS", "RISK_OFF", "CAUTION", "NEUTRAL", "BULL", "STRONG_BULL"]


def _load_close(ticker: str) -> pd.Series:
    return load(ticker)["close"].sort_index()


def _pillar_a_trend(spy: pd.Series, qqq: pd.Series) -> pd.Series:
    dma200 = spy.rolling(200).mean()
    dma50 = spy.rolling(50).mean()
    slope50 = dma50 - dma50.shift(20)
    spy_ret20 = spy.pct_change(20)
    qqq_ret20 = qqq.pct_change(20)

    score = (
        (spy > dma200).astype(int) * 7
        + (spy > dma50).astype(int) * 5
        + (dma50 > dma200).astype(int) * 5
        + (slope50 > 0).astype(int) * 4
        + (qqq_ret20 > spy_ret20).astype(int) * 4
    )
    return score.rename("pillar_A")


def _load_constituent_signals(constituents: list[str], trading_days: pd.DatetimeIndex):
    """구성종목별 (종가>50DMA, 종가>200DMA, 252일 신고가, 252일 신저가)를
    각 종목 고유 인덱스에서 계산한 뒤 공통 캘린더로 정렬. 결측은 최대 3거래일만
    ffill(단기 결측 완충), 그 이상은 NaN으로 남겨 skipna 집계에서 자연히 제외."""
    above50, above200, is_high, is_low = {}, {}, {}, {}
    for t in constituents:
        try:
            s = _load_close(t)
        except FileNotFoundError:
            continue
        dma50 = s.rolling(50).mean()
        dma200 = s.rolling(200).mean()
        roll_max = s.rolling(252, min_periods=252).max()
        roll_min = s.rolling(252, min_periods=252).min()
        above50[t] = (s > dma50).reindex(trading_days).ffill(limit=3)
        above200[t] = (s > dma200).reindex(trading_days).ffill(limit=3)
        is_high[t] = (s >= roll_max).reindex(trading_days).ffill(limit=3)
        is_low[t] = (s <= roll_min).reindex(trading_days).ffill(limit=3)
    return (pd.DataFrame(above50), pd.DataFrame(above200),
            pd.DataFrame(is_high), pd.DataFrame(is_low))


def _tier(pct: pd.Series, hi_cut: float, mid_cut: float, hi_score: float, mid_score: float) -> pd.Series:
    return pd.Series(
        np.select([pct >= hi_cut, pct >= mid_cut], [hi_score, mid_score], default=0.0),
        index=pct.index,
    )


def _pillar_b_breadth(spy: pd.Series, rsp: pd.Series, constituents: list[str],
                       trading_days: pd.DatetimeIndex) -> pd.Series:
    above50, above200, is_high, is_low = _load_constituent_signals(constituents, trading_days)
    pct_above_50 = above50.mean(axis=1, skipna=True)
    pct_above_200 = above200.mean(axis=1, skipna=True)
    nh_nl_5d = (is_high.sum(axis=1) - is_low.sum(axis=1)).rolling(5).mean()

    b1 = _tier(pct_above_50, 0.60, 0.40, 8, 4)
    b2 = _tier(pct_above_200, 0.55, 0.35, 6, 3)
    b3 = (nh_nl_5d > 0).astype(int) * 6
    ratio = rsp / spy
    b4 = (ratio.pct_change(20) >= 0).astype(int) * 5

    return (b1 + b2 + b3 + b4).rename("pillar_B")


def _pillar_c_volatility(spy: pd.Series, vix: pd.Series, vix9d: pd.Series, vix3m: pd.Series):
    vix_pct = vix.rolling(252, min_periods=252).apply(lambda w: (w <= w[-1]).mean(), raw=True)
    vix93_ratio = vix9d / vix3m
    realized_vol20 = spy.pct_change().rolling(20).std() * np.sqrt(252)

    c1 = pd.Series(np.select(
        [vix_pct <= 0.40, vix_pct <= 0.70, vix_pct <= 0.90], [10, 5, 2], default=0.0
    ), index=vix.index)
    c2 = pd.Series(np.select(
        [vix93_ratio < 0.95, vix93_ratio < 1.00], [8, 4], default=0.0
    ), index=vix.index)
    c3 = pd.Series(np.select(
        [realized_vol20 < 0.18, realized_vol20 < 0.28], [7, 3], default=0.0
    ), index=spy.index)

    pillar_c = (c1 + c2 + c3).rename("pillar_C")
    return pillar_c, vix_pct, vix93_ratio


def _pillar_d_risk_appetite(spy: pd.Series, iwm: pd.Series, hyg: pd.Series,
                             ief: pd.Series, tnx: pd.Series, dxy: pd.Series) -> pd.Series:
    hyg_ief = hyg / ief
    hyg_ief_dma50 = hyg_ief.rolling(50).mean()
    d1 = (hyg_ief > hyg_ief_dma50).astype(int) * 9

    iwm_rel20 = iwm.pct_change(20) - spy.pct_change(20)
    d2 = (iwm_rel20 >= -0.01).astype(int) * 6

    # ^TNX는 수익률(%)*10으로 표기 (예: 45.6 = 4.56%) -> bp 변화 = 값 변화 * 10
    tnx_bp_chg20 = (tnx - tnx.shift(20)) * 10
    d3 = (tnx_bp_chg20 < 30).astype(int) * 6

    dxy_dma50 = dxy.rolling(50).mean()
    d4 = (dxy < dxy_dma50).astype(int) * 4

    return (d1 + d2 + d3 + d4).rename("pillar_D")


def classify_raw(total: float, breadth: float, vix_pct: float, vix93_ratio: float) -> str:
    if pd.isna(total) or pd.isna(breadth) or pd.isna(vix_pct) or pd.isna(vix93_ratio):
        return "UNKNOWN"
    if total < 15 or vix_pct > 0.95 or vix93_ratio > 1.10:
        return "CRISIS"
    if total >= 80:
        return "STRONG_BULL" if breadth >= 18 else "BULL"
    if total >= 65:
        return "BULL"
    if total >= 45:
        return "NEUTRAL"
    if total >= 30:
        return "CAUTION"
    return "RISK_OFF"


def _apply_confirmation_and_overrides(df: pd.DataFrame) -> pd.Series:
    """§3-0 2일 확인 규칙 + §3-2 CRISIS 3일 회복 규칙 + SPY -6%/5d 오버라이드.
    상태를 갖는 순차 로직이라 벡터화하지 않고 단순 루프로 구현(가독성 우선,
    ~3천 행이라 성능 문제 없음)."""
    confirmed = []
    prev_confirmed = "UNKNOWN"
    prev_raw = None
    pending_raw = None
    pending_streak = 0
    crisis_recovery_streak = 0

    for _, row in df.iterrows():
        raw = row["raw_regime"]

        if raw == prev_raw:
            pending_streak += 1
        else:
            pending_raw, pending_streak = raw, 1
        prev_raw = raw

        # CRISIS 탈출만 특별 규칙(총점>=45가 3거래일 연속). CRISIS 진입 자체는 §3-2 정의표의
        # 여섯 밴드 중 하나일 뿐이라 다른 전환과 똑같이 2일 확인 규칙을 따른다 — "총점 무관
        # 강제 적용" 오버라이드 목록(§3-2)에 있는 건 탈출 규칙(4번)이지 진입 면제가 아니다.
        if prev_confirmed == "CRISIS":
            crisis_recovery_streak = crisis_recovery_streak + 1 if row["total"] >= 45 else 0
            if crisis_recovery_streak >= 3:
                new_confirmed = raw
                crisis_recovery_streak = 0
            else:
                new_confirmed = "CRISIS"
        elif pending_streak >= 2:
            new_confirmed = pending_raw
            crisis_recovery_streak = 0
        else:
            new_confirmed = prev_confirmed if prev_confirmed != "UNKNOWN" else raw

        # SPY 5거래일 -6% 오버라이드: 총점 무관 강제 적용, 최소 CAUTION까지 강등
        if row["spy_5d_return"] <= -0.06:
            if REGIME_ORDER.index(new_confirmed) > REGIME_ORDER.index("CAUTION"):
                new_confirmed = "CAUTION"

        confirmed.append(new_confirmed)
        prev_confirmed = new_confirmed

    return pd.Series(confirmed, index=df.index, name="confirmed_regime")


def compute_regime_history(constituents: list[str] | None = None) -> pd.DataFrame:
    spy = _load_close("SPY")
    trading_days = spy.index  # SPY 거래일을 공통 캘린더로 삼는다 (V7과 동일한 원칙)

    # 소스가 제각각이라(yfinance/CBOE) 시작일·결측일이 다름 -> 전부 SPY 캘린더로 정렬
    qqq, vix, tnx, dxy, rsp, hyg, ief, iwm = (
        _load_close(t).reindex(trading_days).ffill(limit=3)
        for t in ["QQQ", "VIX", "TNX", "DXY", "RSP", "HYG", "IEF", "IWM"]
    )
    vix9d = _load_close("VIX9D").reindex(trading_days).ffill(limit=3)
    vix3m = _load_close("VIX3M").reindex(trading_days).ffill(limit=3)

    if constituents is None:
        import json
        constituents = json.loads((STORE_DIR / "sp500_constituents.json").read_text())["tickers"]

    pillar_a = _pillar_a_trend(spy, qqq)
    pillar_b = _pillar_b_breadth(spy, rsp, constituents, trading_days)
    pillar_c, vix_pct, vix93_ratio = _pillar_c_volatility(spy, vix, vix9d, vix3m)
    pillar_d = _pillar_d_risk_appetite(spy, iwm, hyg, ief, tnx, dxy)

    df = pd.DataFrame({
        "pillar_A": pillar_a, "pillar_B": pillar_b, "pillar_C": pillar_c, "pillar_D": pillar_d,
    })
    df["total"] = df[["pillar_A", "pillar_B", "pillar_C", "pillar_D"]].sum(axis=1, min_count=4)
    df["vix_pct"] = vix_pct
    df["vix93_ratio"] = vix93_ratio
    df["spy_5d_return"] = spy.pct_change(5)
    df["vix_1d_change"] = vix.pct_change(1)
    df["entry_blocked_vix_spike"] = df["vix_1d_change"] >= 0.40

    df["raw_regime"] = [
        classify_raw(t, b, v, r) for t, b, v, r in
        zip(df["total"], df["pillar_B"], df["vix_pct"], df["vix93_ratio"])
    ]
    df["confirmed_regime"] = _apply_confirmation_and_overrides(df)

    return df.dropna(subset=["pillar_A", "pillar_B", "pillar_C", "pillar_D"])


if __name__ == "__main__":
    print("레짐 히스토리 계산 중 (S&P500 구성종목 breadth 포함, 수 분 소요)...")
    history = compute_regime_history()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / "regime_history.csv"
    history.to_csv(out_path)
    print(f"{len(history)}행 -> {out_path}")
    print(history["confirmed_regime"].value_counts())
