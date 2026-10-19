import json

import pytest
from elasticsearch import helpers

from ingest import index as ix
from ingest.bulk import Checkpoint, DeadLetter, index_stream
from tests.corpus import make_docs

pytestmark = pytest.mark.es


def stream(docs, start=0):
    for i, d in enumerate(docs[start:], start=start + 1):
        yield i, i, d


def run(es, index, docs, tmp_path, **kw):
    dead = DeadLetter(tmp_path / "dl.jsonl")
    with ix.bulk_mode(es, index):
        stats = index_stream(
            es, index, stream(docs, kw.pop("start", 0)), kw.pop("ckpt", None), dead, **kw
        )
    return stats, dead


def test_running_twice_gives_the_same_count(es, temp_index, tmp_path):
    docs = make_docs()
    run(es, temp_index, docs, tmp_path, threads=2, max_chunk_bytes=5_000)
    first = ix.doc_count(es, temp_index)
    run(es, temp_index, docs, tmp_path, threads=2, max_chunk_bytes=5_000)
    assert first == len(docs) == ix.doc_count(es, temp_index)


def test_resume_after_crash_ends_with_same_count(es, temp_index, tmp_path):
    docs = make_docs()
    ckpt = Checkpoint(tmp_path / "ck.json")

    def crashing():  # parser dies after 20 docs
        yield from stream(docs[:20])
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError), ix.bulk_mode(es, temp_index):
        index_stream(
            es,
            temp_index,
            crashing(),
            ckpt,
            DeadLetter(tmp_path / "dl.jsonl"),
            threads=2,
            max_chunk_bytes=3_000,
        )
    state = ckpt.load()
    assert 0 < state["records"] <= 20

    run(
        es,
        temp_index,
        docs,
        tmp_path,
        start=state["records"],
        ckpt=ckpt,
        threads=2,
        max_chunk_bytes=3_000,
    )
    assert ix.doc_count(es, temp_index) == len(docs)


def test_bad_document_goes_to_dead_letter(es, temp_index, tmp_path):
    docs = make_docs()[:10]
    docs[3] = {**docs[3], "not_in_mapping": "strict mapping rejects this"}
    stats, dead = run(es, temp_index, docs, tmp_path)
    assert (stats.indexed, stats.failed) == (9, 1)
    row = json.loads((tmp_path / "dl.jsonl").read_text().splitlines()[0])
    assert row["page_id"] == docs[3]["page_id"] and row["status"] == 400
    assert ix.doc_count(es, temp_index) == 9


def test_bulk_mode_restores_refresh_interval_even_on_crash(es, temp_index):
    with pytest.raises(RuntimeError), ix.bulk_mode(es, temp_index):
        got = es.indices.get_settings(index=temp_index)[temp_index]["settings"]["index"]
        assert got["refresh_interval"] == "-1"
        raise RuntimeError("boom")
    got = es.indices.get_settings(index=temp_index)[temp_index]["settings"]["index"]
    assert got["refresh_interval"] == "1s"


def test_fingerprints_are_searchable_but_not_returned_in_source(es, temp_index):
    docs = make_docs()[:3]
    helpers.bulk(
        es,
        ({"_index": temp_index, "_id": str(d["page_id"]), "_source": d} for d in docs),
        refresh=True,
    )
    fp = docs[0]["fingerprints"][0]
    hit = es.search(index=temp_index, query={"term": {"fingerprints": fp}})["hits"]["hits"]
    assert [h["_id"] for h in hit] == ["1"]
    assert "fingerprints" not in hit[0]["_source"]
