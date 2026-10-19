"""Known-item queries from redirects: alias title -> target article id (free ground truth)."""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Query:
    q: str
    target_id: int
    split: str  # "dev" | "test"


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def split_of(target_id: int) -> str:
    """Split by TARGET so aliases of one article never straddle dev and test."""
    h = int.from_bytes(hashlib.blake2b(str(target_id).encode(), digest_size=2).digest(), "big")
    return "dev" if h % 2 == 0 else "test"


def build(
    redirects_jsonl: Path, n: int = 5_000, seed: int = 42, per_target: int = 2
) -> list[Query]:
    rng = random.Random(seed)
    by_target: dict[int, list[str]] = defaultdict(list)
    for line in redirects_jsonl.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        alias, target = r["alias"], r["target_title"]
        if "#" in alias or normalise(alias) == normalise(target) or len(normalise(alias)) < 2:
            continue  # section redirects and case/punctuation-only variants are trivial
        by_target[r["target_id"]].append(alias)

    pool: list[tuple[str, int]] = []
    for tid in sorted(by_target):
        aliases = sorted(set(by_target[tid]))
        rng.shuffle(aliases)
        pool += [(a, tid) for a in aliases[:per_target]]  # cap: popular pages have hundreds
    rng.shuffle(pool)
    return [Query(a, tid, split_of(tid)) for a, tid in pool[:n]]


def save(queries: list[Query], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for q in queries:
            fh.write(
                json.dumps(
                    {"q": q.q, "target_id": q.target_id, "split": q.split}, ensure_ascii=False
                )
                + "\n"
            )


def load(path: Path) -> list[Query]:
    return [Query(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
