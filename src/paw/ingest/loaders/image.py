from __future__ import annotations

from paw.providers.base import VisionProvider


async def describe_image(data: bytes, vision: VisionProvider, *, prompt: str) -> str:
    return await vision.describe(data, prompt=prompt)
