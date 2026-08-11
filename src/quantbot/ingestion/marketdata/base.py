"""MarketDataProvider interfaces. Analysis code depends only on these — never on a
specific vendor. Free implementations (yfinance, FRED, Finnhub) live alongside; paid
ones drop in behind the same ABCs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from quantbot.models import Fundamentals


class PriceProvider(ABC):
    @abstractmethod
    def daily_prices(self, symbol: str, days: int) -> pd.DataFrame:
        """Return a DataFrame indexed by date with columns
        [open, high, low, close, volume]. Empty DataFrame on failure."""


class FundamentalsProvider(ABC):
    @abstractmethod
    def fundamentals(self, symbol: str) -> Fundamentals:
        """Return a Fundamentals snapshot (fields may be None where unavailable)."""


class EarningsProvider(ABC):
    @abstractmethod
    def next_earnings(self, symbol: str) -> date | None:
        """Return the next earnings date for a symbol, or None."""


class MacroProvider(ABC):
    @abstractmethod
    def series_latest(self, series_id: str, observations: int = 13) -> pd.Series:
        """Return the last `observations` values of a macro series, date-indexed."""


class EconCalendarProvider(ABC):
    @abstractmethod
    def upcoming_events(self, days_ahead: int = 14) -> list[dict]:
        """Return upcoming economic events: [{date, event, impact, ...}]."""
