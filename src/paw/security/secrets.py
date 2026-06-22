from cryptography.fernet import Fernet


class SecretBox:
    """Encrypt/decrypt provider secrets at rest (Fernet, key from env). LLD §11."""

    def __init__(self, key: str) -> None:
        self._f = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()
