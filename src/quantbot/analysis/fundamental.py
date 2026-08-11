"""Fundamental analysis: gather per-symbol Fundamentals and derive days-to-earnings.

This module is thin — it orchestrates the market-data facade and normalizes earnings
dates. The heavy lifting (vendor quirks) lives in the providers.
"""

from __future__ import annotations

import logging
from datetime import date

from quantbot.ingestion.marketdata.composite import MarketData
from quantbot.models import Fundamentals, Portfolio

log = logging.getLogger(__name__)


def gather(portfolio: Portfolio, market: MarketData) -> dict[str, Fundamentals]:
    """Fetch fundamentals for every equity symbol in the portfolio."""
    out: dict[str, Fundamentals] = {}
    for h in portfolio.equity_holdings:
        f = market.fundamentals(h.symbol)
        # If the fundamentals provider didn't supply earnings, try the earnings provider.
        if f.next_earnings is None:
            f.next_earnings = market.next_earnings(h.symbol)
        out[h.symbol] = f
    return out


def days_to_earnings(fundamentals: Fundamentals, today: date | None = None) -> int | None:
    if fundamentals.next_earnings is None:
        return None
    today = today or date.today()
    return (fundamentals.next_earnings - today).days
