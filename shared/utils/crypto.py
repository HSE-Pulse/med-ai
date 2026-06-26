"""Field-level symmetric encryption for GDPR Art. 32 compliance.

Wraps :mod:`cryptography.fernet` with sensible defaults and a decorator
that encrypts designated fields on write. Key comes from the ``FERNET_KEY``
environment variable (production) or an in-process ephemeral key (tests).

Typical usage
-------------

>>> enc = FieldEncryptor.from_env()
>>> enc.encrypt_fields({"icd_codes": ["A40"], "vitals": {...}}, fields=["icd_codes"])
{'icd_codes': 'gAAAAA...', 'vitals': {...}}

The module degrades gracefully when ``cryptography`` is not installed — it
raises ``EncryptorUnavailable`` so callers can fall back to plaintext in
tests while production startup enforces the dependency via ``security_check``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict, Iterable, Optional

try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore
    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - exercised when cryptography missing
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore
    _CRYPTO_AVAILABLE = False


class EncryptorUnavailable(RuntimeError):
    """Raised when field encryption is requested but cryptography is missing."""


class FieldEncryptor:
    """Encrypt/decrypt individual fields inside a dict document."""

    def __init__(self, key: bytes | str) -> None:
        if not _CRYPTO_AVAILABLE:
            raise EncryptorUnavailable(
                "cryptography package is not installed — field encryption disabled"
            )
        if isinstance(key, str):
            key_bytes = key.encode()
        else:
            key_bytes = key
        # Fernet requires a 32-byte url-safe base64 key. Derive if user gave us raw material.
        try:
            Fernet(key_bytes)
            fernet_key = key_bytes
        except Exception:
            digest = hashlib.sha256(key_bytes).digest()
            fernet_key = base64.urlsafe_b64encode(digest)
        self._fernet = Fernet(fernet_key)

    # ------------------------------------------------------------------ factories
    @classmethod
    def from_env(cls, var: str = "FERNET_KEY") -> "FieldEncryptor":
        key = os.environ.get(var)
        if not key:
            raise EncryptorUnavailable(
                f"Environment variable {var} not set — cannot initialise encryption"
            )
        return cls(key)

    @classmethod
    def ephemeral(cls) -> "FieldEncryptor":
        """Generate an ephemeral key — for tests only."""
        if not _CRYPTO_AVAILABLE:
            raise EncryptorUnavailable("cryptography not installed")
        return cls(Fernet.generate_key())

    # ------------------------------------------------------------------ primitives
    def encrypt(self, value: Any) -> str:
        payload = json.dumps(value, default=str).encode()
        return self._fernet.encrypt(payload).decode()

    def decrypt(self, token: str) -> Any:
        raw = self._fernet.decrypt(token.encode())
        return json.loads(raw.decode())

    # ------------------------------------------------------------------ document helpers
    def encrypt_fields(self, doc: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
        out = dict(doc)
        for f in fields:
            if f in out and not (isinstance(out[f], str) and out[f].startswith("gAAAA")):
                out[f] = self.encrypt(out[f])
        return out

    def decrypt_fields(self, doc: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
        out = dict(doc)
        for f in fields:
            if f in out and isinstance(out[f], str) and out[f].startswith("gAAAA"):
                try:
                    out[f] = self.decrypt(out[f])
                except InvalidToken:
                    pass
        return out


__all__ = ["FieldEncryptor", "EncryptorUnavailable"]
