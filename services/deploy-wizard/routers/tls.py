"""
routers/tls.py — TLS/PKI certificate generation, permissions, and CA download.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

from core.auth import _audit
from core.env import CERTS_DIR, ENV_FILE, ROOT, TLS_YML, _load_env
from core.shell import (
    PKI_CA_NAME,
    PKI_URL,
    WIZARD_PKI_OVERRIDE,
    _sse,
    _sudo_run,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


# ── TLS helpers ───────────────────────────────────────────────────────────────

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
        "label": "Executable scripts",
        "desc": "All .sh files in the repo must be executable after git clone.",
        "fix": "find scripts/ awx/ -name '*.sh' -exec chmod +x {} +",
    },
    {
        "id": "certs_dir",
        "label": "traefik/certs/ accessible",
        "desc": "The certificates directory must be writable by the current user.",
        "fix": "chown_certs",
    },
    {
        "id": "docker_group",
        "label": "Docker group",
        "desc": "The user must be in the docker group to run Docker commands without sudo.",
        "fix": "docker_group",
    },
    {
        "id": "env_writable",
        "label": ".env writable",
        "desc": "The .env file must be writable by the current user.",
        "fix": "env_writable",
    },
]


def _check_permissions() -> list[dict]:
    import grp
    results = []
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
        "detail": f"{len(non_exec)} script(s) not executable" if non_exec else "All scripts are executable",
        "items": non_exec[:5],
    })

    # 2. traefik/certs/ writable
    certs = ROOT / "traefik" / "certs"
    certs_ok = certs.exists() and os.access(certs, os.W_OK)
    results.append({
        "id": "certs_dir",
        "ok": certs_ok,
        "detail": "traefik/certs/ writable" if certs_ok else "traefik/certs/ not writable",
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
        "detail": "User is in the docker group" if in_docker else "User not in docker group — sudo required",
        "warn_relogin": not in_docker,
    })

    # 4. .env writable
    env_ok = (not ENV_FILE.exists()) or os.access(ENV_FILE, os.W_OK)
    results.append({
        "id": "env_writable",
        "ok": env_ok,
        "detail": ".env writable" if env_ok else ".env is read-only — wizard cannot save the configuration",
    })

    return results


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/generate-cert")
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

        # ── 2. Ensure pki_data volume exists (external: true — must pre-exist) ──
        subprocess.run(
            ["docker", "volume", "create", "autoflow_pki_data"],
            capture_output=True, text=True,
        )

        # ── 3. Start PKI with temporary port exposure ──────────────────────
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

        # ── 4. Wait for PKI to be healthy ─────────────────────────────────
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

        # ── 5. PKI operations ─────────────────────────────────────────────
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
                yield _sse("[ERROR] Key decryption failed — check PKI_KEY_PASSPHRASE")
                yield _sse("[DONE]")
                return
            key_pem = proc.stdout.decode()

        # ── 6. Write files to traefik/certs/ ──────────────────────────────
        CERTS_DIR.mkdir(parents=True, exist_ok=True)
        crt_file = CERTS_DIR / f"wildcard.{domain}.crt"
        key_file = CERTS_DIR / f"wildcard.{domain}.key"
        ca_file  = CERTS_DIR / f"ca.{domain}.crt"

        crt_file.write_text(cert_pem)
        crt_file.chmod(0o644)
        key_file.write_text(key_pem)
        key_file.chmod(0o600)
        ca_file.write_text(ca_pem)
        ca_file.chmod(0o644)

        yield _sse(f"  traefik/certs/wildcard.{domain}.crt  — server certificate")
        yield _sse(f"  traefik/certs/wildcard.{domain}.key  — private key (600)")
        yield _sse(f"  traefik/certs/ca.{domain}.crt        — Root CA ← import this in browser/OS")

        yield _sse("traefik/dynamic/tls.yml uses {{ env \"DOMAIN\" }} — no update needed.")
        yield _sse("")
        yield _sse("[SUCCESS] PKI setup complete!")
        yield _sse(
            f"  Import traefik/certs/ca.{domain}.crt into your browser/OS to trust all Autoflow services."
        )
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/permissions/status")
def permissions_status():
    checks = _check_permissions()
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


@router.get("/api/permissions/fix")
async def permissions_fix():
    """SSE: fix all permission issues found."""

    async def stream():
        config      = _load_env()
        deploy_user = config.get("DEPLOY_USER", "") or os.environ.get("USER", "")

        yield _sse(f"Fixing permissions (user: {deploy_user or 'current'})…")

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
        yield _sse(f"  {fixed_scripts} script(s) made executable ✔")

        # 2. traefik/certs/ ownership
        certs_dir = ROOT / "traefik" / "certs"
        if not os.access(certs_dir, os.W_OK):
            yield _sse("→ Fixing ownership of traefik/certs/…")
            target = deploy_user or os.environ.get("USER", "")
            r = _sudo_run(
                ["chown", "-R", f"{target}:{target}", str(certs_dir)],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                yield _sse(f"  traefik/certs/ → {target} ✔")
            else:
                yield _sse(f"  [WARN] chown failed: {r.stderr.strip()}")
        else:
            yield _sse("→ traefik/certs/ already accessible ✔")

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
                    yield _sse(f"→ {target} added to the docker group ✔")
                    yield _sse("  ⚠ Log out and back in (or run 'newgrp docker') to activate.")
                else:
                    yield _sse(f"  [WARN] usermod failed: {r.stderr.strip()}")
        except KeyError:
            yield _sse("→ [WARN] docker group not found — is Docker installed?")

        # 4. .env writable
        if ENV_FILE.exists() and not os.access(ENV_FILE, os.W_OK):
            yield _sse("→ Fixing .env ownership…")
            target = deploy_user or os.environ.get("USER", "")
            r = _sudo_run(
                ["chown", f"{target}:{target}", str(ENV_FILE)],
                capture_output=True, text=True,
            )
            yield _sse("  .env → writable ✔" if r.returncode == 0
                       else f"  [WARN] {r.stderr.strip()}")
        else:
            yield _sse("→ .env writable ✔")

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


@router.get("/api/cert-status")
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


@router.get("/api/download-ca")
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
