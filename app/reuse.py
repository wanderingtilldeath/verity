"""Reuse detection over caller-supplied documents (no Elasticsearch): dedupe and template drift.

Both build on the winnowing core used by plagiarism checks. Everything is in memory, so these
suit up to a few thousand documents; beyond that, index them and use `terms_set` candidates.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import combinations

from app.errors import AppError
from app.plagiarism import _span
from app.schemas import (
    DedupeCluster,
    DedupeResponse,
    DriftResponse,
    DriftSection,
    PairScore,
    PassageMatch,
    TextDoc,
)
from app.winnowing import align, containment, jaccard, tokenize, winnow

UNCHANGED = 0.95  # containment at or above this counts as the approved text, verbatim enough
_PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", re.DOTALL)


def _hashes(text: str) -> set[str]:
    return {fp.hash for fp in winnow(tokenize(text))}


def dedupe(docs: list[TextDoc], threshold: float) -> DedupeResponse:
    ids = [d.id for d in docs]
    if len(set(ids)) != len(ids):
        raise AppError(422, "DUPLICATE_ID", "document ids must be unique")
    sets = {d.id: _hashes(d.text) for d in docs}
    lengths = {d.id: len(d.text) for d in docs}

    by_hash: dict[str, list[str]] = defaultdict(list)
    for doc_id, hs in sets.items():
        for h in hs:
            by_hash[h].append(doc_id)
    shared: Counter[tuple[str, str]] = Counter()
    for members in by_hash.values():
        shared.update(combinations(sorted(members), 2))

    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs: list[PairScore] = []
    for (a, b), n in shared.items():
        if n < 2:  # one shared hash is noise
            continue
        j = jaccard(sets[a], sets[b])
        if j >= threshold:
            pairs.append(PairScore(a=a, b=b, jaccard=round(j, 4)))
            parent[find(a)] = find(b)

    groups: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        member_set = set(members)
        clusters.append(
            DedupeCluster(
                canonical=max(members, key=lengths.get),  # first longest: members are sorted
                members=members,
                pairs=[p for p in pairs if p.a in member_set],
            )
        )
    clusters.sort(key=lambda c: (-len(c.members), c.members[0]))
    return DedupeResponse(documents=len(docs), clusters=clusters)


def drift(approved: list[TextDoc], text: str, threshold: float) -> DriftResponse:
    ids = [c.id for c in approved]
    if len(set(ids)) != len(ids):
        raise AppError(422, "DUPLICATE_ID", "approved clause ids must be unique")
    clauses = {}
    for c in approved:
        toks = tokenize(c.text)
        fps = winnow(toks)
        clauses[c.id] = (toks, fps, {fp.hash for fp in fps})

    doc_tokens = tokenize(text)
    sections = []
    for m in _PARAGRAPH.finditer(text):
        toks = [t for t in doc_tokens if m.start() <= t.start and t.end <= m.end()]
        fps = winnow(toks)
        q_set = {fp.hash for fp in fps}
        if not q_set:  # shorter than one k-gram: nothing to compare
            continue
        best_id, best_c = None, 0.0
        for cid, (_, _, c_set) in clauses.items():
            c = containment(q_set, c_set)
            if c > best_c:
                best_id, best_c = cid, c
        if best_c >= UNCHANGED:
            status = "unchanged"
        elif best_c >= threshold:
            status = "drifted"
        else:
            status = "new"
        passages = []
        if status == "drifted":
            c_toks, c_fps, _ = clauses[best_id]
            passages = [
                PassageMatch(
                    input=_span(text, toks, p.q_start, min(p.q_end, len(toks))),
                    source=_span(
                        approved[ids.index(best_id)].text,
                        c_toks,
                        p.d_start,
                        min(p.d_end, len(c_toks)),
                    ),
                    words=p.words,
                )
                for p in align(fps, c_fps)
            ]
        sections.append(
            DriftSection(
                start=m.start(),
                end=m.end(),
                status=status,
                clause_id=best_id if status != "new" else None,
                containment=round(best_c, 4),
                drift=round(1 - best_c, 4),
                passages=passages,
            )
        )
    return DriftResponse(sections=sections)
