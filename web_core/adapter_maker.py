"""Web-side adapters around the cryptographic maker primitives.

This module keeps all capsule assembly concerns outside the frozen core.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from core import core_maker


def _require_positive_rounds(value: int) -> int:
    try:
        rounds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Rounds must be an integer.") from exc
    if rounds <= 0:
        raise ValueError("Rounds must be a positive integer.")
    return rounds


def _require_bytes(value: Any, field_name: str) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raise ValueError(f"{field_name} must be bytes.")


def _entropy_bytes_to_text(entropy_bytes: Optional[bytes]) -> str:
    if entropy_bytes is None:
        return ""
    raw = _require_bytes(entropy_bytes, "entropy_bytes")
    return raw.decode("utf-8", errors="ignore")


@dataclass
class CapsuleResult:
    format: str = "lethesafe-puzzle"
    version: int = core_maker.PUZZLE_VERSION
    mode: str = "new"
    rounds: int = 0
    hash_function: str = "sha256"
    puzzle: bytes = b""
    start_value: bytes = b""
    start_value_protected: Optional[Dict[str, Any]] = None
    secret: bytes = b""
    secret_checksum: bytes = b""


@dataclass
class CapsuleMaterial:
    start_value: bytes
    secret: bytes
    start_value_protected: Optional[Dict[str, Any]]


def _compute_hash_chain_iterative(
    start_value: bytes,
    rounds: int,
    *,
    progress_callback: Optional[Callable[[int], None]] = None,
    abort_check: Optional[Callable[[], None]] = None,
    chunk_size: int = 50_000,
) -> bytes:
    current = start_value
    if rounds <= 0:
        return current

    min_chunk = 1024
    max_chunk = max(min_chunk, int(chunk_size) if chunk_size else min_chunk)
    target_chunk = min(max_chunk, 50_000)
    current_chunk = target_chunk
    progress_interval = 0.1  # seconds
    abort_interval = 0.35  # seconds – cancellation may lag but still happens
    last_progress_time = time.perf_counter()
    last_abort_time = last_progress_time
    completed = 0

    while completed < rounds:
        chunk_start = time.perf_counter()
        chunk_limit = min(rounds, completed + current_chunk)
        while completed < chunk_limit:
            current = hashlib.sha256(current).digest()
            completed += 1
        now = time.perf_counter()

        if progress_callback and (now - last_progress_time >= progress_interval or completed >= rounds):
            progress_callback(completed)
            last_progress_time = now

        if abort_check and (now - last_abort_time >= abort_interval or completed >= rounds):
            abort_check()
            last_abort_time = now

        chunk_duration = now - chunk_start
        if chunk_duration > progress_interval * 1.5 and current_chunk > min_chunk:
            current_chunk = max(min_chunk, current_chunk // 2)
        elif chunk_duration < progress_interval * 0.5 and current_chunk < max_chunk:
            current_chunk = min(max_chunk, current_chunk * 2)

    return current


def _compute_hashes_for_targets(
    start_value: bytes,
    targets: Sequence[int],
    *,
    progress_callback: Optional[Callable[[int], None]] = None,
    abort_check: Optional[Callable[[], None]] = None,
    progress_chunk_size: int = 50_000,
) -> Dict[int, bytes]:
    normalized_targets = sorted({_require_positive_rounds(value) for value in targets})
    if not normalized_targets:
        return {}

    target_set = set(normalized_targets)
    max_target = normalized_targets[-1]
    current = start_value
    completed = 0
    min_chunk = 1024
    max_chunk = 50_000
    chunk_size = min(max_chunk, max(min_chunk, int(progress_chunk_size) if progress_chunk_size else min_chunk))
    progress_interval = 0.1
    abort_interval = 0.35
    last_progress_time = time.perf_counter()
    last_abort_time = last_progress_time
    results: Dict[int, bytes] = {}

    while completed < max_target:
        chunk_start = time.perf_counter()
        chunk_limit = min(max_target, completed + chunk_size)
        while completed < chunk_limit:
            current = hashlib.sha256(current).digest()
            completed += 1
            if completed in target_set:
                results[completed] = current
        now = time.perf_counter()

        if progress_callback and (now - last_progress_time >= progress_interval or completed >= max_target):
            progress_callback(completed)
            last_progress_time = now

        if abort_check and (now - last_abort_time >= abort_interval or completed >= max_target):
            abort_check()
            last_abort_time = now

        chunk_duration = now - chunk_start
        if chunk_duration > progress_interval * 1.5 and chunk_size > min_chunk:
            chunk_size = max(min_chunk, chunk_size // 2)
        elif chunk_duration < progress_interval * 0.5 and chunk_size < max_chunk:
            chunk_size = min(max_chunk, chunk_size * 2)

    return results


def create_capsule_material(
    start_password: Optional[str],
    entropy_bytes: Optional[bytes],
) -> CapsuleMaterial:
    if start_password is not None and not isinstance(start_password, str):
        raise ValueError("start_password must be a string.")

    user_entropy = _entropy_bytes_to_text(entropy_bytes)
    start_value = core_maker.generate_start_value(user_entropy)
    secret_k = core_maker.generate_random_k(user_entropy)

    if start_password:
        start_value_protected = core_maker.protect_start_value(start_value, start_password)
    else:
        start_value_protected = None
    return CapsuleMaterial(
        start_value=start_value,
        secret=secret_k,
        start_value_protected=start_value_protected,
    )


def create_capsule_web(
    *,
    rounds: int,
    hash_function: str,
    start_password: Optional[str],
    entropy_bytes: Optional[bytes],
    progress_callback: Optional[Callable[[int], None]] = None,
    abort_check: Optional[Callable[[], None]] = None,
    progress_chunk_size: int = 50_000,
    material: Optional[CapsuleMaterial] = None,
    precomputed_hash: Optional[bytes] = None,
) -> CapsuleResult:
    rounds_value = _require_positive_rounds(rounds)
    if hash_function != "sha256":
        raise ValueError("Only sha256 hash function is supported.")
    if start_password is not None and not isinstance(start_password, str):
        raise ValueError("start_password must be a string.")

    if material is None:
        material = create_capsule_material(start_password, entropy_bytes)

    start_value = material.start_value
    secret_k = material.secret
    if precomputed_hash is not None:
        hash_n = _require_bytes(precomputed_hash, "precomputed_hash")
    else:
        hash_n = _compute_hash_chain_iterative(
            start_value,
            rounds_value,
            progress_callback=progress_callback,
            abort_check=abort_check,
            chunk_size=progress_chunk_size,
        )
    puzzle = core_maker.build_puzzle(secret_k, hash_n)
    secret_checksum = hashlib.sha256(secret_k).digest()

    start_value_protected = material.start_value_protected

    return CapsuleResult(
        mode="new",
        rounds=rounds_value,
        puzzle=puzzle,
        start_value=start_value,
        start_value_protected=start_value_protected,
        secret=secret_k,
        secret_checksum=secret_checksum,
    )


def clone_capsule_web(
    source: Mapping[str, Any],
    *,
    rounds: int,
    progress_callback: Optional[Callable[[int], None]] = None,
    abort_check: Optional[Callable[[], None]] = None,
    progress_chunk_size: int = 50_000,
    precomputed_hash_source: Optional[bytes] = None,
    precomputed_hash_new: Optional[bytes] = None,
    secret_override: Optional[bytes] = None,
) -> CapsuleResult:
    if not isinstance(source, Mapping):
        raise ValueError("Source capsule must be mapping-like.")

    required_fields = ("rounds", "hash_function", "puzzle", "start_value", "secret_checksum", "start_value_protected")
    for field in required_fields:
        if field not in source:
            raise ValueError(f"Source capsule missing field: {field}")

    source_rounds = _require_positive_rounds(source["rounds"])
    hash_function = str(source["hash_function"])
    if hash_function != "sha256":
        raise ValueError("Only sha256 hash function is supported.")

    start_value = _require_bytes(source["start_value"], "start_value")
    puzzle = _require_bytes(source["puzzle"], "puzzle")
    expected_checksum = _require_bytes(source["secret_checksum"], "secret_checksum")

    def _relay_progress(offset: int) -> Optional[Callable[[int], None]]:
        if not progress_callback:
            return None

        def _wrapper(local_completed: int) -> None:
            progress_callback(offset + local_completed)

        return _wrapper

    if precomputed_hash_source is not None:
        hash_n_source = _require_bytes(precomputed_hash_source, "precomputed_hash_source")
    else:
        hash_n_source = _compute_hash_chain_iterative(
            start_value,
            source_rounds,
            progress_callback=_relay_progress(0),
            abort_check=abort_check,
            chunk_size=max(1, int(progress_chunk_size)),
        )

    if secret_override is not None:
        secret_k = _require_bytes(secret_override, "secret_override")
    else:
        secret_k = core_maker.xor_bytes(puzzle, hash_n_source)

    secret_checksum = hashlib.sha256(secret_k).digest()
    if secret_checksum != expected_checksum:
        raise ValueError("Source capsule checksum mismatch.")

    rounds_value = _require_positive_rounds(rounds)
    if precomputed_hash_new is not None:
        hash_n_new = _require_bytes(precomputed_hash_new, "precomputed_hash_new")
    else:
        hash_n_new = _compute_hash_chain_iterative(
            start_value,
            rounds_value,
            progress_callback=_relay_progress(source_rounds),
            abort_check=abort_check,
            chunk_size=max(1, int(progress_chunk_size)),
        )
    puzzle_new = core_maker.build_puzzle(secret_k, hash_n_new)

    return CapsuleResult(
        format=str(source.get("format", "lethesafe-puzzle")),
        version=int(source.get("version", core_maker.PUZZLE_VERSION)),
        mode="clone",
        rounds=rounds_value,
        hash_function=hash_function,
        puzzle=puzzle_new,
        start_value=start_value,
        start_value_protected=source.get("start_value_protected"),
        secret=secret_k,
        secret_checksum=secret_checksum,
    )
