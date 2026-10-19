"""python -m eval.relevance.run --stage A --redirects ~/data/wikisearch/redirects_A.jsonl

Runs known-item queries (redirect alias -> target article) through each config against the
experiment index. BM25 k1/b is chosen on the DEV split; every reported number is on TEST.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from elasticsearch import Elasticsearch

from app.config import get_settings
from eval.relevance import metrics, queries
from eval.relevance.configs import FIXED, Builder, config_4_tuned, tuned_id

OUT = Path(__file__).resolve().parent.parent / "results" / "relevance"
DATA = Path.home() / "data" / "wikisearch"


def rank_queries(
    es: Elasticsearch, index: str, builder: Builder, qs: list[queries.Query], batch: int = 200
) -> list[int | None]:
    ranks: list[int | None] = []
    for i in range(0, len(qs), batch):
        chunk = qs[i : i + batch]
        searches: list[dict] = []
        for q in chunk:
            searches += [
                {},
                {"query": builder(q.q), "size": 10, "_source": False, "timeout": "10s"},
            ]
        resp = es.msearch(index=index, searches=searches)
        for q, r in zip(chunk, resp["responses"], strict=True):
            if "error" in r:
                raise RuntimeError(f"query {q.q!r} failed: {r['error']}")
            ids = [int(h["_id"]) for h in r["hits"]["hits"]]
            ranks.append(ids.index(q.target_id) + 1 if q.target_id in ids else None)
    return ranks


def grid_points(es: Elasticsearch, index: str) -> list[tuple[float, float]]:
    props = next(iter(es.indices.get_mapping(index=index).values()))["mappings"]["properties"]
    points = []
    for name in props["text"].get("fields", {}):
        if name.startswith("t_"):
            _, k1, b = name.split("_")
            points.append((float(k1.replace("p", ".")), float(b.replace("p", "."))))
    return sorted(points)


def summarise(ranks: list[int | None]) -> dict:
    vals = metrics.per_query(ranks)
    out = {}
    for name, v in vals.items():
        mean, lo, hi = metrics.bootstrap_ci(v)
        out[name] = {"mean": mean, "ci95": [lo, hi]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="A")
    ap.add_argument("--index", default="articles_exp")
    ap.add_argument("--redirects", type=Path)
    ap.add_argument("--n", type=int, default=5_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    s = get_settings()
    es = Elasticsearch(s.es_url, request_timeout=120)
    qpath = DATA / f"relevance_queries_{args.stage}.jsonl"
    if qpath.exists():
        qs = queries.load(qpath)
    else:
        if not args.redirects:
            raise SystemExit("no query file yet: pass --redirects <redirects.jsonl>")
        qs = queries.build(args.redirects, args.n, args.seed)
        queries.save(qs, qpath)
    dev = [q for q in qs if q.split == "dev"]
    test = [q for q in qs if q.split == "test"]
    print(f"{len(qs)} queries: {len(dev)} dev / {len(test)} test")

    # --- tune BM25 on dev ---
    tuned: dict | None = None
    points = grid_points(es, args.index)
    if points:
        grid = []
        for k1, b in points:
            r = rank_queries(es, args.index, config_4_tuned(k1, b), dev)
            grid.append(
                {"k1": k1, "b": b, "dev_MRR@10": float(metrics.per_query(r)["MRR@10"].mean())}
            )
            print(f"  dev grid k1={k1} b={b}: MRR@10={grid[-1]['dev_MRR@10']:.4f}")
        best = max(grid, key=lambda g: g["dev_MRR@10"])
        tuned = {"best": best, "grid": grid}

    # --- evaluate on test ---
    OUT.mkdir(parents=True, exist_ok=True)
    builders: dict[str, Builder] = dict(FIXED)
    if tuned:
        builders["4_tuned_bm25"] = config_4_tuned(tuned["best"]["k1"], tuned["best"]["b"])
    all_ranks: dict[str, list[int | None]] = {}
    report: dict = {"stage": args.stage, "n_test": len(test), "n_dev": len(dev), "configs": {}}
    for name in sorted(builders):
        ranks = rank_queries(es, args.index, builders[name], test)
        all_ranks[name] = ranks
        report["configs"][name] = summarise(ranks)
        (OUT / f"{args.stage}_{name}.json").write_text(
            json.dumps(
                [
                    {"q": q.q, "target": q.target_id, "rank": r}
                    for q, r in zip(test, ranks, strict=True)
                ]
            )
        )
    if tuned:
        report["bm25_tuning"] = tuned

    # paired comparisons between consecutive configs
    order = [
        n
        for n in ("1_default_single_field", "2_english_analyzer", "3_production", "4_tuned_bm25")
        if n in all_ranks
    ]
    report["paired_mrr"] = {}
    for a, b in zip(order, order[1:], strict=False):
        va = metrics.per_query(all_ranks[a])["MRR@10"]
        vb = metrics.per_query(all_ranks[b])["MRR@10"]
        report["paired_mrr"][f"{a} -> {b}"] = metrics.paired_bootstrap(va, vb)

    (OUT / f"summary_{args.stage}.json").write_text(json.dumps(report, indent=2))
    lines = [
        f"Stage {args.stage}, {len(test)} test queries (95% bootstrap CI)",
        "",
        "| Config | MRR@10 | Recall@1 | Recall@10 | nDCG@10 |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(report["configs"]):
        c = report["configs"][name]
        cells = [
            f"{c[m]['mean']:.3f} [{c[m]['ci95'][0]:.3f}, {c[m]['ci95'][1]:.3f}]"
            for m in metrics.METRICS
        ]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if tuned:
        b = tuned["best"]
        lines += ["", f"Tuned on dev: k1={b['k1']}, b={b['b']} (dev MRR@10 {b['dev_MRR@10']:.3f})"]
    (OUT / f"summary_{args.stage}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("tuned id:", tuned_id(tuned["best"]["k1"], tuned["best"]["b"]) if tuned else None)


if __name__ == "__main__":
    main()
