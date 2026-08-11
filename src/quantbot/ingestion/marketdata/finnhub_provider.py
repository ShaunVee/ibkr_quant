"""Finnhub-backed earnings + economic-calendar provider (free tier).

Free tier covers the earnings calendar and (rate-limited) economic calendar. Both
methods degrade to empty results on failure so the report still generates.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from quantbot.ingestion.marketdata.base import EarningsProvider, EconCalendarProvider

log = logging.getLogger(__name__)


class FinnhubProvider(EarningsProvider, EconCalendarProvider):
    def __init__(self, api_key: str) -> None:
        import finnhub  # lazy import

        self._client = finnhub.Client(api_key=api_key)

    def next_earnings(self, symbol: str) -> date | None:
        today = date.today()
        try:
            resp = self._client.earnings_calendar(
                _from=today.isoformat(),
                to=(today + timedelta(days=120)).isoformat(),
                symbol=symbol,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Finnhub earnings fetch failed for %s: %s", symbol, exc)
            return None

        rows = (resp or {}).get("earningsCalendar", [])
        dates = sorted(
            _parse_date(r.get("date")) for r in rows if r.get("date")
        )
        dates = [d for d in dates if d and d >= today]
        return dates[0] if dates else None

    def upcoming_events(self, days_ahead: int = 14) -> list[dict]:
        today = date.today()
        # economic_calendar is a Finnhub premium endpoint and isn't present on the
        # free-tier client; skip cleanly rather than erroring.
        fetch = getattr(self._client, "economic_calendar", None)
        if fetch is None:
            log.info("Finnhub economic calendar unavailable (premium endpoint); skipping.")
            return []
        try:
            resp = fetch()
        except Exception as exc:  # noqa: BLE001
            log.info("Finnhub economic calendar fetch skipped: %s", exc)
            return []

        events = (resp or {}).get("economicCalendar", []) or []
        horizon = today + timedelta(days=days_ahead)
        out = []
        for e in events:
            d = _parse_date((e.get("time") or "")[:10])
            if d and today <= d <= horizon:
                out.append(
                    {
                        "date": d.isoformat(),
                        "event": e.get("event"),
                        "country": e.get("country"),
                        "impact": e.get("impact"),
                        "actual": e.get("actual"),
                        "estimate": e.get("estimate"),
                    }
                )
        out.sort(key=lambda x: x["date"])
        return out


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
