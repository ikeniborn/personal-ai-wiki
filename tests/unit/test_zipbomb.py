import io
import zipfile

import pytest

from paw.security.uploads import UploadRejected, inspect_zip

_LIMITS = {"max_total": 10_000, "max_entries": 5, "max_ratio": 50.0}


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_accepts_small_safe_zip():
    inspect_zip(_zip({"a.txt": b"hello", "b.txt": b"world"}), **_LIMITS)


def test_rejects_non_zip():
    with pytest.raises(UploadRejected):
        inspect_zip(b"not a zip at all", **_LIMITS)


def test_rejects_too_many_entries():
    many = {f"f{i}.txt": b"x" for i in range(6)}
    with pytest.raises(UploadRejected):
        inspect_zip(_zip(many), **_LIMITS)


def test_rejects_total_uncompressed_over_cap():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"big.txt": b"x" * 20_000}), **_LIMITS)


def test_rejects_high_compression_ratio():
    bomb = _zip({"z.bin": b"\x00" * 1_000_000})
    with pytest.raises(UploadRejected):
        inspect_zip(bomb, max_total=10_000_000, max_entries=5, max_ratio=50.0)


def test_rejects_path_traversal():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"../escape.txt": b"x"}), **_LIMITS)


def test_rejects_absolute_path():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"/etc/passwd": b"x"}), **_LIMITS)


def test_rejects_windows_drive_path():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"C:/Windows/win.ini": b"x"}), **_LIMITS)


def test_rejects_backslash_rooted_path():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"\\Windows\\win.ini": b"x"}), **_LIMITS)


@pytest.mark.parametrize("name", ["inner.zip", "inner.docx", "inner.epub"])
def test_rejects_nested_archives(name):
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({name: b"PK\x03\x04"}), **_LIMITS)
