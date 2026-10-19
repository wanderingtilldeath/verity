"""Index lifecycle: create from the JSON mapping, alias management, bulk mode, zero-downtime reindex."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from elasticsearch import Elasticsearch, helpers

from app.winnowing import fingerprint_hashes
from ingest.bulk import DeadLetter, index_stream

log = logging.getLogger("ingest.index")
ES_DIR = Path(__file__).resolve().parent.parent / "es"


def load_mapping(name: str = "articles_v1.json") -> dict:
    return json.loads((ES_DIR / name).read_text())


def create_index(client: Elasticsearch, index: str, mapping: dict | None = None) -> bool:
    """Create the index if it does not exist. Returns True if it was created."""
    if client.indices.exists(index=index):
        return False
    mapping = mapping or load_mapping()
    client.indices.create(index=index, settings=mapping["settings"], mappings=mapping["mappings"])
    log.info("created index %s", index)
    return True


def ensure_alias(client: Elasticsearch, alias: str, index: str) -> None:
    if not client.indices.exists_alias(name=alias, index=index):
        client.indices.put_alias(index=index, name=alias)


@contextmanager
def bulk_mode(client: Elasticsearch, index: str, restore_refresh: str = "1s") -> Iterator[None]:
    """refresh_interval=-1 while loading; always restored, even if the load crashes."""
    client.indices.put_settings(index=index, settings={"index": {"refresh_interval": "-1"}})
    try:
        yield
    finally:
        client.indices.put_settings(
            index=index, settings={"index": {"refresh_interval": restore_refresh}}
        )
        client.indices.refresh(index=index)


def force_merge(client: Elasticsearch, index: str, max_segments: int = 1) -> None:
    client.indices.forcemerge(index=index, max_num_segments=max_segments)


def doc_count(client: Elasticsearch, index: str) -> int:
    return client.count(index=index)["count"]


def index_stats(client: Elasticsearch, index: str) -> dict:
    st = client.indices.stats(index=index, metric="store,docs,segments")["_all"]["primaries"]
    return {
        "docs": st["docs"]["count"],
        "size_bytes": st["store"]["size_in_bytes"],
        "segments": st["segments"]["count"],
    }


def reindex_and_swap(
    client: Elasticsearch,
    alias: str,
    old_index: str,
    new_index: str,
    mapping: dict | None = None,
    delete_old: bool = False,
    dead_letter: Path | None = None,
) -> int:
    """Zero-downtime reindex: build new index, copy, atomically move the alias, optionally drop old.

    Done as scan + bulk rather than `_reindex` because `fingerprints` is excluded from `_source`
    (it would be lost). Fingerprints are recomputed from `text`, which also lets a reindex change
    the winnowing parameters. Writes to the old index during the copy are not carried over.
    """
    create_index(client, new_index, mapping)
    client.indices.put_settings(index=new_index, settings={"index": {"refresh_interval": "-1"}})

    def stream() -> Iterator[tuple[int, int, dict]]:
        n = 0
        query = {"query": {"match_all": {}}, "_source": ["page_id", "title", "text", "url"]}
        for hit in helpers.scan(client, query=query, index=old_index, size=2000):
            doc = hit["_source"]
            doc["fingerprints"] = fingerprint_hashes(doc["text"])
            n += 1
            yield n, n, doc

    dead = DeadLetter(dead_letter or Path(f"dead_letter_reindex_{new_index}.jsonl"))
    stats = index_stream(client, new_index, stream(), None, dead)
    if stats.failed:
        raise RuntimeError(f"reindex failed for {stats.failed} docs; see {dead.path}")
    client.indices.put_settings(index=new_index, settings={"index": {"refresh_interval": "1s"}})
    client.indices.refresh(index=new_index)
    client.indices.update_aliases(
        actions=[
            {"remove": {"index": old_index, "alias": alias}},
            {"add": {"index": new_index, "alias": alias}},
        ]
    )
    if delete_old:
        client.indices.delete(index=old_index)
    return stats.indexed
