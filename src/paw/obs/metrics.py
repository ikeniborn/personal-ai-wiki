from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# --- api RED ---
HTTP_REQUESTS = Counter(
    "paw_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "paw_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)
HTTP_INFLIGHT = Gauge("paw_http_inflight", "In-flight HTTP requests")
SSE_ACTIVE = Gauge("paw_sse_active", "Active SSE streams")

# --- worker / arq ---
JOB_TOTAL = Counter("paw_job_total", "arq jobs by kind+status", ["kind", "status"])
JOB_DURATION = Histogram("paw_job_duration_seconds", "arq job duration", ["kind"])
JOB_RETRIES = Counter("paw_job_retries_total", "arq job retries", ["kind"])
JOB_DEADLETTER = Counter("paw_job_deadletter_total", "arq jobs that exhausted retries", ["kind"])
JOB_LOCK_WAIT = Histogram("paw_job_lock_wait_seconds", "model-lock acquisition wait", ["kind"])
QUEUE_DEPTH = Gauge("paw_queue_depth", "arq queue depth")

# --- domain / LLM ---
LLM_TOKENS = Counter("paw_llm_tokens_total", "LLM tokens", ["op", "direction"])
LLM_COST = Counter("paw_llm_cost_usd_total", "LLM cost in USD", ["op", "model"])
LLM_LATENCY = Histogram("paw_llm_latency_seconds", "LLM call latency", ["op"])
LLM_ERRORS = Counter("paw_llm_errors_total", "LLM call errors", ["op"])
EMBEDDINGS = Counter("paw_embeddings_total", "embeddings generated")
ARTICLES = Counter("paw_articles_total", "articles written by ingest")
CHUNKS = Counter("paw_chunks_total", "chunks written by ingest")
CACHE_HITS = Counter("paw_cache_hits_total", "query cache lookups", ["result"])  # hit|miss


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
