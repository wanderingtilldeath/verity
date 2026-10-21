# Code index
Notation: `path:sym` = symbol in file · `→` calls/produces · `T:` tests (all under tests/; `[es]` = needs ES, marked `es`) · `S:` schema/data shape · `ADR` = docs/adr/NNNN-*.md · `∅T` = no direct test.
Line numbers deliberately omitted (rot); grep the symbol.

## Runtime map
dump → ingest.cli:cmd_run → dump_reader:iter_pages → docs:make_doc (clean+winnow) → bulk:index_stream → ES alias `articles`→`articles_v1`
client → app.main:create_app routes → app.search / app.plagiarism → ES; fingerprints from app.winnowing (shared w/ ingest+eval)

## app/ (FastAPI, async ES)
### HTTP routes — app/main.py:create_app
| route | handler→impl | req/resp schema | T |
|---|---|---|---|
| GET /search | →search.search (multi_match title^4+text, phrase+exact boost, highlight, from/size ≤10k, PIT cursor beyond) | SearchResponse{Hit} | test_api: ranks_title_match_first_and_highlights, exact_title_beats_body_mentions, snippets_are_html_escaped, pagination_from_size, deep_paging…cursor…, cursor_for_other_query_is_rejected, explain_* |
| GET /suggest | →search.suggest (search_as_you_type bool_prefix + phrase suggester) | SuggestResponse | test_api: suggest_* |
| GET /related/{doc_id} | →search.related (more_like_this; 404 unknown) | RelatedResponse | test_api: related_excludes_source_and_404s |
| POST /plagiarism/check | →plagiarism.check | PlagiarismRequest→PlagiarismResponse{PlagiarismMatch{PassageMatch{Span}}} | test_plagiarism_e2e (all) |
| POST /reuse/dedupe | →reuse.dedupe (to_thread, no ES) | DedupeRequest{TextDoc}→DedupeResponse{DedupeCluster{PairScore}} | test_reuse (dedupe_*) |
| POST /reuse/drift | →reuse.drift (paragraph split, best clause by containment, `UNCHANGED`=0.95) | DriftRequest→DriftResponse{DriftSection} | test_reuse (drift_*) |
| GET /healthz | main (inline): ping, alias, mapping `_meta` winnow params vs code | HealthResponse | test_api:healthz_ok; test_analyzers:mapping_is_strict_and_records_winnowing_params |
| GET /metrics | →metrics.render | prometheus text | test_api:metrics_exposes_route_histograms |
| GET / , /static | StaticFiles app/static/index.html | — | test_api:static_ui_is_served |
Middleware (main.py): request_id (x-request-id or uuid[:12]), per-route latency/count metrics, JSON logs (JsonFormatter, configure_logging). lifespan creates the AsyncElasticsearch client (stored on app.state; deps.get_es reads it).

### Modules
- app/config.py:Settings(BaseSettings), get_settings — env: ES_URL ES_ALIAS SEARCH_TIMEOUT_S PLAGIARISM_THRESHOLD ALLOW_EXPLAIN (.env.example). deps.py:settings_dep. ∅T (exercised via test_api:explain_*).
- app/errors.py:AppError, install_handlers → one body `{code,message,request_id}` (schemas.ErrorResponse); ES ConnectionError→503, timeout→504, validation→structured 422. T: test_api:validation_errors_are_structured, es_timeout_maps_to_504, stopped_es_gives_503_not_500.
- app/search.py: build_query, run_search, hit_from_es, encode_cursor/decode_cursor (PIT+search_after, bound to q), search, suggest, related. T: test_api.
- app/plagiarism.py: candidate_query (terms_set on `fingerprints`, ≥2 shared & ≥min_frac), rerank (containment, jaccard, passages via winnowing.align), check (input 50k chars→413, <8 words→422). T: test_plagiarism_e2e: exact_copy_is_found_first_with_passages, light_edit_still_matches, mixed_sources_return_both, unrelated_text_has_no_matches, too_short_and_too_large_inputs, request_validation.
- app/reuse.py: dedupe (inverted hash index→pairs sharing ≥2 hashes→Jaccard≥threshold→union-find clusters), drift (paragraph regex, per-section winnow). Reuses plagiarism._span. In-memory, no ES. T: test_reuse (no `es` marker).
- app/winnowing.py (core, K=5 W=4): tokenize→Token, winnow→Fingerprint, fingerprints, fingerprint_hashes (hex str list stored in index), containment, jaccard, align→Passage. Hash = stdlib deterministic, never builtin (ADR 0006). T: test_winnowing (Hypothesis): shared_run_of_k_plus_w_minus_1_words_is_always_detected, stable_across_processes_and_hash_seeds, offsets_point_at_original_text, density, align_*, containment_and_jaccard, edge_cases.
- app/metrics.py: prometheus counters/histograms (route, ES took, ES errors, candidates), render.
- app/schemas.py: all pydantic models (above).

## ingest/ (CLI: `uv run python -m ingest <sub>`, ingest/__main__.py→cli.main)
| sub | fn | notes |
|---|---|---|
| run | cli:cmd_run | --source xml\|parquet --path --stage A\|B [--limit --pause-at --heldout-frac --resume\|--fresh --merge --workers --threads --chunk-mb]; writes corpus manifest + titles_<stage>.tsv + heldout |
| create-index | cmd_create_index | index.create_index+ensure_alias |
| reindex | cmd_reindex | index.reindex_and_swap (scan+bulk, recompute fps, atomic alias swap; ADR 0003) |
| redirects | cmd_redirects | redirects:load_titles, from_xml\|from_sql, write_jsonl → eval relevance queries |
| manifest | cmd_manifest | name,size,sha256 → eval/results/corpus_manifest.json |
- dump_reader.py: Page(dataclass), iter_xml, iter_parquet, iter_pages(source,path). ∅T (fixtures bypass).
- clean.py: clean_lead, lead_wikitext, truncate(MAX_CHARS 5000), tidy, is_disambiguation, lead_from_plain, word_count. T: test_clean (first 5).
- docs.py: Config, make_doc(args)→(status, doc|None) [S: doc = id,title,text,url,fingerprints,…], is_heldout(page_id,frac) hash-select, text_key. T: test_clean:make_doc_*, heldout_split_is_deterministic….
- bulk.py: chunk_stream (byte+doc limits), send_chunk (429 backoff_delay), index_stream (thread pool), Checkpoint (contiguous prefix), DeadLetter, Stats. Custom, not parallel_bulk (ADR 0004). T: test_bulk (FakeClient, no ES): chunks_respect…, 429_items_are_retried…, permanent_failure_goes_to_dead_letter…, idempotent_ids…, checkpoint_*; [es] test_ingest_es: running_twice…, resume_after_crash…, bad_document_goes_to_dead_letter, bulk_mode_restores_refresh_interval_even_on_crash, fingerprints_are_searchable_but_not_returned_in_source.
- index.py: load_mapping, create_index, ensure_alias, bulk_mode (refresh -1, restored in finally), force_merge, doc_count, index_stats, reindex_and_swap. T: [es] test_alias:reindex_swaps_alias_with_zero_failed_queries_and_keeps_fingerprints; test_ingest_es.
- es/articles_v1.json (prod mapping, strict, 1 shard/0 replica, `_meta` winnow params) · es/articles_exp.json (eval-only, +.std/.english subfields) · es/README.md. T: [es] test_analyzers (possessive, stopwords, stemming, asciifolding, title_exact_normalizer, search_as_you_type_shingles).

## eval/ (results → eval/results/, rendered by `make report`)
- relevance: `python -m eval.relevance.run --stage A --redirects …` (make eval-relevance). queries.py:Query,build,split_of(dev/test),save/load; configs.py:config_1_default…config_5_title_only,config_4_tuned(k1,b),tuned_id; metrics.py:rr,hit,ndcg,bootstrap_ci,paired_bootstrap; build_exp_index.py:mapping_with_grid,parse_grid (`--grid full`); run.py:rank_queries,grid_points,summarise,main. ∅T.
- plagiarism: `python -m eval.plagiarism.run --seed 42 --n 500` (make eval-plagiarism). generate.py:Case,generate,sample_sources,rank_heldout_by_topic,light_edit,synonym_swap,reorder,interleave,write_cases/read_cases (jsonl; transformations exact|light_edit|synonym|reorder|mixed|neg_unrelated|neg_same_topic); metrics.py:recall_at_k,confusion_at,pr_curve,average_precision,best_f1,summarise,markdown_table; run.py:run_cases (async, in-process app),plot_pr; ablation.py:score,main (`--cases`). ∅T (pipeline covered by test_plagiarism_e2e).
- report.py:main (bench_tables, ingest_table) → docs/results.md.

## bench/ (k6)
run.sh (`LABEL=250k bash bench/run.sh`; env BASE CONCURRENCY REPEATS DURATION DATA) · search.js · plagiarism.js · build_queries.py:main · record_env.py:main,sh → bench/results/summary_*.json. ∅T.

## tests/ infra
conftest.py: pytest_collection_modifyitems (skip `es` tests if no ES at ES_URL), fixtures es, fixture_alias (tiny corpus behind alias), temp_index, load_docs. corpus.py: make_docs, filler (deterministic fixture Articles). Unit-only run: `pytest -m "not es"` (test_bulk, test_clean, test_winnowing, test_reuse).

## Invariants → where enforced
alias-only access: ingest.index, app.config:es_alias · fps recomputed not copied: index:reindex_and_swap (ADR 0003) · winnow params match index: main:/healthz + es/articles_v1.json `_meta` · Lead-only ≤5000 ch: clean:truncate (ADR 0001) · Held-out never indexed: docs:is_heldout · single index 250k→1.2M: ADR 0002 · 1 shard: ADR 0005 · highlights escaped, only `<mark>`: search.hit_from_es.
