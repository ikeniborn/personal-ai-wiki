import io
import zipfile

import pytest
from tests.stubs import StubVisionProvider

from paw.ingest.loaders import UnsupportedSource, load_source
from paw.ingest.loaders.image import describe_image
from paw.ingest.loaders.url import load_url


def _minimal_epub(chapter_html: str) -> bytes:
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">x</dc:identifier>'
        "<dc:title>T</dc:title><dc:language>en</dc:language></metadata>"
        '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{chapter_html}</body></html>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/ch1.xhtml", chapter)
    return buf.getvalue()


def test_epub_extracts_spine_text():
    data = _minimal_epub("<h1>QUIC</h1><p>Fast transport protocol.</p>")
    out = load_source(data, "epub")
    assert "QUIC" in out
    assert "Fast transport" in out


def test_epub_uses_spine_order_and_skips_non_spine_documents():
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">x</dc:identifier>'
        "<dc:title>T</dc:title><dc:language>en</dc:language></metadata>"
        '<manifest><item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>'
    )
    chapter_1 = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>First chapter</h1></body></html>"
    )
    chapter_2 = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Second chapter</h1></body></html>"
    )
    nav = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Navigation page</h1></body></html>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/ch2.xhtml", chapter_2)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/ch1.xhtml", chapter_1)

    out = load_source(buf.getvalue(), "epub")
    assert "Navigation page" not in out
    assert out.index("First chapter") < out.index("Second chapter")


def test_epub_empty_raises():
    with pytest.raises(ValueError):
        load_source(_minimal_epub("<body></body>"), "epub")


def test_image_load_source_requires_vision_path():
    with pytest.raises(UnsupportedSource, match="image sources require the vision path"):
        load_source(b"img", "image")


async def test_describe_image_calls_vision():
    vis = StubVisionProvider(text="A photo of a server rack.")
    out = await describe_image(b"img", vis, prompt="Describe")
    assert "server rack" in out
    assert vis.prompts == ["Describe"]


async def test_url_loads_html_via_safe_get(monkeypatch):
    calls = []

    async def fake_safe_get(url: str, *, max_bytes: int, allowlist: list[str]) -> bytes:
        calls.append((url, max_bytes, allowlist))
        return b"<html><body><h1>QUIC</h1><p>Fast transport.</p></body></html>"

    monkeypatch.setattr("paw.ingest.loaders.url.safe_get", fake_safe_get)

    out = await load_url("https://example.com/quic", allowlist=["example.com"], max_bytes=1024)

    assert "QUIC" in out
    assert "Fast transport" in out
    assert calls == [("https://example.com/quic", 1024, ["example.com"])]


async def test_url_empty_extract_raises(monkeypatch):
    async def fake_safe_get(url: str, *, max_bytes: int, allowlist: list[str]) -> bytes:
        return b""

    monkeypatch.setattr("paw.ingest.loaders.url.safe_get", fake_safe_get)

    with pytest.raises(ValueError, match="url produced no extractable text"):
        await load_url("https://example.com/empty", allowlist=[], max_bytes=1024)
