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
MONITORING_USERS  = ROOT / "traefik/dynamic/monitoring_users"
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
    # Start from existing .env to preserve unmanaged keys
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        existing = {k: v or "" for k, v in dotenv_values(ENV_FILE).items()}

    # Merge incoming data
    existing.update({k: v for k, v in data.items() if v is not None})

    # ── Auto-derive values from DOMAIN ─────────────────────────────
    domain = existing.get("DOMAIN", "localhost")
    if not existing.get("GITEA_DOMAIN"):
        existing["GITEA_DOMAIN"] = domain
    if not existing.get("GITEA_ROOT_URL"):
        existing["GITEA_ROOT_URL"] = f"https://git.{domain}"
    if not existing.get("PKI_BASE_URL"):
        existing["PKI_BASE_URL"] = f"https://pki.{domain}"
    if not existing.get("CORS_ORIGINS"):
        existing["CORS_ORIGINS"] = (
            f"https://awx.{domain},https://api.{domain},https://pki.{domain}"
        )

    # ── Write .env preserving template structure ───────────────────
    _write_env(existing)

    # ── Update tls.yml if domain changed ──────────────────────────
    _update_tls_yml(domain)

    # ── Regenerate monitoring_users (bcrypt for Traefik BasicAuth) ─
    pwd  = existing.get("MONITORING_ADMIN_PASSWORD", "")
    user = existing.get("MONITORING_ADMIN_USER", "admin")
    if pwd:
        hashed = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(12)).decode()
        hashed = hashed.replace("$2b$", "$2y$")  # Traefik requires $2y$
        MONITORING_USERS.parent.mkdir(parents=True, exist_ok=True)
        MONITORING_USERS.write_text(f"{user}:{hashed}\n")
        MONITORING_USERS.chmod(0o600)

    return {"status": "saved"}


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


# ── Stack status ──────────────────────────────────────────────────────────────

@app.get("/api/status")
def stack_status():
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    running = result.stdout.count("Up") if result.returncode == 0 else 0
    return {"running": running, "output": result.stdout}
