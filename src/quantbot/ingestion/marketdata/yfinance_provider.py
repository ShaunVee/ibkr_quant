"""yfinance-backed price + fundamentals provider (free tier).

yfinance is unofficial and occasionally flaky/stale — this is the first thing to swap
for a paid feed. All methods degrade gracefully (empty frame / None fields) so a bad
response never kills the pipeline.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd

from quantbot.ingestion.marketdata.base import (
    EarningsProvider,
    FundamentalsProvider,
    PriceProvider,
)
from quantbot.models import Fundamentals

log = logging.getLogger(__name__)


class YFinanceProvider(PriceProvider, FundamentalsProvider, EarningsProvider):
    def __init__(self) -> None:
        # Imported lazily so the package imports without yfinance installed.
        import yfinance as yf  # noqa: F401

        self._yf = yf

    def daily_prices(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            ticker = self._yf.Ticker(symbol)
            # period accepts e.g. "400d"; buffer a little for non-trading days.
            hist = ticker.history(period=f"{days}d", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001 - vendor can raise anything
            log.warning("yfinance price fetch failed for %s: %s", symbol, exc)
            return pd.DataFrame()

        if hist is None or hist.empty:
            return pd.DataFrame()

        hist = hist.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        cols = ["open", "high", "low", "close", "volume"]
        out = hist[[c for c in cols if c in hist.columns]].copy()
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out.index.name = "date"
        return out

    def fundamentals(self, symbol: str) -> Fundamentals:
        f = Fundamentals(symbol=symbol)
        try:
            info = self._yf.Ticker(symbol).info or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance info fetch failed for %s: %s", symbol, exc)
            return f

        f.name = info.get("shortName") or info.get("longName")
        f.sector = info.get("sector")
        f.industry = info.get("industry")
        f.market_cap = info.get("marketCap")
        f.pe = info.get("trailingPE")
        f.forward_pe = info.get("forwardPE")
        f.peg = info.get("pegRatio") or info.get("trailingPegRatio")
        f.price_to_book = info.get("priceToBook")
        f.dividend_yield = info.get("dividendYield")
        f.profit_margin = info.get("profitMargins")
        f.revenue_growth = info.get("revenueGrowth")
        f.earnings_growth = info.get("earningsGrowth")
        f.debt_to_equity = info.get("debtToEquity")
        f.beta = info.get("beta")
        f.next_earnings = self._extract_earnings(info)
        return f

    def next_earnings(self, symbol: str) -> date | None:
        try:
            info = self._yf.Ticker(symbol).info or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance earnings fetch failed for %s: %s", symbol, exc)
            return None
        return self._extract_earnings(info)

    @staticmethod
    def _extract_earnings(info: dict) -> date | None:
        ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(ts).date()
        except (ValueError, OSError, TypeError):
            return None
