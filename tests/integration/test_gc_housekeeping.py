from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from paw.db.repos.chat import ChatRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.settings import SettingsRepo
from paw.db.repos.users import UserRepo
from paw.jobs.tasks import gc_housekeeping
from paw.providers.config import CHAT_KEY
from paw.security.passwords import hash_password


async def _aged_session(db_session, *, user_id, domain_id, days_old):
    repo = ChatRepo(db_session)
    sess = await repo.create_session(user_id=user_id, domain_id=domain_id)
    await db_session.commit()
    when = datetime.now(UTC) - timedelta(days=days_old)
    await db_session.execute(
        text("UPDATE chat_sessions SET last_active_at = :w WHERE id = :i"),
        {"w": when, "i": str(sess.id)},
    )
    await db_session.commit()
    return sess


async def test_gc_prunes_aged_sessions_global_default(db_session, wired_settings):
    # global default max_age_days = 90; one session is 100 days old -> pruned
    user = await UserRepo(db_session).create(
        email="a@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    fresh = await _aged_session(db_session, user_id=user.id, domain_id=dom.id, days_old=1)
    aged = await _aged_session(db_session, user_id=user.id, domain_id=dom.id, days_old=100)

    out = await gc_housekeeping({})
    assert out == "gc:1"
    remaining = {sid for sid, _ in await ChatRepo(db_session).list_for_gc(user.id)}
    assert fresh.id in remaining and aged.id not in remaining


async def test_gc_respects_per_user_max_sessions_override(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="b@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    # per-user override: keep only the newest 1 session
    await db_session.execute(
        text("UPDATE users SET chat_prefs = :p WHERE id = :i"),
        {"p": '{"retention": {"max_sessions": 1}}', "i": str(user.id)},
    )
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s", wiki_prefix="w")
    old = await _aged_session(db_session, user_id=user.id, domain_id=dom.id, days_old=3)
    new = await _aged_session(db_session, user_id=user.id, domain_id=dom.id, days_old=1)
    await db_session.commit()

    out = await gc_housekeeping({})
    assert out == "gc:1"
    remaining = {sid for sid, _ in await ChatRepo(db_session).list_for_gc(user.id)}
    assert new.id in remaining and old.id not in remaining


async def test_gc_global_settings_row_applies(db_session, wired_settings):
    # set a global chat retention of max_age_days=2 via app_settings
    await SettingsRepo(db_session).upsert(
        {CHAT_KEY: {"history_depth": 10, "retention_max_sessions": 50, "retention_max_age_days": 2}}
    )
    user = await UserRepo(db_session).create(
        email="c@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    dom = await DomainRepo(db_session).create(name="d3", source_prefix="s", wiki_prefix="w")
    aged = await _aged_session(db_session, user_id=user.id, domain_id=dom.id, days_old=5)
    await db_session.commit()

    out = await gc_housekeeping({})
    assert out == "gc:1"
    assert aged.id not in {sid for sid, _ in await ChatRepo(db_session).list_for_gc(user.id)}
