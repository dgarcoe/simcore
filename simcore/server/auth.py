"""Dungeon-master access: with SIMCORE_DM_PASSWORD set, only a logged-in DM can use the API.

Players never log in: they reach their game through /api/play/* with the personal token they got
when joining. Without SIMCORE_DM_PASSWORD everything stays open (personal use), as before.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

COOKIE = "simcore_dm"
SESSION_SECONDS = 12 * 3600
# Paths anyone may reach (the player API checks its own tokens).
OPEN_PREFIXES = ("/api/auth/", "/api/version", "/api/play/")
LOGIN_ATTEMPTS = 10  # per address and minute


def dm_password() -> str:
    return os.environ.get("SIMCORE_DM_PASSWORD", "")


def required() -> bool:
    return bool(dm_password())


def _secret() -> bytes:
    """SIMCORE_SECRET, or one generated once and kept with the data (sessions survive restarts)."""
    env = os.environ.get("SIMCORE_SECRET")
    if env:
        return env.encode()
    path = Path(os.environ.get("SIMCORE_GAMES_DIR") or Path.cwd() / "games") / ".secret"
    try:
        if path.exists():
            return path.read_bytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_bytes(32)
        path.write_bytes(value)
        path.chmod(0o600)
        return value
    except OSError:
        return hashlib.sha256(dm_password().encode() + b"simcore").digest()


def check_password(password: str) -> bool:
    expected = dm_password()
    return bool(expected) and hmac.compare_digest(password.encode(), expected.encode())


def make_session(now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    sig = hmac.new(_secret(), f"dm:{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_session(value: str | None, now: float | None = None) -> bool:
    if not value or "." not in value:
        return False
    expires, sig = value.split(".", 1)
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    expected = hmac.new(_secret(), f"dm:{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def is_open_path(path: str) -> bool:
    return not path.startswith(("/api", "/ws")) or path.startswith(OPEN_PREFIXES)


class RateLimit:
    """At most `limit` attempts per key in `window` seconds."""

    def __init__(self, limit: int = LOGIN_ATTEMPTS, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
