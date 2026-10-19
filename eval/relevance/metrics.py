"""Ranking metrics for single-relevant-document queries, with bootstrap confidence intervals.

With exactly one relevant document per query: recall@10 is the hit rate, and nDCG@10 reduces to
1 / log2(rank + 1). `rank` is 1-based, or None when the target is not in the top 10.
"""

from __future__ import annotations

import math

import numpy as np


def rr(rank: int | None, k: int = 10) -> float:
    return 1.0 / rank if rank and rank <= k else 0.0


def hit(rank: int | None, k: int = 10) -> float:
    return 1.0 if rank and rank <= k else 0.0


def ndcg(rank: int | None, k: int = 10) -> float:
    return 1.0 / math.log2(rank + 1) if rank and rank <= k else 0.0


METRICS = {
    "MRR@10": lambda r: rr(r, 10),
    "Recall@1": lambda r: hit(r, 1),
    "Recall@10": lambda r: hit(r, 10),
    "nDCG@10": lambda r: ndcg(r, 10),
}


def per_query(ranks: list[int | None]) -> dict[str, np.ndarray]:
    return {name: np.array([fn(r) for r in ranks]) for name, fn in METRICS.items()}


def bootstrap_ci(values: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n, len(values)))
    means = values[idx].mean(axis=1)
    return float(values.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired_bootstrap(a: np.ndarray, b: np.ndarray, n: int = 2000, seed: int = 0) -> dict:
    """Is B better than A? Same queries, so resample query indices jointly."""
    diff = b - a
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(n, len(diff)))
    means = diff[idx].mean(axis=1)
    return {
        "mean_diff": float(diff.mean()),
        "ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
        "p_b_not_better": float((means <= 0).mean()),
    }
