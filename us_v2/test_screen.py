"""스크리닝 엔진 자체검증 — Hard Gate 경계, ATR 계산, 섹터/상관 제약을 합성 데이터로 확인.
`python us_v2/test_screen.py`로 실행."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from engines import screen  # noqa: E402


def _snap_row(addv20=50_000_000, close=50.0, atr_pct=3.0, above_200ema=True, above_50ema=True):
    return {"addv20": addv20, "close": close, "atr_pct": atr_pct,
            "above_200ema": above_200ema, "above_50ema": above_50ema}


def test_hard_gate_g1_liquidity():
    snap = pd.DataFrame([_snap_row(addv20=29_000_000), _snap_row(addv20=31_000_000)])
    passed = screen.hard_gate(snap, "BULL")
    assert list(passed) == [False, True]


def test_hard_gate_g2_price_floor():
    snap = pd.DataFrame([_snap_row(close=9.99), _snap_row(close=10.0)])
    assert list(screen.hard_gate(snap, "BULL")) == [False, True]


def test_hard_gate_g3_g4_volatility_band():
    snap = pd.DataFrame([_snap_row(atr_pct=1.0), _snap_row(atr_pct=1.2),
                          _snap_row(atr_pct=9.0), _snap_row(atr_pct=9.1)])
    assert list(screen.hard_gate(snap, "BULL")) == [False, True, True, False]


def test_hard_gate_g5_switches_with_regime():
    snap = pd.DataFrame([_snap_row(above_200ema=False, above_50ema=True)])
    # 정상 레짐: 200EMA 기준 -> 탈락
    assert list(screen.hard_gate(snap, "BULL")) == [False]
    # CAUTION 이하: 50EMA로 완화 -> 통과
    assert list(screen.hard_gate(snap, "CAUTION")) == [True]


def test_atr_pct_matches_hand_calculation():
    # 고정폭 true range 10일 연속 -> Wilder ATR이 수렴하는 근사값 확인
    df = pd.DataFrame({
        "high": [110.0] * 30, "low": [100.0] * 30,
        "close": [105.0] * 30, "volume": [1_000_000] * 30,
    })
    atrp = screen._atr_pct(df)
    # true range가 항상 10(=high-low, 이전 종가와 갭 없음)이므로 ATR도 10에 수렴 -> 10/105*100
    assert abs(atrp.iloc[-1] - (10 / 105 * 100)) < 0.5


def test_greedy_select_respects_sector_cap():
    ranked = pd.DataFrame({
        "score": [90, 85, 80, 75, 70],
        "sector": ["Tech", "Tech", "Tech", "Tech", "Health Care"],
    }, index=["A", "B", "C", "D", "E"])
    returns = pd.DataFrame(
        np.random.default_rng(0).normal(size=(60, 5)), columns=ranked.index
    )
    kept = screen._greedy_select(ranked, returns, max_n=5)
    tech_count = sum(1 for t in kept if ranked.loc[t, "sector"] == "Tech")
    assert tech_count <= 3
    assert "E" in kept  # Tech 4번째(D)는 섹터캡에 걸리고 Health Care는 통과해야 함


def test_greedy_select_drops_highly_correlated_pair():
    idx = pd.date_range("2026-01-01", periods=60)
    base = pd.Series(np.random.default_rng(1).normal(size=60), index=idx)
    returns = pd.DataFrame({
        "A": base, "B": base + np.random.default_rng(2).normal(0, 0.001, 60),  # A와 거의 동일
        "C": np.random.default_rng(3).normal(size=60),
    })
    ranked = pd.DataFrame({"score": [90, 85, 80], "sector": ["S1", "S2", "S3"]}, index=["A", "B", "C"])
    kept = screen._greedy_select(ranked, returns, max_n=3)
    assert "A" in kept
    assert "B" not in kept  # A와 상관 > 0.75라 탈락
    assert "C" in kept


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
