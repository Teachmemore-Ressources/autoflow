# Autoflow — user_store — Apache 2.0
"""
User store for multi-user RBAC.

Users are stored in a JSON file (``USERS_FILE`` env var, default
``/etc/autoflow/users.json``).  Passwords are hashed with bcrypt via passlib.

Roles
-----
admin     — full access: read + write + user management
operator  — read + write (launch jobs, etc.); cannot manage users
viewer    — read-only (GET requests only)

Bootstrap
---------
On first load, if the store file is absent or empty, a default ``admin``
user is created with the password equal to ``API_SECRET_KEY``.  This preserves
backward compatibility — existing single-user deployments keep working.

Thread / concurrency safety
---------------------------
All mutations go through ``_save()`` which rewrites the entire JSON file.
Because the API service runs as a single process (uvicorn with ``--workers 1``
in the default Docker setup), there are no concurrent writers.  For multi-worker
deployments, migrate to a proper database.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

from passlib.context import CryptContext

_log = logging.getLogger("autoflow.users")

# ── Role type ─────────────────────────────────────────────────────────────────

Role = Literal["admin", "operator", "viewer"]
ROLES: tuple[Role, ...] = ("admin", "operator", "viewer")

# ── Password hashing ──────────────────────────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plaintext: str) -> str:
    return _pwd_ctx.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plaintext, hashed)


# ── Store ─────────────────────────────────────────────────────────────────────

_USERS_FILE = Path(os.getenv("USERS_FILE", "/etc/autoflow/users.json"))

# In-memory cache: {username: {"hashed_password": str, "role": Role}}
_users: dict[str, dict] = {}
_loaded = False


def _store_path() -> Path:
    """Return the configured users file path (allows override in tests)."""
    return Path(os.getenv("USERS_FILE", str(_USERS_FILE)))


def _load(admin_fallback_password: str = "") -> None:
    """Load users from disk.  Create the default admin if the store is empty."""
    global _users, _loaded
    path = _store_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            _users = data.get("users", {})
        except (json.JSONDecodeError, OSError) as exc:
            _log.error("Failed to load users file %s: %s — starting with empty store", path, exc)
            _users = {}
    else:
        _users = {}

    if not _users:
        # Bootstrap: create a single admin user so the service stays functional
        password = admin_fallback_password or os.getenv("API_SECRET_KEY", "changeme")
        _users["admin"] = {
            "hashed_password": hash_password(password),
            "role": "admin",
        }
        _log.info(
            "User store is empty — created default admin user "
            "(password = API_SECRET_KEY).  Add more users via POST /api/v1/users."
        )
        _save()

    _loaded = True


def _save() -> None:
    """Persist the current user store to disk."""
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"users": _users}, indent=2))
    except OSError as exc:
        _log.error("Failed to write users file %s: %s", path, exc)


def _ensure_loaded(admin_fallback_password: str = "") -> None:
    if not _loaded:
        _load(admin_fallback_password)


# ── Public API ────────────────────────────────────────────────────────────────


def authenticate(username: str, password: str) -> str | None:
    """
    Verify *username* / *password*.  Returns the role on success, None on failure.

    This is the only function that checks plaintext passwords.
    """
    _ensure_loaded()
    entry = _users.get(username)
    if entry is None:
        return None
    if not verify_password(password, entry["hashed_password"]):
        return None
    return entry["role"]


def get_role(username: str) -> Role | None:
    """Return the role for *username*, or None if the user does not exist."""
    _ensure_loaded()
    entry = _users.get(username)
    return entry["role"] if entry else None


def list_users() -> list[dict]:
    """Return a list of ``{username, role}`` dicts (no passwords)."""
    _ensure_loaded()
    return [{"username": u, "role": d["role"]} for u, d in sorted(_users.items())]


def create_user(username: str, password: str, role: Role) -> None:
    """
    Create a new user.  Raises ValueError if the username already exists
    or the role is invalid.
    """
    _ensure_loaded()
    if username in _users:
        raise ValueError(f"User '{username}' already exists")
    if role not in ROLES:
        raise ValueError(f"Invalid role '{role}' — must be one of {ROLES}")
    _users[username] = {"hashed_password": hash_password(password), "role": role}
    _save()
    _log.info("Created user '%s' with role '%s'", username, role)


def update_role(username: str, role: Role) -> None:
    """Change a user's role.  Raises ValueError if user not found or role invalid."""
    _ensure_loaded()
    if username not in _users:
        raise ValueError(f"User '{username}' not found")
    if role not in ROLES:
        raise ValueError(f"Invalid role '{role}' — must be one of {ROLES}")
    _users[username]["role"] = role
    _save()
    _log.info("Updated role for '%s' → '%s'", username, role)


def update_password(username: str, new_password: str) -> None:
    """Change a user's password.  Raises ValueError if user not found."""
    _ensure_loaded()
    if username not in _users:
        raise ValueError(f"User '{username}' not found")
    _users[username]["hashed_password"] = hash_password(new_password)
    _save()
    _log.info("Updated password for '%s'", username)


def delete_user(username: str) -> None:
    """
    Delete a user.  Raises ValueError if user not found or is the last admin
    (to prevent accidental lockout).
    """
    _ensure_loaded()
    if username not in _users:
        raise ValueError(f"User '{username}' not found")
    admin_count = sum(1 for u in _users.values() if u["role"] == "admin")
    if _users[username]["role"] == "admin" and admin_count <= 1:
        raise ValueError("Cannot delete the last admin user")
    del _users[username]
    _save()
    _log.info("Deleted user '%s'", username)


def reload() -> None:
    """Force a reload from disk (useful in tests)."""
    global _loaded
    _loaded = False
    _load()
