"""python -m ingest <command>

run            parse a dump, clean leads, fingerprint, bulk-index (resumable, idempotent)
create-index   create articles_v1 and point the alias at it
reindex        zero-downtime reindex into a new index and swap the alias
redirects      build redirects.jsonl for the relevance eval
manifest       record dump names, sizes and sha256 for the README
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing as mp
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from elasticsearch import Elasticsearch

from app.config import get_settings
from ingest import index as ix
from ingest.bulk import Checkpoint, DeadLetter, index_stream
from ingest.docs import Config, make_doc, text_key
from ingest.dump_reader import iter_pages
from ingest.redirects import from_sql, from_xml, load_titles, write_jsonl

log = logging.getLogger("ingest")
RESULTS = Path(__file__).resolve().parent.parent / "eval" / "results"
MAX_DEAD_LETTER_RATE = 0.005


def default_data_dir() -> Path:
    return Path.home() / "data" / "wikisearch"


def client_from_env() -> Elasticsearch:
    return Elasticsearch(get_settings().es_url, request_timeout=30)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(path: Path, stage: str, lang: str, source: str) -> None:
    manifest_path = RESULTS / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if stage not in manifest or manifest[stage].get("file") != path.name:
        manifest[stage] = {
            "file": path.name,
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256_of(path) if path.is_file() else None,
            "lang": lang,
            "source": source,
            "note": "verify name, date, licence at download time; text is CC BY-SA",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))


def doc_stream(
    args: argparse.Namespace, cfg: Config, skip_records: int, docs_done: int, counters: Counter
) -> Iterator[tuple[int, int, dict]]:
    """(records consumed, docs emitted, doc). Cleaning and fingerprinting run in a process pool."""
    out_dir: Path = args.out_dir
    titles = (out_dir / f"titles_{args.stage}.tsv").open("a", encoding="utf-8")
    heldout = (out_dir / f"heldout_{args.stage}.jsonl").open("a", encoding="utf-8")
    seen: set[bytes] = set()  # exact-duplicate text filter (per run)

    def pages() -> Iterator[tuple]:
        for n, page in enumerate(iter_pages(args.source, args.path), start=1):
            if n <= skip_records:
                continue  # resume: parsing is cheap, cleaning is not
            yield (page, cfg)

    records, docs = skip_records, docs_done
    t0 = time.perf_counter()
    with mp.Pool(args.workers) as pool:
        for status, doc in pool.imap(make_doc, pages(), chunksize=64):
            records += 1
            counters[status] += 1
            if doc is None:
                continue
            if status == "heldout":
                heldout.write(json.dumps(doc, ensure_ascii=False) + "\n")
                continue
            key = text_key(doc["text"])
            if key in seen:
                counters["skip_duplicate"] += 1
                continue
            seen.add(key)
            titles.write(f"{doc['page_id']}\t{doc['title']}\n")
            docs += 1
            yield records, docs, doc
            if args.limit and docs >= args.limit:
                break
            if args.pause_at and docs >= args.pause_at and docs_done < args.pause_at:
                counters["_paused"] = 1
                break
            if docs % 20_000 == 0:
                rate = (docs - docs_done) / (time.perf_counter() - t0)
                log.info("%d docs (%.0f docs/s parse+clean)", docs, rate)
    titles.close()
    heldout.close()


def cmd_run(args: argparse.Namespace) -> int:
    client = client_from_env()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ix.create_index(client, args.index)
    ix.ensure_alias(client, args.alias, args.index)
    write_manifest(args.path, args.stage, args.lang, args.source)

    ckpt = Checkpoint(args.out_dir / f"checkpoint_{args.stage}.json")
    if args.fresh:
        ckpt.clear()
    state = ckpt.load() if args.resume else {"records": 0, "docs": 0}
    dead = DeadLetter(args.out_dir / f"dead_letter_{args.stage}.jsonl")
    cfg = Config(source=args.source, lang=args.lang, heldout_frac=args.heldout_frac)
    counters: Counter = Counter()

    stream = doc_stream(args, cfg, state["records"], state["docs"], counters)
    with ix.bulk_mode(client, args.index):
        stats = index_stream(
            client,
            args.index,
            stream,
            ckpt,
            dead,
            threads=args.threads,
            max_chunk_bytes=int(args.chunk_mb * 1_000_000),
        )
        if args.pause_at or args.merge:
            ix.force_merge(client, args.index, args.segments)
    final = ix.index_stats(client, args.index)

    report = stats.as_dict(args.threads, int(args.chunk_mb * 1e6))
    report.update(
        stage=args.stage,
        resumed_from=state,
        source_counters={k: v for k, v in counters.items() if not k.startswith("_")},
        dead_letter=dead.count,
        index=final,
        paused=bool(counters.pop("_paused", 0)),
    )
    out = RESULTS / f"ingest_{args.stage}.json"
    history = json.loads(out.read_text()) if out.exists() else []
    out.write_text(json.dumps(history + [report], indent=2))
    print(json.dumps(report, indent=2))

    total = stats.indexed + stats.failed
    if total and dead.count / total > MAX_DEAD_LETTER_RATE:
        print(f"dead-letter rate {dead.count / total:.2%} exceeds limit", file=sys.stderr)
        return 1
    if report["paused"]:
        print(
            f"Paused at {final['docs']} docs. Run the benchmark, then re-run with --resume "
            "(without --pause-at) to continue.",
            file=sys.stderr,
        )
    return 0


def cmd_create_index(args: argparse.Namespace) -> int:
    client = client_from_env()
    created = ix.create_index(client, args.index)
    ix.ensure_alias(client, args.alias, args.index)
    print(f"{'created' if created else 'exists'}: {args.index} -> alias {args.alias}")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    n = ix.reindex_and_swap(
        client_from_env(), args.alias, args.old, args.new, delete_old=args.delete_old
    )
    print(f"reindexed {n} docs; alias {args.alias} now -> {args.new}")
    return 0


def cmd_redirects(args: argparse.Namespace) -> int:
    titles = load_titles(args.titles)
    if args.xml:
        rows = from_xml(args.xml, titles)
    else:
        rows = from_sql(args.redirect_sql, args.page_sql, titles)
    n = write_jsonl(rows, args.out)
    print(f"wrote {n} redirects to {args.out}")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    write_manifest(args.path, args.stage, args.lang, args.source)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    settings = get_settings()

    r = sub.add_parser("run")
    r.add_argument("--source", choices=["xml", "parquet"], required=True)
    r.add_argument("--path", type=Path, required=True)
    r.add_argument("--stage", choices=["A", "B"], required=True)
    r.add_argument("--lang", default=None, help="simple | en (default: simple for A, en for B)")
    r.add_argument("--index", default="articles_v1")
    r.add_argument("--alias", default=settings.es_alias)
    r.add_argument(
        "--limit", type=int, default=0, help="stop after this many indexed docs in total"
    )
    r.add_argument("--pause-at", type=int, default=0, help="stop and force-merge at N docs")
    r.add_argument("--heldout-frac", type=float, default=0.0)
    r.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 2))
    r.add_argument("--threads", type=int, default=4)
    r.add_argument("--chunk-mb", type=float, default=4.0)
    r.add_argument("--merge", action="store_true", help="force-merge when done")
    r.add_argument("--segments", type=int, default=1)
    r.add_argument("--out-dir", type=Path, default=default_data_dir())
    g = r.add_mutually_exclusive_group()
    g.add_argument("--resume", action="store_true", help="continue from the checkpoint")
    g.add_argument("--fresh", action="store_true", help="ignore and clear the checkpoint")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("create-index")
    c.add_argument("--index", default="articles_v1")
    c.add_argument("--alias", default=settings.es_alias)
    c.set_defaults(fn=cmd_create_index)

    x = sub.add_parser("reindex")
    x.add_argument("--alias", default=settings.es_alias)
    x.add_argument("--old", required=True)
    x.add_argument("--new", required=True)
    x.add_argument("--delete-old", action="store_true")
    x.set_defaults(fn=cmd_reindex)

    d = sub.add_parser("redirects")
    d.add_argument("--titles", type=Path, required=True)
    d.add_argument("--xml", type=Path)
    d.add_argument("--redirect-sql", type=Path)
    d.add_argument("--page-sql", type=Path)
    d.add_argument("--out", type=Path, required=True)
    d.set_defaults(fn=cmd_redirects)

    m = sub.add_parser("manifest")
    m.add_argument("--path", type=Path, required=True)
    m.add_argument("--stage", choices=["A", "B"], required=True)
    m.add_argument("--lang", default="simple")
    m.add_argument("--source", default="xml")
    m.set_defaults(fn=cmd_manifest)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.cmd == "run" and args.lang is None:
        args.lang = "simple" if args.stage == "A" else "en"
    if args.cmd == "redirects" and not (args.xml or (args.redirect_sql and args.page_sql)):
        print("give --xml, or both --redirect-sql and --page-sql", file=sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
