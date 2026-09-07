from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SENSITIVE_PARTS = {
    "authorization",
    "cookie",
    "credential",
    "jwt",
    "password",
    "secret",
    "session",
    "sign",
    "ticket",
    "token",
}


def is_sensitive_key(key: object) -> bool:
    text = str(key).lower().replace("-", "_")
    return any(part in text for part in SENSITIVE_PARTS)


def scrub(value: Any) -> Any:
    """Recursively remove likely authentication material from decoded data."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if is_sensitive_key(key) else scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    return value


def safe_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return ""
