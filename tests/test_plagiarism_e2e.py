import pytest
from elasticsearch import AsyncElasticsearch
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.deps import settings_dep
from app.main import create_app
from tests.conftest import ES_URL
from tests.corpus import ARTICLES

pytestmark = pytest.mark.es

EINSTEIN = ARTICLES[0][2]
CURIE = ARTICLES[2][2]


@pytest.fixture
async def client(fixture_alias):
    app = create_app()
    app.state.es = AsyncElasticsearch(ES_URL)
    app.dependency_overrides[settings_dep] = lambda: Settings(
        es_url=ES_URL, es_alias=fixture_alias, plagiarism_max_chars=5_000
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.es.close()


async def check(client, text, **extra):
    return await client.post("/plagiarism/check", json={"text": text, **extra})


async def test_exact_copy_is_found_first_with_passages(client):
    paragraph = "Intro words before. " + EINSTEIN + " And a closing remark afterwards."
    r = await check(client, paragraph)
    assert r.status_code == 200, r.text
    top = r.json()["matches"][0]
    assert top["id"] == 1 and top["title"] == "Albert Einstein"
    assert top["containment"] > 0.8
    p = top["passages"][0]
    assert paragraph[p["input"]["start"] : p["input"]["end"]] == p["input"]["text"]
    assert p["input"]["text"].lower().startswith("albert einstein was a german")
    assert p["words"] >= 40


async def test_light_edit_still_matches(client):
    edited = EINSTEIN.replace("German-born", "Swiss").replace("pivotal", "crucial")
    r = await check(client, edited)
    assert r.json()["matches"][0]["id"] == 1


async def test_mixed_sources_return_both(client):
    half_a = " ".join(EINSTEIN.split()[:45])
    half_b = " ".join(CURIE.split()[:45])
    ids = [
        m["id"]
        for m in (await check(client, half_a + " " + half_b, threshold=0.2)).json()["matches"]
    ]
    assert {1, 3} <= set(ids)


async def test_unrelated_text_has_no_matches(client):
    text = "completely different words about nothing indexed here at all, " * 6
    r = await check(client, text)
    assert r.status_code == 200 and r.json()["matches"] == []


async def test_too_short_and_too_large_inputs(client):
    short = await check(client, "only three words")
    assert short.status_code == 422 and short.json()["code"] == "TEXT_TOO_SHORT"
    big = await check(client, "word " * 2_000)  # 10,000 chars > 5,000 cap in this fixture
    assert big.status_code == 413 and big.json()["code"] == "TEXT_TOO_LARGE"


async def test_request_validation(client):
    assert (
        await client.post("/plagiarism/check", json={"text": "x", "bogus": 1})
    ).status_code == 422
    assert (await check(client, "a b c d e f g h i j", threshold=2)).status_code == 422
