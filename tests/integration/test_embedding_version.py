from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def test_get_and_bump_embedding_version(db_session, wired_settings):
    from paw.config import get_settings

    box = SecretBox(get_settings().fernet_key)
    svc = ProviderSettingsService(db_session, box=box)
    assert await svc.get_embedding_version() == 1  # default
    nxt = await svc.bump_embedding_version()
    await db_session.commit()
    assert nxt == 2
    assert await svc.get_embedding_version() == 2


async def test_update_provider_dim_change_bumps_version(db_session, wired_settings):
    from paw.config import get_settings

    box = SecretBox(get_settings().fernet_key)
    svc = ProviderSettingsService(db_session, box=box)
    # establish a provider at dim 8 (creates the embedding column at 8)
    await svc.update_provider(
        base_url="http://x", chat_model="c", embedding_model="e",
        embedding_dim=8, api_key="k",
    )
    assert await svc.get_embedding_version() == 1
    # change the dim -> rebuild + version bump
    await svc.update_provider(
        base_url="http://x", chat_model="c", embedding_model="e",
        embedding_dim=16, api_key="k",
    )
    assert await svc.get_embedding_version() == 2
