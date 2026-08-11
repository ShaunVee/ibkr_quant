"""Deterministic risk-metric tests on hand-built portfolios and price series."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.analysis import risk
from quantbot.models import AccountSummary, Fundamentals, Holding, Portfolio


def _portfolio() -> Portfolio:
    holdings = [
        Holding(symbol="AAPL", quantity=100, market_value=60000, asset_class="STK"),
        Holding(symbol="MSFT", quantity=50, market_value=30000, asset_class="STK"),
        Holding(symbol="XOM", quantity=100, market_value=10000, asset_class="STK"),
    ]
    return Portfolio(
        account=AccountSummary(account_id="U1", base_currency="USD"),
        holdings=holdings,
    )


def test_position_weights_sum_to_one():
    p = _portfolio()
    w = risk.position_weights(p)
    assert w["AAPL"] == pytest.approx(0.60)
    assert w["MSFT"] == pytest.approx(0.30)
    assert w["XOM"] == pytest.approx(0.10)
    assert sum(w.values()) == pytest.approx(1.0)


def test_herfindahl_and_effective_positions():
    w = {"AAPL": 0.6, "MSFT": 0.3, "XOM": 0.1}
    h = risk.herfindahl(w)
    assert h == pytest.approx(0.36 + 0.09 + 0.01)  # 0.46
    assert 1.0 / h == pytest.approx(1.0 / 0.46)


def test_sector_weights_group_by_sector():
    p = _portfolio()
    funds = {
        "AAPL": Fundamentals(symbol="AAPL", sector="Technology"),
        "MSFT": Fundamentals(symbol="MSFT", sector="Technology"),
        "XOM": Fundamentals(symbol="XOM", sector="Energy"),
    }
    sw = risk.sector_weights(p, funds)
    assert sw["Technology"] == pytest.approx(0.90)
    assert sw["Energy"] == pytest.approx(0.10)


def test_weighted_beta_normalizes_partial_coverage():
    w = {"AAPL": 0.6, "MSFT": 0.4}
    funds = {
        "AAPL": Fundamentals(symbol="AAPL", beta=1.2),
        "MSFT": Fundamentals(symbol="MSFT", beta=None),  # uncovered
    }
    # Only AAPL covered -> weighted beta should equal AAPL's beta.
    assert risk.weighted_beta(w, funds) == pytest.approx(1.2)


def test_max_drawdown_on_known_series():
    # Up 10%, then down 20% -> drawdown from peak = -20%.
    returns = pd.Series([0.10, -0.20])
    dd = risk.max_drawdown(returns)
    assert dd == pytest.approx(-0.20)


def test_historical_var_positive_fraction():
    rng = np.random.default_rng(42)
    returns = pd.Series(rng.normal(0, 0.01, 500))
    var = risk.historical_var(returns, confidence=0.95)
    assert var is not None and var > 0


def test_compute_end_to_end_with_synthetic_prices():
    p = _portfolio()
    funds = {
        "AAPL": Fundamentals(symbol="AAPL", sector="Technology", beta=1.2),
        "MSFT": Fundamentals(symbol="MSFT", sector="Technology", beta=1.1),
        "XOM": Fundamentals(symbol="XOM", sector="Energy", beta=0.8),
    }
    idx = pd.date_range("2025-01-01", periods=260, freq="B")
    rng = np.random.default_rng(1)
    frames = {}
    for sym in ("AAPL", "MSFT", "XOM"):
        close = 100 * (1 + pd.Series(rng.normal(0.0005, 0.015, len(idx)))).cumprod()
        frames[sym] = pd.DataFrame({"close": close.values}, index=idx)

    m = risk.compute(p, funds, frames, risk_free_rate=0.04, var_confidence=0.95)
    assert m.top_position[0] == "AAPL"
    assert m.portfolio_beta == pytest.approx(0.6 * 1.2 + 0.3 * 1.1 + 0.1 * 0.8, abs=1e-9)
    assert m.annualized_vol is not None and m.annualized_vol > 0
    assert m.sharpe is not None
    assert m.max_drawdown is not None and m.max_drawdown <= 0
    assert m.effective_positions is not None
