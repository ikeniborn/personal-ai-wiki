import uuid

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
