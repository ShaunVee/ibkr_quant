"""Benchmark-relative performance — "am I adding value, or just SPY?" (analysis #5).

IBKR shows your P/L; it does not tell you whether that P/L is skill or just market
exposure you could have bought in one ticker. This regresses the book against a
benchmark to separate the two:

- excess return over trailing windows (are you ahead of the index?)
- regressed beta + annualized alpha + R² (how much of your return is just market?)
- tracking error and up/down capture (how differently do you ride it?)
- drift from your stated target weights (a rebalance trigger)

The portfolio return series is the current book held constant over history — so beta,
alpha and capture characterize *today's composition* against the benchmark over the
window, not a realized since-inception track record. That distinction is labelled in
the brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantbot.analysis.technical import TRADING_DAYS_YEAR

MIN_OBS = 60


@dataclass(slots=True)
class WindowReturn:
    label: str            # "1d" | "1m" | "3m"
    port_pct: float
    bench_pct: float

    @property
    def excess_pct(self) -> float:
        return self.port_pct - self.bench_pct


@dataclass(slots=True)
class TargetDrift:
    symbol: str
    weight: float
    target: float

    @property
    def drift(self) -> float:
        return self.weight - self.target


@dataclass(slots=True)
class BenchmarkModel:
    symbol: str
    beta: float | None = None
    alpha_annual_pct: float | None = None
    r_squared: float | None = None
    tracking_error_pct: float | None = None    # annualized
    up_capture: float | None = None
    down_capture: float | None = None
    windows: list[WindowReturn] = field(default_factory=list)
    drifts: list[TargetDrift] = field(default_factory=list)


_WINDOWS = [("1d", 1), ("1m", 21), ("3m", 63)]


def _cum_return(returns: pd.Series, n: int) -> float:
    tail = returns.tail(n)
    if tail.empty:
        return 0.0
    return float((1.0 + tail).prod() - 1.0)


def _drifts(
    weights: dict[str, float], targets: dict[str, float], tolerance: float
) -> list[TargetDrift]:
    out: list[TargetDrift] = []
    for sym, target in targets.items():
        w = weights.get(sym, 0.0)
        if abs(w - target) >= tolerance:
            out.append(TargetDrift(symbol=sym, weight=w, target=target))
    out.sort(key=lambda d: -abs(d.drift))
    return out


def compute(
    port_returns: pd.Series,
    bench_close: pd.Series,
    *,
    symbol: str = "SPY",
    weights: dict[str, float] | None = None,
    targets: dict[str, float] | None = None,
    drift_tolerance: float = 0.05,
) -> BenchmarkModel | None:
    """Regress the book's daily returns against the benchmark's."""
    model = BenchmarkModel(symbol=symbol)

    # Target drift needs no price history — compute it even if the regression can't run.
    if weights and targets:
        model.drifts = _drifts(weights, targets, drift_tolerance)

    if port_returns is None or port_returns.empty or bench_close is None or bench_close.empty:
        return model if model.drifts else None

    bench_returns = bench_close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    aligned = pd.DataFrame({"p": port_returns, "b": bench_returns}).dropna()
    if len(aligned) < MIN_OBS:
        return model if model.drifts else None

    p = aligned["p"].to_numpy()
    b = aligned["b"].to_numpy()

    var_b = float(np.var(b, ddof=1))
    if var_b > 0:
        beta = float(np.cov(p, b, ddof=1)[0, 1] / var_b)
        model.beta = beta
        model.alpha_annual_pct = float((p.mean() - beta * b.mean()) * TRADING_DAYS_YEAR * 100.0)
        r = float(np.corrcoef(p, b)[0, 1])
        model.r_squared = r * r

    model.tracking_error_pct = float(
        (p - b).std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR) * 100.0
    )

    up, down = b > 0, b < 0
    if up.any() and b[up].sum() != 0:
        model.up_capture = float(p[up].sum() / b[up].sum())
    if down.any() and b[down].sum() != 0:
        model.down_capture = float(p[down].sum() / b[down].sum())

    model.windows = [
        WindowReturn(
            label=label,
            port_pct=_cum_return(aligned["p"], n) * 100.0,
            bench_pct=_cum_return(aligned["b"], n) * 100.0,
        )
        for label, n in _WINDOWS
        if len(aligned) >= n
    ]
    return model
