"""History & trends — how the book has moved over the recorded window.

IBKR is stateless: it shows you today. The daily snapshots we record turn that into a
time series, and this module reads it back. Everything here is measured from your own
recorded history (never simulated), which is why it only appears once there are at least
two snapshots to compare.

Honest-return note: raw account value is contaminated by deposits/withdrawals, so this
module never labels a value change as a "return". The flow-agnostic signals — how the
risk metrics drifted and how position weights shifted — are the trustworthy part;
account value and drawdown-from-peak are shown as account-level context, flows included.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Trailing snapshots to trend over (~1 trading month). We record one per run (weekdays),
# so this is roughly the last month of runs.
DEFAULT_WINDOW = 21

# Per-snapshot metrics we trend, in display order. Only those present at both endpoints
# of the window are emitted, so a metric added to the schema later won't break old rows.
TRACKED_METRICS = ("annualized_vol", "portfolio_beta", "herfindahl", "flag_count")

# A position weight must move at least this many percentage points across the window to
# be called out — below it is noise from daily price drift.
MIN_WEIGHT_DRIFT = 0.02


@dataclass(slots=True)
class MetricDrift:
    key: str
    start: float
    end: float

    @property
    def delta(self) -> float:
        return self.end - self.start


@dataclass(slots=True)
class WeightDrift:
    symbol: str
    start_weight: float   # 0.0 if the name wasn't held at window start (a new position)
    end_weight: float     # 0.0 if the name has since been exited

    @property
    def delta(self) -> float:
        return self.end_weight - self.start_weight


@dataclass(slots=True)
class TrendModel:
    sessions: int
    first_date: str
    last_date: str
    span_days: int
    value_start: float | None = None
    value_end: float | None = None
    peak_value: float | None = None
    drawdown_from_peak: float | None = None      # <= 0 fraction, e.g. -0.06
    metric_drifts: list[MetricDrift] = field(default_factory=list)
    weight_drifts: list[WeightDrift] = field(default_factory=list)


def compute(
    history: list[dict[str, Any]],
    *,
    window: int = DEFAULT_WINDOW,
    min_weight_drift: float = MIN_WEIGHT_DRIFT,
    max_weight_drifts: int = 6,
) -> TrendModel | None:
    """Trend the recorded snapshot series. Returns None until there are >= 2 snapshots.

    `history` is Store.snapshot_history() output (ascending). We trend over the trailing
    `window` records so the read stays "recent", not since-inception.
    """
    if len(history) < 2:
        return None

    win = history[-window:] if window else history
    start, end = win[0], win[-1]

    span_days = _span_days(start["date"], end["date"])

    # Account value trajectory + drawdown from the recorded running peak (flows included).
    values = [r["net_liq"] for r in win if r.get("net_liq") is not None]
    value_start = start.get("net_liq")
    value_end = end.get("net_liq")
    peak_value = max(values) if values else None
    drawdown = None
    if peak_value and value_end is not None and peak_value > 0:
        drawdown = min(0.0, (value_end - peak_value) / peak_value)

    metric_drifts: list[MetricDrift] = []
    for key in TRACKED_METRICS:
        s = start.get("metrics", {}).get(key)
        e = end.get("metrics", {}).get(key)
        if s is not None and e is not None:
            metric_drifts.append(MetricDrift(key=key, start=float(s), end=float(e)))

    metric_drifts = [m for m in metric_drifts if _metric_moved(m)]

    weight_drifts = _weight_drifts(
        start.get("weights", {}), end.get("weights", {}), min_weight_drift, max_weight_drifts
    )

    return TrendModel(
        sessions=len(win),
        first_date=start["date"],
        last_date=end["date"],
        span_days=span_days,
        value_start=value_start,
        value_end=value_end,
        peak_value=peak_value,
        drawdown_from_peak=drawdown,
        metric_drifts=metric_drifts,
        weight_drifts=weight_drifts,
    )


def _metric_moved(m: MetricDrift) -> bool:
    """Drop dead-flat metrics so the section shows movement, not a list of no-ops."""
    if m.key == "flag_count":
        return m.start != m.end
    scale = max(abs(m.start), abs(m.end), 1e-9)
    return abs(m.delta) / scale >= 0.02  # >= 2% relative move


def _weight_drifts(
    start_w: dict[str, float],
    end_w: dict[str, float],
    min_drift: float,
    limit: int,
) -> list[WeightDrift]:
    drifts = [
        WeightDrift(symbol=sym, start_weight=start_w.get(sym, 0.0), end_weight=end_w.get(sym, 0.0))
        for sym in set(start_w) | set(end_w)
    ]
    drifts = [d for d in drifts if abs(d.delta) >= min_drift]
    drifts.sort(key=lambda d: -abs(d.delta))
    return drifts[:limit]


def _span_days(first: str, last: str) -> int:
    try:
        return (date.fromisoformat(last) - date.fromisoformat(first)).days
    except (ValueError, TypeError):
        return 0
