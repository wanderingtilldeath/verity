# Features & Architecture Ledger

Living ledger of subsystems in this repo (`wikisearch-es`: full-text and plagiarism search on Elasticsearch). Maintained with `/onboard` (seed) and `/ledger-update` (sync). Terms follow `GLOSSARY.md`; decisions are in `docs/adr/`.

### HTTP API & Request Lifecycle
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/main.py`: FastAPI app factory, routes (`/search`, `/suggest`, `/related/{doc_id}`, `/plagiarism/check`, `/healthz`, `/metrics`, `/`), request-context middleware, lifespan
  - `app/deps.py`: `ES` and `SETTINGS` dependencies
  - `app/schemas.py`: request/response models
  - `app/static/index.html`: single-page UI served at `/`
- **Implementation Summary**: One `AsyncElasticsearch` client is created in the lifespan hook and shared by every request through `Depends`. A middleware assigns or propagates `x-request-id`, records Prometheus request count and latency, and writes one JSON access-log line per request. Request bodies are never logged because plagiarism input is user text. Query-parameter validation (length, size, from) happens in FastAPI before any business logic runs.
- **Data & Schema Dependencies**: Elasticsearch alias `articles`; no other store.
- **Production Trade-Offs**: Stateless, so N instances can sit behind a load balancer; the only shared state is Elasticsearch. No auth and no rate limiting in the app: both must come from a gateway. `/healthz` returns 503 rather than raising when ES is down.

### Full-Text Search (`/search`)
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/main.py`: `search_route`
  - `app/search.py`: `build_query`, `search`, `run_search`, cursor helpers, `HIGHLIGHT`
  - `es/articles_v1.json`: analyzers and field mapping
- **Implementation Summary**: `build_query` is a bool-should of `multi_match` over `title^4` and `text`, a `match_phrase` on title (boost 6), and a `term` on `title.exact` (boost 10). Responses carry highlight snippets (HTML-encoded, `<mark>` tags), score, and an exact/lower-bound total flag. `run_search` is the single choke point that sets the client request timeout on every ES call. `explain` is gated by `ALLOW_EXPLAIN`.
- **Data & Schema Dependencies**: `articles` alias; fields `title`, `title.exact`, `text`, `url`, `page_id`.
- **Production Trade-Offs**: `track_total_hits` is capped at `max_result_window` (10,000), so totals past that are a lower bound. Query builders are plain functions so the relevance eval reuses the exact production query. Highlighting adds cost per request; it can be turned off with `highlight=false`.

### Deep Pagination (PIT + `search_after`)
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/search.py`: `encode_cursor`, `decode_cursor`, PIT branch in `search`
  - `app/errors.py`: `AppError` codes `BAD_CURSOR`, `CURSOR_QUERY_MISMATCH`, `CURSOR_WITH_FROM`, `DEEP_PAGING_REQUIRES_CURSOR`
- **Implementation Summary**: `from`+`size` is allowed up to 10,000. Beyond that the client sends `cursor=start`, which opens a point-in-time, then follows the opaque `next` token (base64 JSON of PIT id, last sort values, and the query). Sorting is `_score desc, _shard_doc asc` for a stable total order. The PIT is closed on the last page.
- **Data & Schema Dependencies**: Elasticsearch PIT on the `articles` alias.
- **Production Trade-Offs**: The cursor is not signed, so a client can alter `search_after` values; it only reaches the same public index, but it is not tamper-proof. A client that abandons a cursor holds the PIT until `pit_keep_alive` (1m) expires. A cursor is bound to its query string.

### Suggest & Did-You-Mean (`/suggest`)
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/main.py`: `suggest_route`
  - `app/search.py`: `suggest`
  - `es/articles_v1.json`: `title.suggest` (`search_as_you_type`, 3-gram shingles)
- **Implementation Summary**: One search call runs a `bool_prefix` multi_match over the `title.suggest` subfields for completions, and a phrase suggester on the un-stemmed 2-gram subfield for did-you-mean (max 2 errors). The suggestion is dropped when it equals the input ignoring case.
- **Data & Schema Dependencies**: `title.suggest` subfields on `articles`.
- **Production Trade-Offs**: Titles only, so completions never come from body text. Fires on every keystroke in a typical UI, so it needs client debouncing; the 128-char query cap bounds cost.

### Related Articles (`/related/{doc_id}`)
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/main.py`: `related_route`
  - `app/search.py`: `related`
- **Implementation Summary**: A cheap `get` first confirms the Article exists (404 `NOT_FOUND` otherwise). Then a `more_like_this` query uses the stored document as the `like` source over `title` and `text`, with `min_doc_freq` 3, up to 25 query terms, and 30% minimum should match.
- **Data & Schema Dependencies**: `articles` alias; reads the source document's `text` at query time.
- **Production Trade-Offs**: Two ES round trips per call. `more_like_this` quality depends on term statistics, which are stable because the index is a single shard (no per-shard IDF skew).

### Plagiarism Detection (`/plagiarism/check`)
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/main.py`: `plagiarism_route`
  - `app/plagiarism.py`: `check`, `candidate_query`, `rerank`
  - `app/winnowing.py`: `tokenize`, `winnow`, `containment`, `jaccard`, `align`
  - `app/schemas.py`: `PlagiarismRequest`, `PlagiarismResponse`, `PassageMatch`
- **Implementation Summary**: The input is tokenized and winnowed into fingerprint hashes. A `terms_set` query with a script (`min(num_terms, max(2, ceil(num_terms * min_frac)))`) fetches up to 50 candidates sharing enough hashes. Python then recomputes winnowing on each candidate's stored `text`, scores containment (primary) and Jaccard, filters by threshold, and aligns shared fingerprints into passages with character spans on both sides. Winnowing and re-ranking run in `asyncio.to_thread` so they do not block the event loop.
- **Data & Schema Dependencies**: `fingerprints` keyword field and `text` on `articles`; `_meta` winnowing params on the index mapping.
- **Production Trade-Offs**: Guaranteed detection of any shared run of at least 8 words (k=5, w=4), but only against lead text (ADR 0001). Input capped at 50,000 chars (413) and at least 8 words (422). Re-rank is CPU-bound Python on up to 50 documents per request, so it is the first thing to saturate under load; the thread pool is the scaling limit, not Elasticsearch.

### Winnowing Fingerprint Library
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/winnowing.py`: pure functions shared by ingest and API
  - `tests/test_winnowing.py`: unit and property tests (Hypothesis)
  - `docs/adr/0006-stdlib-hash-never-builtin.md`
- **Implementation Summary**: Words are NFKC-normalized and lowercased, grouped into 5-grams, hashed with 64-bit blake2b, and the minimum (rightmost on ties) of each 4-gram window is kept. The module has no Elasticsearch dependency so indexing and querying cannot drift. Parameters (`K`, `W`, `HASH_BITS`) are constants.
- **Data & Schema Dependencies**: Writes the `fingerprints` field at ingest; params stored in index `_meta`.
- **Production Trade-Offs**: Builtin `hash()` is salted per process and must never be used. Changing `K`/`W` requires a reindex; `/healthz` returns 503 when code and index `_meta` disagree. Stopwords are not removed before winnowing because that would break the run-length guarantee.

### Index Definition & Zero-Downtime Reindex
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `es/articles_v1.json`: production mapping and analysis settings
  - `es/articles_exp.json`: evaluation-only mapping
  - `es/README.md`: design rationale
  - `ingest/index.py`: `create_index`, `ensure_alias`, `bulk_mode`, `force_merge`, `reindex_and_swap`
- **Implementation Summary**: One index behind the alias `articles`, `dynamic: strict`, 1 shard, 0 replicas. The `wiki_text` analyzer applies possessive stemmer, lowercase, stop, asciifolding (keeping the original), then a light English stemmer. `fingerprints` is excluded from `_source` and has no doc values. Reindex builds a new index, scans the old one, recomputes fingerprints from `text`, and swaps the alias atomically.
- **Data & Schema Dependencies**: Indexes `articles_v1`, `articles_v2`, `articles_exp`; alias `articles`.
- **Production Trade-Offs**: A single shard and no replicas (ADR 0005) means no high availability and a ceiling of one node's capacity; fine locally, not for production. `_reindex` cannot be used (it would drop fingerprints). Writes to the old index during a copy are not carried over.

### Ingest Pipeline (`python -m ingest`)
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `ingest/cli.py`: `run`, `create-index`, `reindex`, `redirects`, `manifest` commands
  - `ingest/dump_reader.py`: streaming XML (bz2) and Parquet readers
  - `ingest/clean.py`: wikitext to plain-text lead, filters
  - `ingest/docs.py`: `make_doc`, hash-based held-out split
  - `ingest/bulk.py`: `index_stream`, `chunk_stream`, `send_chunk`, `Checkpoint`, `DeadLetter`
  - `ingest/redirects.py`: redirect extraction for the relevance eval
- **Implementation Summary**: Pages are streamed in constant memory, worker processes turn each into a document (skip non-main namespace, redirects, disambiguations, under 20 words), clean the lead to at most 5,000 chars, and compute fingerprints. Held-out pages (hash-selected, deterministic) go to a side file and are never indexed. Documents are grouped into size-bounded chunks and sent by a thread pool with a bounded in-flight queue for backpressure.
- **Data & Schema Dependencies**: `articles` index; local checkpoint file; dead-letter JSONL; `eval/results/corpus_manifest.json`.
- **Production Trade-Offs**: Writes are idempotent (`_id` is the page id). A 429 or transient failure retries with exponential backoff and jitter (max 8 attempts), then goes to the dead letter; the run aborts above a 0.5% dead-letter rate. The checkpoint advances only over a contiguous prefix of acknowledged chunks, so resume re-sends at most a few chunks. A custom bulk is used instead of `parallel_bulk` (ADR 0004). Ingest is not run in a container or on a schedule; it is an operator-run batch.

### Observability, Errors & Config
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `app/metrics.py`: Prometheus registry (`http_requests_total`, `http_request_seconds`, `es_took_ms`, `es_errors_total`, `plagiarism_candidates`)
  - `app/errors.py`: `AppError` and ES-to-HTTP mapping
  - `app/config.py`: `Settings` (env / `.env`), `.env.example`
- **Implementation Summary**: Every error uses one JSON shape (`code`, `message`, `request_id`). ES timeouts map to 504, connection failures to 503, rejected queries to 400, other ES errors to 502. Every ES call carries both a client request timeout (10s) and a server-side `timeout` (5s). Settings come from environment variables with typed defaults.
- **Data & Schema Dependencies**: None beyond `/metrics` scraping.
- **Production Trade-Offs**: Timeouts and error mapping exist, but there are no retries, circuit breaker, or app-level rate limiter on the query path: a slow ES makes requests wait up to the timeout and then fail with 504. Metrics are per-process, so a multi-instance deployment relies on the scraper to aggregate.

### Relevance Evaluation Harness
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `eval/relevance/run.py`: runs configs via `msearch`
  - `eval/relevance/configs.py`: five query configurations
  - `eval/relevance/queries.py`, `eval/relevance/metrics.py`: known-item queries and MRR/hit/nDCG
  - `eval/relevance/build_exp_index.py`: builds BM25 k1/b subfields on `articles_exp`
  - `eval/report.py`: regenerates `docs/results.md`
- **Implementation Summary**: Redirect titles are used as queries whose only relevant document is the redirect target. Configs compare the standard analyzer, the English analyzer, the production query, tuned BM25, and title-only. k1/b is chosen on the DEV split and every reported number is on TEST.
- **Data & Schema Dependencies**: `articles_exp` index; `redirects_*.jsonl`; `eval/results/`.
- **Production Trade-Offs**: Known-item queries measure title-like lookup, not informational relevance. The experiment index is never served by the API. Results are regenerated, never hand-edited.

### Plagiarism Evaluation & Load Benchmarks
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `eval/plagiarism/generate.py`, `run.py`, `metrics.py`, `ablation.py`: synthetic cases (exact, light edit, synonym, reorder, mixed, negatives), PR curves
  - `bench/run.sh`, `bench/search.js`, `bench/plagiarism.js`, `bench/build_queries.py`, `bench/record_env.py`: k6 latency benchmarks
- **Implementation Summary**: Test cases are derived from indexed Articles and from held-out pages (negatives) with a fixed seed, then run through `app.plagiarism.check` against the live index to produce precision/recall and plots. The k6 scripts measure cold and warm latency at 1, 10, and 50 concurrent users for the 250k and 1.2M document indexes; summaries are committed and raw point streams are not.
- **Data & Schema Dependencies**: Live `articles` index; `~/data/wikisearch`; `eval/results/`, `bench/results/`.
- **Production Trade-Offs**: Cold-cache runs on macOS/Docker Desktop are an approximation because the host page cache is hidden. Both are heavy and are not run in CI.

### CI & Tests
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `.github/workflows/ci.yml`: `lint`, `unit`, `integration` jobs
  - `tests/`: unit tests and `es`-marked integration tests
  - `Makefile`: `test-unit`, `test`, `lint`, `up`, `serve`
  - `docker-compose.yml`: local Elasticsearch 8.19.0
- **Implementation Summary**: Lint runs ruff check and format check. Unit tests run with `-m "not es"` and need no Elasticsearch. The integration job starts an ES 8.19.0 service container and runs `pytest -m es`, covering ingest, alias swap, analyzers, API, and the end-to-end plagiarism fixture corpus.
- **Data & Schema Dependencies**: ES service container on port 9200.
- **Production Trade-Offs**: CI runs on every push and pull request. There is no deploy step and no Dockerfile for the app itself. Local ES disables the disk watermark so a full disk does not flip the index read-only; that must not be copied to production.

### Agent Commands & Feature Ledger
- **Status**: Active
- **Last Updated**: 2026-10-21 (156f566)
- **Entry Points / Key Files**:
  - `CLAUDE.md`: project instructions linking the commands and the ledger
  - `.claude/commands/onboard.md`: `/onboard` codebase mapping and ledger seed
  - `.claude/commands/ledger-update.md`: `/ledger-update` ledger sync from git changes
  - `FEATURES.md`: this file
- **Implementation Summary**: Two project slash commands and a `CLAUDE.md` that tells the agent to run `/ledger-update` after behavior-affecting changes. `/ledger-update` reads `git status` and `git diff`, matches changed files to entries here, and edits them in place (`Modified`), appends new entries (`Active`), or marks removed code `Deprecated`.
- **Data & Schema Dependencies**: None; plain Markdown files and git.
- **Production Trade-Offs**: The ledger is only as current as the last time the command ran; nothing enforces it in CI. Historical entries are never deleted.
