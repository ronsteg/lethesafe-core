"""Flask WebApp für Lethesafe (bereinigt: Transport-Layer only).

- Keine Kryptologie in app.py
- Keine Hashloops / Kalibrierung in app.py
- Zeit→Runden-Entscheidung liegt in web_core/workflows.py (Phase 1)
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from web.version import __version__ as WEB_VERSION
from core.version import __version__ as CORE_VERSION
from core.bench import measure_hashrate
from core.core_unlocker import decode_puzzle_base64, SUPPORTED_HASH_FUNCTION

import web_core.workflows as workflows_module
from web_core.workflows import (
    WorkflowError,
    CloneSourceCapsule,
    workflow_clone,
    workflow_new,
    workflow_unlock,
)
from common.puzzle_format import normalize_puzzle_payload, write_puzzle_v2_canonical

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB Upload-Limit


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@app.context_processor
def inject_versions():
    return {
        "WEB_VERSION": WEB_VERSION,
        "CORE_VERSION": CORE_VERSION,
    }

_RESULT_CACHE: dict[str, tuple[str, bytes]] = {}
_TIME_CONFIGS: "OrderedDict[str, Mapping[str, Any]]" = OrderedDict()
_TIME_CONFIG_TTL_SECONDS = 15 * 60
_TIME_CONFIG_LIMIT = 64
_PENDING_RUN_TOKENS: dict[str, float] = {}
_RUN_TOKEN_TTL_SECONDS = 60 * 30
_TIME_SPEC_PATTERN = re.compile(r"\s*(\d+)\s*([smhdSMHD])\s*")
_CAPSULE_ROUNDS_PATTERN = re.compile(r"_(\d+)r?$", re.IGNORECASE)


# ─────────────────────────────────────────────
# Helper: Ergebnis-Downloads
# ─────────────────────────────────────────────

def _store_result(name: str, content: bytes) -> str:
    token = uuid4().hex
    _RESULT_CACHE[token] = (name, content)
    return token


def _store_text_file(name: str, text: str) -> dict[str, str]:
    token = _store_result(name, text.encode("utf-8"))
    return {"name": name, "token": token}


# ─────────────────────────────────────────────
# Helper: Input / Parsing
# ─────────────────────────────────────────────

def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def parse_rounds_input(raw: str) -> list[int]:
    """Parst eine durch Komma getrennte Rundenliste: '1000,2000,3000'."""
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    rounds: list[int] = []
    seen: set[int] = set()
    for p in parts:
        if not p:
            continue
        try:
            v = int(p)
        except ValueError as exc:
            raise ValueError(f"Ungültiger Rundenwert: {p}") from exc
        if v <= 0:
            raise ValueError("Runden müssen positive Ganzzahlen sein.")
        if v in seen:
            continue
        rounds.append(v)
        seen.add(v)
    return rounds


def parse_time_specs(raw: str) -> list[str]:
    """Parst Zeitangaben als Liste von Tokens, z. B. '3d,12h,30m'."""
    raw = (raw or "").strip()
    if not raw:
        return []
    normalized = raw.replace(";", " ")
    parts = [p.strip() for p in re.split(r"[\s,]+", normalized)]
    return [p for p in parts if p]


def _require_run_token() -> str:
    token = request.headers.get("X-Run-Token", "").strip()
    if not token:
        raise ValueError("Missing X-Run-Token header.")
    return token


def _is_abort_error(msg: str) -> bool:
    msg_l = (msg or "").lower()
    return "abbruch" in msg_l or "cancel" in msg_l or "aborted" in msg_l or "cancelled" in msg_l


def _cleanup_time_configs() -> None:
    if not _TIME_CONFIGS:
        return
    now = time.time()
    expired_keys = [key for key, entry in _TIME_CONFIGS.items() if now - float(entry.get("created_at", 0.0)) > _TIME_CONFIG_TTL_SECONDS]
    for key in expired_keys:
        _TIME_CONFIGS.pop(key, None)
    while len(_TIME_CONFIGS) > _TIME_CONFIG_LIMIT:
        _TIME_CONFIGS.popitem(last=False)


def _store_time_config(entry: Mapping[str, Any]) -> str:
    token = uuid4().hex
    payload = dict(entry)
    payload["created_at"] = time.time()
    _TIME_CONFIGS[token] = payload
    _cleanup_time_configs()
    return token


def _get_time_config(token: str) -> Mapping[str, Any] | None:
    _cleanup_time_configs()
    entry = _TIME_CONFIGS.get(token)
    if not entry:
        return None
    return entry


def _parse_time_value(token: str) -> float:
    match = _TIME_SPEC_PATTERN.fullmatch(token or "")
    if not match:
        raise ValueError("Zeitwerte müssen im Format <Zahl><Einheit> angegeben werden (z. B. 10m).")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("Zeitwerte müssen größer als 0 sein.")
    unit = match.group(2).lower()
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(amount * factors[unit])


def _convert_time_specs_to_seconds(specs: list[str]) -> list[float]:
    seconds: list[float] = []
    for spec in specs:
        seconds.append(_parse_time_value(spec))
    return seconds


def _cleanup_run_tokens() -> None:
    if not _PENDING_RUN_TOKENS:
        return
    now = time.time()
    expired = [token for token, issued in _PENDING_RUN_TOKENS.items() if now - issued > _RUN_TOKEN_TTL_SECONDS]
    for token in expired:
        _PENDING_RUN_TOKENS.pop(token, None)


def _issue_run_token() -> str:
    _cleanup_run_tokens()
    token = uuid4().hex
    _PENDING_RUN_TOKENS[token] = time.time()
    return token


def _claim_run_token(token: str) -> str:
    _cleanup_run_tokens()
    if token not in _PENDING_RUN_TOKENS:
        raise ValueError("Ungültiger oder abgelaufener Run-Token.")
    _PENDING_RUN_TOKENS.pop(token, None)
    return token


def _get_result_value(result: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(result, Mapping):
        return default
    return result.get(key, default)


def _merge_meta_sources(*sources: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key, value in source.items():
            if value is not None:
                merged[key] = value
    return merged


def _render_capsule_file_bytes(core_fields: Mapping[str, Any], meta: Mapping[str, Any] | None) -> bytes:
    fd, tmp_name = tempfile.mkstemp(prefix="lethesafe_capsule_", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        write_puzzle_v2_canonical(tmp_path, dict(core_fields), meta=dict(meta) if meta else None)
        content = tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return content


def _canonical_capsule_files(result: Mapping[str, Any]) -> list[tuple[str, bytes]]:
    capsules = _get_result_value(result, "capsules", []) or []
    if not isinstance(capsules, list) or not capsules:
        return []
    shared_meta = _get_result_value(result, "metadata", {}) or {}
    fallback_mode = str(_get_result_value(result, "mode", "") or "new")
    files: list[tuple[str, bytes]] = []
    used_names: set[str] = set()
    for idx, raw_capsule in enumerate(capsules, start=1):
        normalized = normalize_puzzle_payload(raw_capsule)
        rounds_value = int(normalized["rounds"])
        base_name = f"Zeitkapsel_{rounds_value}r"
        filename = f"{base_name}.json"
        suffix = 1
        while filename in used_names:
            suffix += 1
            filename = f"{base_name}_{suffix}.json"
        used_names.add(filename)

        core_fields: dict[str, Any] = {
            "mode": normalized.get("mode") or fallback_mode,
            "rounds": rounds_value,
            "puzzle_base64": normalized["puzzle_base64"],
            "secret_checksum_hex": normalized["secret_checksum_hex"],
        }
        if "hash_function" in normalized:
            core_fields["hash_function"] = normalized["hash_function"]
        if "start_value_protected" in normalized:
            core_fields["start_value_protected"] = normalized["start_value_protected"]
        if "start_value" in normalized:
            core_fields["start_value"] = normalized["start_value"]

        capsule_meta = normalized.get("meta") if isinstance(normalized.get("meta"), Mapping) else None
        combined_meta = _merge_meta_sources(
            shared_meta if isinstance(shared_meta, Mapping) else {},
            capsule_meta or {},
            {
                "created": utc_timestamp(),
                "capsule_index": idx,
                "capsule_rounds": rounds_value,
            },
        )
        meta_payload = combined_meta or None
        content = _render_capsule_file_bytes(core_fields, meta_payload)
        files.append((filename, content))
    return files


def _normalize_result_files(result: Mapping[str, Any]) -> list[tuple[str, bytes]]:
    """Erwartet, dass Workflows Dateien als Liste von (name, bytes|str) liefern."""
    canonical_files = _canonical_capsule_files(result)
    if canonical_files:
        return canonical_files

    files = _get_result_value(result, "files", []) or []
    normalized: list[tuple[str, bytes]] = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip() or "Zeitkapsel.json"
        content = item.get("content", b"")
        if isinstance(content, str):
            content_b = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            content_b = bytes(content)
        else:
            content_b = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
        normalized.append((name, content_b))
    return normalized


def _extract_secret_value(result: Mapping[str, Any]) -> str:
    # Support mehrere mögliche Keys, ohne Semantik zu ändern:
    for key in ("secret", "secret_base64", "k", "K"):
        v = _get_result_value(result, key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise ValueError("Kein Secret in Workflow-Ergebnis gefunden.")


def _format_capsule_label(rounds_value: int) -> str:
    return f"zeitkapsel_{rounds_value}r"


def _derive_capsule_names(
    base_names: list[str],
    result: Mapping[str, Any],
    rounds_hint: list[int] | None = None,
) -> list[str]:
    def _append_round(target: list[int], seen: set[int], value: Any) -> None:
        try:
            rounds_int = int(value)
        except (TypeError, ValueError):
            return
        if rounds_int <= 0 or rounds_int in seen:
            return
        seen.add(rounds_int)
        target.append(rounds_int)

    ordered_rounds: list[int] = []
    seen_rounds: set[int] = set()

    if rounds_hint:
        for value in rounds_hint:
            _append_round(ordered_rounds, seen_rounds, value)

    capsules = _get_result_value(result, "capsules", []) or []
    for capsule in capsules:
        if not isinstance(capsule, Mapping):
            continue
        _append_round(ordered_rounds, seen_rounds, capsule.get("rounds"))

    if not ordered_rounds and base_names:
        for name in base_names:
            match = _CAPSULE_ROUNDS_PATTERN.search(name)
            if match:
                _append_round(ordered_rounds, seen_rounds, match.group(1))

    if ordered_rounds:
        return [_format_capsule_label(value) for value in ordered_rounds]
    return list(base_names)


def _build_password_header(capsule_names: list[str], password: str) -> str:
    if capsule_names:
        return f"Passwort der {', '.join(capsule_names)} für den Startwert [S]:\n{password}\n"
    return f"Passwort für den Startwert [S]:\n{password}\n"


def _build_secret_header(capsule_names: list[str], secret_value: str) -> str:
    if capsule_names:
        return f"Zielzeichenkette [K] der {', '.join(capsule_names)}:\n{secret_value}\n"
    return f"Zielzeichenkette [K]:\n{secret_value}\n"


def _build_unlock_request_data(payload: Mapping[str, Any], password: str | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Puzzle-Datei ist kein JSON-Objekt.")

    normalized_payload = normalize_puzzle_payload(payload)

    try:
        rounds_value = normalized_payload["rounds"]
    except KeyError as exc:
        raise ValueError("Puzzle-Datei enthält keine gültige Rundenzahl.") from exc
    if not isinstance(rounds_value, int):
        raise ValueError("Capsule corrupted or tampered")
    if rounds_value <= 0:
        raise ValueError("Puzzle-Datei enthält keine gültige Rundenzahl.")

    puzzle_b64 = normalized_payload.get("puzzle_base64")
    if not puzzle_b64:
        raise ValueError("Puzzle-Datei enthält keine Puzzle-Daten.")
    try:
        puzzle_bytes = decode_puzzle_base64(puzzle_b64)
    except (TypeError, ValueError) as exc:
        raise ValueError("Capsule corrupted or tampered") from exc

    start_value_bytes = None
    legacy_plaintext_ignored = False
    start_value_b64 = normalized_payload.get("start_value")
    if start_value_b64:
        try:
            start_value_bytes = base64.b64decode(start_value_b64)
        except (TypeError, ValueError) as exc:
            raise ValueError("Startwert ist beschädigt.") from exc

    start_value_protected = normalized_payload.get("start_value_protected")
    password_protected = start_value_protected is not None
    if password_protected and start_value_bytes is not None:
        start_value_bytes = None
        legacy_plaintext_ignored = True
    if start_value_bytes is None and start_value_protected is None:
        raise ValueError("Puzzle-Datei enthält keinen Startwert.")
    if start_value_protected is not None and not isinstance(start_value_protected, Mapping):
        raise ValueError("Protected-Startwert ist beschädigt.")

    secret_checksum_bytes = None
    secret_checksum_hex = normalized_payload.get("secret_checksum_hex")
    if secret_checksum_hex:
        try:
            secret_checksum_bytes = bytes.fromhex(secret_checksum_hex)
        except (TypeError, ValueError) as exc:
            raise ValueError("Checksumme ist beschädigt.") from exc

    hash_function_raw = normalized_payload.get("hash_function")
    hash_function: str | None = None
    if hash_function_raw is not None:
        if not isinstance(hash_function_raw, str) or not hash_function_raw.strip():
            raise ValueError("hash_function ist ungültig.")
        hash_function = hash_function_raw.strip()
        if hash_function != SUPPORTED_HASH_FUNCTION:
            raise ValueError("Unsupported hash_function in capsule")

    request_data: dict[str, Any] = {
        "rounds": rounds_value,
        "puzzle_bytes": puzzle_bytes,
    }
    if start_value_bytes is not None:
        request_data["start_value"] = start_value_bytes
    if start_value_protected is not None:
        request_data["start_value_protected"] = start_value_protected
    if legacy_plaintext_ignored:
        request_data["legacy_plaintext_ignored"] = True
    if secret_checksum_bytes is not None:
        request_data["secret_checksum"] = secret_checksum_bytes
    if hash_function is not None:
        request_data["hash_function"] = hash_function
    if password:
        request_data["password"] = password
    return request_data


# ─────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create")
def create_view():
    return render_template("create.html", form_data={})


@app.route("/clone")
def clone_view():
    return render_template("clone.html")


@app.route("/unlock")
def unlock_view():
    return render_template("unlock.html")


@app.route("/new", methods=["GET", "POST"])
def new_capsule():
    if request.method == "GET":
        return render_template("new.html")

    rounds_raw = (request.form.get("rounds", "") or "").strip()
    password_raw = (request.form.get("password", "") or "").strip()
    entropy_hint_raw = (request.form.get("entropy", "") or "").strip()

    form_data = {
        "rounds": rounds_raw,
        "password": password_raw,
        "entropy": entropy_hint_raw,
    }

    error: str | None = None
    try:
        rounds_value = int(rounds_raw)
        if rounds_value <= 0:
            raise ValueError("Rounds must be positive.")
    except (TypeError, ValueError):
        error = "Bitte eine positive Ganzzahl für die Runden angeben."
        rounds_value = None

    if error:
        return render_template("new.html", error=error, form_data=form_data), 400

    password = password_raw or None
    entropy_hint = entropy_hint_raw or None

    run_token = str(uuid4())

    try:
        result = workflow_new(
            run_token=run_token,
            rounds=[rounds_value],
            protect_with_password=bool(password),
            password=password,
            entropy_hint=entropy_hint,
            metadata={"source": "web_form_new"},
        )
    except WorkflowError as exc:
        return (
            render_template("new.html", error=str(exc), form_data=form_data),
            400,
        )

    return render_template(
        "new_result.html",
        result=result,
        run_token=run_token,
    )


@app.route("/download/<token>")
def download_file(token: str):
    entry = _RESULT_CACHE.get(token)
    if not entry:
        return redirect(url_for("index"))
    name, content = entry
    # mimetype: json oder text – hier generisch, Browser lädt ohnehin als Attachment
    return send_file(io.BytesIO(content), as_attachment=True, download_name=name, mimetype="application/octet-stream")


# ─────────────────────────────────────────────
# API: CREATE
# ─────────────────────────────────────────────

@app.post("/api/create")
def api_create():
    rounds_mode = (request.form.get("rounds_mode", "round_based") or "round_based").strip().lower()
    rounds_raw = request.form.get("rounds", "")
    entropy = (request.form.get("entropy", "") or "").strip()
    time_config_token = (request.form.get("time_config_token", "") or "").strip()

    protect_with_password = _as_bool(request.form.get("protect_with_password"), default=True)
    password = (request.form.get("ls_start_password", "") or "").strip()
    password_confirm = (request.form.get("ls_start_password_confirm", "") or "").strip()

    # Exports (UX)
    store_password_file = _as_bool(request.form.get("store_password_file"), default=True)
    store_secret_file = _as_bool(request.form.get("store_secret_file"), default=True)

    try:
        run_token = _claim_run_token(_require_run_token())

        # Phase 0 (HTTP): nur Absicht sammeln, keine Umrechnung
        if rounds_mode in {"time_based", "time", "zeit"}:
            config = _get_time_config(time_config_token)
            if not config:
                raise ValueError("Kalibrierung abgelaufen oder ungültig. Bitte erneut starten.")
            rounds_or_time = list(config.get("rounds", []))
            if not rounds_or_time:
                raise ValueError("Kalibrierung ohne Rundenzahl. Bitte erneut starten.")
            rounds_metadata = {
                "rounds_mode": "time_based",
                "inputs": config.get("inputs", []),
                "requested_seconds": config.get("requested_seconds", []),
                "estimated_runtime": config.get("estimated_runtime", []),
                "calibrated_hashrate": config.get("hashrate"),
            }
        else:
            rounds_list = parse_rounds_input(rounds_raw)
            if not rounds_list:
                raise ValueError("Bitte mindestens einen Rundenwert angeben.")
            rounds_or_time = rounds_list
            rounds_metadata = {"rounds_mode": "round_based"}

        # Passwortstrategie bleibt UX/Frontend – Workflows erzwingen Semantik
        if protect_with_password:
            if not password:
                raise ValueError("Bitte ein Passwort festlegen oder den Schutz deaktivieren.")
            if password != password_confirm:
                raise ValueError("Die Passwortfelder stimmen nicht überein.")
        else:
            password = None

        result = workflow_new(
            run_token=run_token,
            rounds=rounds_or_time,
            protect_with_password=protect_with_password,
            password=password,
            entropy_hint=entropy or None,
            metadata={
                "rounds_context": rounds_metadata,
            },
        )

    except (ValueError, WorkflowError) as exc:
        status = 499 if _is_abort_error(str(exc)) else 400
        return jsonify({"success": False, "error": str(exc)}), status

    # Dateien aus Workflow-Ergebnis (JSON + optional text)
    files_out: list[dict[str, str]] = []
    base_names: list[str] = []
    for name, content in _normalize_result_files(result):
        token = _store_result(name, content)
        files_out.append({"name": name, "token": token})
        base_names.append(name.rsplit(".", 1)[0])

    capsule_names = _derive_capsule_names(base_names, result)
    password_file = None
    password_required = bool(_get_result_value(result, "password_required", False))
    if store_password_file and password_required and password:
        header = _build_password_header(capsule_names, password)
        password_file = _store_text_file("lethesafe_start-pwd.txt", header)

    secret_value = _extract_secret_value(result)
    secret_file = None
    if store_secret_file and secret_value:
        secret_header = _build_secret_header(capsule_names, secret_value)
        secret_file = _store_text_file("lethesafe_k-key.txt", secret_header)

    return jsonify(
        {
            "success": True,
            "secret": secret_value,
            "files": files_out,
            "password_file": password_file,
            "password_required": password_required,
            "secret_file": secret_file,
        }
    )


@app.post("/api/create/time_calibrate")
def api_create_time_calibrate():
    delay_raw = (request.form.get("delay", "") or "").strip()
    try:
        time_specs = parse_time_specs(delay_raw)
        if not time_specs:
            raise ValueError("Bitte mindestens eine Zeitdauer angeben (z. B. 10m).")
        seconds = _convert_time_specs_to_seconds(time_specs)
        hashrate = measure_hashrate()
        rounds = [max(1, int(hashrate * value)) for value in seconds]
        estimated_runtime = [round_value / hashrate for round_value in rounds]
        token = _store_time_config(
            {
                "inputs": time_specs,
                "requested_seconds": seconds,
                "rounds": rounds,
                "estimated_runtime": estimated_runtime,
                "hashrate": hashrate,
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    return jsonify(
        {
            "success": True,
            "token": token,
            "inputs": time_specs,
            "requested_seconds": seconds,
            "rounds": rounds,
            "estimated_runtime": estimated_runtime,
            "hashrate": hashrate,
        }
    )


# ─────────────────────────────────────────────
# API: UNLOCK
# ─────────────────────────────────────────────

@app.post("/api/unlock")
def api_unlock():
    puzzle_file = request.files.get("puzzle_file")
    password = (request.form.get("ls_start_password", "") or "").strip()

    if not puzzle_file or not puzzle_file.filename:
        return jsonify({"success": False, "error": "Bitte eine Puzzle-Datei auswählen."}), 400

    try:
        run_token = _claim_run_token(_require_run_token())
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    raw_bytes = puzzle_file.read()

    # Kapseln sind JSON → capsule_payload (Mapping) an Workflow übergeben

    try:
        text = raw_bytes.decode("utf-8-sig")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        capsule_payload = json.loads(text)
        if not isinstance(capsule_payload, Mapping):
            raise ValueError("Puzzle-Datei ist kein JSON-Objekt.")
    except Exception:
        return jsonify({"success": False, "error": "Ungültige Puzzle-Datei (JSON erwartet)."}), 400


    try:
        request_data = _build_unlock_request_data(capsule_payload, password or None)
        result = workflow_unlock(
            request_data=request_data,
            token=run_token,
        )
    except WorkflowError as exc:
        status = 499 if _is_abort_error(str(exc)) else 400
        return jsonify({"success": False, "error": str(exc)}), status
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    warnings = list(_get_result_value(result, "warnings", []) or [])
    capsule_round = request_data.get("rounds") if isinstance(request_data, Mapping) else None
    rounds_hint = [capsule_round] if capsule_round is not None else None
    capsule_names = _derive_capsule_names([], result, rounds_hint=rounds_hint)
    secret_value = _extract_secret_value(result)
    secret_header = _build_secret_header(capsule_names, secret_value)
    secret_file = _store_text_file("lethesafe_k-key.txt", secret_header)
    return jsonify({"success": True, "secret": secret_value, "secret_file": secret_file, "warnings": warnings})


# ─────────────────────────────────────────────
# API: CLONE
# ─────────────────────────────────────────────

@app.post("/api/clone")
def api_clone():
    puzzle_file = request.files.get("puzzle_file")
    rounds_mode = (request.form.get("rounds_mode", "round_based") or "round_based").strip().lower()
    rounds_raw = request.form.get("rounds", "")
    delay_raw = request.form.get("delay", "")
    time_config_token = (request.form.get("time_config_token", "") or "").strip()

    reuse_password = _as_bool(request.form.get("reuse_password"), default=True)
    store_plain_start_value = _as_bool(
        request.form.get("store_plain") or request.form.get("store_plain_start_value"),
        default=False,
    )

    new_password = (request.form.get("ls_start_new_password", "") or "").strip()
    new_password_confirm = (request.form.get("ls_start_new_password_confirm", "") or "").strip()
    password = (request.form.get("ls_start_password", "") or "").strip()

    # Exports (UX)
    store_password_file = _as_bool(request.form.get("store_password_file"), default=True)
    store_secret_file = _as_bool(request.form.get("store_secret_file"), default=True)

    if not puzzle_file or not puzzle_file.filename:
        return jsonify({"success": False, "error": "Bitte eine Basiskapsel auswählen."}), 400

    try:
        run_token = _claim_run_token(_require_run_token())
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    raw_bytes = puzzle_file.read()
    try:
        text = raw_bytes.decode("utf-8-sig")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        source_payload = json.loads(text)
        source_normalized = normalize_puzzle_payload(source_payload)
    except Exception:
        return jsonify({"success": False, "error": "Ungültige Basiskapsel (JSON erwartet)."}), 400

    try:
        puzzle_b64 = source_normalized["puzzle_base64"]
        source_puzzle = decode_puzzle_base64(puzzle_b64)
        source_rounds = source_normalized["rounds"]
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "Basiskapsel ist beschädigt."}), 400
    if not isinstance(source_rounds, int):
        return jsonify({"success": False, "error": "Capsule corrupted or tampered"}), 400
    if source_rounds <= 0:
        return jsonify({"success": False, "error": "Basiskapsel enthält keine gültige Rundenzahl."}), 400

    secret_checksum_hex = source_normalized.get("secret_checksum_hex")
    if not isinstance(secret_checksum_hex, str) or not secret_checksum_hex.strip():
        return jsonify({"success": False, "error": "Basiskapsel enthält keine Checksumme."}), 400
    try:
        secret_checksum_bytes = bytes.fromhex(secret_checksum_hex.strip())
    except ValueError:
        return jsonify({"success": False, "error": "Basiskapsel enthält eine beschädigte Checksumme."}), 400

    start_value_protected = source_normalized.get("start_value_protected")
    if start_value_protected is not None and not isinstance(start_value_protected, Mapping):
        return jsonify({"success": False, "error": "Basiskapsel enthält einen defekten Passwortschutz."}), 400

    start_value_b64 = source_normalized.get("start_value")
    start_value_plain: bytes | None = None
    if start_value_b64:
        try:
            start_value_plain = base64.b64decode(start_value_b64)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Basiskapsel enthält einen beschädigten Startwert."}), 400

    legacy_plaintext_ignored = False
    if start_value_plain is not None and start_value_protected is not None:
        start_value_plain = None
        legacy_plaintext_ignored = True

    hash_function = str(source_normalized.get("hash_function", "sha256") or "sha256").strip() or "sha256"
    source_format = str(source_normalized.get("format", "lethesafe-puzzle") or "lethesafe-puzzle").strip() or "lethesafe-puzzle"
    try:
        source_version = int(source_normalized.get("version", 2) or 2)
    except (TypeError, ValueError):
        source_version = 2

    source_capsule = CloneSourceCapsule(
        rounds=source_rounds,
        puzzle=source_puzzle,
        secret_checksum=secret_checksum_bytes,
        hash_function=hash_function,
        start_value=start_value_plain,
        start_value_protected=start_value_protected,
        format=source_format,
        version=source_version,
        legacy_plaintext_ignored=legacy_plaintext_ignored,
    )

    try:
        # Phase 0 (HTTP): nur Absicht sammeln
        if rounds_mode in {"time_based", "time", "zeit"}:
            config = _get_time_config(time_config_token)
            if config:
                rounds_or_time = list(config.get("rounds", []))
                inputs = config.get("inputs", [])
                metadata_sec = {
                    "requested_seconds": config.get("requested_seconds", []),
                    "estimated_runtime": config.get("estimated_runtime", []),
                    "calibrated_hashrate": config.get("hashrate"),
                }
            else:
                time_specs = parse_time_specs(delay_raw) if delay_raw else parse_time_specs(rounds_raw)
                if not time_specs:
                    raise ValueError("Bitte die Kalibrierung erneut starten.")
                rounds_or_time = time_specs
                inputs = time_specs
                metadata_sec = {}
            if not rounds_or_time:
                raise ValueError("Kalibrierung ohne Rundenzahl. Bitte erneut starten.")
            rounds_metadata = {
                "rounds_mode": "time_based",
                "inputs": inputs,
                **metadata_sec,
            }
        else:
            rounds_list = parse_rounds_input(rounds_raw)
            if not rounds_list:
                raise ValueError("Bitte mindestens einen Rundenwert für den Klon angeben.")
            rounds_or_time = rounds_list
            rounds_metadata = {"rounds_mode": "round_based"}

        # Passwortlogik (UX): reuse vs neues Passwort vs ohne Schutz
        if reuse_password:
            if store_plain_start_value:
                raise ValueError("Bitte Passwortschutz deaktivieren oder Passwort wiederverwenden, nicht beides.")
            new_password_effective = None
        else:
            if store_plain_start_value:
                new_password_effective = None
            else:
                if not new_password:
                    raise ValueError("Bitte ein neues Passwort angeben oder Passwort wiederverwenden.")
                if new_password != new_password_confirm:
                    raise ValueError("Die neuen Passwortfelder stimmen nicht überein.")
                new_password_effective = new_password

        result = workflow_clone(
            run_token=run_token,
            source=source_capsule,
            rounds=rounds_or_time,
            reuse_password=reuse_password,
            store_plain_start_value=store_plain_start_value,
            new_password=new_password_effective,
            source_password=password or None,
            metadata={
                "rounds_context": rounds_metadata,
            },
        )

    except (ValueError, WorkflowError) as exc:
        status = 499 if _is_abort_error(str(exc)) else 400
        return jsonify({"success": False, "error": str(exc)}), status

    warnings = list(_get_result_value(result, "warnings", []) or [])
    # Dateien aus Workflow-Ergebnis
    files_out: list[dict[str, str]] = []
    base_names: list[str] = []
    for name, content in _normalize_result_files(result):
        token = _store_result(name, content)
        files_out.append({"name": name, "token": token})
        base_names.append(name.rsplit(".", 1)[0])

    capsule_names = _derive_capsule_names(base_names, result)
    password_file = None
    password_required = bool(_get_result_value(result, "password_required", False))

    # Passwortfile: je nachdem, ob reuse_password oder new_password
    pw_to_write = None
    if reuse_password:
        # Basiskapsel-Passwort kommt aus Formular (wenn benötigt)
        pw_to_write = password.strip() if password_required else None
    else:
        pw_to_write = new_password.strip() if password_required else None

    if store_password_file and pw_to_write:
        header = _build_password_header(capsule_names, pw_to_write)
        password_file = _store_text_file("lethesafe_start-pwd.txt", header)

    secret_value = _extract_secret_value(result)
    secret_file = None
    if store_secret_file and secret_value:
        secret_header = _build_secret_header(capsule_names, secret_value)
        secret_file = _store_text_file("lethesafe_k-key.txt", secret_header)

    return jsonify(
        {
            "success": True,
            "secret": secret_value,
            "files": files_out,
            "password_file": password_file,
            "password_required": password_required,
            "secret_file": secret_file,
            "warnings": warnings,
        }
    )


# ─────────────────────────────────────────────
# Error Handling
# ─────────────────────────────────────────────

@app.errorhandler(ValueError)
def handle_value_error(exc: ValueError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(Exception)
def handle_unexpected_error(exc: Exception):
    app.logger.exception("Unhandled exception", exc_info=exc)
    return jsonify({"error": "Capsule corrupted or tampered"}), 400


# ─────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────

@app.route("/__test_new", methods=["GET"])
def __test_new():
    from web_core.workflows import workflow_new

    result = workflow_new(
        run_token="test-run",
        rounds=[1000],
        protect_with_password=False,
        password=None,
        entropy_hint="test-entropy",
        metadata={"test": True},
    )
    return result


# ─────────────────────────────────────────────
# API: Run Control (Cancel / Progress)
# ─────────────────────────────────────────────

@app.post("/api/run/cancel")
def api_cancel_run():
    token = request.headers.get("X-Run-Token", "").strip()
    if not token:
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("token", "")).strip()
    if not token:
        return jsonify({"success": False, "error": "Missing run token."}), 400

    workflows_module.abort_manager.cancel(token)
    return jsonify({"success": True})


@app.get("/api/run/progress/<token>")
def api_get_progress(token: str):
    status = workflows_module.progress_tracker.get_status(token)
    if status is None:
        return jsonify({"success": False, "error": "Unknown run token."}), 404
    return jsonify({"success": True, "status": status})

@app.get("/api/version")
def api_version():
    return {
        "web": WEB_VERSION,
        "core": CORE_VERSION,
    }

@app.post("/api/run/token")
def api_issue_run_token_route():
    token = _issue_run_token()
    return jsonify({"success": True, "token": token})


if __name__ == "__main__":
    app.run(debug=True)
