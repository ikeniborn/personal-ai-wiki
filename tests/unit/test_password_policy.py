import pytest

from paw.security.passwords import WeakPassword, validate_password_strength


def test_rejects_short_password(wired_settings):
    with pytest.raises(WeakPassword):
        validate_password_strength("short")


def test_rejects_common_password(wired_settings):
    with pytest.raises(WeakPassword):
        validate_password_strength("password1234")


def test_accepts_strong_password(wired_settings):
    validate_password_strength("a-Long-Unique-Phrase-42")
