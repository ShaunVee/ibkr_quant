"""Tests for technical indicators against known/analytic values."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.analysis import technical
from quantbot.models import TechnicalSnapshot


def test_sma_simple():
    s = pd.Series([1, 2, 3, 4, 5], dtype="float64")
    assert technical.sma(s, 5) == pytest.approx(3.0)
    assert technical.sma(s, 2) == pytest.approx(4.5)
    assert technical.sma(s, 10) is None  # not enough data


def test_rsi_all_gains_is_100():
    s = pd.Series(np.arange(1, 40, dtype="float64"))  # strictly increasing
    assert technical.rsi(s, 14) == pytest.approx(100.0)


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    r = technical.rsi(s, 14)
    assert r is not None and 0.0 <= r <= 100.0


def test_macd_returns_two_values():
    s = pd.Series(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 100)))
    macd_line, signal = technical.macd(s)
    assert macd_line is not None and signal is not None


def test_bollinger_pct_midpoint():
    s = pd.Series([10.0] * 19 + [10.0])  # zero variance -> midpoint 0.5
    assert technical.bollinger_pct(s, 20) == pytest.approx(0.5)


def test_compute_snapshot_fields():
    idx = pd.date_range("2024-01-01", periods=260, freq="B")
    rng = np.random.default_rng(3)
    close = 100 * (1 + pd.Series(rng.normal(0.0005, 0.01, len(idx)))).cumprod()
    df = pd.DataFrame(
        {
            "open": close.values,
            "high": close.values * 1.01,
            "low": close.values * 0.99,
            "close": close.values,
            "volume": 1_000_000,
        },
        index=idx,
    )
    snap = technical.compute("TEST", df)
    assert snap.last_price is not None
    assert snap.sma50 is not None and snap.sma200 is not None
    assert snap.golden_cross in (True, False)
    assert snap.rsi14 is not None
    assert snap.atr14 is not None
    assert snap.ret_1m is not None


def test_compute_empty_frame_is_safe():
    snap = technical.compute("TEST", pd.DataFrame())
    assert snap.last_price is None


def test_obv_rising_when_price_and_volume_climb():
    # Strictly rising closes -> every day adds volume -> OBV monotonically increases.
    n = 40
    df = pd.DataFrame(
        {
            "close": np.arange(1, n + 1, dtype="float64"),
            "volume": np.full(n, 1_000.0),
        }
    )
    last, trend = technical.obv(df)
    assert last == pytest.approx(1_000.0 * (n - 1))  # first diff is NaN->0, rest add
    assert trend == "rising"


def test_obv_falling_when_price_declines():
    n = 40
    df = pd.DataFrame(
        {
            "close": np.arange(n, 0, -1, dtype="float64"),
            "volume": np.full(n, 1_000.0),
        }
    )
    _, trend = technical.obv(df)
    assert trend == "falling"


def test_obv_missing_volume_is_safe():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    assert technical.obv(df) == (None, None)


def test_derive_signals_trend_macd_obv():
    snap = TechnicalSnapshot(
        symbol="X",
        golden_cross=True,
        macd=1.0,
        macd_signal=0.5,
        rsi14=50.0,            # neutral -> no RSI tag
        bollinger_pct=0.5,     # mid -> no BB tag
        obv_trend="rising",
    )
    codes = {s.code for s in technical.derive_signals(snap)}
    assert {"TREND_UP", "MACD_BULL", "OBV_RISING"} <= codes
    assert not {"RSI_OVERBOUGHT", "RSI_OVERSOLD", "BB_UPPER", "BB_LOWER"} & codes


def test_derive_signals_rsi_extremes_respect_thresholds():
    hot = TechnicalSnapshot(symbol="X", rsi14=75.0)
    cold = TechnicalSnapshot(symbol="X", rsi14=25.0)
    assert any(s.code == "RSI_OVERBOUGHT" for s in technical.derive_signals(hot))
    assert any(s.code == "RSI_OVERSOLD" for s in technical.derive_signals(cold))
    # Custom thresholds shift the boundary.
    assert not any(
        s.code == "RSI_OVERBOUGHT"
        for s in technical.derive_signals(hot, rsi_overbought=80.0)
    )


def test_derive_signals_empty_snapshot_is_silent():
    assert technical.derive_signals(TechnicalSnapshot(symbol="X")) == []
