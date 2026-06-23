from sqlalchemy import select

from paw.db.models import ChatMessage, ChatSession
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


async def _seed_user_domain(db_session):
    user = await UserRepo(db_session).create(
        email="u@example.com", pw_hash=hash_password("pw12345"), role="viewer"
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    return user, dom


async def test_session_and_messages_roundtrip(db_session):
    user, dom = await _seed_user_domain(db_session)
    sess = ChatSession(user_id=user.id, domain_id=dom.id, title="hello")
    db_session.add(sess)
    await db_session.flush()
    db_session.add(ChatMessage(session_id=sess.id, role="user", content="hi", meta={}))
    db_session.add(
        ChatMessage(session_id=sess.id, role="assistant", content="hello [a]", meta={"refs": []})
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(ChatMessage).where(ChatMessage.session_id == sess.id)
        )
    ).scalars().all()
    assert {r.role for r in rows} == {"user", "assistant"}
    assert sess.last_active_at is not None  # server default now()


async def test_cascade_delete_messages(db_session):
    user, dom = await _seed_user_domain(db_session)
    sess = ChatSession(user_id=user.id, domain_id=dom.id)
    db_session.add(sess)
    await db_session.flush()
    db_session.add(ChatMessage(session_id=sess.id, role="user", content="hi", meta={}))
    await db_session.commit()
    await db_session.delete(sess)
    await db_session.commit()
    remaining = (await db_session.execute(select(ChatMessage))).scalars().all()
    assert remaining == []
