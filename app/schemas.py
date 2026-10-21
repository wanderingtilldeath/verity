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


class TextDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class DedupeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[TextDoc] = Field(min_length=2)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class PairScore(BaseModel):
    a: str
    b: str
    jaccard: float


class DedupeCluster(BaseModel):
    canonical: str  # suggested one to keep: the longest member
    members: list[str]
    pairs: list[PairScore]


class DedupeResponse(BaseModel):
    documents: int
    clusters: list[DedupeCluster]


class DriftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: list[TextDoc] = Field(min_length=1)
    text: str = Field(min_length=1)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DriftSection(BaseModel):
    start: int  # character offsets of the section (paragraph) in `text`
    end: int
    status: str  # unchanged | drifted | new
    clause_id: str | None = None  # best-matching approved clause (not set for "new")
    containment: float
    drift: float  # 1 - containment
    passages: list[PassageMatch] = []  # drifted sections only; offsets are in `text` / the clause


class DriftResponse(BaseModel):
    sections: list[DriftSection]


class HealthResponse(BaseModel):
    status: str
    docs: int | None = None
    detail: str | None = None
