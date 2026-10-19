"""Wikitext -> plain-text lead section, plus the filters that decide whether a page is indexed."""

from __future__ import annotations

import re

import mwparserfromhell

MAX_CHARS = 5_000
MIN_WORDS = 20

_FIRST_HEADING = re.compile(r"^={2,}[^=\n].*?={2,}[ \t]*$", re.MULTILINE)
_DISAMBIG = re.compile(
    r"\{\{\s*(?:disambig\w*|dab|hndis|geodis|schooldis|numberdis|set index(?: article)?)\b",
    re.IGNORECASE,
)
_DROP_LINK_PREFIX = ("file:", "image:", "category:", "media:")
_QUOTES = re.compile(r"'{2,}")
_SPACE = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def is_disambiguation(title: str, wikitext: str) -> bool:
    return title.endswith("(disambiguation)") or bool(_DISAMBIG.search(wikitext))


def lead_wikitext(wikitext: str) -> str:
    """Everything before the first `== Heading ==`."""
    m = _FIRST_HEADING.search(wikitext)
    return wikitext[: m.start()] if m else wikitext


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(" ")] if " " in cut[limit // 2 :] else cut


def tidy(text: str) -> str:
    text = _QUOTES.sub("", text)
    text = _SPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


def clean_lead(wikitext: str) -> str:
    code = mwparserfromhell.parse(lead_wikitext(wikitext))
    # Top-level templates are infoboxes, hatnotes, citations: none of it is prose.
    for node in code.filter_templates(recursive=False):
        code.remove(node)
    for link in code.filter_wikilinks():
        if str(link.title).strip().lower().startswith(_DROP_LINK_PREFIX):
            try:
                code.remove(link)
            except ValueError:  # already removed with a parent node
                pass
    for tag in code.filter_tags():
        if str(tag.tag).strip().lower() in {"ref", "gallery", "table", "math", "timeline"}:
            try:
                code.remove(tag)
            except ValueError:
                pass
    text = code.strip_code(normalize=True, collapse=True)
    return truncate(tidy(text))


def lead_from_plain(text: str, min_chars: int = 1_000, max_paragraphs: int = 3) -> str:
    """Lead approximation for already-clean articles (no headings markup, e.g. HF parquet).

    Paragraphs are taken until `min_chars` is reached or `max_paragraphs` is hit.
    """
    out, size = [], 0
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        out.append(para)
        size += len(para)
        if size >= min_chars or len(out) >= max_paragraphs:
            break
    return truncate("\n\n".join(out))


def word_count(text: str) -> int:
    return len(text.split())
