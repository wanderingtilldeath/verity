"""Build the experiment index: articles_exp.json + tuned-BM25 subfields, filled from the live alias.

python -m eval.relevance.build_exp_index --grid full      # Stage A: 5 k1 x 4 b = 20 subfield pairs
python -m eval.relevance.build_exp_index --grid 1.2:0.75  # Stage B: only the point chosen on A
"""

from __future__ import annotations

import argparse
import copy
import itertools
from collections.abc import Iterator

from elasticsearch import Elasticsearch, helpers

from app.config import get_settings
from eval.relevance.configs import tuned_id
from ingest.index import load_mapping

K1S = [0.6, 0.9, 1.2, 1.5, 2.0]
BS = [0.3, 0.5, 0.75, 0.9]


def mapping_with_grid(points: list[tuple[float, float]]) -> dict:
    m = copy.deepcopy(load_mapping("articles_exp.json"))
    m["settings"].setdefault("similarity", {})
    for k1, b in points:
        tid = tuned_id(k1, b)
        m["settings"]["similarity"][f"bm25_{tid}"] = {"type": "BM25", "k1": k1, "b": b}
        for field in ("title", "text"):
            m["mappings"]["properties"][field]["fields"][tid] = {
                "type": "text",
                "analyzer": "wiki_text",
                "similarity": f"bm25_{tid}",
            }
    return m


def parse_grid(spec: str) -> list[tuple[float, float]]:
    if spec == "full":
        return list(itertools.product(K1S, BS))
    return [tuple(map(float, p.split(":"))) for p in spec.split(",")]  # type: ignore[misc]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default="full")
    ap.add_argument("--index", default="articles_exp")
    ap.add_argument("--recreate", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    es = Elasticsearch(s.es_url, request_timeout=120)
    if es.indices.exists(index=args.index):
        if not args.recreate:
            raise SystemExit(f"{args.index} exists; pass --recreate to rebuild it")
        es.indices.delete(index=args.index)
    m = mapping_with_grid(parse_grid(args.grid))
    es.indices.create(index=args.index, settings=m["settings"], mappings=m["mappings"])
    es.indices.put_settings(index=args.index, settings={"index": {"refresh_interval": "-1"}})

    def actions() -> Iterator[dict]:
        scan = helpers.scan(
            es,
            index=s.es_alias,
            query={"query": {"match_all": {}}, "_source": ["page_id", "title", "text", "url"]},
            size=2000,
        )
        for hit in scan:
            yield {"_index": args.index, "_id": hit["_id"], "_source": hit["_source"]}

    ok, errors = helpers.bulk(
        es, actions(), chunk_size=2000, raise_on_error=False, request_timeout=120
    )
    es.indices.put_settings(index=args.index, settings={"index": {"refresh_interval": "1s"}})
    es.indices.refresh(index=args.index)
    es.indices.forcemerge(index=args.index, max_num_segments=1)
    print(f"{args.index}: {ok} docs indexed, {len(errors)} errors")


if __name__ == "__main__":
    main()
