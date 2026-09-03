"""Portfolio-level risk metrics — computed from holdings + price history.

Everything here is deterministic math over your own book, which is why it's the most
trustworthy part of the report. Metrics: weights & concentration (Herfindahl), sector
concentration, weighted beta, annualized vol, Sharpe, max drawdown, historical VaR.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantbot.analysis.technical import TRADING_DAYS_YEAR, annualized_vol, daily_returns
from quantbot.models import Fundamentals, Portfolio


@dataclass(slots=True)
class RiskMetrics:
    invested_value: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)          # symbol -> fraction
    sector_weights: dict[str, float] = field(default_factory=dict)
    herfindahl: float | None = None            # 0..1 concentration index
    effective_positions: float | None = None   # 1 / herfindahl
    portfolio_beta: float | None = None
    annualized_vol: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None           # SIMULATED: current weights held over price history
    realized_drawdown: float | None = None      # worst peak-to-trough of recorded account equity
    var_pct: float | None = None                # historical VaR as positive fraction
    top_position: tuple[str, float] | None = None


def position_weights(portfolio: Portfolio) -> dict[str, float]:
    invested = portfolio.invested_value or 0.0
    if invested <= 0:
        return {}
    return {
        h.symbol: (h.market_value or 0.0) / invested
        for h in portfolio.holdings
        if h.asset_class != "CASH"
    }


def sector_weights(
    portfolio: Portfolio, fundamentals: dict[str, Fundamentals]
) -> dict[str, float]:
    invested = portfolio.invested_value or 0.0
    if invested <= 0:
        return {}
    out: dict[str, float] = {}
    for h in portfolio.holdings:
        if h.asset_class == "CASH":
            continue
        sector = (fundamentals.get(h.symbol) or Fundamentals(symbol=h.symbol)).sector
        sector = sector or "Unknown"
        out[sector] = out.get(sector, 0.0) + (h.market_value or 0.0) / invested
    return out


def herfindahl(weights: dict[str, float]) -> float | None:
    if not weights:
        return None
    return float(sum(w * w for w in weights.values()))


def weighted_beta(
    weights: dict[str, float], fundamentals: dict[str, Fundamentals]
) -> float | None:
    contributions = []
    covered = 0.0
    for symbol, w in weights.items():
        beta = (fundamentals.get(symbol) or Fundamentals(symbol=symbol)).beta
        if beta is not None:
            contributions.append(w * beta)
            covered += w
    if covered == 0:
        return None
    # Normalize by covered weight so partial coverage still gives a sensible beta.
    return float(sum(contributions) / covered)


def portfolio_return_series(
    weights: dict[str, float], price_frames: dict[str, pd.DataFrame]
) -> pd.Series:
    """Weighted daily returns of the current book using each symbol's price history.

    Uses *current* weights held constant (a standard approximation when a true daily
    snapshot history isn't yet available) — this is a hypothetical backtest of today's
    book, not a realized track record. On days where only some symbols have price history
    (uneven listing dates), we renormalize by the weight actually *covered* that day, so
    the series stays a true weighted average instead of an under-summed partial that
    silently under-weights the whole book toward whichever names have the longest history.
    """
    return_cols = {}
    for symbol, _w in weights.items():
        df = price_frames.get(symbol)
        if df is None or df.empty or "close" not in df.columns:
            continue
        r = daily_returns(df["close"].dropna())
        if not r.empty:
            return_cols[symbol] = r
    if not return_cols:
        return pd.Series(dtype="float64")
    rets = pd.DataFrame(return_cols).dropna(how="all")
    w = pd.Series({sym: weights[sym] for sym in rets.columns})
    covered = rets.notna().mul(w, axis=1).sum(axis=1)          # weight present each day
    weighted = rets.mul(w, axis=1).sum(axis=1, min_count=1)
    series = (weighted / covered).replace([np.inf, -np.inf], np.nan).dropna()
    return series


def max_drawdown(return_series: pd.Series) -> float | None:
    """SIMULATED worst peak-to-trough of the current book's hypothetical equity curve.

    Built from `portfolio_return_series` (today's weights held constant), so it answers
    "how bad *could* today's book have drawn down" — not a loss actually taken. For the
    realized figure use `realized_drawdown` over the recorded account equity.
    """
    if return_series.empty:
        return None
    equity = (1.0 + return_series).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def realized_drawdown(equity_values: Sequence[float | None]) -> float | None:
    """Worst peak-to-trough of a *recorded* account-equity curve (ascending by date).

    `equity_values` is the net-liquidation series from recorded snapshots — the account's
    actual value over time. Returns a negative fraction (e.g. -0.08), or None until there
    are at least two usable points. Caveat: net_liq includes external flows (deposits /
    withdrawals), so a large withdrawal can register as drawdown; this is the same
    flows-included basis the trends layer uses.
    """
    clean = [float(v) for v in equity_values if v is not None]
    if len(clean) < 2:
        return None
    equity = pd.Series(clean)
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def historical_var(return_series: pd.Series, confidence: float = 0.95) -> float | None:
    """One-day historical VaR as a positive fraction (e.g. 0.021 = 2.1%)."""
    if len(return_series) < 20:
        return None
    quantile = return_series.quantile(1.0 - confidence)
    return float(-quantile)


def compute(
    portfolio: Portfolio,
    fundamentals: dict[str, Fundamentals],
    price_frames: dict[str, pd.DataFrame],
    *,
    risk_free_rate: float = 0.04,
    var_confidence: float = 0.95,
) -> RiskMetrics:
    m = RiskMetrics(invested_value=portfolio.invested_value)
    m.weights = position_weights(portfolio)
    m.sector_weights = sector_weights(portfolio, fundamentals)
    m.herfindahl = herfindahl(m.weights)
    if m.herfindahl:
        m.effective_positions = 1.0 / m.herfindahl
    m.portfolio_beta = weighted_beta(m.weights, fundamentals)

    if m.weights:
        top_symbol = max(m.weights, key=m.weights.get)
        m.top_position = (top_symbol, m.weights[top_symbol])

    port_returns = portfolio_return_series(m.weights, price_frames)
    m.annualized_vol = annualized_vol(port_returns)
    m.max_drawdown = max_drawdown(port_returns)
    m.var_pct = historical_var(port_returns, var_confidence)

    if not port_returns.empty and m.annualized_vol:
        ann_return = float(port_returns.mean() * TRADING_DAYS_YEAR)
        if m.annualized_vol > 0:
            m.sharpe = (ann_return - risk_free_rate) / m.annualized_vol
    return m
