"""Turn a dump `Page` into an index document (or a skip reason). Runs inside worker processes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import quote

from app.winnowing import fingerprint_hashes
from ingest import clean
from ingest.dump_reader import Page


@dataclass(frozen=True)
class Config:
    source: str  # "xml" | "parquet"
    lang: str  # "simple" | "en"
    heldout_frac: float = 0.0


def is_heldout(page_id: int, frac: float) -> bool:
    """Deterministic hash-based split so a re-run always holds out the same pages."""
    if frac <= 0:
        return False
    h = int.from_bytes(hashlib.blake2b(str(page_id).encode(), digest_size=4).digest(), "big")
    return h % 100_000 < frac * 100_000


def text_key(text: str) -> bytes:
    return hashlib.blake2b(text.encode(), digest_size=8).digest()


def make_doc(args: tuple[Page, Config]) -> tuple[str, dict | None]:
    """Returns (status, doc). status is "ok", "heldout" or a skip reason."""
    page, cfg = args
    if page.ns != 0:
        return "skip_namespace", None
    if page.redirect is not None:
        return "skip_redirect", None
    if cfg.source == "xml":
        if clean.is_disambiguation(page.title, page.text):
            return "skip_disambiguation", None
        text = clean.clean_lead(page.text)
    else:
        if page.title.endswith("(disambiguation)"):
            return "skip_disambiguation", None
        text = clean.lead_from_plain(page.text)
    if clean.word_count(text) < clean.MIN_WORDS:
        return "skip_short", None

    url = page.url or f"https://{cfg.lang}.wikipedia.org/wiki/{quote(page.title.replace(' ', '_'))}"
    doc = {
        "page_id": page.page_id,
        "title": page.title,
        "text": text,
        "url": url,
    }
    if is_heldout(page.page_id, cfg.heldout_frac):
        return "heldout", doc  # written to heldout.jsonl, never indexed
    doc["fingerprints"] = fingerprint_hashes(text)
    return "ok", doc
