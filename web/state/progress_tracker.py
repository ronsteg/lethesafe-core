"""In-memory tracker für Hashlauf-Fortschritte."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_states: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def start(token: str, total_rounds: Optional[int] = None, *, status: str = "running") -> None:
    if not token:
        return
    with _lock:
        _states[token] = {
            "status": status,
            "completed_rounds": 0,
            "elapsed_time": 0.0,
            "total_rounds": int(total_rounds) if total_rounds else None,
            "updated_at": time.time(),
            "last_update_time": None,
            "last_update_round": 0,
        }


def update(token: str, completed_rounds: int, elapsed_time: float) -> None:
    if not token:
        return
    with _lock:
        state = _states.get(token)
        if not state:
            return
        state["last_update_time"] = state.get("updated_at")
        state["last_update_round"] = state.get("completed_rounds", 0)
        state["completed_rounds"] = max(state.get("completed_rounds", 0), int(completed_rounds))
        state["elapsed_time"] = max(0.0, float(elapsed_time))
        state["updated_at"] = time.time()


def finish(token: str, status: str) -> None:
    if not token:
        return
    with _lock:
        state = _states.get(token)
        if not state:
            return
        state["status"] = status
        state["updated_at"] = time.time()


def clear(token: str) -> None:
    if not token:
        return
    with _lock:
        _states.pop(token, None)


def get_status(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with _lock:
        state = _states.get(token)
        if not state:
            return None
        return dict(state)
