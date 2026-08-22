"""Telegram delivery via the Bot API. Handles the 4096-char limit by chunking on
line boundaries. Uses HTML parse mode (only & < > need escaping — done upstream)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from quantbot.delivery.base import Notifier

log = logging.getLogger(__name__)

_TELEGRAM_LIMIT = 4096
# Leave headroom below the hard limit for safety.
_CHUNK_SIZE = 3800
# Telegram photo captions are capped far lower than message bodies.
_CAPTION_LIMIT = 1024

# Retry transient failures before giving up (a single slow response used to drop the
# HTML brief straight to the text fallback). Document uploads legitimately take longer
# than a message, so they get a roomier timeout.
_MESSAGE_TIMEOUT = 30
_DOCUMENT_TIMEOUT = 180
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 3.0  # seconds; multiplied by the attempt number

# Network errors worth retrying (as opposed to a 4xx that will never succeed).
_RETRYABLE_EXC = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)


class AmbiguousDeliveryError(Exception):
    """A non-idempotent request (a document upload) failed *after* the server most
    likely accepted it — a 504 gateway timeout, a read timeout, or a dropped response.
    Retrying would duplicate the delivery, so we stop and signal "probably delivered"
    to the caller, which must NOT degrade to a full resend."""


class TelegramNotifier(Notifier):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        parse_mode: str = "HTML",
        session: requests.Session | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._url = f"{self._base}/sendMessage"
        self._document_url = f"{self._base}/sendDocument"
        self._chat_id = chat_id
        self._parse_mode = parse_mode
        self._session = session or requests.Session()
        self._max_attempts = max_attempts

    def send(self, text: str) -> None:
        for chunk in _chunk(text, _CHUNK_SIZE):
            self._send_one(chunk)

    def send_document(
        self, doc_path: str | Path, caption: str = "", *, filename: str | None = None
    ) -> None:
        """Upload a file as a document. An .html file opens in Telegram's in-app browser
        when tapped — full-quality, self-contained. Caption uses the configured parse
        mode and is truncated to Telegram's caption limit."""
        path = Path(doc_path)
        with path.open("rb") as fh:
            self._post(
                self._document_url,
                what="sendDocument",
                timeout=_DOCUMENT_TIMEOUT,
                idempotent=False,
                data={
                    "chat_id": self._chat_id,
                    "caption": caption[:_CAPTION_LIMIT],
                    "parse_mode": self._parse_mode,
                },
                files={"document": (filename or path.name, fh, "text/html")},
            )

    def _send_one(self, text: str) -> None:
        self._post(
            self._url,
            what="sendMessage",
            timeout=_MESSAGE_TIMEOUT,
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": self._parse_mode,
                "disable_web_page_preview": True,
            },
        )

    def _post(
        self, url: str, *, what: str, timeout: int, idempotent: bool = True, **kwargs
    ) -> requests.Response:
        """POST with retry/backoff. Retries transient network errors, Telegram 5xx, and
        429 (honoring retry_after); raises on a non-retryable 4xx or after exhausting
        attempts, so callers can degrade (e.g. HTML doc -> text) only on real failure.

        When `idempotent` is False (document uploads), a network error or 5xx is treated
        as "probably already delivered": we raise AmbiguousDeliveryError instead of
        retrying, because a retry would duplicate the upload. Only a 429 (which means the
        request was rejected, not processed) is still safe to retry."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            _rewind_files(kwargs.get("files"))  # multipart bodies must restart each try
            try:
                resp = self._session.post(url, timeout=timeout, **kwargs)
            except _RETRYABLE_EXC as exc:
                last_exc = exc
                log.warning(
                    "Telegram %s attempt %d/%d failed: %s",
                    what, attempt, self._max_attempts, exc,
                )
                if not idempotent:
                    raise AmbiguousDeliveryError(
                        f"{what} network error, delivery uncertain: {exc}"
                    ) from exc
            else:
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 429:
                    wait = int(resp.json().get("parameters", {}).get("retry_after", _BACKOFF_BASE))
                    log.warning("Telegram %s rate-limited; waiting %ds", what, wait)
                    time.sleep(wait)
                    continue
                if 500 <= resp.status_code < 600:
                    log.warning(
                        "Telegram %s got %d (attempt %d/%d): %s",
                        what, resp.status_code, attempt, self._max_attempts, resp.text,
                    )
                    if not idempotent:
                        # A 504/5xx on a non-idempotent upload means the backend very
                        # likely processed it; retrying just duplicates the file.
                        raise AmbiguousDeliveryError(
                            f"{what} got {resp.status_code}, delivery likely succeeded"
                        )
                else:
                    # 4xx (bad request, forbidden, …) won't fix itself — fail fast.
                    log.error("Telegram %s failed (%s): %s", what, resp.status_code, resp.text)
                    resp.raise_for_status()
            if attempt < self._max_attempts:
                time.sleep(_BACKOFF_BASE * attempt)
        if last_exc is not None:
            raise last_exc
        raise requests.exceptions.RetryError(
            f"Telegram {what} failed after {self._max_attempts} attempts"
        )


def _rewind_files(files: dict | None) -> None:
    """Seek any multipart file handles back to the start before a retry."""
    if not files:
        return
    for value in files.values():
        fh = value[1] if isinstance(value, tuple) else value
        try:
            fh.seek(0)
        except (AttributeError, OSError):
            pass


def _chunk(text: str, size: int) -> list[str]:
    """Split on line boundaries, never exceeding `size` per chunk."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        # A single over-long line is hard-split as a fallback.
        while len(line) > size:
            if current:
                chunks.append("\n".join(current))
                current, length = [], 0
            chunks.append(line[:size])
            line = line[size:]
        if length + len(line) + 1 > size and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks
