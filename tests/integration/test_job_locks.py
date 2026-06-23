import pytest

from paw.jobs.locks import domain_lock, model_lock


async def test_domain_lock_blocks_second(redis_client):
    async with domain_lock(redis_client, "dom-1") as got1:
        assert got1 is True
        async with domain_lock(redis_client, "dom-1") as got2:
            assert got2 is False  # already held -> second job rejected
    # released after the with-block
    async with domain_lock(redis_client, "dom-1") as got3:
        assert got3 is True


async def test_model_lock_serializes(redis_client):
    async with model_lock(redis_client, "gpt-x"):
        with pytest.raises(TimeoutError):
            async with model_lock(redis_client, "gpt-x", timeout=0.2):
                pass
    # different model is independent
    async with model_lock(redis_client, "other-model", timeout=0.2):
        pass
