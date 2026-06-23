ALLOWED_TEXT_EXT = {".md", ".txt", ".markdown"}


class UploadRejected(Exception):
    pass


def validate_text_upload(filename: str, data: bytes, *, max_bytes: int) -> None:
    lower = filename.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_TEXT_EXT):
        raise UploadRejected(f"extension not allowed: {filename}")
    if len(data) > max_bytes:
        raise UploadRejected("file too large")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise UploadRejected("not valid UTF-8 text") from e


_TEXT_EXT = {".md": "md", ".markdown": "md", ".txt": "txt"}
_HTML_EXT = {".html": "html", ".htm": "html"}
_TEXT_LIKE_EXT = {**_TEXT_EXT, **_HTML_EXT}  # utf-8-validated extensions
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"


def validate_source_upload(filename: str, data: bytes, *, max_bytes: int) -> str:
    lower = filename.lower()
    if len(data) > max_bytes:
        raise UploadRejected("file too large")
    if not data:
        raise UploadRejected("empty file")
    for ext, kind in _TEXT_LIKE_EXT.items():
        if lower.endswith(ext):
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise UploadRejected("not valid UTF-8 text") from e
            return kind
    if lower.endswith(".pdf"):
        if not data.startswith(_PDF_MAGIC):
            raise UploadRejected("not a valid PDF (magic bytes)")
        return "pdf"
    if lower.endswith(".docx"):
        if not data.startswith(_ZIP_MAGIC):
            raise UploadRejected("not a valid DOCX (magic bytes)")
        return "docx"
    raise UploadRejected(f"extension not allowed: {filename}")
