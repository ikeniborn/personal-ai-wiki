from tests.stubs import StubChatProvider

from paw.db.repos.domains import DomainRepo
from paw.harness.limits import Budget
from paw.harness.loop import run_loop
from paw.harness.tools import ToolContext, tools_for
from paw.providers.config import WikiConfig


async def test_loop_runs_tool_then_finishes(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    ctx = ToolContext(
        session=db_session, domain_id=dom.id, user_id=None, budget=Budget.from_wiki(WikiConfig())
    )
    chat = StubChatProvider(
        [
            StubChatProvider.tool("list_articles", {}),
            StubChatProvider.text("done"),
        ]
    )
    steps_seen: list[int] = []

    async def on_step(i: int, msg: str) -> None:
        steps_seen.append(i)

    res = await run_loop(
        chat, ctx, system="sys", task="do it", tools=tools_for("ingest"), on_step=on_step
    )
    assert res.final_text == "done"
    assert res.steps >= 2
    assert steps_seen  # progress emitted


async def test_loop_stops_at_step_limit(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    ctx = ToolContext(
        session=db_session,
        domain_id=dom.id,
        user_id=None,
        budget=Budget.from_wiki(WikiConfig(max_steps=1)),
    )
    # never returns plain text -> would loop forever without the guard
    chat = StubChatProvider(responder=lambda m, t: StubChatProvider.tool("list_articles", {}))
    res = await run_loop(chat, ctx, system="s", task="t", tools=tools_for("ingest"))
    assert res.final_text is None  # halted by max_steps
