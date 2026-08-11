"""Assembles all analysis outputs into a single ReportModel — the deterministic source
of truth. The formatter renders it; the narrative layer summarizes it. Neither invents
numbers not present here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from quantbot.analysis.changes import FlagChange
from quantbot.analysis.contribution import ContributionModel
from quantbot.analysis.diversification import DiversificationModel
from quantbot.analysis.fundamental import days_to_earnings
from quantbot.analysis.macro import MacroSnapshot
from quantbot.analysis.movement import MoveContext
from quantbot.analysis.risk import RiskMetrics
from quantbot.models import Flag, Fundamentals, Portfolio, TechnicalSnapshot


@dataclass(slots=True)
class PositionRow:
    symbol: str
    weight: float | None
    market_value: float | None
    unrealized_pnl: float | None
    pe: float | None
    rsi14: float | None
    ret_1m: float | None
    days_to_earnings: int | None
    sector: str | None


@dataclass(slots=True)
class ReportModel:
    as_of: date
    account_id: str
    base_currency: str
    net_liquidation: float | None
    total_cash: float | None
    invested_value: float
    positions: list[PositionRow] = field(default_factory=list)
    risk: RiskMetrics | None = None
    macro: MacroSnapshot | None = None
    flags: list[Flag] = field(default_factory=list)
    moves: MoveContext | None = None
    flag_changes: list[FlagChange] = field(default_factory=list)
    diversification: DiversificationModel | None = None
    contribution: ContributionModel | None = None
    narrative: str | None = None


def build(
    portfolio: Portfolio,
    fundamentals: dict[str, Fundamentals],
    technicals: dict[str, TechnicalSnapshot],
    risk: RiskMetrics,
    macro: MacroSnapshot,
    flags: list[Flag],
    *,
    moves: MoveContext | None = None,
    flag_changes: list[FlagChange] | None = None,
    diversification: DiversificationModel | None = None,
    contribution: ContributionModel | None = None,
    today: date | None = None,
) -> ReportModel:
    today = today or portfolio.as_of.date()
    weights = risk.weights if risk else {}

    rows: list[PositionRow] = []
    for h in portfolio.holdings:
        if h.asset_class == "CASH":
            continue
        fund = fundamentals.get(h.symbol) or Fundamentals(symbol=h.symbol)
        tech = technicals.get(h.symbol) or TechnicalSnapshot(symbol=h.symbol)
        rows.append(
            PositionRow(
                symbol=h.symbol,
                weight=weights.get(h.symbol),
                market_value=h.market_value,
                unrealized_pnl=h.unrealized_pnl,
                pe=fund.pe,
                rsi14=tech.rsi14,
                ret_1m=tech.ret_1m,
                days_to_earnings=days_to_earnings(fund, today),
                sector=fund.sector,
            )
        )
    rows.sort(key=lambda r: -(r.market_value or 0.0))

    return ReportModel(
        as_of=today,
        account_id=portfolio.account.account_id,
        base_currency=portfolio.account.base_currency,
        net_liquidation=portfolio.account.net_liquidation,
        total_cash=portfolio.account.total_cash,
        invested_value=portfolio.invested_value,
        positions=rows,
        risk=risk,
        macro=macro,
        flags=flags,
        moves=moves,
        flag_changes=flag_changes or [],
        diversification=diversification,
        contribution=contribution,
    )
