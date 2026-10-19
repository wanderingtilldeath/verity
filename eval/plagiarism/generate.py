"""Deterministic synthetic plagiarism cases.

Positives (label = source article id(s)): exact, light_edit, synonym, reorder, mixed.
Negatives (label = none): neg_unrelated, neg_same_topic. Both come from the held-out pool written
at ingest time (pages that were never indexed), so verbatim text can never legitimately match.
"same topic" = the held-out pages whose nearest indexed neighbour (more_like_this) scores highest;
"unrelated" = the ones whose neighbour scores lowest.

Every random choice derives from `random.Random(f"{seed}-{page_id}")`, so a case does not depend on
the order or number of other cases.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from elasticsearch import Elasticsearch

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
POSITIVE_KINDS = ["exact", "light_edit", "synonym", "reorder", "mixed"]
NEGATIVE_KINDS = ["neg_unrelated", "neg_same_topic"]
MIN_SOURCE_WORDS = 150


@dataclass
class Case:
    case_id: str
    kind: str
    text: str
    labels: list[int]  # page ids that SHOULD be found; empty for negatives
    meta: dict = field(default_factory=dict)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text.replace("\n", " ")) if s.strip()]


def pick_passage(
    sentences: list[str], rng: random.Random, lo: int = 60, hi: int = 200, min_sentences: int = 3
) -> list[str] | None:
    """A random run of consecutive sentences totalling lo..hi words."""
    starts = list(range(len(sentences)))
    rng.shuffle(starts)
    for st in starts:
        out, n = [], 0
        for s in sentences[st:]:
            w = len(s.split())
            if out and n + w > hi:
                break
            out.append(s)
            n += w
            if n >= lo:
                break
        if lo <= n <= hi and len(out) >= min_sentences:
            return out
    return None


def light_edit(text: str, rng: random.Random, rate: float = 0.10) -> str:
    """~`rate` of the words touched: half deleted, half preceded by a random word from the text."""
    words = text.split()
    vocab = words[:]
    out = []
    for w in words:
        r = rng.random()
        if r < rate / 2:
            continue
        if r < rate:
            out.append(rng.choice(vocab))
        out.append(w)
    return " ".join(out)


_POS = {"N": "n", "V": "v", "J": "a", "R": "r"}


def synonym_swap(text: str, rng: random.Random, rate: float = 0.30) -> tuple[str, int]:
    """Swap ~`rate` of the content words that have a WordNet synonym (first sense, same POS).

    Inflection is not handled ("running" -> "run"), which makes this harsher than a human rewrite
    in some ways and gentler in others; the README says so.
    """
    from nltk import pos_tag
    from nltk.corpus import wordnet as wn

    tokens = text.split()
    bare = [re.sub(r"^\W+|\W+$", "", t) for t in tokens]
    tags = pos_tag(bare)
    candidates: dict[int, str] = {}
    for i, (word, tag) in enumerate(tags):
        if not word.isalpha() or len(word) < 4 or tag[0] not in _POS or tag in ("NNP", "NNPS"):
            continue
        for syn in wn.synsets(word.lower(), _POS[tag[0]])[:1]:
            names = [
                lem.name()
                for lem in syn.lemmas()
                if "_" not in lem.name() and lem.name().lower() != word.lower()
            ]
            if names:
                candidates[i] = names[0]
    chosen = rng.sample(sorted(candidates), k=round(len(candidates) * rate))
    for i in chosen:
        old, new = bare[i], candidates[i]
        new = new.capitalize() if old[0].isupper() else new
        tokens[i] = tokens[i].replace(old, new, 1)
    return " ".join(tokens), len(chosen)


def reorder(sentences: list[str], rng: random.Random) -> list[str]:
    out = sentences[:]
    for _ in range(10):
        rng.shuffle(out)
        if out != sentences:
            break
    return out


def interleave(a: list[str], b: list[str]) -> list[str]:
    out = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            out.append(a[i])
        if i < len(b):
            out.append(b[i])
    return out


def sample_sources(es: Elasticsearch, alias: str, n: int, seed: int) -> list[dict]:
    """Seeded random sample of indexed articles long enough to give 3+ sentence passages."""
    resp = es.search(
        index=alias,
        size=n * 4,
        source=["page_id", "title", "text"],
        query={
            "function_score": {
                "query": {"bool": {"must_not": [{"prefix": {"title.exact": "list of"}}]}},
                "random_score": {"seed": seed, "field": "_seq_no"},
            }
        },
    )
    out = []
    for h in resp["hits"]["hits"]:
        src = h["_source"]
        if len(src["text"].split()) >= MIN_SOURCE_WORDS:
            out.append(src)
    return out


def rank_heldout_by_topic(es: Elasticsearch, alias: str, heldout: list[dict]) -> list[dict]:
    """Annotate each held-out page with its nearest indexed neighbour, sorted by MLT score desc."""
    scored = []
    for doc in heldout:
        resp = es.search(
            index=alias,
            size=1,
            source=["page_id", "title"],
            query={
                "more_like_this": {
                    "fields": ["text"],
                    "like": doc["text"],
                    "min_term_freq": 1,
                    "min_doc_freq": 3,
                    "max_query_terms": 25,
                }
            },
        )
        hits = resp["hits"]["hits"]
        top = hits[0] if hits else None
        scored.append(
            {
                **doc,
                "_neighbour_score": top["_score"] if top else 0.0,
                "_neighbour_id": int(top["_id"]) if top else None,
                "_neighbour_title": top["_source"]["title"] if top else None,
            }
        )
    scored.sort(key=lambda d: (-d["_neighbour_score"], d["page_id"]))
    return scored


def generate(
    es: Elasticsearch, alias: str, heldout_path: Path, n: int = 500, seed: int = 42
) -> list[Case]:
    sources = sample_sources(es, alias, n, seed)
    heldout = [json.loads(line) for line in heldout_path.read_text().splitlines()]
    heldout = [d for d in heldout if len(d["text"].split()) >= 60]
    if len(heldout) < 2 * n:
        raise SystemExit(
            f"need >= {2 * n} held-out pages for negatives, found {len(heldout)}; "
            "re-run ingest with a larger --heldout-frac"
        )
    ranked = rank_heldout_by_topic(es, alias, heldout)
    same_topic, unrelated = ranked[:n], ranked[::-1][:n]

    passages = []
    for src in sources:
        rng = random.Random(f"{seed}-{src['page_id']}")
        p = pick_passage(split_sentences(src["text"]), rng)
        if p:
            passages.append((src, p))
    passages = passages[:n]
    if len(passages) < n:
        raise SystemExit(
            f"only {len(passages)} usable sources; raise the oversampling in sample_sources"
        )

    cases: list[Case] = []
    for i, (src, sents) in enumerate(passages):
        pid = src["page_id"]
        rng = random.Random(f"{seed}-{pid}")
        base = " ".join(sents)
        cases.append(Case(f"{pid}:exact", "exact", base, [pid]))
        cases.append(Case(f"{pid}:light_edit", "light_edit", light_edit(base, rng), [pid]))
        swapped, swaps = synonym_swap(base, rng)
        cases.append(Case(f"{pid}:synonym", "synonym", swapped, [pid], {"swaps": swaps}))
        cases.append(Case(f"{pid}:reorder", "reorder", " ".join(reorder(sents, rng)), [pid]))
        other_src, other_sents = passages[(i + 1) % len(passages)]
        cases.append(
            Case(
                f"{pid}:mixed",
                "mixed",
                " ".join(interleave(sents, other_sents)),
                [pid, other_src["page_id"]],
            )
        )
        u, s = unrelated[i], same_topic[i]
        cases.append(
            Case(
                f"{pid}:neg_unrelated",
                "neg_unrelated",
                " ".join(
                    pick_passage(split_sentences(u["text"]), rng, min_sentences=1)
                    or [u["text"][:1200]]
                ),
                [],
                {"heldout_id": u["page_id"], "neighbour_score": u["_neighbour_score"]},
            )
        )
        cases.append(
            Case(
                f"{pid}:neg_same_topic",
                "neg_same_topic",
                " ".join(
                    pick_passage(split_sentences(s["text"]), rng, min_sentences=1)
                    or [s["text"][:1200]]
                ),
                [],
                {
                    "heldout_id": s["page_id"],
                    "neighbour_id": s["_neighbour_id"],
                    "neighbour_title": s["_neighbour_title"],
                    "neighbour_score": s["_neighbour_score"],
                },
            )
        )
    return cases


def write_cases(cases: list[Case], path: Path, spot_check: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    if spot_check:
        same = [c for c in cases if c.kind == "neg_same_topic"][:20]
        lines = [
            "# neg_same_topic spot check (20 cases)",
            "",
            "Held-out text vs. its nearest indexed article.",
            "",
        ]
        for c in same:
            lines += [
                f"## held-out {c.meta['heldout_id']} ~ {c.meta['neighbour_title']} "
                f"(MLT score {c.meta['neighbour_score']:.1f})",
                "",
                c.text[:400],
                "",
            ]
        spot_check.write_text("\n".join(lines))


def read_cases(path: Path) -> list[Case]:
    return [Case(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
