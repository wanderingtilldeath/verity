"""Streaming readers for Wikipedia dumps. Constant memory; both yield `Page` records."""

from __future__ import annotations

import bz2
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Page:
    page_id: int
    title: str
    ns: int
    redirect: str | None  # target title if this page is a redirect
    text: str  # raw wikitext (xml) or plain article text (parquet)
    url: str | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_xml(path: str | Path) -> Iterator[Page]:
    """Parse a `pages-articles.xml(.bz2)` dump with iterparse, clearing elements as we go."""
    opener = bz2.open if str(path).endswith(".bz2") else open
    with opener(path, "rb") as fh:
        context = ET.iterparse(fh, events=("start", "end"))
        root = None
        for event, elem in context:
            if event == "start":
                if root is None:
                    root = elem
                continue
            if _local(elem.tag) != "page":
                continue
            title = ns = pid = text = redirect = None
            for child in elem:
                name = _local(child.tag)
                if name == "title":
                    title = child.text
                elif name == "ns":
                    ns = int(child.text)
                elif name == "id" and pid is None:  # first direct <id> is the page id
                    pid = int(child.text)
                elif name == "redirect":
                    redirect = child.attrib.get("title")
                elif name == "revision":
                    for rev in child:
                        if _local(rev.tag) == "text":
                            text = rev.text or ""
            if title is not None and pid is not None:
                yield Page(pid, title, ns if ns is not None else 0, redirect, text or "")
            elem.clear()
            if root is not None:
                root.clear()  # drop cleared <page> shells so memory stays flat


def iter_parquet(path: str | Path, batch_size: int = 2_000) -> Iterator[Page]:
    """Read HF `wikimedia/wikipedia` parquet shards (columns: id, url, title, text)."""
    import pyarrow.parquet as pq

    p = Path(path)
    files = sorted(p.glob("*.parquet")) if p.is_dir() else [p]
    for f in files:
        for batch in pq.ParquetFile(f).iter_batches(
            batch_size=batch_size, columns=["id", "url", "title", "text"]
        ):
            for row in batch.to_pylist():
                yield Page(int(row["id"]), row["title"], 0, None, row["text"], row.get("url"))


def iter_pages(source: str, path: str | Path) -> Iterator[Page]:
    if source == "xml":
        return iter_xml(path)
    if source == "parquet":
        return iter_parquet(path)
    raise ValueError(f"unknown source {source!r}")
