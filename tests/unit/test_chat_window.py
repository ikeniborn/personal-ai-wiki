from paw.harness.ops.chat import build_chat_messages, refs_payload, window_turns
from paw.harness.retrieve import Ref, RetrievedContext
from paw.providers.config import WikiConfig


def test_window_pairs_and_truncates():
    msgs = [
        ("user", "q1"), ("assistant", "a1"),
        ("user", "q2"), ("assistant", "a2"),
        ("user", "q3"), ("assistant", "a3"),
    ]
    assert window_turns(msgs, 2) == [("q2", "a2"), ("q3", "a3")]
    assert window_turns(msgs, 0) == []
    assert window_turns([], 5) == []


def test_window_drops_unpaired_trailing_user():
    msgs = [("user", "q1"), ("assistant", "a1"), ("user", "dangling")]
    assert window_turns(msgs, 5) == [("q1", "a1")]


def test_build_messages_folds_history_with_delimiters():
    import uuid
    ref = Ref(article_id=uuid.uuid4(), slug="tcp", title="TCP")
    ctx = RetrievedContext(passages=[], refs=[ref], prompt_block="<<CONTEXT>>seed<<END_CONTEXT>>")
    msgs = build_chat_messages("new q", [("q1", "a1")], ctx, WikiConfig())
    assert msgs[0].role == "system"
    user = msgs[1].content
    assert "<<THREAD" in user and "<<END_THREAD>>" in user
    assert "User: q1" in user and "Assistant: a1" in user
    assert "QUESTION:\nnew q" in user
    assert "<<CONTEXT>>" in user  # ctx.prompt_block appended


def test_build_messages_no_history_has_no_thread_block():
    ctx = RetrievedContext(passages=[], refs=[], prompt_block="<<CONTEXT>>x<<END_CONTEXT>>")
    msgs = build_chat_messages("q", [], ctx, WikiConfig())
    assert "<<THREAD" not in msgs[1].content
    assert "QUESTION:\nq" in msgs[1].content


def test_refs_payload_shape():
    import uuid
    aid = uuid.uuid4()
    out = refs_payload([Ref(article_id=aid, slug="tcp", title="TCP")])
    assert out == [{"article_id": str(aid), "slug": "tcp", "title": "TCP"}]
