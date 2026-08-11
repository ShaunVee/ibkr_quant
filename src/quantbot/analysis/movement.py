"""Today's move, contextualized — the "is this even notable?" analysis.

A raw daily P/L number is a mirror: IBKR already shows it. The value here is turning
it into a decision — *ignore it* vs. *go look* — by z-scoring the move against the
book's own trailing volatility, and attributing it to the names that actually drove it.

Everything is computed from the price cache already in hand; no new data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quantbot.analysis.risk import portfolio_return_series
from quantbot.analysis.technical import daily_returns

# |z| at or above this is "unusual" — worth a line in the brief.
UNUSUAL_Z = 2.0


@dataclass(slots=True)
class PositionMove:
    symbol: str
    ret_pct: float                 # close-to-close return, percent
    contribution_pp: float         # weight x return, in portfolio percentage points
    z: float | None = None         # move vs the name's own trailing daily vol


@dataclass(slots=True)
class MoveContext:
    port_ret_pct: float | None = None      # today's book return, percent
    port_z: float | None = None            # vs trailing portfolio-return distribution
    sigma_pct: float | None = None         # trailing daily sigma of the book, percent
    unusual: bool = False                  # |port_z| >= UNUSUAL_Z
    top_contributors: list[PositionMove] = field(default_factory=list)
    abnormal_names: list[PositionMove] = field(default_factory=list)


def _last_return(close: pd.Series) -> float | None:
    """Most recent close-to-close return as a fraction, or None if too short."""
    close = close.dropna()
    if len(close) < 2 or close.iloc[-2] == 0:
        return None
    return float(close.iloc[-1] / close.iloc[-2] - 1.0)


def compute(
    price_frames: dict[str, pd.DataFrame],
    weights: dict[str, float],
    *,
    prior_weights: dict[str, float] | None = None,
    top_n: int = 4,
) -> MoveContext:
    """Contextualize today's portfolio move.

    weights is the current book; prior_weights (yesterday's snapshot) is preferred when
    available, so the move is measured on the book you actually *held* into today.
    """
    book = prior_weights or weights
    ctx = MoveContext()
    if not book:
        return ctx

    moves: list[PositionMove] = []
    port_ret = 0.0
    covered = 0.0
    for symbol, w in book.items():
        df = price_frames.get(symbol)
        if df is None or df.empty or "close" not in df.columns:
            continue
        close = df["close"]
        r = _last_return(close)
        if r is None:
            continue
        # Per-name z: today's move against the name's own trailing daily vol.
        rets = daily_returns(close)
        sigma_i = float(rets.std(ddof=1)) if len(rets) >= 2 else 0.0
        z_i = r / sigma_i if sigma_i > 0 else None
        moves.append(
            PositionMove(
                symbol=symbol,
                ret_pct=r * 100.0,
                contribution_pp=w * r * 100.0,
                z=z_i,
            )
        )
        port_ret += w * r
        covered += w

    if not moves:
        return ctx

    ctx.port_ret_pct = port_ret * 100.0

    # Baseline distribution: the book's own trailing daily returns (current weights held
    # constant — the same synthetic series the risk module uses). Its std is our sigma.
    series = portfolio_return_series(weights, price_frames)
    if len(series) >= 2:
        sigma = float(series.std(ddof=1))
        mu = float(series.mean())
        if sigma > 0:
            ctx.sigma_pct = sigma * 100.0
            ctx.port_z = (port_ret - mu) / sigma
            ctx.unusual = abs(ctx.port_z) >= UNUSUAL_Z

    ctx.top_contributors = sorted(
        moves, key=lambda m: -abs(m.contribution_pp)
    )[:top_n]
    ctx.abnormal_names = sorted(
        (m for m in moves if m.z is not None and abs(m.z) >= UNUSUAL_Z),
        key=lambda m: -abs(m.z),
    )
    return ctx
