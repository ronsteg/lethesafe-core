"""Flask WebApp für Lethesafe (bereinigt: Transport-Layer only).

- Keine Kryptologie in app.py
- Keine Hashloops / Kalibrierung in app.py
- Zeit→Runden-Entscheidung liegt in web_core/workflows.py (Phase 1)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
from collections import OrderedDict
from typing import Any, Mapping
from uuid import uuid4

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from web.version import __version__ as WEB_VERSION
from core.version import __version__ as CORE_VERSION

import web_core.workflows as workflows_module
from web_core.workflows import WorkflowError, workflow_clone, workflow_new, workflow_unlock

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB Upload-Limit

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


def _measure_hashrate(duration_seconds: float = 2.5) -> float:
    sample = os.urandom(32)
    start = time.perf_counter()
    end = start + max(0.5, duration_seconds)
    iterations = 0
    while time.perf_counter() < end:
        sample = hashlib.sha256(sample).digest()
        iterations += 1
    elapsed = max(time.perf_counter() - start, 1e-9)
    return max(iterations / elapsed, 1.0)


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


def _normalize_result_files(result: Mapping[str, Any]) -> list[tuple[str, bytes]]:
    """Erwartet, dass Workflows Dateien als Liste von (name, bytes|str) liefern."""
    files = _get_result_value(result, "files", []) or []
    normalized: list[tuple[str, bytes]] = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip() or "zeitkapsel.json"
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


def _derive_capsule_names(
    base_names: list[str],
    result: Mapping[str, Any],
) -> list[str]:
    if base_names:
        return base_names
    capsules = _get_result_value(result, "capsules", []) or []
    derived: list[str] = []
    for capsule in capsules:
        if not isinstance(capsule, Mapping):
            continue
        rounds_value = capsule.get("rounds")
        try:
            rounds_int = int(rounds_value)
        except (TypeError, ValueError):
            continue
        if rounds_int <= 0:
            continue
        derived.append(f"zeitkapsel_{rounds_int}")
    return derived


def _ensure_clone_payload_keys(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Basiskapsel ist kein JSON-Objekt.")
    normalized = dict(payload)
    if "puzzle" not in normalized and "puzzle_base64" in normalized:
        normalized["puzzle"] = normalized["puzzle_base64"]
    if "puzzle_base64" not in normalized and "puzzle" in normalized:
        normalized["puzzle_base64"] = normalized["puzzle"]
    if "start_value_base64" not in normalized and "start_value" in normalized:
        normalized["start_value_base64"] = normalized["start_value"]
    if "secret_checksum_hex" not in normalized and "secret_checksum" in normalized:
        normalized["secret_checksum_hex"] = normalized["secret_checksum"]
    return normalized


def _get_capsule_string(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _build_unlock_request_data(payload: Mapping[str, Any], password: str | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Puzzle-Datei ist kein JSON-Objekt.")

    try:
        rounds_value = int(payload["rounds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Puzzle-Datei enthält keine gültige Rundenzahl.") from exc
    if rounds_value <= 0:
        raise ValueError("Puzzle-Datei enthält keine gültige Rundenzahl.")

    puzzle_b64 = _get_capsule_string(payload, "puzzle_base64", "puzzle")
    if not puzzle_b64:
        raise ValueError("Puzzle-Datei enthält keine Puzzle-Daten.")
    try:
        puzzle_bytes = base64.b64decode(puzzle_b64)
    except (TypeError, ValueError) as exc:
        raise ValueError("Puzzle-Daten sind beschädigt.") from exc

    start_value_bytes = None
    legacy_plaintext_ignored = False
    start_value_b64 = _get_capsule_string(payload, "start_value_base64", "start_value")
    if start_value_b64:
        try:
            start_value_bytes = base64.b64decode(start_value_b64)
        except (TypeError, ValueError) as exc:
            raise ValueError("Startwert ist beschädigt.") from exc

    start_value_protected = payload.get("start_value_protected")
    password_protected = start_value_protected is not None
    if password_protected and start_value_bytes is not None:
        start_value_bytes = None
        legacy_plaintext_ignored = True
    if start_value_bytes is None and start_value_protected is None:
        raise ValueError("Puzzle-Datei enthält keinen Startwert.")
    if start_value_protected is not None and not isinstance(start_value_protected, Mapping):
        raise ValueError("Protected-Startwert ist beschädigt.")

    secret_checksum_bytes = None
    secret_checksum_hex = _get_capsule_string(payload, "secret_checksum_hex", "secret_checksum")
    if secret_checksum_hex:
        try:
            secret_checksum_bytes = bytes.fromhex(secret_checksum_hex)
        except (TypeError, ValueError) as exc:
            raise ValueError("Checksumme ist beschädigt.") from exc

    hash_function_raw = payload.get("hash_function")
    if hash_function_raw is not None:
        if not isinstance(hash_function_raw, str) or not hash_function_raw.strip():
            raise ValueError("hash_function ist ungültig.")
        hash_function = hash_function_raw.strip().lower()
    else:
        hash_function = "sha256"

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
    if hash_function:
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
    password = (request.form.get("password", "") or "").strip()
    password_confirm = (request.form.get("password_confirm", "") or "").strip()

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

    password_file = None
    password_required = bool(_get_result_value(result, "password_required", False))
    if store_password_file and password_required and password:
        if base_names:
            header = f"Passwort der {', '.join(base_names)} für den Startwert [S]:\n{password}\n"
        else:
            header = f"Passwort für den Startwert [S]:\n{password}\n"
        password_file = _store_text_file("lethesafe_start-pwd.txt", header)

    secret_value = _extract_secret_value(result)
    secret_file = None
    if store_secret_file and secret_value:
        capsule_names = _derive_capsule_names(base_names, result)
        if capsule_names:
            secret_header = f"Zielzeichenkette [K] der {', '.join(capsule_names)}:\n{secret_value}\n"
        else:
            secret_header = f"Zielzeichenkette [K]:\n{secret_value}\n"
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
        hashrate = _measure_hashrate()
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
    password = (request.form.get("password", "") or "").strip()

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
    secret_value = _extract_secret_value(result)
    secret_file = _store_text_file("lethesafe_k-key.txt", secret_value)
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

    new_password = (request.form.get("new_password", "") or "").strip()
    new_password_confirm = (request.form.get("new_password_confirm", "") or "").strip()
    password = (request.form.get("password", "") or "").strip()

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
        source_capsule = json.loads(text)
        source_capsule = _ensure_clone_payload_keys(source_capsule)
    except Exception:
        return jsonify({"success": False, "error": "Ungültige Basiskapsel (JSON erwartet)."}), 400


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
            source_capsule=source_capsule,
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
        if base_names:
            header = f"Passwort der {', '.join(base_names)} für den Startwert [S]:\n{pw_to_write}\n"
        else:
            header = f"Passwort für den Startwert [S]:\n{pw_to_write}\n"
        password_file = _store_text_file("lethesafe_start-pwd.txt", header)

    secret_value = _extract_secret_value(result)
    secret_file = None
    if store_secret_file and secret_value:
        capsule_names = _derive_capsule_names(base_names, result)
        if capsule_names:
            secret_header = f"Zielzeichenkette [K] der {', '.join(capsule_names)}:\n{secret_value}\n"
        else:
            secret_header = f"Zielzeichenkette [K]:\n{secret_value}\n"
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
