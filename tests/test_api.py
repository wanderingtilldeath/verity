import pytest
from elasticsearch import AsyncElasticsearch, ConnectionTimeout
from httpx import ASGITransport, AsyncClient

from app import search as search_module
from app.config import Settings
from app.deps import settings_dep
from app.main import create_app
from tests.conftest import ES_URL

pytestmark = pytest.mark.es


@pytest.fixture
async def client(fixture_alias):
    app = create_app()
    app.state.es = AsyncElasticsearch(ES_URL, request_timeout=10)
    app.dependency_overrides[settings_dep] = lambda: Settings(
        es_url=ES_URL,
        es_alias=fixture_alias,
        allow_explain=True,
        max_result_window=60,
        related_min_doc_freq=1,
        related_min_should_match="1",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.app = app
        yield c
    await app.state.es.close()


async def test_search_ranks_title_match_first_and_highlights(client):
    r = await client.get("/search", params={"q": "einstein relativity"})
    assert r.status_code == 200
    body = r.json()
    assert body["hits"][0]["title"] == "Albert Einstein"
    assert any("<mark>" in s for s in body["hits"][0]["snippets"])
    assert body["total"] >= 1 and body["took_ms"] >= 0
    assert r.headers["x-request-id"]


async def test_exact_title_beats_body_mentions(client):
    r = await client.get("/search", params={"q": "jazz"})
    assert r.json()["hits"][0]["title"] == "Jazz"


async def test_snippets_are_html_escaped(client):
    r = await client.get("/search", params={"q": "coffee"})
    for h in r.json()["hits"]:
        for s in h["snippets"]:
            assert "<script" not in s.lower()


async def test_pagination_from_size(client):
    p1 = (await client.get("/search", params={"q": "river", "size": 3, "from": 0})).json()
    p2 = (await client.get("/search", params={"q": "river", "size": 3, "from": 3})).json()
    ids1, ids2 = [h["id"] for h in p1["hits"]], [h["id"] for h in p2["hits"]]
    assert len(ids1) == 3 and not set(ids1) & set(ids2)


async def test_deep_paging_requires_cursor_then_pit_walks_all_hits(client):
    r = await client.get("/search", params={"q": "river", "size": 10, "from": 55})
    assert r.status_code == 400 and r.json()["code"] == "DEEP_PAGING_REQUIRES_CURSOR"

    seen, cursor, pages = [], "start", 0
    while cursor:
        r = await client.get("/search", params={"q": "river", "size": 7, "cursor": cursor})
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [h["id"] for h in body["hits"]]
        cursor, pages = body["next"], pages + 1
        assert pages < 50
    assert len(seen) == len(set(seen)) and len(seen) > 7  # several pages, no duplicates


async def test_cursor_for_other_query_is_rejected(client):
    first = (
        await client.get("/search", params={"q": "river", "size": 2, "cursor": "start"})
    ).json()
    r = await client.get("/search", params={"q": "jazz", "size": 2, "cursor": first["next"]})
    assert r.status_code == 400 and r.json()["code"] == "CURSOR_QUERY_MISMATCH"
    bad = await client.get("/search", params={"q": "river", "cursor": "not-base64!!"})
    assert bad.status_code == 400 and bad.json()["code"] == "BAD_CURSOR"


async def test_explain_returns_explanation_when_enabled(client):
    r = await client.get("/search", params={"q": "jazz", "explain": "true"})
    assert r.json()["hits"][0]["explanation"]["value"] > 0


async def test_explain_disabled_by_default(fixture_alias):
    app = create_app()
    app.state.es = AsyncElasticsearch(ES_URL)
    app.dependency_overrides[settings_dep] = lambda: Settings(es_url=ES_URL, es_alias=fixture_alias)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/search", params={"q": "jazz", "explain": "true"})
    await app.state.es.close()
    assert r.status_code == 403 and r.json()["code"] == "EXPLAIN_DISABLED"


async def test_suggest_completes_prefixes(client):
    r = await client.get("/suggest", params={"q": "albert ein"})
    assert r.status_code == 200
    assert "Albert Einstein" in r.json()["completions"]


async def test_suggest_response_shape_for_misspelling(client):
    r = await client.get("/suggest", params={"q": "einstien"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"completions", "did_you_mean", "took_ms"}
    # the phrase suggester needs corpus statistics; on 50 docs just require sane output
    assert body["did_you_mean"] in (None, "einstein")


async def test_related_excludes_source_and_404s(client):
    r = await client.get("/related/3")  # Marie Curie
    assert r.status_code == 200
    ids = [h["id"] for h in r.json()["hits"]]
    assert 3 not in ids and 1 in ids  # Einstein shares physicist / Nobel Prize vocabulary
    missing = await client.get("/related/999999")
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"


async def test_validation_errors_are_structured(client):
    r = await client.get("/search", params={"q": ""})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["request_id"]
    r = await client.get("/search", params={"q": "x", "size": 1000})
    assert r.status_code == 422


async def test_healthz_ok(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["docs"] == 52


async def test_metrics_exposes_route_histograms(client):
    await client.get("/search", params={"q": "jazz"})
    text = (await client.get("/metrics")).text
    assert 'http_requests_total{method="GET",route="/search",status="200"}' in text
    assert "http_request_seconds_bucket" in text and "es_took_ms_bucket" in text


async def test_es_timeout_maps_to_504(client, monkeypatch):
    async def boom(*a, **k):
        raise ConnectionTimeout("timed out")

    monkeypatch.setattr(search_module, "run_search", boom)
    r = await client.get("/search", params={"q": "jazz"})
    assert r.status_code == 504 and r.json()["code"] == "SEARCH_TIMEOUT"


async def test_stopped_es_gives_503_not_500():
    app = create_app()
    app.state.es = AsyncElasticsearch("http://127.0.0.1:9", request_timeout=1, max_retries=0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/search", params={"q": "jazz"})
        health = await c.get("/healthz")
    await app.state.es.close()
    assert r.status_code == 503 and r.json()["code"] == "SEARCH_UNAVAILABLE"
    assert health.status_code == 503


async def test_static_ui_is_served(client):
    r = await client.get("/")
    assert r.status_code == 200 and "verity" in r.text
