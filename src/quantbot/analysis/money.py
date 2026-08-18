"""Your money, in plain English (analysis #8) — dollars first, no finance vocabulary.

Every other module answers a quant question (beta, vol, factor share). This one answers
the three questions a normal owner actually asks:

  1. "How much money did my holdings make or lose?" — over today / a month / three months,
     in currency, plus the single best and worst day.
  2. "Am I beating just buying the index?" — the same money laid against what SPY (or the
     configured benchmark) would have done: ahead or behind, in dollars.
  3. "How far underwater am I, and how far back to even?" — the drawdown asymmetry, spelled
     out (a 20% fall needs a 25% gain to recover).

Plus a one-line "what am I actually betting on?" read.

Nothing new is computed from market data here — it re-expresses figures the benchmark,
trends and diversification modules already produced, times the invested value, as money.
The percentages are the current book held constant over history (the same synthetic series
the benchmark uses), so the dollar figures are "what today's holdings would have done",
not a realized track record — labelled as such in the brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from quantbot.analysis.benchmark import BenchmarkModel
from quantbot.analysis.diversification import DiversificationModel
from quantbot.analysis.trends import TrendModel
from quantbot.models import Holding

# Window code -> plain label. Order is display order.
_WINDOW_LABELS = [("1d", "Today"), ("1m", "Past month"), ("3m", "Past 3 months")]

# Only look for the best/worst single day inside the recent window (~3 trading months),
# so it stays relatable rather than surfacing something from a year ago.
_RECENT_DAYS = 63


@dataclass(slots=True)
class MoneyWindow:
    label: str
    pct: float                    # book return over the window, percent
    pnl: float                    # ≈ invested_value * pct/100, in currency
    bench_label: str | None = None
    bench_pct: float | None = None
    bench_pnl: float | None = None


@dataclass(slots=True)
class DayPnL:
    ret_pct: float
    pnl: float
    day: date | None = None


@dataclass(slots=True)
class HoldingPnL:
    """Where you stand on one position, since you bought it."""

    symbol: str
    pnl: float                    # unrealized, in currency
    pct: float | None = None      # pnl / cost basis, percent


@dataclass(slots=True)
class Recovery:
    """Loss-recovery asymmetry: down `drawdown_pct`, needs `gain_needed_pct` to get even."""

    drawdown_pct: float           # positive magnitude, e.g. 11.0
    gain_needed_pct: float        # e.g. 12.4
    peak_value: float | None = None


@dataclass(slots=True)
class MoneyModel:
    currency: str
    invested_value: float
    windows: list[MoneyWindow] = field(default_factory=list)
    best_day: DayPnL | None = None
    worst_day: DayPnL | None = None
    bench_symbol: str | None = None
    vs_index_pnl: float | None = None      # your P/L minus the index's, over `vs_index_label`
    vs_index_label: str | None = None
    recovery: Recovery | None = None
    betting_on: str | None = None          # plain-English "what you're really long"
    winners: list[HoldingPnL] = field(default_factory=list)   # green, biggest gain first
    losers: list[HoldingPnL] = field(default_factory=list)    # underwater, biggest loss first
    total_unrealized: float | None = None


def _extract_day(idx_value: object) -> date | None:
    """Best-effort date from a return-series index entry (DatetimeIndex or plain)."""
    if isinstance(idx_value, date):
        return idx_value
    to_date = getattr(idx_value, "date", None)
    if callable(to_date):
        try:
            return to_date()
        except (TypeError, ValueError):
            return None
    return None


def _best_worst(port_returns: pd.Series | None, invested_value: float) -> tuple[DayPnL | None, DayPnL | None]:
    if port_returns is None or port_returns.empty:
        return None, None
    recent = port_returns.tail(_RECENT_DAYS).dropna()
    if recent.empty:
        return None, None
    hi_pos, lo_pos = recent.idxmax(), recent.idxmin()
    best = DayPnL(
        ret_pct=float(recent.loc[hi_pos]) * 100.0,
        pnl=invested_value * float(recent.loc[hi_pos]),
        day=_extract_day(hi_pos),
    )
    worst = DayPnL(
        ret_pct=float(recent.loc[lo_pos]) * 100.0,
        pnl=invested_value * float(recent.loc[lo_pos]),
        day=_extract_day(lo_pos),
    )
    return best, worst


def _holdings_pnl(holdings: list[Holding] | None) -> tuple[list[HoldingPnL], list[HoldingPnL], float | None]:
    """Split positions into winners and losers by unrealized P/L (the broker-app view)."""
    if not holdings:
        return [], [], None
    rows: list[HoldingPnL] = []
    total = 0.0
    seen = False
    for h in holdings:
        if h.asset_class == "CASH":
            continue
        pnl = h.unrealized_pnl
        if pnl is None:
            continue
        seen = True
        total += pnl
        cost = h.cost_basis
        pct = (pnl / cost * 100.0) if cost else None
        rows.append(HoldingPnL(symbol=h.symbol, pnl=pnl, pct=pct))
    winners = sorted((r for r in rows if r.pnl > 0), key=lambda r: -r.pnl)
    losers = sorted((r for r in rows if r.pnl < 0), key=lambda r: r.pnl)
    return winners, losers, (total if seen else None)


def _betting_on(diversification: DiversificationModel | None) -> str | None:
    """One plain sentence: how many real bets the book is, and if one theme dominates."""
    d = diversification
    if d is None or d.effective_bets is None or not d.coverage:
        return None
    eff = round(d.effective_bets)
    # "9 names, but really about 2 bets" only lands when they actually differ.
    if eff >= d.coverage:
        base = f"Your {d.coverage} holdings act like {d.coverage} fairly independent bets"
    else:
        base = f"Your {d.coverage} holdings really act like about {eff} bet" + ("s" if eff != 1 else "")
    if d.top_factor_share is not None and d.top_factor_share >= 0.5:
        base += f" — one theme drives {d.top_factor_share * 100:.0f}% of the day-to-day swings"
    return base + "."


def compute(
    *,
    invested_value: float,
    currency: str,
    benchmark: BenchmarkModel | None = None,
    trends: TrendModel | None = None,
    port_returns: pd.Series | None = None,
    diversification: DiversificationModel | None = None,
    holdings: list[Holding] | None = None,
) -> MoneyModel | None:
    """Re-express the already-computed analytics as plain-English money. Returns None only
    when there is nothing at all to say (no windows, no history, no drawdown)."""
    model = MoneyModel(currency=currency, invested_value=invested_value)

    # 1. Money made/lost per window, with the index counterfactual alongside.
    if benchmark is not None and benchmark.windows:
        model.bench_symbol = benchmark.symbol
        by_code = {w.label: w for w in benchmark.windows}
        for code, label in _WINDOW_LABELS:
            w = by_code.get(code)
            if w is None:
                continue
            model.windows.append(
                MoneyWindow(
                    label=label,
                    pct=w.port_pct,
                    pnl=invested_value * w.port_pct / 100.0,
                    bench_label=benchmark.symbol,
                    bench_pct=w.bench_pct,
                    bench_pnl=invested_value * w.bench_pct / 100.0,
                )
            )
        # Counterfactual on the longest window we have: are you ahead of the lazy option?
        if model.windows:
            longest = model.windows[-1]
            if longest.bench_pnl is not None:
                model.vs_index_pnl = longest.pnl - longest.bench_pnl
                model.vs_index_label = longest.label

    # Best & worst single day (recent), in money.
    model.best_day, model.worst_day = _best_worst(port_returns, invested_value)

    # 3. How far underwater, and the gain needed to climb back — the loss asymmetry.
    if trends is not None and trends.drawdown_from_peak is not None:
        d = abs(trends.drawdown_from_peak)
        if d >= 0.005 and d < 1.0:
            model.recovery = Recovery(
                drawdown_pct=d * 100.0,
                gain_needed_pct=(d / (1.0 - d)) * 100.0,
                peak_value=trends.peak_value,
            )

    # Per-holding standing: winners vs. underwater names, since you bought them.
    model.winners, model.losers, model.total_unrealized = _holdings_pnl(holdings)

    # A one-line "what am I actually betting on?".
    model.betting_on = _betting_on(diversification)

    has_content = bool(
        model.windows or model.best_day or model.recovery or model.betting_on
        or model.winners or model.losers
    )
    return model if has_content else None
