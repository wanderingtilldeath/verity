"""Build k6 inputs from the live index.

  python -m bench.build_queries --redirects ~/data/wikisearch/redirects_B.jsonl --out-dir bench/data

queries.json            [{"q": ..., "id": ...}] 5,000 mixed alias/title queries, seeded, mixed lengths
plagiarism_inputs.json  {"50": [text...], "200": [...], "1000": [...], "4000": [...], "8000": [...]}
(Written under bench/data/, which is untracked: it contains Wikipedia text.)
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from elasticsearch import Elasticsearch

from app.config import get_settings

LENGTHS = [50, 200, 1000, 4000, 8000]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redirects", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("bench/data"))
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--per-length", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    s = get_settings()
    es = Elasticsearch(s.es_url, request_timeout=60)

    pool: list[dict] = []
    if args.redirects:
        for line in args.redirects.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            pool.append({"q": r["alias"], "id": r["target_id"]})
    resp = es.search(
        index=s.es_alias,
        size=10_000,
        source=["title", "text"],
        query={
            "function_score": {
                "query": {"match_all": {}},
                "random_score": {"seed": args.seed, "field": "_seq_no"},
            }
        },
    )
    docs = [(int(h["_id"]), h["_source"]) for h in resp["hits"]["hits"]]
    pool += [{"q": d["title"], "id": i} for i, d in docs]

    rng.shuffle(pool)
    # Mix of 1-, 2- and 3+-word queries (about a third each) so the set is not all long titles.
    buckets: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    for item in pool:
        buckets[min(3, len(item["q"].split()))].append(item)
    third = args.n // 3
    queries = buckets[1][:third] + buckets[2][:third] + buckets[3][: args.n - 2 * third]
    rng.shuffle(queries)
    (args.out_dir / "queries.json").write_text(json.dumps(queries, ensure_ascii=False))

    texts = [d["text"] for _, d in docs]
    inputs: dict[str, list[str]] = {}
    for n_words in LENGTHS:
        items = []
        for _ in range(args.per_length):
            words: list[str] = []
            while len(words) < n_words:
                words += rng.choice(texts).split()
            items.append(" ".join(words[:n_words]))
        inputs[str(n_words)] = items
    (args.out_dir / "plagiarism_inputs.json").write_text(json.dumps(inputs, ensure_ascii=False))
    print(f"{len(queries)} queries; plagiarism inputs for {LENGTHS} words")


if __name__ == "__main__":
    main()
