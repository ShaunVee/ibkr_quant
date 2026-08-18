"""Tests for driver attribution + catalyst headlines (analysis/drivers.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantbot.analysis import drivers
from quantbot.analysis.movement import MoveContext, PositionMove
from quantbot.models import Fundamentals


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"close": closes}, index=idx)


def _move(symbol: str, ret_pct: float, *, z: float | None = None) -> PositionMove:
    return PositionMove(symbol=symbol, ret_pct=ret_pct, contribution_pp=ret_pct, z=z)


DMAP = {"SLV": {"ticker": "SI=F", "theme": "silver"}}
DEFAULT = {"ticker": "SPY", "theme": "the broad market"}


def test_driver_for_resolves_and_falls_back():
    assert drivers.driver_for("SLV", DMAP, DEFAULT)["theme"] == "silver"
    assert drivers.driver_for("AAPL", DMAP, DEFAULT)["ticker"] == "SPY"
    # A name cannot be attributed to its own series.
    assert drivers.driver_for("SPY", DMAP, DEFAULT) is None
    assert drivers.driver_for("SLV", {}, None) is None


def test_needed_tickers_dedupes():
    move = MoveContext(
        port_ret_pct=1.0,
        top_contributors=[_move("SLV", 3.0), _move("AAPL", 1.0)],
        abnormal_names=[_move("SLV", 3.0)],
    )
    tickers = drivers.needed_driver_tickers(move, DMAP, DEFAULT)
    assert tickers == ["SI=F", "SPY"]


def test_systematic_move_tracks_theme():
    # SLV moves ~1:1 with silver every day, including a big final day -> systematic.
    rng = np.random.default_rng(1)
    drv = [50.0]
    name = [25.0]
    for r in rng.normal(0, 0.01, 150):
        drv.append(drv[-1] * (1 + r))
        name.append(name[-1] * (1 + r))          # identical returns -> beta ~ 1, R^2 ~ 1
    drv.append(drv[-1] * 1.03)                    # silver +3% today
    name.append(name[-1] * 1.03)                  # SLV +3% today

    move = MoveContext(port_ret_pct=3.0, top_contributors=[_move("SLV", 3.0)])
    model = drivers.compute(
        move, {"SLV": _frame(name)}, {"SI=F": _frame(drv)}, DMAP, DEFAULT
    )
    a = model.attributions[0]
    assert a.symbol == "SLV"
    assert a.kind == "systematic"
    assert a.beta == pytest_approx(1.0, 0.1)
    assert abs(a.residual_pct) < abs(a.explained_pct)


def test_idiosyncratic_move_is_name_specific():
    # Theme is flat today but the name jumps -> the move is name-specific.
    rng = np.random.default_rng(2)
    drv = [100.0]
    name = [40.0]
    for r in rng.normal(0, 0.01, 150):
        drv.append(drv[-1] * (1 + r))
        name.append(name[-1] * (1 + r))
    drv.append(drv[-1] * 1.000)                   # theme flat today
    name.append(name[-1] * 1.06)                  # name +6% on its own

    move = MoveContext(
        port_ret_pct=6.0,
        top_contributors=[_move("FIG", 6.0, z=3.0)],
        abnormal_names=[_move("FIG", 6.0, z=3.0)],
    )
    model = drivers.compute(
        move, {"FIG": _frame(name)}, {"SPY": _frame(drv)}, {}, DEFAULT
    )
    a = model.attributions[0]
    assert a.kind == "idiosyncratic"
    assert abs(a.residual_pct) > abs(a.explained_pct)


def test_missing_theme_frame_yields_unattributed():
    move = MoveContext(port_ret_pct=3.0, top_contributors=[_move("SLV", 3.0)])
    model = drivers.compute(move, {"SLV": _frame([1, 2, 3])}, {}, DMAP, DEFAULT)
    a = model.attributions[0]
    assert a.kind == "unattributed"
    assert a.explained_pct is None


def test_tiny_moves_skipped():
    move = MoveContext(port_ret_pct=0.1, top_contributors=[_move("SLV", 0.05)])
    model = drivers.compute(move, {"SLV": _frame([1, 2, 3])}, {}, DMAP, DEFAULT)
    assert model.attributions == []


def test_catalysts_only_for_abnormal_stocks():
    calls: list[str] = []

    def news_fn(sym: str):
        calls.append(sym)
        return [drivers.Catalyst(symbol=sym, headline=f"{sym} in the news", days_ago=1)]

    # META is an abnormal single stock (has a sector); SLV is abnormal but an ETF (no sector);
    # AAPL is a stock but not abnormal. Only META should get a headline.
    move = MoveContext(
        port_ret_pct=1.0,
        top_contributors=[_move("META", 2.0), _move("AAPL", 1.0), _move("SLV", 3.0)],
        abnormal_names=[_move("META", 2.0, z=3.0), _move("SLV", 3.0, z=3.0)],
    )
    frames = {"META": _frame([1, 2, 3]), "AAPL": _frame([1, 2, 3]), "SLV": _frame([1, 2, 3])}
    funds = {
        "META": Fundamentals(symbol="META", sector="Communication Services"),
        "AAPL": Fundamentals(symbol="AAPL", sector="Technology"),
        "SLV": Fundamentals(symbol="SLV"),   # ETF: no sector
    }
    model = drivers.compute(
        move, frames, {}, {}, DEFAULT, fundamentals=funds, news_fn=news_fn
    )
    assert calls == ["META"]
    assert [c.symbol for c in model.catalysts] == ["META"]


def pytest_approx(value: float, rel: float):
    import pytest

    return pytest.approx(value, rel=rel)
