"""k / w ablation on an in-memory corpus (source articles + random distractors).

Re-indexing 250k docs per (k, w) is not worth it for a sensitivity check, so this uses a local
inverted index over the sources plus `--distractors` random indexed articles and scores the same
cases with the same metrics. Absolute numbers are therefore optimistic (smaller haystack); the
*comparison between settings* is the point.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

from elasticsearch import Elasticsearch

from app.config import get_settings
from app.winnowing import fingerprint_hashes
from eval.plagiarism import generate, metrics

OUT = Path(__file__).resolve().parent.parent / "results" / "plagiarism"


def score(
    cases: list[generate.Case], docs: dict[int, str], k: int, w: int
) -> tuple[list[dict], int]:
    postings: dict[str, set[int]] = defaultdict(set)
    fp_sets: dict[int, set[str]] = {}
    total_fps = 0
    for did, text in docs.items():
        hs = set(fingerprint_hashes(text, k, w))
        fp_sets[did] = hs
        total_fps += len(hs)
        for h in hs:
            postings[h].add(did)
    results = []
    for c in cases:
        q = set(fingerprint_hashes(c.text, k, w))
        counts: Counter[int] = Counter()
        for h in q:
            counts.update(postings.get(h, ()))
        top = sorted(
            ([d, n / len(q), n / (len(q) + len(fp_sets[d]) - n)] for d, n in counts.items()),
            key=lambda t: (-t[1], -t[2], t[0]),
        )[:10]
        results.append({"kind": c.kind, "labels": c.labels, "top": top, "ms": 0.0})
    return results, total_fps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--distractors", type=int, default=9_000)
    ap.add_argument("--ks", type=int, nargs="+", default=[4, 5, 6])
    ap.add_argument("--ws", type=int, nargs="+", default=[3, 4, 6])
    ap.add_argument("--threshold", type=float, default=get_settings().plagiarism_threshold)
    args = ap.parse_args()

    s = get_settings()
    es = Elasticsearch(s.es_url, request_timeout=60)
    cases = generate.read_cases(args.cases)
    ids = sorted({i for c in cases for i in c.labels})
    docs = {
        int(h["_id"]): h["_source"]["text"]
        for h in es.mget(index=s.es_alias, ids=[str(i) for i in ids], source=["text"])["docs"]
        if h.get("found")
    }
    extra = es.search(
        index=s.es_alias,
        size=min(args.distractors, 10_000),
        source=["text"],
        query={
            "function_score": {
                "query": {"match_all": {}},
                "random_score": {"seed": 7, "field": "_seq_no"},
            }
        },
    )
    docs.update({int(h["_id"]): h["_source"]["text"] for h in extra["hits"]["hits"]})

    rows = []
    for k, w in itertools.product(args.ks, args.ws):
        results, total_fps = score(cases, docs, k, w)
        summ = metrics.summarise(results, args.threshold)
        pooled = summ["pooled_excluding_synonym"]
        words = sum(len(t.split()) for t in docs.values())
        rows.append(
            {
                "k": k,
                "w": w,
                "guarantee_words": k + w - 1,
                "recall@10_excl_syn": pooled["recall@10"],
                "precision_at_threshold": pooled["at_threshold"]["precision"],
                "best_f1": pooled["best_f1"],
                "synonym_recall@10": summ["per_kind"].get("synonym", {}).get("recall@10"),
                "fingerprints_per_word": total_fps / words,
                "fingerprints_total": total_fps,
            }
        )
        print(rows[-1])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ablation_kw.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
