"""Simple in-memory manager to track cancellable hash runs."""

from __future__ import annotations

import threading

_tokens: dict[str, bool] = {}
_lock = threading.Lock()


def register(token: str) -> None:
    if not token:
        return
    with _lock:
        _tokens.setdefault(token, False)


def cancel(token: str) -> None:
    if not token:
        return
    with _lock:
        _tokens[token] = True


def clear(token: str) -> None:
    if not token:
        return
    with _lock:
        _tokens.pop(token, None)


def is_cancelled(token: str) -> bool:
    if not token:
        return False
    with _lock:
        return _tokens.get(token, False)


def is_registered(token: str) -> bool:
    if not token:
        return False
    with _lock:
        return token in _tokens
