"""사이징 엔진 자체검증 — RAER 계산, 버킷 경계, 캡 축소, 순차배분을 합성 데이터로 확인.
`python us_v2/test_sizing.py`로 실행."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engines import sizing  # noqa: E402


def test_volatility_adjustment_bounds():
    assert sizing.volatility_adjustment(0.01) == sizing.VOL_ADJ_MAX  # 극저변동 -> 상한 클립
    assert sizing.volatility_adjustment(1.0) == sizing.VOL_ADJ_MIN   # 극고변동 -> 하한 클립
    assert abs(sizing.volatility_adjustment(0.20) - 1.0) < 1e-9      # 목표변동성 그대로


def test_event_risk_multiplier_tiers():
    assert sizing.event_risk_multiplier(None) == 1.0
    assert sizing.event_risk_multiplier(3) == 0.0     # T-5~T+1(근사) 내부
    assert sizing.event_risk_multiplier(-2) == 0.0
    assert sizing.event_risk_multiplier(8) == 0.7      # 실적 6~10일전
    assert sizing.event_risk_multiplier(15) == 1.0     # 충분히 먼 미래


def test_raer_bucket_boundaries():
    assert sizing.raer_bucket(75)[0] == "Full"
    assert sizing.raer_bucket(74.9)[0] == "Standard"
    assert sizing.raer_bucket(60)[0] == "Standard"
    assert sizing.raer_bucket(59.9)[0] == "Half"
    assert sizing.raer_bucket(45)[0] == "Half"
    assert sizing.raer_bucket(44.9)[0] == "Watch"
    assert sizing.raer_bucket(30)[0] == "Watch"
    assert sizing.raer_bucket(29.9)[0] == "Exclude"


def test_compute_raer_matches_doc_worked_examples():
    # ARCHITECTURE.md §8-4 예시 A/B (문서 자체가 중간값을 반올림해 예시를 만들어서
    # 정확히 일치하진 않음 -> 근사 비교)
    a = sizing.compute_raer(82, "BULL", 0.34, 34)
    assert abs(a["raer"] - 48.4) < 1.0 and a["action"] == "Half"

    b = sizing.compute_raer(71, "STRONG_BULL", 0.19, 8)
    assert abs(b["raer"] - 60.0) < 1.0 and b["action"] == "Standard"


def _signals(entries):
    return {"date": "2026-01-02", "regime": "STRONG_BULL", "signals": entries}


def _candidates(rows):
    return {"candidates": rows}


def test_allocate_core_shrinks_to_per_stock_cap():
    signals_data = _signals([
        {"ticker": "X", "entry": 100.0, "stop": 99.0, "risk_per_share": 1.0},
    ])
    candidates_data = _candidates([
        {"ticker": "X", "score": 100.0, "sector": "Tech", "days_to_earnings": None},
    ])
    result = sizing._allocate_core(signals_data, candidates_data, equity=100_000,
                                    vol_lookup={"X": 0.20})
    order = result["orders"][0]
    # STRONG_BULL 종목당 상한 15% -> 150주. 리스크 기반(1%/1.0)이면 1000주라 캡이 걸려야 함
    assert order["shares"] == 150
    assert any("종목당 상한" in r for r in order["reasons"])


def test_allocate_core_sector_cap_binds_on_second_position():
    signals_data = _signals([
        {"ticker": "A", "entry": 100.0, "stop": 99.0, "risk_per_share": 1.0},
        {"ticker": "B", "entry": 100.0, "stop": 99.0, "risk_per_share": 1.0},
    ])
    candidates_data = _candidates([
        {"ticker": "A", "score": 100.0, "sector": "Tech", "days_to_earnings": None},
        {"ticker": "B", "score": 100.0, "sector": "Tech", "days_to_earnings": None},
    ])
    result = sizing._allocate_core(signals_data, candidates_data, equity=100_000,
                                    vol_lookup={"A": 0.20, "B": 0.20})
    orders = {o["ticker"]: o for o in result["orders"]}
    assert orders["A"]["shares"] == 150   # 종목당 상한(15%)까지는 정상 배분
    assert orders["B"]["shares"] == 100   # 남은 섹터 여유(25%-15%=10%)로 축소
    assert any("섹터 상한" in r for r in orders["B"]["reasons"])


def test_allocate_core_halts_on_severe_drawdown():
    signals_data = _signals([{"ticker": "X", "entry": 100.0, "stop": 99.0, "risk_per_share": 1.0}])
    candidates_data = _candidates([{"ticker": "X", "score": 100.0, "sector": "Tech", "days_to_earnings": None}])
    result = sizing._allocate_core(signals_data, candidates_data, equity=100_000,
                                    vol_lookup={"X": 0.20},
                                    portfolio_state={"drawdown_pct": -0.20})
    assert result["halted"] is True
    assert result["orders"] == []


def test_allocate_core_halves_risk_on_moderate_drawdown():
    signals_data = _signals([{"ticker": "X", "entry": 100.0, "stop": 1.0, "risk_per_share": 1.0}])
    candidates_data = _candidates([{"ticker": "X", "score": 100.0, "sector": "Tech", "days_to_earnings": None}])
    result = sizing._allocate_core(signals_data, candidates_data, equity=100_000,
                                    vol_lookup={"X": 0.20},
                                    portfolio_state={"drawdown_pct": -0.09})
    assert result["halted"] is False
    assert result["risk_scale"] == 0.5


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
