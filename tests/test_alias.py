import threading
import time

import pytest

from app.plagiarism import candidate_query
from ingest import index as ix
from tests.conftest import load_docs
from tests.corpus import ARTICLES, make_docs

pytestmark = pytest.mark.es


def test_reindex_swaps_alias_with_zero_failed_queries_and_keeps_fingerprints(es, temp_index):
    alias = f"{temp_index}_alias"
    new_index = f"{temp_index}_v2"
    load_docs(es, temp_index, make_docs())
    ix.ensure_alias(es, alias, temp_index)

    failures: list[Exception] = []
    counts: list[int] = []
    stop = threading.Event()

    def hammer():
        while not stop.is_set():
            try:
                counts.append(es.count(index=alias)["count"])
            except Exception as exc:  # any error during the swap is a failure
                failures.append(exc)
            time.sleep(0.01)

    t = threading.Thread(target=hammer)
    t.start()
    try:
        copied = ix.reindex_and_swap(es, alias, temp_index, new_index)
    finally:
        time.sleep(0.2)
        stop.set()
        t.join()

    try:
        assert copied == len(make_docs())
        assert not failures
        assert set(counts) == {len(make_docs())}  # never an empty or partial index
        assert list(es.indices.get_alias(name=alias)) == [new_index]

        # fingerprints were recomputed from `text`: a plagiarism-style query still finds the doc
        from app.winnowing import fingerprint_hashes

        hashes = fingerprint_hashes(ARTICLES[0][2])
        resp = es.search(index=alias, query=candidate_query(hashes, 0.05), size=3)
        assert resp["hits"]["hits"][0]["_id"] == "1"
    finally:
        es.indices.delete(index=[new_index], ignore_unavailable=True)
