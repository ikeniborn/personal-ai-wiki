import base64
import binascii


def encode_cursor(sort_value: str, ident: str) -> str:
    raw = f"{sort_value}|{ident}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise ValueError("invalid cursor") from e
    sort_value, _, ident = raw.partition("|")
    if not ident:
        raise ValueError("invalid cursor")
    return sort_value, ident
