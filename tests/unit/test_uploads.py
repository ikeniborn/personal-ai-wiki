import pytest

from paw.security.uploads import UploadRejected, validate_source_upload, validate_text_upload


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


def test_validate_source_accepts_docx_zip_magic():
    assert validate_source_upload("d.docx", b"PK\x03\x04rest", max_bytes=1024) == "docx"


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
