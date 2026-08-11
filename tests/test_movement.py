"""Tests for the move-contextualization analysis (analysis/movement.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantbot.analysis import movement


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"close": closes}, index=idx)


def test_no_data_returns_empty_context():
    ctx = movement.compute({}, {})
    assert ctx.port_ret_pct is None
    assert ctx.top_contributors == []


def test_portfolio_return_and_contributions():
    # AAPL +10% today (100 -> 110), MSFT flat. 60/40 book -> +6% portfolio move.
    frames = {"AAPL": _frame([100, 100, 110]), "MSFT": _frame([50, 50, 50])}
    weights = {"AAPL": 0.6, "MSFT": 0.4}
    ctx = movement.compute(frames, weights)

    assert ctx.port_ret_pct is not None
    assert round(ctx.port_ret_pct, 4) == 6.0
    # AAPL contributes 0.6 * 10% = 6.0pp; it is the top driver.
    top = ctx.top_contributors[0]
    assert top.symbol == "AAPL"
    assert round(top.contribution_pp, 4) == 6.0


def test_prior_weights_take_precedence():
    frames = {"AAPL": _frame([100, 100, 110]), "MSFT": _frame([50, 50, 50])}
    # Today's book is all MSFT, but yesterday we held all AAPL — the move we lived
    # through is AAPL's +10%.
    ctx = movement.compute(
        frames, {"MSFT": 1.0}, prior_weights={"AAPL": 1.0}
    )
    assert round(ctx.port_ret_pct, 4) == 10.0


def test_unusual_flag_and_zscore():
    # A calm series of tiny moves, then a large final jump -> high z-score, unusual.
    rng = np.random.default_rng(0)
    calm = list(100 * (1 + pd.Series(rng.normal(0, 0.002, 200))).cumprod())
    calm.append(calm[-1] * 1.08)  # +8% shock on the last day
    frames = {"AAPL": _frame(calm)}
    ctx = movement.compute(frames, {"AAPL": 1.0})

    assert ctx.port_z is not None and abs(ctx.port_z) >= movement.UNUSUAL_Z
    assert ctx.unusual is True
    assert ctx.abnormal_names and ctx.abnormal_names[0].symbol == "AAPL"


def test_normal_move_not_flagged():
    # Alternating +/-1% history (sigma ~ 1%), then a modest +0.5% final day -> z ~ 0.5.
    closes = [100.0]
    for r in [0.01, -0.01] * 100:
        closes.append(closes[-1] * (1 + r))
    closes.append(closes[-1] * 1.005)
    frames = {"AAPL": _frame(closes)}
    ctx = movement.compute(frames, {"AAPL": 1.0})
    assert ctx.port_z is not None and abs(ctx.port_z) < movement.UNUSUAL_Z
    assert ctx.unusual is False
