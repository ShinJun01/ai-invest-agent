"""Position Sizing Engine — ARCHITECTURE.md §8(RAER) + §10(Risk Management).

L5(이벤트 승수) → L6(리스크 캡) → L7(사이징)을 한 파일에서 처리한다. 셋 다
개별 파일로 만들기엔 너무 작고, engines/signals.py 출력 하나를 순서대로
변형해가는 파이프라인이라 쪼개면 오히려 추적이 어려워진다.

범위 밖:
- L5 매크로 이벤트 승수(0.6, 당일 발표)는 매크로 캘린더 소스가 없어 미구현.
  실적 기반 승수(1.0 / 0.7 / 0.0, §6-3)만 적용
- L5 일일/주간 손실한도, L6 드로다운 사다리는 실현손익 이력이 필요한데
  매매일지(L10)가 아직 없다 — allocate()가 파라미터로 받되 기본값은 "이력 없음"
  (오늘 첫 거래). 저널이 생기면 그 값을 실제로 채워 넣으면 됨
- L3 상관클러스터 30% 상한은 별도 계산 안 함 — screen.py Stage5가 이미 상관>0.75
  쌍을 걸러내 후보 리스트가 이 기준보다 엄격한 상태로 들어옴
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import load  # noqa: E402
from engines.regime import REGIME_PARAMS  # noqa: E402  (총노출상한, 종목당상한, 허용전략)
from engines.screen import REPORTS_DIR  # noqa: E402

REGIME_MULTIPLIER = {
    "STRONG_BULL": 1.15, "BULL": 1.00, "NEUTRAL": 0.70,
    "CAUTION": 0.40, "RISK_OFF": 0.10, "CRISIS": 0.00,
}
TARGET_ANNUAL_VOL = 0.20
VOL_ADJ_MIN, VOL_ADJ_MAX = 0.3, 1.2

# RAER 임계값 내림차순: (하한, 라벨, 리스크%)
RAER_BUCKETS = [
    (75, "Full", 0.010),
    (60, "Standard", 0.0075),
    (45, "Half", 0.005),
    (30, "Watch", 0.0),
    (0, "Exclude", 0.0),
]

SECTOR_CAP_PCT = 0.25
PORTFOLIO_HEAT_CAP_PCT = 0.06
MIN_POSITION_PCT = 0.01  # 이하는 수수료 대비 비효율(§10-2 L2) -> 제외


# ---------- 입력 로드 ----------

def load_latest(prefix: str) -> dict:
    files = sorted(REPORTS_DIR.glob(f"{prefix}_*.json"))
    if not files:
        raise FileNotFoundError(f"{prefix}_*.json 없음")
    return json.loads(files[-1].read_text())


def load_latest_signals() -> dict:
    files = sorted(REPORTS_DIR.glob("signals_*.json"))
    if not files:
        raise FileNotFoundError("signals_*.json 없음 -> 먼저 engines/signals.py 실행")
    return json.loads(files[-1].read_text())


# ---------- RAER ----------

def annualized_realized_vol(ticker: str, window: int = 20) -> float:
    close = load(ticker)["close"].sort_index()
    return close.pct_change().iloc[-window:].std() * math.sqrt(252)


def volatility_adjustment(annual_vol: float) -> float:
    if annual_vol <= 0:
        return VOL_ADJ_MAX
    return min(VOL_ADJ_MAX, max(VOL_ADJ_MIN, TARGET_ANNUAL_VOL / annual_vol))


def event_risk_multiplier(days_to_earnings: float | None) -> float:
    """§6-3. 매크로 이벤트 당일 0.6 승수는 매크로 캘린더 없어 미구현."""
    if days_to_earnings is None:
        return 1.0
    if -2 <= days_to_earnings <= 7:  # T-5~T+1 근사(거래일 기준 문서를 달력일로 근사)
        return 0.0
    if 6 <= days_to_earnings <= 10:
        return 0.7
    return 1.0


def raer_bucket(raer: float) -> tuple[str, float]:
    for lower, label, risk_pct in RAER_BUCKETS:
        if raer >= lower:
            return label, risk_pct
    return "Exclude", 0.0


def compute_raer(score: float, regime_name: str, annual_vol: float,
                  days_to_earnings: float | None) -> dict:
    regime_mult = REGIME_MULTIPLIER[regime_name]
    vol_adj = volatility_adjustment(annual_vol)
    event_mult = event_risk_multiplier(days_to_earnings)
    raer = score * regime_mult * vol_adj * event_mult
    label, risk_pct = raer_bucket(raer)
    return {
        "raer": round(raer, 1), "action": label, "risk_pct": risk_pct,
        "regime_multiplier": regime_mult, "volatility_adjustment": round(vol_adj, 3),
        "event_risk_multiplier": event_mult, "annualized_vol": round(annual_vol, 3),
    }


# ---------- 사이징 + 캡 ----------

def _initial_shares(equity: float, risk_pct: float, risk_per_share: float) -> int:
    if risk_pct <= 0 or risk_per_share <= 0:
        return 0
    return math.floor((equity * risk_pct) / risk_per_share)


def allocate(signals_data: dict, candidates_data: dict, equity: float,
             portfolio_state: dict | None = None) -> dict:
    """RAER 내림차순으로 오늘의 시그널을 순차 배분(디스크 I/O: 종목별 변동성 조회).
    portfolio_state 기본값은 "기존 포지션 없음, 오늘 첫 거래"(모든 이력 0)."""
    vol_lookup = {sig["ticker"]: annualized_realized_vol(sig["ticker"]) for sig in signals_data["signals"]}
    return _allocate_core(signals_data, candidates_data, equity, vol_lookup, portfolio_state)


def _allocate_core(signals_data: dict, candidates_data: dict, equity: float,
                    vol_lookup: dict[str, float], portfolio_state: dict | None = None) -> dict:
    """순수 함수(디스크 I/O 없음) — allocate()에서 분리해 합성 데이터로 테스트하기 쉽게 함."""
    portfolio_state = portfolio_state or {}
    daily_pnl_pct = portfolio_state.get("daily_pnl_pct", 0.0)
    weekly_pnl_pct = portfolio_state.get("weekly_pnl_pct", 0.0)
    drawdown_pct = portfolio_state.get("drawdown_pct", 0.0)
    sector_exposure = dict(portfolio_state.get("sector_exposure_pct", {}))
    total_exposure = portfolio_state.get("total_exposure_pct", 0.0)
    portfolio_heat = portfolio_state.get("portfolio_heat_pct", 0.0)

    regime_name = signals_data["regime"]
    total_cap, per_stock_cap, _ = REGIME_PARAMS[regime_name]

    # L5/L6 킬스위치 — 신규 진입 자체를 전면 중단시키는 조건
    halt_reasons = []
    if drawdown_pct <= -0.18:
        halt_reasons.append("drawdown<=-18%: 전량 청산 + 시스템 중단(재검증 필요)")
    elif drawdown_pct <= -0.12:
        halt_reasons.append("drawdown<=-12%: 신규 진입 중단")
    if daily_pnl_pct <= -0.02:
        halt_reasons.append("일일손실<=-2%: 당일 신규 진입 중단")
    if weekly_pnl_pct <= -0.05:
        halt_reasons.append("주간손실<=-5%: 잔여 주간 신규 진입 중단")

    risk_scale = 0.5 if drawdown_pct <= -0.08 else 1.0  # L6: DD -8%면 리스크 50% 축소

    candidates_by_ticker = {c["ticker"]: c for c in candidates_data["candidates"]}
    orders = []

    if not halt_reasons:
        enriched = []
        for sig in signals_data["signals"]:
            cand = candidates_by_ticker.get(sig["ticker"])
            if cand is None:
                continue
            vol = vol_lookup[sig["ticker"]]
            raer_info = compute_raer(cand["score"], regime_name, vol, cand.get("days_to_earnings"))
            enriched.append({**sig, **raer_info, "sector": cand["sector"]})
        enriched.sort(key=lambda x: x["raer"], reverse=True)

        for item in enriched:
            reasons = []
            risk_pct = item["risk_pct"] * risk_scale
            shares = _initial_shares(equity, risk_pct, item["risk_per_share"])

            # L2 종목당 상한
            cap_shares = math.floor((equity * per_stock_cap) / item["entry"]) if item["entry"] > 0 else 0
            if shares > cap_shares:
                shares, reasons = cap_shares, reasons + ["종목당 상한(L2)으로 축소"]

            # L3 섹터 상한(25%)
            sector_used = sector_exposure.get(item["sector"], 0.0)
            sector_room = max(0.0, SECTOR_CAP_PCT - sector_used) * equity
            sector_cap_shares = math.floor(sector_room / item["entry"]) if item["entry"] > 0 else 0
            if shares > sector_cap_shares:
                shares, reasons = sector_cap_shares, reasons + ["섹터 상한(L3, 25%)으로 축소"]

            # L4 포트폴리오 heat 상한(6%)
            heat_room = max(0.0, PORTFOLIO_HEAT_CAP_PCT - portfolio_heat) * equity
            heat_cap_shares = math.floor(heat_room / item["risk_per_share"]) if item["risk_per_share"] > 0 else 0
            if shares > heat_cap_shares:
                shares, reasons = heat_cap_shares, reasons + ["포트폴리오 heat 상한(L4, 6%)으로 축소"]

            # L4 총노출 상한(레짐별)
            total_room = max(0.0, total_cap - total_exposure) * equity
            total_cap_shares = math.floor(total_room / item["entry"]) if item["entry"] > 0 else 0
            if shares > total_cap_shares:
                shares, reasons = total_cap_shares, reasons + ["총노출 상한(L4, 레짐별)으로 축소"]

            notional = shares * item["entry"]
            if shares > 0 and notional / equity < MIN_POSITION_PCT:
                shares, notional = 0, 0.0
                reasons.append(f"최소 비중({MIN_POSITION_PCT:.0%}) 미만 -> 제외")

            heat = shares * item["risk_per_share"]
            orders.append({
                "ticker": item["ticker"], "action": item["action"], "raer": item["raer"],
                "shares": shares, "entry": item["entry"], "stop": item["stop"],
                "notional": round(notional, 2), "notional_pct": round(notional / equity, 4),
                "heat_pct": round(heat / equity, 4),
                "reasons": reasons or ["제약 없음"],
            })
            if shares > 0:
                sector_exposure[item["sector"]] = sector_used + notional / equity
                total_exposure += notional / equity
                portfolio_heat += heat / equity

    return {
        "date": signals_data["date"], "regime": regime_name, "equity": equity,
        "halted": bool(halt_reasons), "halt_reasons": halt_reasons,
        "risk_scale": risk_scale,
        "orders": orders,
        "portfolio_after": {
            "total_exposure_pct": round(total_exposure, 4),
            "portfolio_heat_pct": round(portfolio_heat, 4),
            "sector_exposure_pct": {k: round(v, 4) for k, v in sector_exposure.items()},
        },
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", type=float, required=True, help="계좌 총자산(달러)")
    args = parser.parse_args()

    signals_data = load_latest_signals()
    candidates_data = load_latest("candidates")
    result = allocate(signals_data, candidates_data, args.equity)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"orders_{result['date'].replace('-', '')}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "orders"}, ensure_ascii=False, indent=2))
    for o in result["orders"]:
        print(f"  {o['ticker']}: {o['action']} RAER={o['raer']} shares={o['shares']} "
              f"notional={o['notional']} ({o['notional_pct']:.1%}) -- {', '.join(o['reasons'])}")
    print(f"-> {out_path}")
