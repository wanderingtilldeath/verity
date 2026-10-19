from __future__ import annotations

import os
import uuid

import pytest
from elasticsearch import Elasticsearch

from ingest import index as ix
from tests.corpus import make_docs

ES_URL = os.environ.get("ES_URL", "http://localhost:9200")


def pytest_collection_modifyitems(config, items):
    """Tests marked `es` are skipped (not failed) when no cluster answers."""
    try:
        up = Elasticsearch(ES_URL, request_timeout=2).ping()
    except Exception:
        up = False
    if up:
        return
    skip = pytest.mark.skip(reason=f"no Elasticsearch at {ES_URL}")
    for item in items:
        if "es" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def es() -> Elasticsearch:
    client = Elasticsearch(ES_URL, request_timeout=30)
    yield client
    client.close()


def load_docs(client: Elasticsearch, index: str, docs: list[dict]) -> None:
    from elasticsearch import helpers

    helpers.bulk(
        client,
        ({"_index": index, "_id": str(d["page_id"]), "_source": d} for d in docs),
        refresh=True,
    )


@pytest.fixture(scope="session")
def fixture_alias(es):
    """A throwaway index built from the real mapping, loaded with the fixture corpus, behind an alias."""
    suffix = uuid.uuid4().hex[:8]
    index, alias = f"articles_test_{suffix}", f"articles_test_alias_{suffix}"
    ix.create_index(es, index)
    ix.ensure_alias(es, alias, index)
    load_docs(es, index, make_docs())
    yield alias
    es.indices.delete(index=[index, f"{index}_v2"], ignore_unavailable=True)


@pytest.fixture
def temp_index(es):
    """A fresh empty index with the real mapping, deleted afterwards."""
    name = f"articles_tmp_{uuid.uuid4().hex[:8]}"
    ix.create_index(es, name)
    yield name
    es.indices.delete(index=name, ignore_unavailable=True)
