"""Fundamental analysis: gather per-symbol Fundamentals and derive days-to-earnings.

This module is thin — it orchestrates the market-data facade and normalizes earnings
dates. The heavy lifting (vendor quirks) lives in the providers.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from datetime import date
from typing import Any

from quantbot.ingestion.marketdata.composite import MarketData
from quantbot.models import Fundamentals, Portfolio

log = logging.getLogger(__name__)

_FUNDAMENTAL_FIELDS = {f.name for f in fields(Fundamentals)} - {"symbol"}


def gather(
    portfolio: Portfolio,
    market: MarketData,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Fundamentals]:
    """Fetch fundamentals for every equity symbol in the portfolio.

    `overrides` maps a symbol to a dict of Fundamentals fields to force — used to fill
    in values vendors leave blank (e.g. the sector of an ETF) or to correct a mislabel.
    An override wins over the provider value; matching is case-insensitive on symbol.
    """
    normalized = {sym.upper(): vals for sym, vals in (overrides or {}).items()}
    out: dict[str, Fundamentals] = {}
    for h in portfolio.equity_holdings:
        f = market.fundamentals(h.symbol)
        # If the fundamentals provider didn't supply earnings, try the earnings provider.
        if f.next_earnings is None:
            f.next_earnings = market.next_earnings(h.symbol)
        _apply_overrides(f, normalized.get(h.symbol.upper()))
        out[h.symbol] = f
    return out


def _apply_overrides(f: Fundamentals, override: dict[str, Any] | None) -> None:
    if not override:
        return
    for key, value in override.items():
        if key in _FUNDAMENTAL_FIELDS:
            setattr(f, key, value)
        else:
            log.warning("Ignoring unknown fundamentals override %r for %s", key, f.symbol)


def days_to_earnings(fundamentals: Fundamentals, today: date | None = None) -> int | None:
    if fundamentals.next_earnings is None:
        return None
    today = today or date.today()
    return (fundamentals.next_earnings - today).days
