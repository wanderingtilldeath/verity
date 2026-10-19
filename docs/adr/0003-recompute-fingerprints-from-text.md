# 3. Fingerprints are excluded from `_source` and recomputed from `text`

Status: accepted

`fingerprints` can be ~16 bytes x ~60 per document; storing it in `_source` for 1.2M docs adds
over a GB. It is excluded, and positions are never stored: re-ranking recomputes winnowing on each
candidate's `text`, which is deterministic. Consequence: `_reindex` would lose the field, so
`ingest.index.reindex_and_swap` does scan + bulk and recomputes it. Bonus: a reindex may change `k`/`w`.
Open item: a `long` field instead of 16-char keyword is smaller; measure before switching.
