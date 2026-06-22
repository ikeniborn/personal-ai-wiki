from cryptography.fernet import Fernet

from paw.providers.config import WikiConfig
from paw.providers.factory import build_chat_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def test_set_and_get_provider_encrypts_key(db_session):
    svc = ProviderSettingsService(db_session)
    pc = await svc.set_provider(
        base_url="https://api.example/v1",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=1536,
        api_key="sk-secret-123",
    )
    # stored key is encrypted, not plaintext
    assert pc.api_key_enc != "sk-secret-123"
    got = await svc.get_provider()
    assert got is not None
    assert got.chat_model == "gpt-x"
    assert got.embedding_dim == 1536


async def test_get_wiki_returns_defaults_when_unset(db_session):
    svc = ProviderSettingsService(db_session)
    wiki = await svc.get_wiki()
    assert wiki == WikiConfig()


async def test_build_chat_provider_decrypts_key(db_session):
    key = Fernet.generate_key().decode()
    box = SecretBox(key)
    svc = ProviderSettingsService(db_session, box=box)
    pc = await svc.set_provider(
        base_url="https://api.example/v1",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=8,
        api_key="sk-secret-123",
    )
    provider = build_chat_provider(pc, box)
    assert provider.chat_model == "gpt-x"
