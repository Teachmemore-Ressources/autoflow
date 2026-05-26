"""
Integration-test fixtures for the deploy wizard.

Strategy
--------
wizard main.py is loaded under the alias ``wizard_main`` (same as the unit
conftest) so it never shadows ``main`` from the event-engine or API.

The ``wizard_app`` fixture is session-scoped (one import per run).
The ``client`` fixture is function-scoped: a fresh ASGI AsyncClient per test.
The ``wizard_paths`` fixture is function-scoped: redirects global Path constants
via monkeypatch so tests never touch the real repo's .env / audit log.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

_WIZARD_DIR = Path(__file__).parent.parent.parent.parent / "services" / "deploy-wizard"

# Token must match what root conftest set in os.environ["WIZARD_TOKEN"]
_TOKEN = os.environ.get("WIZARD_TOKEN", "test-wizard-token")


def _load_wizard_main():
    """Load wizard main.py as 'wizard_main' — idempotent across test modules."""
    alias = "wizard_main"
    if alias in sys.modules:
        return sys.modules[alias]

    # Append LAST — must not shadow event-engine's main.py which other
    # integration tests import via `from main import _dispatch_core`
    if str(_WIZARD_DIR) not in sys.path:
        sys.path.append(str(_WIZARD_DIR))

    spec = importlib.util.spec_from_file_location(alias, str(_WIZARD_DIR / "main.py"))
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="session")
def wizard_app():
    """The wizard FastAPI app — loaded once per pytest session."""
    return _load_wizard_main().app


@pytest.fixture
async def client(wizard_app):
    """
    Async ASGI test client authenticated with the test wizard token.
    username is arbitrary (ignored); password must match WIZARD_TOKEN.
    """
    async with AsyncClient(
        transport=ASGITransport(app=wizard_app),
        base_url="http://testserver",
        auth=("tester", _TOKEN),
    ) as c:
        yield c


@pytest.fixture
def wizard_paths(tmp_path, monkeypatch):
    """
    Redirect wizard global Path constants to tmp_path so tests are isolated.

    Patched names
    -------------
    ENV_FILE, ENV_EXAMPLE_FILE  — .env read/write
    MONITORING_USERS            — bcrypt hash file (written by save_config)
    GITEA_BEARER_TOKEN_FILE     — Prometheus bearer token (written by save_config)
    AUDIT_LOG                   — append-only JSON audit trail

    Patches both wizard_main (backward compat) and core.env / routers.config
    (where the functions actually live after the package refactor).
    """
    import core.auth as core_auth
    import core.env as core_env
    import routers.config as routers_config
    import wizard_main as wm

    env_file = tmp_path / ".env"
    env_example = tmp_path / ".env.example"
    mon_users = tmp_path / "monitoring_users"
    gitea_tok = tmp_path / "gitea_token"
    audit_log = tmp_path / "audit.log"

    # Patch core.env (source of truth) and all modules that imported from it
    for mod in (wm, core_env, routers_config):
        monkeypatch.setattr(mod, "ENV_FILE", env_file)
        monkeypatch.setattr(mod, "ENV_EXAMPLE_FILE", env_example)

    for mod in (wm, core_env, routers_config):
        monkeypatch.setattr(mod, "MONITORING_USERS", mon_users)
        monkeypatch.setattr(mod, "GITEA_BEARER_TOKEN_FILE", gitea_tok)

    for mod in (wm, core_env, core_auth):
        monkeypatch.setattr(mod, "AUDIT_LOG", audit_log)

    return {
        "env": env_file,
        "example": env_example,
        "mon_users": mon_users,
        "gitea_token": gitea_tok,
        "audit": audit_log,
        "tmp": tmp_path,
    }
