from __future__ import annotations

from paw.providers.config import WikiConfig


class LimitExceeded(Exception):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"limit exceeded: {kind}")


class Budget:
    def __init__(
        self, *, max_steps: int, max_tool_calls: int, max_writes: int, token_budget: int
    ) -> None:
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls
        self._max_writes = max_writes
        self._token_budget = token_budget
        self.steps = 0
        self.tool_calls = 0
        self.writes = 0
        self.tokens = 0
        self._signatures: set[str] = set()

    @classmethod
    def from_wiki(cls, cfg: WikiConfig) -> Budget:
        return cls(
            max_steps=cfg.max_steps,
            max_tool_calls=cfg.max_tool_calls,
            max_writes=cfg.max_writes,
            token_budget=cfg.token_budget,
        )

    def step(self) -> None:
        self.steps += 1
        if self.steps > self._max_steps:
            raise LimitExceeded("max_steps")

    def tool_call(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > self._max_tool_calls:
            raise LimitExceeded("max_tool_calls")

    def write(self) -> None:
        self.writes += 1
        if self.writes > self._max_writes:
            raise LimitExceeded("max_writes")

    def add_tokens(self, n: int) -> None:
        if self.tokens >= self._token_budget:
            raise LimitExceeded("token_budget")
        self.tokens += n

    def seen(self, signature: str) -> bool:
        if signature in self._signatures:
            return True
        self._signatures.add(signature)
        return False
