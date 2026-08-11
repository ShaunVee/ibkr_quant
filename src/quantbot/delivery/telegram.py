"""Telegram delivery via the Bot API. Handles the 4096-char limit by chunking on
line boundaries. Uses HTML parse mode (only & < > need escaping — done upstream)."""

from __future__ import annotations

import logging

import requests

from quantbot.delivery.base import Notifier

log = logging.getLogger(__name__)

_TELEGRAM_LIMIT = 4096
# Leave headroom below the hard limit for safety.
_CHUNK_SIZE = 3800


class TelegramNotifier(Notifier):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        parse_mode: str = "HTML",
        session: requests.Session | None = None,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._parse_mode = parse_mode
        self._session = session or requests.Session()

    def send(self, text: str) -> None:
        for chunk in _chunk(text, _CHUNK_SIZE):
            self._send_one(chunk)

    def _send_one(self, text: str) -> None:
        resp = self._session.post(
            self._url,
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": self._parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log.error("Telegram send failed (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()


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
