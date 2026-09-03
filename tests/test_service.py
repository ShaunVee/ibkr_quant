"""Command listener: parsing, owner-only dispatch, and /report triggering a run."""

from __future__ import annotations

from datetime import date

import pytest

from quantbot import service
from quantbot.models import AccountSummary, Holding, Portfolio
from quantbot.service import CommandListener, make_trigger, parse_command
from quantbot.storage.db import Store


class _FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


def _listener(chat_id: str = "42", db_path=None) -> tuple[CommandListener, _FakeNotifier]:
    lis = CommandListener(
        "token", chat_id, make_trigger(), "Asia/Singapore",
        **({"db_path": db_path} if db_path is not None else {}),
    )
    fake = _FakeNotifier()
    lis._notifier = fake
    return lis, fake


def _portfolio() -> Portfolio:
    return Portfolio(
        account=AccountSummary(
            account_id="U1", base_currency="USD", net_liquidation=100000.0, total_cash=5000.0
        ),
        holdings=[Holding(symbol="AAPL", quantity=100, market_value=60000, asset_class="STK")],
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/report", "/report"),
        ("  /Report  ", "/report"),
        ("/report@QuantBot", "/report"),
        ("/status extra args", "/status"),
        ("hello", "hello"),
        ("", ""),
        (None, ""),
    ],
)
def test_parse_command(text, expected):
    assert parse_command(text) == expected


def test_ignores_unauthorized_chat(monkeypatch):
    ran = []
    monkeypatch.setattr(service, "run_pipeline", lambda **kw: ran.append(True) or "ok")
    lis, fake = _listener(chat_id="42")

    lis._handle({"chat": {"id": 999}, "text": "/report"})  # not the owner

    assert fake.sent == []          # no reply
    assert ran == []                # and no run triggered


def test_help_replies(monkeypatch):
    lis, fake = _listener(chat_id="42")
    lis._handle({"chat": {"id": 42}, "text": "/help"})
    assert len(fake.sent) == 1 and "/report" in fake.sent[0]


def test_report_triggers_serialized_run(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "run_pipeline", lambda **kw: calls.append(True) or "ok")

    lis, fake = _listener(chat_id="42")
    lis._handle({"chat": {"id": 42}, "text": "/report"})

    # First reply is the immediate ack; the run happens on a worker thread.
    assert fake.sent[0].startswith("🚀")
    for t in threading_enumerate_workers():
        t.join(timeout=5)
    assert calls == [True]
    assert "✅ Brief sent." in fake.sent


def test_status_reports_snapshot_data(tmp_path):
    db = tmp_path / "portfolio.db"
    store = Store(db)
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 18))
    store.save_snapshot(_portfolio(), snapshot_date=date(2026, 8, 19))

    lis, fake = _listener(chat_id="42", db_path=db)
    lis._handle({"chat": {"id": 42}, "text": "/status"})

    assert len(fake.sent) == 1
    msg = fake.sent[0]
    assert "Next scheduled run:" in msg
    assert "2 snapshots, latest 2026-08-19" in msg


def test_status_handles_empty_db(tmp_path):
    lis, fake = _listener(chat_id="42", db_path=tmp_path / "empty.db")
    lis._handle({"chat": {"id": 42}, "text": "/status"})
    assert "no snapshots recorded yet" in fake.sent[0]


def threading_enumerate_workers():
    import threading

    return [t for t in threading.enumerate() if t is not threading.main_thread()]
