from paw.obs import readiness


async def test_readiness_reports_degraded_component(monkeypatch):
    class BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, _statement):
            raise RuntimeError("down")

    class RedisOk:
        async def ping(self):
            return True

    def fake_sessionmaker():
        return BoomSession

    monkeypatch.setattr(readiness, "get_sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(readiness, "get_redis", lambda: RedisOk())

    ok, components = await readiness.check_readiness()

    assert ok is False
    assert components["db"] == "error: RuntimeError"
    assert components["redis"] == "ok"
