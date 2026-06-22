import mistune
import nh3

_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "strong", "em", "del", "blockquote", "code", "pre",
    "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
}
_ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}

_md = mistune.create_markdown(
    renderer=mistune.HTMLRenderer(escape=False),
    plugins=["table", "strikethrough"],
)


def render_markdown(text: str) -> str:
    raw_html = _md(text)
    if not isinstance(raw_html, str):
        raise TypeError(f"mistune returned {type(raw_html)!r}, expected str")
    return nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)
