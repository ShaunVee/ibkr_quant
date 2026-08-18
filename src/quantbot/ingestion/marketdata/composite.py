"""Assembles market-data providers from config into a single MarketData facade.

Providers are constructed lazily and defensively: a missing API key or uninstalled
vendor library disables just that capability (methods return empty/None) instead of
breaking the whole run. Analysis code talks only to this facade.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from quantbot.config import Config
from quantbot.models import Fundamentals

log = logging.getLogger(__name__)


class MarketData:
    """Facade over the configured price/fundamentals/earnings/macro/calendar providers."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._price = None
        self._fundamentals = None
        self._earnings = None
        self._macro = None
        self._econ = None
        self._news = None
        self._build()

    def _build(self) -> None:
        md = self._config.providers.get("market_data", {})

        # yfinance covers prices + fundamentals + (fallback) earnings.
        yf_provider = None
        if "yfinance" in (md.get("prices"), md.get("fundamentals"), md.get("earnings")):
            yf_provider = self._safe(self._make_yfinance, "yfinance")

        if md.get("prices") == "yfinance":
            self._price = yf_provider
        if md.get("fundamentals") == "yfinance":
            self._fundamentals = yf_provider

        finnhub_users = {md.get("earnings"), md.get("econ_calendar"), md.get("news")}
        if "finnhub" in finnhub_users:
            finnhub = self._safe(self._make_finnhub, "finnhub")
            if md.get("earnings") == "finnhub":
                self._earnings = finnhub
            if md.get("econ_calendar") == "finnhub":
                self._econ = finnhub
            if md.get("news") == "finnhub":
                self._news = finnhub
        if self._earnings is None and md.get("earnings") == "yfinance":
            self._earnings = yf_provider

        if md.get("macro") == "fred":
            self._macro = self._safe(self._make_fred, "fred")

    # --- provider constructors ---
    def _make_yfinance(self):
        from quantbot.ingestion.marketdata.yfinance_provider import YFinanceProvider

        return YFinanceProvider()

    def _make_finnhub(self):
        from quantbot.ingestion.marketdata.finnhub_provider import FinnhubProvider

        key = self._config.secrets.get("FINNHUB_KEY")
        return FinnhubProvider(key)

    def _make_fred(self):
        from quantbot.ingestion.marketdata.fred_provider import FredProvider

        key = self._config.secrets.get("FRED_KEY")
        return FredProvider(key)

    @staticmethod
    def _safe(factory, name):
        try:
            return factory()
        except Exception as exc:  # noqa: BLE001
            log.warning("Provider %s unavailable: %s", name, exc)
            return None

    # --- capability methods (degrade gracefully) ---
    def daily_prices(self, symbol: str, days: int) -> pd.DataFrame:
        if self._price is None:
            return pd.DataFrame()
        return self._price.daily_prices(symbol, days)

    def fundamentals(self, symbol: str) -> Fundamentals:
        if self._fundamentals is None:
            return Fundamentals(symbol=symbol)
        return self._fundamentals.fundamentals(symbol)

    def next_earnings(self, symbol: str) -> date | None:
        if self._earnings is None:
            return None
        return self._earnings.next_earnings(symbol)

    def macro_series(self, series_id: str, observations: int = 13) -> pd.Series:
        if self._macro is None:
            return pd.Series(dtype="float64")
        return self._macro.series_latest(series_id, observations)

    def upcoming_events(self, days_ahead: int = 14) -> list[dict]:
        if self._econ is None:
            return []
        return self._econ.upcoming_events(days_ahead)

    def company_news(self, symbol: str, days_back: int = 5) -> list[dict]:
        if self._news is None:
            return []
        return self._news.company_news(symbol, days_back)
