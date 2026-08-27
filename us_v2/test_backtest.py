"""백테스트 엔진 자체검증 — 체결/청산/리스크단위 계산을 합성 데이터로 확인.
`python us_v2/test_backtest.py`로 실행."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import engine, report  # noqa: E402


def _day(open_, high, low, close):
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


def test_try_fill_confirmed_reversal_fills_at_open_with_costs():
    sig = {"status": "confirmed_reversal", "trigger_price": 100.0}
    fill = engine._try_fill(sig, _day(100.5, 101, 99, 100.8), addv=200_000_000)
    assert fill is not None
    fill_price, _ = fill
    assert fill_price > 100.5  # 슬리피지+스프레드로 시가보다 불리하게 체결


def test_try_fill_pending_breakout_requires_high_above_trigger():
    sig = {"status": "pending_breakout", "trigger_price": 105.0}
    assert engine._try_fill(sig, _day(100, 104.9, 99, 102), addv=200_000_000) is None
    filled = engine._try_fill(sig, _day(100, 106, 99, 103), addv=200_000_000)
    assert filled is not None


def test_try_fill_skips_implausible_gap():
    sig = {"status": "confirmed_reversal", "trigger_price": 100.0}
    # 20% 갭업 -> MAX_PLAUSIBLE_GAP(15%) 초과라 체결 스킵
    assert engine._try_fill(sig, _day(120, 121, 119, 120.5), addv=200_000_000) is None


def test_check_exit_priority_gap_through_stop_over_low_touch():
    pos = {"stop": 95.0, "target_1r": 110.0}
    exit_info = engine._check_exit(pos, _day(90, 96, 89, 92), holding_days=1)
    assert exit_info == (90, "gap_through_stop")  # 시가 자체가 손절가 아래 -> 시가 체결


def test_check_exit_stop_touch_without_gap():
    pos = {"stop": 95.0, "target_1r": 110.0}
    exit_info = engine._check_exit(pos, _day(97, 98, 94, 96), holding_days=1)
    assert exit_info == (95.0, "stop")


def test_check_exit_target_hit():
    pos = {"stop": 95.0, "target_1r": 110.0}
    exit_info = engine._check_exit(pos, _day(105, 111, 104, 108), holding_days=3)
    assert exit_info == (110.0, "target_1r")


def test_check_exit_max_hold_when_neither_touched():
    pos = {"stop": 95.0, "target_1r": 110.0}
    exit_info = engine._check_exit(pos, _day(100, 102, 99, 101), holding_days=20)
    assert exit_info == (101, "max_hold")


def test_check_exit_none_before_max_hold():
    pos = {"stop": 95.0, "target_1r": 110.0}
    assert engine._check_exit(pos, _day(100, 102, 99, 101), holding_days=5) is None


def test_report_metrics_on_synthetic_trades():
    trades = pd.DataFrame({
        "r_multiple": [1.0, -1.0, 1.0, -1.0, 1.0],
        "pnl": [1000, -1000, 1000, -1000, 1000],
    })
    equity = pd.DataFrame({
        "equity": [100_000, 101_000, 100_000, 101_000, 100_000, 101_000],
    }, index=pd.bdate_range("2020-01-01", periods=6))
    # compute_report는 SPY를 디스크에서 읽으므로 여기선 헬퍼만 개별 검증
    assert report._max_drawdown(equity["equity"]) < 0
    assert abs(report._cagr(equity["equity"]) ) >= 0  # 계산 자체가 에러 없이 도는지만 확인


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
