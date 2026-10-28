"""Secret redaction for logs, reports and persisted error messages."""

from __future__ import annotations

import re
import threading

REDACTED = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # GitHub tokens: classic, OAuth, user-to-server, server-to-server, refresh, fine-grained.
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTED),
    # Authorization headers.
    (re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-]{8,}"), r"\1 " + REDACTED),
    # Passwords embedded in URLs: scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^:/\s@]+):([^@\s/]+)@"), r"\1:" + REDACTED + "@"),
    # key=value style secrets.
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*([^\s&;,'\"]+)"
        ),
        r"\1=" + REDACTED,
    ),
)

_known_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    """Register an exact secret value (token, password) so it is always masked."""
    if value and len(value) >= 6:
        with _lock:
            _known_secrets.add(value)


def clear_registered_secrets() -> None:
    with _lock:
        _known_secrets.clear()


def redact(text: object) -> str:
    """Return ``text`` as a string with every known secret pattern masked."""
    if text is None:
        return ""
    result = str(text)
    with _lock:
        secrets = sorted(_known_secrets, key=len, reverse=True)
    for secret in secrets:
        result = result.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def safe_error_message(exc: BaseException, limit: int = 1000) -> str:
    """A sanitized, length-bounded description of an exception for persistence."""
    message = redact(f"{exc}")
    return message[:limit]
