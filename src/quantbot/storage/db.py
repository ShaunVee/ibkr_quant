"""SQLite storage: daily portfolio snapshots, per-position rows, price cache, reports.

Single-user, zero-ops. Daily snapshots are what make portfolio-level risk metrics
(realized vol, drawdown, Sharpe over time) possible — that time series only exists once
we start recording it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from quantbot.models import Flag, Portfolio

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,               -- ISO date (one logical run per day)
    account_id    TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    net_liq       REAL,
    total_cash    REAL,
    invested_val  REAL,
    metrics_json  TEXT,                         -- portfolio-level metrics blob
    created_at    TEXT NOT NULL,
    UNIQUE(snapshot_date, account_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    symbol        TEXT NOT NULL,
    quantity      REAL,
    avg_cost      REAL,
    market_price  REAL,
    market_value  REAL,
    weight        REAL,
    asset_class   TEXT,
    extra_json    TEXT                          -- FA/TA fields attached at report time
);

CREATE TABLE IF NOT EXISTS prices (
    symbol   TEXT NOT NULL,
    px_date  TEXT NOT NULL,
    open     REAL, high REAL, low REAL, close REAL,
    volume   REAL,
    PRIMARY KEY (symbol, px_date)
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    format        TEXT NOT NULL,                -- 'markdown' | 'text'
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flag_history (
    snapshot_date TEXT NOT NULL,               -- ISO date (one run per day)
    account_id    TEXT NOT NULL,
    code          TEXT NOT NULL,               -- e.g. CONCENTRATION, HIGH_BETA
    symbol        TEXT NOT NULL DEFAULT '',    -- '' for portfolio-level flags
    severity      TEXT NOT NULL,
    message       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, account_id, code, symbol)
);
"""


class Store:
    """Thin repository over a SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # --- snapshots -------------------------------------------------------
    def save_snapshot(
        self,
        portfolio: Portfolio,
        *,
        metrics: dict[str, Any] | None = None,
        snapshot_date: date | None = None,
    ) -> int:
        snap_date = (snapshot_date or portfolio.as_of.date()).isoformat()
        total_mv = portfolio.invested_value
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots
                    (snapshot_date, account_id, base_currency, net_liq, total_cash,
                     invested_val, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, account_id) DO UPDATE SET
                    net_liq=excluded.net_liq, total_cash=excluded.total_cash,
                    invested_val=excluded.invested_val, metrics_json=excluded.metrics_json,
                    created_at=excluded.created_at
                """,
                (
                    snap_date,
                    portfolio.account.account_id,
                    portfolio.account.base_currency,
                    portfolio.account.net_liquidation,
                    portfolio.account.total_cash,
                    total_mv,
                    json.dumps(metrics or {}),
                    datetime.now().isoformat(),
                ),
            )
            snapshot_id = cur.lastrowid
            if not snapshot_id:  # updated existing row; fetch its id
                row = conn.execute(
                    "SELECT id FROM snapshots WHERE snapshot_date=? AND account_id=?",
                    (snap_date, portfolio.account.account_id),
                ).fetchone()
                snapshot_id = row["id"]

            # Replace position rows for this snapshot.
            conn.execute("DELETE FROM positions WHERE snapshot_id=?", (snapshot_id,))
            invested = total_mv or 1.0
            for h in portfolio.holdings:
                weight = (h.market_value or 0.0) / invested if invested else None
                conn.execute(
                    """
                    INSERT INTO positions
                        (snapshot_id, symbol, quantity, avg_cost, market_price,
                         market_value, weight, asset_class, extra_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        h.symbol,
                        h.quantity,
                        h.avg_cost,
                        h.market_price,
                        h.market_value,
                        weight,
                        h.asset_class,
                        None,
                    ),
                )
        return int(snapshot_id)

    def prior_position_weights(
        self, account_id: str, before_date: date
    ) -> dict[str, float]:
        """Position weights from the most recent snapshot strictly before `before_date`.

        Used to measure today's move on the book actually held into today. Empty dict
        when there is no earlier snapshot.
        """
        before = before_date.isoformat()
        with self._conn() as conn:
            prev = conn.execute(
                "SELECT id FROM snapshots WHERE account_id=? AND snapshot_date < ? "
                "ORDER BY snapshot_date DESC LIMIT 1",
                (account_id, before),
            ).fetchone()
            if prev is None:
                return {}
            rows = conn.execute(
                "SELECT symbol, weight FROM positions "
                "WHERE snapshot_id=? AND asset_class != 'CASH'",
                (prev["id"],),
            ).fetchall()
        return {r["symbol"]: r["weight"] for r in rows if r["weight"] is not None}

    def portfolio_value_history(self, account_id: str) -> list[tuple[str, float]]:
        """Return [(iso_date, invested_val), ...] ascending — for realized risk metrics."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT snapshot_date, invested_val FROM snapshots "
                "WHERE account_id=? ORDER BY snapshot_date ASC",
                (account_id,),
            ).fetchall()
        return [(r["snapshot_date"], r["invested_val"]) for r in rows]

    # --- price cache -----------------------------------------------------
    def upsert_prices(self, symbol: str, rows: list[dict[str, Any]]) -> None:
        """rows: list of {date, open, high, low, close, volume}."""
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO prices (symbol, px_date, open, high, low, close, volume)
                VALUES (:symbol, :px_date, :open, :high, :low, :close, :volume)
                ON CONFLICT(symbol, px_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume
                """,
                [{"symbol": symbol, **r} for r in rows],
            )

    def get_prices(self, symbol: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT px_date, open, high, low, close, volume FROM prices "
                "WHERE symbol=? ORDER BY px_date ASC",
                (symbol,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- reports ---------------------------------------------------------
    def save_report(self, snapshot_date: date, fmt: str, body: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO reports (snapshot_date, format, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (snapshot_date.isoformat(), fmt, body, datetime.now().isoformat()),
            )

    # --- flag history (the memory layer) ---------------------------------
    def save_flags(
        self, account_id: str, snapshot_date: date, flags: list[Flag]
    ) -> None:
        """Persist today's flags. Idempotent per (day, account, code, symbol) so a
        re-run of the same day replaces rather than duplicates."""
        snap = snapshot_date.isoformat()
        now = datetime.now().isoformat()
        with self._conn() as conn:
            # Clear this day's rows first so a flag that stopped firing today doesn't
            # linger from an earlier run of the same date.
            conn.execute(
                "DELETE FROM flag_history WHERE snapshot_date=? AND account_id=?",
                (snap, account_id),
            )
            conn.executemany(
                """
                INSERT INTO flag_history
                    (snapshot_date, account_id, code, symbol, severity, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (snap, account_id, f.code, f.symbol or "", f.severity, f.message, now)
                    for f in flags
                ],
            )

    def prior_flags(self, account_id: str, before_date: date) -> list[Flag] | None:
        """Flags from the most recent run strictly before `before_date`.

        Returns None when there is no earlier run at all — the caller uses that to skip
        new/cleared classification on the first day of tracking.
        """
        before = before_date.isoformat()
        with self._conn() as conn:
            prev = conn.execute(
                "SELECT MAX(snapshot_date) AS d FROM flag_history "
                "WHERE account_id=? AND snapshot_date < ?",
                (account_id, before),
            ).fetchone()
            if prev is None or prev["d"] is None:
                return None
            rows = conn.execute(
                "SELECT code, symbol, severity, message FROM flag_history "
                "WHERE account_id=? AND snapshot_date=?",
                (account_id, prev["d"]),
            ).fetchall()
        return [
            Flag(
                code=r["code"],
                severity=r["severity"],
                symbol=r["symbol"] or None,
                message=r["message"],
            )
            for r in rows
        ]

    def flag_streak(
        self, account_id: str, code: str, symbol: str | None, today: date
    ) -> int:
        """Number of consecutive runs (ending today) in which this flag was present."""
        sym = symbol or ""
        today_iso = today.isoformat()
        with self._conn() as conn:
            run_dates = [
                r["snapshot_date"]
                for r in conn.execute(
                    "SELECT DISTINCT snapshot_date FROM flag_history "
                    "WHERE account_id=? AND snapshot_date <= ? ORDER BY snapshot_date DESC",
                    (account_id, today_iso),
                ).fetchall()
            ]
            present = {
                r["snapshot_date"]
                for r in conn.execute(
                    "SELECT snapshot_date FROM flag_history "
                    "WHERE account_id=? AND code=? AND symbol=? AND snapshot_date <= ?",
                    (account_id, code, sym, today_iso),
                ).fetchall()
            }
        streak = 0
        for d in run_dates:
            if d in present:
                streak += 1
            else:
                break
        return streak
