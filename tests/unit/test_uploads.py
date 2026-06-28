import io
import zipfile

import pytest

from paw.config import get_settings
from paw.security.uploads import UploadRejected, validate_source_upload, validate_text_upload


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("REDIS_URL", "redis://x")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("FERNET_KEY", "k" * 43 + "=")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_accepts_markdown():
    validate_text_upload("note.md", b"# hello\n", max_bytes=1024)  # no raise


def test_rejects_bad_extension():
    with pytest.raises(UploadRejected):
        validate_text_upload("evil.exe", b"MZ...", max_bytes=1024)


def test_rejects_oversize():
    with pytest.raises(UploadRejected):
        validate_text_upload("note.md", b"x" * 2048, max_bytes=1024)


def test_rejects_non_utf8():
    with pytest.raises(UploadRejected):
        validate_text_upload("note.txt", b"\xff\xfe\x00binary", max_bytes=1024)


def test_validate_source_accepts_pdf_magic():
    assert validate_source_upload("doc.pdf", b"%PDF-1.7\n...", max_bytes=1024) == "pdf"


def test_validate_source_rejects_pdf_bad_magic():
    with pytest.raises(UploadRejected):
        validate_source_upload("doc.pdf", b"not a pdf", max_bytes=1024)


def _real_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_validate_source_accepts_docx_zip_magic():
    data = _real_zip({"word/document.xml": b"<w:document/>"})
    assert validate_source_upload("d.docx", data, max_bytes=1_000_000) == "docx"


def test_validate_source_rejects_docx_zip_traversal():
    with pytest.raises(UploadRejected):
        validate_source_upload("d.docx", _real_zip({"../escape.txt": b"x"}), max_bytes=1_000_000)


def _epub_bytes(n_entries: int = 2, body: bytes = b"<html></html>") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", b"application/epub+zip")
        for i in range(n_entries):
            z.writestr(f"OEBPS/ch{i}.xhtml", body)
    return buf.getvalue()


def test_validate_source_accepts_epub():
    assert validate_source_upload("book.epub", _epub_bytes(), max_bytes=1_000_000) == "epub"


def test_validate_source_rejects_epub_bad_magic():
    with pytest.raises(UploadRejected):
        validate_source_upload("book.epub", b"not a zip", max_bytes=1_000_000)


def test_validate_source_rejects_epub_nested_archive():
    with pytest.raises(UploadRejected):
        validate_source_upload("book.epub", _real_zip({"inner.zip": b"x"}), max_bytes=1_000_000)


def test_validate_source_accepts_png():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert validate_source_upload("scan.png", png, max_bytes=1_000_000) == "image"


def test_validate_source_accepts_jpeg():
    jpg = b"\xff\xd8\xff" + b"\x00" * 32
    assert validate_source_upload("scan.jpg", jpg, max_bytes=1_000_000) == "image"


def test_validate_source_accepts_webp():
    webp = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 32
    assert validate_source_upload("scan.webp", webp, max_bytes=1_000_000) == "image"


def test_validate_source_rejects_image_bad_magic():
    with pytest.raises(UploadRejected):
        validate_source_upload("scan.png", b"GIF89a", max_bytes=1_000_000)


def test_validate_source_accepts_html_and_md():
    assert validate_source_upload("p.html", b"<html></html>", max_bytes=1024) == "html"
    assert validate_source_upload("n.md", b"# h", max_bytes=1024) == "md"


def test_validate_source_rejects_unknown_ext():
    with pytest.raises(UploadRejected):
        validate_source_upload("x.exe", b"MZ", max_bytes=1024)


def test_validate_source_rejects_oversize():
    with pytest.raises(UploadRejected):
        validate_source_upload("big.md", b"x" * 100, max_bytes=10)


def test_validate_source_rejects_empty():
    with pytest.raises(UploadRejected):
        validate_source_upload("empty.md", b"", max_bytes=1024)
