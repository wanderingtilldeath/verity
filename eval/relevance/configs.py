"""The relevance configurations compared in the README. All run on the experiment index."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.search import build_query

Builder = Callable[[str], dict[str, Any]]


def tuned_id(k1: float, b: float) -> str:
    return f"t_{k1}_{b}".replace(".", "p")


def config_1_default(q: str) -> dict:
    """Standard analyzer, one field."""
    return {"match": {"text.std": q}}


def config_2_english(q: str) -> dict:
    """Built-in English analyzer, one field. Isolates the analyzer effect from field weighting."""
    return {"match": {"text.english": q}}


def config_3_production(q: str) -> dict:
    """The production query: custom analyzer, title^4, title phrase boost, exact-title boost."""
    return build_query(q)


def config_4_tuned(k1: float, b: float) -> Builder:
    """Production query shape on subfields that differ only in BM25 k1/b."""
    tid = tuned_id(k1, b)
    return lambda q: build_query(q, f"title.{tid}", f"text.{tid}")


def config_5_title_only(q: str) -> dict:
    """Optional ablation: how much do titles alone carry?"""
    return {"match": {"title": q}}


FIXED: dict[str, Builder] = {
    "1_default_single_field": config_1_default,
    "2_english_analyzer": config_2_english,
    "3_production": config_3_production,
    "5_title_only": config_5_title_only,
}
