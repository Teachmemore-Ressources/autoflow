"""
Unit-test conftest for the deploy wizard.

Loads services/deploy-wizard/main.py under the alias ``wizard_main`` so it
never shadows the event-engine or API ``main`` modules that live in
sys.modules["main"] when the full test suite runs together.

WIZARD_TOKEN is expected to be set by the root conftest before this file runs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_WIZARD_DIR = (
    Path(__file__).parent.parent.parent.parent / "services" / "deploy-wizard"
)


def _load_wizard_main():
    """Load wizard main.py as 'wizard_main' — idempotent."""
    alias = "wizard_main"
    if alias in sys.modules:
        return sys.modules[alias]

    # Append wizard dir LAST so it never shadows event-engine's main.py
    # (test_event_flow.py does `from main import _dispatch_core` directly
    #  and depends on EE being earlier in sys.path than the wizard dir).
    if str(_WIZARD_DIR) not in sys.path:
        sys.path.append(str(_WIZARD_DIR))

    spec = importlib.util.spec_from_file_location(
        alias, str(_WIZARD_DIR / "main.py")
    )
    mod = importlib.util.module_from_spec(spec)       # type: ignore[arg-type]
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]
    return mod


# Load eagerly so the module is ready before any test collects
_load_wizard_main()


# ── Shared fixture: redirect wizard file paths to a temp directory ────────────

@pytest.fixture
def wizard_env_paths(tmp_path, monkeypatch):
    """
    Redirect the wizard's global Path constants to tmp_path so unit tests
    that call _write_env / _load_env / _audit never touch the real repo root.

    Patches both wizard_main (for backward-compat) and core.env (where the
    functions actually read the module-level globals after the refactor).
    """
    import core.env as core_env
    import wizard_main as wm

    env_file    = tmp_path / ".env"
    env_example = tmp_path / ".env.example"
    audit_log   = tmp_path / "audit.log"

    # Patch wizard_main re-exports (backward compat)
    monkeypatch.setattr(wm, "ENV_FILE",         env_file)
    monkeypatch.setattr(wm, "ENV_EXAMPLE_FILE", env_example)
    monkeypatch.setattr(wm, "AUDIT_LOG",        audit_log)

    # Patch core.env where the functions actually live
    monkeypatch.setattr(core_env, "ENV_FILE",         env_file)
    monkeypatch.setattr(core_env, "ENV_EXAMPLE_FILE", env_example)
    monkeypatch.setattr(core_env, "AUDIT_LOG",        audit_log)

    return {
        "env":     env_file,
        "example": env_example,
        "audit":   audit_log,
        "tmp":     tmp_path,
    }
