from __future__ import annotations

from pydantic import BaseModel

from paw.harness.prompts import get_prompt
from paw.providers.base import ChatProvider, Message
from paw.providers.config import WikiConfig


class StructurePlan(BaseModel):
    topics: list[str]


async def build_structure_plan(
    *, domain_name: str, brief: str, chat: ChatProvider, cfg: WikiConfig
) -> list[str]:
    system = get_prompt(
        "init", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )
    plan = await chat.structured(  # type: ignore[attr-defined]
        [
            Message(role="system", content=system),
            Message(role="user", content=f"DOMAIN: {domain_name}\nBRIEF: {brief}"),
        ],
        StructurePlan,
        retries=cfg.max_retries,
    )
    return list(dict.fromkeys(t.strip() for t in plan.topics if t.strip()))
