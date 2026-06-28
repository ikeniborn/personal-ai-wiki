from __future__ import annotations

from paw.providers.config import ProviderConfig
from paw.providers.openai_compat import OpenAICompatProvider
from paw.security.secrets import SecretBox


def build_chat_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url=pc.base_url,
        api_key=box.decrypt(pc.api_key_enc),
        chat_model=pc.chat_model,
        embedding_model=pc.embedding_model,
    )


def build_embedding_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider:
    # Same provider type; embed() defaults to pc.embedding_model.
    return build_chat_provider(pc, box)


def build_vision_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider | None:
    if not pc.vision_model:
        return None
    return OpenAICompatProvider(
        base_url=pc.base_url,
        api_key=box.decrypt(pc.api_key_enc),
        chat_model=pc.chat_model,
        embedding_model=pc.embedding_model,
        vision_model=pc.vision_model,
    )
