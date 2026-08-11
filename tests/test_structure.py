"""Tests for the covariance engine and its two structural analyses:
diversification (#1, "secretly one bet") and risk contribution (#2, "risk != weight").
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.analysis import contribution, covariance, diversification
from quantbot.analysis.covariance import CovarianceModel


def _frame(closes) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"close": closes}, index=idx)


# --- covariance engine ---------------------------------------------------

def test_build_needs_two_names():
    assert covariance.build({"AAPL": _frame([1, 2, 3])}) is None


def test_build_drops_short_history():
    rng = np.random.default_rng(0)
    long_a = list(100 * (1 + pd.Series(rng.normal(0, 0.01, 200))).cumprod())
    long_b = list(100 * (1 + pd.Series(rng.normal(0, 0.01, 200))).cumprod())
    short_c = [50, 51, 52]  # far too short — should be excluded
    model = covariance.build({"A": _frame(long_a), "B": _frame(long_b), "C": _frame(short_c)})
    assert model is not None
    assert set(model.symbols) == {"A", "B"}
    assert model.cov.shape == (2, 2)


# --- diversification (#1) ------------------------------------------------

def _corr_model(corr_df, symbols) -> CovarianceModel:
    return CovarianceModel(
        symbols=symbols,
        returns=pd.DataFrame(),
        cov=corr_df,
        corr=corr_df,
        n_obs=100,
    )


def test_diversification_detects_one_bet():
    # A and B move identically (corr 1); C independent. Eigenvalues {2,1,0}.
    corr = pd.DataFrame(
        [[1.0, 1.0, 0.0], [1.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        index=["A", "B", "C"], columns=["A", "B", "C"],
    )
    weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    d = diversification.compute(_corr_model(corr, ["A", "B", "C"]), weights, cluster_corr=0.7)

    assert d is not None
    # 3 tickers, but they behave like ~1.9 independent bets.
    assert d.effective_bets == pytest.approx(1.89, abs=0.05)
    assert d.top_factor_share == pytest.approx(2 / 3, abs=1e-6)  # PC1 = 2 of 3
    # A and B cluster together; C stands alone.
    assert len(d.clusters) == 1
    assert d.clusters[0].symbols == ["A", "B"]
    assert d.clusters[0].weight == pytest.approx(2 / 3)


def test_diversification_independent_book_has_full_bets():
    corr = pd.DataFrame(np.eye(3), index=["A", "B", "C"], columns=["A", "B", "C"])
    weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    d = diversification.compute(_corr_model(corr, ["A", "B", "C"]), weights)
    assert d.effective_bets == pytest.approx(3.0, abs=1e-6)
    assert d.top_factor_share == pytest.approx(1 / 3, abs=1e-6)
    assert d.clusters == []


# --- risk contribution (#2) ----------------------------------------------

def test_risk_contribution_is_not_weight():
    # Two uncorrelated names, equal weight, but A twice as volatile (2% vs 1% daily).
    cov = pd.DataFrame(
        [[0.0004, 0.0], [0.0, 0.0001]],
        index=["A", "B"], columns=["A", "B"],
    )
    model = CovarianceModel(symbols=["A", "B"], returns=pd.DataFrame(),
                            cov=cov, corr=pd.DataFrame(), n_obs=100)
    rc = contribution.compute(model, {"A": 0.5, "B": 0.5})

    assert rc is not None
    by = {c.symbol: c for c in rc.contributions}
    # Equal weight, but A carries 80% of the risk, B just 20%.
    assert by["A"].risk_pct == pytest.approx(0.8, abs=1e-6)
    assert by["B"].risk_pct == pytest.approx(0.2, abs=1e-6)
    assert by["A"].ratio == pytest.approx(1.6, abs=1e-6)   # 0.8 / 0.5
    # Contributions sum to the whole.
    assert sum(c.risk_pct for c in rc.contributions) == pytest.approx(1.0)


def test_risk_contribution_renormalizes_partial_coverage():
    cov = pd.DataFrame([[0.0004, 0.0], [0.0, 0.0004]], index=["A", "B"], columns=["A", "B"])
    model = CovarianceModel(symbols=["A", "B"], returns=pd.DataFrame(),
                            cov=cov, corr=pd.DataFrame(), n_obs=100)
    # Book weights don't cover everything (a third of the book is an uncovered name).
    rc = contribution.compute(model, {"A": 0.3, "B": 0.3})
    assert rc.coverage == pytest.approx(0.6)
    # Weights renormalize over the covered pair -> 0.5 each.
    assert all(c.weight == pytest.approx(0.5) for c in rc.contributions)
