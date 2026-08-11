"""Event radar — the risk you're walking into blind (analysis #4).

IBKR scatters earnings dates, the macro calendar, and rate sensitivity across five
screens and never tells you how much of *your* book each one touches. This consolidates
them into one forward view, weighted by exposure:

- a forward earnings calendar for the holdings, each tagged with its book weight, plus
  the total weight reporting inside the horizon ("event-weighted exposure")
- the upcoming macro calendar (CPI, FOMC, jobs) already gathered for the macro section,
  filtered to the same horizon and sorted by date
- a rates-sensitivity beta regressed from prices already in hand: each name's daily
  return against a rates proxy, so the share of the book exposed to a rate print is a
  number, not a guess

Everything is off data already fetched for other sections except the one rates-proxy
price series, so the radar degrades to None cleanly when nothing lands in the horizon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from quantbot.analysis.fundamental import days_to_earnings
from quantbot.models import Fundamentals

MIN_OBS = 60          # common history before a rate beta is trustworthy (matches Layer A)
RATE_BETA_MIN = 0.20  # |beta| to the rates proxy above which a name is "rate-sensitive"


@dataclass(slots=True)
class EarningsEvent:
    symbol: str
    day: date
    days_away: int
    weight: float


@dataclass(slots=True)
class MacroEvent:
    day: date
    event: str
    days_away: int
    impact: str | None = None
    country: str | None = None


@dataclass(slots=True)
class RateSensitivity:
    symbol: str
    weight: float
    beta: float           # signed beta to the rates proxy (proxy up => this name up)


@dataclass(slots=True)
class EventRadar:
    horizon_days: int
    earnings: list[EarningsEvent] = field(default_factory=list)
    earnings_weight: float = 0.0            # book weight reporting inside the horizon
    macro: list[MacroEvent] = field(default_factory=list)
    rate_proxy: str | None = None
    rate_sensitive: list[RateSensitivity] = field(default_factory=list)
    rate_sensitive_weight: float = 0.0      # book weight with |beta| >= RATE_BETA_MIN

    @property
    def ok(self) -> bool:
        return bool(self.earnings or self.macro or self.rate_sensitive)


def _earnings(
    fundamentals: dict[str, Fundamentals],
    weights: dict[str, float],
    horizon_days: int,
    today: date,
) -> list[EarningsEvent]:
    out: list[EarningsEvent] = []
    for sym, fund in fundamentals.items():
        dte = days_to_earnings(fund, today)
        if dte is None or dte < 0 or dte > horizon_days:
            continue
        out.append(
            EarningsEvent(
                symbol=sym,
                day=fund.next_earnings,
                days_away=dte,
                weight=float(weights.get(sym, 0.0)),
            )
        )
    out.sort(key=lambda e: (e.days_away, -e.weight))
    return out


def _macro(events: list[dict], horizon_days: int, today: date) -> list[MacroEvent]:
    out: list[MacroEvent] = []
    for e in events or []:
        raw = e.get("date")
        try:
            day = date.fromisoformat(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(day, date):
            continue
        dte = (day - today).days
        if dte < 0 or dte > horizon_days:
            continue
        out.append(
            MacroEvent(
                day=day,
                event=str(e.get("event") or "").strip() or "event",
                days_away=dte,
                impact=e.get("impact"),
                country=e.get("country"),
            )
        )
    out.sort(key=lambda m: m.days_away)
    return out


def _rate_sensitivity(
    weights: dict[str, float],
    price_frames: dict[str, pd.DataFrame],
    rate_proxy_close: pd.Series | None,
    rate_proxy: str,
    threshold: float,
) -> list[RateSensitivity]:
    if rate_proxy_close is None or rate_proxy_close.empty:
        return []
    proxy = rate_proxy_close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if len(proxy) < MIN_OBS:
        return []

    out: list[RateSensitivity] = []
    for sym, df in price_frames.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        r = df["close"].pct_change().replace([np.inf, -np.inf], np.nan)
        aligned = pd.DataFrame({"n": r, "p": proxy}).dropna()
        if len(aligned) < MIN_OBS:
            continue
        p = aligned["p"].to_numpy()
        var_p = float(np.var(p, ddof=1))
        if var_p <= 0:
            continue
        beta = float(np.cov(aligned["n"].to_numpy(), p, ddof=1)[0, 1] / var_p)
        if abs(beta) >= threshold:
            out.append(
                RateSensitivity(symbol=sym, weight=float(weights.get(sym, 0.0)), beta=beta)
            )
    out.sort(key=lambda s: -abs(s.beta))
    return out


def compute(
    fundamentals: dict[str, Fundamentals],
    weights: dict[str, float],
    *,
    macro_events: list[dict] | None = None,
    price_frames: dict[str, pd.DataFrame] | None = None,
    rate_proxy_close: pd.Series | None = None,
    rate_proxy: str = "TLT",
    horizon_days: int = 14,
    rate_beta_threshold: float = RATE_BETA_MIN,
    today: date | None = None,
) -> EventRadar | None:
    """Assemble the forward event radar. Returns None when nothing lands in the horizon."""
    today = today or date.today()
    radar = EventRadar(horizon_days=horizon_days)

    radar.earnings = _earnings(fundamentals or {}, weights, horizon_days, today)
    radar.earnings_weight = float(sum(e.weight for e in radar.earnings))

    radar.macro = _macro(macro_events or [], horizon_days, today)

    radar.rate_sensitive = _rate_sensitivity(
        weights,
        price_frames or {},
        rate_proxy_close,
        rate_proxy,
        rate_beta_threshold,
    )
    if radar.rate_sensitive:
        radar.rate_proxy = rate_proxy
        radar.rate_sensitive_weight = float(sum(s.weight for s in radar.rate_sensitive))

    return radar if radar.ok else None
