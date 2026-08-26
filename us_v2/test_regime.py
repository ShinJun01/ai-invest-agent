"""레짐 엔진 자체검증 — Pillar 채점 경계값 + 분류/확인규칙을 합성 데이터로 확인.
`python us_v2/test_regime.py`로 실행."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from engines import regime  # noqa: E402


def test_c3_realized_vol_tier_is_inclusive_at_28pct():
    # §3-1 원문: ">28% -> 0" (배타) 이므로 정확히 28%는 +3 구간에 포함돼야 함
    vol = pd.Series([0.10, 0.18, 0.20, 0.28, 0.30])
    score = regime._c3_realized_vol_score(vol)
    assert list(score) == [7, 3, 3, 3, 0]


def test_classify_raw_bands():
    # 총점 밴드
    assert regime.classify_raw(85, 20, 0.5, 0.9) == "STRONG_BULL"
    assert regime.classify_raw(85, 10, 0.5, 0.9) == "BULL"  # breadth<18 -> STRONG_BULL 강등
    assert regime.classify_raw(70, 20, 0.5, 0.9) == "BULL"
    assert regime.classify_raw(50, 20, 0.5, 0.9) == "NEUTRAL"
    assert regime.classify_raw(35, 20, 0.5, 0.9) == "CAUTION"
    assert regime.classify_raw(20, 20, 0.5, 0.9) == "RISK_OFF"
    assert regime.classify_raw(10, 20, 0.5, 0.9) == "CRISIS"


def test_classify_raw_crisis_overrides():
    # 총점이 높아도 VIX 백분위>95 또는 VIX9D/3M>1.10이면 무조건 CRISIS
    assert regime.classify_raw(90, 20, 0.97, 0.9) == "CRISIS"
    assert regime.classify_raw(90, 20, 0.5, 1.15) == "CRISIS"


def test_classify_raw_nan_is_unknown():
    assert regime.classify_raw(float("nan"), 20, 0.5, 0.9) == "UNKNOWN"


def test_two_day_confirmation_ignores_single_day_blip():
    df = pd.DataFrame({
        "raw_regime": ["BULL", "BULL", "STRONG_BULL", "BULL", "BULL"],
        "total": [70, 70, 85, 70, 70],
        "spy_5d_return": [0.0] * 5,
    })
    confirmed = regime._apply_confirmation_and_overrides(df)
    # 하루짜리 STRONG_BULL 튐은 확인되지 않고 BULL 유지
    assert list(confirmed) == ["BULL", "BULL", "BULL", "BULL", "BULL"]


def test_two_day_confirmation_confirms_persistent_change():
    df = pd.DataFrame({
        "raw_regime": ["BULL", "BULL", "NEUTRAL", "NEUTRAL", "NEUTRAL"],
        "total": [70, 70, 50, 50, 50],
        "spy_5d_return": [0.0] * 5,
    })
    confirmed = regime._apply_confirmation_and_overrides(df)
    assert list(confirmed) == ["BULL", "BULL", "BULL", "NEUTRAL", "NEUTRAL"]


def test_crisis_requires_three_day_recovery():
    df = pd.DataFrame({
        "raw_regime":     ["CRISIS", "NEUTRAL", "NEUTRAL", "NEUTRAL", "NEUTRAL"],
        "total":          [10,       50,        50,        50,        50],
        "spy_5d_return":  [0.0] * 5,
    })
    confirmed = regime._apply_confirmation_and_overrides(df)
    # 총점 45+ 가 3일 연속이어야 탈출 -> 4번째 행(0-idx 3)에서 처음 3연속 달성
    assert list(confirmed) == ["CRISIS", "CRISIS", "CRISIS", "NEUTRAL", "NEUTRAL"]


def test_crisis_entry_needs_two_day_confirmation_like_any_other_transition():
    # BULL 도중 CRISIS가 하루만 튀면 확정되면 안 됨(§3-0 2일 확인 규칙은 CRISIS도 예외 아님)
    df = pd.DataFrame({
        "raw_regime":    ["BULL", "BULL", "CRISIS", "BULL", "BULL"],
        "total":         [70, 70, 10, 70, 70],
        "spy_5d_return": [0.0] * 5,
    })
    confirmed = regime._apply_confirmation_and_overrides(df)
    assert list(confirmed) == ["BULL", "BULL", "BULL", "BULL", "BULL"]

    # 이틀 연속이면 확정됨
    df2 = pd.DataFrame({
        "raw_regime":    ["BULL", "BULL", "CRISIS", "CRISIS", "CRISIS"],
        "total":         [70, 70, 10, 10, 10],
        "spy_5d_return": [0.0] * 5,
    })
    confirmed2 = regime._apply_confirmation_and_overrides(df2)
    assert list(confirmed2) == ["BULL", "BULL", "BULL", "CRISIS", "CRISIS"]


def test_spy_drawdown_override_caps_at_caution():
    df = pd.DataFrame({
        "raw_regime": ["STRONG_BULL", "STRONG_BULL"],
        "total": [90, 90],
        "spy_5d_return": [0.0, -0.07],
    })
    confirmed = regime._apply_confirmation_and_overrides(df)
    assert confirmed.iloc[1] == "CAUTION"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
