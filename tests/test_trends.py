"""Tests for the history & trends layer."""

from __future__ import annotations

from datetime import date

from quantbot.analysis import trends
from quantbot.models import AccountSummary, Holding, Portfolio
from quantbot.storage.db import Store


def _rec(day, net_liq, weights, *, vol=None, beta=None, herf=None, flags=None):
    metrics = {}
    if vol is not None:
        metrics["annualized_vol"] = vol
    if beta is not None:
        metrics["portfolio_beta"] = beta
    if herf is not None:
        metrics["herfindahl"] = herf
    if flags is not None:
        metrics["flag_count"] = flags
    return {
        "date": day,
        "net_liq": net_liq,
        "invested_val": net_liq,
        "metrics": metrics,
        "weights": weights,
    }


def test_none_until_two_snapshots():
    assert trends.compute([]) is None
    assert trends.compute([_rec("2026-08-10", 100.0, {"AAPL": 1.0})]) is None


def test_value_trajectory_and_drawdown_from_peak():
    history = [
        _rec("2026-08-09", 100_000.0, {"AAPL": 1.0}),
        _rec("2026-08-10", 110_000.0, {"AAPL": 1.0}),   # peak
        _rec("2026-08-11", 104_500.0, {"AAPL": 1.0}),   # -5% off peak
    ]
    tr = trends.compute(history)
    assert tr is not None
    assert tr.sessions == 3
    assert tr.value_start == 100_000.0
    assert tr.value_end == 104_500.0
    assert tr.peak_value == 110_000.0
    assert tr.drawdown_from_peak is not None
    assert abs(tr.drawdown_from_peak - (-0.05)) < 1e-9
    assert tr.span_days == 2


def test_no_drawdown_when_ending_at_peak():
    history = [
        _rec("2026-08-10", 100_000.0, {"AAPL": 1.0}),
        _rec("2026-08-11", 120_000.0, {"AAPL": 1.0}),
    ]
    tr = trends.compute(history)
    assert tr.drawdown_from_peak == 0.0


def test_metric_drift_only_reports_real_moves():
    history = [
        _rec("2026-08-10", 100.0, {"AAPL": 1.0}, vol=0.14, beta=1.0, flags=2),
        _rec("2026-08-11", 100.0, {"AAPL": 1.0}, vol=0.19, beta=1.0, flags=3),
    ]
    tr = trends.compute(history)
    keys = {m.key for m in tr.metric_drifts}
    assert "annualized_vol" in keys   # 0.14 -> 0.19 is a real move
    assert "flag_count" in keys       # 2 -> 3
    assert "portfolio_beta" not in keys  # unchanged, dropped as noise
    vol = next(m for m in tr.metric_drifts if m.key == "annualized_vol")
    assert abs(vol.delta - 0.05) < 1e-9


def test_weight_drift_thresholding_and_ranking():
    history = [
        _rec("2026-08-10", 100.0, {"AAPL": 0.50, "MSFT": 0.50}),
        _rec("2026-08-11", 100.0, {"AAPL": 0.60, "MSFT": 0.39, "NVDA": 0.01}),
    ]
    tr = trends.compute(history, min_weight_drift=0.02)
    syms = [d.symbol for d in tr.weight_drifts]
    assert syms[0] == "MSFT"          # biggest move (-11pp) ranks first
    assert "AAPL" in syms             # +10pp also called out
    assert "NVDA" not in syms         # +1pp is below the 2pp threshold
    msft = tr.weight_drifts[0]
    assert abs(msft.delta - (-0.11)) < 1e-9


def test_window_limits_to_trailing_records():
    history = [_rec(f"2026-08-{d:02d}", 100.0 + d, {"AAPL": 1.0}) for d in range(1, 11)]
    tr = trends.compute(history, window=3)
    assert tr.sessions == 3
    assert tr.first_date == "2026-08-08"
    assert tr.last_date == "2026-08-10"


def test_handles_missing_net_liq_gracefully():
    history = [
        _rec("2026-08-10", None, {"AAPL": 1.0}),
        _rec("2026-08-11", None, {"AAPL": 1.0}),
    ]
    tr = trends.compute(history)
    assert tr is not None
    assert tr.peak_value is None
    assert tr.drawdown_from_peak is None


def test_snapshot_history_roundtrip_from_store(tmp_path):
    store = Store(tmp_path / "t.db")

    def _pf(net_liq, aapl_mv, msft_mv):
        return Portfolio(
            account=AccountSummary(account_id="U1", base_currency="USD", net_liquidation=net_liq),
            holdings=[
                Holding(symbol="AAPL", quantity=1, market_value=aapl_mv, asset_class="STK"),
                Holding(symbol="MSFT", quantity=1, market_value=msft_mv, asset_class="STK"),
            ],
        )

    store.save_snapshot(_pf(100.0, 60.0, 40.0), metrics={"annualized_vol": 0.14},
                        snapshot_date=date(2026, 8, 10))
    store.save_snapshot(_pf(120.0, 90.0, 30.0), metrics={"annualized_vol": 0.20},
                        snapshot_date=date(2026, 8, 11))

    hist = store.snapshot_history("U1")
    assert len(hist) == 2
    assert hist[0]["date"] == "2026-08-10"
    assert hist[1]["net_liq"] == 120.0
    assert hist[1]["metrics"]["annualized_vol"] == 0.20
    # weights are non-cash fractions of invested value
    assert abs(hist[0]["weights"]["AAPL"] - 0.60) < 1e-9
    assert abs(hist[1]["weights"]["AAPL"] - 0.75) < 1e-9

    # limit keeps the most recent N
    assert [r["date"] for r in store.snapshot_history("U1", limit=1)] == ["2026-08-11"]

    # end-to-end: the store's history feeds the trend model
    tr = trends.compute(hist)
    assert tr is not None and tr.value_end == 120.0
