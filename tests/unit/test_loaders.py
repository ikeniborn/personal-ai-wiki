import io
import zipfile

import pytest

from paw.ingest.loaders import UnsupportedSource, load_source


def test_md_strips_frontmatter():
    raw = b"---\ntitle: x\n---\n# Heading\n\nBody text."
    out = load_source(raw, "md")
    assert "title: x" not in out
    assert "# Heading" in out


def test_txt_passthrough():
    assert load_source(b"plain text", "txt") == "plain text"


def test_html_extracts_main_content():
    html = b"<html><body><article><h1>QUIC</h1><p>Fast transport.</p></article></body></html>"
    out = load_source(html, "html")
    assert "QUIC" in out
    assert "Fast transport" in out


def test_pdf_extracts_text():
    import fitz  # pymupdf

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF body")
    data = doc.tobytes()
    out = load_source(data, "pdf")
    assert "Hello PDF" in out


def _minimal_docx(text: str) -> bytes:
    # OOXML skeleton mammoth can read.
    document = (
        '<?xml version="1.0"?><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0"?><Types '
        'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def test_docx_extracts_text():
    out = load_source(_minimal_docx("Docx body words"), "docx")
    assert "Docx body words" in out


def test_unsupported_type():
    with pytest.raises(UnsupportedSource):
        load_source(b"x", "epub")


def test_empty_extraction_raises():
    with pytest.raises(ValueError):
        load_source(b"", "txt")
