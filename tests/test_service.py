"""Command listener: parsing, owner-only dispatch, and /report triggering a run."""

from __future__ import annotations

import pytest

from quantbot import service
from quantbot.service import CommandListener, make_trigger, parse_command


class _FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


def _listener(chat_id: str = "42") -> tuple[CommandListener, _FakeNotifier]:
    lis = CommandListener("token", chat_id, make_trigger(), "Asia/Singapore")
    fake = _FakeNotifier()
    lis._notifier = fake
    return lis, fake


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
    monkeypatch.setattr(service, "run_pipeline", lambda: ran.append(True) or "ok")
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
    monkeypatch.setattr(service, "run_pipeline", lambda: calls.append(True) or "ok")

    lis, fake = _listener(chat_id="42")
    lis._handle({"chat": {"id": 42}, "text": "/report"})

    # First reply is the immediate ack; the run happens on a worker thread.
    assert fake.sent[0].startswith("🚀")
    for t in threading_enumerate_workers():
        t.join(timeout=5)
    assert calls == [True]
    assert "✅ Brief sent." in fake.sent


def threading_enumerate_workers():
    import threading

    return [t for t in threading.enumerate() if t is not threading.main_thread()]
