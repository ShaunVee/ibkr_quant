"""Tests for the event radar (analysis #4)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from quantbot.analysis import events
from quantbot.models import Fundamentals

TODAY = date(2024, 6, 3)  # a Monday


def _fund(symbol, earnings=None):
    return Fundamentals(symbol=symbol, next_earnings=earnings)


def _price_frame(n=200, seed=0, beta=1.0, proxy_rets=None):
    idx = pd.date_range("2023-08-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    if proxy_rets is None:
        rets = pd.Series(rng.normal(0.0003, 0.01, n), index=idx)
    else:
        # Construct a name whose returns are `beta` * proxy plus small idiosyncratic noise.
        noise = pd.Series(rng.normal(0, 0.0005, len(proxy_rets)), index=proxy_rets.index)
        rets = beta * proxy_rets + noise
    close = 100 * (1 + rets).cumprod()
    return pd.DataFrame({"close": close}, index=rets.index)


def test_earnings_calendar_within_horizon_only():
    funds = {
        "AAA": _fund("AAA", date(2024, 6, 5)),   # in 2 days -> included
        "BBB": _fund("BBB", date(2024, 6, 20)),  # in 17 days -> outside 14d horizon
        "CCC": _fund("CCC", None),               # unknown -> skipped
    }
    weights = {"AAA": 0.25, "BBB": 0.30, "CCC": 0.10}
    radar = events.compute(funds, weights, horizon_days=14, today=TODAY)

    assert radar is not None
    assert [e.symbol for e in radar.earnings] == ["AAA"]
    assert radar.earnings[0].days_away == 2
    assert radar.earnings_weight == 0.25


def test_earnings_sorted_by_proximity():
    funds = {
        "FAR": _fund("FAR", date(2024, 6, 12)),
        "NEAR": _fund("NEAR", date(2024, 6, 4)),
    }
    weights = {"FAR": 0.1, "NEAR": 0.1}
    radar = events.compute(funds, weights, horizon_days=14, today=TODAY)
    assert [e.symbol for e in radar.earnings] == ["NEAR", "FAR"]


def test_macro_overlay_filtered_and_sorted():
    macro = [
        {"date": "2024-06-06", "event": "CPI", "impact": "high"},
        {"date": "2024-06-04", "event": "ISM", "impact": "medium"},
        {"date": "2024-07-30", "event": "FOMC", "impact": "high"},  # outside horizon
        {"date": "not-a-date", "event": "junk"},                    # unparsable -> skipped
    ]
    radar = events.compute({}, {}, macro_events=macro, horizon_days=14, today=TODAY)
    assert radar is not None
    assert [m.event for m in radar.macro] == ["ISM", "CPI"]
    assert radar.macro[0].days_away == 1


def test_rate_sensitivity_beta_and_share():
    proxy = _price_frame(seed=42)
    proxy_rets = proxy["close"].pct_change().dropna()
    # SENS tracks the proxy at beta ~1; FLAT is independent noise (near-zero beta).
    frames = {
        "SENS": _price_frame(seed=1, beta=1.0, proxy_rets=proxy_rets),
        "FLAT": _price_frame(seed=7),
    }
    weights = {"SENS": 0.40, "FLAT": 0.20}
    radar = events.compute(
        {}, weights,
        price_frames=frames,
        rate_proxy_close=proxy["close"],
        rate_proxy="TLT",
        rate_beta_threshold=0.20,
        today=TODAY,
    )
    assert radar is not None
    syms = [s.symbol for s in radar.rate_sensitive]
    assert "SENS" in syms
    assert "FLAT" not in syms
    sens = next(s for s in radar.rate_sensitive if s.symbol == "SENS")
    assert sens.beta == pytest_approx(1.0)
    assert radar.rate_proxy == "TLT"
    assert radar.rate_sensitive_weight == 0.40


def test_returns_none_when_nothing_lands():
    # No earnings in horizon, no macro, no rate proxy -> nothing to show.
    funds = {"AAA": _fund("AAA", date(2024, 12, 25))}
    radar = events.compute(funds, {"AAA": 0.5}, horizon_days=14, today=TODAY)
    assert radar is None


# Small local approx to avoid importing pytest just for one assertion style.
def pytest_approx(expected, tol=0.1):
    class _A:
        def __eq__(self, other):
            return abs(other - expected) <= tol
        def __repr__(self):
            return f"~{expected}"
    return _A()
