#!/usr/bin/env bash
# Usage: LABEL=250k bash bench/run.sh      (API must be running on BASE; index at the target size)
# Produces, per cell, a k6 summary export (committed) and a gzipped raw point stream (untracked).
#   CONCURRENCY="1 10 50"  REPEATS=3  DURATION=60s  DATA=bench/data
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE:-http://127.0.0.1:8000}"
LABEL="${LABEL:?set LABEL, e.g. 250k or 1.2M}"
CONCURRENCY="${CONCURRENCY:-1 10 50}"
REPEATS="${REPEATS:-3}"
DURATION="${DURATION:-60s}"
DATA="${DATA:-bench/data}"
OUT=bench/results
mkdir -p "$OUT/raw"

uv run python -m bench.record_env "$LABEL"

k6run() { # name, script, extra -e args...
  local name="$1" script="$2"; shift 2
  k6 run --quiet \
    --summary-export "$OUT/summary_${name}.json" \
    --out "json=$OUT/raw/${name}.json.gz" \
    -e BASE="$BASE" -e DURATION="$DURATION" "$@" "$script"
}

wait_ready() {
  for _ in $(seq 1 60); do curl -fs "$BASE/healthz" >/dev/null && return 0; sleep 2; done
  echo "API not healthy at $BASE" >&2; exit 1
}

# --- cold: restart ES, clear caches, NO warm-up. Approximation: macOS/Docker Desktop hides the page cache.
for r in $(seq 1 "$REPEATS"); do
  docker compose restart es >/dev/null
  until curl -fs localhost:9200/_cluster/health >/dev/null; do sleep 2; done
  curl -fs -XPOST "localhost:9200/_cache/clear" >/dev/null
  wait_ready
  k6run "search_${LABEL}_c1_cold_r${r}" bench/search.js -e VUS=1 -e WARMUP=1s -e QUERIES="$DATA/queries.json"
done

# --- warm: 30 s unmeasured pass first, then measured runs
k6 run --quiet -e BASE="$BASE" -e VUS=10 -e DURATION=30s -e WARMUP=1s -e QUERIES="$DATA/queries.json" bench/search.js >/dev/null
for c in $CONCURRENCY; do
  for r in $(seq 1 "$REPEATS"); do
    k6run "search_${LABEL}_c${c}_warm_r${r}" bench/search.js -e VUS="$c" -e QUERIES="$DATA/queries.json"
  done
done

# --- secondary endpoints
for ep in suggest related; do
  for c in 10 50; do
    k6run "${ep}_${LABEL}_c${c}_warm_r1" bench/search.js -e ENDPOINT="$ep" -e VUS="$c" -e QUERIES="$DATA/queries.json"
  done
done

# --- plagiarism latency vs input length
for len in 50 200 1000 4000 8000; do
  for c in 1 10; do
    k6run "plagiarism_${LABEL}_len${len}_c${c}_r1" bench/plagiarism.js -e LEN="$len" -e VUS="$c" -e INPUTS="$DATA/plagiarism_inputs.json"
  done
done
echo "done: $OUT"
