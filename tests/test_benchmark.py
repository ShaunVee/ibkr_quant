"""Tests for benchmark-relative performance (analysis #5)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.analysis import benchmark


def _bench_series(n=200, seed=0):
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    rets = pd.Series(rng.normal(0.0003, 0.01, n), index=idx)
    close = 100 * (1 + rets).cumprod()
    return rets, close


def test_beta_and_r2_on_leveraged_book():
    bench_rets, bench_close = _bench_series()
    # Portfolio is exactly 1.5x the benchmark each day -> beta 1.5, alpha 0, R^2 1.
    port = 1.5 * bench_rets
    m = benchmark.compute(port, bench_close, symbol="SPY")

    assert m is not None
    assert m.beta == pytest.approx(1.5, abs=1e-6)
    assert m.r_squared == pytest.approx(1.0, abs=1e-6)
    assert m.alpha_annual_pct == pytest.approx(0.0, abs=1e-6)
    # A 1.5x book captures 150% of up and down moves.
    assert m.up_capture == pytest.approx(1.5, abs=1e-6)
    assert m.down_capture == pytest.approx(1.5, abs=1e-6)


def test_positive_alpha_detected():
    bench_rets, bench_close = _bench_series()
    # Track the benchmark exactly but add a steady daily edge -> positive alpha.
    port = bench_rets + 0.0004
    m = benchmark.compute(port, bench_close, symbol="SPY")
    assert m.beta == pytest.approx(1.0, abs=1e-6)
    assert m.alpha_annual_pct > 0


def test_insufficient_history_returns_none():
    bench_rets, bench_close = _bench_series(n=30)
    m = benchmark.compute(bench_rets, bench_close, symbol="SPY")
    assert m is None


def test_target_drift_flagged_without_prices():
    # No usable price history, but drift is still computed from weights vs targets.
    empty = pd.Series(dtype="float64")
    m = benchmark.compute(
        empty, empty, symbol="SPY",
        weights={"SLV": 0.42, "IBIT": 0.19},
        targets={"SLV": 0.30, "IBIT": 0.20},
        drift_tolerance=0.05,
    )
    assert m is not None
    # SLV drifted +12pp (>= tolerance); IBIT only -1pp (below tolerance, ignored).
    assert [d.symbol for d in m.drifts] == ["SLV"]
    assert m.drifts[0].drift == pytest.approx(0.12)


def test_excess_windows():
    bench_rets, bench_close = _bench_series()
    port = bench_rets + 0.001  # outperforms every day
    m = benchmark.compute(port, bench_close, symbol="SPY")
    labels = {w.label: w for w in m.windows}
    assert set(labels) == {"1d", "1m", "3m"}
    assert all(w.excess_pct > 0 for w in m.windows)
