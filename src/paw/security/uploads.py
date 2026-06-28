import io
import zipfile

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
_NESTED_ARCHIVE_SUFFIXES = (".zip", ".docx", ".epub")


def inspect_zip(data: bytes, *, max_total: int, max_entries: int, max_ratio: float) -> None:
    """Metadata-only anti-zip-bomb / path-traversal guard.

    Never decompresses; reads only central-directory sizes. Raises UploadRejected.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UploadRejected("not a valid zip archive") from e
    with zf:
        infos = zf.infolist()
    if len(infos) > max_entries:
        raise UploadRejected("zip has too many entries")
    total = 0
    for info in infos:
        name = info.filename
        safe_name = name.replace("\\", "/")
        if safe_name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise UploadRejected(f"absolute path in zip: {name}")
        if ".." in safe_name.split("/"):
            raise UploadRejected(f"path traversal in zip: {name}")
        if name.lower().endswith(_NESTED_ARCHIVE_SUFFIXES):
            raise UploadRejected(f"nested archive in zip: {name}")
        total += info.file_size
        if total > max_total:
            raise UploadRejected("zip uncompressed size over cap")
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > max_ratio:
            raise UploadRejected(f"suspicious compression ratio: {name}")


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
