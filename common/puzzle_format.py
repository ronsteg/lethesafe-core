import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

PUZZLE_FORMAT = "lethesafe-puzzle"
PUZZLE_VERSION = 2
DEFAULT_HASH_FUNCTION = "sha256"

_CANONICAL_FIELD_ORDER = [
    "format",
    "version",
    "mode",
    "hash_function",
    "rounds",
    "puzzle_base64",
    "secret_checksum_hex",
    "start_value_protected",
    "start_value",
]

_LEGACY_ALIASES = {
    "puzzle": "puzzle_base64",
    "secret_checksum": "secret_checksum_hex",
    "start_value_base64": "start_value",
}


def _filter_none_values(source: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in source.items() if value is not None}


def normalize_puzzle_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Normalizes puzzle data so that only canonical fields remain at the top level
    and legacy aliases are converted.
    """
    working: Dict[str, Any] = dict(data)

    for legacy, canonical in _LEGACY_ALIASES.items():
        if canonical not in working and legacy in working:
            working[canonical] = working[legacy]
        working.pop(legacy, None)

    raw_meta = working.pop("meta", None)
    meta: Dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}

    normalized: Dict[str, Any] = {}
    for field in _CANONICAL_FIELD_ORDER:
        if field in working:
            normalized[field] = working.pop(field)

    extra_meta = working
    if extra_meta:
        if meta:
            meta = {**meta, **extra_meta}
        else:
            meta = extra_meta

    if meta:
        normalized["meta"] = meta

    return normalized


def write_puzzle_v2_canonical(
    path: Path,
    core_fields: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Serializes puzzle data in the canonical v2 format.
    """
    payload: Dict[str, Any] = {
        "format": PUZZLE_FORMAT,
        "version": PUZZLE_VERSION,
        "hash_function": DEFAULT_HASH_FUNCTION,
    }
    payload.update(core_fields)

    required_fields = ("mode", "rounds", "puzzle_base64", "secret_checksum_hex")
    for field in required_fields:
        if field not in payload:
            raise ValueError(f"Missing required puzzle field: {field}")

    if "start_value_protected" not in payload and "start_value" not in payload:
        raise ValueError("Puzzle must contain start_value or start_value_protected.")

    disallowed = ("puzzle", "secret_checksum", "start_value_base64")
    for field in disallowed:
        if field in payload:
            raise ValueError(f"Field '{field}' is not allowed in canonical payloads.")

    if meta:
        cleaned_meta = _filter_none_values(dict(meta))
        if cleaned_meta:
            payload["meta"] = cleaned_meta

    payload = {key: value for key, value in payload.items() if value is not None}

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
