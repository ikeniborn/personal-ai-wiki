import uuid

from paw.security.sanitize import render_markdown, resolve_wikilinks


def test_known_slug_becomes_link():
    aid = uuid.uuid4()
    out = resolve_wikilinks("see [[tcp]] now", {"tcp": aid})
    assert out == f"see [tcp](/articles/{aid}) now"


def test_labelled_wikilink_uses_label():
    aid = uuid.uuid4()
    out = resolve_wikilinks("[[tcp|the TCP page]]", {"tcp": aid})
    assert out == f"[the TCP page](/articles/{aid})"


def test_unknown_slug_renders_plain_label():
    assert resolve_wikilinks("[[ghost]]", {}) == "ghost"
    assert resolve_wikilinks("[[ghost|Ghost]]", {}) == "Ghost"


def test_multiple_wikilinks_in_one_line():
    a, b = uuid.uuid4(), uuid.uuid4()
    out = resolve_wikilinks("[[a]] and [[b]]", {"a": a, "b": b})
    assert out == f"[a](/articles/{a}) and [b](/articles/{b})"


def test_rendered_html_carries_relative_anchor():
    aid = uuid.uuid4()
    html = render_markdown(resolve_wikilinks("[[tcp]]", {"tcp": aid}))
    assert f'href="/articles/{aid}"' in html
