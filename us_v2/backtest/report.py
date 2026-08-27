"""성과 평가 — ARCHITECTURE.md §12-1(Tier 1 전부) + Tier 2 일부(승률/손익비/기대값 등,
신호 기반 전략 해석에 핵심이라 §12-1만으로는 판단 불가). OOS 열화율은 아직 계산 안 함
(§11-3 Train/Val/OOS 구간 분리가 필요 — backtest/validators.py 몫으로 미룸).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.store import load  # noqa: E402

TRADING_DAYS_PER_YEAR = 252


def _cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def _max_drawdown(equity: pd.Series) -> float:
    drawdown = equity / equity.cummax() - 1
    return drawdown.min()


def _sharpe(daily_returns: pd.Series) -> float:
    if daily_returns.std() == 0:
        return float("nan")
    return daily_returns.mean() / daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)


def _sortino(daily_returns: pd.Series) -> float:
    downside = daily_returns[daily_returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        return float("nan")
    return daily_returns.mean() / downside.std() * np.sqrt(TRADING_DAYS_PER_YEAR)


def _max_consecutive_losses(trades: pd.DataFrame) -> int:
    is_loss = (trades["r_multiple"] <= 0).astype(int)
    longest = current = 0
    for v in is_loss:
        current = current + 1 if v else 0
        longest = max(longest, current)
    return longest


def compute_report(trades: pd.DataFrame, equity_curve: pd.DataFrame, initial_capital: float) -> dict:
    equity = equity_curve["equity"]
    daily_returns = equity.pct_change().dropna()

    spy = load("SPY")["close"].sort_index()
    spy = spy.loc[equity.index[0]:equity.index[-1]]
    spy_cagr = _cagr(spy)
    spy_mdd = _max_drawdown(spy)

    cagr, mdd, sharpe, sortino = _cagr(equity), _max_drawdown(equity), _sharpe(daily_returns), _sortino(daily_returns)
    calmar = cagr / abs(mdd) if mdd else float("nan")

    wins = trades[trades["r_multiple"] > 0]
    losses = trades[trades["r_multiple"] <= 0]
    win_rate = len(wins) / len(trades) if len(trades) else float("nan")
    avg_win_r = wins["r_multiple"].mean() if len(wins) else float("nan")
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else float("nan")
    expectancy_r = trades["r_multiple"].mean() if len(trades) else float("nan")
    gross_profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    gross_loss = trades.loc[trades["pnl"] < 0, "pnl"].sum()
    profit_factor = gross_profit / abs(gross_loss) if gross_loss else float("nan")

    tier1 = {
        "CAGR": cagr, "CAGR_vs_SPY_pass": bool(cagr > spy_cagr) if pd.notna(cagr) else False,
        "MDD": mdd, "MDD_pass": bool(abs(mdd) <= 0.20 and abs(mdd) < abs(spy_mdd)) if pd.notna(mdd) else False,
        "Sharpe": sharpe, "Sharpe_pass": bool(sharpe >= 1.0) if pd.notna(sharpe) else False,
        "Calmar": calmar, "Calmar_pass": bool(calmar >= 0.7) if pd.notna(calmar) else False,
        "OOS_degradation": None,  # §11-3 단계 분리 필요 -> 미계산
    }
    tier2 = {
        "Sortino": sortino, "ProfitFactor": profit_factor, "Expectancy_R": expectancy_r,
        "WinRate": win_rate, "AvgWin_R": avg_win_r, "AvgLoss_R": avg_loss_r,
        "MaxConsecutiveLosses": _max_consecutive_losses(trades) if len(trades) else 0,
        "RecoveryFactor": (equity.iloc[-1] - initial_capital) / (abs(mdd) * initial_capital) if mdd else float("nan"),
    }
    return {
        "n_trades": len(trades), "spy_cagr": spy_cagr, "spy_mdd": spy_mdd,
        "tier1": tier1, "tier2": tier2,
    }


def write_report(report: dict, out_path: Path) -> None:
    t1, t2 = report["tier1"], report["tier2"]
    lines = [
        "# S1 Trend Pullback — MVP 백테스트 성과 리포트", "",
        "**주의: 시점별 유니버스가 아닌 현재 S&P500으로 돌린 결과라 생존편향이 있다"
        "(연 3~5%p 과대평가 추정, ARCHITECTURE.md §5-2/§17). 이 숫자는 참고용이며"
        " Phase 2 완료 조건을 충족하지 않는다.**", "",
        f"거래 수: {report['n_trades']} (§11-4 최소 기준 300 {'충족' if report['n_trades'] >= 300 else '미달'})",
        f"SPY 동기간 CAGR: {report['spy_cagr']:.2%} / MDD: {report['spy_mdd']:.2%}", "",
        "## Tier 1 (5개 전부 통과해야 다음 단계 의미 있음)",
        "| 지표 | 값 | 판정 |", "|---|---|---|",
        f"| CAGR | {t1['CAGR']:.2%} | {'PASS' if t1['CAGR_vs_SPY_pass'] else 'FAIL'} (SPY 초과 필요) |",
        f"| MDD | {t1['MDD']:.2%} | {'PASS' if t1['MDD_pass'] else 'FAIL'} (<=20% 이고 SPY보다 작아야) |",
        f"| Sharpe | {t1['Sharpe']:.2f} | {'PASS' if t1['Sharpe_pass'] else 'FAIL'} (>=1.0) |",
        f"| Calmar | {t1['Calmar']:.2f} | {'PASS' if t1['Calmar_pass'] else 'FAIL'} (>=0.7) |",
        "| OOS 열화율 | 미계산 | Train/Val/OOS 분리 필요(§11-3), 다음 단계 |",
        "", "## Tier 2 (구조 진단)",
        "| 지표 | 값 |", "|---|---|",
        f"| Expectancy(R) | {t2['Expectancy_R']:.3f} |",
        f"| Win Rate | {t2['WinRate']:.1%} |",
        f"| Avg Win / Avg Loss (R) | {t2['AvgWin_R']:.2f} / {t2['AvgLoss_R']:.2f} |",
        f"| Profit Factor | {t2['ProfitFactor']:.2f} |",
        f"| Sortino | {t2['Sortino']:.2f} |",
        f"| Max Consecutive Losses | {t2['MaxConsecutiveLosses']} |",
        f"| Recovery Factor | {t2['RecoveryFactor']:.2f} |",
        "",
        "## 해석: 트레이드 기대값은 거의 0인데 왜 MDD가 이렇게 큰가",
        "Expectancy(R)이 0에 가까운데(그리고 승률도 절반을 넘는데) MDD가 SPY보다도",
        "훨씬 나쁘게 나오는 건 버그가 아니라 **이 백테스트가 engines/screen.py의",
        "Stage5 랭킹·섹터/상관 다각화·engines/sizing.py의 포트폴리오 캡(섹터 25%,",
        "heat 6% 등)을 전혀 적용하지 않기 때문**이다 — 조건을 만족하는 신호를",
        "전부(최대 동시 10종목) 매매해서, 같은 날 같은 방향으로 몰리는 상관된 베팅이",
        "그대로 들어간다. 개별 트레이드 단위 우위(엣지)는 거의 없더라도, 분산 없는",
        "동시다발 손실이 겹치면 포트폴리오 단위 낙폭은 훨씬 커질 수 있다 — 역설적으로",
        "이건 문서가 §5-5/§10에서 랭킹·다각화·리스크 상한을 별도 레이어로 둔 이유를",
        "숫자로 보여주는 결과다. 다음 단계는 이 백테스트에 그 선별/캡 로직을 이식해",
        "\"제대로 걸러진 S1\"의 성과를 다시 재는 것.",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
