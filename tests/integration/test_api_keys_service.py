import pytest

from paw.api.errors import ProblemError
from paw.db.repos.users import UserRepo
from paw.services.api_keys import ApiKeyService


async def _user(db_session, email="a@b.c"):
    return await UserRepo(db_session).create(email=email, pw_hash="x", role="admin")


async def test_issue_then_authenticate(db_session):
    u = await _user(db_session)
    await db_session.commit()
    svc = ApiKeyService(db_session)
    issued = await svc.issue(user_id=u.id, scopes=["read"])
    assert issued.token.startswith("paw_")

    authed = await svc.authenticate(f"Bearer {issued.token}")
    assert authed is not None
    assert authed.user_id == u.id
    assert authed.scopes == ["read"]


async def test_issue_rejects_unknown_scope(db_session):
    u = await _user(db_session)
    await db_session.commit()
    with pytest.raises(ProblemError) as ei:
        await ApiKeyService(db_session).issue(user_id=u.id, scopes=["write"])
    assert ei.value.status == 422


async def test_authenticate_rejects_unknown_and_revoked(db_session):
    u = await _user(db_session)
    await db_session.commit()
    svc = ApiKeyService(db_session)
    assert await svc.authenticate("Bearer paw_deadbeef.nope") is None
    assert await svc.authenticate(None) is None

    issued = await svc.issue(user_id=u.id, scopes=["read"])
    await svc.revoke(user_id=u.id, key_id=issued.id)
    assert await svc.authenticate(f"Bearer {issued.token}") is None


async def test_authenticate_rejects_wrong_secret(db_session):
    u = await _user(db_session)
    await db_session.commit()
    svc = ApiKeyService(db_session)
    issued = await svc.issue(user_id=u.id, scopes=["read"])
    prefix = issued.token.removeprefix("paw_").split(".")[0]
    assert await svc.authenticate(f"Bearer paw_{prefix}.tampered") is None


async def test_revoke_missing_raises_404(db_session):
    import uuid

    u = await _user(db_session)
    await db_session.commit()
    with pytest.raises(ProblemError) as ei:
        await ApiKeyService(db_session).revoke(user_id=u.id, key_id=uuid.uuid4())
    assert ei.value.status == 404
