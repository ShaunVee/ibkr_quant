"""Tests for fundamentals gathering and manual per-symbol overrides."""

from __future__ import annotations

from quantbot.analysis import fundamental
from quantbot.models import AccountSummary, Fundamentals, Holding, Portfolio


class _FakeMarket:
    """Stands in for MarketData: returns whatever sector we seed per symbol."""

    def __init__(self, sectors: dict[str, str | None]) -> None:
        self._sectors = sectors

    def fundamentals(self, symbol: str) -> Fundamentals:
        return Fundamentals(symbol=symbol, sector=self._sectors.get(symbol))

    def next_earnings(self, symbol: str):  # noqa: ANN201 - test stub
        return None


def _portfolio(*symbols: str) -> Portfolio:
    holdings = [Holding(symbol=s, quantity=1, market_value=100, asset_class="STK") for s in symbols]
    return Portfolio(
        account=AccountSummary(account_id="U1", base_currency="USD"),
        holdings=holdings,
    )


def test_override_fills_blank_sector():
    p = _portfolio("ETHA")
    market = _FakeMarket({"ETHA": None})
    out = fundamental.gather(p, market, overrides={"ETHA": {"sector": "Crypto ETF"}})
    assert out["ETHA"].sector == "Crypto ETF"


def test_override_is_case_insensitive_on_symbol():
    p = _portfolio("ETHA")
    market = _FakeMarket({"ETHA": None})
    out = fundamental.gather(p, market, overrides={"etha": {"sector": "Crypto ETF"}})
    assert out["ETHA"].sector == "Crypto ETF"


def test_override_wins_over_provider_value():
    p = _portfolio("META")
    market = _FakeMarket({"META": "Technology"})
    out = fundamental.gather(p, market, overrides={"META": {"sector": "Communication Services"}})
    assert out["META"].sector == "Communication Services"


def test_provider_value_kept_when_no_override():
    p = _portfolio("AAPL")
    market = _FakeMarket({"AAPL": "Technology"})
    out = fundamental.gather(p, market, overrides={"ETHA": {"sector": "Crypto ETF"}})
    assert out["AAPL"].sector == "Technology"


def test_unknown_override_field_ignored():
    p = _portfolio("ETHA")
    market = _FakeMarket({"ETHA": None})
    out = fundamental.gather(p, market, overrides={"ETHA": {"not_a_field": "x", "sector": "Crypto ETF"}})
    assert out["ETHA"].sector == "Crypto ETF"
    assert not hasattr(out["ETHA"], "not_a_field")


def test_no_overrides_is_noop():
    p = _portfolio("ETHA")
    market = _FakeMarket({"ETHA": None})
    out = fundamental.gather(p, market)
    assert out["ETHA"].sector is None
