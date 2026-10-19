# wikisearch-es: Specification


## 1. Context and goals

The CDAC project (Solr + Python, 2020: full-text search plus plagiarism detection) has no public code and unverifiable numbers. This project rebuilds it on Elasticsearch and **measures** everything the resume claims: relevance, plagiarism accuracy and latency at scale.

**Success means:**
1. A working search service (search, suggest, did-you-mean, related, plagiarism check) over **>= 1.2M** Wikipedia documents.
2. Reproducible evaluation: one command per eval, fixed seeds, raw outputs saved in the repo.
3. A README whose numbers all come from saved eval output, with limits stated.
4. A resume bullet filled only from measured values (N, X, Y, Z in section 12).

**Non-goals:** Kubernetes, Kibana dashboards, multi-node clusters, embeddings/hybrid search (check licensing before RRF), auth.

## 2. Environment (checked on this machine)

| Item | State | Consequence |
|---|---|---|
| CPU/RAM | Intel x86_64, 12 cores, 16 GB | ES 2 GB heap plus OS page cache. Docker Desktop VM needs >= 6 GB. Load generator shares the machine, so latency numbers are "laptop, single node". State this in the README. |
| Disk | 263 GB free | Fine for ~5-8 GB index plus dumps (dumps are 1-25 GB; keep them outside the repo). |
| Docker | v29.1.3 installed, **daemon not running** | Start Docker Desktop and raise memory before Phase 0. |
| Python | 3.13.9 only; no `uv`, `ruff`, `k6` | Spec says 3.12. Install `uv` and pin 3.12 via `.python-version`; `brew install k6`. |
| Repo | `verity/` empty, not a git repo | `git init`, `.gitignore` (data/, `*.bz2`, checkpoints, `.venv`). |

Pins: Elasticsearch **8.19.x** (latest 8.x at implementation time; verify the tag), `elasticsearch[async]` 8.x client matching the server minor, `fastapi`, `pydantic>=2`, `pydantic-settings`, `httpx`, `pytest`, `pytest-asyncio`, `hypothesis`, `ruff`, `nltk` (WordNet), `mwparserfromhell` or equivalent, `prometheus-client`.

## 3. Repository layout

```
.
├── docker-compose.yml          # ES 8.x single node, security off, ES_JAVA_OPTS=-Xms2g -Xmx2g
├── pyproject.toml / uv.lock / .python-version
├── Makefile                    # up, down, ingest, serve, test, eval-*, bench-*
├── es/
│   ├── articles_v1.json        # settings + mappings (analysis, similarity, shards)
│   ├── articles_exp.json       # experiment mapping with extra subfields (Phase 4 only)
│   └── README.md               # why each setting
├── ingest/
│   ├── dump_reader.py          # streaming parse (XML or parquet)
│   ├── clean.py                # wikitext to plain-text lead
│   ├── bulk.py                 # parallel_bulk, retry/backoff, checkpoint, dead letter
│   ├── redirects.py            # redirect + page tables to redirects.jsonl
│   └── cli.py                  # python -m ingest ...
├── app/
│   ├── main.py  config.py  schemas.py  errors.py  deps.py
│   ├── search.py               # search, suggest, related, did-you-mean
│   ├── plagiarism.py           # normalise, winnow, candidate query, re-rank
│   ├── winnowing.py            # pure functions, no ES dependency
│   ├── metrics.py              # Prometheus
│   └── static/index.html
├── eval/
│   ├── relevance/              # queries from redirects, metrics, configs, report
│   ├── plagiarism/             # case generator, runner, PR curves
│   └── results/                # raw JSON/CSV committed
├── bench/                      # k6 scripts, query sets, raw outputs
├── tests/
├── .github/workflows/ci.yml
├── GLOSSARY.md                 # created lazily with terms below
├── docs/adr/                   # ADRs for the decisions in section 13
└── README.md
```

## 4. Domain vocabulary (seed for GLOSSARY.md)

- **Article**: one indexed Wikipedia main-namespace, non-redirect page.
- **Lead**: the text before the first section heading, cleaned to plain text.
- **Redirect query**: a redirect title used as a query; its target Article is the only relevant document (known-item search).
- **Fingerprint**: one `(hash, position)` pair selected by winnowing.
- **Candidate**: an Article returned by the `terms_set` fingerprint query before re-ranking.
- **Containment**: `|FP(query) ∩ FP(doc)| / |FP(query)|`, the primary plagiarism score.
- **Transformation**: one way of deriving a plagiarism test query from a source Article (copy, light edit, synonym swap, reorder, mixed, negative types).
- **Stage A / Stage B**: Simple English dev corpus / >= 1.2M English leads.

## 5. Phase 0: corpus and cluster (~1 day)

### 5.1 Corpus
- **Stage A**: Simple English Wikipedia (~250k articles). Source: Wikimedia dump `simplewiki-<date>-pages-articles.xml.bz2` (primary). Used for fast iteration and all dev work.
- **Stage B**: English Wikipedia, **lead sections only**, **>= 1.2M documents**. Candidate sources, in order of preference: (a) `enwiki-<date>-pages-articles` multistream dump, parse leads for a random or first-N sample of 1.2M+; (b) the HF `wikimedia/wikipedia` parquet (pre-cleaned full articles, no redirects, truncate to lead-sized text). **Verify at download time**: exact name, size, article counts, licence (CC BY-SA 4.0 / GFDL for Wikimedia dumps; check what the HF card says).
- **Snapshot consistency rule**: articles and redirects must come from the **same dated dump**. If using HF for article text, check that a redirect table dump for that date still exists on dumps.wikimedia.org (old dates get purged); otherwise use dumps for both.
- **Document shape** (same for A and B so latency compares): `id` (page id), `title`, `text` (lead, plain text, capped e.g. 5,000 chars), `url`, `ns`, `redirect_of` not stored. Filter out: non-main namespace, redirects, disambiguation pages, leads < 20 words (they pollute plagiarism eval and relevance). Dedupe exact-duplicate texts by hash.
- **Scaling method for the 250k vs 1.2M latency comparison**: ingest Stage B in a fixed order; at **250k docs** pause, `_forcemerge` to a fixed segment count, run the bench, then continue ingesting to 1.2M, force-merge again and bench again. One index, like-for-like documents, no second ingest.
- Data lives in `~/data/wikisearch/` (untracked). Record dump names, dates, sizes, sha256 in `eval/results/corpus_manifest.json`.
- README carries CC BY-SA attribution: Wikipedia contributors, licence link, "text modified (cleaned, truncated)", and a note that search results link back to the source article. The UI shows a link to each article.

### 5.2 Cluster
- `docker-compose.yml`: ES 8.19.x, `discovery.type=single-node`, `xpack.security.enabled=false`, `ES_JAVA_OPTS=-Xms2g -Xmx2g`, named volume, `ulimits.memlock`, healthcheck on `_cluster/health`, port 9200 bound to localhost only.
- Disk watermarks: set low/high/flood-stage thresholds explicitly (or `cluster.routing.allocation.disk.threshold_enabled=false` locally) so a nearly full disk does not turn the index read-only mid-run.
- **Acceptance:** `curl localhost:9200` returns the pinned version; `make up` is idempotent; Docker memory documented in README.

## 6. Phase 1: index design and ingestion (~1.5 days)

### 6.1 Index
- Concrete index `articles_v1`, alias `articles` for all reads and writes (zero-downtime reindex: build `articles_v2`, `_reindex`, atomic alias swap via `_aliases`). Document the procedure and test it.
- `number_of_shards: 1`, `number_of_replicas: 0` locally. Why: a single node cannot place replicas (they would stay unassigned and the cluster yellow); one shard avoids cross-shard score skew in BM25 (per-shard IDF) and simplifies evaluation. Note the trade-off: shard size ~ a few GB is fine; a multi-node deployment would use more shards.
- During bulk: `refresh_interval: -1`, then restore to `1s` (or `30s`), run `_refresh` and `_forcemerge`. The ingest CLI does this in a `try/finally` so a crash does not leave refresh disabled.

### 6.2 Analysis
- Custom analyzer `wiki_text`: `standard` tokenizer, filters: `lowercase`, `english_possessive_stemmer`, `stop` (`_english_`), `asciifolding` (preserve_original), `stemmer` (`english` / light). Order matters (possessive before stem; asciifolding before stop is fine), document the final order and why.
- Fields:

| Field | Type | Notes |
|---|---|---|
| `title` | `text`, `wiki_text` | `similarity` default. `index_options: offsets` not needed. |
| `title.exact` | `keyword` + lowercase normalizer | exact-title boost, redirect check |
| `title.suggest` | `search_as_you_type` | autocomplete (`max_shingle_size: 3`) |
| `text` | `text`, `wiki_text`, `index_options: offsets` | unified highlighter |
| `fingerprints` | `keyword`, norms off, excluded from `_source` | Phase 3 |
| `url`, `page_id` | `keyword` / `long`, not analysed | |

- `_source.excludes: [fingerprints]`; `dynamic: strict`.
- `es/articles_exp.json` (Phase 4 only) adds subfields on `text` and `title`: `.std` (standard analyzer), `.english` (built-in `english` analyzer), `.tuned` (custom BM25 `k1`/`b`). All four relevance configs then run against one index via different queries. Subfield similarity is per field, so this avoids rebuilding the index per config. The production mapping stays lean.

### 6.3 Ingestion (`ingest/`)
- **Streaming parse**: iterative XML (`iterparse`, clearing elements) or parquet row groups; constant memory. Wikitext cleaned to the lead. Cleaning is the slowest step; run in a `multiprocessing` pool and measure it separately from indexing throughput.
- **Bulk**: `helpers.parallel_bulk` (sync client in threads) or `async_bulk`; chunks of **1-5 MB** (`max_chunk_bytes`), `thread_count` ~ 4 to start; tune by measurement.
- **Idempotent**: `_id` = page id, `op_type: index`; running twice yields the same doc count.
- **Checkpoint**: file with last committed offset (dump byte offset or page id plus count). Written only after a chunk is acknowledged. `--resume` continues; `--fresh` clears it.
- **Retry/backoff**: on HTTP 429 and connection errors, exponential backoff with jitter, capped attempts; log each retry.
- **Dead letter**: per-document failures (mapping errors, oversized) appended to `dead_letter.jsonl` with the reason. Ingest exits non-zero if the dead-letter rate exceeds a threshold (e.g. 0.5%).
- **Fingerprints computed at ingest** (Phase 3 code, imported from `app/winnowing.py`) so the plagiarism field is populated in one pass.
- **Metrics recorded** into `eval/results/ingest_<stage>.json`: docs/sec (indexing only and end to end), total time, index size on disk (`_cat/indices`, `_stats/store`), dead-letter count, 429 retry count, chunk size and thread settings.
- **Acceptance:** Stage A completes with doc count equal to parsed count minus dead letters; re-run leaves the count unchanged; killing ingest mid-run and resuming ends with the same count; alias reindex test passes.

## 7. Phase 2: search API (~1.5 days)

All routes are served by FastAPI with one shared `AsyncElasticsearch` client (created in lifespan, closed on shutdown), reading from alias `articles`.

### 7.1 Endpoints

| Route | Behaviour |
|---|---|
| `GET /search?q=&size=10&from=0&highlight=true&explain=false&search_after=&pit=` | `bool`: `should` of `multi_match` (`best_fields`, fields `title^4`, `text`) plus `match_phrase` on title (boost) plus `title.exact` term boost. Highlights on `text` and `title` (unified, `fragment_size` ~150, 2 fragments). Response: hits (`id`, `title`, `url`, `score`, `snippets`), `total`, `took_ms`, `next` cursor. |
| Deep paging | `from + size <= 10,000` uses `from/size`. Beyond that, the client sends no `from`; the server opens a PIT (`keep_alive` 1 m), sorts by `_score`, `_shard_doc`, and returns an opaque `next` token (encoded `search_after` + PIT id). `size` capped (e.g. 100). |
| `?explain=true` | passes `explain: true`, returns the per-hit `_explanation` tree. Gated by a config flag (`ALLOW_EXPLAIN`), since it is slow. |
| `GET /suggest?q=` | autocomplete: `multi_match` type `bool_prefix` over `title.suggest`, `title.suggest._2gram`, `_3gram`; returns top 8 titles. Plus did-you-mean: `phrase` suggester on `title`/`text` (with `direct_generator`, `max_errors`, `highlight`). Returns `{completions, did_you_mean}`. |
| `GET /related/{id}` | `more_like_this` on `text` and `title` with `like: [{_index, _id}]`, tuned `min_term_freq`, `max_query_terms`, `minimum_should_match`; excludes the source doc. 404 if the id does not exist. |
| `POST /plagiarism/check` | Phase 3. |
| `GET /healthz` | ES ping plus alias exists plus doc count; 200/503. |
| `GET /metrics` | Prometheus: request count/latency by route, ES `took`, ES error count, plagiarism candidate count. |
| `GET /` | static HTML UI (search box, suggestions, highlighted results, paste-box for plagiarism check with highlighted matching passages). Vanilla JS, no build step. |

### 7.2 Hygiene
- **Timeouts on every ES call**: client-level `request_timeout` plus a per-request `timeout` in the body; ES timeouts return partial results flagged `timed_out`; hard timeout returns 504.
- **pydantic v2** request and response models (`schemas.py`); `extra="forbid"` on requests; length and range validation (`q` 1-256 chars).
- **`pydantic-settings`** (`config.py`): ES URL, alias, timeouts, size caps, plagiarism limits, feature flags; `.env` plus env vars.
- **Structured errors**: one `ErrorResponse` (`code`, `message`, `request_id`); handlers map ES `ConnectionError` to 503, `ConnectionTimeout` to 504, `NotFoundError` to 404, validation to 422, ES 4xx query errors to 400.
- **Observability**: request-id middleware; JSON logs; no query text at INFO if it could be sensitive (plagiarism input is not logged).
- **Acceptance:** each endpoint has an API test; a stopped ES yields 503 not 500; `/metrics` shows route histograms; UI works against Stage A.

## 8. Phase 3: plagiarism detection (~2 days)

### 8.1 Winnowing (`app/winnowing.py`, pure Python)
Following Schleimer, Wilkerson and Aiken (SIGMOD 2003, MOSS).
1. **Normalise**: Unicode NFKC, lowercase, replace non-alphanumerics with spaces, collapse whitespace, split into words with their original word index. Stopwords are **not** removed (preserves the guarantee).
2. **k-grams**: word **5-grams** (`k=5`).
3. **Hash**: stable 64-bit hash of the joined gram (`hashlib.blake2b(digest_size=8)` or `xxhash`; **never** Python's salted `hash()`). Fingerprints stored as 16-char hex.
4. **Window**: `w=4`; in each window pick the minimum hash, ties broken by the **rightmost** occurrence (paper's rule); record `(hash, position)` once per selected position.
5. **Guarantee**: any shared run of `>= k + w - 1 = 8` words yields at least one common fingerprint. Expected density ~ `2/(w+1) = 0.4` of grams.
6. Texts shorter than `k` words produce no fingerprints; the API returns 422 `TEXT_TOO_SHORT` (minimum e.g. 8 words).
- Parameters (`k`, `w`, hash width) live in one constants block and are stored in index `_meta`; a query-time/index-time mismatch fails the health check.

### 8.2 Index and candidate query
- `fingerprints`: `keyword` array, norms off, not in `_source`.
- Candidate query: `terms_set` on `fingerprints` with `terms` = the input's fingerprint hashes and `minimum_should_match_script` = `Math.max(2, (int)Math.ceil(params.num_terms * params.min_frac))` (default `min_frac` ~ 0.05, tuned in eval; spike to confirm script params and cost). Return top `candidate_limit` (e.g. 50) with `_source: [title, text, url]`.
- **Re-rank in Python**: recompute winnowing for each candidate's `text` (deterministic, no stored positions needed), compute `containment` and `jaccard`, sort by containment, drop below `threshold` (default tuned from the PR curve).
- **Matching passages**: map shared hashes to word positions in both the input and the candidate; merge adjacent/overlapping positions (gap tolerance of `k` words) into spans; return the text of each span from both sides, with character offsets in the input for UI highlighting.
- **Large inputs**: `terms_set` has a terms limit (`index.max_terms_count`, default 65,536). Cap input at **50,000 chars** (~8k words, ~3k fingerprints), which also bounds script cost. Enforce at schema level (413/422).

### 8.3 API
`POST /plagiarism/check` body: `{text, threshold?, max_results?}`. Response: `{input_words, input_fingerprints, took_ms, matches: [{id, title, url, containment, jaccard, passages: [{input: {start, end, text}, source: {start, end, text}, words}]}]}`.

### 8.4 Unit tests (`tests/test_winnowing.py`)
- **Determinism**: same text twice, and across processes (subprocess with different `PYTHONHASHSEED`), gives identical fingerprints.
- **Guarantee** (hypothesis): random word lists, embed a shared run of exactly 8 words in different random contexts; the fingerprint sets intersect. Also shows that 7 shared words **can** be missed (documents the bound).
- **Noise tolerance**: one-word substitution changes at most `k` k-grams; containment of the edited text against the original stays above a stated floor; whitespace, case and punctuation changes give identical fingerprints.
- **Density**: average fingerprint ratio over a large random text is within tolerance of 0.4.
- **Edge cases**: empty, shorter than k, unicode, repeated text (many equal hashes).

### 8.5 Evaluation set (`eval/plagiarism/`)
Seeded (`--seed 42`), deterministic, regenerable. Sample **500 random Articles** from the Stage-A index (filter: >= 150 words, not a list or disambiguation page). Each source yields one query per transformation:

| Transformation | Construction | Label |
|---|---|---|
| `exact` | paragraph (60-200 words) copied verbatim | source id |
| `light_edit` | 10% of words deleted or inserted (seeded) | source id |
| `synonym` | WordNet synonym swap on ~30% of content words (nltk POS-tagged, same POS only, first non-identical lemma) | source id |
| `reorder` | sentences of a paragraph shuffled | source id |
| `mixed` | sentences from **two** different sources interleaved | both ids |
| `neg_unrelated` | paragraph from an Article **not in the index** (e.g. held-out sample, or a different-language-free generated control) | none |
| `neg_same_topic` | paragraph on the same topic but different text: lead of a `more_like_this` neighbour of the source, with the source excluded; manually spot-check 20 | none |

- **Held-out negatives**: for truly unrelated text not present in the index, hold out a random 1% of Articles at ingest time (written to `heldout_ids.txt`, never indexed) and use them as the source of `neg_unrelated`.
- **Cleaning the eval**: Wikipedia has near-duplicate leads (templated stubs, lists). Flag a "positive" whose top hit is a different Article with containment >= 0.9 as a **near-duplicate**, report them separately instead of counting them as false positives, and say so.
- **Metrics, per transformation**: recall@1/5/10 (positive found in top-k), precision at the chosen threshold (positives correct / all flagged), full **PR curve** by sweeping threshold on containment (positives pooled with negatives), AUC-PR, plus latency per query. Mixed: recall of both sources.
- **Outputs**: `eval/results/plagiarism/*.json`, PR curve PNGs (one figure with a curve per transformation), a markdown table inserted into the README.
- **Headline number**: define precisely in the README ("on N=500 sources x 7 transformations = 3,500 cases, X% recall@10 on positive cases and Y% precision at threshold T"), and which subset feeds the resume bullet. State openly that **synonym swaps score lowest**, because word-level hashing cannot match changed words.
- **Also report** a small ablation on `k` and `w` (e.g. k in {4,5,6} x w in {3,4,6}): recall, precision, index fingerprint count and size.
- **Acceptance:** exact >= ~99% recall@1; noise-tolerance targets are reported, not asserted, except exact and negatives (false positive rate for unrelated negatives ~ 0 at the chosen threshold).

## 9. Phase 4: relevance and latency evaluation (~1.5 days)

### 9.1 Relevance (`eval/relevance/`)
- **Ground truth from redirects**: `redirects.jsonl` of `(alias_title, target_page_id)` from the redirect and page tables (or `<redirect>` elements in the XML). Keep only main-namespace redirects to a main-namespace target that exists in the index; drop section redirects (`#`), self-redirects, and queries that equal the target title after normalisation (trivial); cap at 2 per target to avoid bias to popular pages. Seeded sample of ~5,000 queries, split **50% dev / 50% test** (by target id).
- **Configs** (all via `articles_exp` subfields, same query shape except where noted):
  1. default analyzer, single field (`text.std` over concatenated title and text, or `title.std` + `text.std` as one `multi_match` without boosts);
  2. built-in English analyzer (`.english` subfields);
  3. custom analyzer + `title^4` + title phrase boost + `title.exact` (the production query);
  4. config 3 with tuned BM25 `k1`/`b` (`.tuned` subfields).
  - Optional 5th: title-only ablation to show how much `title` carries.
- **BM25 tuning**: grid `k1 in {0.6, 0.9, 1.2, 1.5, 2.0}` x `b in {0.3, 0.5, 0.75, 0.9}` chosen on the **dev** split, reported on **test** (no tuning on the reporting set). Since similarity is mapping-level, build one `.tuned_<k1>_<b>` subfield per grid point on Stage A (small), or reindex a 50k-doc sample per grid point; pick the cheaper after a spike.
- **Metrics**: MRR@10, Recall@10, nDCG@10, plus Recall@1. With a single relevant doc per query, Recall@10 = hit-rate@10 and nDCG@10 = `1/log2(rank+1)`. Report **bootstrap 95% CIs** and a **paired bootstrap** significance test between consecutive configs.
- **Caveats written in the README**: redirects measure known-item search only; redirect titles and targets often share terms (lexical bias); popular aliases dominate; no judgement of informational queries. **Optional**: hand-judge ~30 informational queries (graded 0-2, saved as `eval/relevance/judged.jsonl`) and report nDCG@10 for configs 3 and 4 only.
- **Run at**: Stage A (250k) and Stage B (1.2M) so the README shows whether the ranking of configs holds at scale.
- **Output**: `eval/results/relevance/{stage}_{config}.json` (per-query ranks, so metrics can be recomputed), summary table (markdown + CSV).

### 9.2 Latency (`bench/`, k6)
- **Query sets**: 5,000 queries sampled from redirect titles and article titles (with a mix of 1-, 2- and 3+-word queries), seeded; loaded into k6 via `SharedArray`. Plagiarism inputs: slices of articles at 50, 200, 1,000, 4,000 and 8,000 words.
- **Scenarios**: `/search` at concurrency **1, 10, 50** (closed-loop VUs, 60 s after 10 s ramp), at **250k** and **1.2M** docs; `/suggest` and `/related` at concurrency 10 and 50 as secondary; **plagiarism latency vs input length** at concurrency 1 and 10.
- **Cold vs warm cache**: cold = restart the ES container, drop OS page cache where possible (note: macOS/Docker Desktop makes true page-cache control hard; document how "cold" was approximated: container restart plus `_cache/clear`), first N requests measured; warm = after a warm-up pass. Report both and what was done.
- **Reporting**: p50/p95/p99, throughput (req/s), error rate, plus server-side ES `took` percentiles, so API overhead and ES time can be separated. Same ES `_forcemerge` state for 250k and 1.2M.
- **Method hygiene**: pin Docker CPU/memory, close other apps, 3 repeated runs per cell reported as median with min-max; record machine spec, ES version and settings in `bench/results/env.json`. Note that k6 and ES share the machine, so numbers are conservative-ish and not production figures.
- **Raw output saved**: `k6 run --out json=...` and `--summary-export`, committed (or gzipped) under `bench/results/`. README tables are generated from them by a script, not hand-copied.

## 10. Phase 5: tests, CI, docs (~1 day)

### 10.1 Tests (pytest)
| Area | Tests |
|---|---|
| Analyzers | `_analyze` API: possessives, stemming, stopwords, asciifolding (`café` to `cafe`), `title.exact` normalizer, `search_as_you_type` shingles |
| API | `httpx.AsyncClient` with `ASGITransport` against a small fixture index (~50 docs): search, highlight, pagination, PIT deep paging, `explain`, suggest, did-you-mean, related, `healthz`, `metrics`, error mapping (stopped ES, bad input, timeout) |
| Winnowing | section 8.4 |
| Plagiarism | end-to-end: ingest 20 docs, query a copied paragraph, expect source first with passages |
| Ingestion | idempotency (run twice, same count), checkpoint resume after simulated crash, 429 retry (mock transport), dead-letter on bad doc |
| Alias | reindex `v1` to `v2` and alias swap with zero failed queries during the swap |

- Unit tests run without ES; integration tests marked `@pytest.mark.es` and use the service container or compose.
- **Lint/format**: `ruff check` and `ruff format --check`.

### 10.2 CI (`.github/workflows/ci.yml`)
Jobs: `lint`, `unit` (no ES), `integration` with an `elasticsearch:8.19.x` service container (security off, `discovery.type=single-node`, small heap, health-check wait). Python 3.12, `uv` cache. Evals and benches are **not** run in CI (too heavy); CI runs a tiny smoke version of the plagiarism eval on the fixture index.

### 10.3 README
Sections: what and why; architecture diagram; quickstart; ES-vs-Solr notes; index-design decisions (and why); evaluation tables (relevance, plagiarism per transformation, latency); limits; data attribution; reproduction commands.
- **ES-vs-Solr** (from CDAC Solr experience; verify claims before writing): schema (`managed-schema`/schemaless vs explicit mappings), analysis chains, query DSL (JSON) vs Solr params/`edismax`, relevance default (both BM25 now), highlighting, suggesters (Solr `SuggestComponent` vs ES suggesters/`search_as_you_type`), `more_like_this` handlers, near-real-time (soft commit vs refresh), sharding/replicas model (SolrCloud/ZooKeeper vs ES cluster state), reindexing (aliases in both), operational differences. Mark opinions as opinions.
- **Limits** (must include): known-item eval bias; single node on a laptop; lead-only text; synthetic plagiarism set (not human-created plagiarism); synonym swaps; Wikipedia near-duplicates; cold-cache approximation; no auth.

## 11. Milestones and acceptance gates

| # | Milestone | Gate |
|---|---|---|
| M0 | Cluster up, Stage A downloaded and parsed (counts, manifest) | ES responds, `corpus_manifest.json` written |
| M1 | `articles_v1` + ingest on Stage A; docs/sec recorded | idempotency and resume tests pass |
| M2 | Search, suggest, related, health, metrics, UI | API tests green on fixture index |
| M3 | Winnowing + plagiarism endpoint | winnowing tests + e2e test green |
| M4 | Plagiarism eval on Stage A | per-transformation table and PR curves saved |
| M5 | Stage B ingest to 1.2M (with 250k pause point) | doc count >= 1.2M, size and rate recorded |
| M6 | Relevance eval (A and B) | table with CIs saved |
| M7 | Latency bench (250k, 1.2M, cold/warm, plagiarism) | raw k6 output saved |
| M8 | CI green, README with all numbers | every README number traces to a file in `eval/results` or `bench/results` |

Order rationale: build and validate everything on Stage A first; only then start the long Stage B download and ingest (can run in the background while Phase 3 and 4 code is written).

## 12. Resume bullet (filled only from saved results)

"Rebuilt a full-text + plagiarism search service on Elasticsearch/FastAPI over **N** M Wikipedia documents; winnowing fingerprints reached **X**% recall / **Y**% precision on a 500-case synthetic set; p95 search latency **Z** ms at 50 concurrent users."

Bind each placeholder to a file: N from `ingest_B.json`; X/Y from `eval/results/plagiarism/summary.json` (state which transformations are pooled); Z from `bench/results/search_1.2M_c50_warm.json`. The README says "laptop, single node" next to Z. Note that "500-case" must match the actual eval definition (500 source articles per transformation).

## 13. Decisions made, risks and open items

**Decided (record as ADRs):**
1. `verity/` is the repo root; load tool is **k6**.
2. Python 3.12 via `uv`; ES 8.19.x pinned.
3. Lead-only document shape, identical for Stage A and B.
4. One index with `articles_exp` subfields for relevance A/B instead of per-config indexes.
5. Re-rank by recomputing winnowing in Python from stored `text` (no stored positions).
6. 64-bit hex fingerprints in a `keyword` field (a `long` field is an alternative worth one measured ablation if size or speed matters).
7. 1 shard / 0 replicas locally.

**Risks:**
- **Docker memory/daemon**: daemon is currently off; ES with 2 GB heap on a 16 GB machine alongside k6 and the dump parser can OOM. Mitigate: Docker VM 6-8 GB, run ingest and bench separately.
- **Redirect/dump date mismatch** (section 5.1) breaks ground truth. Mitigate: pin one dump date.
- **Wikitext cleaning cost/quality**: leads for 1.2M pages can take hours. Mitigate: multiprocessing, measure early on 10k pages; consider the pre-cleaned HF parquet.
- **`terms_set` cost** with thousands of terms and a script: spike early on Stage A to confirm latency is acceptable; fallback is a higher `min_frac` or using `minimum_should_match_field` with a stored count.
- **Cold-cache control on macOS**: can only be approximated; stated in README.
- **Synthetic eval optimism**: synthetic transformations are easier than real plagiarism; stated in README.
- **Licence**: verify at download; keep CC BY-SA attribution; do not redistribute dumps; the committed eval set contains small Wikipedia excerpts, so attribute them, or commit IDs and seed only.

**Open items to confirm at implementation time (not blockers for approval):** exact Stage B source and dump date; whether HF `wikimedia/wikipedia` card licence wording is acceptable; ES 8.19.x tag availability; `terms_set` script params behaviour on 8.19; whether to include the optional 30-query hand-judged set.

## 14. Verification (how we know the project is done)

1. `make up && make ingest-a` brings up ES and indexes Stage A; re-running does not change `_count`.
2. `make test` is green locally, and CI is green on a PR.
3. `curl 'localhost:8000/search?q=...'` returns highlighted results; `/` UI works; `/metrics` and `/healthz` respond.
4. `make eval-plagiarism` and `make eval-relevance` regenerate `eval/results/` from the seed; a re-run gives identical numbers.
5. `make bench` produces raw k6 output at 250k and 1.2M; `make report` regenerates README tables from the saved files.
6. A spot check: 5 random README numbers trace back to their source files.
