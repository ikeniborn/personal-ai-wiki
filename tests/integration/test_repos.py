from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo


async def test_user_create_and_get_by_email(db_session):
    repo = UserRepo(db_session)
    u = await repo.create(email="a@example.com", pw_hash="x", role="admin")
    await db_session.commit()
    got = await repo.get_by_email("a@example.com")
    assert got is not None and got.id == u.id and got.role == "admin"


async def test_domain_create_and_list(db_session):
    repo = DomainRepo(db_session)
    await repo.create(name="net", source_prefix="src/net", wiki_prefix="wiki/net")
    await db_session.commit()
    items = await repo.list()
    assert any(d.name == "net" for d in items)
