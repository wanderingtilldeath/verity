"""Redirect ground truth for the relevance eval: (alias title -> target article id).

Two sources, both must match the dump date of the indexed articles:
  * `from_xml`: `<redirect title="..."/>` elements in a pages-articles XML dump (simplewiki, enwiki)
  * `from_sql`: `redirect.sql.gz` + `page.sql.gz` (much smaller than the enwiki XML)
Targets are resolved against `titles_<stage>.tsv` (written at ingest), so only redirects pointing at
an indexed article survive. Section redirects (fragment present) and cross-namespace ones are dropped.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterator
from pathlib import Path

from ingest.dump_reader import iter_xml

_SQL_STR = r"'((?:[^'\\]|\\.)*)'"
_REDIRECT_ROW = re.compile(
    rf"\((\d+),(\d+),{_SQL_STR},(NULL|'(?:[^'\\]|\\.)*'),(NULL|'(?:[^'\\]|\\.)*')\)"
)
_PAGE_ROW = re.compile(rf"\((\d+),(\d+),{_SQL_STR},(\d)")
_UNESCAPE = re.compile(r"\\(.)")


def _unescape(s: str) -> str:
    return _UNESCAPE.sub(r"\1", s).replace("_", " ")


def load_titles(path: Path) -> dict[str, int]:
    """title -> page_id from the tsv written during ingest."""
    out = {}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            pid, _, title = line.rstrip("\n").partition("\t")
            out[title] = int(pid)
    return out


def from_xml(xml_path: Path, titles: dict[str, int]) -> Iterator[dict]:
    for page in iter_xml(xml_path):
        if page.ns != 0 or not page.redirect or "#" in page.redirect:
            continue
        target = page.redirect.replace("_", " ")
        if target in titles and page.title != target:
            yield {"alias": page.title, "target_id": titles[target], "target_title": target}


def _sql_rows(path: Path, pattern: re.Pattern) -> Iterator[re.Match]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("INSERT INTO"):
                yield from pattern.finditer(line)


def from_sql(redirect_sql: Path, page_sql: Path, titles: dict[str, int]) -> Iterator[dict]:
    wanted: dict[int, str] = {}  # rd_from -> target title, only for indexed targets
    for m in _sql_rows(redirect_sql, _REDIRECT_ROW):
        rd_from, ns, title, interwiki, fragment = m.groups()
        empty = ("NULL", "''")
        if ns != "0" or interwiki not in empty or fragment not in empty:
            continue
        target = _unescape(title)
        if target in titles:
            wanted[int(rd_from)] = target
    for m in _sql_rows(page_sql, _PAGE_ROW):
        pid, ns, title, is_redirect = m.groups()
        if ns == "0" and is_redirect == "1" and int(pid) in wanted:
            alias, target = _unescape(title), wanted[int(pid)]
            if alias != target:
                yield {"alias": alias, "target_id": titles[target], "target_title": target}


def write_jsonl(rows: Iterator[dict], out: Path) -> int:
    n = 0
    with Path(out).open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n
