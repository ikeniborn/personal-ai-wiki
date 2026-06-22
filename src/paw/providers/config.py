from __future__ import annotations

from pydantic import BaseModel, Field

PROVIDER_KEY = "provider"
WIKI_KEY = "wiki"


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
