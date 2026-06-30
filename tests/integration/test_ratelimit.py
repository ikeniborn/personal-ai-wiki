import uuid

import pytest

from paw.api.deps import get_redis
from paw.security.ratelimit import LoginGuard, RateLimiter


async def test_rate_limiter_blocks_after_limit(wired_settings):
    rl = RateLimiter(get_redis())
    key = f"t:{uuid.uuid4()}"
    assert await rl.hit(key, limit=2, window_seconds=60) is True
    assert await rl.hit(key, limit=2, window_seconds=60) is True
    assert await rl.hit(key, limit=2, window_seconds=60) is False


async def test_login_guard_locks_then_resets(wired_settings):
    g = LoginGuard(get_redis(), threshold=3, lock_seconds=60)
    key = f"u:{uuid.uuid4()}"
    assert await g.is_locked(key) is False
    for _ in range(3):
        await g.record_failure(key)
    assert await g.is_locked(key) is True
    await g.reset(key)
    assert await g.is_locked(key) is False


async def test_login_guard_first_failure_has_ttl(wired_settings):
    redis = get_redis()
    g = LoginGuard(redis, threshold=3, lock_seconds=60, fail_window_seconds=120)
    key = f"u:{uuid.uuid4()}"

    await g.record_failure(key)

    ttl = await redis.ttl(f"loginfail:{key}")
    assert ttl > 0


async def test_rate_limiter_rejects_invalid_limits(wired_settings):
    rl = RateLimiter(get_redis())
    key = f"t:{uuid.uuid4()}"

    with pytest.raises(ValueError, match="limit"):
        await rl.hit(key, limit=0, window_seconds=60)
    with pytest.raises(ValueError, match="window_seconds"):
        await rl.hit(key, limit=1, window_seconds=0)


def test_login_guard_rejects_invalid_limits(wired_settings):
    redis = get_redis()

    with pytest.raises(ValueError, match="threshold"):
        LoginGuard(redis, threshold=0, lock_seconds=60)
    with pytest.raises(ValueError, match="lock_seconds"):
        LoginGuard(redis, threshold=1, lock_seconds=0)
    with pytest.raises(ValueError, match="fail_window_seconds"):
        LoginGuard(redis, threshold=1, lock_seconds=60, fail_window_seconds=0)
