# core_unlocker.py
# Lethesafe – Cryptographic Core (Unlocker)

import base64
import hashlib
import hmac
from typing import Dict, Any, Union

PERSON_ENC = b"LethSenc"
PERSON_MAC = b"LethSmac"
DEFAULT_PBKDF2_ITERS = 400_000
SUPPORTED_HASH_FUNCTION = "sha256"
SUPPORTED_KDF = "pbkdf2_hmac_sha256"
SUPPORTED_MAC_FUNCTION = "hmac_sha256"


# ─────────────────────────────
# Primitive
# ─────────────────────────────

def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# ─────────────────────────────
# Password / Start-Value Handling
# ─────────────────────────────

def _derive_pbkdf2_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=32
    )


def _blake2_digest(key: bytes, personalization: bytes, data: bytes, size: int) -> bytes:
    h = hashlib.blake2b(
        key=key,
        digest_size=size,
        person=personalization
    )
    h.update(data)
    return h.digest()


def _require_algorithm(value: Union[str, None], expected: str, error: str) -> None:
    if not isinstance(value, str) or value != expected:
        raise ValueError(error)


def decrypt_start_value(
    protected: Dict[str, Any],
    password: str
) -> bytes:
    _require_algorithm(
        protected.get("kdf"),
        SUPPORTED_KDF,
        "Unsupported KDF in capsule"
    )
    _require_algorithm(
        protected.get("mac_function"),
        SUPPORTED_MAC_FUNCTION,
        "Unsupported MAC function in capsule"
    )
    ciphertext = base64.b64decode(protected["ciphertext"])
    salt = base64.b64decode(protected["salt"])
    nonce = base64.b64decode(protected["nonce"])
    mac = base64.b64decode(protected["mac"])
    iterations = int(protected.get("iterations", DEFAULT_PBKDF2_ITERS))

    key = _derive_pbkdf2_key(password, salt, iterations)
    keystream = _blake2_digest(key, PERSON_ENC, nonce, len(ciphertext))
    start_value = xor_bytes(ciphertext, keystream)

    mac_key = _blake2_digest(key, PERSON_MAC, nonce, 32)
    expected = hmac.new(mac_key, start_value, hashlib.sha256).digest()

    if not hmac.compare_digest(mac, expected):
        raise ValueError("Password invalid or data corrupted")

    return start_value


# ─────────────────────────────
# Time-Lock Resolution
# ─────────────────────────────

def compute_hash_chain(start_value: bytes, rounds: int) -> bytes:
    h = start_value
    for _ in range(rounds):
        h = hashlib.sha256(h).digest()
    return h


def _normalize_checksum(secret_checksum: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(secret_checksum, str):
        checksum = secret_checksum.strip()
        if not checksum:
            raise ValueError("Capsule corrupted or tampered")
        try:
            result = bytes.fromhex(checksum)
        except ValueError as exc:
            raise ValueError("Capsule corrupted or tampered") from exc
    elif isinstance(secret_checksum, (bytes, bytearray)):
        result = bytes(secret_checksum)
    else:
        raise ValueError("Capsule corrupted or tampered")

    if len(result) != hashlib.sha256().digest_size:
        raise ValueError("Capsule corrupted or tampered")
    return result


def recover_secret_k(
    puzzle: bytes,
    hash_n: bytes,
    *,
    secret_checksum: Union[str, bytes, bytearray],
    hash_function: Union[str, None]
) -> bytes:
    _require_algorithm(
        hash_function,
        SUPPORTED_HASH_FUNCTION,
        "Unsupported hash_function in capsule"
    )
    secret = xor_bytes(puzzle, hash_n)
    expected = _normalize_checksum(secret_checksum)
    computed = hashlib.sha256(secret).digest()

    if not hmac.compare_digest(computed, expected):
        raise ValueError("Capsule corrupted or tampered")

    return secret
