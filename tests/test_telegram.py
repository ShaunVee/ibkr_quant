"""TelegramNotifier retry/backoff: a transient timeout should not drop the HTML brief."""

from __future__ import annotations

import pytest
import requests

from quantbot.delivery import telegram
from quantbot.delivery.telegram import AmbiguousDeliveryError, TelegramNotifier


class _Resp:
    def __init__(self, status_code=200, text="ok", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


class _Session:
    """Replays a scripted list of responses/exceptions across successive .post calls."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def post(self, url, **kwargs):
        # Snapshot the upload's start position, then consume the body like requests
        # would, so a retry only sends bytes if the handle was rewound first.
        file_pos = None
        files = kwargs.get("files")
        if files:
            fh = files["document"][1]
            file_pos = fh.tell()
            fh.read()
        self.calls.append({"kwargs": kwargs, "file_pos": file_pos})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(telegram.time, "sleep", lambda *_: None)


def test_send_retries_after_timeout():
    session = _Session([requests.exceptions.Timeout("read timed out"), _Resp()])
    notifier = TelegramNotifier("t", "42", session=session)
    notifier.send("hello")  # should not raise — succeeds on the second attempt
    assert len(session.calls) == 2


def test_send_document_rewinds_file_before_retry(tmp_path):
    # Documents are non-idempotent, so only a 429 (request rejected, not processed) is
    # retried. When it is, the multipart body must restart from byte 0.
    doc = tmp_path / "brief.html"
    doc.write_text("<html>hi</html>")
    session = _Session([
        _Resp(status_code=429, payload={"parameters": {"retry_after": 0}}),
        _Resp(),
    ])
    notifier = TelegramNotifier("t", "42", session=session)

    notifier.send_document(doc, "caption")

    assert len(session.calls) == 2
    assert session.calls[1]["file_pos"] == 0


def test_send_document_does_not_retry_on_504(tmp_path):
    # A 504 likely means the upload was processed; retrying would duplicate the file.
    doc = tmp_path / "brief.html"
    doc.write_text("<html>hi</html>")
    session = _Session([_Resp(status_code=504, text="Gateway Timeout")])
    notifier = TelegramNotifier("t", "42", session=session)

    with pytest.raises(AmbiguousDeliveryError):
        notifier.send_document(doc, "caption")
    assert len(session.calls) == 1  # no retry — no duplicate delivery


def test_send_document_ambiguous_on_network_error(tmp_path):
    # A dropped connection on a non-idempotent upload is "delivery uncertain", not a
    # clean failure — surface it as AmbiguousDeliveryError without retrying.
    doc = tmp_path / "brief.html"
    doc.write_text("<html>hi</html>")
    session = _Session([requests.exceptions.ConnectionError("boom")])
    notifier = TelegramNotifier("t", "42", session=session)

    with pytest.raises(AmbiguousDeliveryError):
        notifier.send_document(doc, "caption")
    assert len(session.calls) == 1


def test_gives_up_after_max_attempts():
    session = _Session([requests.exceptions.Timeout("t")] * 3)
    notifier = TelegramNotifier("t", "42", session=session, max_attempts=3)
    with pytest.raises(requests.exceptions.Timeout):
        notifier.send("hello")
    assert len(session.calls) == 3


def test_client_error_is_not_retried():
    session = _Session([_Resp(status_code=400, text="bad request")])
    notifier = TelegramNotifier("t", "42", session=session)
    with pytest.raises(requests.exceptions.HTTPError):
        notifier.send("hello")
    assert len(session.calls) == 1  # fail fast, no retry on 4xx
