"""Tests for rule-based flag thresholds."""

from __future__ import annotations

from datetime import date, timedelta

from quantbot.analysis import flags
from quantbot.analysis.risk import RiskMetrics
from quantbot.models import AccountSummary, Fundamentals, Holding, Portfolio, TechnicalSnapshot

THRESHOLDS = {
    "concentration_pct": 25.0,
    "sector_pct": 40.0,
    "portfolio_beta_high": 1.3,
    "rsi_overbought": 70.0,
    "rsi_oversold": 30.0,
    "earnings_soon_days": 5,
    "drawdown_pct": 15.0,
}


def _portfolio():
    return Portfolio(
        account=AccountSummary(account_id="U1"),
        holdings=[
            Holding(symbol="AAPL", quantity=100, market_value=50000, asset_class="STK"),
            Holding(symbol="MSFT", quantity=50, market_value=50000, asset_class="STK"),
        ],
    )


def _codes(flag_list):
    return {f.code for f in flag_list}


def test_concentration_flag_fires_above_threshold():
    risk = RiskMetrics(weights={"AAPL": 0.50, "MSFT": 0.50})
    out = flags.evaluate(_portfolio(), {}, {}, risk, THRESHOLDS)
    assert "CONCENTRATION" in _codes(out)
    # 50% >= 25%*1.5 -> high severity.
    conc = [f for f in out if f.code == "CONCENTRATION"]
    assert all(f.severity == "high" for f in conc)


def test_no_concentration_when_balanced():
    risk = RiskMetrics(weights={"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2})
    out = flags.evaluate(_portfolio(), {}, {}, risk, THRESHOLDS)
    assert "CONCENTRATION" not in _codes(out)


def test_high_beta_flag():
    risk = RiskMetrics(weights={"AAPL": 1.0}, portfolio_beta=1.5)
    out = flags.evaluate(_portfolio(), {}, {}, risk, THRESHOLDS)
    assert "HIGH_BETA" in _codes(out)


def test_sector_overweight_flag():
    risk = RiskMetrics(weights={"AAPL": 1.0}, sector_weights={"Technology": 0.9})
    out = flags.evaluate(_portfolio(), {}, {}, risk, THRESHOLDS)
    assert "SECTOR_OVERWEIGHT" in _codes(out)


def test_rsi_overbought_and_oversold():
    risk = RiskMetrics(weights={"AAPL": 0.5, "MSFT": 0.5})
    techs = {
        "AAPL": TechnicalSnapshot(symbol="AAPL", rsi14=80.0),
        "MSFT": TechnicalSnapshot(symbol="MSFT", rsi14=20.0),
    }
    out = flags.evaluate(_portfolio(), {}, techs, risk, THRESHOLDS)
    assert {"OVERBOUGHT", "OVERSOLD"} <= _codes(out)


def test_earnings_soon_flag():
    today = date(2026, 8, 11)
    risk = RiskMetrics(weights={"AAPL": 1.0})
    funds = {"AAPL": Fundamentals(symbol="AAPL", next_earnings=today + timedelta(days=3))}
    out = flags.evaluate(_portfolio(), funds, {}, risk, THRESHOLDS, today=today)
    assert "EARNINGS_SOON" in _codes(out)


def test_earnings_far_out_does_not_flag():
    today = date(2026, 8, 11)
    risk = RiskMetrics(weights={"AAPL": 1.0})
    funds = {"AAPL": Fundamentals(symbol="AAPL", next_earnings=today + timedelta(days=30))}
    out = flags.evaluate(_portfolio(), funds, {}, risk, THRESHOLDS, today=today)
    assert "EARNINGS_SOON" not in _codes(out)


def test_drawdown_flag_fires_on_realized():
    risk = RiskMetrics(weights={"AAPL": 1.0}, realized_drawdown=-0.20)
    out = flags.evaluate(_portfolio(), {}, {}, risk, THRESHOLDS)
    assert "DRAWDOWN" in _codes(out)


def test_drawdown_flag_ignores_simulated():
    # A big *simulated* drawdown is a "could have", not a loss taken -> no breach.
    risk = RiskMetrics(weights={"AAPL": 1.0}, max_drawdown=-0.44)
    out = flags.evaluate(_portfolio(), {}, {}, risk, THRESHOLDS)
    assert "DRAWDOWN" not in _codes(out)


def test_flags_sorted_by_severity():
    risk = RiskMetrics(
        weights={"AAPL": 0.5, "MSFT": 0.5},
        portfolio_beta=1.5,
    )
    techs = {"AAPL": TechnicalSnapshot(symbol="AAPL", rsi14=80.0)}
    out = flags.evaluate(_portfolio(), {}, techs, risk, THRESHOLDS)
    ranks = [flags._SEVERITY_RANK[f.severity] for f in out]
    assert ranks == sorted(ranks)
