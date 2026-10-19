from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class Hit(BaseModel):
    id: int
    title: str
    url: str | None = None
    score: float | None = None
    snippets: list[str] = []
    title_highlight: str | None = None
    explanation: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    query: str
    total: int
    total_is_exact: bool
    took_ms: int
    timed_out: bool = False
    hits: list[Hit]
    # Opaque cursor. Present while more results exist; pass it back as `cursor`.
    next: str | None = None


class SuggestResponse(BaseModel):
    completions: list[str]
    did_you_mean: str | None = None
    took_ms: int


class RelatedResponse(BaseModel):
    id: int
    took_ms: int
    hits: list[Hit]


class PlagiarismRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_results: int | None = Field(default=None, ge=1, le=50)


class Span(BaseModel):
    start: int  # character offsets
    end: int
    text: str


class PassageMatch(BaseModel):
    input: Span
    source: Span
    words: int


class PlagiarismMatch(BaseModel):
    id: int
    title: str
    url: str | None = None
    containment: float
    jaccard: float
    passages: list[PassageMatch]


class PlagiarismResponse(BaseModel):
    input_words: int
    input_fingerprints: int
    candidates: int
    took_ms: int
    threshold: float
    matches: list[PlagiarismMatch]


class HealthResponse(BaseModel):
    status: str
    docs: int | None = None
    detail: str | None = None
