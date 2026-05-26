# Autoflow — tests/unit/test_user_store — Apache 2.0
"""
Unit tests for the user store (services/api/user_store.py).

All tests use a temporary directory for the store file to avoid any
interaction with the real /etc/autoflow/users.json.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Load user_store under a unique alias ─────────────────────────────────────

_API = Path(__file__).parent.parent.parent / "services" / "api"


def _load_us(tmp_path: Path):
    """Load a fresh user_store module instance (bypasses sys.modules caching).

    Note: user_store.py imports only stdlib (os, json, logging, pathlib) and
    passlib — no services/api path manipulation needed, which keeps sys.path
    clean and avoids polluting the canonical 'main' / 'settings' module names
    used by the integration tests.
    """
    alias = f"user_store_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(alias, _API / "user_store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Point the module at a temp directory
    store_path = tmp_path / "users.json"
    mod._USERS_FILE = store_path
    # Reset module state
    mod._users = {}
    mod._loaded = False
    return mod, store_path


# ── Bootstrap ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_bootstrap_creates_admin(tmp_path):
    """When the store is absent, a default admin user must be created."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store), "API_SECRET_KEY": "mysecret"}):
        us._ensure_loaded("mysecret")
    assert "admin" in us._users
    assert us._users["admin"]["role"] == "admin"
    # File is written to disk
    assert store.exists()


@pytest.mark.unit
def test_bootstrap_password_usable(tmp_path):
    """The bootstrapped admin must be authenticatable with the fallback password."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._ensure_loaded("s3cr3t-pass")
    result = us.authenticate("admin", "s3cr3t-pass")
    assert result == "admin"


@pytest.mark.unit
def test_bootstrap_not_repeated(tmp_path):
    """Loading twice must not overwrite an existing store."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._ensure_loaded("first-pass")
        # Manually add a second user
        us.create_user("alice", "alicepass1", "operator")
        us._loaded = False  # force reload from disk
        us._load()
    # alice must still exist after reload
    assert "alice" in us._users


# ── authenticate ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_authenticate_valid(tmp_path):
    """Valid credentials return the user's role."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.create_user("bob", "bobpass123", "operator")
    assert us.authenticate("bob", "bobpass123") == "operator"


@pytest.mark.unit
def test_authenticate_wrong_password(tmp_path):
    """Wrong password returns None."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    assert us.authenticate("admin", "wrongpass") is None


@pytest.mark.unit
def test_authenticate_unknown_user(tmp_path):
    """Unknown user returns None."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    assert us.authenticate("nobody", "pass") is None


# ── create_user ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_create_user_success(tmp_path):
    """Creating a new user with a valid role must succeed."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.create_user("carol", "carolpass1", "viewer")
    assert "carol" in us._users
    assert us._users["carol"]["role"] == "viewer"


@pytest.mark.unit
def test_create_user_duplicate_raises(tmp_path):
    """Creating a user with a duplicate username must raise ValueError."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    with pytest.raises(ValueError, match="already exists"):
        us.create_user("admin", "newpass12", "operator")


@pytest.mark.unit
def test_create_user_invalid_role(tmp_path):
    """Creating a user with an invalid role must raise ValueError."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    with pytest.raises(ValueError, match="Invalid role"):
        us.create_user("dave", "davepass1", "superuser")  # type: ignore


# ── delete_user ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_delete_user_success(tmp_path):
    """A non-admin (or extra admin) user can be deleted."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.create_user("alice", "alicepass1", "operator")
        us.delete_user("alice")
    assert "alice" not in us._users


@pytest.mark.unit
def test_delete_last_admin_raises(tmp_path):
    """Deleting the last admin must raise ValueError."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    with pytest.raises(ValueError, match="last admin"):
        us.delete_user("admin")


@pytest.mark.unit
def test_delete_nonexistent_raises(tmp_path):
    """Deleting a user that does not exist must raise ValueError."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    with pytest.raises(ValueError, match="not found"):
        us.delete_user("ghost")


# ── update_role ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_update_role_success(tmp_path):
    """update_role changes the role and persists it."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.create_user("eve", "evepass12", "viewer")
        us.update_role("eve", "operator")
    assert us._users["eve"]["role"] == "operator"


@pytest.mark.unit
def test_update_role_invalid(tmp_path):
    """update_role with an unknown role raises ValueError."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
    with pytest.raises(ValueError, match="Invalid role"):
        us.update_role("admin", "root")  # type: ignore


# ── update_password ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_update_password(tmp_path):
    """After update_password, the new password authenticates correctly."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.update_password("admin", "newAdminPass9")
    assert us.authenticate("admin", "newAdminPass9") == "admin"
    assert us.authenticate("admin", "adminpass") is None


# ── list_users ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_list_users_no_passwords(tmp_path):
    """list_users must not include hashed_password fields."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.create_user("frank", "frankpass", "viewer")
    result = us.list_users()
    for entry in result:
        assert "hashed_password" not in entry
        assert "username" in entry
        assert "role" in entry


# ── persistence ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_persistence_survives_reload(tmp_path):
    """Users created in one session are still present after a reload from disk."""
    us, store = _load_us(tmp_path)
    with patch.dict("os.environ", {"USERS_FILE": str(store)}):
        us._load("adminpass")
        us.create_user("grace", "gracepass", "operator")
        # Simulate restart
        us._users = {}
        us._loaded = False
        us._load()
    assert "grace" in us._users
    assert us.authenticate("grace", "gracepass") == "operator"
