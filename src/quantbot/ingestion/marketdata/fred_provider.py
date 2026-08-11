"""FRED-backed macro provider (free, authoritative). CPI, rates, unemployment, etc."""

from __future__ import annotations

import logging

import pandas as pd

from quantbot.ingestion.marketdata.base import MacroProvider

log = logging.getLogger(__name__)


class FredProvider(MacroProvider):
    def __init__(self, api_key: str) -> None:
        from fredapi import Fred  # lazy import

        self._fred = Fred(api_key=api_key)

    def series_latest(self, series_id: str, observations: int = 13) -> pd.Series:
        try:
            series = self._fred.get_series(series_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("FRED fetch failed for %s: %s", series_id, exc)
            return pd.Series(dtype="float64")
        if series is None or series.empty:
            return pd.Series(dtype="float64")
        return series.dropna().tail(observations)
