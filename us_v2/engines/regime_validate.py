"""레짐 유효성 검증 — ARCHITECTURE.md 부록A 작업3.

레짐 라벨 부여일 이후 SPY 5/10/20일 수익률 분포가 단조 정렬되는가:
STRONG_BULL > BULL > NEUTRAL > CAUTION > RISK_OFF (CRISIS는 최하단 참고용).
이 검증을 통과하기 전에는 종목 선정/매매 로직으로 넘어가면 안 된다(§17).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import load  # noqa: E402
from engines.regime import REGIME_ORDER, REPORTS_DIR  # noqa: E402

CHECK_ORDER = ["STRONG_BULL", "BULL", "NEUTRAL", "CAUTION", "RISK_OFF"]  # 부록A 명시 순서
HORIZONS = [5, 10, 20]


def validate() -> tuple[bool, pd.DataFrame]:
    history = pd.read_csv(REPORTS_DIR / "regime_history.csv", index_col=0, parse_dates=True)
    spy = load("SPY")["close"].reindex(history.index)

    for h in HORIZONS:
        history[f"fwd_{h}d"] = spy.shift(-h) / spy - 1

    history = history[~history["confirmed_regime"].isin(["UNKNOWN"])]
    history = history.dropna(subset=[f"fwd_{h}d" for h in HORIZONS])

    summary = (
        history.groupby("confirmed_regime")[[f"fwd_{h}d" for h in HORIZONS]]
        .mean()
        .reindex(REGIME_ORDER[::-1])  # STRONG_BULL 위, CRISIS 아래로 보기 좋게
    )
    counts = history["confirmed_regime"].value_counts()
    summary["n"] = counts

    monotonic = {}
    for h in HORIZONS:
        ranked = summary.loc[CHECK_ORDER, f"fwd_{h}d"]
        monotonic[h] = bool(ranked.is_monotonic_decreasing)

    overall_pass = all(monotonic.values())
    return overall_pass, summary, monotonic


def write_report(overall_pass: bool, summary: pd.DataFrame, monotonic: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "regime_validation.md"
    lines = [
        "# 레짐 유효성 검증", "",
        f"판정: {'PASS' if overall_pass else 'FAIL'} — "
        f"기대 순서: {' > '.join(CHECK_ORDER)}", "",
        "| 레짐 | n | 5일 후행수익률 | 10일 후행수익률 | 20일 후행수익률 |",
        "|---|---|---|---|---|",
    ]
    for regime_name, row in summary.iterrows():
        lines.append(
            f"| {regime_name} | {int(row['n']) if pd.notna(row['n']) else 0} | "
            f"{row['fwd_5d']:.2%} | {row['fwd_10d']:.2%} | {row['fwd_20d']:.2%} |"
        )
    lines += ["", "단조 정렬 여부(수평선별):"]
    for h, ok in monotonic.items():
        lines.append(f"- {h}일: {'PASS' if ok else 'FAIL'}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    ok, summary, monotonic = validate()
    path = write_report(ok, summary, monotonic)
    print(summary)
    print(f"\n{'PASS' if ok else 'FAIL'} -> {path}")
