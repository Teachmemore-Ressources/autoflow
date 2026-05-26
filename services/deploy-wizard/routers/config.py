"""
routers/config.py — Configuration schema, .env read/write, secret generation and SOPS encryption.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from datetime import datetime, timezone

import bcrypt
from core.auth import _audit
from core.env import (
    _HEADING_KEYS,
    ENV_ENC_FILE,
    ENV_EXAMPLE_FILE,  # noqa: F401 — re-exported for integration test monkeypatching
    ENV_FILE,
    GITEA_BEARER_TOKEN_FILE,
    MONITORING_USERS,
    SOPS_AGE_KEY_FILE,
    WIZARD_VERSION,
    _load_env,
    _write_env,
)
from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Request
from schema import FIELDS, SECTIONS

router = APIRouter()

# ── Service restart map ───────────────────────────────────────────────────────

KEY_TO_SERVICES: dict[str, list[str]] = {
    "DOMAIN":                     ["traefik"],
    "TRAEFIK_HTTP_PORT":          ["traefik"],
    "TRAEFIK_HTTPS_PORT":         ["traefik"],
    "GITEA_SSH_PORT":             ["traefik", "gitea"],
    "POSTGRES_PASSWORD":          ["postgres", "awx", "awx_task", "awx_ee"],
    "REDIS_PASSWORD":             ["redis", "awx", "awx_task", "awx_ee", "event_engine"],
    "AWX_TOKEN":                  ["event_engine"],
    "AWX_JOB_TEMPLATE_ID":        ["event_engine"],
    "API_SECRET_KEY":             ["api"],
    "API_USERNAME":               ["api"],
    "API_PASSWORD":               ["api"],
    "CORS_ORIGINS":               ["api"],
    "RATE_LIMIT":                 ["api"],
    "JWT_SECRET_KEY":             ["api"],
    "JWT_EXPIRE_MINUTES":         ["api"],
    "GRAFANA_ADMIN_PASSWORD":     ["grafana"],
    "PROMETHEUS_RETENTION":       ["prometheus"],
    "LOKI_RETENTION":             ["loki"],
    "MONITORING_ADMIN_USER":      ["traefik"],
    "MONITORING_ADMIN_PASSWORD":  ["traefik"],
    "MINIO_ROOT_USER":            ["minio"],
    "MINIO_ROOT_PASSWORD":        ["minio"],
    "LOKI_S3_ACCESS_KEY":         ["loki"],
    "LOKI_S3_SECRET_KEY":         ["loki"],
    "DEDUP_TTL":                  ["event_engine"],
    "GITHUB_WEBHOOK_SECRET":      ["event_engine"],
    "EVENT_ENGINE_ADMIN_TOKEN":   ["event_engine"],
    "NOTIFICATION_WEBHOOK_URL":   ["event_engine"],
    "NOTIFICATION_SLACK_WEBHOOK": ["event_engine"],
    "JOB_WATCHER_INTERVAL":       ["event_engine"],
    "GITEA_DOMAIN":               ["gitea"],
    "GITEA_ROOT_URL":             ["gitea"],
    "GITEA_DB_PASSWORD":          ["gitea_postgres", "gitea"],
    "GITEA_METRICS_TOKEN":        ["gitea", "prometheus"],
    "GITEA_WEBHOOK_SECRET":       ["gitea"],
    "GITEA_REGISTRY_TOKEN":       ["gitea"],
    "GITEA_LOG_LEVEL":            ["gitea"],
    "PKI_ADMIN_PASSWORD":         ["pki"],
    "PKI_JWT_SECRET":             ["pki"],
    "LOG_LEVEL":                  ["api", "event_engine"],
    "AWX_METRICS_INTERVAL":       ["event_engine"],
    "SCAN_INTERVAL":              ["api"],
    "EE_DEFAULT_VERSION":         ["ee_builder", "awx_migrate"],
    "BUILD_PYCMD":                ["ee_builder"],
    "DOCKER_GID":                 ["ee_builder", "act_runner", "security_scanner"],
}

# Keys consumed only on first container start — changing them has no effect after init
FIRST_START_ONLY: frozenset[str] = frozenset({
    "AWX_ADMIN_USER", "AWX_ADMIN_PASSWORD", "AWX_ADMIN_EMAIL", "AWX_SECRET_KEY",
    "GITEA_ADMIN_USER", "GITEA_ADMIN_PASSWORD", "GITEA_ADMIN_EMAIL",
    "GITEA_SECRET_KEY", "GITEA_INTERNAL_TOKEN",
    "POSTGRES_DB", "POSTGRES_USER",
    "GITEA_DB_NAME", "GITEA_DB_USER",
    "GRAFANA_ADMIN_USER", "PKI_JWT_SECRET",
    # minio_init est restart:no — le bucket/user Loki est créé une seule fois
    "LOKI_S3_ACCESS_KEY", "LOKI_S3_SECRET_KEY",
})

# Keys that require container recreation (not just restart)
NEEDS_RECREATE: frozenset[str] = frozenset({
    "DOCKER_GID", "TRAEFIK_HTTP_PORT", "TRAEFIK_HTTPS_PORT", "GITEA_SSH_PORT",
})


# ── Schema ────────────────────────────────────────────────────────────────────

@router.get("/api/schema")
def get_schema():
    return {
        "sections":        SECTIONS,
        "fields":          FIELDS,
        "first_start_only": sorted(FIRST_START_ONLY),
    }


# ── Config ────────────────────────────────────────────────────────────────────

@router.get("/api/config")
def get_config():
    return _load_env()


@router.post("/api/config")
def save_config(request: Request, data: dict):
    # Drop UI-only heading fields — they must never reach .env
    data = {k: v for k, v in data.items() if k not in _HEADING_KEYS}

    # Load current .env to compute diff BEFORE merging
    old: dict[str, str] = {}
    if ENV_FILE.exists():
        old = {k: v or "" for k, v in dotenv_values(ENV_FILE).items()}

    # Keys whose value actually changed
    changed = [k for k, v in data.items() if v is not None and old.get(k, "") != str(v)]

    # Merge incoming data on top of existing
    merged = {**old}
    merged.update({k: v for k, v in data.items() if v is not None})

    # ── Auto-derive values from DOMAIN ─────────────────────────────
    domain = merged.get("DOMAIN", "localhost")
    if not merged.get("GITEA_DOMAIN"):
        merged["GITEA_DOMAIN"] = domain
    if not merged.get("GITEA_ROOT_URL"):
        merged["GITEA_ROOT_URL"] = f"https://git.{domain}"
    if not merged.get("PKI_BASE_URL"):
        merged["PKI_BASE_URL"] = f"https://pki.{domain}"
    if not merged.get("CORS_ORIGINS"):
        merged["CORS_ORIGINS"] = (
            f"https://awx.{domain},https://api.{domain},https://pki.{domain}"
        )

    # ── Backup .env before overwriting ────────────────────────────
    backup_path: str | None = None
    if ENV_FILE.exists():
        _bak_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        _bak    = ENV_FILE.with_name(f".env.bak.{_bak_ts}")
        shutil.copy2(ENV_FILE, _bak)
        _bak.chmod(0o600)
        backup_path = str(_bak)

    # ── Write .env preserving template structure ───────────────────
    _write_env(merged)

    # ── Regenerate monitoring_users (bcrypt for Traefik BasicAuth) ─
    pwd  = merged.get("MONITORING_ADMIN_PASSWORD", "")
    user = merged.get("MONITORING_ADMIN_USER", "admin")
    if pwd:
        hashed = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(12)).decode()
        hashed = hashed.replace("$2b$", "$2y$")  # Traefik requires $2y$
        MONITORING_USERS.parent.mkdir(parents=True, exist_ok=True)
        MONITORING_USERS.write_text(f"{user}:{hashed}\n")
        MONITORING_USERS.chmod(0o600)

    # ── Write Gitea metrics bearer token for Prometheus ───────────
    gitea_token = merged.get("GITEA_METRICS_TOKEN", "")
    if gitea_token:
        try:
            GITEA_BEARER_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            GITEA_BEARER_TOKEN_FILE.write_text(gitea_token)
            GITEA_BEARER_TOKEN_FILE.chmod(0o644)  # readable by Prometheus non-root user
        except PermissionError:
            pass  # file owned by root; user must run: sudo chown $(whoami) <path>

    # ── Compute affected services and warnings ─────────────────────
    services: set[str] = set()
    for key in changed:
        services.update(KEY_TO_SERVICES.get(key, []))

    warnings: list[dict] = []
    first_start_hits = [k for k in changed if k in FIRST_START_ONLY]
    needs_recreate   = any(k in NEEDS_RECREATE for k in changed)

    if first_start_hits:
        warnings.append({
            "type": "first_start_only",
            "message": (
                "These values are only read on first container start — "
                "restart has no effect, update manually in the service UI: "
                + ", ".join(first_start_hits)
            ),
        })
    if "DOMAIN" in changed:
        warnings.append({
            "type": "domain_changed",
            "message": "Domain changed — regenerate the TLS certificate in Pre-flight.",
        })
    if needs_recreate:
        warnings.append({
            "type": "needs_recreate",
            "message": (
                "Port or DOCKER_GID changes require container recreation. "
                "Use 'Save & Deploy' (docker compose up -d) instead of restart."
            ),
        })

    _audit(request, "config.save", keys=changed)

    return {
        "status":            "saved",
        "changed":           changed,
        "affected_services": sorted(services),
        "needs_recreate":    needs_recreate,
        "warnings":          warnings,
        "backup":            backup_path,
    }


# ── Secret generation ─────────────────────────────────────────────────────────

@router.get("/api/generate/{generate_type}")
def generate_secret(generate_type: str):
    match generate_type:
        case "hex32":
            return {"value": secrets.token_hex(32)}
        case "hex64":
            return {"value": secrets.token_hex(64)}
        case "urlsafe32":
            return {"value": secrets.token_urlsafe(32)}
        case _:
            raise HTTPException(400, f"Unknown generate_type: {generate_type}")


@router.get("/api/version")
def get_version():
    """Return the wizard version."""
    return {"version": WIZARD_VERSION}


# ── SOPS ──────────────────────────────────────────────────────────────────────

@router.post("/api/encrypt")
def encrypt_env(request: Request):
    if not ENV_FILE.exists():
        raise HTTPException(400, ".env not found — save config first")
    env = {**os.environ, "SOPS_AGE_KEY_FILE": str(SOPS_AGE_KEY_FILE)}
    result = subprocess.run(
        ["sops", "--encrypt", "--input-type", "dotenv", "--output-type", "dotenv", str(ENV_FILE)],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        raise HTTPException(500, f"SOPS error: {result.stderr.strip()}")
    ENV_ENC_FILE.write_text(result.stdout)
    _audit(request, "secrets.encrypt")
    return {"status": "encrypted", "path": str(ENV_ENC_FILE)}
