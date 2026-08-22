"""The cron trigger fires when we expect: 07:00 SGT, Tue-Sun.

Each brief reports the prior US session, so the run week is shifted one day past the
US trading week: Sat covers Friday's close (often still stale from IBKR's overnight
batch), Sun catches up to Friday's finalized close, then Mon is dark and the next run
is Tuesday.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quantbot.scheduler import make_trigger, schedule_timezone

SGT = ZoneInfo("Asia/Singapore")


@pytest.fixture(autouse=True)
def _clear_schedule_env(monkeypatch):
    # Exercise the built-in defaults, not whatever the dev shell exports.
    for var in (
        "QUANTBOT_SCHEDULE_DAYS",
        "QUANTBOT_SCHEDULE_HOUR",
        "QUANTBOT_SCHEDULE_MINUTE",
        "QUANTBOT_TZ",
        "TZ",
    ):
        monkeypatch.delenv(var, raising=False)


def _next(after: datetime) -> datetime:
    return make_trigger().get_next_fire_time(None, after)


def test_default_timezone_is_singapore():
    assert schedule_timezone() == "Asia/Singapore"


def test_fires_same_day_when_before_seven():
    # Wednesday 06:00 -> that morning at 07:00.
    got = _next(datetime(2026, 8, 12, 6, 0, tzinfo=SGT))
    assert (got.year, got.month, got.day, got.hour, got.minute) == (2026, 8, 12, 7, 0)


def test_skips_to_next_day_after_seven():
    # Wednesday 08:00 -> Thursday 07:00.
    got = _next(datetime(2026, 8, 12, 8, 0, tzinfo=SGT))
    assert (got.month, got.day, got.hour) == (8, 13, 7)


def test_friday_after_fire_rolls_to_saturday():
    # Friday 08:00 -> Saturday 07:00 (Sat covers Fri's close).
    got = _next(datetime(2026, 8, 14, 8, 0, tzinfo=SGT))
    assert (got.month, got.day, got.hour) == (8, 15, 7)


def test_saturday_after_fire_rolls_to_sunday():
    # Saturday 12:00 -> Sunday 07:00 (Sun catches up to Fri's finalized close).
    got = _next(datetime(2026, 8, 15, 12, 0, tzinfo=SGT))
    assert (got.month, got.day, got.hour) == (8, 16, 7)


def test_sunday_after_fire_rolls_to_tuesday():
    # Sunday 12:00 -> Tuesday 07:00 — Mon is dark (would only re-report Fri).
    got = _next(datetime(2026, 8, 16, 12, 0, tzinfo=SGT))
    assert (got.month, got.day, got.hour) == (8, 18, 7)


def test_env_overrides_time(monkeypatch):
    monkeypatch.setenv("QUANTBOT_SCHEDULE_HOUR", "9")
    monkeypatch.setenv("QUANTBOT_SCHEDULE_MINUTE", "30")
    got = _next(datetime(2026, 8, 12, 6, 0, tzinfo=SGT))
    assert (got.hour, got.minute) == (9, 30)
