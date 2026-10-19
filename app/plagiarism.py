"""Plagiarism check: winnow the input, fetch candidates with terms_set, re-rank in Python.

Candidates are Articles sharing enough fingerprint hashes with the input. Re-ranking recomputes
winnowing on each candidate's stored `text` (deterministic, so no positions need to be indexed),
scores containment and Jaccard, and aligns shared fingerprints into matching passages.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from elasticsearch import AsyncElasticsearch

from app import metrics
from app.config import Settings
from app.errors import AppError
from app.schemas import PassageMatch, PlagiarismMatch, PlagiarismResponse, Span
from app.search import run_search
from app.winnowing import (
    Fingerprint,
    Token,
    align,
    containment,
    jaccard,
    tokenize,
    winnow,
)

# Match at least 2 hashes (a single shared hash is noise), at least `min_frac` of the input's
# hashes, but never more than the input has (short inputs would otherwise match nothing).
MIN_SHOULD_MATCH = (
    "Math.min(params.num_terms, Math.max(2, (int)Math.ceil(params.num_terms * params.min_frac)))"
)


def candidate_query(hashes: list[str], min_frac: float) -> dict[str, Any]:
    return {
        "terms_set": {
            "fingerprints": {
                "terms": hashes,
                "minimum_should_match_script": {
                    "source": MIN_SHOULD_MATCH,
                    "params": {"min_frac": min_frac},
                },
            }
        }
    }


def _span(text: str, tokens: list[Token], start: int, end: int) -> Span:
    s, e = tokens[start].start, tokens[end - 1].end
    return Span(start=s, end=e, text=text[s:e])


def rerank(
    text: str,
    q_tokens: list[Token],
    q_fps: list[Fingerprint],
    hits: list[dict[str, Any]],
    threshold: float,
    max_results: int,
) -> list[PlagiarismMatch]:
    q_set = {fp.hash for fp in q_fps}
    scored = []
    for h in hits:
        d_text = h["_source"].get("text", "")
        d_tokens = tokenize(d_text)
        d_fps = winnow(d_tokens)
        d_set = {fp.hash for fp in d_fps}
        c = containment(q_set, d_set)
        if c < threshold:
            continue
        scored.append((c, jaccard(q_set, d_set), h, d_text, d_tokens, d_fps))
    scored.sort(key=lambda t: (-t[0], -t[1], int(t[2]["_id"])))

    matches = []
    for c, j, h, d_text, d_tokens, d_fps in scored[:max_results]:
        passages = [
            PassageMatch(
                input=_span(text, q_tokens, p.q_start, min(p.q_end, len(q_tokens))),
                source=_span(d_text, d_tokens, p.d_start, min(p.d_end, len(d_tokens))),
                words=p.words,
            )
            for p in align(q_fps, d_fps)
        ]
        matches.append(
            PlagiarismMatch(
                id=int(h["_id"]),
                title=h["_source"].get("title", ""),
                url=h["_source"].get("url"),
                containment=round(c, 4),
                jaccard=round(j, 4),
                passages=passages,
            )
        )
    return matches


async def check(
    es: AsyncElasticsearch,
    s: Settings,
    text: str,
    threshold: float | None = None,
    max_results: int | None = None,
) -> PlagiarismResponse:
    started = time.perf_counter()
    if len(text) > s.plagiarism_max_chars:
        raise AppError(
            413, "TEXT_TOO_LARGE", f"text is limited to {s.plagiarism_max_chars} characters"
        )
    threshold = s.plagiarism_threshold if threshold is None else threshold
    max_results = max_results or s.plagiarism_max_results

    q_tokens = tokenize(text)
    if len(q_tokens) < s.plagiarism_min_words:
        raise AppError(
            422, "TEXT_TOO_SHORT", f"need at least {s.plagiarism_min_words} words to fingerprint"
        )
    q_fps = await asyncio.to_thread(winnow, q_tokens)
    hashes = list(dict.fromkeys(fp.hash for fp in q_fps))

    body = {
        "query": candidate_query(hashes, s.plagiarism_min_frac),
        "size": s.plagiarism_candidates,
        "_source": ["title", "url", "text"],
        "timeout": f"{int(s.search_timeout_s * 1000)}ms",
        "track_total_hits": False,
    }
    try:
        resp = await run_search(es, s, body, index=s.es_alias)
    except Exception:
        metrics.ES_ERRORS.labels("plagiarism").inc()
        raise
    metrics.ES_TOOK.labels("plagiarism").observe(resp["took"])
    hits = resp["hits"]["hits"]
    metrics.PLAGIARISM_CANDIDATES.observe(len(hits))

    matches = await asyncio.to_thread(rerank, text, q_tokens, q_fps, hits, threshold, max_results)
    return PlagiarismResponse(
        input_words=len(q_tokens),
        input_fingerprints=len(hashes),
        candidates=len(hits),
        took_ms=int((time.perf_counter() - started) * 1000),
        threshold=threshold,
        matches=matches,
    )
