import pytest

from paw.security.uploads import UploadRejected, validate_text_upload


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
