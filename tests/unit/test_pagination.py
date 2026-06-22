from paw.api.pagination import decode_cursor, encode_cursor


def test_cursor_roundtrip():
    cur = encode_cursor("2026-06-22T10:00:00+00:00", "abc-id")
    ts, ident = decode_cursor(cur)
    assert ts == "2026-06-22T10:00:00+00:00"
    assert ident == "abc-id"


def test_bad_cursor_raises():
    import pytest

    with pytest.raises(ValueError):
        decode_cursor("!!!notbase64!!!")
