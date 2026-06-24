import uuid

from paw.services.cache_seam import mark_cache_stale


async def test_empty_article_ids_is_a_noop():
    # No article ids -> early return, never touches the session.
    result = await mark_cache_stale(None, domain_id=uuid.uuid4(), article_ids=[])  # type: ignore[arg-type]
    assert result is None
