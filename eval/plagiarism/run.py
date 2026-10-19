"""python -m eval.plagiarism.run --seed 42 --n 500

Generates (or reuses) the case file, runs every case through app.plagiarism.check against the live
index, and writes raw results, summary JSON, a markdown table and PR-curve plots to
eval/results/plagiarism/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from elasticsearch import AsyncElasticsearch, Elasticsearch

from app import plagiarism
from app.config import get_settings
from eval.plagiarism import generate, metrics

OUT = Path(__file__).resolve().parent.parent / "results" / "plagiarism"
DATA = Path.home() / "data" / "wikisearch"


async def run_cases(cases: list[generate.Case], concurrency: int) -> list[dict]:
    s = get_settings()
    es = AsyncElasticsearch(s.es_url, request_timeout=s.es_request_timeout_s)
    sem = asyncio.Semaphore(concurrency)

    async def one(c: generate.Case) -> dict:
        async with sem:
            t0 = time.perf_counter()
            resp = await plagiarism.check(es, s, c.text, threshold=0.0, max_results=10)
            ms = (time.perf_counter() - t0) * 1000
        return {
            "case_id": c.case_id,
            "kind": c.kind,
            "labels": c.labels,
            "top": [[m.id, m.containment, m.jaccard] for m in resp.matches],
            "candidates": resp.candidates,
            "ms": ms,
        }

    try:
        return await asyncio.gather(*(one(c) for c in cases))
    finally:
        await es.close()


def plot_pr(results: list[dict], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    negatives = [r for r in results if r["kind"] in metrics.NEGATIVE]
    fig, ax = plt.subplots(figsize=(7, 5))
    for kind in metrics.POSITIVE:
        pos = [r for r in results if r["kind"] == kind]
        if not pos:
            continue
        curve = metrics.pr_curve(pos, negatives)
        ax.plot([p["recall"] for p in curve], [p["precision"] for p in curve], label=kind)
    ax.set_xlabel("recall")
    ax.set_ylabel("precision")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(0, 1.0)
    ax.set_title("Plagiarism detection: PR curve per transformation")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--stage", default="A")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--regenerate", action="store_true")
    ap.add_argument("--threshold", type=float, default=get_settings().plagiarism_threshold)
    ap.add_argument("--data-dir", type=Path, default=DATA)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cases_path = args.data_dir / f"plagiarism_cases_{args.stage}_{args.seed}_{args.n}.jsonl"
    s = get_settings()
    if args.regenerate or not cases_path.exists():
        es = Elasticsearch(s.es_url, request_timeout=60)
        cases = generate.generate(
            es, s.es_alias, args.data_dir / f"heldout_{args.stage}.jsonl", args.n, args.seed
        )
        generate.write_cases(cases, cases_path, OUT / "same_topic_spot_check.md")
        (OUT / "cases_manifest.json").write_text(
            json.dumps(
                {
                    "seed": args.seed,
                    "n_sources": args.n,
                    "sources": sorted({c.labels[0] for c in cases if c.kind == "exact"}),
                    "kinds": sorted({c.kind for c in cases}),
                    "note": "case text is regenerated from the index + seed; excerpts are CC BY-SA",
                },
                indent=1,
            )
        )
    else:
        cases = generate.read_cases(cases_path)

    results = asyncio.run(run_cases(cases, args.concurrency))
    with (OUT / "results_raw.jsonl").open("w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    summary = metrics.summarise(results, args.threshold)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    table = metrics.markdown_table(summary)
    (OUT / "summary.md").write_text(table + "\n")
    plot_pr(results, OUT / "pr_curves.png")
    print(table)
    best = summary["pooled_excluding_synonym"]["best_f1"]
    print(
        f"\nbest-F1 threshold (pooled excl. synonym): {best['threshold']:.3f} "
        f"P={best['precision']:.3f} R={best['recall']:.3f}"
    )


if __name__ == "__main__":
    main()
