from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

registry = CollectorRegistry()

REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["route", "method", "status"], registry=registry
)
LATENCY = Histogram(
    "http_request_seconds",
    "HTTP request latency",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=registry,
)
ES_TOOK = Histogram(
    "es_took_ms",
    "Elasticsearch-reported took",
    ["op"],
    buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000),
    registry=registry,
)
ES_ERRORS = Counter("es_errors_total", "Elasticsearch call failures", ["op"], registry=registry)
PLAGIARISM_CANDIDATES = Histogram(
    "plagiarism_candidates",
    "Candidates returned by terms_set before re-ranking",
    buckets=(0, 1, 5, 10, 25, 50, 100),
    registry=registry,
)


def render() -> bytes:
    return generate_latest(registry)
