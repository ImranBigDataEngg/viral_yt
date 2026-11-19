"""Encrypted storage for platform OAuth tokens (Fernet, key derived from TT_SECRET_KEY)."""
from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken


class TokenVault:
    def __init__(self, secret_key: str):
        key = hashlib.sha256(b"ezzyclips-token-vault|" + secret_key.encode()).digest()
        self._f = Fernet(base64.urlsafe_b64encode(key))

    def seal(self, data: dict) -> str:
        return self._f.encrypt(json.dumps(data).encode()).decode()

    def open(self, blob: str) -> dict:
        try:
            return json.loads(self._f.decrypt(blob.encode()))
        except (InvalidToken, ValueError) as exc:
            raise ValueError("Stored token can't be decrypted (secret key changed?) - reconnect the account") from exc
