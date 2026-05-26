"""
Integration test fixtures.

Design
------
Both the event engine and the API service have modules named ``main``,
``settings``, etc.  Adding both services to ``sys.path`` causes Python to
cache the first one it finds, breaking imports in whichever file runs second.

To avoid this, service modules are loaded with **unique aliases** via
``importlib.util.spec_from_file_location``, e.g.:
    ``ee_main``    → services/event-engine/main.py
    ``api_notifications`` → services/api/notifications.py

The ``sys.path`` is still extended (so transitive ``import`` statements inside
service modules resolve), but the *alias* trick prevents cross-service shadowing
of top-level module names.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# ── Repo paths ────────────────────────────────────────────────────────────────

_REPO   = Path(__file__).parent.parent.parent
_EE     = _REPO / "services" / "event-engine"
_API    = _REPO / "services" / "api"
_SHARED = _REPO / "services" / "shared"
_STUBS  = _REPO / "tests" / "stubs"

# Shared modules (security_headers, etc.) must be importable from service modules
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def _load_module(alias: str, file_path: Path, prepend_paths: list[Path] | None = None):
    """
    Load a Python source file as a module under *alias*.

    The module is registered in ``sys.modules[alias]`` so subsequent imports
    can find it.  *prepend_paths* are inserted at the FRONT of sys.path only
    when the caller explicitly needs transitive imports from those directories
    to resolve (use sparingly to avoid shadowing other service modules).
    """
    if alias in sys.modules:
        return sys.modules[alias]

    for p in (prepend_paths or []):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    spec = importlib.util.spec_from_file_location(alias, str(file_path))
    mod  = importlib.util.module_from_spec(spec)        # type: ignore[arg-type]
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)                        # type: ignore[union-attr]
    return mod


# ── Event-engine module registry ─────────────────────────────────────────────
#
# Pre-load EE modules under their canonical names so that intra-service
# ``from settings import settings`` etc. resolve correctly.  The EE path
# is added to sys.path once here; it will never shadow api/main.py because
# the API modules are loaded via importlib aliases.

def _ensure_ee_loaded():
    """Load event-engine modules into sys.modules under their canonical names."""
    if str(_EE) not in sys.path:
        sys.path.insert(0, str(_EE))

    # Order matters — dependencies before dependants
    for name in ("settings", "tracing", "dedup", "rules", "parsers",
                 "awx_client", "event_store", "retry_worker", "scheduler", "main"):
        if name not in sys.modules:
            _load_module(name, _EE / f"{name}.py", prepend_paths=[_EE])


def _ensure_api_loaded():
    """
    Load the API notifications module under the alias ``api_notifications``,
    without adding services/api to sys.path (which would shadow the
    event-engine's ``main.py`` and other EE modules).

    The trick: ``notifications.py`` does ``from settings import settings``.
    We temporarily replace ``sys.modules['settings']`` with the API's settings
    object, exec the module, then restore.  The loaded module retains a
    reference to the *API* settings object internally.
    """
    _api_alias = "api_notifications"
    if _api_alias in sys.modules:
        return sys.modules[_api_alias]

    # Load the API settings under a unique alias (no sys.path change needed —
    # settings.py only imports pydantic_settings and os, both always available).
    api_settings_alias = "api_settings_module"
    if api_settings_alias not in sys.modules:
        _load_module(api_settings_alias, _API / "settings.py")

    # Temporarily expose the API settings as 'settings' so that
    # ``from settings import settings`` inside notifications.py resolves
    # to the API version.
    _old_settings = sys.modules.get("settings")
    sys.modules["settings"] = sys.modules[api_settings_alias]

    try:
        # Load notifications.py without adding _API to sys.path.
        # Its only non-stdlib import is 'from settings import settings' (handled
        # above) and 'import httpx' (always available).
        _load_module(_api_alias, _API / "notifications.py")
    finally:
        # Restore the original 'settings' entry (event-engine's, if already loaded)
        if _old_settings is not None:
            sys.modules["settings"] = _old_settings
        elif "settings" in sys.modules and sys.modules.get("settings") is sys.modules.get(api_settings_alias):
            del sys.modules["settings"]

    return sys.modules[_api_alias]


# ── AWX stub ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def awx_stub_app():
    """AWX stub FastAPI app (shared across all integration tests)."""
    mod = _load_module("stub_awx", _STUBS / "awx.py")
    return mod.app


@pytest.fixture
async def awx_stub(awx_stub_app):
    """In-process AWX stub — state reset before each test."""
    async with AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
    ) as client:
        await client.delete("/_test/reset")
        yield client


# ── Callback stub ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def callback_stub_app():
    """Notification callback stub FastAPI app."""
    mod = _load_module("stub_callback", _STUBS / "callback.py")
    return mod.app


@pytest.fixture
async def callback_stub(callback_stub_app):
    """In-process callback stub — state reset before each test."""
    async with AsyncClient(
        transport=ASGITransport(app=callback_stub_app),
        base_url="http://callback-stub",
    ) as client:
        await client.delete("/_test/reset")
        yield client


# ── Event engine fixture ──────────────────────────────────────────────────────

@pytest.fixture
async def ee_client(awx_stub_app, awx_stub):
    """
    Event engine ASGI test client.

    ``app.state`` is populated manually so the lifespan is never started
    (no background tasks, no Redis, no APScheduler).
    AWX HTTP calls route to the in-process AWX stub.
    """
    _ensure_ee_loaded()

    from awx_client import AWXClient
    from dedup import DedupStore
    from main import app
    from rules import RuleEngine
    from settings import settings

    awx_http = AsyncClient(
        transport=ASGITransport(app=awx_stub_app),
        base_url="http://awx-stub",
        headers={"Authorization": "Bearer test-awx-token"},
        timeout=30.0,
    )

    app.state.awx          = AWXClient(awx_http, settings.awx_job_template_id)
    app.state.rules        = RuleEngine(
        settings.rules_file or None, settings.awx_job_template_id
    )
    app.state.dedup        = DedupStore(ttl_seconds=settings.dedup_ttl)
    app.state.event_store  = None
    app.state.retry_worker = None

    sched = MagicMock()
    sched.loaded_count = 0
    sched.jobs.return_value = []
    app.state.scheduler = sched

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    await awx_http.aclose()
    app.state.dedup.clear()


# ── Notification module fixture ───────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_notifications():
    """API notifications module loaded without shadowing EE modules."""
    return _ensure_api_loaded()
