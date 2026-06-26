from httpx import ASGITransport, AsyncClient

from paw.main import create_app
from paw.obs import metrics


def _sample(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


async def test_known_route_uses_template_label():
    app = create_app()
    before = _sample(metrics.HTTP_REQUESTS, method="GET", route="/health", status="200")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/health")
    after = _sample(metrics.HTTP_REQUESTS, method="GET", route="/health", status="200")
    assert after == before + 1


async def test_unmatched_route_is_bucketed():
    app = create_app()
    before = _sample(metrics.HTTP_REQUESTS, method="GET", route="<unmatched>", status="404")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/api/v1/domains/does-not-exist-zzz/nope")
    after = _sample(metrics.HTTP_REQUESTS, method="GET", route="<unmatched>", status="404")
    assert after >= before + 1
