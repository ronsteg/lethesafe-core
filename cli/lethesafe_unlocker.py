#!/usr/bin/env python3
"""
Lethesafe Unlocker (CLI)
=======================
Historisches CLI-Frontend mit vollständiger UX.
"""

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from getpass import getpass
from typing import Any, Dict
from cli.version import __version__ as CLI_VERSION
from core.version import __version__ as CORE_VERSION

from core.core_unlocker import (
    decrypt_start_value,
    compute_hash_chain,
    recover_secret_k,
)

PROGRESS_ENABLED = os.environ.get("LETHESAFE_PROGRESS", "1").strip() != "0"


def format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m {secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins:02d}m"


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


def unique_filename(path: Path) -> Path:
    candidate = path
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return candidate


def _get_string_field(data: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise KeyError(keys[0])


def unlock(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
        rounds = int(data["rounds"])
        puzzle_b64 = _get_string_field(data, "puzzle", "puzzle_base64")
        puzzle = base64.b64decode(puzzle_b64)
    except Exception as e:
        print(f"🚫 Fehler beim Lesen: {e}")
        return False

    protected = data.get("start_value_protected")
    start_value_b64 = None
    try:
        start_value_b64 = _get_string_field(data, "start_value", "start_value_base64")
    except KeyError:
        start_value_b64 = None

    if protected is not None:
        if start_value_b64:
            print("⚠️ Insecure capsule: plaintext start_value was present and ignored.")
        if not isinstance(protected, dict):
            print("🚫 Zeitkapsel enthält einen beschädigten Passwortschutz.")
            return False
        while True:
            pw = getpass("🔑 Passwort: ").strip()
            if not pw:
                print("❌ Password is required to decrypt the protected start value.")
                continue
            try:
                start_value = decrypt_start_value(protected, pw)
                break
            except Exception as e:
                print(f"❌ {e}")
    elif start_value_b64:
        print("⚠️ Startwert ist ungeschützt.")
        try:
            start_value = base64.b64decode(start_value_b64)
        except Exception as e:
            print(f"🚫 Fehler beim Lesen: {e}")
            return False
    else:
        print("🚫 Zeitkapsel enthält keinen Startwert.")
        return False

    if PROGRESS_ENABLED:
        print(f"\n⏳ Hashkettenlauf ({rounds:,} Runden) …")
    hash_n = compute_hash_chain_with_progress(start_value, rounds)
    if PROGRESS_ENABLED:
        print("✅ Hashkettenlauf abgeschlossen.")

    secret = recover_secret_k(
        puzzle,
        hash_n,
        secret_checksum=data.get("secret_checksum") or data.get("secret_checksum_hex"),
        hash_function=data.get("hash_function")
    )

    print("\n✅ Zielzeichenkette [K]:")
    print(base64.b64encode(secret).decode())

    out = unique_filename(path.with_name(f"lethesafe_k-key_{rounds}r.txt"))
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out.write_text(
        base64.b64encode(secret).decode()
        + f"\n# Generiert: {timestamp}\n"
    )
    print(f"📄 Gespeichert unter: {out.name}")
    return True


def main():
    print(f"🔓 Lethesafe Unlocker CLI v{CLI_VERSION} (Core v{CORE_VERSION})")
    print("==============================================")

    while True:
        raw = input("📂 Zeitkapsel-Datei: ").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.exists():
            print("❌ Datei nicht gefunden.")
            continue
        if unlock(path):
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🚫 Abgebrochen.")
