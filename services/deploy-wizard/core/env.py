"""
core/env.py — Path constants, .env helpers, and shared globals for the Deploy Wizard.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from schema import FIELDS, SECTIONS  # noqa: F401 — re-exported for convenience

# ── Version ───────────────────────────────────────────────────────────────────
WIZARD_VERSION = "1.0.0"

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT              = Path(os.environ.get("AUTOFLOW_ROOT", Path(__file__).parent.parent.parent.parent))
ENV_FILE          = ROOT / ".env"
ENV_ENC_FILE      = ROOT / ".env.enc"
ENV_EXAMPLE_FILE  = ROOT / ".env.example"
MONITORING_USERS        = ROOT / "traefik/dynamic/monitoring_users"
GITEA_BEARER_TOKEN_FILE = ROOT / "monitoring/prometheus/secrets/gitea_bearer_token"
TLS_YML           = ROOT / "traefik/dynamic/tls.yml"
CERTS_DIR         = ROOT / "traefik/certs"
AWX_DOCKERFILE    = ROOT / "awx/Dockerfile.patched"
SOPS_AGE_KEY_FILE = Path.home() / ".config/sops/age/keys.txt"

STATIC_DIR = Path(__file__).parent.parent / "static"
AUDIT_LOG  = ROOT / "wizard-audit.log"

# ── UI-only fields (heading + info banners) — never written to .env ───────────
_HEADING_KEYS: frozenset[str] = frozenset(
    f["key"] for f in FIELDS if f.get("type") in ("heading", "info")
)

# ── Shell-unsafe characters for .env quoting ─────────────────────────────────
_SHELL_UNSAFE = set(' \t*?[]{}()<>|&;!\\$`\'"')


# ── .env helpers ──────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    """Load current .env values, falling back to .env.example defaults."""
    config: dict[str, str] = {}
    # Seed with schema defaults (skip UI-only heading fields)
    for f in FIELDS:
        if f.get("type") == "heading":
            continue
        config[f["key"]] = f.get("default", "")
    # Override from .env if it exists
    if ENV_FILE.exists():
        config.update({k: v or "" for k, v in dotenv_values(ENV_FILE).items()})
    elif ENV_EXAMPLE_FILE.exists():
        config.update({k: v or "" for k, v in dotenv_values(ENV_EXAMPLE_FILE).items()})
    return config


def _quote_env_value(v: str) -> str:
    """Quote a .env value when it contains characters that bash would misinterpret.

    Both python-dotenv and bash handle double-quoted values correctly, so this
    is safe for both programmatic reads (dotenv_values) and `source .env` in scripts.
    """
    if not v:
        return ""
    if any(c in v for c in _SHELL_UNSAFE):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


def _write_env(values: dict[str, str]) -> None:
    """Write .env, preserving structure from template if available."""
    template = ENV_EXAMPLE_FILE if ENV_EXAMPLE_FILE.exists() else None
    lines: list[str] = []
    written: set[str] = set()

    if template:
        for raw in template.read_text().splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(raw)
            elif "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip()
                if key in values:
                    lines.append(f"{key}={_quote_env_value(values[key])}")
                    written.add(key)
                else:
                    lines.append(raw)
            else:
                lines.append(raw)

    # Append any keys not in the template
    extras = [k for k in values if k not in written]
    if extras:
        if lines:
            lines.append("")
        for key in extras:
            lines.append(f"{key}={_quote_env_value(values[key])}")

    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)
