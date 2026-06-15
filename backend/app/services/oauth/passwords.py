"""
Password hashing.

Uses argon2 if available (preferred — memory-hard, modern), else falls back
to PBKDF2-HMAC-SHA256 from the stdlib (no third-party dependency). Both store
a self-describing string so verify() knows which scheme produced a hash and
old hashes keep verifying after an upgrade.

NEVER store or log plaintext passwords. NEVER compare hashes with ==; use the
constant-time verify provided here.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import base64


_PBKDF2_ROUNDS = 600_000  # OWASP-ish floor for PBKDF2-SHA256


def _pbkdf2_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return "pbkdf2$%d$%s$%s" % (
        _PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def _pbkdf2_verify(password: str, stored: str) -> bool:
    try:
        _, rounds_s, salt_b64, dk_b64 = stored.split("$")
        rounds = int(rounds_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return hmac.compare_digest(dk, expected)


def hash_password(password: str) -> str:
    try:
        from argon2 import PasswordHasher
        return PasswordHasher().hash(password)  # argon2 hashes start with "$argon2"
    except Exception:
        return _pbkdf2_hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher
            from argon2.exceptions import VerifyMismatchError
            try:
                PasswordHasher().verify(stored_hash, password)
                return True
            except VerifyMismatchError:
                return False
        except Exception:
            return False
    if stored_hash.startswith("pbkdf2$"):
        return _pbkdf2_verify(password, stored_hash)
    return False