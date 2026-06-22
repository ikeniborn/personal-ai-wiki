# tests/unit/test_secrets.py
from cryptography.fernet import Fernet

from paw.security.secrets import SecretBox


def test_encrypt_decrypt_roundtrip():
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("sk-provider-123")
    assert token != "sk-provider-123"
    assert box.decrypt(token) == "sk-provider-123"


def test_decrypt_tampered_raises():
    import pytest
    from cryptography.fernet import InvalidToken

    box = SecretBox(Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        box.decrypt("not-a-valid-token")
