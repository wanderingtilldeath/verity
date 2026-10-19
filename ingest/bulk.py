"""Bulk loading: size-bounded chunks, parallel senders, 429 retry with backoff, dead letter, checkpoint.

Why not `helpers.parallel_bulk`: it cannot retry 429 rejections, and its out-of-order completion makes
a safe resume point impossible. This sends the same `_bulk` requests from a thread pool and advances
the checkpoint only over a *contiguous* prefix of acknowledged chunks. Writes are idempotent
(`_id` = page id), so a crash between ack and checkpoint only re-sends some documents.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from elasticsearch import ApiError, ConnectionError, ConnectionTimeout, Elasticsearch

log = logging.getLogger("ingest.bulk")

RETRYABLE_STATUS = {429, 502, 503, 504}


@dataclass
class Stats:
    indexed: int = 0
    failed: int = 0
    retries: int = 0  # documents re-sent after a 429 / transient failure
    chunks: int = 0
    bytes_sent: int = 0
    es_seconds: float = 0.0  # summed request time across threads
    started: float = field(default_factory=time.perf_counter)

    def as_dict(self, threads: int, chunk_bytes: int) -> dict:
        wall = time.perf_counter() - self.started
        return {
            "indexed": self.indexed,
            "failed": self.failed,
            "retries_429": self.retries,
            "chunks": self.chunks,
            "mb_sent": round(self.bytes_sent / 1e6, 1),
            "wall_seconds": round(wall, 1),
            "docs_per_sec_end_to_end": round(self.indexed / wall, 1) if wall else 0,
            "docs_per_sec_es_only": (
                round(self.indexed * threads / self.es_seconds, 1) if self.es_seconds else 0
            ),
            "threads": threads,
            "max_chunk_bytes": chunk_bytes,
        }


@dataclass
class Chunk:
    seq: int
    docs: list[dict]
    records: int  # source records consumed once this chunk is complete
    docs_total: int  # indexable docs emitted so far, across runs
    size: int


class Checkpoint:
    """{"records": n, "docs": n}. Written atomically; only ever moves forward."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {"records": 0, "docs": 0}
        return json.loads(self.path.read_text())

    def save(self, records: int, docs: int) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"records": records, "docs": docs}))
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class DeadLetter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.count = 0

    def write(self, doc: dict, reason: str, status: int | None = None) -> None:
        line = json.dumps(
            {
                "page_id": doc.get("page_id"),
                "title": doc.get("title"),
                "status": status,
                "reason": reason,
            },
            ensure_ascii=False,
        )
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            self.count += 1


def chunk_stream(
    stream: Iterable[tuple[int, int, dict]],
    max_bytes: int = 4_000_000,
    max_docs: int = 2_000,
) -> Iterator[Chunk]:
    """Group (records, docs_total, doc) triples into chunks of at most `max_bytes` serialized JSON."""
    seq, buf, size, records, docs_total = 0, [], 0, 0, 0
    prev_records = prev_docs = 0  # position after the last doc already in `buf`
    for records, docs_total, doc in stream:
        doc_size = len(json.dumps(doc, ensure_ascii=False).encode()) + 80  # + action line
        if buf and (size + doc_size > max_bytes or len(buf) >= max_docs):
            yield Chunk(seq, buf, prev_records, prev_docs, size)
            seq, buf, size = seq + 1, [], 0
        buf.append(doc)
        size += doc_size
        prev_records, prev_docs = records, docs_total
    if buf:
        yield Chunk(seq, buf, records, docs_total, size)


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    return min(cap, base * 2**attempt) * random.uniform(0.5, 1.0)  # jitter


def send_chunk(
    client: Elasticsearch,
    index: str,
    chunk: Chunk,
    stats: Stats,
    dead: DeadLetter,
    max_attempts: int = 8,
    sleep: Callable[[float], None] = time.sleep,
    lock: threading.Lock | None = None,
) -> None:
    lock = lock or threading.Lock()
    pending = chunk.docs
    attempt = 0
    while pending:
        ops: list[dict] = []
        for d in pending:
            ops.append({"index": {"_index": index, "_id": str(d["page_id"])}})
            ops.append(d)
        retry: list[dict] = []
        t0 = time.perf_counter()
        try:
            resp = client.options(request_timeout=120).bulk(operations=ops, refresh=False)
        except ApiError as exc:
            if exc.status_code not in RETRYABLE_STATUS:
                for d in pending:
                    dead.write(d, f"bulk request rejected: {exc.message}", exc.status_code)
                with lock:
                    stats.failed += len(pending)
                return
            resp = None
        except (ConnectionError, ConnectionTimeout):
            resp = None
        elapsed = time.perf_counter() - t0

        ok = 0
        if resp is None:
            retry = pending
        else:
            for d, item in zip(pending, resp["items"], strict=True):
                r = item.get("index") or item.get("create") or {}
                status = r.get("status", 500)
                if status < 300:
                    ok += 1
                elif status in RETRYABLE_STATUS:
                    retry.append(d)
                else:
                    dead.write(d, json.dumps(r.get("error"), ensure_ascii=False)[:500], status)
                    with lock:
                        stats.failed += 1
        with lock:
            stats.indexed += ok
            stats.es_seconds += elapsed
            stats.bytes_sent += chunk.size if not retry else 0

        if not retry:
            return
        attempt += 1
        with lock:
            stats.retries += len(retry)
        if attempt >= max_attempts:
            for d in retry:
                dead.write(d, "retries exhausted", 429)
            with lock:
                stats.failed += len(retry)
            return
        delay = backoff_delay(attempt)
        log.warning("retrying %d docs after backoff %.1fs (attempt %d)", len(retry), delay, attempt)
        sleep(delay)
        pending = retry


def index_stream(
    client: Elasticsearch,
    index: str,
    stream: Iterable[tuple[int, int, dict]],
    checkpoint: Checkpoint | None,
    dead: DeadLetter,
    threads: int = 4,
    max_chunk_bytes: int = 4_000_000,
    sleep: Callable[[float], None] = time.sleep,
) -> Stats:
    stats = Stats()
    lock = threading.Lock()
    inflight: dict[Future, Chunk] = {}
    finished: dict[int, Chunk] = {}
    next_seq = 0

    def advance(done: Chunk) -> None:
        nonlocal next_seq
        finished[done.seq] = done
        while next_seq in finished:
            c = finished.pop(next_seq)
            if checkpoint:
                checkpoint.save(c.records, c.docs_total)
            next_seq += 1

    def drain(block_until: int) -> None:
        while len(inflight) > block_until:
            done, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in done:
                chunk = inflight.pop(fut)
                fut.result()  # surface unexpected exceptions; per-doc failures are in dead letter
                stats.chunks += 1
                advance(chunk)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        for chunk in chunk_stream(stream, max_bytes=max_chunk_bytes):
            fut = pool.submit(send_chunk, client, index, chunk, stats, dead, 8, sleep, lock)
            inflight[fut] = chunk
            drain(block_until=threads * 2 - 1)  # bounded queue: backpressure on the parser
        drain(block_until=0)
    return stats
