# 4. Custom threaded bulk sender instead of `helpers.parallel_bulk`

Status: accepted

`parallel_bulk` cannot retry per-item 429 rejections and completes out of order, so a safe resume point
is not derivable. `ingest/bulk.py` sends the same `_bulk` requests from a thread pool, retries 429 and
transient failures with jittered exponential backoff, dead-letters permanent failures, and advances the
checkpoint only over a contiguous prefix of acknowledged chunks. Writes are idempotent (`_id` = page id).
