"""The morning-brief staleness guard — no network involved.

Covers the IBKR Flex overnight-batch lag that made Saturday briefs report Thursday's
close: the pipeline should key off the statement's real session date and flag data that
is older than the session we'd expect that morning.
"""

from __future__ import annotations

from datetime import date

from quantbot.pipeline import _staleness_flag


def test_fresh_data_no_flag():
    # Healthy run: statement reports the previous session (run_date - 1).
    assert _staleness_flag(date(2026, 8, 21), run_date=date(2026, 8, 22)) is None


def test_same_day_data_no_flag():
    # If IBKR ever returns same-day data, that's not stale.
    assert _staleness_flag(date(2026, 8, 22), run_date=date(2026, 8, 22)) is None


def test_sunday_run_reporting_friday_is_fresh():
    # Sun 23rd run: Fri 21st is the last real session (no Sat/Sun trading), so a
    # Friday statement is as fresh as possible and must NOT be flagged stale.
    assert _staleness_flag(date(2026, 8, 21), run_date=date(2026, 8, 23)) is None


def test_stale_data_flagged():
    # Saturday run (22nd) still serving Thursday's (20th) close -> 2 days old.
    flag = _staleness_flag(date(2026, 8, 20), run_date=date(2026, 8, 22))
    assert flag is not None
    assert flag.code == "STALE_DATA"
    assert flag.severity == "warn"
    assert "2026-08-20" in flag.message


def test_missing_report_date_flagged():
    flag = _staleness_flag(None, run_date=date(2026, 8, 22))
    assert flag is not None
    assert flag.code == "DATA_DATE_UNKNOWN"
    assert flag.severity == "warn"
