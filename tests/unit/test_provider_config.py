from paw.providers.config import PROVIDER_KEY, WIKI_KEY, ProviderConfig, WikiConfig


def test_wiki_defaults():
    w = WikiConfig()
    assert w.hub_threshold == 2
    assert w.chunk_target_size == 800
    assert "related" in w.link_types
    assert w.max_steps == 12


def test_provider_config_roundtrip():
    pc = ProviderConfig(
        base_url="https://api.example/v1",
        api_key_enc="gAAAA-token",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=1536,
    )
    dumped = pc.model_dump()
    assert ProviderConfig.model_validate(dumped) == pc
    assert pc.vision_model is None


def test_keys_are_stable():
    assert PROVIDER_KEY == "provider"
    assert WIKI_KEY == "wiki"
