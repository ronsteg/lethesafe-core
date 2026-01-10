# core_maker.py
# Lethesafe – Cryptographic Core (Maker)

import os
import base64
import hashlib
import hmac
from typing import Dict, Any, List

PUZZLE_VERSION = 2
DEFAULT_PBKDF2_ITERS = 400_000

PERSON_ENC = b"LethSenc"
PERSON_MAC = b"LethSmac"


# ─────────────────────────────
# Primitive
# ─────────────────────────────

def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# ─────────────────────────────
# Entropy & Secrets
# ─────────────────────────────

def derive_entropy_value(label: str, user_entropy: str, size: int = 32) -> bytes:
    h = hashlib.sha512()
    h.update(label.encode("utf-8"))
    h.update(os.urandom(size * 2))
    if user_entropy:
        h.update(hashlib.sha256(user_entropy.encode("utf-8")).digest())
    else:
        h.update(os.urandom(size))
    h.update(os.urandom(size * 2))
    return h.digest()[:size]


def generate_start_value(user_entropy: str) -> bytes:
    return derive_entropy_value("start_value", user_entropy)


def generate_random_k(user_entropy: str) -> bytes:
    return derive_entropy_value("target_secret", user_entropy)


# ─────────────────────────────
# Password Protection for S
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


def protect_start_value(
    start_value: bytes,
    password: str,
    iterations: int = DEFAULT_PBKDF2_ITERS
) -> Dict[str, Any]:
    salt = os.urandom(16)
    nonce = os.urandom(16)

    key = _derive_pbkdf2_key(password, salt, iterations)
    keystream = _blake2_digest(key, PERSON_ENC, nonce, len(start_value))
    ciphertext = xor_bytes(start_value, keystream)

    mac_key = _blake2_digest(key, PERSON_MAC, nonce, 32)
    mac = hmac.new(mac_key, start_value, hashlib.sha256).digest()

    return {
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "iterations": iterations,
        "kdf": "pbkdf2_hmac_sha256",
        "mac": base64.b64encode(mac).decode(),
        "mac_function": "hmac_sha256",
    }


# ─────────────────────────────
# Time-Lock Computation
# ─────────────────────────────

def compute_hash_chain(start_value: bytes, rounds: int) -> bytes:
    h = start_value
    for _ in range(rounds):
        h = hashlib.sha256(h).digest()
    return h


def compute_hashes_for_rounds(
    start_value: bytes,
    rounds_list: List[int]
) -> Dict[int, bytes]:
    results = {}
    max_rounds = max(rounds_list)
    current = start_value

    targets = set(rounds_list)
    for i in range(1, max_rounds + 1):
        current = hashlib.sha256(current).digest()
        if i in targets:
            results[i] = current

    return results


# ─────────────────────────────
# Puzzle Construction
# ─────────────────────────────

def build_puzzle(secret_k: bytes, hash_n: bytes) -> bytes:
    return xor_bytes(secret_k, hash_n)
