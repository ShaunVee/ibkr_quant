"""Shared returns / covariance engine — Layer A.

An aligned daily-return matrix and its covariance + correlation for the book's
holdings. Several structural analyses (diversification, risk contribution) stand on
this one object, so the alignment and minimum-history rules live here and are computed
once. Everything is off the price cache already in hand — no new data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_OBS = 60          # need ~3 months of common history before the matrix is trustworthy
MIN_COVERAGE = 0.5    # drop a name whose history covers < this fraction of the longest,
                      # so one freshly-added holding can't truncate the common window


@dataclass(slots=True)
class CovarianceModel:
    symbols: list[str]
    returns: pd.DataFrame    # aligned daily returns, one column per symbol
    cov: pd.DataFrame        # daily covariance matrix
    corr: pd.DataFrame       # correlation matrix
    n_obs: int

    @property
    def ok(self) -> bool:
        return len(self.symbols) >= 2 and self.n_obs >= MIN_OBS


def build(
    price_frames: dict[str, pd.DataFrame], symbols: list[str] | None = None
) -> CovarianceModel | None:
    """Build the covariance model from cached price frames.

    Returns None when there isn't enough common history for a meaningful matrix
    (fewer than two names, or a common window shorter than MIN_OBS).
    """
    cols: dict[str, pd.Series] = {}
    for sym, df in price_frames.items():
        if symbols is not None and sym not in symbols:
            continue
        if df is None or df.empty or "close" not in df.columns:
            continue
        r = df["close"].pct_change().replace([np.inf, -np.inf], np.nan)
        if r.notna().any():
            cols[sym] = r
    if len(cols) < 2:
        return None

    raw = pd.DataFrame(cols)
    max_count = int(raw.notna().sum().max())
    keep = [s for s in raw.columns if raw[s].notna().sum() >= MIN_COVERAGE * max_count]
    frame = raw[keep].dropna(how="any")
    if frame.shape[1] < 2 or len(frame) < MIN_OBS:
        return None

    return CovarianceModel(
        symbols=list(frame.columns),
        returns=frame,
        cov=frame.cov(),
        corr=frame.corr(),
        n_obs=len(frame),
    )
