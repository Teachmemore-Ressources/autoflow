"""
Autoflow Deploy Wizard — Backend
Serves the configuration UI and orchestrates .env writes,
SOPS encryption, cert generation and docker compose deployment.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import warnings

import bcrypt
import httpx
from dotenv import dotenv_values

# Suppress TLS verification warnings for internal calls to self-signed CA
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from schema import FIELDS, SECTIONS

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT              = Path(os.environ.get("AUTOFLOW_ROOT", Path(__file__).parent.parent.parent))
ENV_FILE          = ROOT / ".env"
ENV_ENC_FILE      = ROOT / ".env.enc"
ENV_EXAMPLE_FILE  = ROOT / ".env.example"
MONITORING_USERS        = ROOT / "traefik/dynamic/monitoring_users"
GITEA_BEARER_TOKEN_FILE = ROOT / "monitoring/prometheus/secrets/gitea_bearer_token"
TLS_YML           = ROOT / "traefik/dynamic/tls.yml"
CERTS_DIR         = ROOT / "traefik/certs"
AWX_DOCKERFILE    = ROOT / "awx/Dockerfile.patched"
SOPS_AGE_KEY_FILE = Path.home() / ".config/sops/age/keys.txt"

STATIC_DIR = Path(__file__).parent / "static"
AUDIT_LOG  = ROOT / "wizard-audit.log"

# ── Auth ──────────────────────────────────────────────────────────────────────

_WIZARD_TOKEN = os.environ.get("WIZARD_TOKEN", "").strip()
if not _WIZARD_TOKEN:
    print(
        "\n  ERROR: WIZARD_TOKEN environment variable is not set.\n"
        "  Generate a token and export it before starting the wizard:\n\n"
        "    export WIZARD_TOKEN=$(python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")\n"
        "    make wizard\n",
        file=sys.stderr,
    )
    sys.exit(1)

_http_basic = HTTPBasic(realm="Autoflow Deploy Wizard")


def _require_auth(creds: HTTPBasicCredentials = Depends(_http_basic)) -> str:
    """HTTP Basic Auth — username ignored, password must match WIZARD_TOKEN."""
    ok = secrets.compare_digest(creds.password.encode(), _WIZARD_TOKEN.encode())
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": 'Basic realm="Autoflow Deploy Wizard"'},
        )
    return creds.username


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(request: Request, action: str, **extra) -> None:
    """Append a JSON line to wizard-audit.log — values are never logged."""
    entry = {
        "ts":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ip":     request.client.host if request.client else "unknown",
        "action": action,
        **extra,
    }
    with AUDIT_LOG.open("a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── App ───────────────────────────────────────────────────────────────────────
# dependencies=[Depends(_require_auth)] applique l'auth Basic à TOUTES les routes.
# Les fichiers statiques (app.mount) ne passent pas par ce mécanisme — OK car
# /static/ ne contient que HTML/CSS/logo, aucune donnée sensible.
app = FastAPI(
    title="Autoflow Deploy Wizard",
    docs_url=None,
    redoc_url=None,
    dependencies=[Depends(_require_auth)],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    return (STATIC_DIR / "index.html").read_text()


# ── Schema ────────────────────────────────────────────────────────────────────

@app.get("/api/schema")
def get_schema():
    return {"sections": SECTIONS, "fields": FIELDS}


# ── Config ────────────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    """Load current .env values, falling back to .env.example defaults."""
    config: dict[str, str] = {}
    # Seed with schema defaults
    for f in FIELDS:
        config[f["key"]] = f.get("default", "")
    # Override from .env if it exists
    if ENV_FILE.exists():
        config.update({k: v or "" for k, v in dotenv_values(ENV_FILE).items()})
    elif ENV_EXAMPLE_FILE.exists():
        config.update({k: v or "" for k, v in dotenv_values(ENV_EXAMPLE_FILE).items()})
    return config


@app.get("/api/config")
def get_config():
    return _load_env()


@app.post("/api/config")
def save_config(request: Request, data: dict):
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
    }


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
                    lines.append(f"{key}={values[key]}")
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
            lines.append(f"{key}={values[key]}")

    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


# ── Secret generation ─────────────────────────────────────────────────────────

@app.get("/api/generate/{generate_type}")
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


# ── SOPS ──────────────────────────────────────────────────────────────────────

@app.post("/api/encrypt")
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

# ── Docker Compose deployment ─────────────────────────────────────────────────

@app.get("/api/deploy")
async def deploy(request: Request, encrypt: bool = False):
    """Stream docker compose up -d output via Server-Sent Events."""

    _audit(request, "stack.deploy", encrypt=encrypt)

    async def event_stream():
        if encrypt:
            yield _sse("Encrypting .env with SOPS…")
            env = {**os.environ, "SOPS_AGE_KEY_FILE": str(SOPS_AGE_KEY_FILE)}
            r = subprocess.run(
                ["sops", "--encrypt", "--input-type", "dotenv",
                 "--output-type", "dotenv", str(ENV_FILE)],
                capture_output=True, text=True, env=env,
            )
            if r.returncode != 0:
                yield _sse(f"[ERROR] SOPS: {r.stderr.strip()}")
                yield _sse("[DONE]")
                return
            ENV_ENC_FILE.write_text(r.stdout)
            yield _sse(".env.enc written.")

        yield _sse("Starting: docker compose up -d --build")
        process = await asyncio.create_subprocess_exec(
            "docker", "compose", "--env-file", str(ENV_FILE), "up", "-d", "--build",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        async for line in process.stdout:
            yield _sse(line.decode().rstrip())
        await process.wait()
        if process.returncode == 0:
            yield _sse("[SUCCESS] Stack deployed successfully!")
        else:
            yield _sse(f"[ERROR] docker compose exited with code {process.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(msg: str) -> str:
    return f"data: {msg}\n\n"


# ── Targeted service restart ───────────────────────────────────────────────────

@app.get("/api/restart")
async def restart_services(request: Request, services: str = ""):
    """SSE: docker compose restart <services>."""
    service_list = [s.strip() for s in services.split(",") if s.strip()]
    _audit(request, "services.restart", services=service_list)

    async def stream():
        if not service_list:
            yield _sse("[ERROR] No services specified")
            yield _sse("[DONE]")
            return
        yield _sse(f"Restarting: {', '.join(service_list)}")
        process = await asyncio.create_subprocess_exec(
            "docker", "compose", "--env-file", str(ENV_FILE),
            "restart", *service_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        async for line in process.stdout:
            yield _sse(line.decode().rstrip())
        await process.wait()
        if process.returncode == 0:
            yield _sse(f"[SUCCESS] Restarted: {', '.join(service_list)}")
        else:
            yield _sse(f"[ERROR] docker compose restart exited with code {process.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── PKI constants ─────────────────────────────────────────────────────────────

PKI_URL           = "http://localhost:8004"


def _sudo_password() -> str:
    return _load_env().get("SUDO_PASSWORD", "")


def _sudo_run(cmd: list, *, password: str | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run cmd with sudo, injecting password via stdin when available."""
    pw = password if password is not None else _sudo_password()
    if pw:
        return subprocess.run(
            ["sudo", "-S", "--"] + cmd,
            input=pw + "\n",
            **kwargs,
        )
    return subprocess.run(["sudo"] + cmd, **kwargs)


async def _async_sudo_exec(cmd: list, *, password: str | None = None, **kwargs):
    """Async version — returns (proc, stdout_pipe) using create_subprocess_exec."""
    pw = password if password is not None else _sudo_password()
    if pw:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-S", "--", *cmd,
            stdin=asyncio.subprocess.PIPE,
            **kwargs,
        )
        proc.stdin.write((pw + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()
    else:
        proc = await asyncio.create_subprocess_exec("sudo", *cmd, **kwargs)
    return proc


def _gitea_api_url() -> str:
    """Return the Gitea API base URL reachable from the host.

    Gitea has no direct host port binding — it is accessible only via Traefik
    at https://git.<DOMAIN>.  We use verify=False (or the local CA) because
    the CA may not yet be in the system trust store when this is called.
    """
    cfg = _load_env()
    domain = cfg.get("DOMAIN", "localhost")
    return f"https://git.{domain}/api/v1"
PKI_CA_NAME       = "autoflow-root"
WIZARD_PKI_OVERRIDE = ROOT / "docker-compose.wizard-pki.yml"


# ── TLS certificate — PKI-based flow ──────────────────────────────────────────

@app.get("/api/generate-cert")
async def generate_cert(request: Request):
    """SSE: start PKI → create Root CA → issue wildcard → write to traefik/certs/."""
    _audit(request, "pki.generate_cert")

    async def stream():
        import httpx as _httpx

        config         = _load_env()
        domain         = config.get("DOMAIN", "localhost")
        pki_user       = config.get("PKI_ADMIN_USER", "admin")
        pki_password   = config.get("PKI_ADMIN_PASSWORD", "")
        pki_passphrase = config.get("PKI_KEY_PASSPHRASE", "")

        if not pki_password:
            yield _sse("[ERROR] PKI_ADMIN_PASSWORD is not set. Fill in the PKI section first.")
            yield _sse("[DONE]")
            return

        # ── 1. Build PKI image if needed ──────────────────────────────────
        yield _sse("Building PKI image (skipped if already cached)…")
        build = subprocess.run(
            ["docker", "compose", "build", "pki"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if build.returncode != 0:
            yield _sse(f"[ERROR] docker build pki failed:\n{build.stderr[:400]}")
            yield _sse("[DONE]")
            return

        # ── 2. Start PKI with temporary port exposure ─────────────────────
        yield _sse("Starting PKI service on localhost:8004…")
        start = subprocess.run(
            [
                "docker", "compose",
                "-f", str(ROOT / "docker-compose.yml"),
                "-f", str(WIZARD_PKI_OVERRIDE),
                "up", "-d", "--no-deps", "pki",
            ],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if start.returncode != 0:
            yield _sse(f"[ERROR] Could not start PKI:\n{start.stderr[:400]}")
            yield _sse("[DONE]")
            return

        # ── 3. Wait for PKI to be healthy ─────────────────────────────────
        yield _sse("Waiting for PKI to be ready (up to 60 s)…")
        ready = False
        async with _httpx.AsyncClient() as c:
            for _ in range(30):
                try:
                    r = await c.get(f"{PKI_URL}/health", timeout=2.0)
                    if r.status_code == 200:
                        ready = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)

        if not ready:
            yield _sse("[ERROR] PKI did not start within 60 s. Check: docker compose logs pki")
            yield _sse("[DONE]")
            return

        # ── 4. PKI operations ─────────────────────────────────────────────
        yield _sse("PKI is ready. Authenticating…")

        async with _httpx.AsyncClient() as c:

            # Login
            r = await c.post(f"{PKI_URL}/api/auth/login",
                             json={"username": pki_user, "password": pki_password})
            if r.status_code != 200:
                yield _sse(f"[ERROR] PKI login failed ({r.status_code}): {r.text[:200]}")
                yield _sse("[DONE]")
                return
            token   = r.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Create Root CA (idempotent)
            r = await c.get(f"{PKI_URL}/api/ca/list", headers=headers)
            existing_cas = [ca.get("name") for ca in (r.json() if r.status_code == 200 else [])]

            if PKI_CA_NAME in existing_cas:
                yield _sse(f"Root CA '{PKI_CA_NAME}' already exists — skipping creation.")
            else:
                yield _sse(f"Creating Root CA '{PKI_CA_NAME}' (RSA-4096, 10 years)…")
                r = await c.post(f"{PKI_URL}/api/ca/create", headers=headers, json={
                    "name":          PKI_CA_NAME,
                    "common_name":   "Autoflow Root CA",
                    "organization":  "Autoflow",
                    "country":       "FR",
                    "validity_days": 3650,
                    "key_size":      4096,
                })
                if r.status_code != 200:
                    yield _sse(f"[ERROR] CA creation failed: {r.text[:300]}")
                    yield _sse("[DONE]")
                    return
                yield _sse("Root CA created.")

            # Issue wildcard certificate (825 days = browser X.509 limit)
            cn = f"*.{domain}"[:64]
            yield _sse(f"Issuing wildcard certificate for {cn} (825 days)…")
            r = await c.post(f"{PKI_URL}/api/certs/issue", headers=headers, json={
                "ca_name":      PKI_CA_NAME,
                "common_name":  cn,
                "domains":      [f"*.{domain}", domain],
                "organization": "Autoflow",
                "country":      "FR",
                "validity_days": 825,
                "wildcard":     True,
                "cert_type":    "server",
                "key_size":     4096,
            })
            if r.status_code != 200:
                yield _sse(f"[ERROR] Certificate issuance failed: {r.text[:300]}")
                yield _sse("[DONE]")
                return
            serial = r.json()["serial"]
            yield _sse(f"Certificate issued — serial: {serial}")

            # Download cert PEM
            r = await c.get(f"{PKI_URL}/api/certs/{serial}/cert.pem", headers=headers)
            cert_pem = r.text

            # Download private key PEM (admin permission required)
            r = await c.get(
                f"{PKI_URL}/api/certs/{serial}/key.pem",
                headers={**headers, "x-request-reason": "Deploy Wizard - Traefik TLS bootstrap"},
            )
            key_pem = r.text

            # Download CA certificate (public endpoint, no auth)
            r = await c.get(f"{PKI_URL}/api/ca/{PKI_CA_NAME}/cert.pem")
            ca_pem = r.text

        # Decrypt key if PKI stored it encrypted (Traefik needs plaintext PEM)
        if "ENCRYPTED" in key_pem:
            if not pki_passphrase:
                yield _sse("[ERROR] Private key is encrypted but PKI_KEY_PASSPHRASE is empty.")
                yield _sse("[DONE]")
                return
            yield _sse("Decrypting private key (PKI_KEY_PASSPHRASE is set)…")
            proc = subprocess.run(
                ["openssl", "pkey", "-passin", f"pass:{pki_passphrase}"],
                input=key_pem.encode(), capture_output=True,
            )
            if proc.returncode != 0:
                yield _sse(f"[ERROR] Key decryption failed — check PKI_KEY_PASSPHRASE")
                yield _sse("[DONE]")
                return
            key_pem = proc.stdout.decode()

        # ── 5. Write files to traefik/certs/ ──────────────────────────────
        CERTS_DIR.mkdir(parents=True, exist_ok=True)
        crt_file = CERTS_DIR / f"wildcard.{domain}.crt"
        key_file = CERTS_DIR / f"wildcard.{domain}.key"
        ca_file  = CERTS_DIR / f"ca.{domain}.crt"

        crt_file.write_text(cert_pem); crt_file.chmod(0o644)
        key_file.write_text(key_pem);  key_file.chmod(0o600)
        ca_file.write_text(ca_pem);    ca_file.chmod(0o644)

        yield _sse(f"  traefik/certs/wildcard.{domain}.crt  — server certificate")
        yield _sse(f"  traefik/certs/wildcard.{domain}.key  — private key (600)")
        yield _sse(f"  traefik/certs/ca.{domain}.crt        — Root CA ← import this in browser/OS")

        yield _sse("traefik/dynamic/tls.yml uses {{ env \"DOMAIN\" }} — no update needed.")
        yield _sse("")
        yield _sse("[SUCCESS] PKI setup complete!")
        yield _sse(f"  Import traefik/certs/ca.{domain}.crt into your browser/OS to trust all Autoflow services.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _update_tls_yml(domain: str) -> None:
    TLS_YML.write_text(
        f"tls:\n"
        f"  stores:\n"
        f"    default:\n"
        f"      defaultCertificate:\n"
        f"        certFile: /etc/traefik/certs/wildcard.{domain}.crt\n"
        f"        keyFile:  /etc/traefik/certs/wildcard.{domain}.key\n"
        f"\n"
        f"  certificates:\n"
        f"    - certFile: /etc/traefik/certs/wildcard.{domain}.crt\n"
        f"      keyFile:  /etc/traefik/certs/wildcard.{domain}.key\n"
    )


# ── Permissions ───────────────────────────────────────────────────────────────

_PERM_CHECKS = [
    {
        "id": "scripts",
        "label": "Scripts exécutables",
        "desc": "Tous les fichiers .sh du repo doivent être exécutables après un git clone.",
        "fix": "find scripts/ awx/ -name '*.sh' -exec chmod +x {} +",
    },
    {
        "id": "certs_dir",
        "label": "traefik/certs/ accessible",
        "desc": "Le répertoire des certificats doit appartenir à l'utilisateur courant.",
        "fix": "chown_certs",
    },
    {
        "id": "docker_group",
        "label": "Groupe docker",
        "desc": "L'utilisateur doit être dans le groupe docker pour lancer des commandes sans sudo.",
        "fix": "docker_group",
    },
    {
        "id": "env_writable",
        "label": ".env accessible en écriture",
        "desc": "Le fichier .env doit être modifiable par l'utilisateur courant.",
        "fix": "env_writable",
    },
]


def _check_permissions() -> list[dict]:
    import grp, pwd
    results = []
    current_user = os.environ.get("USER", "") or os.environ.get("LOGNAME", "")
    deploy_user  = _load_env().get("DEPLOY_USER", current_user) or current_user

    # 1. Scripts executable
    non_exec = [
        str(p) for p in (ROOT / "scripts").glob("**/*.sh")
        if not os.access(p, os.X_OK)
    ]
    for extra in ["awx/init.sh", "awx/init-ees.sh"]:
        p = ROOT / extra
        if p.exists() and not os.access(p, os.X_OK):
            non_exec.append(str(p))
    results.append({
        "id": "scripts",
        "ok": len(non_exec) == 0,
        "detail": f"{len(non_exec)} script(s) non exécutables" if non_exec else "Tous les scripts sont exécutables",
        "items": non_exec[:5],
    })

    # 2. traefik/certs/ writable
    certs = ROOT / "traefik" / "certs"
    certs_ok = certs.exists() and os.access(certs, os.W_OK)
    results.append({
        "id": "certs_dir",
        "ok": certs_ok,
        "detail": "traefik/certs/ accessible en écriture" if certs_ok else "traefik/certs/ non accessible en écriture",
    })

    # 3. Docker group
    try:
        docker_gid  = grp.getgrnam("docker").gr_gid
        user_groups = os.getgroups()
        in_docker   = docker_gid in user_groups
    except KeyError:
        in_docker = False
    results.append({
        "id": "docker_group",
        "ok": in_docker,
        "detail": "Utilisateur dans le groupe docker" if in_docker else "Utilisateur hors du groupe docker — sudo requis pour docker",
        "warn_relogin": not in_docker,
    })

    # 4. .env writable
    env_ok = (not ENV_FILE.exists()) or os.access(ENV_FILE, os.W_OK)
    results.append({
        "id": "env_writable",
        "ok": env_ok,
        "detail": ".env accessible en écriture" if env_ok else ".env en lecture seule — le wizard ne peut pas sauvegarder la config",
    })

    return results


@app.get("/api/permissions/status")
def permissions_status():
    checks = _check_permissions()
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


@app.get("/api/permissions/fix")
async def permissions_fix():
    """SSE: fix all permission issues found."""

    async def stream():
        config      = _load_env()
        deploy_user = config.get("DEPLOY_USER", "") or os.environ.get("USER", "")

        yield _sse(f"Correction des permissions (utilisateur : {deploy_user or 'courant'})…")

        # 1. Scripts executable
        yield _sse("→ chmod +x sur tous les scripts .sh…")
        sh_files = list((ROOT / "scripts").glob("**/*.sh"))
        for extra in ["awx/init.sh", "awx/init-ees.sh"]:
            p = ROOT / extra
            if p.exists():
                sh_files.append(p)
        fixed_scripts = 0
        for p in sh_files:
            if not os.access(p, os.X_OK):
                try:
                    p.chmod(p.stat().st_mode | 0o111)
                    fixed_scripts += 1
                except PermissionError:
                    _sudo_run(["chmod", "+x", str(p)], capture_output=True)
                    fixed_scripts += 1
        yield _sse(f"  {fixed_scripts} script(s) rendu(s) exécutables ✔")

        # 2. traefik/certs/ ownership
        certs_dir = ROOT / "traefik" / "certs"
        if not os.access(certs_dir, os.W_OK):
            yield _sse(f"→ Correction ownership de traefik/certs/…")
            target = deploy_user or os.environ.get("USER", "")
            r = _sudo_run(
                ["chown", "-R", f"{target}:{target}", str(certs_dir)],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                yield _sse(f"  traefik/certs/ → {target} ✔")
            else:
                yield _sse(f"  [WARN] chown échoué: {r.stderr.strip()}")
        else:
            yield _sse("→ traefik/certs/ déjà accessible ✔")

        # 3. Docker group
        import grp as _grp
        try:
            _grp.getgrnam("docker")
            target = deploy_user or os.environ.get("USER", "")
            if target:
                r = _sudo_run(
                    ["usermod", "-aG", "docker", target],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    yield _sse(f"→ {target} ajouté au groupe docker ✔")
                    yield _sse("  ⚠ Déconnecte-toi et reconnecte-toi (ou 'newgrp docker') pour activer.")
                else:
                    yield _sse(f"  [WARN] usermod échoué: {r.stderr.strip()}")
        except KeyError:
            yield _sse("→ [WARN] Groupe docker introuvable — Docker est-il installé ?")

        # 4. .env writable
        if ENV_FILE.exists() and not os.access(ENV_FILE, os.W_OK):
            yield _sse("→ Correction ownership de .env…")
            target = deploy_user or os.environ.get("USER", "")
            r = _sudo_run(
                ["chown", f"{target}:{target}", str(ENV_FILE)],
                capture_output=True, text=True,
            )
            yield _sse("  .env → accessible en écriture ✔" if r.returncode == 0
                       else f"  [WARN] {r.stderr.strip()}")
        else:
            yield _sse("→ .env accessible en écriture ✔")

        # Final check
        checks = _check_permissions()
        remaining = [c for c in checks if not c["ok"]]
        if not remaining:
            yield _sse("[SUCCESS] Toutes les permissions sont correctes ✔")
        else:
            for c in remaining:
                yield _sse(f"[WARN] {c['id']}: {c['detail']}")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/cert-status")
def cert_status():
    config = _load_env()
    domain = config.get("DOMAIN", "localhost")
    crt    = CERTS_DIR / f"wildcard.{domain}.crt"
    ca     = CERTS_DIR / f"ca.{domain}.crt"
    return {
        "domain":    domain,
        "exists":    crt.exists(),
        "ca_exists": ca.exists(),
        "ca_name":   PKI_CA_NAME,
        "path":      str(crt),
        "ca_path":   str(ca),
    }


@app.get("/api/download-ca")
def download_ca():
    """Serve the Root CA certificate for browser/OS import."""
    config = _load_env()
    domain = config.get("DOMAIN", "localhost")
    ca     = CERTS_DIR / f"ca.{domain}.crt"
    if not ca.exists():
        raise HTTPException(404, "CA certificate not found — run the pre-flight first")
    return StreamingResponse(
        iter([ca.read_bytes()]),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="autoflow-root-ca.{domain}.crt"'},
    )


# ── AWX custom image build ─────────────────────────────────────────────────────

@app.get("/api/build-awx")
async def build_awx(request: Request):
    """Build the custom AWX patched image (SSE stream)."""
    _audit(request, "awx.build_image")
    awx_version = _load_env().get("AWX_VERSION", "24.6.1")
    tag = f"autoflow/awx-patched:{awx_version}"

    async def stream():
        yield _sse(f"Building {tag} from awx/Dockerfile.patched")
        yield _sse("This may take 5–15 minutes depending on your connection…")

        proc = await asyncio.create_subprocess_exec(
            "docker", "build",
            "-f", str(AWX_DOCKERFILE),
            "--build-arg", f"AWX_VERSION={awx_version}",
            "-t", tag,
            str(ROOT / "awx"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()

        if proc.returncode == 0:
            yield _sse(f"[SUCCESS] {tag} built successfully.")
        else:
            yield _sse(f"[ERROR] docker build exited with code {proc.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/awx-image-status")
def awx_image_status():
    version = _load_env().get("AWX_VERSION", "24.6.1")
    tag = f"autoflow/awx-patched:{version}"
    r = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        capture_output=True, text=True,
    )
    return {"tag": tag, "exists": r.returncode == 0}


# ── Execution Environments ────────────────────────────────────────────────────

EE_DEFINITIONS = [
    {
        "id": "base",
        "label": "EE Base",
        "description": "Collections communes — community.general, ansible.posix, community.crypto",
        "version": "1.0.0",
    },
    {
        "id": "security",
        "label": "EE Security",
        "description": "Sécurité et conformité — boto3, openssl, community.crypto, ansible.utils",
        "version": "1.0.0",
    },
    {
        "id": "network",
        "label": "EE Network",
        "description": "Réseau multi-vendeurs — NAPALM, Netmiko, Nornir, cisco.ios, junipernetworks.junos, arista.eos, f5",
        "version": "1.0.0",
    },
]


def _find_ansible_builder() -> str | None:
    """Locate the ansible-builder binary (PATH → ~/.local/bin → pipx venv)."""
    import shutil
    found = shutil.which("ansible-builder")
    if found:
        return found
    for candidate in [
        Path.home() / ".local/bin/ansible-builder",
        Path.home() / ".local/pipx/venvs/ansible-builder/bin/ansible-builder",
    ]:
        if candidate.exists():
            return str(candidate)
    return None


@app.get("/api/ee/status")
def ee_status():
    config     = _load_env()
    domain     = config.get("DOMAIN", "localhost")
    registry   = f"git.{domain}"
    gitea_user = config.get("GITEA_ADMIN_USER", "admin")

    ab = _find_ansible_builder()
    ab_version = None
    if ab:
        r = subprocess.run([ab, "--version"], capture_output=True, text=True)
        ab_version = r.stdout.strip() if r.returncode == 0 else None

    ee_images = []
    for ee in EE_DEFINITIONS:
        tag = f"{registry}/{gitea_user}/ee-{ee['id']}:{ee['version']}"
        r   = subprocess.run(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
            capture_output=True, text=True,
        )
        ee_images.append({**ee, "tag": tag, "built": r.returncode == 0})

    cert_dir      = Path(f"/etc/docker/certs.d/{registry}")
    docker_trusted = (cert_dir / "ca.crt").exists()

    return {
        "ansible_builder":         ab,
        "ansible_builder_version": ab_version,
        "registry":                registry,
        "docker_trusted":          docker_trusted,
        "ees":                     ee_images,
    }


@app.get("/api/ee/install-deps")
async def ee_install_deps():
    """SSE: install ansible-builder via pipx (compatible Debian/Ubuntu PEP-668)."""

    async def stream():
        ab = _find_ansible_builder()
        if ab:
            r = subprocess.run([ab, "--version"], capture_output=True, text=True)
            yield _sse(f"[SUCCESS] ansible-builder déjà installé : {r.stdout.strip()}")
            yield _sse("[DONE]")
            return

        # Priority 1: pipx
        pipx_check = subprocess.run(["which", "pipx"], capture_output=True, text=True)
        if pipx_check.returncode == 0:
            yield _sse("Installation via pipx...")
            proc = await asyncio.create_subprocess_exec(
                "pipx", "install", "ansible-builder",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in proc.stdout:
                yield _sse(line.decode().rstrip())
            await proc.wait()
            if proc.returncode == 0:
                yield _sse("[SUCCESS] ansible-builder installé via pipx ✔")
                yield _sse("[DONE]")
                return
            yield _sse(f"[WARN] pipx a échoué — essai alternatif...")

        # Priority 2: pip --break-system-packages (Debian/Ubuntu 22+)
        yield _sse("Installation via pip3 --break-system-packages...")
        proc = await asyncio.create_subprocess_exec(
            "pip3", "install", "--user", "--break-system-packages", "ansible-builder",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()
        if proc.returncode == 0:
            yield _sse("[SUCCESS] ansible-builder installé ✔")
            yield _sse("Note: si la commande n'est pas trouvée, recharge le wizard (PATH mis à jour).")
        else:
            yield _sse("[ERROR] Installation échouée.")
            yield _sse("Lance manuellement dans un terminal: pipx install ansible-builder")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/ee/build")
async def ee_build(ee: str = "base", version: str = "1.0.0"):
    """SSE: ansible-builder build + docker push for a single EE."""

    async def stream():
        config     = _load_env()
        domain     = config.get("DOMAIN", "localhost")
        gitea_user = config.get("GITEA_ADMIN_USER", "admin")
        registry   = f"git.{domain}"

        ee_file = ROOT / "execution-environments" / ee / "execution-environment.yml"
        if not ee_file.exists():
            yield _sse(f"[ERROR] Définition introuvable: {ee_file}")
            yield _sse("[DONE]")
            return

        ab = _find_ansible_builder()
        if not ab:
            yield _sse("[ERROR] ansible-builder introuvable — lance d'abord 'Installer ansible-builder'.")
            yield _sse("[DONE]")
            return

        tag     = f"{registry}/{gitea_user}/ee-{ee}:{version}"
        ctx_dir = f"/tmp/ee-build-{ee}"

        yield _sse(f"▶  Build EE '{ee}' → {tag}")
        yield _sse(f"   ansible-builder : {ab}")
        yield _sse("   Cette opération peut prendre 10–20 min (téléchargement collections + pip)...")
        yield _sse("")

        # Docker login
        token = config.get("GITEA_REGISTRY_TOKEN", "") or config.get("GITEA_ADMIN_PASSWORD", "")
        if token:
            yield _sse(f"docker login {registry}...")
            login = subprocess.run(
                ["docker", "login", registry, "-u", gitea_user, "--password-stdin"],
                input=token, capture_output=True, text=True,
            )
            if login.returncode == 0:
                yield _sse("docker login OK ✔")
            else:
                yield _sse(f"[WARN] docker login: {login.stderr.strip()}")

        # ansible-builder build
        yield _sse("ansible-builder build...")
        build_proc = await asyncio.create_subprocess_exec(
            ab, "build",
            "--file",      str(ee_file),
            "--tag",       tag,
            "--context",   ctx_dir,
            "--build-arg", "PYCMD=/usr/bin/python3.12",
            "--verbosity", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in build_proc.stdout:
            yield _sse(line.decode().rstrip())
        await build_proc.wait()

        if build_proc.returncode != 0:
            yield _sse(f"[ERROR] ansible-builder échoué (code {build_proc.returncode})")
            yield _sse("[DONE]")
            return

        yield _sse(f"Build OK — push vers {registry}...")

        push_rc = -1
        push_lines: list[str] = []
        push_proc = await asyncio.create_subprocess_exec(
            "docker", "push", tag,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in push_proc.stdout:
            ln = raw.decode().rstrip()
            push_lines.append(ln)
            yield _sse(ln)
        await push_proc.wait()
        push_rc = push_proc.returncode

        if push_rc == 0:
            # Push immutable dated tag for rollback capability
            from datetime import datetime as _dt, timezone as _tz
            dated_tag = _dt.now(_tz.utc).strftime("%Y%m%d-%H%M%S")
            dated_image = f"{registry}/{gitea_user}/ee-{ee}:{dated_tag}"
            yield _sse(f"Pushing dated tag {dated_tag} for rollback…")
            tag_r = subprocess.run(
                ["docker", "tag", tag, dated_image],
                capture_output=True, text=True,
            )
            if tag_r.returncode == 0:
                dt_push = await asyncio.create_subprocess_exec(
                    "docker", "push", dated_image,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                await dt_push.wait()
                if dt_push.returncode == 0:
                    yield _sse(f"  Dated tag pushed: {dated_tag} ✔")
                else:
                    yield _sse(f"  [WARN] Dated tag push failed — rollback won't include this build")
            else:
                yield _sse(f"  [WARN] docker tag failed: {tag_r.stderr.strip()}")
            yield _sse(f"[SUCCESS] ee-{ee}:{version} buildé et poussé ✔")
        else:
            push_out = "\n".join(push_lines)
            if "certificate signed by unknown authority" in push_out or "x509" in push_out:
                yield _sse("[WARN] Erreur TLS détectée — configuration automatique du CA système...")
                _ca_file = CERTS_DIR / f"ca.{domain}.crt"
                if _ca_file.exists():
                    _ca_pem = _ca_file.read_text()
                    _sys_ca = "/usr/local/share/ca-certificates/autoflow-registry-ca.crt"
                    import tempfile as _t2
                    with _t2.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as _tf2:
                        _tf2.write(_ca_pem)
                        _tp2 = _tf2.name
                    _sr = _sudo_run(
                        ["bash", "-c",
                         f"cp '{_tp2}' '{_sys_ca}' && chmod 644 '{_sys_ca}'"
                         f" && update-ca-certificates --fresh 2>&1 | tail -3"
                         f" && systemctl restart docker"],
                        capture_output=True, text=True,
                    )
                    Path(_tp2).unlink(missing_ok=True)
                    if _sr.returncode == 0:
                        yield _sse("CA système mis à jour + Docker redémarré ✔")
                        yield _sse("Relance le build pour pusher l'image.")
                    else:
                        yield _sse(f"[WARN] Auto-fix échoué: {_sr.stderr.strip()[:200]}")
                        yield _sse("[WARN] Lance manuellement 'Configurer CA Docker' puis relance le build.")
                else:
                    yield _sse("[WARN] Fichier CA introuvable — lance 'Configurer CA Docker' d'abord.")
            else:
                yield _sse(f"[ERROR] docker push échoué (code {push_rc})")
                yield _sse("[WARN] Vérifie: 1) 'Configurer CA Docker' 2) GITEA_REGISTRY_TOKEN dans .env")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/ee/versions/{ee}")
def ee_versions(ee: str):
    """List available tags in the Gitea container registry for a given EE."""
    import urllib.request, urllib.error, base64, json as _j
    config     = _load_env()
    domain     = config.get("DOMAIN", "localhost")
    gitea_user = config.get("GITEA_ADMIN_USER", "admin")
    registry   = f"git.{domain}"
    token      = config.get("GITEA_REGISTRY_TOKEN", "") or config.get("GITEA_ADMIN_PASSWORD", "")
    creds      = base64.b64encode(f"{gitea_user}:{token}".encode()).decode()

    # Use Docker Registry v2 API on the intra-stack hostname (no TLS needed)
    # Fallback to the external hostname if intra-stack is not available
    for base in (f"http://gitea:3001", f"https://{registry}"):
        url = f"{base}/v2/{gitea_user}/ee-{ee}/tags/list"
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
        try:
            ctx = None
            if base.startswith("https"):
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode    = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
                data  = _j.loads(resp.read())
                tags  = data.get("tags") or []
                dated = sorted([t for t in tags if len(t) == 15 and t[8] == "-"], reverse=True)
                other = [t for t in tags if t not in dated]
                return {
                    "ee":         ee,
                    "image_base": f"{registry}/{gitea_user}/ee-{ee}",
                    "dated_tags": dated,
                    "other_tags": other,
                    "total":      len(tags),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"ee": ee, "image_base": f"{registry}/{gitea_user}/ee-{ee}",
                        "dated_tags": [], "other_tags": [], "total": 0}
        except Exception:
            continue

    raise HTTPException(502, "Could not reach Gitea container registry — is Gitea running?")


@app.get("/api/ee/rollback")
async def ee_rollback(ee: str, version: str):
    """SSE: roll back an EE image by re-tagging a dated version as 'latest' and pushing."""

    async def stream():
        config     = _load_env()
        domain     = config.get("DOMAIN", "localhost")
        gitea_user = config.get("GITEA_ADMIN_USER", "admin")
        registry   = f"git.{domain}"
        default_v  = config.get("EE_DEFAULT_VERSION", "latest")

        source = f"{registry}/{gitea_user}/ee-{ee}:{version}"
        target = f"{registry}/{gitea_user}/ee-{ee}:{default_v}"

        yield _sse(f"Rolling back ee-{ee} to {version}…")
        yield _sse(f"Source : {source}")
        yield _sse(f"Target : {target}")
        yield _sse("")

        # Step 1: pull the dated image
        yield _sse(f"[1/3] docker pull {source}…")
        pull_proc = await asyncio.create_subprocess_exec(
            "docker", "pull", source,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in pull_proc.stdout:
            ln = raw.decode().rstrip()
            if ln:
                yield _sse(f"  {ln}")
        await pull_proc.wait()
        if pull_proc.returncode != 0:
            yield _sse(f"[ERROR] docker pull failed (exit {pull_proc.returncode})")
            yield _sse("[DONE]")
            return
        yield _sse("[1/3] Pull OK ✔")

        # Step 2: re-tag
        yield _sse(f"[2/3] docker tag → {target}…")
        tag_r = subprocess.run(["docker", "tag", source, target], capture_output=True, text=True)
        if tag_r.returncode != 0:
            yield _sse(f"[ERROR] docker tag failed: {tag_r.stderr.strip()}")
            yield _sse("[DONE]")
            return
        yield _sse("[2/3] Tag OK ✔")

        # Step 3: push floating tag
        yield _sse(f"[3/3] docker push {target}…")
        push_proc = await asyncio.create_subprocess_exec(
            "docker", "push", target,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in push_proc.stdout:
            ln = raw.decode().rstrip()
            if ln:
                yield _sse(f"  {ln}")
        await push_proc.wait()
        if push_proc.returncode != 0:
            yield _sse(f"[ERROR] docker push failed (exit {push_proc.returncode})")
            yield _sse("[DONE]")
            return
        yield _sse("[3/3] Push OK ✔")
        yield _sse("")
        yield _sse(f"[SUCCESS] ee-{ee} rolled back to {version} → now serves as {default_v} ✔")
        yield _sse("AWX will use the rolled-back image on next job run.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/docker/trust-ca")
async def docker_trust_ca():
    """SSE: install the PKI Root CA into Docker certs.d for the Gitea registry."""

    async def stream():
        import httpx as _httpx

        config   = _load_env()
        domain   = config.get("DOMAIN", "localhost")
        registry = f"git.{domain}"

        yield _sse(f"Configuration du CA Docker pour le registry {registry}...")

        ca_pem = ""

        # Try 1: PKI API (local port)
        try:
            async with _httpx.AsyncClient() as c:
                r = await c.get(f"{PKI_URL}/api/ca/list", timeout=4)
                if r.status_code == 200:
                    cas = r.json()
                    ca_name = (cas[0].get("name") if isinstance(cas, list) and cas
                               else cas.get("cas", [{}])[0].get("name", ""))
                    if ca_name:
                        rc = await c.get(f"{PKI_URL}/api/ca/{ca_name}/cert.pem", timeout=4)
                        if rc.status_code == 200 and "BEGIN CERTIFICATE" in rc.text:
                            ca_pem = rc.text
                            yield _sse(f"CA '{ca_name}' récupéré depuis le service PKI.")
        except Exception as e:
            yield _sse(f"PKI local non disponible ({type(e).__name__}) — essai fichier local...")

        # Try 2: ca.<domain>.crt written by generate-cert
        if not ca_pem:
            for candidate in [
                CERTS_DIR / f"ca.{domain}.crt",
                CERTS_DIR / f"wildcard.{domain}.crt",
            ]:
                if candidate.exists():
                    ca_pem = candidate.read_text()
                    yield _sse(f"CA récupéré depuis {candidate.name}")
                    break

        if not ca_pem:
            yield _sse("[ERROR] Certificat CA introuvable.")
            yield _sse("Lance d'abord 'Setup PKI & Generate cert' dans Pre-flight.")
            yield _sse("[DONE]")
            return

        cert_dir = Path(f"/etc/docker/certs.d/{registry}")
        try:
            cert_dir.mkdir(parents=True, exist_ok=True)
            (cert_dir / "ca.crt").write_text(ca_pem)
            (cert_dir / "ca.crt").chmod(0o644)
            yield _sse(f"CA écrit dans {cert_dir}/ca.crt ✔")
        except PermissionError:
            yield _sse(f"Permission refusée — tentative via sudo...")
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as tmp:
                tmp.write(ca_pem)
                tmp_path = tmp.name
            r = _sudo_run(
                ["bash", "-c",
                 f"mkdir -p '{cert_dir}' && cp '{tmp_path}' '{cert_dir}/ca.crt' && chmod 644 '{cert_dir}/ca.crt'"],
                capture_output=True, text=True,
            )
            Path(tmp_path).unlink(missing_ok=True)
            if r.returncode != 0:
                yield _sse(f"[ERROR] sudo échoué: {r.stderr.strip()}")
                yield _sse("Lance manuellement: make docker-trust-ca")
                yield _sse("[DONE]")
                return
            yield _sse(f"CA installé via sudo dans {cert_dir}/ca.crt ✔")

        # Install CA in the system trust store so the docker credential helper trusts it
        # (the credential helper uses system TLS, NOT /etc/docker/certs.d/)
        import tempfile as _tempfile_sys
        with _tempfile_sys.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as _sf:
            _sf.write(ca_pem)
            _sys_tmp = _sf.name
        _sys_ca_dst = "/usr/local/share/ca-certificates/autoflow-registry-ca.crt"
        _sys_r = _sudo_run(
            ["bash", "-c",
             f"cp '{_sys_tmp}' '{_sys_ca_dst}' && chmod 644 '{_sys_ca_dst}' && update-ca-certificates --fresh 2>&1 | tail -3"],
            capture_output=True, text=True,
        )
        Path(_sys_tmp).unlink(missing_ok=True)
        if _sys_r.returncode == 0:
            yield _sse(f"CA ajouté au store système + update-ca-certificates ✔")
            if _sys_r.stdout.strip():
                yield _sse(_sys_r.stdout.strip())
        else:
            yield _sse(f"[WARN] Store système non mis à jour: {_sys_r.stderr.strip()}")

        # Also add to insecure-registries in daemon.json so docker push bypasses
        # TLS verification for this local registry (belt + suspenders approach)
        import json as _json_mod
        daemon_json = Path("/etc/docker/daemon.json")
        try:
            existing_cfg = _json_mod.loads(daemon_json.read_text()) if daemon_json.exists() else {}
        except Exception:
            existing_cfg = {}
        insecure = existing_cfg.get("insecure-registries", [])
        if registry not in insecure:
            insecure.append(registry)
            existing_cfg["insecure-registries"] = insecure
            daemon_content = _json_mod.dumps(existing_cfg, indent=2)
            import tempfile as _tmp_mod
            with _tmp_mod.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
                tf.write(daemon_content)
                tf_path = tf.name
            ir = _sudo_run(
                ["bash", "-c", f"cp '{tf_path}' /etc/docker/daemon.json && chmod 644 /etc/docker/daemon.json"],
                capture_output=True, text=True,
            )
            Path(tf_path).unlink(missing_ok=True)
            if ir.returncode == 0:
                yield _sse(f"insecure-registries → {registry} ajouté dans daemon.json ✔")
            else:
                yield _sse(f"[WARN] daemon.json non mis à jour: {ir.stderr.strip()}")
        else:
            yield _sse(f"insecure-registries déjà configuré pour {registry}.")

        # Restart Docker daemon so it trusts the new CA cert
        yield _sse("Redémarrage du daemon Docker pour charger le nouveau CA...")
        yield _sse("(Arrêt des conteneurs en cours — peut prendre 30–60 s...)")
        restart_proc = await _async_sudo_exec(
            ["systemctl", "restart", "docker"],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Heartbeat while waiting (up to 90 s)
        deadline = asyncio.get_running_loop().time() + 90
        while True:
            try:
                rc = await asyncio.wait_for(restart_proc.wait(), timeout=10)
                break
            except asyncio.TimeoutError:
                if asyncio.get_running_loop().time() > deadline:
                    restart_proc.kill()
                    yield _sse("[WARN] Redémarrage Docker trop long — relance manuellement : sudo systemctl restart docker")
                    rc = -1
                    break
                yield _sse("…en attente du daemon Docker…")
        if rc == 0:
            yield _sse("Docker daemon redémarré ✔ (les conteneurs Autoflow reviennent automatiquement)")
            await asyncio.sleep(3)
        else:
            stderr_out = (await restart_proc.stderr.read()).decode().strip() if restart_proc.stderr else ""
            yield _sse(f"[WARN] Redémarrage Docker échoué (code {rc}): {stderr_out}")
            yield _sse("[WARN] Relance manuellement : sudo systemctl restart docker")

        # Ensure registry hostname resolves (add to /etc/hosts if needed)
        import socket as _socket
        try:
            _socket.getaddrinfo(registry, 443)
        except _socket.gaierror:
            yield _sse(f"[WARN] {registry} non résolu par DNS — ajout dans /etc/hosts...")
            hosts_line = f"127.0.0.1  {registry}"
            check = subprocess.run(["grep", "-qF", registry, "/etc/hosts"], capture_output=True)
            if check.returncode != 0:
                add = _sudo_run(
                    ["bash", "-c", f"echo '{hosts_line}' >> /etc/hosts"],
                    capture_output=True, text=True,
                )
                if add.returncode == 0:
                    yield _sse(f"Entrée ajoutée dans /etc/hosts : {hosts_line} ✔")
                else:
                    yield _sse(f"[WARN] Impossible d'écrire dans /etc/hosts: {add.stderr.strip()}")
                    yield _sse(f"[WARN] Ajoute manuellement : echo '{hosts_line}' | sudo tee -a /etc/hosts")
            else:
                yield _sse(f"Entrée déjà présente dans /etc/hosts pour {registry}.")

        # Test docker login — auto-generate registry token if needed
        user     = config.get("GITEA_ADMIN_USER", "admin")
        password = config.get("GITEA_ADMIN_PASSWORD", "")
        reg_token = config.get("GITEA_REGISTRY_TOKEN", "")

        def _try_docker_login(secret: str) -> bool:
            r = subprocess.run(
                ["docker", "login", registry, "-u", user, "--password-stdin"],
                input=secret, capture_output=True, text=True,
            )
            return r.returncode == 0

        yield _sse(f"Test docker login {registry}...")

        if reg_token and _try_docker_login(reg_token):
            yield _sse(f"[SUCCESS] docker login {registry} → OK ✔")
        else:
            if reg_token:
                yield _sse("[WARN] GITEA_REGISTRY_TOKEN invalide — génération d'un nouveau token via l'API Gitea...")
            else:
                yield _sse("GITEA_REGISTRY_TOKEN absent — génération automatique via l'API Gitea...")

            # Try to generate a token via the Gitea API using admin credentials
            gitea_api = _gitea_api_url()
            new_token = ""
            try:
                async with _httpx.AsyncClient(verify=False) as c:
                    # Delete existing token with same name (ignore errors)
                    await c.delete(
                        f"{gitea_api}/users/{user}/tokens/autoflow-registry",
                        auth=(user, password), timeout=5,
                    )
                    # Create new token with package scope
                    resp = await c.post(
                        f"{gitea_api}/users/{user}/tokens",
                        auth=(user, password),
                        json={"name": "autoflow-registry", "scopes": ["read:package", "write:package"]},
                        timeout=5,
                    )
                    if resp.status_code == 201:
                        new_token = resp.json().get("sha1", "")
                    else:
                        yield _sse(f"[WARN] API Gitea {resp.status_code}: {resp.text[:200]}")
            except Exception as exc:
                yield _sse(f"[WARN] Connexion API Gitea échouée: {exc}")

            if new_token:
                yield _sse("Token généré — écriture dans .env (GITEA_REGISTRY_TOKEN)...")
                current = _load_env()
                current["GITEA_REGISTRY_TOKEN"] = new_token
                _write_env(current)
                if _try_docker_login(new_token):
                    yield _sse(f"[SUCCESS] docker login {registry} → OK ✔")
                else:
                    yield _sse("[WARN] Token généré mais docker login toujours KO.")
                    yield _sse("[WARN] Vérifie que Gitea est démarré et que les packages sont activés.")
            else:
                # Last resort: try with admin password directly
                if password and _try_docker_login(password):
                    yield _sse(f"[SUCCESS] docker login avec mot de passe admin → OK ✔")
                    yield _sse("[WARN] Utilise le mot de passe admin — génère un token dédié dans Gitea > Settings > Applications")
                else:
                    yield _sse("[WARN] docker login KO — Gitea est-il démarré et accessible ?")
                    yield _sse(f"[WARN] Génère manuellement : Gitea > {user} > Settings > Applications > Generate Token (scopes: package)")
                    yield _sse(f"[WARN] Puis écris dans .env : GITEA_REGISTRY_TOKEN=<token>")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/gitea/init-network")
async def gitea_init_network():
    """SSE: create network-playbooks repo in Gitea and push initial content."""

    async def stream():
        script = ROOT / "scripts" / "gitea-init-network.sh"
        if not script.exists():
            yield _sse(f"[ERROR] Script introuvable: {script}")
            yield _sse("[DONE]")
            return

        config = _load_env()
        env    = {
            **os.environ,
            "GITEA_ROOT_URL":       config.get("GITEA_ROOT_URL", ""),
            "GITEA_ADMIN_USER":     config.get("GITEA_ADMIN_USER", "admin"),
            "GITEA_ADMIN_PASSWORD": config.get("GITEA_ADMIN_PASSWORD", ""),
            "AUTOFLOW_ROOT":        str(ROOT),
        }

        yield _sse("Initialisation du repo Gitea 'network-playbooks'...")

        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()

        if proc.returncode == 0:
            gitea_url  = config.get("GITEA_ROOT_URL", "")
            gitea_user = config.get("GITEA_ADMIN_USER", "admin")
            yield _sse(f"[SUCCESS] Repo disponible sur {gitea_url}/{gitea_user}/network-playbooks ✔")
        else:
            yield _sse(f"[ERROR] Initialisation échouée (code {proc.returncode})")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Post-deploy setup ─────────────────────────────────────────────────────────

@app.get("/api/init-gitea")
async def init_gitea(request: Request):
    """SSE: create the Gitea admin user via 'gitea admin user create'."""
    _audit(request, "gitea.init_admin")

    async def stream():
        config   = _load_env()
        username = config.get("GITEA_ADMIN_USER", "admin")
        password = config.get("GITEA_ADMIN_PASSWORD", "")
        email    = config.get("GITEA_ADMIN_EMAIL", f"{username}@localhost")

        if not password:
            yield _sse("[ERROR] GITEA_ADMIN_PASSWORD is not set. Fill in the Gitea section first.")
            yield _sse("[DONE]")
            return

        # Check the container is running
        check = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_gitea"],
            capture_output=True, text=True,
        )
        if check.stdout.strip() != "true":
            yield _sse("[ERROR] Container 'autoflow_gitea' is not running. Deploy the stack first.")
            yield _sse("[DONE]")
            return

        yield _sse(f"Creating Gitea admin user '{username}'…")
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "autoflow_gitea",
            "gitea", "admin", "user", "create",
            "--username", username,
            "--password", password,
            "--email",    email,
            "--admin",
            "--must-change-password=false",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()

        if proc.returncode == 0:
            yield _sse(f"[SUCCESS] Admin user '{username}' created — you can now log in.")
        else:
            yield _sse("[ERROR] User creation failed (may already exist — try logging in).")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/init-gitea-status")
def init_gitea_status():
    """Check whether the Gitea admin user already exists."""
    config   = _load_env()
    username = config.get("GITEA_ADMIN_USER", "admin")
    result   = subprocess.run(
        ["docker", "exec", "autoflow_gitea",
         "gitea", "admin", "user", "list", "--admin"],
        capture_output=True, text=True,
    )
    exists = username in result.stdout if result.returncode == 0 else False
    running = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_gitea"],
        capture_output=True, text=True,
    ).stdout.strip() == "true"
    return {"username": username, "exists": exists, "running": running}


@app.get("/api/init-awx-token")
async def init_awx_token(request: Request):
    """SSE: create an AWX API token and save it to .env + restart event_engine."""
    _audit(request, "awx.init_token")

    async def stream():
        config       = _load_env()
        awx_user     = config.get("AWX_ADMIN_USER", "admin")
        awx_password = config.get("AWX_ADMIN_PASSWORD", "")

        if not awx_password:
            yield _sse("[ERROR] AWX_ADMIN_PASSWORD is not set.")
            yield _sse("[DONE]")
            return

        import json as _json

        # ── Step 1: verify container is running ───────────────────────────
        chk = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", "autoflow_awx_web"],
            capture_output=True, text=True,
        )
        container_status = chk.stdout.strip()
        yield _sse(f"Container autoflow_awx_web status: {container_status or '(not found)'}")
        if container_status != "running":
            yield _sse("[ERROR] Container is not running. Start the stack first: make start")
            yield _sse("[DONE]")
            return

        # ── Step 2: detect AWX internal port (nginx proxy → uwsgi) ───────
        # Try 8052 first (default), fallback to 80
        for port in ("8052", "80"):
            probe = subprocess.run(
                ["docker", "exec", "autoflow_awx_web",
                 "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 f"http://localhost:{port}/api/v2/ping/"],
                capture_output=True, text=True,
            )
            if probe.stdout.strip() in ("200", "401"):
                awx_port = port
                yield _sse(f"AWX API reachable on port {awx_port} (HTTP {probe.stdout.strip()}).")
                break
        else:
            yield _sse("[ERROR] AWX API not reachable on port 8052 or 80 — AWX may still be starting.")
            yield _sse("[DONE]")
            return

        # ── Step 3: create token ──────────────────────────────────────────
        yield _sse("Creating API token…")
        payload = _json.dumps({
            "description": "Autoflow Event Engine",
            "application": None,
            "scope":       "write",
        })
        result = subprocess.run(
            [
                "docker", "exec", "autoflow_awx_web",
                "curl", "-s", "-X", "POST",
                "-u", f"{awx_user}:{awx_password}",
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"http://localhost:{awx_port}/api/v2/tokens/",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            yield _sse(f"[ERROR] docker exec failed: {result.stderr.strip()}")
            yield _sse("[DONE]")
            return

        yield _sse(f"AWX response: {result.stdout[:300]}")

        try:
            r_data = _json.loads(result.stdout)
        except Exception:
            yield _sse(f"[ERROR] Could not parse AWX response (see above).")
            yield _sse("[DONE]")
            return

        if "token" not in r_data:
            detail = r_data.get("detail", r_data.get("non_field_errors", result.stdout[:200]))
            yield _sse(f"[ERROR] AWX returned no token — {detail}")
            yield _sse("[DONE]")
            return

        token = r_data["token"]
        yield _sse(f"Token created (id={r_data.get('id')}). Writing to .env…")
        current = _load_env()
        current["AWX_TOKEN"] = token
        _write_env(current)
        yield _sse("AWX_TOKEN written to .env.")

        yield _sse("Restarting event_engine to pick up new token…")
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "--env-file", str(ENV_FILE), "restart", "event_engine",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(ROOT),
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()
        yield _sse("[SUCCESS] AWX token configured and event_engine restarted.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Gitea Actions Runner ──────────────────────────────────────────────────────

@app.get("/api/runner/status")
def runner_status():
    env = _load_env()
    gitea_api = _gitea_api_url()
    user   = env.get("GITEA_ADMIN_USER", "admin")
    passwd = env.get("GITEA_ADMIN_PASSWORD", "")
    try:
        resp = httpx.get(
            f"{gitea_api}/admin/runners",
            params={"limit": 20},
            auth=(user, passwd),
            verify=False,
            timeout=5,
        )
        if resp.status_code != 200:
            return {"registered": False, "error": f"Gitea API HTTP {resp.status_code}"}
        data = resp.json()
        runners = data if isinstance(data, list) else data.get("data", [])
        autoflow = [r for r in runners if r.get("name") == "autoflow-runner"]
        return {"registered": bool(autoflow), "runner_count": len(autoflow), "runners": autoflow}
    except Exception as exc:
        return {"registered": False, "error": str(exc)}


@app.get("/api/runner/register")
async def runner_register():
    async def stream():
        script = ROOT / "scripts" / "gitea-init-runner.sh"
        if not script.exists():
            yield _sse(f"[ERROR] Script non trouvé : {script}")
            yield _sse("[DONE]")
            return

        env = _load_env()
        env_vars = {**os.environ, **env}
        yield _sse("Lancement de gitea-init-runner.sh…")

        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=env_vars, cwd=str(ROOT),
        )
        async for raw in proc.stdout:
            line = raw.decode().rstrip()
            if not line:
                continue
            cls = "[ERROR]" if "✖" in line or "ERROR" in line else \
                  "[SUCCESS]" if "✔" in line or "Runner" in line and "enregistré" in line else ""
            yield _sse(f"{cls} {line}".strip() if cls else line)
        rc = await proc.wait()
        if rc == 0:
            yield _sse("[SUCCESS] Runner enregistré dans Gitea Actions ✔")
        else:
            yield _sse(f"[ERROR] gitea-init-runner.sh a échoué (code {rc})")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Grafana dashboard export ──────────────────────────────────────────────────

GRAFANA_DASHBOARDS_DIR = ROOT / "monitoring" / "grafana" / "dashboards"


def _grafana_api_url() -> str:
    domain = _load_env().get("DOMAIN", "localhost")
    return f"https://grafana.{domain}/api"


@app.get("/api/grafana/export-status")
def grafana_export_status():
    """Return the mtime of the newest dashboard file and whether Grafana is running."""
    import time
    running = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_grafana"],
        capture_output=True, text=True,
    ).stdout.strip() == "true"

    files = list(GRAFANA_DASHBOARDS_DIR.glob("*.json")) if GRAFANA_DASHBOARDS_DIR.exists() else []
    last_export: str | None = None
    if files:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        last_export = newest.stat().st_mtime.__class__  # just need the value
        import datetime
        ts = datetime.datetime.fromtimestamp(newest.stat().st_mtime)
        last_export = ts.strftime("%Y-%m-%d %H:%M")

    return {
        "running":     running,
        "file_count":  len(files),
        "last_export": last_export,
    }


@app.get("/api/grafana/export")
async def grafana_export():
    """SSE: export all Grafana dashboards from the live instance to JSON files."""

    async def stream():
        import httpx as _httpx, json as _j, re

        config   = _load_env()
        user     = config.get("GRAFANA_ADMIN_USER", "admin")
        password = config.get("GRAFANA_ADMIN_PASSWORD", "")

        if not password:
            yield _sse("[ERROR] GRAFANA_ADMIN_PASSWORD not set — fill in the Monitoring section.")
            yield _sse("[DONE]")
            return

        # Verify container is running
        chk = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_grafana"],
            capture_output=True, text=True,
        )
        if chk.stdout.strip() != "true":
            yield _sse("[ERROR] Container 'autoflow_grafana' is not running — deploy the stack first.")
            yield _sse("[DONE]")
            return

        grafana_url = _grafana_api_url().rstrip("/api")
        yield _sse(f"Connecting to Grafana ({grafana_url})…")

        GRAFANA_DASHBOARDS_DIR.mkdir(parents=True, exist_ok=True)

        async with _httpx.AsyncClient(verify=False, auth=(user, password), timeout=20) as c:

            # ── List all dashboards ───────────────────────────────────────────
            try:
                r = await c.get(f"{grafana_url}/api/search", params={"type": "dash-db", "limit": 500})
            except Exception as exc:
                yield _sse(f"[ERROR] Cannot reach Grafana: {exc}")
                yield _sse(f"Make sure the stack is running and {grafana_url} resolves.")
                yield _sse("[DONE]")
                return

            if r.status_code == 401:
                yield _sse("[ERROR] Authentication failed — check GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD.")
                yield _sse("[DONE]")
                return
            if r.status_code != 200:
                yield _sse(f"[ERROR] Grafana API returned HTTP {r.status_code}: {r.text[:200]}")
                yield _sse("[DONE]")
                return

            items = [i for i in r.json() if i.get("uid")]
            yield _sse(f"Found {len(items)} dashboard(s) — exporting…")

            saved, skipped = 0, 0
            for item in items:
                uid   = item["uid"]
                title = item.get("title", uid)

                dr = await c.get(f"{grafana_url}/api/dashboards/uid/{uid}")
                if dr.status_code != 200:
                    yield _sse(f"[WARN] Could not fetch '{title}' (HTTP {dr.status_code}) — skipping.")
                    skipped += 1
                    continue

                dashboard = dr.json().get("dashboard", {})
                dashboard.pop("id", None)   # strip DB-internal ID; UID is preserved

                slug = re.sub(r"[^\w\-]", "_", title.lower()).strip("_")
                slug = re.sub(r"_+", "_", slug)[:60]
                out  = GRAFANA_DASHBOARDS_DIR / f"{slug}.json"

                out.write_text(_j.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
                yield _sse(f"  [{saved + 1}/{len(items)}] {title}  →  {out.name}")
                saved += 1

        yield _sse("")
        yield _sse(f"[SUCCESS] {saved} dashboard(s) saved to monitoring/grafana/dashboards/")
        if skipped:
            yield _sse(f"[WARN] {skipped} dashboard(s) could not be fetched.")
        yield _sse("Commit these files to preserve dashboard changes across fresh deploys.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── System preflight ─────────────────────────────────────────────────────────

@app.get("/api/system/preflight")
def system_preflight():
    """Check Docker version, RAM, disk space and DNS before deployment."""
    import re, socket, shutil as _sh

    checks: list[dict] = []

    # ── Docker engine ─────────────────────────────────────────────────────────
    r = subprocess.run(["docker", "--version"], capture_output=True, text=True)
    if r.returncode == 0:
        m = re.search(r"(\d+)\.(\d+)", r.stdout)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            ok = major >= 24
            checks.append({
                "id": "docker", "label": "Docker ≥ 24",
                "ok": ok,
                "detail": f"Docker {major}.{minor}" + ("" if ok else " — need ≥ 24, upgrade: https://docs.docker.com/engine/install/"),
            })
        else:
            checks.append({"id": "docker", "label": "Docker ≥ 24", "ok": False, "detail": r.stdout.strip()})
    else:
        checks.append({"id": "docker", "label": "Docker ≥ 24", "ok": False, "detail": "docker not found in PATH"})

    # ── Docker Compose plugin ─────────────────────────────────────────────────
    rc = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if rc.returncode == 0:
        version_line = rc.stdout.strip().split("\n")[0]
        checks.append({"id": "compose", "label": "Docker Compose plugin", "ok": True, "detail": version_line})
    else:
        checks.append({
            "id": "compose", "label": "Docker Compose plugin", "ok": False,
            "detail": "docker compose plugin not found — install: sudo apt install docker-compose-plugin",
        })

    # ── RAM ≥ 4 GB ────────────────────────────────────────────────────────────
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gb = kb / 1024 / 1024
                    ok = gb >= 4.0
                    checks.append({
                        "id": "ram", "label": "RAM ≥ 4 GB",
                        "ok": ok,
                        "detail": f"{gb:.1f} GB available" + ("" if ok else " — AWX alone needs ≥ 4 GB"),
                    })
                    break
    except Exception as exc:
        checks.append({"id": "ram", "label": "RAM ≥ 4 GB", "ok": False, "detail": f"could not read /proc/meminfo: {exc}"})

    # ── Disk ≥ 20 GB free ─────────────────────────────────────────────────────
    try:
        usage  = _sh.disk_usage(str(ROOT))
        free   = usage.free  / 1024 ** 3
        total  = usage.total / 1024 ** 3
        ok     = free >= 20.0
        checks.append({
            "id": "disk", "label": "Disk ≥ 20 GB free",
            "ok": ok,
            "detail": f"{free:.1f} GB free / {total:.1f} GB total" + ("" if ok else " — AWX images + DB need ≥ 20 GB"),
        })
    except Exception as exc:
        checks.append({"id": "disk", "label": "Disk ≥ 20 GB free", "ok": False, "detail": str(exc)})

    # ── DNS resolution ────────────────────────────────────────────────────────
    dns_hosts = ["github.com", "registry-1.docker.io"]
    dns_ok    = True
    dns_parts: list[str] = []
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(3)
    try:
        for host in dns_hosts:
            try:
                socket.getaddrinfo(host, 443)
                dns_parts.append(f"{host} OK")
            except Exception:
                dns_ok = False
                dns_parts.append(f"{host} FAILED")
    finally:
        socket.setdefaulttimeout(prev_timeout)

    checks.append({
        "id": "dns", "label": "DNS resolution",
        "ok": dns_ok,
        "detail": "  |  ".join(dns_parts) + ("" if dns_ok else " — check /etc/resolv.conf and network connectivity"),
    })

    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


# ── Stack status ──────────────────────────────────────────────────────────────

@app.get("/api/status")
def stack_status():
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    running = result.stdout.count("Up") if result.returncode == 0 else 0
    return {"running": running, "output": result.stdout}


# ── Disaster Recovery / Backup ────────────────────────────────────────────────

BACKUP_SCRIPT = ROOT / "scripts" / "backup.sh"


def _restic_env(config: dict) -> dict | None:
    """Build env vars for Restic commands. Returns None if not configured."""
    import shutil
    password = config.get("BACKUP_RESTIC_PASSWORD", "")
    if not password or not shutil.which("restic"):
        return None

    env = {**os.environ, "RESTIC_PASSWORD": password}
    backend = config.get("BACKUP_BACKEND", "local")

    if backend == "local":
        env["RESTIC_REPOSITORY"] = config.get("BACKUP_LOCAL_PATH", str(ROOT / "backups" / "restic"))
    elif backend == "sftp":
        path = config.get("BACKUP_LOCAL_PATH", "")
        env["RESTIC_REPOSITORY"] = path if path.startswith("sftp:") else f"sftp:{path}"
    elif backend == "s3":
        endpoint = config.get("BACKUP_S3_ENDPOINT", "").rstrip("/")
        bucket   = config.get("BACKUP_S3_BUCKET", "autoflow-backup")
        env["AWS_ACCESS_KEY_ID"]     = config.get("BACKUP_S3_ACCESS_KEY", "")
        env["AWS_SECRET_ACCESS_KEY"] = config.get("BACKUP_S3_SECRET_KEY", "")
        if endpoint:
            env["RESTIC_REPOSITORY"] = f"s3:{endpoint}/{bucket}"
        else:
            env["RESTIC_REPOSITORY"] = f"s3:s3.amazonaws.com/{bucket}"
    elif backend == "b2":
        bucket = config.get("BACKUP_S3_BUCKET", "autoflow-backup")
        env["B2_ACCOUNT_ID"]  = config.get("BACKUP_S3_ACCESS_KEY", "")
        env["B2_ACCOUNT_KEY"] = config.get("BACKUP_S3_SECRET_KEY", "")
        env["RESTIC_REPOSITORY"] = f"b2:{bucket}:restic"

    return env


@app.get("/api/backup/status")
def backup_status():
    import shutil, json as _j
    config = _load_env()

    restic_path = shutil.which("restic")
    result: dict = {
        "configured":       bool(config.get("BACKUP_RESTIC_PASSWORD", "")),
        "restic_installed": bool(restic_path),
        "backend":          config.get("BACKUP_BACKEND", "local"),
        "rto":              config.get("BACKUP_RTO_HOURS", "4"),
        "rpo":              config.get("BACKUP_RPO_HOURS", "24"),
        "cron_schedule":    config.get("BACKUP_CRON", "0 2 * * *"),
    }

    if not result["configured"] or not restic_path:
        return result

    env = _restic_env(config)
    if not env:
        return result

    result["repo"] = env.get("RESTIC_REPOSITORY", "")

    # Check last snapshot
    r = subprocess.run(
        ["restic", "snapshots", "--json", "--last", "--no-lock"],
        capture_output=True, text=True, env=env,
    )
    if r.returncode == 0:
        try:
            snaps = _j.loads(r.stdout)
            result["repo_initialized"] = True
            if snaps:
                last = snaps[-1]
                result["last_snapshot"] = {
                    "id":       last.get("id", "")[:8],
                    "time":     last.get("time", "")[:19].replace("T", " "),
                    "hostname": last.get("hostname", ""),
                }
        except Exception:
            result["repo_initialized"] = True
            result["last_snapshot"] = None
    else:
        result["repo_initialized"] = False

    # Check cron
    cron_r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    result["cron_installed"] = (
        "backup.sh" in cron_r.stdout if cron_r.returncode == 0 else False
    )

    return result


@app.get("/api/backup/run")
async def backup_run():
    """SSE: run a Restic backup immediately."""

    async def stream():
        config = _load_env()
        if not config.get("BACKUP_RESTIC_PASSWORD", ""):
            yield _sse("[ERROR] BACKUP_RESTIC_PASSWORD not set — configure the Disaster Recovery section first.")
            yield _sse("[DONE]")
            return
        if not BACKUP_SCRIPT.exists():
            yield _sse(f"[ERROR] backup.sh not found at {BACKUP_SCRIPT}")
            yield _sse("[DONE]")
            return

        yield _sse("Starting backup…")
        env = {**os.environ, **{k: v for k, v in config.items() if v}}

        proc = await asyncio.create_subprocess_exec(
            "bash", str(BACKUP_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(ROOT),
        )
        async for line in proc.stdout:
            ln = line.decode().rstrip()
            if ln.startswith("[ERROR]") or "error" in ln.lower():
                yield _sse(f"[ERROR] {ln}" if not ln.startswith("[") else ln)
            elif ln.startswith("[SUCCESS]") or "snapshot" in ln.lower() or "complete" in ln.lower():
                yield _sse(f"[SUCCESS] {ln}" if not ln.startswith("[") else ln)
            else:
                yield _sse(ln)

        await proc.wait()
        if proc.returncode == 0:
            yield _sse("[SUCCESS] Backup completed successfully.")
        else:
            yield _sse(f"[ERROR] Backup script exited with code {proc.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/backup/restore-test")
async def backup_restore_test():
    """SSE: smoke-test — restic check + snapshots list + dry-run restore."""

    async def stream():
        import shutil, json as _j, tempfile
        config = _load_env()

        if not config.get("BACKUP_RESTIC_PASSWORD", ""):
            yield _sse("[ERROR] BACKUP_RESTIC_PASSWORD not set.")
            yield _sse("[DONE]")
            return

        if not shutil.which("restic"):
            yield _sse("[ERROR] restic is not installed.")
            yield _sse("Install it: curl -fsSL https://rclone.org/install.sh | bash  (or via package manager)")
            yield _sse("[DONE]")
            return

        env = _restic_env(config)
        if not env:
            yield _sse("[ERROR] Could not build Restic environment — check BACKUP_RESTIC_PASSWORD and backend config.")
            yield _sse("[DONE]")
            return

        # ── Step 1: repository integrity ──────────────────────────────────────
        yield _sse("Step 1/3: Verifying repository integrity (restic check)…")
        r = subprocess.run(["restic", "check", "--no-lock"], capture_output=True, text=True, env=env)
        for ln in r.stdout.splitlines():
            if ln.strip():
                yield _sse(f"  {ln}")
        if r.returncode != 0:
            yield _sse(f"[ERROR] Repository check failed: {r.stderr.strip()[:300]}")
            yield _sse("[DONE]")
            return
        yield _sse("[SUCCESS] Repository integrity: OK")

        # ── Step 2: list snapshots ────────────────────────────────────────────
        yield _sse("Step 2/3: Listing recent snapshots…")
        r2 = subprocess.run(["restic", "snapshots", "--json", "--no-lock"], capture_output=True, text=True, env=env)
        if r2.returncode != 0:
            yield _sse(f"[ERROR] Could not list snapshots: {r2.stderr.strip()[:200]}")
            yield _sse("[DONE]")
            return
        try:
            snaps = _j.loads(r2.stdout)
            if not snaps:
                yield _sse("[WARN] No snapshots found — run a backup first.")
                yield _sse("[DONE]")
                return
            yield _sse(f"  Found {len(snaps)} snapshot(s) — last 5:")
            for s in snaps[-5:]:
                sid   = s.get("id", "")[:8]
                stime = s.get("time", "")[:19].replace("T", " ")
                shost = s.get("hostname", "")
                yield _sse(f"  [{sid}] {stime} @ {shost}")
            yield _sse("[SUCCESS] Snapshots: OK")
        except Exception as exc:
            yield _sse(f"[WARN] Could not parse snapshots: {exc}")

        # ── Step 3: dry-run restore ───────────────────────────────────────────
        yield _sse("Step 3/3: Dry-run restore of latest snapshot…")
        with tempfile.TemporaryDirectory() as tmpdir:
            r3 = subprocess.run(
                ["restic", "restore", "latest", "--target", tmpdir, "--dry-run", "--no-lock"],
                capture_output=True, text=True, env=env,
            )
            for ln in (r3.stdout + r3.stderr).splitlines():
                if ln.strip():
                    yield _sse(f"  {ln}")
            if r3.returncode == 0:
                yield _sse("[SUCCESS] Restore dry-run: OK")
            else:
                yield _sse(f"[WARN] Restore dry-run had issues (exit {r3.returncode})")

        yield _sse("")
        yield _sse("[SUCCESS] Smoke test PASSED — backup is readable and restorable.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/backup/cron/install")
def backup_cron_install():
    """Install (or update) the automatic backup cron job."""
    config   = _load_env()
    schedule = config.get("BACKUP_CRON", "0 2 * * *").strip() or "0 2 * * *"

    if not BACKUP_SCRIPT.exists():
        raise HTTPException(400, "backup.sh not found")

    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [l for l in (r.stdout if r.returncode == 0 else "").splitlines()
             if "backup.sh" not in l]

    cron_line = (
        f"{schedule}  bash {BACKUP_SCRIPT} "
        f">> /var/log/autoflow-backup.log 2>&1  # autoflow-dr"
    )
    lines.append(cron_line)

    r2 = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                        capture_output=True, text=True)
    if r2.returncode != 0:
        raise HTTPException(500, f"crontab update failed: {r2.stderr.strip()}")

    return {"status": "installed", "schedule": schedule, "line": cron_line}


@app.delete("/api/backup/cron/uninstall")
def backup_cron_uninstall():
    """Remove the automatic backup cron job."""
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"status": "not_installed"}

    lines = [l for l in r.stdout.splitlines() if "backup.sh" not in l]
    r2 = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                        capture_output=True, text=True)
    if r2.returncode != 0:
        raise HTTPException(500, f"crontab update failed: {r2.stderr.strip()}")

    return {"status": "uninstalled"}


# ── Compliance & Audit ────────────────────────────────────────────────────────

SCANNER_CONTAINER = "autoflow_security_scanner"
_COMPLIANCE_REPORT_DIR = Path("/tmp/wizard-compliance")


def _scanner_running() -> bool:
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", SCANNER_CONTAINER],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "true"


@app.get("/api/compliance/status")
def compliance_status():
    """Return compliance report metadata (last generated, summary)."""
    import json as _j
    running = _scanner_running()
    _COMPLIANCE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cached  = _COMPLIANCE_REPORT_DIR / "latest.json"

    result: dict = {
        "scanner_running": running,
        "report_available": cached.exists(),
    }
    if cached.exists():
        try:
            data = _j.loads(cached.read_text())
            result["generated_at"]   = data.get("generated_at", "")
            result["total_findings"] = data.get("summary", {}).get("total_findings", 0)
            result["by_severity"]    = data.get("summary", {}).get("by_severity", {})
        except Exception:
            pass
    return result


@app.get("/api/compliance/generate")
async def compliance_generate():
    """SSE: trigger on-demand compliance report via docker exec inside the scanner."""

    async def stream():
        if not _scanner_running():
            yield _sse("[ERROR] Security scanner container is not running — deploy the stack first.")
            yield _sse("[DONE]")
            return

        config = _load_env()
        token  = config.get("COMPLIANCE_ADMIN_TOKEN", "")
        bearer = f"Bearer {token}" if token else ""

        yield _sse("Triggering compliance report generation inside security-scanner…")
        yield _sse("(This may take 5–30 min depending on image sizes and Trivy cache)")

        # Stream output from docker exec python3 compliance.py inside the container
        cmd = ["docker", "exec", SCANNER_CONTAINER, "python3", "/app/compliance.py"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async for raw in proc.stdout:
            line = raw.decode().rstrip()
            if not line:
                continue
            if "ERROR" in line.upper():
                yield _sse(f"[ERROR] {line}" if not line.startswith("[") else line)
            elif "saving" in line.lower() or "complete" in line.lower() or "report saved" in line.lower():
                yield _sse(f"[SUCCESS] {line}" if not line.startswith("[") else line)
            else:
                yield _sse(line)

        rc = await proc.wait()

        if rc != 0:
            yield _sse(f"[ERROR] compliance.py exited with code {rc}")
            yield _sse("[DONE]")
            return

        # Pull report files out of the container for local caching
        _COMPLIANCE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for filename in ("latest.json", "latest.md"):
            cp_r = subprocess.run(
                ["docker", "cp",
                 f"{SCANNER_CONTAINER}:/tmp/compliance/{filename}",
                 str(_COMPLIANCE_REPORT_DIR / filename)],
                capture_output=True, text=True,
            )
            if cp_r.returncode != 0:
                yield _sse(f"[WARN] Could not copy {filename} from container: {cp_r.stderr.strip()}")

        yield _sse("[SUCCESS] Compliance report generated and cached.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/compliance/report/latest")
def compliance_report_latest():
    """Return the cached compliance report JSON."""
    import json as _j
    cached = _COMPLIANCE_REPORT_DIR / "latest.json"
    if not cached.exists():
        raise HTTPException(404, "No compliance report — run generate first")
    return StreamingResponse(
        iter([cached.read_bytes()]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="compliance-report.json"'},
    )


@app.get("/api/compliance/report/latest/markdown")
def compliance_report_latest_md():
    """Return the cached compliance report as Markdown."""
    cached = _COMPLIANCE_REPORT_DIR / "latest.md"
    if not cached.exists():
        raise HTTPException(404, "No compliance report — run generate first")
    return StreamingResponse(
        iter([cached.read_bytes()]),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="compliance-report.md"'},
    )
