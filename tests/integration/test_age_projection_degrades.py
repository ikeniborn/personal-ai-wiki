"""Regression: AGE projection on write paths must degrade (not hard-fail) when the graph
does not exist yet (e.g. domain flipped to engine=age before graph_rebuild).

The relational write (article title change) must succeed even if project_article raises
because the AGE graph schema is missing.
"""
from __future__ import annotations

import pytest
from tests.factories import _set_domain_engine_age, seed_article_with_entities

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.users import UserRepo
from paw.db.session import get_sessionmaker
from paw.security.passwords import hash_password
from paw.services.articles import ArticleService


@pytest.mark.usefixtures("wired_settings")
async def test_update_degrades_when_age_graph_missing() -> None:
    """ArticleService.update must commit the relational write even when the AGE graph
    does not exist and project_article would raise.
    """
    maker = get_sessionmaker()

    # Seed a domain + article, flip to engine=age — but do NOT create the AGE graph.
    async with maker() as s:
        domain_id, article_id = await seed_article_with_entities(s)
        await _set_domain_engine_age(s, domain_id)
        user = await UserRepo(s).create(
            email="author-deg@example.com",
            pw_hash=hash_password("pw12345"),
            role="viewer",
        )
        author_id = user.id
        await s.commit()

    # Calling update with a missing AGE graph must NOT raise; it should return the article.
    async with maker() as s:
        svc = ArticleService(s)
        art = await svc.update(
            article_id=article_id,
            expected_rev=1,
            title="Degraded",
            markdown="# Degraded\n\nbody",
            author_id=author_id,
        )
        assert art.title == "Degraded"

    # The relational write must have committed: title persisted in PG.
    async with maker() as s:
        repo = ArticleRepo(s)
        persisted = await repo.get(article_id)
        assert persisted is not None
        assert persisted.title == "Degraded"
