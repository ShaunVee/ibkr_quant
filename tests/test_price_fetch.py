"""Tests for the cache-first price fetcher (pipeline._fetch_prices).

Covers: full fetch on a cold cache, delta fetch on a warm cache, provider-failure
fallback to the cached series, and provider-rows-win on overlapping dates.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from quantbot.pipeline import _DELTA_BUFFER_DAYS, _DELTA_MIN_DAYS, _fetch_prices
from quantbot.storage.db import Store


def _df(dates: list[str], closes: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    idx.name = "date"
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100.0] * len(dates),
        },
        index=idx,
    )


class _FakeMarket:
    """Records daily_prices calls and returns canned frames per symbol."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames
        self.calls: list[tuple[str, int]] = []

    def daily_prices(self, symbol: str, days: int) -> pd.DataFrame:
        self.calls.append((symbol, days))
        return self._frames.get(symbol, pd.DataFrame()).copy()


def test_cold_cache_fetches_full_window_and_caches(tmp_path):
    store = Store(tmp_path / "t.db")
    full = _df(["2026-01-02", "2026-01-05"], [10.0, 11.0])
    market = _FakeMarket({"AAPL": full})

    df = _fetch_prices(store, market, "AAPL", 400, date(2026, 1, 6))

    assert len(df) == 2
    assert market.calls == [("AAPL", 400)]  # full window, not a delta
    assert len(store.get_prices("AAPL")) == 2


def test_warm_cache_delta_fetches_only_the_gap(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_prices(
        "AAPL",
        [
            {"px_date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
            {"px_date": "2026-01-05", "open": 11, "high": 11, "low": 11, "close": 11, "volume": 1},
        ],
    )
    fresh = _df(["2026-01-06", "2026-01-07"], [12.0, 13.0])
    market = _FakeMarket({"AAPL": fresh})

    df = _fetch_prices(store, market, "AAPL", 400, date(2026, 1, 8))

    assert len(df) == 4
    # Gap of 3 days + buffer, but never below the minimum window.
    assert market.calls == [("AAPL", max(3 + _DELTA_BUFFER_DAYS, _DELTA_MIN_DAYS))]
    cached_dates = [r["px_date"] for r in store.get_prices("AAPL")]
    assert cached_dates == ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]


def test_provider_failure_falls_back_to_cached_series(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_prices(
        "AAPL",
        [
            {"px_date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
            {"px_date": "2026-01-05", "open": 11, "high": 11, "low": 11, "close": 11, "volume": 1},
        ],
    )
    market = _FakeMarket({})  # provider returns nothing for everything

    df = _fetch_prices(store, market, "AAPL", 400, date(2026, 1, 8))

    assert len(df) == 2
    assert df["close"].tolist() == [10.0, 11.0]


def test_provider_rows_win_on_overlapping_dates(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_prices(
        "AAPL",
        [{"px_date": "2026-01-05", "open": 11, "high": 11, "low": 11, "close": 11, "volume": 1}],
    )
    # Provider returns a *corrected* close for the overlapping date plus a new one.
    fresh = _df(["2026-01-05", "2026-01-06"], [11.5, 12.0])
    market = _FakeMarket({"AAPL": fresh})

    df = _fetch_prices(store, market, "AAPL", 400, date(2026, 1, 7))

    assert df.loc[pd.Timestamp("2026-01-05"), "close"] == 11.5  # corrected, not the cache
    assert len(df) == 2
    # The overlapping row was already cached; only genuinely new dates are written.
    assert [r["px_date"] for r in store.get_prices("AAPL")] == ["2026-01-05", "2026-01-06"]
