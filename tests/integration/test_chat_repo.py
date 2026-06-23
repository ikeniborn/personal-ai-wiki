import uuid

from paw.db.repos.chat import ChatRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


async def _user(db_session, email):
    return await UserRepo(db_session).create(
        email=email, pw_hash=hash_password("pw12345"), role="viewer"
    )


async def test_message_order_user_before_assistant(db_session):
    # user + assistant inserted in one transaction share now(); ordering must keep user first.
    u = await _user(db_session, "a@example.com")
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = ChatRepo(db_session)
    sess = await repo.create_session(user_id=u.id, domain_id=dom.id)
    await repo.add_message(session_id=sess.id, role="user", content="q1", meta={})
    await repo.add_message(session_id=sess.id, role="assistant", content="a1", meta={"refs": []})
    await db_session.commit()
    msgs = await repo.list_messages(sess.id)
    assert [(m.role, m.content) for m in msgs] == [("user", "q1"), ("assistant", "a1")]
    assert await repo.count_messages(sess.id) == 2


async def test_title_and_bump(db_session):
    u = await _user(db_session, "b@example.com")
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s", wiki_prefix="w")
    repo = ChatRepo(db_session)
    sess = await repo.create_session(user_id=u.id, domain_id=dom.id)
    before = sess.last_active_at
    await repo.set_title(sess.id, "My title")
    await db_session.commit()
    await repo.bump_last_active(sess.id)
    await db_session.commit()
    refreshed = await repo.get(sess.id)
    assert refreshed.title == "My title"
    assert refreshed.last_active_at >= before


async def test_list_by_user_keyset_pagination(db_session):
    u = await _user(db_session, "c@example.com")
    dom = await DomainRepo(db_session).create(name="d3", source_prefix="s", wiki_prefix="w")
    repo = ChatRepo(db_session)
    sessions = []
    for _ in range(3):
        s = await repo.create_session(user_id=u.id, domain_id=dom.id)
        await repo.bump_last_active(s.id)  # distinct last_active_at per commit
        await db_session.commit()
        sessions.append(await repo.get(s.id))
    page1 = await repo.list_by_user(u.id, limit=2)
    assert len(page1) == 2
    cursor = (page1[-1].last_active_at.isoformat(), str(page1[-1].id))
    page2 = await repo.list_by_user(u.id, limit=2, cursor=cursor)
    seen = {s.id for s in page1} | {s.id for s in page2}
    assert seen == {s.id for s in sessions}
    assert len(page2) == 1  # no overlap


async def test_list_for_gc_and_delete_by_ids(db_session):
    u = await _user(db_session, "d@example.com")
    dom = await DomainRepo(db_session).create(name="d4", source_prefix="s", wiki_prefix="w")
    repo = ChatRepo(db_session)
    s1 = await repo.create_session(user_id=u.id, domain_id=dom.id)
    s2 = await repo.create_session(user_id=u.id, domain_id=dom.id)
    await db_session.commit()
    rows = await repo.list_for_gc(u.id)
    assert {sid for sid, _ in rows} == {s1.id, s2.id}
    await repo.delete_by_ids([s1.id])
    await db_session.commit()
    assert {sid for sid, _ in await repo.list_for_gc(u.id)} == {s2.id}
    assert await repo.get(uuid.uuid4()) is None
