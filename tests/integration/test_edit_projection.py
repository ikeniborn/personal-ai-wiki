"""Tests for in-transaction AGE projection during article edit/rollback (Task 11)."""
from __future__ import annotations

import pytest

from paw.db.repos.users import UserRepo
from paw.db.session import get_sessionmaker
from paw.graph.age import schema
from paw.graph.age.cypher import run_cypher
from paw.graph.age.naming import graph_name
from paw.security.passwords import hash_password
from paw.services.articles import ArticleService
from tests.factories import _set_domain_engine_age, seed_article_with_entities


@pytest.mark.usefixtures("wired_settings")
async def test_edit_reprojects_title_when_engine_age() -> None:
    maker = get_sessionmaker()

    # Seed domain, article, and a user to act as author.
    async with maker() as s:
        domain_id, article_id = await seed_article_with_entities(s)
        await schema.ensure_graph(s, domain_id)
        await _set_domain_engine_age(s, domain_id)
        user = await UserRepo(s).create(
            email="author@example.com", pw_hash=hash_password("pw12345"), role="viewer"
        )
        author_id = user.id
        await s.commit()

    # Edit the article — service must re-project the AGE node in the same transaction.
    async with maker() as s:
        svc = ArticleService(s)
        await svc.update(
            article_id=article_id,
            expected_rev=1,
            title="Beta",
            markdown="# Beta\n\nbody",
            author_id=author_id,
        )

    # Verify the AGE Article node reflects the new title.
    async with maker() as s:
        rows = await run_cypher(
            s,
            graph=graph_name(domain_id),
            body="MATCH (a:Article {id: $id}) RETURN a.title",
            columns="title agtype",
            params={"id": str(article_id)},
        )
        assert rows == [("Beta",)]
        # Cleanup: drop the AGE graph so _clean_db teardown isn't confused.
        await schema.drop_graph(s, domain_id)
        await s.commit()
