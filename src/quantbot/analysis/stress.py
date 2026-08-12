"""Scenario stress test — "what does that risk number actually cost me?" (analysis #6).

The risk section reports beta, vol and VaR — abstractions. None of them answer the
question you actually ask in a selloff: how many dollars is this. This translates the
book's own risk into concrete P/L under named shocks, all off data already in hand:

- market shocks: beta-scaled moves (e.g. SPY -5% / -10%) -> portfolio % and $ impact
- a rate shock: the book's regressed beta to the rate proxy applied to a proxy move
- historical replay: the worst actual 1-day and 5-day move this book would have taken
  over the price window, in % and $
- CVaR (expected shortfall): the average loss across the worst `1 - confidence` of days
  — the tail beyond VaR, i.e. "when it's a bad day, how bad on average"

Everything is deterministic and buy/sell-neutral: it sizes the downside, it does not
tell you to trade. Degrades to None when there isn't enough history or a base value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

MIN_OBS = 20  # matches the historical-VaR floor in risk.py


@dataclass(slots=True)
class Scenario:
    label: str            # "Market -10%", "TLT -5%"
    kind: str             # "market" | "rate"
    shock_pct: float      # the input shock as a fraction (e.g. -0.10)
    port_ret_pct: float   # resulting portfolio return, percent (e.g. -11.5)
    pnl: float | None     # dollar impact on the invested value (None without a base)


@dataclass(slots=True)
class HistoricalStress:
    label: str            # "Worst 1-day", "Worst 5-day"
    ret_pct: float        # percent, negative
    pnl: float | None
    day: date | None      # end date of the worst window, when known


@dataclass(slots=True)
class StressModel:
    invested_value: float
    scenarios: list[Scenario] = field(default_factory=list)
    worst_day: HistoricalStress | None = None
    worst_week: HistoricalStress | None = None
    cvar_pct: float | None = None       # positive fraction magnitude, e.g. 0.029
    cvar_pnl: float | None = None       # dollar impact (negative-direction loss)
    beta: float | None = None
    rate_proxy: str | None = None
    rate_beta: float | None = None      # regressed portfolio beta to the rate proxy

    @property
    def ok(self) -> bool:
        return bool(self.scenarios or self.worst_day or self.cvar_pct is not None)


def _rate_beta(port_returns: pd.Series, proxy_returns: pd.Series) -> float | None:
    """Regress the book's daily returns on the rate proxy's -> one portfolio rate beta."""
    aligned = pd.DataFrame({"p": port_returns, "r": proxy_returns}).dropna()
    if len(aligned) < MIN_OBS:
        return None
    p = aligned["p"].to_numpy()
    r = aligned["r"].to_numpy()
    var_r = float(np.var(r, ddof=1))
    if var_r <= 0:
        return None
    return float(np.cov(p, r, ddof=1)[0, 1] / var_r)


def _worst_window(returns: pd.Series, window: int) -> tuple[float, date | None] | None:
    """Worst cumulative return over any rolling `window` of days, with its end date."""
    if len(returns) < window:
        return None
    roll = (1.0 + returns).rolling(window).apply(np.prod, raw=True) - 1.0
    roll = roll.dropna()
    if roll.empty:
        return None
    idx = roll.idxmin()
    day = idx.date() if hasattr(idx, "date") else None
    return float(roll.min()), day


def compute(
    port_returns: pd.Series,
    *,
    beta: float | None,
    invested_value: float,
    market_shocks: list[float],
    rate_proxy_returns: pd.Series | None = None,
    rate_proxy: str | None = None,
    rate_shocks: list[float] | None = None,
    var_confidence: float = 0.95,
) -> StressModel | None:
    """Size the book's downside under named shocks and its own worst history."""
    model = StressModel(invested_value=invested_value, beta=beta)
    base = invested_value if invested_value and invested_value > 0 else None

    # --- market shocks, scaled through the book's beta ---
    if beta is not None:
        for s in market_shocks or []:
            port_ret = beta * s
            model.scenarios.append(
                Scenario(
                    label=f"Market {s * 100:+.0f}%",
                    kind="market",
                    shock_pct=s,
                    port_ret_pct=port_ret * 100.0,
                    pnl=port_ret * base if base is not None else None,
                )
            )

    # --- rate shock, scaled through the regressed beta to the rate proxy ---
    have_proxy = rate_proxy_returns is not None and not rate_proxy_returns.empty
    have_port = port_returns is not None and not port_returns.empty
    if have_proxy and have_port and rate_shocks:
        rb = _rate_beta(port_returns, rate_proxy_returns)
        if rb is not None:
            model.rate_beta = rb
            model.rate_proxy = rate_proxy
            for s in rate_shocks:
                port_ret = rb * s
                model.scenarios.append(
                    Scenario(
                        label=f"{rate_proxy or 'Rate proxy'} {s * 100:+.0f}%",
                        kind="rate",
                        shock_pct=s,
                        port_ret_pct=port_ret * 100.0,
                        pnl=port_ret * base if base is not None else None,
                    )
                )

    # --- historical replay + CVaR, off the book's own return window ---
    if have_port and len(port_returns) >= MIN_OBS:
        wd = _worst_window(port_returns, 1)
        if wd is not None:
            val, day = wd
            model.worst_day = HistoricalStress(
                "Worst 1-day", val * 100.0, val * base if base is not None else None, day
            )
        ww = _worst_window(port_returns, 5)
        if ww is not None:
            val, day = ww
            model.worst_week = HistoricalStress(
                "Worst 5-day", val * 100.0, val * base if base is not None else None, day
            )
        quantile = float(port_returns.quantile(1.0 - var_confidence))
        tail = port_returns[port_returns <= quantile]
        if not tail.empty:
            es = float(tail.mean())         # negative
            model.cvar_pct = -es
            model.cvar_pnl = es * base if base is not None else None

    return model if model.ok else None
