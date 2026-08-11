"""Macro context: latest CPI/rates prints with trend + upcoming economic events."""

from __future__ import annotations

from dataclasses import dataclass, field

from quantbot.config import Config
from quantbot.ingestion.marketdata.composite import MarketData


@dataclass(slots=True)
class MacroReading:
    key: str                 # e.g. "cpi"
    series_id: str           # FRED id
    latest: float | None = None
    latest_date: str | None = None
    prev: float | None = None
    change: float | None = None          # latest - prev (level change)
    yoy_pct: float | None = None         # year-over-year % (when 12+ obs available)


@dataclass(slots=True)
class MacroSnapshot:
    readings: list[MacroReading] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def gather(config: Config, market: MarketData) -> MacroSnapshot:
    snapshot = MacroSnapshot()
    for key, series_id in config.macro_series.items():
        series = market.macro_series(series_id, observations=13)
        reading = MacroReading(key=key, series_id=series_id)
        if not series.empty:
            reading.latest = float(series.iloc[-1])
            reading.latest_date = str(series.index[-1].date())
            if len(series) >= 2:
                reading.prev = float(series.iloc[-2])
                reading.change = reading.latest - reading.prev
            if len(series) >= 13 and series.iloc[-13] not in (0, None):
                reading.yoy_pct = float(
                    (series.iloc[-1] / series.iloc[-13] - 1.0) * 100.0
                )
        snapshot.readings.append(reading)

    days_ahead = int(config.raw.get("macro", {}).get("events_days_ahead", 14))
    snapshot.events = market.upcoming_events(days_ahead=days_ahead)
    return snapshot
