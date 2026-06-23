import uuid

from paw.services.cache_seam import mark_domain_cache_stale


async def test_seam_is_a_noop_and_returns_none():
    # Passing None for the session proves the Phase 6 seam touches no DB.
    result = await mark_domain_cache_stale(None, uuid.uuid4())  # type: ignore[arg-type]
    assert result is None
