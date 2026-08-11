"""Diversification structure — "your book is secretly one bet" (analysis #1).

Answers a question IBKR never volunteers: how many *independent* bets you actually
hold, versus how many tickers. Built from the correlation matrix:

- effective bets  — entropy of the correlation eigenvalue spread (1..N). Your N names
                    behaving like ~K independent bets.
- top factor      — share of variance explained by the single largest statistical
                    factor. High = one thing moves your whole book.
- avg correlation — weight-weighted mean pairwise correlation.
- clusters        — groups that move together above a threshold, naming the hidden
                    themes that sector labels miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from quantbot.analysis.covariance import CovarianceModel


@dataclass(slots=True)
class Cluster:
    symbols: list[str]
    avg_corr: float
    weight: float          # combined book weight (fraction of covered book)


@dataclass(slots=True)
class DiversificationModel:
    n_holdings: int
    coverage: int                              # names with enough history to include
    effective_bets: float | None = None
    top_factor_share: float | None = None      # 0..1
    avg_correlation: float | None = None       # weight-weighted, off-diagonal
    clusters: list[Cluster] = field(default_factory=list)


def _weighted_avg_corr(corr, symbols, weights) -> float | None:
    num = den = 0.0
    for i, a in enumerate(symbols):
        for j in range(i + 1, len(symbols)):
            wa, wb = weights.get(a, 0.0), weights.get(symbols[j], 0.0)
            num += wa * wb * float(corr.iloc[i, j])
            den += wa * wb
    return num / den if den > 0 else None


def _clusters(corr, symbols, weights, threshold) -> list[Cluster]:
    """Connected components where pairwise correlation >= threshold (union-find)."""
    parent = {s: s for s in symbols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    n = len(symbols)
    for i in range(n):
        for j in range(i + 1, n):
            if float(corr.iloc[i, j]) >= threshold:
                union(symbols[i], symbols[j])

    groups: dict[str, list[str]] = {}
    for s in symbols:
        groups.setdefault(find(s), []).append(s)

    out: list[Cluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        idx = [symbols.index(m) for m in members]
        pairs = [
            float(corr.iloc[a, b])
            for ai, a in enumerate(idx)
            for b in idx[ai + 1:]
        ]
        out.append(
            Cluster(
                symbols=sorted(members),
                avg_corr=float(np.mean(pairs)) if pairs else 1.0,
                weight=float(sum(weights.get(m, 0.0) for m in members)),
            )
        )
    out.sort(key=lambda c: -c.weight)
    return out


def compute(
    cov_model: CovarianceModel | None,
    weights: dict[str, float],
    *,
    cluster_corr: float = 0.7,
) -> DiversificationModel | None:
    if cov_model is None or not cov_model.ok:
        return None

    corr = cov_model.corr
    symbols = cov_model.symbols
    model = DiversificationModel(n_holdings=len(weights), coverage=len(symbols))

    eigvals = np.clip(np.linalg.eigvalsh(corr.values), 0.0, None)
    total = float(eigvals.sum())
    if total > 0:
        p = eigvals / total
        p = p[p > 0]
        model.effective_bets = float(np.exp(-np.sum(p * np.log(p))))
        model.top_factor_share = float(eigvals.max() / total)

    model.avg_correlation = _weighted_avg_corr(corr, symbols, weights)
    model.clusters = _clusters(corr, symbols, weights, cluster_corr)
    return model
