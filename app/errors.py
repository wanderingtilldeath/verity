"""One error shape for the whole API, plus the mapping from Elasticsearch failures."""

from __future__ import annotations

import logging

from elasticsearch import (
    ApiError,
    BadRequestError,
    ConnectionTimeout,
    NotFoundError,
)
from elasticsearch import (
    ConnectionError as ESConnectionError,
)
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import ErrorResponse

log = logging.getLogger("app.errors")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def _response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(
        code=code, message=message, request_id=getattr(request.state, "request_id", None)
    )
    return JSONResponse(status_code=status, content=body.model_dump())


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        return _response(request, 422, "VALIDATION_ERROR", f"{loc}: {first.get('msg', 'invalid')}")

    @app.exception_handler(ConnectionTimeout)
    async def _timeout(request: Request, exc: ConnectionTimeout):
        return _response(request, 504, "SEARCH_TIMEOUT", "Search backend timed out")

    @app.exception_handler(ESConnectionError)
    async def _conn(request: Request, exc: ESConnectionError):
        return _response(request, 503, "SEARCH_UNAVAILABLE", "Search backend is unavailable")

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return _response(request, 404, "NOT_FOUND", "Resource not found")

    @app.exception_handler(BadRequestError)
    async def _bad_request(request: Request, exc: BadRequestError):
        log.warning("ES rejected query: %s", exc.message)
        return _response(request, 400, "BAD_QUERY", "Query was rejected by the search backend")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        log.error("ES error %s: %s", exc.status_code, exc.message)
        return _response(request, 502, "SEARCH_ERROR", "Search backend error")
