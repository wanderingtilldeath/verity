from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    es_url: str = "http://localhost:9200"
    es_alias: str = "articles"

    # Every ES call carries both: a client-side request timeout and a server-side `timeout`.
    es_request_timeout_s: float = 10.0
    search_timeout_s: float = 5.0

    max_page_size: int = 100
    max_result_window: int = 10_000
    pit_keep_alive: str = "1m"
    allow_explain: bool = False
    related_min_doc_freq: int = 3  # ignore very rare terms in more_like_this
    related_min_should_match: str = "30%"  # of 25 query terms

    # Plagiarism
    plagiarism_max_chars: int = 50_000
    plagiarism_min_words: int = 8
    plagiarism_threshold: float = 0.3
    plagiarism_min_frac: float = (
        0.05  # terms_set: share of query fingerprints a candidate must match
    )
    plagiarism_candidates: int = 50
    plagiarism_max_results: int = 10

    # Reuse (dedupe / drift) over caller-supplied documents
    reuse_max_docs: int = 500
    reuse_max_chars: int = 200_000  # total across the request
    dedupe_threshold: float = 0.6  # Jaccard of fingerprint sets
    drift_threshold: float = 0.3


@lru_cache
def get_settings() -> Settings:
    return Settings()
