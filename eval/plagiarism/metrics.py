"""Plagiarism metrics. Pure functions over per-case results, so they are unit-testable offline.

A *result* is {"kind", "labels": [ids], "top": [[id, containment, jaccard], ...] sorted desc,
"ms": float}. A case is flagged when its top-1 containment >= threshold. A flagged positive is
correct when top-1 is a labelled article; a flagged negative, or a flagged positive whose top-1 is
some other article, is a false positive.

Near-duplicates: Wikipedia has templated stubs and copies. If a positive's top-1 is a different
article but BOTH it and a labelled article match with containment >= NEAR_DUP, the case counts as
correct and is tallied separately, instead of being scored as a false positive.
"""

from __future__ import annotations

import statistics

POSITIVE = ("exact", "light_edit", "synonym", "reorder", "mixed")
NEGATIVE = ("neg_unrelated", "neg_same_topic")
NEAR_DUP = 0.9


def top1(r: dict) -> tuple[int | None, float]:
    return (r["top"][0][0], r["top"][0][1]) if r["top"] else (None, 0.0)


def is_near_duplicate(r: dict) -> bool:
    tid, tc = top1(r)
    if tid is None or tid in r["labels"] or tc < NEAR_DUP:
        return False
    return any(i in r["labels"] and c >= NEAR_DUP for i, c, _ in r["top"])


def is_correct(r: dict) -> bool:
    tid, _ = top1(r)
    return tid in r["labels"] or is_near_duplicate(r)


def recall_at_k(results: list[dict], k: int) -> float:
    """Mean share of a case's labelled articles found in the top-k (mixed has two labels)."""
    vals = []
    for r in results:
        found = {i for i, _, _ in r["top"][:k]} & set(r["labels"])
        if is_near_duplicate(r):
            found = set(r["labels"])
        vals.append(len(found) / len(r["labels"]))
    return sum(vals) / len(vals) if vals else float("nan")


def all_labels_found_at_k(results: list[dict], k: int) -> float:
    """Share of cases where EVERY labelled article is in the top-k (the strict mixed metric)."""
    ok = [
        set(r["labels"]) <= {i for i, _, _ in r["top"][:k]} or is_near_duplicate(r) for r in results
    ]
    return sum(ok) / len(ok) if ok else float("nan")


def confusion_at(positives: list[dict], negatives: list[dict], tau: float) -> dict:
    tp = sum(1 for r in positives if top1(r)[1] >= tau and is_correct(r))
    fp_pos = sum(1 for r in positives if top1(r)[1] >= tau and not is_correct(r))
    fp_neg = sum(1 for r in negatives if top1(r)[1] >= tau)
    fp = fp_pos + fp_neg
    return {
        "threshold": tau,
        "tp": tp,
        "fp": fp,
        "fp_negatives": fp_neg,
        "fn": len(positives) - tp,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / len(positives) if positives else float("nan"),
    }


def pr_curve(positives: list[dict], negatives: list[dict]) -> list[dict]:
    taus = sorted({top1(r)[1] for r in positives + negatives} | {0.0, 1.0}, reverse=True)
    return [confusion_at(positives, negatives, t) for t in taus]


def average_precision(curve: list[dict]) -> float:
    """Step-wise AP over a curve ordered by descending threshold (non-decreasing recall)."""
    ap, prev_recall = 0.0, 0.0
    for p in curve:
        if p["recall"] > prev_recall:
            ap += (p["recall"] - prev_recall) * p["precision"]
            prev_recall = p["recall"]
    return ap


def best_f1(curve: list[dict]) -> dict:
    def f1(p: dict) -> float:
        d = p["precision"] + p["recall"]
        return 2 * p["precision"] * p["recall"] / d if d else 0.0

    return max(curve, key=f1)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarise(results: list[dict], threshold: float) -> dict:
    by_kind: dict[str, list[dict]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(r)
    negatives = [r for k in NEGATIVE for r in by_kind.get(k, [])]

    def block(positives: list[dict]) -> dict:
        curve = pr_curve(positives, negatives)
        at = confusion_at(positives, negatives, threshold)
        best = best_f1(curve)
        return {
            "n": len(positives),
            "recall@1": recall_at_k(positives, 1),
            "recall@5": recall_at_k(positives, 5),
            "recall@10": recall_at_k(positives, 10),
            "all_labels@10": all_labels_found_at_k(positives, 10),
            "near_duplicates": sum(is_near_duplicate(r) for r in positives),
            "at_threshold": at,
            "best_f1": best,
            "average_precision": average_precision(curve),
            "latency_ms_p50": statistics.median([r["ms"] for r in positives])
            if positives
            else None,
            "latency_ms_p95": percentile([r["ms"] for r in positives], 0.95),
        }

    summary = {"threshold": threshold, "per_kind": {}, "negatives": {}}
    for kind in POSITIVE:
        if kind in by_kind:
            summary["per_kind"][kind] = block(by_kind[kind])
    pooled = [r for k in POSITIVE for r in by_kind.get(k, [])]
    pooled_no_syn = [r for r in pooled if r["kind"] != "synonym"]
    summary["pooled_all_positives"] = block(pooled)
    summary["pooled_excluding_synonym"] = block(pooled_no_syn)
    for kind in NEGATIVE:
        rs = by_kind.get(kind, [])
        flagged = sum(1 for r in rs if top1(r)[1] >= threshold)
        summary["negatives"][kind] = {
            "n": len(rs),
            "false_positive_rate": flagged / len(rs) if rs else float("nan"),
            "max_top1_containment": max((top1(r)[1] for r in rs), default=0.0),
        }
    return summary


def markdown_table(summary: dict) -> str:
    t = summary["threshold"]
    rows = [
        f"| Transformation | n | recall@1 | recall@10 | precision @ {t} | recall @ {t} | AP | p50 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    items = list(summary["per_kind"].items()) + [
        ("pooled (all positives)", summary["pooled_all_positives"]),
        ("pooled (excl. synonym)", summary["pooled_excluding_synonym"]),
    ]
    for name, b in items:
        a = b["at_threshold"]
        rows.append(
            f"| {name} | {b['n']} | {b['recall@1']:.3f} | {b['recall@10']:.3f} | "
            f"{a['precision']:.3f} | {a['recall']:.3f} | {b['average_precision']:.3f} | "
            f"{b['latency_ms_p50']:.0f} |"
        )
    rows += [
        "",
        "| Negative set | n | false-positive rate | max top-1 containment |",
        "|---|---:|---:|---:|",
    ]
    for name, b in summary["negatives"].items():
        rows.append(
            f"| {name} | {b['n']} | {b['false_positive_rate']:.3f} | {b['max_top1_containment']:.3f} |"
        )
    return "\n".join(rows)
