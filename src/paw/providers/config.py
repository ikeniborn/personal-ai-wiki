from __future__ import annotations

from pydantic import BaseModel, Field

PROVIDER_KEY = "provider"
WIKI_KEY = "wiki"
RETRIEVAL_KEY = "retrieval"
CHAT_KEY = "chat"


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
