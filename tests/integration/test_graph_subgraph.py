from sqlalchemy import text


async def test_backlink_index_exists(db_session):
    res = await db_session.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_links_dst_article_id'")
    )
    assert res.scalar_one_or_none() == 1
