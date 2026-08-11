"""Risk contribution — "risk is not weight" (analysis #2).

IBKR shows you weight. It never shows you which position is the actual *source* of your
daily swings — usually not the biggest holding. This decomposes portfolio volatility
into each name's marginal contribution:

    sigma_p = sqrt(wᵀ Σ w)          # portfolio vol
    contrib_i = w_i (Σw)_i / sigma_p # component contribution, sums to sigma_p

reported as each name's share of total risk next to its share of weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from quantbot.analysis.covariance import CovarianceModel
from quantbot.analysis.technical import TRADING_DAYS_YEAR


@dataclass(slots=True)
class RiskContribution:
    symbol: str
    weight: float          # fraction of the covered book
    risk_pct: float        # fraction of total portfolio risk (0..1)

    @property
    def ratio(self) -> float | None:
        """Risk share / weight share. >1 means the name punches above its weight."""
        return self.risk_pct / self.weight if self.weight else None


@dataclass(slots=True)
class ContributionModel:
    annualized_vol: float | None = None   # covariance-based portfolio vol
    coverage: float = 0.0                 # sum of covered weights (of the full book)
    contributions: list[RiskContribution] = field(default_factory=list)


def compute(
    cov_model: CovarianceModel | None, weights: dict[str, float]
) -> ContributionModel | None:
    if cov_model is None or not cov_model.ok:
        return None

    symbols = cov_model.symbols
    raw = np.array([weights.get(s, 0.0) for s in symbols], dtype=float)
    coverage = float(raw.sum())
    if coverage <= 0:
        return None
    w = raw / coverage                              # renormalize over covered names

    sigma = cov_model.cov.values                    # daily covariance
    port_var = float(w @ sigma @ w)
    if port_var <= 0:
        return None
    port_vol = float(np.sqrt(port_var))

    mctr = sigma @ w                                # marginal contribution
    pct = (w * mctr) / port_var                     # share of risk, sums to 1

    model = ContributionModel(
        annualized_vol=port_vol * np.sqrt(TRADING_DAYS_YEAR),
        coverage=coverage,
    )
    model.contributions = sorted(
        (
            RiskContribution(symbol=s, weight=float(w[i]), risk_pct=float(pct[i]))
            for i, s in enumerate(symbols)
        ),
        key=lambda c: -c.risk_pct,
    )
    return model
