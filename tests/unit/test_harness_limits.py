import pytest

from paw.harness.limits import Budget, LimitExceeded
from paw.providers.config import WikiConfig


def test_step_limit():
    b = Budget.from_wiki(WikiConfig(max_steps=2))
    b.step()
    b.step()
    with pytest.raises(LimitExceeded) as ei:
        b.step()
    assert ei.value.kind == "max_steps"


def test_write_and_token_limits():
    b = Budget.from_wiki(WikiConfig(max_writes=1, token_budget=10))
    b.write()
    with pytest.raises(LimitExceeded):
        b.write()
    b2 = Budget.from_wiki(WikiConfig(token_budget=10))
    b2.add_tokens(11)
    with pytest.raises(LimitExceeded):
        b2.add_tokens(0)


def test_loop_detection_repeat_signature():
    b = Budget.from_wiki(WikiConfig())
    assert b.seen("get_article|{'id':'a'}") is False
    assert b.seen("get_article|{'id':'a'}") is True
