import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.deps import settings_dep
from app.main import create_app

BASE = (
    "The supplier shall deliver the goods to the buyer within thirty days of receiving a written "
    "purchase order and shall bear all costs of transport and insurance until delivery is complete."
)
OTHER = (
    "Employees accrue paid leave monthly and may carry unused days into the next calendar year "
    "provided that their manager approves the request in writing before the end of December."
)
EDITED = BASE.replace("thirty days", "sixty days")
CLAUSE_A = {"id": "delivery", "text": BASE}
CLAUSE_B = {"id": "leave", "text": OTHER}


@pytest.fixture
async def client():
    app = create_app()
    app.dependency_overrides[settings_dep] = lambda: Settings(
        reuse_max_docs=4, reuse_max_chars=5_000
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def docs(*pairs):
    return [{"id": i, "text": t} for i, t in pairs]


async def dedupe(client, *pairs, **extra):
    return await client.post("/reuse/dedupe", json={"documents": docs(*pairs), **extra})


async def test_exact_and_light_edit_cluster_unrelated_stays_out(client):
    r = await dedupe(client, ("a", BASE), ("b", BASE), ("c", EDITED), ("d", OTHER), threshold=0.4)
    assert r.status_code == 200, r.text
    clusters = r.json()["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["members"] == ["a", "b", "c"]
    assert clusters[0]["canonical"] == "a"  # longest; ties go to the first id


async def test_dedupe_no_duplicates(client):
    r = await dedupe(client, ("a", BASE), ("d", OTHER))
    assert r.json()["clusters"] == []


async def test_dedupe_rejects_duplicate_ids_and_big_requests(client):
    assert (await dedupe(client, ("a", BASE), ("a", OTHER))).json()["code"] == "DUPLICATE_ID"
    many = [(str(i), OTHER) for i in range(5)]
    assert (await dedupe(client, *many)).status_code == 413
    big = "word " * 3_000
    assert (await dedupe(client, ("a", big), ("b", big))).status_code == 413


async def drift(client, text, **extra):
    return await client.post(
        "/reuse/drift", json={"approved": [CLAUSE_A, CLAUSE_B], "text": text, **extra}
    )


async def test_drift_labels_unchanged_drifted_new(client):
    novel = "Disputes shall be settled by arbitration in Mumbai under the rules then in force."
    text = f"{BASE}\n\n{EDITED}\n\n{novel}"
    r = await drift(client, text)
    assert r.status_code == 200, r.text
    s = r.json()["sections"]
    assert [x["status"] for x in s] == ["unchanged", "drifted", "new"]
    assert s[0]["clause_id"] == s[1]["clause_id"] == "delivery"
    assert s[2]["clause_id"] is None
    assert 0 < s[1]["drift"] < 0.7 and s[1]["passages"]
    assert text[s[1]["start"] : s[1]["end"]] == EDITED


async def test_drift_skips_sections_too_short_to_fingerprint(client):
    r = await drift(client, f"Heading\n\n{BASE}")
    assert [x["status"] for x in r.json()["sections"]] == ["unchanged"]
