"""The cron trigger fires when we expect: 07:00 SGT on weekdays only."""

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


def test_weekend_rolls_to_monday():
    # Friday after the fire, and Saturday, both land on Monday 07:00.
    monday = (8, 17, 7)
    assert (
        _next(datetime(2026, 8, 14, 8, 0, tzinfo=SGT)).month,
        _next(datetime(2026, 8, 14, 8, 0, tzinfo=SGT)).day,
        _next(datetime(2026, 8, 14, 8, 0, tzinfo=SGT)).hour,
    ) == monday
    assert (
        _next(datetime(2026, 8, 15, 12, 0, tzinfo=SGT)).month,
        _next(datetime(2026, 8, 15, 12, 0, tzinfo=SGT)).day,
        _next(datetime(2026, 8, 15, 12, 0, tzinfo=SGT)).hour,
    ) == monday


def test_env_overrides_time(monkeypatch):
    monkeypatch.setenv("QUANTBOT_SCHEDULE_HOUR", "9")
    monkeypatch.setenv("QUANTBOT_SCHEDULE_MINUTE", "30")
    got = _next(datetime(2026, 8, 12, 6, 0, tzinfo=SGT))
    assert (got.hour, got.minute) == (9, 30)
