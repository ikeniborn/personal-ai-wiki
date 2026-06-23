from __future__ import annotations

from dataclasses import dataclass

from paw.harness.prompts import get_prompt
from paw.harness.retrieve import Passage, Ref, RetrievedContext
from paw.providers.base import Message
from paw.providers.config import WikiConfig

DONT_KNOW = "I don't have enough information in this domain to answer that."


@dataclass(frozen=True)
class QueryAnswer:
    answer_md: str
    refs: list[Ref]
    passages: list[Passage]


def build_messages(question: str, ctx: RetrievedContext, wiki: WikiConfig) -> list[Message]:
    system = get_prompt(
        "query", gen_language=wiki.gen_language, reasoning_language=wiki.reasoning_language
    )
    user = f"QUESTION:\n{question}\n\n{ctx.prompt_block}"
    return [Message(role="system", content=system), Message(role="user", content=user)]


def to_answer(answer_md: str, ctx: RetrievedContext) -> QueryAnswer:
    return QueryAnswer(answer_md=answer_md, refs=ctx.refs, passages=ctx.passages)


def dont_know() -> QueryAnswer:
    return QueryAnswer(answer_md=DONT_KNOW, refs=[], passages=[])
