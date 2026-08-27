"""Backtest Engine — ARCHITECTURE.md §11 (MVP 범위, 아래 "범위 밖" 참고).

**결과 숫자는 참고용이지 검증된 값이 아니다.** §5-2/§17이 명시한 대로 시점별
구성종목(point-in-time)·상장폐지종목 데이터 없이는 백테스트 숫자 전체가
무효다 — 현재 시점 S&P500(503종목)으로만 돌리므로 생존편향이 있고, 문서
추정으로 연 3~5%p 과대평가된다. 이 MVP는 "체결/슬리피지/갭 모델링이 제대로
동작하는가"를 검증하기 위한 엔진 배관 확인용이며, 유료 데이터 도입 전까지는
Phase 2 완료 조건(§17 OOS 검증)을 충족하지 않는다.

범위 밖:
- 시점별 유니버스/상장폐지 종목(§5-2) — 유료 데이터 필요
- **screen.py의 G1~G5 하드게이트(유동성/가격/ATR% 밴드)와 실적 이벤트 게이트를
  historical_signals()에 이식하지 않았다** — S1 진입조건 5개(§9-1)만 히스토리
  스캔에 반영. 즉 이 백테스트는 저유동성/실적임박 종목의 신호도 그대로 트레이드로
  잡는다. 실측으로 이게 결과를 오염시키는 걸 확인함(ROK 2017-10-31 사례,
  _try_fill의 MAX_PLAUSIBLE_GAP 주석 참고) — 다음 단계에서 반드시 이식 필요
- 청산 로직 단순화: §9-1은 "1R 도달시 50% 청산 + 나머지 2.5×ATR 샹들리에 트레일링"
  이지만 이 MVP는 스톱/1R목표/20일 최대보유 중 먼저 도달하는 것으로 전량 청산한다
  (부분청산+트레일링은 포지션을 등분 관리해야 해서 추적 상태가 늘어남 — 엔진
  뼈대가 검증되면 다음 단계에서 추가)
- §11-4 과최적화 방어 프로토콜 10종(Plateau/DSR/부트스트랩/MC/랜덤대조 등)은
  별도 backtest/validators.py로 미룸 — 이 파일은 [1]~[2]단계(체결 모델링과
  거래 생성)까지만
- FOMC/CPI 이벤트 필터, S2~S5 전략은 여전히 미구현
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import STORE_DIR, load  # noqa: E402
from engines.screen import _atr_pct  # noqa: E402
from engines.signals import (  # noqa: E402
    STOP_ATR_MULT, STOP_PULLBACK_LOW_ATR_MULT, SWING_HIGH_LOOKBACK, UP_LEG_LOOKBACK,
    MAX_HOLD_DAYS, APPLICABLE_REGIMES,
)

MIN_HISTORY = 260
RS_TOP_PCT = 80

REPORTS_DIR = Path(__file__).parent.parent / "reports"
RUNS_DIR = Path(__file__).parent / "runs"

# ---------- §11-2 체결 모델 ----------
BASE_SLIPPAGE_BP = 5.0          # ±0.05%
SPREAD_LARGE_CAP_BP = 2.0       # ADDV 기준 프록시(시가총액 데이터 없음 — 알려진 근사)
SPREAD_MID_CAP_BP = 5.0
LARGE_CAP_ADDV = 100_000_000
ROUNDTRIP_COMMISSION_BP = 3.0
MAX_VOLUME_PARTICIPATION = 0.01  # 주문 <= 당일 거래량의 1%
RISK_PCT_PER_TRADE = 0.01        # RAER Full 버킷과 동일값. 이 MVP는 매 트레이드 고정 1% 사용
                                  # (engines/sizing.py의 RAER/캡 로직은 재사용하지 않음 — 단순화)
MAX_CONCURRENT_POSITIONS = 10    # STRONG_BULL 최대 후보수(§5-5)와 동일하게 맞춤


def _roc_blend_series(close: pd.Series) -> pd.Series:
    return 0.4 * close.pct_change(63) + 0.3 * close.pct_change(126) + 0.3 * close.pct_change(252)


def build_rs_rank_table(universe: list[str]) -> pd.DataFrame:
    """날짜 x 티커 RS(SPY 초과분) 횡단면 백분위(0~100). c2 진입조건 판정용."""
    spy_roc = _roc_blend_series(load("SPY")["close"].sort_index())
    rs = {}
    for t in universe:
        try:
            close = load(t)["close"].sort_index()
        except FileNotFoundError:
            continue
        rs[t] = _roc_blend_series(close) - spy_roc
    rs_df = pd.DataFrame(rs)
    return rs_df.rank(axis=1, pct=True) * 100


def scan_ticker(ticker: str, rs_rank: pd.Series) -> pd.DataFrame:
    """단일 종목 전체 히스토리에서 S1 진입조건(§9-1 ①~⑤) 통과일을 찾는다.
    engines/signals.py의 조건 정의를 성능을 위해 배열 기반으로 재구현한 것이라
    signals.py 로직이 바뀌면 이 함수도 같이 갱신해야 한다(중복 유지보수 부담,
    알려진 리스크)."""
    df = load(ticker).sort_index()
    if len(df) < MIN_HISTORY:
        return pd.DataFrame()

    close, high, volume = df["close"].to_numpy(), df["high"].to_numpy(), df["volume"].to_numpy()
    dates = df.index
    ema20 = df["close"].ewm(span=20, adjust=False).mean().to_numpy()
    ema50 = df["close"].ewm(span=50, adjust=False).mean().to_numpy()
    ema200 = df["close"].ewm(span=200, adjust=False).mean().to_numpy()
    atr_dollar = (_atr_pct(df) / 100 * df["close"]).to_numpy()
    rs_aligned = rs_rank.reindex(dates).to_numpy()

    records = []
    n = len(df)
    for t in range(MIN_HISTORY, n):
        if not (close[t] > ema200[t] and close[t] > ema50[t]):          # c1
            continue
        rs_val = rs_aligned[t]
        if np.isnan(rs_val) or rs_val < RS_TOP_PCT:                     # c2
            continue
        pct_from_ema20 = close[t] / ema20[t] - 1
        if not (-0.01 <= pct_from_ema20 <= 0.02):                       # c3
            continue

        win_start = max(0, t - SWING_HIGH_LOOKBACK + 1)
        peak_idx = win_start + int(np.argmax(close[win_start:t + 1]))
        up_leg_start = max(0, peak_idx - UP_LEG_LOOKBACK)
        if peak_idx <= up_leg_start:
            continue
        pullback_avg_vol = volume[peak_idx:t + 1].mean()
        up_leg_avg_vol = volume[up_leg_start:peak_idx].mean()
        if not (pullback_avg_vol < up_leg_avg_vol):                     # c4
            continue

        pullback_low = close[peak_idx:t + 1].min()
        if not (pullback_low > ema50[t]):                               # c5
            continue

        if t < 2:
            continue
        is_reversal = close[t] > close[t - 1] and close[t - 1] <= close[t - 2]
        status = "confirmed_reversal" if is_reversal else "pending_breakout"
        trigger_price = float(close[t]) if is_reversal else float(high[t])

        stop = min(trigger_price - STOP_ATR_MULT * atr_dollar[t],
                   pullback_low - STOP_PULLBACK_LOW_ATR_MULT * atr_dollar[t])
        risk_per_share = trigger_price - stop
        if risk_per_share <= 0:
            continue

        records.append({
            "ticker": ticker, "signal_date": dates[t], "status": status,
            "trigger_price": trigger_price, "stop": stop,
            "target_1r": trigger_price + risk_per_share, "risk_per_share": risk_per_share,
        })

    return pd.DataFrame(records)


def historical_signals(universe: list[str], regime_history: pd.DataFrame) -> pd.DataFrame:
    """전 종목 스캔 + 레짐 게이팅(§9-2: S1은 STRONG_BULL/BULL/NEUTRAL만)."""
    rs_rank = build_rs_rank_table(universe)
    frames = [scan_ticker(t, rs_rank[t]) for t in universe if t in rs_rank.columns]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    signals = pd.concat(frames, ignore_index=True)

    regime_by_date = regime_history["confirmed_regime"]
    signals["regime"] = signals["signal_date"].map(regime_by_date)
    signals = signals[signals["regime"].isin(APPLICABLE_REGIMES)].reset_index(drop=True)
    return signals


# ---------- 체결 + 포지션 관리 ----------

def _slippage_frac(notional: float, addv: float) -> float:
    """§11-2: ±0.05% + 0.5×(주문금액/ADDV)×100bp, 최소 5bp."""
    if addv <= 0:
        return BASE_SLIPPAGE_BP / 10_000
    impact_bp = 0.5 * (notional / addv) * 100
    return max(BASE_SLIPPAGE_BP, BASE_SLIPPAGE_BP + impact_bp) / 10_000


def _spread_bp(addv: float) -> float:
    return SPREAD_LARGE_CAP_BP if addv >= LARGE_CAP_ADDV else SPREAD_MID_CAP_BP


MAX_PLAUSIBLE_GAP = 0.15  # 이 이상 벌어진 시가는 데이터 이상/미모델링 이벤트로 보고 스킵


def _try_fill(sig: dict, next_day: pd.Series, addv: float) -> tuple[float, bool] | None:
    """T+1 시가 체결(§11-2). pending_breakout은 당일 고가가 트리거를 넘어야 체결.
    갭 처리: 시가가 이미 트리거/손절을 관통했으면 시가에 체결(트리거가 아니라).

    MAX_PLAUSIBLE_GAP 가드: 실측 중 ROK(2017-10-31)가 전일 대비 +12.7% 갭으로 열려
    20일 뒤 원래 수준으로 되돌아오며 -6.5R을 만든 사례를 발견했다. 미조정 배당/분할
    같은 데이터 결함일 수도, 이 스캐너가 아직 반영 안 한 실적 이벤트일 수도 있다
    (이 파일은 screen.py의 G1~G5 하드게이트·실적 이벤트 게이트를 히스토리 스캔에
    이식하지 않은 상태 — 알려진 축소 범위, 모듈 docstring 참고). 둘 다 이 MVP 체결
    모델이 다루도록 설계되지 않은 케이스라 통계를 오염시키지 않도록 스킵한다."""
    open_, high = next_day["open"], next_day["high"]
    if sig["status"] == "pending_breakout" and high < sig["trigger_price"]:
        return None  # 돌파 미확인 -> 체결 안 됨(익일로 이월하지 않음, 단순화)

    raw_fill = max(sig["trigger_price"], open_) if sig["status"] == "pending_breakout" else open_
    if abs(open_ / sig["trigger_price"] - 1) > MAX_PLAUSIBLE_GAP:
        return None

    notional_est = raw_fill * 100  # 슬리피지 계산용 개략 주문금액(실제 수량 확정 전 근사)
    slip = _slippage_frac(notional_est, addv)
    spread = _spread_bp(addv) / 10_000
    fill_price = raw_fill * (1 + slip + spread)
    return fill_price, True


def _check_exit(pos: dict, day: pd.Series, holding_days: int) -> tuple[float, str] | None:
    """스톱/1R목표/최대보유 중 먼저 도달하는 것으로 전량 청산(MVP 단순화, 모듈 docstring 참고).
    같은 날 스톱·목표가 둘 다 닿을 수 있으면 보수적으로 스톱을 우선한다."""
    if day["open"] <= pos["stop"]:
        return day["open"], "gap_through_stop"
    if day["low"] <= pos["stop"]:
        return pos["stop"], "stop"
    if day["high"] >= pos["target_1r"]:
        return pos["target_1r"], "target_1r"
    if holding_days >= MAX_HOLD_DAYS:
        return day["close"], "max_hold"
    return None


def simulate(signals: pd.DataFrame, initial_capital: float = 100_000.0) -> dict:
    """일별 순차 시뮬레이션: 청산 체크 -> 신규 진입 -> 미실현손익 마감(시가평가)."""
    tickers = sorted(set(signals["ticker"]) | {"SPY"})
    prices = {t: load(t).sort_index() for t in tickers}
    trading_days = prices["SPY"].index

    addv = {t: (df["close"] * df["volume"]).rolling(20).mean() for t, df in prices.items()}
    signals_by_prev_day: dict[pd.Timestamp, list[dict]] = {}
    for _, row in signals.iterrows():
        sig_date = row["signal_date"]
        pos = trading_days.searchsorted(sig_date)
        if pos + 1 >= len(trading_days):
            continue
        exec_day = trading_days[pos + 1]
        signals_by_prev_day.setdefault(exec_day, []).append(row.to_dict())

    equity_realized = initial_capital
    open_positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for day in trading_days:
        # 1) 기존 포지션 청산 체크
        for ticker in list(open_positions):
            if day not in prices[ticker].index:
                continue
            pos = open_positions[ticker]
            if day <= pos["entry_date"]:
                continue
            row = prices[ticker].loc[day]
            holding_days = trading_days.searchsorted(day) - trading_days.searchsorted(pos["entry_date"])
            exit_info = _check_exit(pos, row, holding_days)
            if exit_info:
                exit_price_raw, reason = exit_info
                a = addv[ticker].get(day, np.nan)
                slip = _slippage_frac(exit_price_raw * pos["shares"], a if pd.notna(a) else 0)
                exit_price = exit_price_raw * (1 - slip)  # 매도는 불리한 방향(더 낮게)
                pnl = (exit_price - pos["entry_price"]) * pos["shares"]
                pnl -= exit_price * pos["shares"] * (ROUNDTRIP_COMMISSION_BP / 10_000)
                r_multiple = (exit_price - pos["entry_price"]) / pos["risk_per_share"]
                equity_realized += pnl
                trades.append({
                    "ticker": ticker, "entry_date": pos["entry_date"], "exit_date": day,
                    "entry_price": pos["entry_price"], "exit_price": exit_price,
                    "shares": pos["shares"], "pnl": pnl, "r_multiple": r_multiple,
                    "holding_days": holding_days, "exit_reason": reason,
                })
                del open_positions[ticker]

        # 2) 신규 진입
        for sig in signals_by_prev_day.get(day, []):
            ticker = sig["ticker"]
            if ticker in open_positions or len(open_positions) >= MAX_CONCURRENT_POSITIONS:
                continue
            if ticker not in prices or day not in prices[ticker].index:
                continue
            a = addv[ticker].get(day, np.nan)
            a = a if pd.notna(a) else 0
            fill = _try_fill(sig, prices[ticker].loc[day], a)
            if fill is None:
                continue
            fill_price, _ = fill
            # 1R(리스크 단위)은 신호 시점 계획값(ATR 기반, sig["risk_per_share"])을 그대로
            # 쓴다 — 체결가 기준으로 다시 재는(fill_price - stop) 방식은 한 번 시도해봤는데,
            # 갭이 커서 체결가가 손절가 바로 근처로 떨어지면 리스크 단위가 0에 가까워지고
            # R배수가 폭발하는 버그가 생겼다(실측: ELV 2022-01-06, -28.7R). 목표가만 실제
            # 체결가 기준으로 옮기고("실제 진입가에서 1R만큼"), 리스크 단위 자체는 안정된
            # 계획값으로 고정해야 사이징도 R배수도 정상 범위에 머문다.
            risk_per_share = sig["risk_per_share"]
            target_1r = fill_price + risk_per_share
            shares = int((equity_realized * RISK_PCT_PER_TRADE) // risk_per_share)
            vol_cap = int(prices[ticker].loc[day, "volume"] * MAX_VOLUME_PARTICIPATION)
            shares = max(0, min(shares, vol_cap))
            if shares == 0:
                continue
            open_positions[ticker] = {
                "entry_date": day, "entry_price": fill_price, "stop": sig["stop"],
                "target_1r": target_1r, "risk_per_share": risk_per_share,
                "shares": shares,
            }

        # 3) 시가평가 자본곡선
        unrealized = sum(
            pos["shares"] * (prices[t].loc[day, "close"] - pos["entry_price"])
            for t, pos in open_positions.items() if day in prices[t].index
        )
        equity_curve.append({"date": day, "equity": equity_realized + unrealized})

    return {
        "trades": pd.DataFrame(trades),
        "equity_curve": pd.DataFrame(equity_curve).set_index("date"),
        "initial_capital": initial_capital,
    }
