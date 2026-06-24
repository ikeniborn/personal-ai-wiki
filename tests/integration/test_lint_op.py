from datetime import UTC, datetime

from sqlalchemy import text

from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo
from paw.graph.repo import GraphRepo
from paw.harness.ops.lint import run_lint
from paw.providers.config import MaintenanceConfig
from paw.services.ingest_write import upsert_article


async def _plant(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    # intro -> links to a real [[tcp]] and a broken [[ghost]]
    intro, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="See [[tcp]] and [[ghost]].", summary="", author_id=None,
    )
    tcp, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="tcp", title="TCP",
        markdown="TCP body.", summary="", author_id=None,
    )
    # orphan: no links at all
    orphan, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="lonely", title="Lonely",
        markdown="No links.", summary="", author_id=None,
    )
    # a real link intro -> tcp so neither is an orphan
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=intro.id, dst_article_id=tcp.id, type="related"
    )
    # duplicate entities
    await EntityRepo(db_session).upsert(domain_id=dom.id, name="QUIC")
    await EntityRepo(db_session).upsert(domain_id=dom.id, name="quic")
    # make 'lonely' stale
    await db_session.execute(
        text("UPDATE articles SET updated_at = :t WHERE id = :i"),
        {"t": datetime(2024, 1, 1, tzinfo=UTC), "i": str(orphan.id)},
    )
    await db_session.commit()
    return dom


async def test_run_lint_reports_all_kinds_and_writes_nothing(db_session):
    dom = await _plant(db_session)
    before = (await db_session.execute(text("SELECT count(*) FROM article_revisions"))).scalar_one()

    result = await run_lint(
        db_session, domain_id=dom.id, cfg=MaintenanceConfig(stale_days=180),
        now=datetime(2026, 6, 23, tzinfo=UTC),
    )
    kinds = {i.kind for i in result.issues}
    assert {"broken_ref", "orphan", "stale", "duplicate_entity"} <= kinds
    broken = next(i for i in result.issues if i.kind == "broken_ref")
    assert broken.target_slug == "intro" and "ghost" in broken.detail
    orphan = next(i for i in result.issues if i.kind == "orphan")
    assert orphan.target_slug == "lonely"
    # ids are unique
    assert len({i.id for i in result.issues}) == len(result.issues)

    after = (await db_session.execute(text("SELECT count(*) FROM article_revisions"))).scalar_one()
    assert after == before  # read-only
