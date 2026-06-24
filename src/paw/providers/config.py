from __future__ import annotations

from pydantic import BaseModel, Field

PROVIDER_KEY = "provider"
WIKI_KEY = "wiki"
RETRIEVAL_KEY = "retrieval"
CHAT_KEY = "chat"
GRAPH_KEY = "graph"
MAINTENANCE_KEY = "maintenance"
EMBEDDING_KEY = "embedding"


class ProviderConfig(BaseModel):
    base_url: str
    api_key_enc: str  # Fernet token (SecretBox.encrypt output)
    chat_model: str
    embedding_model: str
    vision_model: str | None = None
    embedding_dim: int


class WikiConfig(BaseModel):
    gen_language: str = "en"
    reasoning_language: str = "en"
    chunk_target_size: int = 800
    chunk_overlap_sentences: int = 1
    hub_threshold: int = 2
    max_steps: int = 12
    token_budget: int = 100_000
    max_writes: int = 20
    max_tool_calls: int = 40
    link_types: list[str] = Field(
        default_factory=lambda: ["related", "prerequisite", "part_of", "see_also"]
    )
    request_timeout_s: int = 60
    max_retries: int = 3


class RetrievalConfig(BaseModel):
    k1: int = 20  # vector arm: ANN candidates
    k2: int = 20  # fts arm: FTS candidates
    top_n: int = 8  # fused seed passages kept after RRF
    rrf_k: int = 60  # RRF constant: score = Σ weight_i / (rrf_k + rank_i)
    vector_weight: float = 1.0
    fts_weight: float = 1.0
    bfs_depth: int = 1  # outgoing-link expansion depth from seeds
    context_token_budget: int = 3000  # ~len/4 token estimate for assembled context
    entity_boost: float = 0.5  # added to fused score of chunks tagged with a query entity
    # Must match the regconfig used to build chunks.tsv (Phase 2 uses 'english').
    fts_regconfig: str = "english"


class ChatConfig(BaseModel):
    history_depth: int = 10  # last N turns folded into the chat prompt
    retention_max_sessions: int = 50  # keep newest N sessions per user
    retention_max_age_days: int = 90  # prune sessions inactive longer than this


class GraphConfig(BaseModel):
    default_depth: int = 2  # neighbourhood depth for graph view
    max_depth: int = 4  # hard ceiling the endpoint clamps requested depth to
    link_types: list[str] = Field(
        default_factory=lambda: ["related", "parent", "child", "references", "depends_on"]
    )


class MaintenanceConfig(BaseModel):
    enabled_ops: list[str] = Field(
        default_factory=lambda: ["lint", "fix", "format", "reindex"]
    )
    reindex_batch_size: int = 128  # chunks re-embedded per batch
    stale_days: int = 180  # an article older than this (no update) is flagged stale


class EmbeddingConfig(BaseModel):
    version: int = 1  # the embedding_version search filters on; bumped on a model/dim change
