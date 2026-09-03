"""Normalized domain models shared across the pipeline.

These are broker- and vendor-agnostic on purpose: every BrokerAdapter and
MarketDataProvider maps its raw payloads onto these types so the analysis engine
never learns which vendor answered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Holding:
    """A single open position, normalized from whatever the broker returned."""

    symbol: str
    quantity: float
    # Cost basis and market value are in the account's base currency.
    avg_cost: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    currency: str = "USD"
    asset_class: str = "STK"       # STK | OPT | FUT | CASH | FUND | ...
    con_id: int | None = None      # IBKR contract id, when available
    account_id: str | None = None

    @property
    def cost_basis(self) -> float | None:
        if self.avg_cost is None:
            return None
        return self.avg_cost * self.quantity

    @property
    def unrealized_pnl(self) -> float | None:
        if self.market_value is None or self.cost_basis is None:
            return None
        return self.market_value - self.cost_basis


@dataclass(slots=True)
class AccountSummary:
    """Top-level account figures at snapshot time."""

    account_id: str
    base_currency: str = "USD"
    net_liquidation: float | None = None
    total_cash: float | None = None
    as_of: datetime | None = None


@dataclass(slots=True)
class Portfolio:
    """The full snapshot handed from ingestion to analysis."""

    account: AccountSummary
    holdings: list[Holding] = field(default_factory=list)
    as_of: datetime = field(default_factory=_utcnow)
    # The trading session the statement actually reflects (from IBKR's own report date),
    # as opposed to `as_of`, which is when we fetched it. None if the broker didn't say.
    # This is the session the P&L/marks belong to — the brief keys off it, not wall-clock.
    report_date: date | None = None

    @property
    def equity_holdings(self) -> list[Holding]:
        return [h for h in self.holdings if h.asset_class == "STK"]

    @property
    def total_market_value(self) -> float:
        return sum(h.market_value or 0.0 for h in self.holdings)

    @property
    def invested_value(self) -> float:
        """Market value of non-cash positions."""
        return sum(
            h.market_value or 0.0 for h in self.holdings if h.asset_class != "CASH"
        )


@dataclass(slots=True)
class Fundamentals:
    """Per-symbol fundamental snapshot. Fields are optional — vendors vary."""

    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    price_to_book: float | None = None
    dividend_yield: float | None = None
    profit_margin: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    debt_to_equity: float | None = None
    beta: float | None = None
    next_earnings: date | None = None


@dataclass(slots=True)
class TechnicalSnapshot:
    """Per-symbol technical indicators computed from cached prices."""

    symbol: str
    last_price: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    golden_cross: bool | None = None       # sma50 > sma200
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bollinger_pct: float | None = None     # 0..1 position within bands
    atr14: float | None = None
    pct_from_52w_high: float | None = None
    pct_from_52w_low: float | None = None
    ret_1m: float | None = None
    ret_3m: float | None = None
    ret_6m: float | None = None
    obv: float | None = None               # On-Balance Volume (cumulative)
    obv_trend: str | None = None           # "rising" | "falling" (EMA cross of OBV)


@dataclass(slots=True)
class TechnicalSignal:
    """A distilled technical read for one holding — a short tag, not a raw number.

    tone drives display colour: bull (green), bear (red), warn (amber caution),
    neutral (plain). code is stable for tests; label is the human-facing chip text.
    """

    code: str          # e.g. TREND_UP, MACD_BULL, RSI_OVERBOUGHT, OBV_RISING
    label: str         # e.g. "uptrend", "MACD+", "overbought", "OBV rising"
    tone: str          # bull | bear | warn | neutral


@dataclass(slots=True)
class Flag:
    """A rule-based risk flag: what to look at, never a buy/sell verdict."""

    code: str                              # e.g. CONCENTRATION, HIGH_BETA
    severity: str                          # info | warn | high
    symbol: str | None                     # None for portfolio-level flags
    message: str                           # plain-English reason
