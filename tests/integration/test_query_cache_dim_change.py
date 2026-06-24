from sqlalchemy import text

from paw.db.managed import ensure_query_cache_embedding_column, query_cache_embedding_dim
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService

_FERNET = "k" * 43 + "="


async def test_dim_change_clears_and_rebuilds_query_cache(db_session):
    box = SecretBox(_FERNET)
    psvc = ProviderSettingsService(db_session, box=box)
    # initial provider at dim 4 + a cache row at dim 4
    await psvc.set_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    await ensure_query_cache_embedding_column(db_session, 4)
    await QueryCacheRepo(db_session).upsert(
        domain_id=dom.id, query_norm="q", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    assert await query_cache_embedding_dim(db_session) == 4

    # change dim -> chunks rebuild + cache cleared & rebuilt at the new dim
    await psvc.update_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    assert await query_cache_embedding_dim(db_session) == 8
    remaining = (await db_session.execute(text("SELECT count(*) FROM query_cache"))).scalar_one()
    assert remaining == 0  # stale-dim answers cleared
