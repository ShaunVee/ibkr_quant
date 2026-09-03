"""Tests for the plain-English 'Your Money' layer (analysis/money.py)."""

from __future__ import annotations

import pandas as pd

from quantbot.analysis import money
from quantbot.analysis.benchmark import BenchmarkModel, WindowReturn
from quantbot.analysis.diversification import DiversificationModel
from quantbot.analysis.trends import TrendModel
from quantbot.models import Holding


def _bench() -> BenchmarkModel:
    return BenchmarkModel(
        symbol="SPY",
        windows=[
            WindowReturn(label="1d", port_pct=0.4, bench_pct=0.2),
            WindowReturn(label="1m", port_pct=-3.0, bench_pct=-1.0),
            WindowReturn(label="3m", port_pct=6.0, bench_pct=8.0),
        ],
    )


def _trend(drawdown: float | None) -> TrendModel:
    return TrendModel(
        sessions=10, first_date="2026-07-01", last_date="2026-08-01", span_days=31,
        peak_value=110_000.0, drawdown_from_peak=drawdown,
    )


def test_windows_are_dollarized_and_labelled():
    m = money.compute(invested_value=100_000.0, currency="USD", benchmark=_bench())
    assert m is not None
    labels = [w.label for w in m.windows]
    assert labels == ["Today", "Past month", "Past 3 months"]
    today = m.windows[0]
    assert today.pnl == 400.0            # 100k * 0.4%
    assert today.bench_pnl == 200.0      # 100k * 0.2%


def test_counterfactual_uses_longest_window():
    m = money.compute(invested_value=100_000.0, currency="USD", benchmark=_bench())
    # Longest window is 3m: you +6% ($6,000) vs SPY +8% ($8,000) -> $2,000 behind.
    assert m.vs_index_label == "Past 3 months"
    assert m.vs_index_pnl == -2000.0
    assert m.bench_symbol == "SPY"


def test_recovery_asymmetry_math():
    # Down 20% needs +25% to get back to even (0.2 / 0.8).
    m = money.compute(invested_value=100_000.0, currency="USD", trends=_trend(-0.20))
    assert m is not None and m.recovery is not None
    assert round(m.recovery.drawdown_pct, 1) == 20.0
    assert round(m.recovery.gain_needed_pct, 1) == 25.0


def test_no_recovery_when_at_peak():
    m = money.compute(invested_value=100_000.0, currency="USD",
                      benchmark=_bench(), trends=_trend(0.0))
    assert m.recovery is None


def test_best_and_worst_day_in_money():
    rets = pd.Series([0.01, -0.02, 0.015, -0.008],
                     index=pd.date_range("2026-08-01", periods=4, freq="B"))
    m = money.compute(invested_value=100_000.0, currency="USD", port_returns=rets)
    assert m is not None
    assert round(m.best_day.pnl) == 1500      # +1.5% * 100k
    assert round(m.worst_day.pnl) == -2000    # -2.0% * 100k
    assert m.worst_day.day is not None        # date carried from the series index


def test_betting_on_collapses_names_to_bets():
    div = DiversificationModel(
        n_holdings=9, coverage=9, effective_bets=2.1, top_factor_share=0.63
    )
    m = money.compute(invested_value=100_000.0, currency="USD", diversification=div)
    assert m is not None and m.betting_on is not None
    assert "9 holdings" in m.betting_on and "about 2 bet" in m.betting_on
    assert "63%" in m.betting_on


def test_winners_and_losers_split_and_ranked():
    holdings = [
        # SLV: value 12,100 vs cost 10,000 -> +2,100
        Holding(symbol="SLV", quantity=100, avg_cost=100.0, market_value=12_100.0),
        # FIG: value 8,600 vs cost 10,000 -> -1,400
        Holding(symbol="FIG", quantity=100, avg_cost=100.0, market_value=8_600.0),
        # AAPL: value 10,900 vs cost 10,000 -> +900
        Holding(symbol="AAPL", quantity=100, avg_cost=100.0, market_value=10_900.0),
        Holding(symbol="USD", quantity=1, asset_class="CASH", market_value=5_000.0),  # ignored
    ]
    m = money.compute(invested_value=100_000.0, currency="USD", holdings=holdings)
    assert m is not None
    assert [w.symbol for w in m.winners] == ["SLV", "AAPL"]      # biggest gain first
    assert [h.symbol for h in m.losers] == ["FIG"]
    assert round(m.total_unrealized) == 1600                     # 2100 + 900 - 1400
    # Percent is against cost basis: SLV +2,100 on 10,000 = +21%.
    assert round(m.winners[0].pct) == 21


def test_holdings_without_cost_basis_are_skipped():
    holdings = [Holding(symbol="X", quantity=10, avg_cost=None, market_value=1_000.0)]
    m = money.compute(invested_value=100_000.0, currency="USD", holdings=holdings)
    assert m is None                                            # no P/L derivable, nothing else


def test_returns_none_when_nothing_to_say():
    assert money.compute(invested_value=100_000.0, currency="USD") is None
