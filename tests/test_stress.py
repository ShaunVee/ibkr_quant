"""Tests for the scenario stress test (analysis #6)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.analysis import stress


def _series(n=200, mu=0.0, sigma=0.01, seed=0):
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sigma, n), index=idx)


def test_market_shock_scales_by_beta_and_value():
    port = _series()
    m = stress.compute(
        port, beta=1.2, invested_value=100_000,
        market_shocks=[-0.10], rate_proxy_returns=None, rate_shocks=None,
    )
    assert m is not None
    sc = next(s for s in m.scenarios if s.kind == "market")
    assert sc.port_ret_pct == pytest.approx(-12.0)   # beta 1.2 * -10%
    assert sc.pnl == pytest.approx(-12_000.0)


def test_rate_shock_uses_regressed_proxy_beta():
    proxy = _series(sigma=0.008, seed=1)
    port = 0.5 * proxy                                # book moves half as hard as the proxy
    m = stress.compute(
        port, beta=1.0, invested_value=100_000, market_shocks=[],
        rate_proxy_returns=proxy, rate_proxy="TLT", rate_shocks=[-0.04],
    )
    assert m.rate_beta == pytest.approx(0.5, abs=1e-9)
    rate = next(s for s in m.scenarios if s.kind == "rate")
    assert rate.label == "TLT -4%"
    assert rate.port_ret_pct == pytest.approx(-2.0, abs=1e-6)   # 0.5 * -4%
    assert rate.pnl == pytest.approx(-2_000.0, abs=1e-3)


def test_historical_replay_and_cvar():
    port = _series(n=300, sigma=0.012, seed=7)
    m = stress.compute(
        port, beta=1.0, invested_value=80_000, market_shocks=[],
        rate_proxy_returns=None, rate_shocks=None, var_confidence=0.95,
    )
    # Worst single day matches the series minimum, sized in dollars.
    assert m.worst_day.ret_pct == pytest.approx(port.min() * 100)
    assert m.worst_day.pnl == pytest.approx(port.min() * 80_000)
    assert m.worst_day.day == port.idxmin().date()
    assert m.worst_week is not None
    # CVaR is minus the mean of the worst 5% of days.
    q = port.quantile(0.05)
    expected_es = port[port <= q].mean()
    assert m.cvar_pct == pytest.approx(-expected_es)
    assert m.cvar_pnl == pytest.approx(expected_es * 80_000)


def test_scenarios_without_base_value_have_no_pnl():
    port = _series(n=80)
    m = stress.compute(
        port, beta=1.1, invested_value=0.0, market_shocks=[-0.05],
        rate_proxy_returns=None, rate_shocks=None,
    )
    assert m is not None
    assert m.scenarios[0].port_ret_pct == pytest.approx(-5.5)
    assert m.scenarios[0].pnl is None


def test_returns_none_without_usable_inputs():
    empty = pd.Series(dtype="float64")
    m = stress.compute(
        empty, beta=None, invested_value=0.0, market_shocks=[-0.10],
        rate_proxy_returns=None, rate_shocks=None,
    )
    assert m is None


def test_short_history_skips_historical_but_keeps_market():
    port = _series(n=10)                              # below MIN_OBS
    m = stress.compute(
        port, beta=1.0, invested_value=50_000, market_shocks=[-0.05],
        rate_proxy_returns=None, rate_shocks=None,
    )
    assert m is not None
    assert m.scenarios and m.worst_day is None and m.cvar_pct is None
