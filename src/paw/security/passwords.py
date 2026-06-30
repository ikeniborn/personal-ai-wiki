from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_ph = PasswordHasher()


class WeakPassword(Exception):
    pass


_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "password1234",
        "123456",
        "1234567",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty",
        "qwertyuiop",
        "letmein",
        "welcome",
        "admin",
        "admin123",
        "iloveyou",
        "abc123",
        "monkey",
        "dragon",
        "000000",
        "111111",
    }
)


def validate_password_strength(plain: str) -> None:
    """Raise WeakPassword if the password is too short or too common."""
    from paw.config import get_settings

    min_length = get_settings().password_min_length
    if len(plain) < min_length:
        raise WeakPassword(f"password must be at least {min_length} characters")
    if plain.strip().casefold() in _COMMON_PASSWORDS:
        raise WeakPassword("password is too common")


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError):
        return False
