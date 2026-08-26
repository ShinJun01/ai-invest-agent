"""데이터 검증 게이트 V1~V7 — ARCHITECTURE.md §14-2.

ingest.py가 채운 parquet 캐시 + manifest.json을 검증한다. Phase 1은 매매가
없으므로 "HALT"는 실제 주문 중단이 아니라 브리핑 생성 차단으로 해석한다
(design spec 참고). 핵심 지표(SPY/VIX/QQQ/섹터ETF) 실패는 전체 실패로,
개별 S&P500 구성종목 실패는 커버리지(V6) 집계로만 반영한다.
"""
from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import MANIFEST_PATH, STORE_DIR, load as _load  # noqa: E402

REPORTS_DIR = Path(__file__).parent.parent / "reports"

CORE_TICKERS = {"SPY", "QQQ", "GSPC", "VIX", "VIX9D", "VIX3M", "TNX", "DXY", "RSP", "HYG", "IEF", "IWM"}
VIX_RANGE = (5.0, 150.0)
MAX_DAILY_RETURN = 0.30
MIN_COVERAGE = 0.98


@dataclass
class GateResult:
    gate: str
    ticker: str
    passed: bool
    message: str
    blocking: bool = True  # False = 참고용 플래그, HALT 판정에 포함하지 않음


def v1_schema(ticker: str, df: pd.DataFrame) -> GateResult:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        return GateResult("V1_schema", ticker, False, f"결측 컬럼: {missing}")
    if not all(pd.api.types.is_numeric_dtype(df[c]) for c in required):
        return GateResult("V1_schema", ticker, False, "숫자 타입 아닌 컬럼 존재")
    return GateResult("V1_schema", ticker, True, "ok")


def v2_freshness(ticker: str, as_of: str, trading_days: pd.DatetimeIndex) -> GateResult:
    """as_of가 캘린더상 가장 최근 거래일과 일치하는지."""
    last_trading_day = trading_days.max().date()
    as_of_date = date.fromisoformat(as_of)
    if as_of_date < last_trading_day:
        stale_days = (last_trading_day - as_of_date).days
        return GateResult("V2_freshness", ticker, False,
                           f"{stale_days}일 지연 (as_of={as_of}, 최신 거래일={last_trading_day})")
    return GateResult("V2_freshness", ticker, True, "ok")


def v3_range(ticker: str, df: pd.DataFrame) -> GateResult:
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        return GateResult("V3_range", ticker, False, "0 이하 가격 존재")
    if (df["high"] < df["low"]).any():
        return GateResult("V3_range", ticker, False, "high < low 발생")
    if ticker in {"VIX", "VIX9D", "VIX3M"}:
        lo, hi = VIX_RANGE
        if not df["close"].between(lo, hi).all():
            return GateResult("V3_range", ticker, False, f"VIX 범위({lo}~{hi}) 벗어난 값 존재")
    return GateResult("V3_range", ticker, True, "ok")


def v4_continuity(ticker: str, df: pd.DataFrame) -> GateResult:
    """§14-2 원문: '분할/오류 의심 -> 수동 확인 플래그'. 실패가 아니라 참고용 표시이므로
    blocking=False — 실적 발표·VIX 스파이크 등 정상적인 30%+ 단일 종목/지수 변동이
    500종목 x 3년, 변동성 지수 특성상 흔하게 발생한다."""
    ret = df["close"].pct_change().abs()
    flagged = ret[ret > MAX_DAILY_RETURN]
    if len(flagged) > 0:
        dates = ", ".join(d.date().isoformat() for d in flagged.index[:5])
        return GateResult("V4_continuity", ticker, False,
                           f"|수익률|>{MAX_DAILY_RETURN:.0%} 인 날 {len(flagged)}건 (예: {dates})",
                           blocking=False)
    return GateResult("V4_continuity", ticker, True, "ok", blocking=False)


def _fred_csv(series_id: str) -> pd.Series:
    resp = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), parse_dates=["observation_date"])
    df = df[df[series_id] != "."]
    df[series_id] = df[series_id].astype(float)
    return df.set_index("observation_date")[series_id]


def v5_cross_check() -> list[GateResult]:
    """VIX·S&P500 지수를 FRED(독립 소스)와 대조. 편차 >0.1%면 실패."""
    results = []
    checks = [("VIX", "VIXCLS"), ("GSPC", "SP500")]
    for ticker, fred_id in checks:
        try:
            local = _load(ticker)["close"]
            fred = _fred_csv(fred_id)
        except Exception as e:  # 네트워크/데이터 문제는 검증 실패로 취급
            results.append(GateResult("V5_cross_check", ticker, False, f"조회 실패: {e}"))
            continue
        common = local.index.normalize().intersection(fred.index)
        if len(common) == 0:
            results.append(GateResult("V5_cross_check", ticker, False, "공통 날짜 없음"))
            continue
        last = common.max()
        local_v = local.loc[local.index.normalize() == last].iloc[-1]
        fred_v = fred.loc[last]
        deviation = abs(local_v - fred_v) / fred_v
        passed = deviation <= 0.001
        results.append(GateResult(
            "V5_cross_check", ticker, passed,
            f"{last.date()} yfinance={local_v:.2f} fred={fred_v:.2f} 편차={deviation:.3%}"))
    return results


def v6_coverage(expected: list[str], manifest: dict) -> GateResult:
    received = sum(1 for t in expected if t in manifest)
    ratio = received / len(expected) if expected else 0.0
    passed = ratio >= MIN_COVERAGE
    return GateResult("V6_coverage", "sp500_universe", passed,
                       f"{received}/{len(expected)} ({ratio:.1%})")


def v7_calendar(ticker: str, df: pd.DataFrame, trading_days: pd.DatetimeIndex) -> GateResult:
    """SPY 실제 거래일 시퀀스를 캘린더 삼아, 해당 종목에 없는 캘린더상 거래일을 검출."""
    recent_calendar = trading_days[trading_days >= trading_days.max() - timedelta(days=30)]
    ticker_dates = set(df.index.normalize())
    missing = [d for d in recent_calendar if d not in ticker_dates]
    if len(missing) > 2:  # 상장폐지/거래정지 등 2일 이내는 허용
        return GateResult("V7_calendar", ticker, False,
                           f"최근 30일 캘린더 중 {len(missing)}일 결측")
    return GateResult("V7_calendar", ticker, True, "ok")


def validate_all() -> tuple[bool, list[GateResult]]:
    manifest = json.loads(MANIFEST_PATH.read_text())
    constituents = json.loads((STORE_DIR / "sp500_constituents.json").read_text())["tickers"]

    spy_df = _load("SPY")
    trading_days = spy_df.index.normalize().unique()

    results: list[GateResult] = []

    for ticker in CORE_TICKERS:
        if ticker not in manifest:
            results.append(GateResult("V1_schema", ticker, False, "manifest에 없음(수집 실패)"))
            continue
        df = _load(ticker)
        results.append(v1_schema(ticker, df))
        results.append(v2_freshness(ticker, manifest[ticker]["as_of"], trading_days))
        results.append(v3_range(ticker, df))
        results.append(v4_continuity(ticker, df))
        results.append(v7_calendar(ticker, df, trading_days))

    results.extend(v5_cross_check())
    results.append(v6_coverage(constituents, manifest))

    # 구성종목: 스키마/범위 위반은 실제 결함(하드 실패). V4 연속성은 문서(§14-2) 표현대로
    # "분할/오류 의심 → 수동 확인 플래그"일 뿐 실패가 아니다 — 실적 발표 등으로
    # 개별 종목이 하루 30%+ 움직이는 건 500종목 x 3년이면 정상적으로 발생한다.
    stock_defects = 0
    continuity_flags: list[GateResult] = []
    for ticker in constituents:
        if ticker not in manifest:
            stock_defects += 1
            continue
        df = _load(ticker)
        defect_checks = [v1_schema(ticker, df), v3_range(ticker, df)]
        if not all(c.passed for c in defect_checks):
            stock_defects += 1
        flag = v4_continuity(ticker, df)
        if not flag.passed:
            continuity_flags.append(flag)
    results.append(GateResult(
        "V_constituents_defects", "sp500_universe",
        stock_defects / max(len(constituents), 1) <= 0.02,
        f"{stock_defects}/{len(constituents)} 종목에서 스키마/범위 결함"))
    results.append(GateResult(
        "V4_continuity_flags", "sp500_universe", True,
        f"{len(continuity_flags)}개 종목 수동 확인 플래그 (실패 아님, 참고용): "
        + ", ".join(f.ticker for f in continuity_flags)))

    core_passed = all(r.passed for r in results if r.blocking)
    return core_passed, results


def write_report(passed: bool, results: list[GateResult]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().date().isoformat()
    path = REPORTS_DIR / f"validation_{today.replace('-', '')}.md"
    lines = [f"# 데이터 검증 결과 — {today}", "", f"전체 판정: {'PASS' if passed else 'HALT'}", "",
             "| 게이트 | 대상 | 결과 | 메시지 |", "|---|---|---|---|"]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"| {r.gate} | {r.ticker} | {mark} | {r.message} |")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    ok, results = validate_all()
    report_path = write_report(ok, results)
    blocking_fails = [r for r in results if not r.passed and r.blocking]
    flags = [r for r in results if not r.passed and not r.blocking]
    print(f"{'PASS' if ok else 'HALT'} — {len(blocking_fails)}개 차단 실패, "
          f"{len(flags)}개 참고 플래그. 리포트: {report_path}")
    for r in blocking_fails:
        print(f"  [FAIL] {r.gate} {r.ticker}: {r.message}")
    for r in flags:
        print(f"  [flag] {r.gate} {r.ticker}: {r.message}")
