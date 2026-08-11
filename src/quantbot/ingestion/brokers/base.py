"""BrokerAdapter interface. Every broker (IBKR today, Alpaca/Schwab later) maps its
raw payloads onto the shared models so downstream code is broker-agnostic."""

from __future__ import annotations

from abc import ABC, abstractmethod

from quantbot.models import Portfolio


class BrokerAdapter(ABC):
    """Read-only portfolio access. Implementations must return normalized models."""

    @abstractmethod
    def get_portfolio(self) -> Portfolio:
        """Fetch the current holdings snapshot as a normalized Portfolio."""
        raise NotImplementedError
