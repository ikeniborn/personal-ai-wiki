import uuid

import pytest
from sqlalchemy import text

import paw.services.jobs as jobs_svc
from paw.api.errors import ProblemError
from paw.db.repos.domains import DomainRepo
from paw.services.jobs import JobService
from paw.services.provider_settings import ProviderSettingsService


async def _seed_provider(session) -> None:
    await ProviderSettingsService(session).set_provider(
        base_url="https://api.example/v1",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=8,
        api_key="sk-x",
    )


async def test_init_domain_creates_job_per_topic(db_session, wired_settings, monkeypatch):
    await _seed_provider(db_session)
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await db_session.commit()

    async def fake_plan(*, domain_name, brief, chat, cfg):
        assert domain_name == "net"  # the real domain name, not the UUID
        return ["QUIC", "TCP"]

    enqueued: list[dict] = []

    async def fake_enqueue(redis, **kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(jobs_svc, "build_structure_plan", fake_plan)
    monkeypatch.setattr(jobs_svc, "enqueue_ingest", fake_enqueue)

    pairs = await JobService(db_session).init_domain(domain_id=dom.id, brief="seed")

    assert [t for t, _ in pairs] == ["QUIC", "TCP"]
    n = await db_session.execute(
        text("SELECT count(*) FROM jobs WHERE domain_id=:d AND kind='ingest'"),
        {"d": str(dom.id)},
    )
    assert n.scalar_one() == 2
    assert len(enqueued) == 2
    assert {k["topic"] for k in enqueued} == {"QUIC", "TCP"}


async def test_init_domain_requires_provider(db_session, wired_settings):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    with pytest.raises(ProblemError) as exc:
        await JobService(db_session).init_domain(domain_id=dom.id, brief="seed")
    assert exc.value.status == 422


async def test_init_domain_unknown_domain(db_session, wired_settings):
    await _seed_provider(db_session)
    with pytest.raises(ProblemError) as exc:
        await JobService(db_session).init_domain(domain_id=uuid.uuid4(), brief="seed")
    assert exc.value.status == 404
