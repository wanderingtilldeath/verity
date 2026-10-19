"""Bulk pipeline behaviour against a scripted fake client (no Elasticsearch needed)."""

import json
import threading

from elasticsearch import ApiError, ConnectionError

from ingest.bulk import Checkpoint, DeadLetter, Stats, chunk_stream, index_stream, send_chunk


def docs(n, size=100):
    return [{"page_id": i, "title": f"t{i}", "text": "x" * size} for i in range(1, n + 1)]


def stream(ds):
    for i, d in enumerate(ds, start=1):
        yield i, i, d


class FakeClient:
    """Each doc id fails with the scripted statuses (in order) before succeeding."""

    def __init__(self, script=None, raise_first=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.raise_first = raise_first
        self.calls = 0
        self.indexed: dict[str, dict] = {}
        self.lock = threading.Lock()

    def options(self, **_):
        return self

    def bulk(self, operations, refresh=False):
        with self.lock:
            self.calls += 1
            if self.raise_first is not None:
                exc, self.raise_first = self.raise_first, None
                raise exc
            items = []
            for action, doc in zip(operations[::2], operations[1::2], strict=True):
                _id = action["index"]["_id"]
                statuses = self.script.get(_id)
                status = statuses.pop(0) if statuses else 201
                if status < 300:
                    self.indexed[_id] = doc
                items.append(
                    {
                        "index": {
                            "_id": _id,
                            "status": status,
                            "error": {"type": "boom"} if status >= 400 else None,
                        }
                    }
                )
            return {"items": items, "errors": any(i["index"]["status"] >= 400 for i in items)}


def test_chunks_respect_byte_and_doc_limits():
    chunks = list(chunk_stream(stream(docs(100, 1000)), max_bytes=20_000, max_docs=1000))
    assert sum(len(c.docs) for c in chunks) == 100
    assert all(c.size <= 20_000 + 1200 for c in chunks)
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert chunks[-1].records == 100 and chunks[-1].docs_total == 100
    capped = list(chunk_stream(stream(docs(10)), max_bytes=10**9, max_docs=3))
    assert [len(c.docs) for c in capped] == [3, 3, 3, 1]


def test_429_items_are_retried_with_backoff_then_succeed(tmp_path):
    client = FakeClient(script={"2": [429, 429], "3": [429]})
    sleeps = []
    stats, dead = Stats(), DeadLetter(tmp_path / "dl.jsonl")
    (chunk,) = list(chunk_stream(stream(docs(5))))
    send_chunk(client, "idx", chunk, stats, dead, sleep=sleeps.append)
    assert stats.indexed == 5 and stats.failed == 0
    assert stats.retries == 3  # doc 2 twice, doc 3 once
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)
    assert not (tmp_path / "dl.jsonl").exists()


def test_whole_request_429_and_connection_errors_are_retried(tmp_path):
    meta = type("M", (), {"status": 429})()
    for exc in (ApiError("too many", meta, {"error": "x"}), ConnectionError("down")):
        client = FakeClient(raise_first=exc)
        stats, dead = Stats(), DeadLetter(tmp_path / "dl.jsonl")
        (chunk,) = list(chunk_stream(stream(docs(3))))
        send_chunk(client, "idx", chunk, stats, dead, sleep=lambda s: None)
        assert stats.indexed == 3 and client.calls == 2


def test_permanent_failure_goes_to_dead_letter_and_does_not_block_others(tmp_path):
    client = FakeClient(script={"2": [400]})
    stats, dead = Stats(), DeadLetter(tmp_path / "dl.jsonl")
    (chunk,) = list(chunk_stream(stream(docs(4))))
    send_chunk(client, "idx", chunk, stats, dead, sleep=lambda s: None)
    assert (stats.indexed, stats.failed) == (3, 1)
    rows = [json.loads(line) for line in (tmp_path / "dl.jsonl").read_text().splitlines()]
    assert rows[0]["page_id"] == 2 and rows[0]["status"] == 400


def test_retries_exhausted_goes_to_dead_letter(tmp_path):
    client = FakeClient(script={"1": [429] * 50})
    stats, dead = Stats(), DeadLetter(tmp_path / "dl.jsonl")
    (chunk,) = list(chunk_stream(stream(docs(2))))
    send_chunk(client, "idx", chunk, stats, dead, max_attempts=3, sleep=lambda s: None)
    assert stats.indexed == 1 and stats.failed == 1
    assert "retries exhausted" in (tmp_path / "dl.jsonl").read_text()


def test_idempotent_ids_same_count_when_run_twice(tmp_path):
    client = FakeClient()
    ds = docs(30)
    for _ in range(2):
        index_stream(
            client,
            "idx",
            stream(ds),
            None,
            DeadLetter(tmp_path / "dl.jsonl"),
            threads=3,
            max_chunk_bytes=2000,
        )
    assert len(client.indexed) == 30


def test_checkpoint_only_advances_over_contiguous_prefix(tmp_path):
    ckpt = Checkpoint(tmp_path / "ck.json")
    client = FakeClient()
    index_stream(
        client,
        "idx",
        stream(docs(40)),
        ckpt,
        DeadLetter(tmp_path / "dl.jsonl"),
        threads=4,
        max_chunk_bytes=1500,
    )
    assert ckpt.load() == {"records": 40, "docs": 40}


def test_checkpoint_resume_roundtrip(tmp_path):
    ckpt = Checkpoint(tmp_path / "ck.json")
    assert ckpt.load() == {"records": 0, "docs": 0}
    ckpt.save(120, 100)
    assert ckpt.load() == {"records": 120, "docs": 100}
    ckpt.clear()
    assert ckpt.load() == {"records": 0, "docs": 0}
