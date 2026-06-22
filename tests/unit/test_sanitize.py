from paw.security.sanitize import render_markdown


def test_renders_markdown_to_html():
    html = render_markdown("# Title\n\nSome **bold** text.")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html


def test_strips_script_tags():
    html = render_markdown("ok\n\n<script>alert(1)</script>")
    assert "<script>" not in html
    assert "alert(1)" not in html


def test_strips_event_handlers():
    html = render_markdown('<a href="x" onclick="evil()">link</a>')
    assert "onclick" not in html
