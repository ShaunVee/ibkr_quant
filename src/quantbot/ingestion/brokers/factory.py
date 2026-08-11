"""Broker adapter factory — maps config `providers.broker` to an implementation."""

from __future__ import annotations

from quantbot.config import Config
from quantbot.ingestion.brokers.base import BrokerAdapter
from quantbot.ingestion.brokers.ibkr_flex import IBKRFlexAdapter

_BROKERS = {
    "ibkr_flex": IBKRFlexAdapter,
}


def make_broker(config: Config) -> BrokerAdapter:
    name = config.providers.get("broker", "ibkr_flex")
    try:
        cls = _BROKERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown broker {name!r}. Known: {sorted(_BROKERS)}"
        ) from exc
    return cls(config)
