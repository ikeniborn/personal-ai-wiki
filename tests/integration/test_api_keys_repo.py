from paw.db.repos.api_keys import ApiKeyRepo
from paw.db.repos.users import UserRepo


async def _user(db_session):
    return await UserRepo(db_session).create(email="a@b.c", pw_hash="x", role="admin")


async def test_create_and_by_prefix(db_session):
    u = await _user(db_session)
    repo = ApiKeyRepo(db_session)
    k = await repo.create(user_id=u.id, prefix="abc12345", hash="h", scopes=["read"])
    await db_session.commit()
    rows = await repo.by_prefix("abc12345")
    assert [r.id for r in rows] == [k.id]
    assert rows[0].scopes == ["read"]
    assert await repo.by_prefix("missing") == []


async def test_list_for_user(db_session):
    u = await _user(db_session)
    repo = ApiKeyRepo(db_session)
    await repo.create(user_id=u.id, prefix="p1", hash="h", scopes=[])
    await repo.create(user_id=u.id, prefix="p2", hash="h", scopes=["read"])
    await db_session.commit()
    rows = await repo.list_for_user(u.id)
    assert {r.prefix for r in rows} == {"p1", "p2"}


async def test_revoke_is_idempotent_and_owner_scoped(db_session):
    u = await _user(db_session)
    other = await UserRepo(db_session).create(email="o@b.c", pw_hash="x", role="viewer")
    repo = ApiKeyRepo(db_session)
    k = await repo.create(user_id=u.id, prefix="p3", hash="h", scopes=["read"])
    await db_session.commit()

    assert await repo.revoke(k.id, other.id) is False  # not owner -> no change
    assert await repo.revoke(k.id, u.id) is True
    await db_session.commit()
    assert (await repo.by_prefix("p3"))[0].revoked_at is not None
    assert await repo.revoke(k.id, u.id) is False  # already revoked


async def test_touch_last_used(db_session):
    u = await _user(db_session)
    repo = ApiKeyRepo(db_session)
    k = await repo.create(user_id=u.id, prefix="p4", hash="h", scopes=["read"])
    await db_session.commit()
    await repo.touch_last_used(k.id)
    await db_session.commit()
    assert (await repo.by_prefix("p4"))[0].last_used is not None
