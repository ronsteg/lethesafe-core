#!/usr/bin/env python3
"""
Lethesafe Maker (CLI)
====================
Vollständiges historisches CLI-Frontend.

Wichtig (Ablaufsicherheit im Clone-Modus):
- Alle Nutzerabfragen finden VOR dem Hashlauf zur Rekonstruktion von K statt.
- Nach Start des Hashlaufs gibt es KEINE weiteren Prompts mehr.
- Direkt nach dem Auslesen von K werden ohne weitere Interaktion die neuen Kapseln erstellt.

Kategorie A: Core
Kategorie B/C: Interaktion, Kalibrierung, Dateiausgabe, UX
"""

import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from common.puzzle_format import normalize_puzzle_payload, write_puzzle_v2_canonical
from cli.version import __version__ as CLI_VERSION
from core.version import __version__ as CORE_VERSION


# ─────────────────────────────────────────────
# Core (Kategorie A)
# ─────────────────────────────────────────────
from core.core_maker import (
    generate_start_value,
    generate_random_k,
    protect_start_value,
    build_puzzle,
    compute_hash_chain,
)

from core.core_unlocker import (
    decrypt_start_value,
    recover_secret_k,
    compute_secret_checksum,
    decode_puzzle_base64,
)

PROGRESS_ENABLED = os.environ.get("LETHESAFE_PROGRESS", "1").strip() != "0"


def wait_for_exit():
    while True:
        try:
            input("\nFertig. [ENTER] zum Beenden …")
        except EOFError:
            return
        if ask_yes_no("❓ wirklich beenden? (Fenster schließt sich ggf.)", default=False):
            return
        print("↩️  Fenster bleibt geöffnet.")


def format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m {secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins:02d}m"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compute_hash_chain_with_progress(
    start_value: bytes,
    rounds: int,
    *,
    chunk_size: int = 250_000,
) -> bytes:
    rounds = int(rounds)
    if rounds <= 0:
        return start_value
    if not PROGRESS_ENABLED:
        return compute_hash_chain(start_value, rounds)

    chunk_size = max(100_000, int(chunk_size))
    done = 0
    current = start_value
    total = rounds
    start = time.perf_counter()
    next_print = start

    while done < total:
        step = min(chunk_size, total - done)
        current = compute_hash_chain(current, step)
        done += step
        now = time.perf_counter()
        if now >= next_print or done == total:
            percent = done / total * 100
            elapsed = now - start
            rate = done / elapsed if elapsed > 0 else 0.0
            remaining = total - done
            eta = remaining / rate if rate > 0 else 0.0
            print(
                f"\r🔄 Hashkettenlauf: {done:,}/{total:,} "
                f"({percent:5.1f}%) – ETA {format_eta(eta)}",
                end="",
                flush=True,
            )
            next_print = now + 1.0

    print()
    return current


# ─────────────────────────────────────────────
# UX helpers (Kategorie B/C)
# ─────────────────────────────────────────────

def ask_yes_no(text: str, default: bool = True) -> bool:
    hint = "[J/n]" if default else "[j/N]"
    while True:
        val = input(f"{text} {hint} ").strip().lower()
        if not val:
            return default
        if val in ("j", "y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        print("❌ Bitte j oder n eingeben.")


def prompt_entropy() -> str:
    print("\n🌀 Optionale zusätzliche Entropie (Tastaturmashing, Wörter, etc.)")
    return input("➡️  Entropie (Enter = Systemzufall): ").strip()


def prompt_password(text: str, confirm: bool) -> str:
    while True:
        pw = getpass(text).strip()
        if not pw:
            print("❌ Password is required to decrypt the protected start value.")
            continue
        if confirm:
            pw2 = getpass("🔁 Passwort wiederholen: ").strip()
            if pw != pw2:
                print("❌ Passwörter stimmen nicht überein.")
                continue
        return pw


ALLOWED_NAME_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def _format_invalid_chars(chars: set[str]) -> str:
    return ", ".join(sorted(repr(ch) for ch in chars))


def sanitize_name(name: str, fallback: str = "puzzle", label: Optional[str] = None) -> str:
    raw = name.strip()
    if not raw:
        return fallback
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)
    sanitized = sanitized.strip("_") or fallback
    if label and sanitized != raw:
        invalid_chars = {ch for ch in raw if ch not in ALLOWED_NAME_CHARS}
        if invalid_chars:
            print(
                f"⚠️ {label}: unzulässige Zeichen ({_format_invalid_chars(invalid_chars)}) wurden entfernt bzw. zu '_' ersetzt."
            )
    return sanitized


def unique_export_filename(path: Path) -> Path:
    candidate = path
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return candidate


def decide_export_file(
    question: str,
    default_base: str,
    suffix: str,
    extension: str,
    label: str,
) -> Optional[Path]:
    """
    Fragt nur ab, OB und WIE die Datei heißen soll.
    Schreibt NICHT – damit kann man das im Clone-Modus vor den Hashlauf ziehen.
    """
    if not ask_yes_no(question, default=True):
        return None
    raw = input(f"➡️  Dateiname [{default_base}]: ").strip()
    path_input = Path(raw).expanduser() if raw else None
    if path_input and path_input.name:
        base_candidate = path_input.name
    else:
        base_candidate = default_base
    sanitized = sanitize_name(base_candidate, fallback=default_base, label=label)
    filename = f"{sanitized}{suffix}{extension}"
    if path_input and path_input.parent not in (Path(""), Path(".")):
        parent = path_input.parent
        if not parent.is_absolute():
            parent = Path.cwd() / parent
    else:
        parent = Path.cwd()
    return unique_export_filename(parent / filename)


def write_text_file_no_prompt(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"💾 Gespeichert: {path}")


def _collect_capsule_labels(rounds: Sequence[int]) -> List[str]:
    labels: List[str] = []
    seen: set[int] = set()
    for value in rounds:
        try:
            rounds_int = int(value)
        except (TypeError, ValueError):
            continue
        if rounds_int <= 0 or rounds_int in seen:
            continue
        seen.add(rounds_int)
        labels.append(f"zeitkapsel_{rounds_int}r")
    return labels


def format_secret_file(rounds: Sequence[int], secret_b64: str) -> str:
    names = _collect_capsule_labels(rounds)
    if names:
        return f"Zielzeichenkette [K] der {', '.join(names)}:\n{secret_b64}\n"
    return f"Zielzeichenkette [K]:\n{secret_b64}\n"


def format_start_password_file(rounds: Sequence[int], password: str) -> str:
    names = _collect_capsule_labels(rounds)
    if names:
        return f"Passwort der {', '.join(names)} für den Startwert [S]:\n{password}\n"
    return f"Passwort für den Startwert [S]:\n{password}\n"


def build_meta_payload(shared: Dict[str, Any], capsule_meta: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    for key, value in shared.items():
        if key == "mode":
            continue
        if value is not None:
            meta[key] = value
    for key, value in capsule_meta.items():
        if value is not None:
            meta[key] = value
    return meta


# ─────────────────────────────────────────────
# Hash helpers (delegated to core)
# ─────────────────────────────────────────────

def compute_hash_targets(
    start_value: bytes,
    rounds_list: Sequence[int],
) -> Dict[int, bytes]:
    if not rounds_list:
        return {}

    normalized: List[int] = []
    positive_rounds: List[int] = []
    include_zero = False

    for raw in rounds_list:
        rounds_int = int(raw)
        if rounds_int < 0:
            raise ValueError(f"Hash round {rounds_int} was not computed.")
        normalized.append(rounds_int)
        if rounds_int == 0:
            include_zero = True
        elif rounds_int > 0:
            positive_rounds.append(rounds_int)

    hashes: Dict[int, bytes] = {}
    if include_zero:
        hashes[0] = start_value

    if positive_rounds:
        current = start_value
        last_round = 0
        computed: Dict[int, bytes] = {}
        for target in sorted(set(positive_rounds)):
            if target < last_round:
                raise ValueError(f"Hash round {target} was not computed.")
            advance = target - last_round
            current = compute_hash_chain_with_progress(current, advance)
            computed[target] = current
            last_round = target
        for target in positive_rounds:
            hashes[target] = computed[target]

    result: Dict[int, bytes] = {}
    for value in normalized:
        if value == 0:
            result[value] = start_value
        else:
            result[value] = hashes[value]
    return result


# ─────────────────────────────────────────────
# Dauerwahl (Kategorie B)
# ─────────────────────────────────────────────

def calibrate_hashrate(duration: float = 3.0) -> float:
    print(f"\n🧪 Kalibriere SHA256-Durchsatz (~{duration:.1f}s) …")
    data = os.urandom(32)
    iterations = 0
    start = time.perf_counter()
    end = start + max(1.0, duration)
    batch_size = 1_000
    while time.perf_counter() < end:
        data = compute_hash_chain(data, batch_size)
        iterations += batch_size
    elapsed = max(time.perf_counter() - start, 1e-9)
    rate = iterations / elapsed
    print(f"   Gemessene Hashrate: {rate:,.0f} Hashes/s")
    return rate


def parse_time_expression(raw: str) -> float:
    m = re.fullmatch(r"\s*(\d+)\s*([smhdSMHD])\s*", raw)
    if not m:
        raise ValueError("Format: <Zahl><Einheit>, z. B. 10m, 2h oder 1d.")
    value = int(m.group(1))
    unit = m.group(2).lower()
    if value <= 0:
        raise ValueError("Zeitwert muss > 0 sein.")
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(value * factors[unit])


def choose_rounds_mode() -> Tuple[List[int], Dict[str, Any]]:
    """
    Rückgabe: (rounds_list, shared_metadata)
    """
    print("\nDauer für neue Zeitkapsel(n) festlegen:")
    print("  [1] Zeitbasiert (empfohlen)")
    print("  [2] Rundenbasiert")
    while True:
        sel = input("➡️  Auswahl [1/2]: ").strip().lower() or "1"
        if sel in ("2", "runden", "rounds", "r"):
            rounds = prompt_rounds_list()
            return rounds, {"rounds_mode": "round_based"}
        if sel in ("1", "zeit", "time", "t"):
            rounds, meta = prompt_time_based_rounds()
            return rounds, meta
        print("❌ Ungültige Auswahl. Bitte 1 oder 2 wählen.")


def prompt_rounds_list() -> List[int]:
    print("\n⏱️ Rundenwerte angeben (z. B. 10_000, 20_000, 40_000).")
    print("   Du kannst Trennzeichen , ; oder Leerzeichen verwenden.")
    while True:
        raw = input("➡️  Runden: ").replace(";", " ").replace(",", " ").split()
        if not raw:
            print("❌ Bitte mindestens einen Wert eingeben.")
            continue
        try:
            rounds: List[int] = []
            seen = set()
            for token in raw:
                value = int(token.replace("_", ""))
                if value <= 0:
                    raise ValueError
                if value not in seen:
                    rounds.append(value)
                    seen.add(value)
            return rounds
        except ValueError:
            print("❌ Ungültige Eingabe. Nur positive Ganzzahlen erlaubt.")


def prompt_time_based_rounds() -> Tuple[List[int], Dict[str, Any]]:
    while True:
        print("\n🕒 Gewünschte Verzögerung(en) eingeben. Einheiten: s, m, h, d.")
        raw = input("➡️  Zeit (z. B. 10m oder 10m, 2h, 1d): ").strip()
        if not raw:
            print("❌ Bitte mindestens eine Dauer angeben.")
            continue
        tokens = re.split(r"[,\s;]+", raw.strip())
        try:
            entries = []
            for t in tokens:
                if t.strip():
                    entries.append((t.strip(), parse_time_expression(t.strip())))
        except ValueError as exc:
            print(f"❌ {exc}")
            continue
        if not entries:
            print("❌ Bitte mindestens eine Dauer angeben.")
            continue

        rate = calibrate_hashrate()
        rounds: List[int] = []
        print("\n📊 Kalibrierungsergebnis:")
        print(f"   • Gemessene Hashrate: {rate:,.0f} Hashes/s")
        for label, seconds in entries:
            r = max(1, int(rate * seconds))
            rounds.append(r)
            est = seconds
            print(f"     - {label}: {r:,} Runden (~{est:.0f}s Ziel)")

        if ask_yes_no("➡️  Mit diesen Parametern fortfahren?", default=True):
            return rounds, {"rounds_mode": "time_based", "calibrated_hashrate": rate}

        print("↩️  Eingabe erneut versuchen.")


# ─────────────────────────────────────────────
# File IO helpers (Kategorie C)
# ─────────────────────────────────────────────

def read_json_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return json.loads(text)


def load_existing_capsule() -> Tuple[Path, Dict[str, Any]]:
    while True:
        raw = input("📂 Pfad und Datei der bestehenden Zeitkapsel: ").strip()
        if not raw:
            print("❌ Bitte einen Pfad angeben.")
            continue
        path = Path(raw).expanduser()
        if not path.exists() or path.is_dir():
            print("❌ Datei nicht gefunden.")
            continue
        try:
            data = read_json_file(path)
        except Exception as exc:
            print(f"🚫 Fehler beim Lesen der Zeitkapsel-Datei: {exc}")
            continue
        normalized = normalize_puzzle_payload(data)
        if "puzzle_base64" not in normalized or "rounds" not in normalized:
            print("🚫 Ungültige Zeitkapsel-Datei (Felder fehlen).")
            continue
        return path, normalized


def resolve_start_value_from_data(data: Dict[str, Any]) -> bytes:
    protected = data.get("start_value_protected")
    plaintext = data.get("start_value")
    plaintext_present = plaintext is not None

    if protected is not None:
        if plaintext_present:
            print("⚠️ Insecure capsule: plaintext start_value was present and ignored.")
        if not isinstance(protected, dict):
            raise ValueError("Startwertschutz ist beschädigt oder ungültig.")
        while True:
            pw = getpass("🔑 Passwort der Originaldatei: ").strip()
            if not pw:
                print("❌ Original PWD is required to decrypt the protected start value.")
                continue
            try:
                return decrypt_start_value(protected, pw)
            except Exception as exc:
                print(f"❌ {exc}")
    if plaintext_present:
        print("⚠️ Hinweis: Diese Zeitkapsel-Datei schützt [S] nicht mit einem Passwort.")
        return base64.b64decode(plaintext)
    raise ValueError("Startwert in den Zeitkapsel-Daten nicht gefunden.")


def build_capsule_filename(prefix: str, rounds: int) -> Path:
    base = Path.cwd() / f"{prefix}_Zeitkapsel_{rounds}r.json"
    return unique_export_filename(base)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    print(f"⌛ Lethesafe Maker CLI v{CLI_VERSION} (Core v{CORE_VERSION})")
    print("===========================================")
    print("Dieses Tool erzeugt Zeitkapsel-Dateien (Time-Lock-Puzzles).\n")

    print("Was möchtest du tun?")
    print("  [1] 💥 Neu   – frische Zeitkapseln erzeugen")
    print("  [2] 🐑 Clone – bestehende Zeitkapsel entschlüsseln und neue Kapseln bauen")
    while True:
        mode_sel = input("➡️  Auswahl [1/2]: ").strip().lower()
        if mode_sel in ("1", "2"):
            break
        print("❌ Bitte 1 oder 2 eingeben.")
    is_clone = (mode_sel == "2")

    # Shared metadata (wird pro Kapsel ergänzt)
    metadata_shared: Dict[str, Any] = {}
    metadata_shared["mode"] = "clone" if is_clone else "new"

    # ─────────────────────────────────────────
    # Vorbereitung (ALLE Prompts vor Hashlauf im Clone-Modus)
    # ─────────────────────────────────────────

    export_k_path: Optional[Path] = None
    export_start_pw_path: Optional[Path] = None
    start_password_to_save: Optional[str] = None

    if is_clone:
        source_path, source_data = load_existing_capsule()
        source_rounds = source_data["rounds"]
        if not isinstance(source_rounds, int):
            raise ValueError("Capsule corrupted or tampered")
        source_puzzle = decode_puzzle_base64(source_data["puzzle_base64"])
        source_meta = source_data.get("meta") or {}

        # Startwert S (inkl. Passwortprompt) – MUSS vor Hashlauf passieren
        start_value = resolve_start_value_from_data(source_data)

        metadata_shared["source_file"] = str(source_path.resolve())
        metadata_shared["source_rounds"] = source_rounds
        metadata_shared["reused_start_value"] = True

        # Schutzstrategie für neue Kapseln festlegen – MUSS vor Hashlauf passieren
        source_protected = source_data.get("start_value_protected")
        inherited_protection = source_meta.get("start_value_protection")
        inherited_iterations = source_meta.get("start_value_iterations")

        if source_protected and ask_yes_no("🔐 Originalen Passwortschutz für [S] beibehalten?", default=True):
            start_value_payload = {"start_value_protected": source_protected}
            metadata_shared["start_value_protection"] = inherited_protection or "password_pbkdf2_blake2b_hmac"
            if inherited_iterations is not None:
                metadata_shared["start_value_iterations"] = inherited_iterations
            elif isinstance(source_protected, dict) and "iterations" in source_protected:
                metadata_shared["start_value_iterations"] = int(source_protected["iterations"])
            else:
                # falls nicht vorhanden, nicht erfinden
                metadata_shared.pop("start_value_iterations", None)
        else:
            print("\n🔐 Neuer Passwortschutz für den Startwert [S]")
            if ask_yes_no("   Soll ein neuer Passwortschutz eingerichtet werden?", default=True):
                new_pw = prompt_password(
                    "🔑 Neues Passwort festlegen (Dieses unbedingt zur Wiederherstellung merken): ",
                    confirm=True,
                )
                start_password_to_save = new_pw
                protected = protect_start_value(start_value, new_pw)
                start_value_payload = {"start_value_protected": protected}
                metadata_shared["start_value_protection"] = "password_pbkdf2_blake2b_hmac"
                if isinstance(protected, dict) and "iterations" in protected:
                    metadata_shared["start_value_iterations"] = int(protected["iterations"])
            else:
                print("   ⚠️ [S] wird ungeschützt gespeichert – jede Person mit der Datei kann sofort hashen.")
                start_value_payload = {"start_value": base64.b64encode(start_value).decode("ascii")}
                metadata_shared["start_value_protection"] = "none"
                metadata_shared.pop("start_value_iterations", None)

        # Dauerwahl – MUSS vor Hashlauf passieren
        rounds_list, rounds_meta = choose_rounds_mode()
        metadata_shared.update(rounds_meta)

        # Dateiname-Präfix – MUSS vor Hashlauf passieren
        raw_prefix = input("\n📝 Dateiname-Präfix für Zeitkapsel (Standard: 'lethesafe'): ")
        prefix = sanitize_name(raw_prefix or "", fallback="lethesafe", label="Dateiname-Präfix")

        # Export-Entscheidungen – MUSS vor Hashlauf passieren
        if start_password_to_save:
            export_start_pw_path = decide_export_file(
                "💾 Soll das neue Startpasswort in eine Datei geschrieben werden?",
                "lethesafe",
                "_start-pwd",
                ".txt",
                "Dateiname (Startpasswort)",
            )
        export_k_path = decide_export_file(
            "💾 Soll die Zielzeichenkette [K] in eine Datei geschrieben werden?",
            "lethesafe",
            "_k-key",
            ".txt",
            "Dateiname (Zielzeichenkette)",
        )

        # ─────────────────────────────────────────
        # Ab hier: KEINE weiteren Prompts im Clone-Modus!
        # ─────────────────────────────────────────

        print(f"\n💎 Originaldatei '{source_path.name}' verwendet {source_rounds:,} Runden.")
        print("🔁 Rekonstruiere [K] – das dauert so lange wie die ursprüngliche Zeitkapsel.")
        combined_rounds = list(rounds_list) + [source_rounds]
        if PROGRESS_ENABLED:
            print("\n⏳ Hashkettenlauf (Original + neue Kapseln) …")
        hash_results = compute_hash_targets(start_value, combined_rounds)
        if PROGRESS_ENABLED:
            print("✅ Hashkettenlauf abgeschlossen.")

        Hn = hash_results[int(source_rounds)]
        secret = recover_secret_k(
            source_puzzle,
            Hn,
            secret_checksum=source_data.get("secret_checksum_hex"),
            hash_function=source_data.get("hash_function")
        )
        secret_b64 = base64.b64encode(secret).decode("ascii")

        print("✅ Zielzeichenkette [K] rekonstruiert.")
        print("➡️ Erzeugung der Klone …")

        # Hashes für neue Kapseln (stumm; Core)
        hashes = {int(r): hash_results[int(r)] for r in rounds_list}

        checksum = compute_secret_checksum(secret).hex()
        written: List[Path] = []

        for idx, r in enumerate(rounds_list, start=1):
            out_path = build_capsule_filename(prefix, r)
            puzzle_bytes = build_puzzle(secret, hashes[r])
            puzzle_b64 = base64.b64encode(puzzle_bytes).decode("ascii")

            core_fields: Dict[str, Any] = {
                "mode": metadata_shared["mode"],
                "rounds": int(r),
                "puzzle_base64": puzzle_b64,
                "secret_checksum_hex": checksum,
                **start_value_payload,
            }
            meta_payload = build_meta_payload(
                metadata_shared,
                {
                    "created": utc_timestamp(),
                    "capsule_index": idx,
                    "capsule_rounds": int(r),
                },
            )

            write_puzzle_v2_canonical(out_path, core_fields, meta=meta_payload or None)
            print(f"✅ Zeitkapsel-Datei gespeichert: {out_path.name}")
            written.append(out_path)

        # Exporte (ohne Prompt)
        if export_start_pw_path and start_password_to_save:
            write_text_file_no_prompt(
                export_start_pw_path,
                format_start_password_file(rounds_list, start_password_to_save),
            )

        if export_k_path:
            write_text_file_no_prompt(export_k_path, format_secret_file(rounds_list, secret_b64))

        # Ausgabe K (ohne Prompt)
        print("\n🔑 Zielzeichenkette [K] (Base64):")
        print(secret_b64)

        print("\n🎉 Fertig! Zusammenfassung:")
        for p in written:
            print(f"   • {p.name}")
        if export_k_path:
            print(f"   • Zielketten-Datei [K]: {export_k_path.name}")
        else:
            print("   • [K] wurde nicht als Datei gespeichert.")
        if export_start_pw_path and start_password_to_save:
            print(f"   • Startpasswort-Datei: {export_start_pw_path.name}")
        else:
            print("   • Startpasswort wurde nicht als Datei gespeichert oder war nicht neu gesetzt.")

        print("\n💎 Bewahre die Zeitkapsel-Datei(en) sicher auf!")
        print("🔥 Zerstöre den Klartext von [K] nach deren Gebrauch.")
        return

    # ─────────────────────────────────────────
    # NEW-Modus (Interaktionen wie üblich)
    # ─────────────────────────────────────────
    entropy = prompt_entropy()
    secret = generate_random_k(entropy)
    secret_b64 = base64.b64encode(secret).decode("ascii")
    start_value = generate_start_value(entropy)

    print("\n🔐 Passwortschutz für den Startwert [S]")
    if ask_yes_no("   Soll der Startwert [S] passwortgeschützt werden? empfohlen: ja", default=True):
        new_pw = prompt_password(
            "🔑 Neues Passwort festlegen (Dieses unbedingt zur Wiederherstellung merken): ",
            confirm=True,
        )
        start_password_to_save = new_pw
        protected = protect_start_value(start_value, new_pw)
        start_value_payload = {"start_value_protected": protected}
        metadata_shared["start_value_protection"] = "password_pbkdf2_blake2b_hmac"
        if isinstance(protected, dict) and "iterations" in protected:
            metadata_shared["start_value_iterations"] = int(protected["iterations"])
    else:
        print("   ⚠️ [S] wird ungeschützt gespeichert – jeder mit der Datei kann den Hashlauf starten.")
        start_value_payload = {"start_value": base64.b64encode(start_value).decode("ascii")}
        metadata_shared["start_value_protection"] = "none"
        metadata_shared.pop("start_value_iterations", None)

    # Optional: Startpasswort speichern (NEW-Modus darf interaktiv bleiben)
    if start_password_to_save:
        export_start_pw_path = decide_export_file(
            "💾 Soll das Startpasswort in eine Datei geschrieben werden?",
            "lethesafe",
            "_start-pwd",
            ".txt",
            "Dateiname (Startpasswort)",
        )

    # Dauerwahl
    rounds_list, rounds_meta = choose_rounds_mode()
    metadata_shared.update(rounds_meta)

    raw_prefix = input("\n📝 Dateiname-Präfix für Zeitkapsel (Standard: 'lethesafe'): ")
    prefix = sanitize_name(raw_prefix or "", fallback="lethesafe", label="Dateiname-Präfix")

    export_k_path = decide_export_file(
        "💾 Soll die Zielzeichenkette [K] in eine Datei geschrieben werden?",
        "lethesafe",
        "_k-key",
        ".txt",
        "Dateiname (Zielzeichenkette)",
    )

    print("\n⛓️  Berechne Hashketten für neue Zeitkapseln")
    if PROGRESS_ENABLED:
        print("⏳ Hashkettenlauf (neue Zeitkapseln) …")
    hashes = compute_hash_targets(start_value, rounds_list)
    if PROGRESS_ENABLED:
        print("✅ Hashkettenlauf abgeschlossen.")
    checksum = compute_secret_checksum(secret).hex()

    written: List[Path] = []
    for idx, r in enumerate(rounds_list, start=1):
        out_path = build_capsule_filename(prefix, r)
        puzzle_bytes = build_puzzle(secret, hashes[r])

        core_fields = {
            "mode": metadata_shared["mode"],
            "rounds": int(r),
            "puzzle_base64": base64.b64encode(puzzle_bytes).decode("ascii"),
            "secret_checksum_hex": checksum,
            **start_value_payload,
        }
        meta_payload = build_meta_payload(
            metadata_shared,
            {
                "created": utc_timestamp(),
                "capsule_index": idx,
                "capsule_rounds": int(r),
            },
        )

        write_puzzle_v2_canonical(out_path, core_fields, meta=meta_payload or None)
        print(f"✅ Zeitkapsel-Datei gespeichert: {out_path.name}")
        written.append(out_path)

    # Exporte (ohne weitere Prompts, aber im NEW-Modus wäre es egal – wir halten es sauber)
    if export_start_pw_path and start_password_to_save:
        write_text_file_no_prompt(
            export_start_pw_path,
            format_start_password_file(rounds_list, start_password_to_save),
        )

    if export_k_path:
        write_text_file_no_prompt(export_k_path, format_secret_file(rounds_list, secret_b64))

    print("\n🔑 Zielzeichenkette [K] (Base64):")
    print(secret_b64)

    print("\n🎉 Fertig! Zusammenfassung:")
    for p in written:
        print(f"   • {p.name}")
    if export_k_path:
        print(f"   • Zielketten-Datei [K]: {export_k_path.name}")
    else:
        print("   • [K] wurde nicht als Datei gespeichert.")
    if export_start_pw_path and start_password_to_save:
        print(f"   • Startpasswort-Datei: {export_start_pw_path.name}")
    else:
        if start_password_to_save:
            print("   • Startpasswort wurde nicht als Datei gespeichert.")
        else:
            print("   • Kein Startpasswort gesetzt.")

    print("\n💎 Bewahre die Zeitkapsel-Datei(en) sicher auf!")
    print("🔥 Zerstöre den Klartext von [K] nach dem Gebrauch.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🚫 Abgebrochen durch Benutzer.")
        sys.exit(130)
    wait_for_exit()
