"""Winnowing document fingerprints (Schleimer, Wilkerson, Aiken; SIGMOD 2003, the MOSS paper).

Pipeline: tokenise into normalised words -> word k-grams -> stable 64-bit hash -> in every window of
`W` consecutive hashes keep the minimum (rightmost on ties) -> fingerprint set with word positions.

Guarantee: any run of >= K + W - 1 words shared by two texts yields at least one common
fingerprint, because such a run contains a whole window of identical k-gram hashes.

Pure functions, no Elasticsearch dependency, so ingest and the API share exactly the same code.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

K = 5  # words per k-gram
W = 4  # window size, in k-grams
HASH_BITS = 64
GUARANTEE_WORDS = K + W - 1  # shared runs at least this long are always detected

# Alphanumeric runs. Underscore is excluded so `foo_bar` is two words.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Token:
    word: str  # normalised (NFKC, lowercase)
    start: int  # char offsets in the ORIGINAL text, so the UI can highlight
    end: int


@dataclass(frozen=True, slots=True)
class Fingerprint:
    hash: str  # 16 hex chars
    pos: int  # index of the first word of the k-gram


@dataclass(frozen=True, slots=True)
class Passage:
    q_start: int  # word indices, end exclusive
    q_end: int
    d_start: int
    d_end: int

    @property
    def words(self) -> int:
        return self.q_end - self.q_start


def tokenize(text: str) -> list[Token]:
    """Normalise per token so character offsets still refer to the original text."""
    tokens = []
    for m in _WORD_RE.finditer(text):
        word = unicodedata.normalize("NFKC", m.group()).lower()
        tokens.append(Token(word, m.start(), m.end()))
    return tokens


def _hash_gram(words: list[str]) -> int:
    # blake2b, never the builtin hash(): that one is salted per process.
    digest = hashlib.blake2b(" ".join(words).encode("utf-8"), digest_size=HASH_BITS // 8).digest()
    return int.from_bytes(digest, "big")


def winnow(tokens: list[Token], k: int = K, w: int = W) -> list[Fingerprint]:
    """Select fingerprints. Fewer than `k` words gives none; fewer than `w` grams gives one window."""
    words = [t.word for t in tokens]
    n = len(words) - k + 1
    if n <= 0:
        return []
    hashes = [_hash_gram(words[i : i + k]) for i in range(n)]

    selected: list[tuple[int, int]] = []  # (pos, hash)
    last_pos = -1
    for start in range(max(1, n - w + 1)):
        window = hashes[start : start + w]
        low = min(window)
        # rightmost occurrence of the minimum (paper's tie-break)
        pos = start + len(window) - 1 - window[::-1].index(low)
        if pos != last_pos:
            selected.append((pos, low))
            last_pos = pos
    return [Fingerprint(f"{h:016x}", p) for p, h in selected]


def fingerprints(text: str, k: int = K, w: int = W) -> list[Fingerprint]:
    return winnow(tokenize(text), k, w)


def fingerprint_hashes(text: str, k: int = K, w: int = W) -> list[str]:
    """Distinct hashes, in first-seen order. This is what goes into the `fingerprints` ES field."""
    return list(dict.fromkeys(fp.hash for fp in fingerprints(text, k, w)))


def containment(query: set[str], doc: set[str]) -> float:
    """Share of the query's fingerprints that also occur in the document."""
    return len(query & doc) / len(query) if query else 0.0


def jaccard(a: set[str], b: set[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def align(
    q_fps: list[Fingerprint], d_fps: list[Fingerprint], k: int = K, gap: int = 2 * K
) -> list[Passage]:
    """Turn shared fingerprints into matching passages.

    Shared hashes are paired in query order (each query fingerprint takes the next document
    position at or after the previous match, else the first one) and merged into a passage while
    both sides advance by at most `gap` words. Sentence reordering therefore yields several
    passages; a light edit inside a copied run (which removes up to `k` k-grams) is bridged.
    """
    d_pos: dict[str, list[int]] = defaultdict(list)
    for fp in sorted(d_fps, key=lambda f: f.pos):
        d_pos[fp.hash].append(fp.pos)

    pairs: list[tuple[int, int]] = []
    last_d = -1
    for fp in sorted(q_fps, key=lambda f: f.pos):
        candidates = d_pos.get(fp.hash)
        if not candidates:
            continue
        ahead = [p for p in candidates if p >= last_d]
        d = ahead[0] if ahead else candidates[0]
        pairs.append((fp.pos, d))
        last_d = d

    passages: list[Passage] = []
    run: list[int] | None = None  # [q_first, q_last, d_first, d_last]
    for q, d in pairs:
        if run and 0 <= q - run[1] <= gap and 0 <= d - run[3] <= gap:
            run[1], run[3] = q, d
            continue
        if run:
            passages.append(Passage(run[0], run[1] + k, run[2], run[3] + k))
        run = [q, q, d, d]
    if run:
        passages.append(Passage(run[0], run[1] + k, run[2], run[3] + k))
    return passages
