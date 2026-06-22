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
