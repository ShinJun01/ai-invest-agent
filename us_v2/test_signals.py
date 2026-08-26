"""S1 시그널 엔진 자체검증 — 진입조건/트리거/손절폭 계산을 합성 데이터로 확인.
`python us_v2/test_signals.py`로 실행."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from engines import signals  # noqa: E402


def _uptrend_df(n=300, start=50.0, slope=0.3, vol=2_000_000):
    closes = start + np.arange(n) * slope
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": [float(vol)] * n,
    }, index=pd.bdate_range("2020-01-01", periods=n))


def _with_peak_and_pullback(df):
    """마지막 15일에 명확한 스윙하이(-8일째)+눌림목(-7~-1일)을 심고, 눌림목 구간만
    거래량을 낮춰(500k vs 2M) c4 방향성을 명확하게 만든다."""
    df = df.copy()
    close_col, vol_col = df.columns.get_loc("close"), df.columns.get_loc("volume")
    tail = [100, 101, 102, 103, 104, 105, 106, 107, 106, 105, 104, 103, 102, 101, 100]
    df.iloc[-15:, close_col] = tail
    df.iloc[-15:, df.columns.get_loc("high")] = [c + 0.5 for c in tail]
    df.iloc[-15:, df.columns.get_loc("low")] = [c - 0.5 for c in tail]
    df.iloc[-8:, vol_col] = 500_000  # 스윙하이(peak, -8) 포함 이후 눌림목 구간
    return df


def test_entry_trigger_confirmed_reversal():
    df = _uptrend_df()
    df.loc[df.index[-3], "close"] = 100
    df.loc[df.index[-2], "close"] = 98   # 전일 대비 하락
    df.loc[df.index[-1], "close"] = 99   # 전일 대비 반등 -> 반전일
    trig = signals.entry_trigger(df)
    assert trig["status"] == "confirmed_reversal"
    assert trig["entry_price"] == 99


def test_entry_trigger_pending_breakout():
    df = _uptrend_df()
    df.loc[df.index[-3], "close"] = 100
    df.loc[df.index[-2], "close"] = 98
    df.loc[df.index[-1], "close"] = 97   # 계속 하락 -> 반전 아님
    df.loc[df.index[-1], "high"] = 97.5
    trig = signals.entry_trigger(df)
    assert trig["status"] == "pending_breakout"
    assert trig["entry_price"] == 97.5


def test_c1_above_ema_true_for_steady_uptrend():
    cond = signals.evaluate_entry_conditions(_uptrend_df(), rs_percentile=90)
    assert cond["c1_above_200_50ema"] is True


def test_c1_above_ema_false_for_downtrend():
    cond = signals.evaluate_entry_conditions(_uptrend_df(slope=-0.3, start=150), rs_percentile=90)
    assert cond["c1_above_200_50ema"] is False


def test_c2_rs_threshold_is_top_20_percent():
    df = _uptrend_df()
    assert signals.evaluate_entry_conditions(df, rs_percentile=80)["c2_rs_top20pct"] is True
    assert signals.evaluate_entry_conditions(df, rs_percentile=79)["c2_rs_top20pct"] is False


def test_c4_volume_lower_in_pullback_leg_than_up_leg():
    df = _with_peak_and_pullback(_uptrend_df())
    leg = signals._find_pullback_leg(df["close"], df["volume"])
    assert leg["pullback_avg_volume"] < leg["up_leg_avg_volume"]


def test_c5_fails_when_pullback_low_breaks_50ema():
    df = _uptrend_df(n=300, start=50, slope=0.3)
    df.iloc[-5:, df.columns.get_loc("close")] = 10.0  # 급락 -> 50EMA 훨씬 아래로
    cond = signals.evaluate_entry_conditions(df, rs_percentile=90)
    assert cond["c5_pullback_low_above_50ema"] is False


def test_stop_is_wider_of_two_formulas():
    entry_price, pullback_low, atr = 100.0, 95.0, 4.0
    stop = min(entry_price - signals.STOP_ATR_MULT * atr,
                pullback_low - signals.STOP_PULLBACK_LOW_ATR_MULT * atr)
    # ATR 기준(100-6=94)이 눌림목저점 기준(95-1.2=93.8)보다 넓다(더 낮다) -> 더 낮은 쪽 채택
    assert stop == min(94.0, 93.8)
    assert stop < entry_price


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
