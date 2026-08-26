"""V1~V7 게이트 자체검증 — 합성 데이터로 pass/fail 케이스를 확인한다.
프레임워크 없이 assert 기반. `python us_v2/test_validate.py`로 실행."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from data import validate  # noqa: E402


def _ohlcv(closes: list[float], start="2026-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000] * len(closes),
    }, index=idx)


def test_v1_schema():
    ok = validate.v1_schema("T", _ohlcv([100, 101, 102]))
    assert ok.passed

    bad = _ohlcv([100, 101, 102]).drop(columns=["volume"])
    result = validate.v1_schema("T", bad)
    assert not result.passed


def test_v3_range():
    ok = validate.v3_range("T", _ohlcv([100, 101, 102]))
    assert ok.passed

    df = _ohlcv([100, -5, 102])
    assert not validate.v3_range("T", df).passed

    df = _ohlcv([100, 101, 102])
    df.loc[df.index[0], "high"] = df.loc[df.index[0], "low"] - 1
    assert not validate.v3_range("T", df).passed

    vix = _ohlcv([15, 200, 16])
    assert not validate.v3_range("VIX", vix).passed
    assert validate.v3_range("AAPL", vix).passed  # VIX 범위는 VIX류 종목에만 적용


def test_v4_continuity_is_non_blocking_flag():
    spike = _ohlcv([100, 140, 141])  # 100 -> 140 = 40% 급등
    result = validate.v4_continuity("T", spike)
    assert not result.passed
    assert result.blocking is False  # 실패해도 HALT를 막지 않는 참고용 플래그

    normal = _ohlcv([100, 101, 99, 102])
    assert validate.v4_continuity("T", normal).passed


def test_v2_freshness():
    trading_days = pd.DatetimeIndex(["2026-08-24", "2026-08-25"])
    ok = validate.v2_freshness("T", "2026-08-25", trading_days)
    assert ok.passed

    stale = validate.v2_freshness("T", "2026-08-20", trading_days)
    assert not stale.passed


def test_v6_coverage():
    expected = [f"T{i}" for i in range(100)]
    manifest_full = {t: {} for t in expected}
    assert validate.v6_coverage(expected, manifest_full).passed

    manifest_sparse = {t: {} for t in expected[:50]}  # 50% 커버리지
    assert not validate.v6_coverage(expected, manifest_sparse).passed


def test_v7_calendar():
    trading_days = pd.bdate_range("2026-07-01", "2026-08-25")
    full = _ohlcv([100] * len(trading_days), start="2026-07-01")
    full.index = trading_days
    assert validate.v7_calendar("T", full, trading_days).passed

    gappy = full.iloc[::2]  # 이틀에 한 번씩만 데이터 존재
    assert not validate.v7_calendar("T", gappy, trading_days).passed


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
