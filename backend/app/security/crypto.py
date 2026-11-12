"""Password hashing, token hashing, field-level encryption and PII masking."""

import base64
import hashlib
import hmac
import os
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_ph = PasswordHasher()  # argon2id with library defaults (OWASP-recommended family)
# Pre-computed hash so that login against an unknown email costs the same as a real check
# (prevents user enumeration by timing).
_DUMMY_HASH = _ph.hash("timing-equalizer-not-a-password")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --------------------------------------------------------------------------- AES-GCM field encryption
@lru_cache
def _aes() -> AESGCM:
    key_b64 = get_settings().field_encryption_key
    if key_b64:
        key = base64.urlsafe_b64decode(key_b64)
    else:
        # Development fallback only: derived, deterministic key so local data survives restarts.
        # get_settings() refuses to start in production without a real key.
        key = hashlib.sha256(b"sentinelflow-dev-only-key").digest()
    if len(key) != 32:
        raise RuntimeError("SF_FIELD_ENCRYPTION_KEY must decode to 32 bytes")
    return AESGCM(key)


def encrypt_field(value: str | None) -> bytes | None:
    if value is None:
        return None
    nonce = os.urandom(12)
    return nonce + _aes().encrypt(nonce, value.encode(), None)


def decrypt_field(blob: bytes | None) -> str | None:
    if blob is None:
        return None
    return _aes().decrypt(blob[:12], blob[12:], None).decode()


# --------------------------------------------------------------------------- masking
def mask_name(name: str | None) -> str | None:
    if not name:
        return name
    return " ".join(p[0] + "***" for p in name.split())  # fixed width: masks never leak name length


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}"


def mask_dob(dob: str | None) -> str | None:
    return f"{dob[:4]}-**-**" if dob else dob


def mask_document(last4: str | None) -> str | None:
    return f"*****{last4}" if last4 else None
