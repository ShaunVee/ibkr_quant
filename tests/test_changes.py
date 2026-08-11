"""Tests for the flag change/memory layer (analysis/changes.py + Store persistence)."""

from __future__ import annotations

from datetime import date

from quantbot.analysis import changes
from quantbot.models import AccountSummary, Flag, Holding, Portfolio
from quantbot.storage.db import Store


def _flag(code, symbol=None, severity="warn"):
    return Flag(code=code, severity=severity, symbol=symbol, message=f"{code} {symbol or ''}".strip())


def test_first_run_has_no_baseline():
    # prior_flags None -> nothing classified (don't flood day one with "new").
    assert changes.diff([_flag("HIGH_BETA")], None) == []


def test_new_persistent_cleared_classification():
    prior = [_flag("HIGH_BETA"), _flag("DRAWDOWN")]
    today = [_flag("HIGH_BETA"), _flag("CONCENTRATION", symbol="NVDA")]
    result = {(c.flag.code, c.flag.symbol): c.status for c in changes.diff(today, prior)}

    assert result[("HIGH_BETA", None)] == "persistent"
    assert result[("CONCENTRATION", "NVDA")] == "new"
    assert result[("DRAWDOWN", None)] == "cleared"


def test_streak_callback_used_for_persistent():
    prior = [_flag("HIGH_BETA")]
    today = [_flag("HIGH_BETA")]
    out = changes.diff(today, prior, streak_of=lambda code, sym: 6)
    persistent = [c for c in out if c.status == "persistent"][0]
    assert persistent.streak == 6


# --- Store persistence ---------------------------------------------------

def _portfolio():
    return Portfolio(
        account=AccountSummary(account_id="U1", base_currency="USD"),
        holdings=[Holding(symbol="AAPL", quantity=1, market_value=100, asset_class="STK")],
    )


def test_prior_flags_returns_last_run(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_flags("U1", date(2026, 8, 10), [_flag("HIGH_BETA")])
    store.save_flags("U1", date(2026, 8, 11), [_flag("DRAWDOWN")])

    # Querying "before 8/11" returns the 8/10 run, not 8/11 itself.
    prior = store.prior_flags("U1", date(2026, 8, 11))
    assert prior is not None
    assert [f.code for f in prior] == ["HIGH_BETA"]


def test_prior_flags_none_when_no_earlier_run(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_flags("U1", date(2026, 8, 11), [_flag("HIGH_BETA")])
    assert store.prior_flags("U1", date(2026, 8, 11)) is None


def test_save_flags_idempotent_per_day(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_flags("U1", date(2026, 8, 11), [_flag("HIGH_BETA"), _flag("DRAWDOWN")])
    store.save_flags("U1", date(2026, 8, 11), [_flag("HIGH_BETA")])  # re-run, fewer flags
    # The re-run replaces the day rather than accumulating.
    assert store.flag_streak("U1", "DRAWDOWN", None, date(2026, 8, 11)) == 0
    assert store.flag_streak("U1", "HIGH_BETA", None, date(2026, 8, 11)) == 1


def test_flag_streak_counts_consecutive_runs(tmp_path):
    store = Store(tmp_path / "t.db")
    for d in (10, 11, 12):
        store.save_flags("U1", date(2026, 8, d), [_flag("HIGH_BETA")])
    assert store.flag_streak("U1", "HIGH_BETA", None, date(2026, 8, 12)) == 3

    # A gap resets the streak: present 10 & 12, absent 11.
    store2 = Store(tmp_path / "t2.db")
    store2.save_flags("U1", date(2026, 8, 10), [_flag("HIGH_BETA")])
    store2.save_flags("U1", date(2026, 8, 11), [_flag("DRAWDOWN")])
    store2.save_flags("U1", date(2026, 8, 12), [_flag("HIGH_BETA")])
    assert store2.flag_streak("U1", "HIGH_BETA", None, date(2026, 8, 12)) == 1


def test_prior_position_weights(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 10))
    w = store.prior_position_weights("U1", date(2026, 8, 11))
    assert w == {"AAPL": 1.0}
    # No earlier snapshot -> empty.
    assert store.prior_position_weights("U1", date(2026, 8, 10)) == {}
