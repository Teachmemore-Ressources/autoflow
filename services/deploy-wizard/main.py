"""
Autoflow Deploy Wizard — Backend
Serves the configuration UI and orchestrates .env writes,
SOPS encryption, cert generation and docker compose deployment.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
from pathlib import Path

import bcrypt
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
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

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Autoflow Deploy Wizard", docs_url=None, redoc_url=None)
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
def save_config(data: dict):
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

    # ── Update tls.yml if domain changed ──────────────────────────
    _update_tls_yml(domain)

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
        GITEA_BEARER_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        GITEA_BEARER_TOKEN_FILE.write_text(gitea_token)
        GITEA_BEARER_TOKEN_FILE.chmod(0o600)

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
def encrypt_env():
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
    "MONITORING_ADMIN_USER":      ["traefik"],
    "MONITORING_ADMIN_PASSWORD":  ["traefik"],
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
})

# Keys that require container recreation (not just restart)
NEEDS_RECREATE: frozenset[str] = frozenset({
    "DOCKER_GID", "TRAEFIK_HTTP_PORT", "TRAEFIK_HTTPS_PORT", "GITEA_SSH_PORT",
})

# ── Docker Compose deployment ─────────────────────────────────────────────────

@app.get("/api/deploy")
async def deploy(encrypt: bool = False):
    """Stream docker compose up -d output via Server-Sent Events."""

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
async def restart_services(services: str = ""):
    """SSE: docker compose restart <services>."""
    service_list = [s.strip() for s in services.split(",") if s.strip()]

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
PKI_CA_NAME       = "autoflow-root"
WIZARD_PKI_OVERRIDE = ROOT / "docker-compose.wizard-pki.yml"


# ── TLS certificate — PKI-based flow ──────────────────────────────────────────

@app.get("/api/generate-cert")
async def generate_cert():
    """SSE: start PKI → create Root CA → issue wildcard → write to traefik/certs/."""

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

        # ── 6. Update tls.yml ─────────────────────────────────────────────
        _update_tls_yml(domain)
        yield _sse("traefik/dynamic/tls.yml updated with new paths.")
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
async def build_awx():
    """Build the custom AWX patched image (SSE stream)."""
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


# ── Post-deploy setup ─────────────────────────────────────────────────────────

@app.get("/api/init-gitea")
async def init_gitea():
    """SSE: create the Gitea admin user via 'gitea admin user create'."""

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
async def init_awx_token():
    """SSE: create an AWX API token and save it to .env + restart event_engine."""

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


# ── Stack status ──────────────────────────────────────────────────────────────

@app.get("/api/status")
def stack_status():
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    running = result.stdout.count("Up") if result.returncode == 0 else 0
    return {"running": running, "output": result.stdout}
