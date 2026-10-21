from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import metrics, plagiarism, reuse, search
from app.config import Settings, get_settings
from app.deps import ES, SETTINGS
from app.errors import AppError, install_handlers
from app.schemas import (
    DedupeRequest,
    DedupeResponse,
    DriftRequest,
    DriftResponse,
    HealthResponse,
    PlagiarismRequest,
    PlagiarismResponse,
    RelatedResponse,
    SearchResponse,
    SuggestResponse,
)
from app.winnowing import HASH_BITS, K, W

STATIC = Path(__file__).parent / "static"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name}
        out["msg"] = record.getMessage()
        for key in ("request_id", "route", "status", "ms"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        return json.dumps(out)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


log = logging.getLogger("app.access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # One client for the whole process; per-call request_timeout is applied in run_search.
    app.state.es = AsyncElasticsearch(s.es_url, request_timeout=s.es_request_timeout_s)
    try:
        yield
    finally:
        await app.state.es.close()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="verity", version="0.1.0", lifespan=lifespan)
    install_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request.state.request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", "unmatched")
            metrics.REQUESTS.labels(route, request.method, str(status)).inc()
            metrics.LATENCY.labels(route).observe(elapsed)
            # Request bodies (plagiarism input) are deliberately never logged.
            log.info(
                "request",
                extra={
                    "request_id": request.state.request_id,
                    "route": route,
                    "status": status,
                    "ms": round(elapsed * 1000, 1),
                },
            )

    @app.get("/search", response_model=SearchResponse)
    async def search_route(
        es: Annotated[AsyncElasticsearch, ES],
        s: Annotated[Settings, SETTINGS],
        q: Annotated[str, Query(min_length=1, max_length=256)],
        size: Annotated[int, Query(ge=1, le=100)] = 10,
        from_: Annotated[int, Query(alias="from", ge=0)] = 0,
        highlight: bool = True,
        explain: bool = False,
        cursor: Annotated[str | None, Query(max_length=4096)] = None,
    ):
        return await search.search(es, s, q.strip(), size, from_, highlight, explain, cursor)

    @app.get("/suggest", response_model=SuggestResponse)
    async def suggest_route(
        es: Annotated[AsyncElasticsearch, ES],
        s: Annotated[Settings, SETTINGS],
        q: Annotated[str, Query(min_length=1, max_length=128)],
    ):
        return await search.suggest(es, s, q.strip())

    @app.get("/related/{doc_id}", response_model=RelatedResponse)
    async def related_route(
        es: Annotated[AsyncElasticsearch, ES],
        s: Annotated[Settings, SETTINGS],
        doc_id: int,
        size: Annotated[int, Query(ge=1, le=50)] = 10,
    ):
        return await search.related(es, s, doc_id, size)

    @app.post("/plagiarism/check", response_model=PlagiarismResponse)
    async def plagiarism_route(
        es: Annotated[AsyncElasticsearch, ES],
        s: Annotated[Settings, SETTINGS],
        body: PlagiarismRequest,
    ):
        return await plagiarism.check(es, s, body.text, body.threshold, body.max_results)

    def _check_size(s: Settings, *texts: str) -> None:
        if sum(map(len, texts)) > s.reuse_max_chars:
            raise AppError(
                413, "TEXT_TOO_LARGE", f"request is limited to {s.reuse_max_chars} characters"
            )

    @app.post("/reuse/dedupe", response_model=DedupeResponse)
    async def dedupe_route(s: Annotated[Settings, SETTINGS], body: DedupeRequest):
        if len(body.documents) > s.reuse_max_docs:
            raise AppError(413, "TOO_MANY_DOCUMENTS", f"at most {s.reuse_max_docs} documents")
        _check_size(s, *(d.text for d in body.documents))
        threshold = s.dedupe_threshold if body.threshold is None else body.threshold
        return await asyncio.to_thread(reuse.dedupe, body.documents, threshold)

    @app.post("/reuse/drift", response_model=DriftResponse)
    async def drift_route(s: Annotated[Settings, SETTINGS], body: DriftRequest):
        _check_size(s, body.text, *(c.text for c in body.approved))
        threshold = s.drift_threshold if body.threshold is None else body.threshold
        return await asyncio.to_thread(reuse.drift, body.approved, body.text, threshold)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz(
        response: Response,
        es: Annotated[AsyncElasticsearch, ES],
        s: Annotated[Settings, SETTINGS],
    ):
        try:
            if not await es.options(request_timeout=2).ping():
                response.status_code = 503
                return HealthResponse(status="unhealthy", detail="elasticsearch ping failed")
            if not await es.indices.exists_alias(name=s.es_alias):
                response.status_code = 503
                return HealthResponse(status="unhealthy", detail=f"alias {s.es_alias} missing")
            count = (await es.count(index=s.es_alias))["count"]
            mapping = await es.indices.get_mapping(index=s.es_alias)
            meta = next(iter(mapping.values()))["mappings"].get("_meta", {})
            expected = {"winnow_k": K, "winnow_w": W, "hash_bits": HASH_BITS}
            if {k: meta.get(k) for k in expected} != expected:
                response.status_code = 503
                return HealthResponse(
                    status="unhealthy",
                    docs=count,
                    detail=f"index winnowing params {meta} != code {expected}",
                )
            return HealthResponse(status="ok", docs=count)
        except Exception as exc:  # health must answer, not raise
            response.status_code = 503
            return HealthResponse(status="unhealthy", detail=type(exc).__name__)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_route():
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
