"""Workflow orchestration for the Lethesafe web interface.

The actual cryptographic operations remain inside ``core_*`` modules.  This
module only validates inputs, coordinates progress tracking, enforces abort
signals and triggers the canonical core once the environment is ready.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core import core_maker
from core.bench import measure_hashrate
from core.core_unlocker import (
    compute_hash_chain,
    decrypt_start_value,
    recover_secret_k,
    compute_secret_checksum,
    decode_puzzle_base64,
)
from web.state import abort_manager, progress_tracker
from web_core.adapter_maker import (
    CapsuleMaterial,
    CapsuleResult,
    clone_capsule_web,
    create_capsule_material,
    create_capsule_web,
    _compute_hashes_for_targets,
)

MODE_MAP = {
    "round": "rounds",
    "rounds": "rounds",
    "round_based": "rounds",
    "rounds_based": "rounds",
    "time": "time",
    "time_based": "time",
}

LEGACY_PLAINTEXT_WARNING = "Insecure capsule: plaintext start_value was present and ignored."

class WorkflowError(RuntimeError):
    """Raised when a workflow request is invalid or cannot proceed."""


def _calibrate_hashrate(duration_seconds: float = 2.5) -> float:
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Calibration duration must be numeric.") from exc
    if duration <= 0:
        raise WorkflowError("Calibration duration must be positive.")

    return measure_hashrate(duration)


def _hash_chain_with_progress(
    start_value: bytes,
    rounds: int,
    *,
    run_token: str,
    start_time: float,
) -> bytes:
    """Compute the hash chain while updating progress in controlled intervals."""
    current = start_value
    if rounds <= 0:
        return current

    min_chunk = 1024
    max_chunk = 50_000
    target_chunk = max(min_chunk, rounds // 200) if rounds > 0 else min_chunk
    chunk_size = min(max_chunk, max(min_chunk, target_chunk))
    progress_interval = 0.1
    abort_interval = 0.35
    last_progress_ts = time.perf_counter()
    last_abort_ts = last_progress_ts
    completed = 0

    ensure_not_cancelled(run_token)

    while completed < rounds:
        ensure_not_cancelled(run_token)
        chunk_start = time.perf_counter()
        step = min(chunk_size, rounds - completed)
        current = compute_hash_chain(current, step)
        completed += step
        now = time.perf_counter()

        if now - last_progress_ts >= progress_interval or completed >= rounds:
            elapsed = time.time() - start_time
            progress_update(run_token, completed, elapsed)
            last_progress_ts = now

        if now - last_abort_ts >= abort_interval:
            ensure_not_cancelled(run_token)
            last_abort_ts = now

        elapsed_chunk = now - chunk_start
        if elapsed_chunk > progress_interval * 1.5 and chunk_size > min_chunk:
            chunk_size = max(min_chunk, chunk_size // 2)
        elif elapsed_chunk < progress_interval * 0.5 and chunk_size < max_chunk:
            chunk_size = min(max_chunk, chunk_size * 2)

    return current


def _parse_time_value(value: str) -> float:
    if not isinstance(value, str):
        raise WorkflowError("Time values must be strings such as '10m' or '2h'.")
    match = re.fullmatch(r"\s*(\d+)\s*([smhdSMHD])\s*", value)
    if not match:
        raise WorkflowError("Time format must be <number><unit>, e.g. 10m or 2h.")
    amount = int(match.group(1))
    if amount <= 0:
        raise WorkflowError("Time values must be greater than zero.")
    unit = match.group(2).lower()
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(amount * factors[unit])


def _normalize_rounds(values: Optional[Iterable[Any]]) -> List[int]:
    rounds: List[int] = []
    seen: set[int] = set()
    if values is None:
        return rounds
    for raw in values:
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise WorkflowError("Round entries must be integers.") from exc
        if value <= 0:
            raise WorkflowError("Rounds must be positive integers.")
        if value in seen:
            continue
        rounds.append(value)
        seen.add(value)
    return rounds


def _normalize_mode_value(mode: Optional[str]) -> Optional[str]:
    if mode is None:
        return None
    if not isinstance(mode, str):
        raise WorkflowError("Rounds mode must be a string.")
    normalized = MODE_MAP.get(mode.strip().lower())
    if normalized is None:
        raise WorkflowError("Unsupported rounds mode.")
    return normalized


def _looks_like_time_entries(values: Any) -> bool:
    if values is None or isinstance(values, Mapping):
        return False
    if isinstance(values, (list, tuple, set)):
        iterable = values
    else:
        iterable = [values]
    for entry in iterable:
        if isinstance(entry, str) and re.fullmatch(r"\s*(\d+)\s*[smhdSMHD]\s*", entry):
            return True
    return False


def _normalize_time_entries(times: Any) -> List[float]:
    seconds_list: List[float] = []
    raw_entries: Any = None
    if isinstance(times, Mapping):
        for key in (
            "requested_delay_seconds",
            "requested_seconds",
            "seconds",
            "durations",
        ):
            if key in times:
                raw_entries = times[key]
                break
    else:
        raw_entries = times

    if raw_entries is None:
        raw_entries = []

    if isinstance(raw_entries, (list, tuple, set)):
        entries_iterable = list(raw_entries)
    else:
        entries_iterable = [raw_entries] if raw_entries else []

    for entry in entries_iterable:
        if entry is None:
            continue
        if isinstance(entry, (int, float)):
            seconds = float(entry)
        elif isinstance(entry, str):
            seconds = _parse_time_value(entry)
        else:
            raise WorkflowError("Time entries must be numeric or duration strings.")
        if seconds <= 0:
            raise WorkflowError("Time entries must be greater than zero.")
        seconds_list.append(seconds)

    if not seconds_list:
        raise WorkflowError("Time-based mode requires at least one duration.")
    return seconds_list


def _resolve_rounds(mode: Optional[str], rounds: Optional[Iterable[Any]], times: Any) -> List[int]:
    provided_mode = _normalize_mode_value(mode)
    candidate_rounds: Any = rounds
    candidate_times: Any = times
    candidate_mode: Optional[str] = provided_mode

    if isinstance(rounds, Mapping):
        spec = rounds
        embedded_mode = spec.get("mode") or spec.get("rounds_mode")
        if candidate_mode is None and embedded_mode is not None:
            candidate_mode = _normalize_mode_value(embedded_mode)
        for key in ("rounds", "values", "entries"):
            if key in spec:
                candidate_rounds = spec[key]
                break
        else:
            candidate_rounds = spec.get("round_values", candidate_rounds)
        if candidate_times is None:
            for key in ("durations", "times", "seconds"):
                if key in spec:
                    candidate_times = spec[key]
                    break

    if candidate_mode is None:
        if candidate_times is not None:
            candidate_mode = "time"
        elif _looks_like_time_entries(candidate_rounds):
            candidate_mode = "time"
        else:
            candidate_mode = "rounds"

    if candidate_mode == "time":
        seconds_list = _normalize_time_entries(candidate_times if candidate_times is not None else candidate_rounds)
        hashrate = _calibrate_hashrate()
        resolved = {max(1, int(hashrate * seconds)) for seconds in seconds_list}
        return sorted(resolved)

    normalized_rounds = _normalize_rounds(candidate_rounds)
    if not normalized_rounds:
        raise WorkflowError("At least one round value is required.")
    return normalized_rounds


@dataclass
class NewWorkflowDecisions:
    rounds: List[int]
    protect_with_password: bool
    password: Optional[str]
    entropy_hint: Optional[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CloneSourceCapsule:
    rounds: int
    puzzle: bytes
    secret_checksum: bytes
    hash_function: str
    start_value: Optional[bytes]
    start_value_protected: Optional[Mapping[str, Any]]
    format: str = "lethesafe-puzzle"
    version: int = core_maker.PUZZLE_VERSION
    legacy_plaintext_ignored: bool = False


@dataclass
class CloneWorkflowDecisions:
    source: CloneSourceCapsule
    rounds: List[int]
    reuse_password: bool
    store_plain_start_value: bool
    new_password: Optional[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _ensure_token(run_token: Optional[str]) -> str:
    if not run_token or not isinstance(run_token, str):
        raise WorkflowError("A valid run token is required.")
    return run_token


def progress_start(run_token: str, total_rounds: Optional[int]) -> None:
    abort_manager.register(run_token)
    progress_tracker.start(run_token, total_rounds)


def progress_update(run_token: str, completed_rounds: int, elapsed_time: float) -> None:
    progress_tracker.update(run_token, completed_rounds, elapsed_time)


def progress_finish(run_token: str, status: str) -> None:
    progress_tracker.finish(run_token, status)
    abort_manager.clear(run_token)


def is_cancelled(run_token: str) -> bool:
    return abort_manager.is_cancelled(run_token)


def ensure_not_cancelled(run_token: str) -> None:
    if is_cancelled(run_token):
        raise WorkflowError("Run was cancelled before execution.")


def workflow_new(
    *,
    run_token: str,
    rounds: Sequence[Any],
    protect_with_password: bool,
    password: Optional[str],
    entropy_hint: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Coordinate the creation workflow for new capsules."""
    token = _ensure_token(run_token)

    metadata_payload: Mapping[str, Any] = metadata or {}
    # Phase 1 – Entscheidungsphase (bereits finalisiert, kein Re-Entry)
    final_rounds = _resolve_rounds(None, rounds, None)
    if protect_with_password and not password:
        raise WorkflowError("A password is required when password protection is enabled.")
    decisions = NewWorkflowDecisions(
        rounds=final_rounds,
        protect_with_password=protect_with_password,
        password=password,
        entropy_hint=entropy_hint,
        metadata=metadata_payload,
    )

    total_rounds = max(decisions.rounds) if decisions.rounds else None
    progress_ceiling = total_rounds or 0
    progress_start(token, total_rounds)
    status = "failed"
    progress_start_time = time.perf_counter()
    reported_rounds = 0

    def _progress_hook(local_completed: int) -> None:
        nonlocal reported_rounds
        target_value = local_completed
        if progress_ceiling:
            target_value = min(target_value, progress_ceiling)
        if target_value <= reported_rounds:
            return
        elapsed = max(time.perf_counter() - progress_start_time, 0.0)
        reported_rounds = target_value
        progress_update(token, reported_rounds, elapsed)

    try:
        ensure_not_cancelled(token)
        start_password = decisions.password if decisions.protect_with_password else None
        entropy_bytes = _entropy_hint_to_bytes(decisions.entropy_hint)
        shared_material = create_capsule_material(start_password, entropy_bytes)

        chunk_base = max(decisions.rounds) if decisions.rounds else 0
        chunk_size = max(1, chunk_base // 20) if chunk_base > 0 else 1
        hash_map = _compute_hashes_for_targets(
            shared_material.start_value,
            decisions.rounds,
            progress_callback=_progress_hook,
            abort_check=lambda: ensure_not_cancelled(token),
            progress_chunk_size=chunk_size,
        )

        capsule_results: List[CapsuleResult] = []
        for round_value in decisions.rounds:
            ensure_not_cancelled(token)
            hash_value = hash_map.get(int(round_value))
            if hash_value is None:
                raise WorkflowError("Hash chain computation failed for requested round.")
            try:
                capsule = create_capsule_web(
                    rounds=round_value,
                    hash_function="sha256",
                    start_password=start_password,
                    entropy_bytes=entropy_bytes,
                    progress_callback=None,
                    abort_check=None,
                    progress_chunk_size=chunk_size,
                    material=shared_material,
                    precomputed_hash=hash_value,
                )
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc
            capsule_results.append(capsule)

        files, serialized_capsules = _serialize_capsule_batch(capsule_results)
        primary_capsule = capsule_results[0]
        # Explicit: secret is ephemeral and only returned for immediate disclosure
        secret_b64 = base64.b64encode(primary_capsule.secret).decode("ascii")

        result = {
            "status": "ok",
            "mode": "new",
            "run_token": token,
            "capsules": serialized_capsules,
            "files": files,
            "secret_base64": secret_b64,
            "secret_checksum_hex": primary_capsule.secret_checksum.hex(),
            "password_required": bool(start_password),
            "metadata": decisions.metadata,
        }
        status = "completed"
        return result
    finally:
        progress_finish(token, status)


def workflow_clone(
    *,
    run_token: str,
    source: CloneSourceCapsule,
    rounds: Sequence[Any],
    reuse_password: bool,
    store_plain_start_value: bool,
    new_password: Optional[str],
    source_password: Optional[str],
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Coordinate the clone workflow with strict phase ordering."""
    token = _ensure_token(run_token)
    metadata_payload: Mapping[str, Any] = metadata or {}
    # Phase 1 – Entscheidungsphase (bereits finalisiert, kein Re-Entry)
    final_rounds = _resolve_rounds(None, rounds, None)
    if not isinstance(source.puzzle, (bytes, bytearray)):
        raise WorkflowError("Source capsule must provide puzzle bytes.")
    if source.secret_checksum is None:
        raise WorkflowError("Source capsule requires a secret checksum.")
    if source.rounds <= 0:
        raise WorkflowError("Source capsule must contain a positive round count.")

    if reuse_password and store_plain_start_value:
        raise WorkflowError("Cannot reuse the original password and store the start value unprotected.")
    if not reuse_password and not store_plain_start_value and not new_password:
        raise WorkflowError("A new password must be provided when no protection is inherited.")

    decisions = CloneWorkflowDecisions(
        source=source,
        rounds=final_rounds,
        reuse_password=reuse_password,
        store_plain_start_value=store_plain_start_value,
        new_password=new_password,
        metadata=metadata_payload,
    )

    # NOTE:
    # Password/protection semantics are intentionally frozen in the core.
    # reuse_password/store_plain_start_value/new_password are validated here but not forwarded.

    legacy_plaintext_warning = bool(source.legacy_plaintext_ignored)
    warnings: List[str] = []
    if legacy_plaintext_warning:
        warnings.append(LEGACY_PLAINTEXT_WARNING)

    source_rounds = int(source.rounds)
    combined_rounds = list(final_rounds) + [source_rounds]
    max_rounds = max(combined_rounds) if combined_rounds else source_rounds
    progress_start(token, max_rounds)
    progress_ceiling = max_rounds or 0
    progress_start_time = time.perf_counter()
    reported_rounds = 0

    def _progress_hook(local_completed: int) -> None:
        nonlocal reported_rounds
        target_value = local_completed
        if progress_ceiling:
            target_value = min(target_value, progress_ceiling)
        if target_value <= reported_rounds:
            return
        elapsed = max(time.perf_counter() - progress_start_time, 0.0)
        reported_rounds = target_value
        progress_update(token, reported_rounds, elapsed)
    status = "failed"
    try:
        ensure_not_cancelled(token)
        start_value_bytes = source.start_value
        start_value_protected = source.start_value_protected
        if start_value_bytes is None:
            if not isinstance(start_value_protected, Mapping):
                raise WorkflowError("Source capsule is missing the start value.")
            if not source_password:
                raise WorkflowError("Original PWD is required to decrypt the protected start value.")
            try:
                start_value_bytes = decrypt_start_value(start_value_protected, source_password)
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc

        chunk_base = max_rounds
        chunk_size = max(1, chunk_base // 20) if chunk_base > 0 else 1
        hash_map = _compute_hashes_for_targets(
            start_value_bytes,
            combined_rounds,
            progress_callback=_progress_hook,
            abort_check=lambda: ensure_not_cancelled(token),
            progress_chunk_size=chunk_size,
        )

        hash_n_source = hash_map.get(source_rounds)
        if hash_n_source is None:
            raise WorkflowError("Source capsule hash computation failed.")
        secret_k = core_maker.xor_bytes(bytes(source.puzzle), hash_n_source)
        expected_checksum = source.secret_checksum
        if compute_secret_checksum(secret_k) != expected_checksum:
            raise WorkflowError("Source capsule checksum mismatch.")

        start_value_protected_override: Optional[Dict[str, Any]] = None
        password_required = False
        if decisions.store_plain_start_value:
            start_value_protected_override = None
            password_required = False
        elif decisions.reuse_password:
            start_value_protected_override = start_value_protected if isinstance(start_value_protected, Mapping) else None
            password_required = bool(start_value_protected_override)
        else:
            if not decisions.new_password:
                raise WorkflowError("A new password must be provided when not reusing the original password.")
            start_value_protected_override = core_maker.protect_start_value(start_value_bytes, decisions.new_password)
            password_required = True

        source_payload = {
            "format": source.format,
            "version": source.version,
            "mode": "clone",
            "rounds": source_rounds,
            "hash_function": source.hash_function,
            "puzzle": bytes(source.puzzle),
            "start_value": start_value_bytes,
            "start_value_protected": start_value_protected,
            "secret_checksum": expected_checksum,
        }

        capsule_results: List[CapsuleResult] = []
        for round_value in decisions.rounds:
            ensure_not_cancelled(token)
            hash_value = hash_map.get(int(round_value))
            if hash_value is None:
                raise WorkflowError("Hash chain computation failed for requested round.")
            try:
                capsule = clone_capsule_web(
                    source=source_payload,
                    rounds=round_value,
                    progress_callback=None,
                    abort_check=None,
                    progress_chunk_size=chunk_size,
                    precomputed_hash_source=hash_n_source,
                    precomputed_hash_new=hash_value,
                    secret_override=secret_k,
                )
            except ValueError as exc:
                raise WorkflowError(str(exc)) from exc
            capsule.start_value_protected = start_value_protected_override
            capsule_results.append(capsule)

        files, serialized_capsules = _serialize_capsule_batch(capsule_results)
        primary_capsule = capsule_results[0]
        secret_b64 = base64.b64encode(primary_capsule.secret).decode("ascii")

        result = {
            "status": "ok",
            "mode": "clone",
            "run_token": token,
            "capsules": serialized_capsules,
            "files": files,
            "secret_base64": secret_b64,
            "secret_checksum_hex": primary_capsule.secret_checksum.hex(),
            "password_required": password_required,
            "metadata": decisions.metadata,
            "base_rounds": source_rounds,
        }
        if warnings:
            result["warnings"] = warnings
        status = "completed"
        return result
    finally:
        progress_finish(token, status)


def workflow_unlock(
    *,
    request_data: Dict[str, Any],
    token: str,
) -> Dict[str, Any]:
    """
    UNLOCK-Workflow (Web)

    Ablauf:
    1. Validierung
    2. Progress starten
    3. Core-Unlock ausführen
    4. Ergebnis zurückgeben
    """

    run_token = _ensure_token(token)
    legacy_plaintext_warning = bool(request_data.pop("legacy_plaintext_ignored", False))
    if not isinstance(request_data, Mapping):
        raise WorkflowError("Missing puzzle data.")

    try:
        rounds_value = request_data["rounds"]
    except KeyError as exc:
        raise WorkflowError("Unlock request missing valid 'rounds'.") from exc
    if not isinstance(rounds_value, int):
        raise WorkflowError("Capsule corrupted or tampered")
    if rounds_value <= 0:
        raise WorkflowError("Rounds must be a positive integer.")

    puzzle_bytes = request_data.get("puzzle_bytes")
    if not isinstance(puzzle_bytes, (bytes, bytearray)):
        raise WorkflowError("Puzzle data must be provided as bytes.")
    puzzle = bytes(puzzle_bytes)

    start_value_protected = request_data.get("start_value_protected")
    start_value_raw = request_data.get("start_value")
    if start_value_protected is None and start_value_raw is None:
        raise WorkflowError("Unlock request requires either start_value or start_value_protected.")

    password = request_data.get("password")
    start_value: Optional[bytes] = None
    if start_value_protected is not None:
        if not isinstance(start_value_protected, Mapping):
            raise WorkflowError("start_value_protected must be a mapping.")
        if password is None:
            raise WorkflowError("Password is required to decrypt the protected start value.")
        if not isinstance(password, str):
            raise WorkflowError("Password must be a string.")
        try:
            start_value = decrypt_start_value(start_value_protected, password)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
    else:
        if not isinstance(start_value_raw, (bytes, bytearray)):
            raise WorkflowError("start_value must be bytes when provided directly.")
        start_value = bytes(start_value_raw)

    secret_checksum_raw = request_data.get("secret_checksum")
    if secret_checksum_raw is None:
        raise WorkflowError("Unlock request requires secret_checksum.")
    if not isinstance(secret_checksum_raw, (bytes, bytearray)):
        raise WorkflowError("secret_checksum must be bytes.")
    secret_checksum = bytes(secret_checksum_raw)

    progress_started = False
    try:
        progress_start(run_token, rounds_value)
        progress_started = True

        start_time = time.time()
        current_hash = _hash_chain_with_progress(
            start_value,
            rounds_value,
            run_token=run_token,
            start_time=start_time,
        )

        secret_k = recover_secret_k(
            puzzle,
            current_hash,
            secret_checksum=secret_checksum,
            hash_function=request_data.get("hash_function")
        )

        secret_base64 = base64.b64encode(secret_k).decode("ascii")
        progress_finish(run_token, status="completed")
        result_payload = {
            "status": "ok",
            "run_token": run_token,
            "secret_base64": secret_base64,
        }
        if legacy_plaintext_warning:
            result_payload["warnings"] = [LEGACY_PLAINTEXT_WARNING]
        return result_payload
    except Exception:
        if progress_started:
            progress_finish(run_token, status="failed")
        raise


def _phase2_execute_new(decisions: NewWorkflowDecisions, *, run_token: str) -> Dict[str, Any]:
    """Placeholder for Phase 2 of the NEW workflow.

    Expected responsibilities:
      * Generate entropy via ``core.core_maker.generate_start_value`` and
        ``core.core_maker.generate_random_k``.
      * Apply password protection using ``core.core_maker.protect_start_value``.
      * Run the hash chains via ``core.core_maker.compute_hashes_for_rounds``.
      * Update progress through ``progress_tracker.update`` and honor aborts.
    """
    raise NotImplementedError("Phase 2 of workflow_new must call the canonical core.")


def _phase3_finalize_new(
    decisions: NewWorkflowDecisions,
    phase2_output: Mapping[str, Any],
) -> Dict[str, Any]:
    """Placeholder for Phase 3 of the NEW workflow (result assembly)."""
    raise NotImplementedError("Phase 3 of workflow_new must format the response payload.")


def _phase2_execute_clone(decisions: CloneWorkflowDecisions, *, run_token: str) -> Dict[str, Any]:
    """Placeholder for Phase 2 of the CLONE workflow.

    Must:
      * Decrypt the original start value using ``core.core_unlocker``.
      * Reconstruct ``[K]`` via ``core.core_unlocker.compute_hash_chain``.
      * Never request further interaction once hashing began.
      * Continuously update ``progress_tracker`` with combined progress.
    """
    raise NotImplementedError("Phase 2 of workflow_clone must call the canonical core.")


def _phase3_finalize_clone(
    decisions: CloneWorkflowDecisions,
    phase2_output: Mapping[str, Any],
    *,
    run_token: str,
) -> Dict[str, Any]:
    """Placeholder for Phase 3 of the CLONE workflow (new capsule creation)."""
    raise NotImplementedError("Phase 3 of workflow_clone must produce the new capsules.")


def _entropy_hint_to_bytes(value: Optional[str]) -> Optional[bytes]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowError("Entropy hints must be provided as strings.")
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.encode("utf-8")


def _serialize_capsule_batch(
    capsules: Sequence[CapsuleResult],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    files: List[Dict[str, Any]] = []
    payloads: List[Dict[str, Any]] = []
    for capsule in capsules:
        payload = _capsule_to_public_payload(capsule)
        payloads.append(payload)
        filename = f"zeitkapsel_{capsule.rounds}.json"
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        files.append({"name": filename, "content": content})
    return files, payloads


def _capsule_to_public_payload(capsule: CapsuleResult) -> Dict[str, Any]:
    payload = {
        "format": capsule.format,
        "version": capsule.version,
        "mode": capsule.mode,
        "rounds": capsule.rounds,
        "hash_function": capsule.hash_function,
        "puzzle_base64": base64.b64encode(capsule.puzzle).decode("ascii"),
        "secret_checksum_hex": capsule.secret_checksum.hex(),
    }
    if capsule.start_value_protected is None:
        payload["start_value_base64"] = base64.b64encode(capsule.start_value).decode("ascii")
    if capsule.start_value_protected is not None:
        payload["start_value_protected"] = capsule.start_value_protected
    return payload


    def _get_value(*keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
        return None

    try:
        rounds_value = payload["rounds"]
    except KeyError as exc:
        raise WorkflowError("Invalid round information in source capsule.") from exc
    if not isinstance(rounds_value, int):
        raise WorkflowError("Capsule corrupted or tampered")

    puzzle_encoded = _get_value("puzzle_base64", "puzzle")
    checksum_hex = _get_value("secret_checksum_hex", "secret_checksum")
    if puzzle_encoded is None or checksum_hex is None:
        raise WorkflowError("Source capsule missing required fields.")

    start_value_encoded = _get_value("start_value_base64", "start_value")
    start_value_protected = payload.get("start_value_protected")
    password_protected = start_value_protected is not None
    legacy_plaintext_ignored = False

    try:
        puzzle = decode_puzzle_base64(puzzle_encoded)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Capsule corrupted or tampered") from exc

    start_value = None
    if start_value_encoded is not None:
        if password_protected:
            legacy_plaintext_ignored = True
        else:
            try:
                start_value = base64.b64decode(start_value_encoded)
            except (TypeError, ValueError) as exc:
                raise WorkflowError("Source capsule contains invalid start value data.") from exc

    try:
        checksum_bytes = bytes.fromhex(str(checksum_hex))
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Source capsule contains an invalid checksum encoding.") from exc

    decoded = {
        "format": payload.get("format", "lethesafe-puzzle"),
        "version": int(payload.get("version", core_maker.PUZZLE_VERSION)),
        "mode": payload.get("mode", "new"),
        "rounds": rounds_value,
        "hash_function": str(payload.get("hash_function", "sha256")),
        "puzzle": puzzle,
        "start_value": start_value,
        "start_value_protected": start_value_protected,
        "secret_checksum": checksum_bytes,
    }
    if legacy_plaintext_ignored:
        decoded["legacy_plaintext_ignored"] = True
    return decoded
