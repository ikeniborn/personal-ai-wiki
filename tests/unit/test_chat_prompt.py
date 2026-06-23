from paw.harness.ops.chat import ChatTurn, dont_know_turn, to_chat_turn
from paw.harness.ops.query import DONT_KNOW
from paw.harness.prompts import get_prompt
from paw.harness.retrieve import RetrievedContext


def test_chat_overlay_present_and_localised():
    p = get_prompt("chat", gen_language="fr", reasoning_language="de")
    assert "fr" in p and "de" in p  # preamble localisation
    assert "DATA" in p  # untrusted-context discipline restated


def test_dont_know_turn_is_canonical():
    t = dont_know_turn()
    assert isinstance(t, ChatTurn)
    assert t.answer_md == DONT_KNOW and t.refs == []


def test_to_chat_turn_carries_refs():
    ctx = RetrievedContext(passages=[], refs=[], prompt_block="")
    assert to_chat_turn("hi [a]", ctx).answer_md == "hi [a]"
