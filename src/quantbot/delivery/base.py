"""Notifier interface — Telegram today, email/Discord later."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str) -> None:
        """Deliver a message. Implementations handle their own length limits."""
