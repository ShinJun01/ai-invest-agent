"""S1 Trend Pullback Signal Engine — ARCHITECTURE.md §9-1.

L3 스크리닝(engines/screen.py) 통과 후보에 대해 S1의 5개 진입조건을 확인하고
진입가/손절가/1R 목표가를 확정한다(L4).

범위 밖:
- 포지션 사이징(§8 RAER -> 주식수)은 engines/sizing.py(다음 작업) 몫
- 트레일링(2.5×ATR chandelier)·최대보유 20일 청산은 포지션이 생긴 뒤 저널이
  관리할 일이라 여기선 파라미터로만 남김(트레일링을 지금 계산하려면 진입일
  이후 고점이 필요한데 아직 진입 전이라 존재하지 않음)
- 적합 레짐은 §9-1 본표의 STRONG_BULL/BULL/NEUTRAL만 구현. §9-2가 CAUTION을
  "대형주만" 조건부 허용으로 추가하지만 시가총액 데이터가 없어 이번엔 제외
- 스윙하이/눌림목 구간 탐지: 문서가 정확한 알고리즘을 주지 않아 "최근 15거래일
  종가 최고점=스윙하이, 그 이전 10거래일=상승구간"으로 정의(판단값, 후보정 가능)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import load  # noqa: E402
from engines.screen import REPORTS_DIR, _atr_pct  # noqa: E402

APPLICABLE_REGIMES = {"STRONG_BULL", "BULL", "NEUTRAL"}
SWING_HIGH_LOOKBACK = 15   # 스윙하이를 찾는 창(거래일)
UP_LEG_LOOKBACK = 10       # 스윙하이 이전 "상승구간" 길이(거래일)
STOP_ATR_MULT = 1.5
STOP_PULLBACK_LOW_ATR_MULT = 0.3
CHANDELIER_ATR_MULT = 2.5  # 참고용 파라미터(트레일링은 포지션 생성 후 계산)
MAX_HOLD_DAYS = 20


def load_latest_candidates() -> dict:
    files = sorted(REPORTS_DIR.glob("candidates_*.json"))
    if not files:
        raise FileNotFoundError("candidates_*.json 없음 -> 먼저 engines/screen.py 실행")
    return json.loads(files[-1].read_text())


def _find_pullback_leg(close: pd.Series, volume: pd.Series):
    """최근 SWING_HIGH_LOOKBACK일 중 종가 최고점을 스윙하이로 잡고, 그 이후를
    되돌림 구간, 그 이전 UP_LEG_LOOKBACK일을 상승구간으로 정의."""
    recent = close.iloc[-SWING_HIGH_LOOKBACK:]
    swing_high_pos = recent.values.argmax()
    swing_high_idx = recent.index[swing_high_pos]
    loc = close.index.get_loc(swing_high_idx)

    pullback_leg = slice(loc, None)  # 스윙하이 포함 ~ 오늘
    up_leg_start = max(0, loc - UP_LEG_LOOKBACK)
    up_leg = slice(up_leg_start, loc + 1)

    return {
        "swing_high_date": swing_high_idx,
        "pullback_avg_volume": volume.iloc[pullback_leg].mean(),
        "up_leg_avg_volume": volume.iloc[up_leg].mean(),
        "pullback_low": close.iloc[pullback_leg].min(),
    }


def evaluate_entry_conditions(df: pd.DataFrame, rs_percentile: float) -> dict:
    """§9-1 진입조건 ①~⑤. df는 정렬된 OHLCV(오늘=T가 마지막 행)."""
    close, volume = df["close"], df["volume"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()

    leg = _find_pullback_leg(close, volume)
    pct_from_ema20 = close.iloc[-1] / ema20.iloc[-1] - 1

    conditions = {
        "c1_above_200_50ema": bool(close.iloc[-1] > ema200.iloc[-1] and close.iloc[-1] > ema50.iloc[-1]),
        "c2_rs_top20pct": bool(rs_percentile >= 80),
        "c3_pullback_to_20ema": bool(-0.01 <= pct_from_ema20 <= 0.02),
        "c4_volume_contraction": bool(leg["pullback_avg_volume"] < leg["up_leg_avg_volume"]),
        "c5_pullback_low_above_50ema": bool(leg["pullback_low"] > ema50.iloc[-1]),
    }
    conditions["all_passed"] = all(conditions.values())
    conditions["pullback_low"] = leg["pullback_low"]
    conditions["ema50"] = ema50.iloc[-1]
    return conditions


def entry_trigger(df: pd.DataFrame) -> dict:
    """진입 실행(§9-1): 되돌림 후 첫 상승 반전일 종가, 또는 전일 고가 돌파.
    오늘(T)이 반전일이면 즉시 확정, 아니면 T의 고가를 T+1 돌파 트리거로 제시."""
    close, high = df["close"], df["high"]
    is_reversal_day = bool(close.iloc[-1] > close.iloc[-2] and close.iloc[-2] <= close.iloc[-3])
    if is_reversal_day:
        return {"status": "confirmed_reversal", "entry_price": float(close.iloc[-1])}
    return {"status": "pending_breakout", "entry_price": float(high.iloc[-1])}


def build_signal(ticker: str, rs_percentile: float) -> dict | None:
    df = load(ticker).sort_index()
    if len(df) < 260:
        return None

    conditions = evaluate_entry_conditions(df, rs_percentile)
    if not conditions["all_passed"]:
        return None

    trigger = entry_trigger(df)
    atr_dollar = _atr_pct(df).iloc[-1] / 100 * df["close"].iloc[-1]
    entry_price = trigger["entry_price"]
    stop = min(entry_price - STOP_ATR_MULT * atr_dollar,
               conditions["pullback_low"] - STOP_PULLBACK_LOW_ATR_MULT * atr_dollar)
    risk_per_share = entry_price - stop
    target_1r = entry_price + risk_per_share

    return {
        "ticker": ticker,
        "strategy": "S1_TrendPullback",
        "entry_status": trigger["status"],
        "entry": round(entry_price, 2),
        "stop": round(stop, 2),
        "r_multiple_target_1": round(target_1r, 2),
        "risk_per_share": round(risk_per_share, 2),
        "chandelier_atr_multiple": CHANDELIER_ATR_MULT,
        "max_hold_days": MAX_HOLD_DAYS,
        "conditions": {k: v for k, v in conditions.items() if k.startswith("c")},
    }


def run() -> dict:
    candidates_data = load_latest_candidates()
    regime_name = candidates_data["regime"]

    if regime_name not in APPLICABLE_REGIMES:
        return {"date": candidates_data["date"], "regime": regime_name,
                "strategy": "S1_TrendPullback", "applicable": False, "signals": []}

    signals = []
    for c in candidates_data["candidates"]:
        sig = build_signal(c["ticker"], c["factor_breakdown"]["F1"])
        if sig is not None:
            sig["score"] = c["score"]
            sig["sector"] = c["sector"]
            signals.append(sig)

    return {
        "date": candidates_data["date"], "regime": regime_name,
        "strategy": "S1_TrendPullback", "applicable": True,
        "candidates_evaluated": len(candidates_data["candidates"]),
        "signals": signals,
    }


if __name__ == "__main__":
    result = run()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"signals_S1_{result['date'].replace('-', '')}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "signals"}, ensure_ascii=False, indent=2))
    print(f"signals: {len(result['signals'])} -> {out_path}")
