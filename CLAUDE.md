# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`verity`: full-text search and plagiarism detection over Wikipedia on Elasticsearch 8.x, FastAPI, Python 3.12 (uv). Every figure quoted anywhere must come from `eval/results/` or `bench/results/` via `make report` → `docs/results.md`; the README says no measurements exist yet, so do not invent numbers. Spec: `.scratch/verity/spec.md`. Use the terms in `GLOSSARY.md` exactly (Article, Lead, Fingerprint, Candidate, Containment, Passage, Held-out page, Stage A/B...).

## Commands

```bash
uv sync
make up / make down            # Elasticsearch 8.19 via docker compose (single node, 2g heap)
make serve                     # uvicorn app.main:app on 127.0.0.1:8000 (UI at /, OpenAPI at /docs)
make test-unit                 # uv run pytest -m "not es"  (no ES needed)
make test                      # all tests; `es`-marked tests are skipped, not failed, if ES_URL doesn't answer
uv run pytest tests/test_winnowing.py::test_name   # single test
make lint                      # ruff check + ruff format --check (line length 100; E501 ignored)
make ingest-a                  # Stage A (Simple English dump) -> index
make eval-relevance | eval-plagiarism | bench | report
```

CI (`.github/workflows/ci.yml`) runs lint, `pytest -m "not es"`, and `pytest -m es` against an ES service container. Config comes from env (`.env.example`: `ES_URL`, `ES_ALIAS`, `SEARCH_TIMEOUT_S`, `PLAGIARISM_THRESHOLD`, `ALLOW_EXPLAIN`).

## Architecture

Three packages share the same ES index and the same winnowing code:

- `ingest/`: dump (xml.bz2 or parquet) → parse/clean Lead (process pool) → winnow → size-bounded bulk chunks (thread pool, 429 retry, dead letter, checkpoint/resume). Custom bulk instead of `parallel_bulk` (ADR 0004). `refresh_interval=-1` during loads, restored in a `finally`. `ingest/index.py` also owns alias management and `reindex_and_swap`.
- `app/`: FastAPI over `AsyncElasticsearch`. `search.py` builds queries, `plagiarism.py` does `terms_set` candidate fetch → Python re-rank by containment/Jaccard → passage alignment, `winnowing.py` is the shared fingerprinting core, `errors.py` gives one error shape `{code, message, request_id}` (ES down → 503, timeout → 504), `/healthz` fails if the index's winnowing params (in mapping `_meta`) differ from the code's.
- `eval/` and `bench/`: relevance eval (redirect titles as known-item queries; configs 1–4 incl. BM25 k1/b grid on a dev split, reported on test split) against the evaluation-only `articles_exp` index; plagiarism eval (seeded synthetic transformations + negatives from Held-out pages); k6 benchmarks.

Key invariants:
- Index is always reached through alias `articles` → `articles_v1`; mapping is `dynamic: strict`, 1 shard / 0 replicas (reproducible BM25 stats). Mapping files in `es/`.
- `fingerprints` is excluded from `_source`, so `_reindex` would lose it; it is **recomputed from `text`** (ADR 0003). Changing `k`/`w` or the hash requires reindex.
- Winnowing: word 5-grams, window 4, guarantees detection of shared runs ≥ 8 words (property-tested with Hypothesis). Hash must be a stdlib/deterministic hash, never builtin `hash()` (ADR 0006).
- Only the Lead (≤5,000 chars) is indexed (ADR 0001); Stage A and B share one index for the 250k/1.2M comparison (ADR 0002). Held-out pages are hash-selected at ingest and never indexed.
- Highlights are HTML-escaped with only `<mark>` allowed.

Decisions are recorded in `docs/adr/`; index design notes in `es/README.md`.

## Code index

`docs/INDEX.md` maps every module/feature to its entry points, schemas and tests. Read it first when locating code or the test covering a behaviour; update it when adding routes, CLI subcommands or modules.
