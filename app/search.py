"""Query builders and execution for /search, /suggest and /related.

Builders are plain functions returning query dicts so the relevance evaluation can reuse the
production query (config 3) against the experiment index instead of re-implementing it.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError

from app import metrics
from app.config import Settings
from app.errors import AppError
from app.schemas import Hit, RelatedResponse, SearchResponse, SuggestResponse

TITLE_BOOST = 4
TITLE_PHRASE_BOOST = 6
TITLE_EXACT_BOOST = 10

HIGHLIGHT = {
    "type": "unified",
    "encoder": "html",  # escape the document text; the UI inserts snippets with innerHTML
    "pre_tags": ["<mark>"],
    "post_tags": ["</mark>"],
    "fields": {
        "text": {"fragment_size": 150, "number_of_fragments": 2, "no_match_size": 150},
        "title": {"number_of_fragments": 0},
    },
}


def build_query(q: str, title_field: str = "title", text_field: str = "text") -> dict[str, Any]:
    """multi_match over title^4 + text, plus a title phrase boost and an exact-title boost.

    The field arguments let the relevance eval run the identical query shape against subfields
    that differ only in similarity (tuned BM25).
    """
    return {
        "bool": {
            "should": [
                {
                    "multi_match": {
                        "query": q,
                        "type": "best_fields",
                        "fields": [f"{title_field}^{TITLE_BOOST}", text_field],
                    }
                },
                {"match_phrase": {title_field: {"query": q, "boost": TITLE_PHRASE_BOOST}}},
                # keyword + normalizer: the normalizer is applied to the query term too
                {"term": {"title.exact": {"value": q, "boost": TITLE_EXACT_BOOST}}},
            ],
            "minimum_should_match": 1,
        }
    }


def _timeout(s: Settings) -> str:
    return f"{int(s.search_timeout_s * 1000)}ms"


def _client(es: AsyncElasticsearch, s: Settings) -> AsyncElasticsearch:
    return es.options(request_timeout=s.es_request_timeout_s)


def _kwargs(body: dict[str, Any]) -> dict[str, Any]:
    """The 8.x client names `_source` -> `source` and `from` -> `from_`; our bodies use ES names."""
    renames = {"_source": "source", "from": "from_"}
    return {renames.get(k, k): v for k, v in body.items()}


async def run_search(
    es: AsyncElasticsearch, s: Settings, body: dict[str, Any], index: str | None = None
) -> dict[str, Any]:
    """Single choke point for ES searches: request timeout always set, `index` omitted for PIT."""
    kwargs = _kwargs(body)
    if index is not None:
        kwargs["index"] = index
    return dict(await es.options(request_timeout=s.es_request_timeout_s).search(**kwargs))


def hit_from_es(h: dict[str, Any]) -> Hit:
    src = h.get("_source", {})
    hl = h.get("highlight", {})
    title_hl = hl.get("title", [None])[0]
    return Hit(
        id=int(h["_id"]),
        title=src.get("title", ""),
        url=src.get("url"),
        score=h.get("_score"),
        snippets=hl.get("text", []),
        title_highlight=title_hl,
        explanation=h.get("_explanation"),
    )


# ---- cursor (PIT + search_after) ------------------------------------------------------------


def encode_cursor(pit_id: str, search_after: list[Any], q: str) -> str:
    raw = json.dumps({"p": pit_id, "s": search_after, "q": q}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: str) -> tuple[str, list[Any], str]:
    try:
        d = json.loads(base64.urlsafe_b64decode(token.encode()))
        pit, sa, q = d["p"], d["s"], d["q"]
        if not (isinstance(pit, str) and isinstance(sa, list) and isinstance(q, str)):
            raise ValueError
        return pit, sa, q
    except Exception as exc:
        raise AppError(400, "BAD_CURSOR", "Malformed cursor") from exc


# ---- /search ----------------------------------------------------------------------------------


async def search(
    es: AsyncElasticsearch,
    s: Settings,
    q: str,
    size: int = 10,
    from_: int = 0,
    highlight: bool = True,
    explain: bool = False,
    cursor: str | None = None,
) -> SearchResponse:
    if size > s.max_page_size:
        raise AppError(422, "SIZE_TOO_LARGE", f"size must be <= {s.max_page_size}")
    if explain and not s.allow_explain:
        raise AppError(403, "EXPLAIN_DISABLED", "explain is disabled (set ALLOW_EXPLAIN=true)")

    body: dict[str, Any] = {
        "query": build_query(q),
        "size": size,
        "_source": ["title", "url", "page_id"],
        "timeout": _timeout(s),
        "track_total_hits": s.max_result_window,
    }
    if highlight:
        body["highlight"] = HIGHLIGHT
    if explain:
        body["explain"] = True

    use_pit = cursor is not None
    if use_pit:
        if from_:
            raise AppError(422, "CURSOR_WITH_FROM", "`from` cannot be combined with `cursor`")
        pit_id: str | None = None
        if cursor == "start":
            opened = await _client(es, s).open_point_in_time(
                index=s.es_alias, keep_alive=s.pit_keep_alive
            )
            pit_id = opened["id"]
        else:
            pit_id, search_after, cursor_q = decode_cursor(cursor)
            if cursor_q != q:
                raise AppError(400, "CURSOR_QUERY_MISMATCH", "cursor belongs to a different query")
            body["search_after"] = search_after
        body["pit"] = {"id": pit_id, "keep_alive": s.pit_keep_alive}
        body["sort"] = [{"_score": "desc"}, {"_shard_doc": "asc"}]
    elif from_ + size > s.max_result_window:
        raise AppError(
            400,
            "DEEP_PAGING_REQUIRES_CURSOR",
            f"from+size exceeds {s.max_result_window}; start with cursor=start and follow `next`",
        )
    else:
        body["from"] = from_

    try:
        resp = await run_search(es, s, body, index=None if use_pit else s.es_alias)
    except Exception:
        metrics.ES_ERRORS.labels("search").inc()
        raise

    metrics.ES_TOOK.labels("search").observe(resp["took"])
    raw_hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]

    next_token = None
    if use_pit:
        new_pit = resp.get("pit_id", body["pit"]["id"])
        if len(raw_hits) == size and raw_hits:
            next_token = encode_cursor(new_pit, raw_hits[-1]["sort"], q)
        else:
            # last page: release the PIT now instead of waiting for keep_alive
            await _client(es, s).close_point_in_time(id=new_pit)

    return SearchResponse(
        query=q,
        total=total["value"],
        total_is_exact=total["relation"] == "eq",
        took_ms=resp["took"],
        timed_out=resp["timed_out"],
        hits=[hit_from_es(h) for h in raw_hits],
        next=next_token,
    )


# ---- /suggest ---------------------------------------------------------------------------------


async def suggest(es: AsyncElasticsearch, s: Settings, q: str, size: int = 8) -> SuggestResponse:
    body = {
        "query": {
            "multi_match": {
                "query": q,
                "type": "bool_prefix",
                "fields": ["title.suggest", "title.suggest._2gram", "title.suggest._3gram"],
            }
        },
        "size": size,
        "_source": ["title"],
        "timeout": _timeout(s),
        # Did-you-mean uses the un-stemmed standard-analysed subfield so corrections are real words.
        "suggest": {
            "text": q,
            "dym": {
                "phrase": {
                    "field": "title.suggest._2gram",
                    "size": 1,
                    "max_errors": 2,
                    "direct_generator": [
                        {"field": "title.suggest", "suggest_mode": "missing", "min_word_length": 3}
                    ],
                }
            },
        },
    }
    try:
        resp = await run_search(es, s, body, index=s.es_alias)
    except Exception:
        metrics.ES_ERRORS.labels("suggest").inc()
        raise
    metrics.ES_TOOK.labels("suggest").observe(resp["took"])

    titles = [h["_source"]["title"] for h in resp["hits"]["hits"]]
    options = resp.get("suggest", {}).get("dym", [{}])[0].get("options", [])
    dym = options[0]["text"] if options and options[0]["text"].lower() != q.lower() else None
    return SuggestResponse(completions=titles, did_you_mean=dym, took_ms=resp["took"])


# ---- /related/{id} ----------------------------------------------------------------------------


async def related(
    es: AsyncElasticsearch, s: Settings, doc_id: int, size: int = 10
) -> RelatedResponse:
    c = _client(es, s)
    try:
        await c.get(index=s.es_alias, id=str(doc_id), source=False)
    except NotFoundError:
        raise AppError(404, "NOT_FOUND", f"No article with id {doc_id}") from None

    body = {
        "query": {
            "more_like_this": {
                "fields": ["title", "text"],
                "like": [{"_index": s.es_alias, "_id": str(doc_id)}],
                "min_term_freq": 1,
                "min_doc_freq": s.related_min_doc_freq,
                "max_query_terms": 25,
                "minimum_should_match": "30%",
            }
        },
        "size": size,
        "_source": ["title", "url", "page_id"],
        "timeout": _timeout(s),
    }
    try:
        resp = await run_search(es, s, body, index=s.es_alias)
    except Exception:
        metrics.ES_ERRORS.labels("related").inc()
        raise
    metrics.ES_TOOK.labels("related").observe(resp["took"])
    return RelatedResponse(
        id=doc_id, took_ms=resp["took"], hits=[hit_from_es(h) for h in resp["hits"]["hits"]]
    )
