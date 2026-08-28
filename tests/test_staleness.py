"""The morning-brief staleness guard — no network involved.

Covers the IBKR Flex overnight-batch lag that made Saturday briefs report Thursday's
close: the pipeline keys off the statement's real session date, tolerates the one-
session lag that lag inherently produces (a known IBKR limitation), and flags only data
that is *further* behind than that — a sign the feed is actually stuck.
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


def test_one_session_batch_lag_not_flagged():
    # Saturday run (22nd) serving Thursday's (20th) close: the report is one session
    # behind the expected Friday, which is IBKR's permanent overnight-batch lag. That's
    # a known limitation, not news, so it must NOT be flagged in every brief.
    assert _staleness_flag(date(2026, 8, 20), run_date=date(2026, 8, 22)) is None


def test_abnormally_stale_data_flagged():
    # Saturday run (22nd) still serving Wednesday's (19th) close: two sessions behind
    # the expected Friday, further than the normal batch lag -> the feed looks stuck.
    flag = _staleness_flag(date(2026, 8, 19), run_date=date(2026, 8, 22))
    assert flag is not None
    assert flag.code == "STALE_DATA"
    assert flag.severity == "warn"
    assert "2026-08-19" in flag.message


def test_missing_report_date_flagged():
    flag = _staleness_flag(None, run_date=date(2026, 8, 22))
    assert flag is not None
    assert flag.code == "DATA_DATE_UNKNOWN"
    assert flag.severity == "warn"
