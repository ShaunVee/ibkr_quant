"""Tests for the SQLite store — snapshots, weights, price cache, history."""

from __future__ import annotations

from datetime import date

from quantbot.models import AccountSummary, Holding, Portfolio
from quantbot.storage.db import Store


def _portfolio(net_liq=100000.0):
    return Portfolio(
        account=AccountSummary(
            account_id="U1", base_currency="USD", net_liquidation=net_liq, total_cash=5000.0
        ),
        holdings=[
            Holding(symbol="AAPL", quantity=100, market_value=60000, asset_class="STK"),
            Holding(symbol="MSFT", quantity=50, market_value=30000, asset_class="STK"),
        ],
    )


def test_save_snapshot_and_history(tmp_path):
    store = Store(tmp_path / "test.db")
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 10))
    store.save_snapshot(_portfolio(net_liq=110000.0), snapshot_date=date(2026, 8, 11))

    history = store.portfolio_value_history("U1")
    assert len(history) == 2
    assert history[0][0] == "2026-08-10"
    assert history[1][1] == 90000.0  # invested value = 60k + 30k


def test_snapshot_summary_counts_and_latest(tmp_path):
    store = Store(tmp_path / "test.db")
    assert store.snapshot_summary() == {"count": 0, "latest_date": None}
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 10))
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 11))
    assert store.snapshot_summary() == {"count": 2, "latest_date": "2026-08-11"}


def test_snapshot_upsert_is_idempotent_per_day(tmp_path):
    store = Store(tmp_path / "test.db")
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 11))
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 11))  # same day again
    history = store.portfolio_value_history("U1")
    assert len(history) == 1  # replaced, not duplicated


def test_price_cache_roundtrip(tmp_path):
    store = Store(tmp_path / "test.db")
    rows = [
        {"px_date": "2026-08-10", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
        {"px_date": "2026-08-11", "open": 1.5, "high": 2.5, "low": 1, "close": 2, "volume": 200},
    ]
    store.upsert_prices("AAPL", rows)
    got = store.get_prices("AAPL")
    assert len(got) == 2
    assert got[-1]["close"] == 2

    # Upsert same dates updates rather than duplicates.
    store.upsert_prices("AAPL", [{**rows[0], "close": 9.9}])
    got = store.get_prices("AAPL")
    assert len(got) == 2
    assert got[0]["close"] == 9.9


# --- reports (upsert + narrative cache) -----------------------------------

def test_save_report_upserts_per_day_and_format(tmp_path):
    store = Store(tmp_path / "test.db")
    d = date(2026, 8, 11)
    store.save_report(d, "html", "<html>v1</html>")
    store.save_report(d, "markdown", "v1")
    store.save_report(d, "html", "<html>v2</html>")  # same day+format again
    store.save_report(date(2026, 8, 12), "html", "<html>next</html>")

    with store._conn() as conn:
        rows = conn.execute("SELECT snapshot_date, format, body FROM reports").fetchall()
    assert len(rows) == 3  # replaced, not duplicated
    assert store.get_report(d, "html") == "<html>v2</html>"
    assert store.get_report(d, "markdown") == "v1"
    assert store.get_report(d, "narrative") is None


def test_migration_collapses_pre_existing_report_duplicates(tmp_path):
    import sqlite3

    db = tmp_path / "legacy.db"
    # Build a legacy database: reports table WITHOUT the unique index, already
    # carrying same-day duplicates (what pre-fix re-runs produced).
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE reports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "snapshot_date TEXT NOT NULL, format TEXT NOT NULL, "
        "body TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO reports (snapshot_date, format, body, created_at) VALUES (?, ?, ?, ?)",
        [
            ("2026-08-11", "html", "old", "t1"),
            ("2026-08-11", "html", "newest", "t2"),
            ("2026-08-11", "markdown", "md", "t3"),
        ],
    )
    conn.commit()
    conn.close()

    store = Store(db)  # opening runs the dedupe + unique index migration
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT body FROM reports WHERE snapshot_date='2026-08-11' AND format='html'"
        ).fetchall()
    assert len(rows) == 1
    # The kept row is the latest insert.
    assert rows[0]["body"] == "newest"
    # And the upsert path works on the migrated table.
    store.save_report(date(2026, 8, 11), "html", "v3")
    assert store.get_report(date(2026, 8, 11), "html") == "v3"
