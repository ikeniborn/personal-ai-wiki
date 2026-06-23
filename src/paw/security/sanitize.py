import re
import uuid

import mistune
import nh3

_ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "del",
    "blockquote",
    "code",
    "pre",
    "ul",
    "ol",
    "li",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
_ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}

_md = mistune.create_markdown(
    renderer=mistune.HTMLRenderer(escape=False),
    plugins=["table", "strikethrough"],
)

# [[slug]] or [[slug|label]] — slug has no '|' or ']'; optional label has no ']'.
_WIKILINK = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]")


def extract_wikilink_targets(text: str) -> list[str]:
    """Return the slug of every [[slug]] / [[slug|label]] occurrence, in order."""
    return [m.group(1).strip() for m in _WIKILINK.finditer(text)]


def resolve_wikilinks(text: str, slug_to_id: dict[str, uuid.UUID]) -> str:
    """Rewrite [[slug]] / [[slug|label]] to markdown links for known slugs.

    Unknown slugs degrade to their plain label text (visible, not a broken link).
    Call this BEFORE render_markdown.
    """

    def _replace(match: re.Match[str]) -> str:
        slug = match.group(1).strip()
        label = (match.group(2) or slug).strip()
        article_id = slug_to_id.get(slug)
        return f"[{label}](/articles/{article_id})" if article_id is not None else label

    return _WIKILINK.sub(_replace, text)


def render_markdown(text: str) -> str:
    raw_html = _md(text)
    if not isinstance(raw_html, str):
        raise TypeError(f"mistune returned {type(raw_html)!r}, expected str")
    return nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)
