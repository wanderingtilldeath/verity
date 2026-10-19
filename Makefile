.PHONY: up down setup ingest-a ingest-b ingest-b-resume serve test test-unit lint eval-plagiarism eval-relevance bench report

DATA ?= $(HOME)/data/wikisearch
STAGE_A_DUMP ?= $(DATA)/simplewiki-latest-pages-articles.xml.bz2

up:
	docker compose up -d --wait

down:
	docker compose down

setup:
	uv sync
	uv run python -c "import nltk; nltk.download('wordnet'); nltk.download('averaged_perceptron_tagger_eng')"

ingest-a:
	uv run python -m ingest run --source xml --path $(STAGE_A_DUMP) --stage A --heldout-frac 0.01

# Stage B pauses at 250k docs (force-merged) so the 250k benchmark runs on the same index; then resume.
ingest-b:
	uv run python -m ingest run --source $(B_SOURCE) --path $(B_PATH) --stage B --limit 1250000 --pause-at 250000

ingest-b-resume:
	uv run python -m ingest run --source $(B_SOURCE) --path $(B_PATH) --stage B --limit 1250000 --resume --merge

serve:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

test-unit:
	uv run pytest -m "not es"

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check .

eval-plagiarism:
	uv run python -m eval.plagiarism.run --seed 42 --n 500

eval-relevance:
	uv run python -m eval.relevance.run --stage A --redirects $(DATA)/redirects_A.jsonl

bench:
	bash bench/run.sh

report:
	uv run python -m eval.report
