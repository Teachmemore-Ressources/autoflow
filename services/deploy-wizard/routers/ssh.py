"""
routers/ssh.py — SSH key management for AWX provisioning.

Manages the lifecycle of the SSH key pair used by AWX EE containers
to provision VMs via cloud-init:
  - autoflow/ssh/id_ed25519      (private key, 600, never committed)
  - autoflow/ssh/id_ed25519.pub  (public key, 644)
  - autoflow/ssh/config          (SSH client config, 600)
  - autoflow/ssh/known_hosts     (644, environment-specific)

The public key is injected into cloud-init by 01_clone_vm.yml via slurp.
The private key is mounted read-only into EE containers via AWX_ISOLATION_SHOW_PATHS.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from core.auth import _audit
from core.env import ROOT, SSH_DIR, _load_env
from core.shell import _sse
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

_PRIVATE_KEY = SSH_DIR / "id_ed25519"
_PUBLIC_KEY  = SSH_DIR / "id_ed25519.pub"
_SSH_CONFIG  = SSH_DIR / "config"
_KNOWN_HOSTS = SSH_DIR / "known_hosts"

_SSH_CONFIG_CONTENT = """\
Host *
    # No strict host-key checking for lab VMs (dynamic IPs, fresh cloud-init)
    StrictHostKeyChecking no
    UserKnownHostsFile /var/lib/awx/.ssh/known_hosts
    IdentityFile /var/lib/awx/.ssh/id_ed25519
    ConnectTimeout 10
    ServerAliveInterval 30
    ServerAliveCountMax 3
"""

# ── Helpers ───────────────────────────────────────────────────────────────────


def _key_exists() -> bool:
    return _PRIVATE_KEY.exists() and _PUBLIC_KEY.exists()


def _fingerprint() -> str | None:
    """Return SHA-256 fingerprint of the current public key, or None."""
    if not _PUBLIC_KEY.exists():
        return None
    result = subprocess.run(
        ["ssh-keygen", "-l", "-E", "sha256", "-f", str(_PUBLIC_KEY)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _read_pubkey() -> str | None:
    if not _PUBLIC_KEY.exists():
        return None
    return _PUBLIC_KEY.read_text().strip()


def _generate_key(comment: str = "awx-provisioning@autoflow") -> None:
    """Generate a new ed25519 key pair in SSH_DIR (overwrites existing)."""
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Remove stale keys before generation
    for f in (_PRIVATE_KEY, _PUBLIC_KEY):
        if f.exists():
            f.unlink()
    subprocess.run(
        [
            "ssh-keygen",
            "-t", "ed25519",
            "-C", comment,
            "-f", str(_PRIVATE_KEY),
            "-N", "",     # No passphrase (AWX injects the key without interaction)
        ],
        check=True,
        capture_output=True,
    )
    _PRIVATE_KEY.chmod(0o600)
    _PUBLIC_KEY.chmod(0o644)

    # Ensure config and known_hosts exist
    if not _SSH_CONFIG.exists():
        _SSH_CONFIG.write_text(_SSH_CONFIG_CONTENT)
        _SSH_CONFIG.chmod(0o600)
    if not _KNOWN_HOSTS.exists():
        _KNOWN_HOSTS.touch(mode=0o644)

    # Ensure .gitkeep is present so the directory is tracked (without the secret)
    gitkeep = SSH_DIR / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()


async def _patch_awx_credential(
    awx_url: str,
    awx_user: str,
    awx_password: str,
    credential_name: str,
    private_key_pem: str,
) -> tuple[bool, str]:
    """
    Update the AWX Machine credential private key via the AWX API.
    Returns (success: bool, message: str).
    """
    import httpx as _httpx

    async with _httpx.AsyncClient(verify=False, auth=(awx_user, awx_password), timeout=20) as c:
        r = await c.get(
            f"{awx_url}/api/v2/credentials/",
            params={"name": credential_name, "kind": "ssh"},
        )
        if r.status_code != 200:
            return False, f"AWX credentials API returned {r.status_code}"

        results = r.json().get("results", [])
        if not results:
            return False, f"Credential '{credential_name}' not found"

        cred_id = results[0]["id"]
        r = await c.patch(
            f"{awx_url}/api/v2/credentials/{cred_id}/",
            json={"inputs": {"ssh_key_data": private_key_pem}},
        )
        if r.status_code in (200, 204):
            return True, f"Credential '{credential_name}' (id={cred_id}) updated ✔"
        else:
            return False, f"PATCH failed (HTTP {r.status_code}): {r.text[:200]}"


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/api/ssh/status")
def ssh_status():
    """
    Return the current state of the SSH key pair.
    Includes: exists, fingerprint, public key, creation date.
    """
    exists = _key_exists()
    fp = _fingerprint() if exists else None
    pubkey = _read_pubkey() if exists else None

    # Key creation date from file mtime
    created_at = None
    if exists and _PRIVATE_KEY.exists():
        mtime = _PRIVATE_KEY.stat().st_mtime
        created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "exists": exists,
        "fingerprint": fp,
        "public_key": pubkey,
        "created_at": created_at,
        "private_key_path": str(_PRIVATE_KEY),
        "public_key_path": str(_PUBLIC_KEY),
        "config_exists": _SSH_CONFIG.exists(),
        "known_hosts_exists": _KNOWN_HOSTS.exists(),
        "ssh_dir": str(SSH_DIR),
    }


@router.get("/api/ssh/public-key")
def ssh_public_key():
    """Return the raw public key string (one-liner, for copy-paste)."""
    if not _PUBLIC_KEY.exists():
        raise HTTPException(404, "SSH public key not found — run POST /api/ssh/generate first.")
    return {"public_key": _read_pubkey(), "path": str(_PUBLIC_KEY)}


@router.get("/api/ssh/generate")
async def ssh_generate(request: Request):
    """
    SSE: generate a new ed25519 key pair in autoflow/ssh/.
    If a key already exists it is backed up before overwrite.
    Does NOT restart AWX — the new key is picked up on the next job run
    (it is bind-mounted read-only at container launch time).
    """
    _audit(request, "ssh.generate")

    async def stream():
        yield _sse("=== SSH Key Generation ===")
        yield _sse(f"Target directory: {SSH_DIR}")

        if not shutil.which("ssh-keygen"):
            yield _sse("[ERROR] ssh-keygen is not available in this environment.")
            yield _sse("[DONE]")
            return

        # ── Backup existing key if present ────────────────────────────────
        if _PRIVATE_KEY.exists():
            ts = int(time.time())
            bak_priv = SSH_DIR / f"id_ed25519.{ts}.bak"
            bak_pub  = SSH_DIR / f"id_ed25519.pub.{ts}.bak"
            shutil.copy2(_PRIVATE_KEY, bak_priv)
            shutil.copy2(_PUBLIC_KEY, bak_pub)
            bak_priv.chmod(0o600)
            yield _sse(f"Existing key backed up → id_ed25519.{ts}.bak")
            yield _sse("⚠  Any VM provisioned with the OLD key will need the public key re-injected.")
            yield _sse("   (or use ssh-copy-id with the old key before rotating)")

        # ── Generate new key pair ─────────────────────────────────────────
        yield _sse("Generating ed25519 key pair (awx-provisioning@autoflow)…")
        try:
            _generate_key()
        except subprocess.CalledProcessError as exc:
            yield _sse(f"[ERROR] ssh-keygen failed: {exc.stderr.decode() if exc.stderr else exc}")
            yield _sse("[DONE]")
            return

        fp = _fingerprint()
        pubkey = _read_pubkey()
        yield _sse(f"Key generated ✔")
        yield _sse(f"  Fingerprint : {fp}")
        yield _sse(f"  Public key  : {pubkey}")
        yield _sse("")
        yield _sse("Files written:")
        yield _sse(f"  {_PRIVATE_KEY}  (600 — never committed)")
        yield _sse(f"  {_PUBLIC_KEY} (644 — safe to commit)")
        yield _sse(f"  {_SSH_CONFIG}       (600)")
        yield _sse(f"  {_KNOWN_HOSTS}  (644)")
        yield _sse("")
        yield _sse("Next steps:")
        yield _sse("  1. Sync the private key to AWX Machine credential via /api/ssh/sync-awx")
        yield _sse("  2. Restart awx_task so the mount picks up the new key:")
        yield _sse("     docker compose restart awx_task")
        yield _sse("  3. New VMs provisioned after this point will use the new public key ✔")
        yield _sse("[SUCCESS] SSH key generation complete!")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/ssh/sync-awx")
async def ssh_sync_awx(request: Request):
    """
    SSE: push the current private key to the AWX Machine credential 'lab-ssh-key'.
    Uses the AWX API with admin credentials from the .env.
    After sync, old jobs will re-use the old key from the AWX DB, while new jobs
    will use the new mounted key — the credential is the authoritative source for
    remote SSH connections (02_configure_vm.yml, 03_validate_vm.yml).
    """
    _audit(request, "ssh.sync_awx")

    async def stream():
        import httpx as _httpx

        if not _PRIVATE_KEY.exists():
            yield _sse("[ERROR] No private key found at:")
            yield _sse(f"  {_PRIVATE_KEY}")
            yield _sse("Run /api/ssh/generate first.")
            yield _sse("[DONE]")
            return

        config = _load_env()
        awx_domain = config.get("DOMAIN", "localhost")
        awx_user   = config.get("AWX_ADMIN_USER", "admin")
        awx_pass   = config.get("AWX_ADMIN_PASSWORD", "")
        # The AWX URL is either localhost (from inside compose network)
        # or based on the domain (from the wizard, which runs in-compose).
        awx_url = f"https://awx.{awx_domain}"
        # Credential name to update
        cred_name = config.get("AWX_SSH_CREDENTIAL_NAME", "lab-ssh-key")

        if not awx_pass:
            yield _sse("[ERROR] AWX_ADMIN_PASSWORD not set in .env")
            yield _sse("[DONE]")
            return

        private_key = _PRIVATE_KEY.read_text()
        pubkey = _read_pubkey()

        yield _sse(f"=== Sync SSH key → AWX credential '{cred_name}' ===")
        yield _sse(f"AWX endpoint : {awx_url}")
        yield _sse(f"Public key   : {pubkey[:60]}…")
        yield _sse("")

        yield _sse(f"Connecting to AWX ({awx_url})…")

        async with _httpx.AsyncClient(verify=False, auth=(awx_user, awx_pass), timeout=20) as c:
            # ── Verify AWX connectivity ───────────────────────────────────
            try:
                r = await c.get(f"{awx_url}/api/v2/ping/")
                if r.status_code != 200:
                    yield _sse(f"[ERROR] AWX /ping returned HTTP {r.status_code}")
                    yield _sse("[DONE]")
                    return
                yield _sse("AWX reachable ✔")
            except Exception as exc:
                yield _sse(f"[ERROR] Cannot reach AWX: {exc}")
                yield _sse(f"Verify the stack is running: docker compose ps awx_web")
                yield _sse("[DONE]")
                return

            # ── Find credential by name ───────────────────────────────────
            r = await c.get(
                f"{awx_url}/api/v2/credentials/",
                params={"name": cred_name, "kind": "ssh"},
            )
            if r.status_code != 200:
                yield _sse(f"[ERROR] Cannot query AWX credentials (HTTP {r.status_code})")
                yield _sse("[DONE]")
                return

            results = r.json().get("results", [])
            if not results:
                yield _sse(f"[WARN] Credential '{cred_name}' not found in AWX (kind=ssh).")
                yield _sse("Available credentials:")
                r2 = await c.get(f"{awx_url}/api/v2/credentials/", params={"kind": "ssh"})
                for cr in r2.json().get("results", []):
                    yield _sse(f"  - id={cr['id']}  name={cr['name']}")
                yield _sse("")
                yield _sse(f"Set AWX_SSH_CREDENTIAL_NAME in .env to the correct credential name.")
                yield _sse("[DONE]")
                return

            cred_id   = results[0]["id"]
            cred_name_actual = results[0]["name"]
            yield _sse(f"Found credential '{cred_name_actual}' (id={cred_id}) ✔")

            # ── PATCH private key ─────────────────────────────────────────
            yield _sse("Updating private key…")
            r = await c.patch(
                f"{awx_url}/api/v2/credentials/{cred_id}/",
                json={"inputs": {"ssh_key_data": private_key}},
            )
            if r.status_code in (200, 204):
                yield _sse(f"Credential '{cred_name_actual}' updated ✔")
            else:
                yield _sse(f"[ERROR] PATCH failed (HTTP {r.status_code}): {r.text[:300]}")
                yield _sse("[DONE]")
                return

            # ── Update JT extra_vars if a JT name is configured ──────────
            jt_name = config.get("AWX_CLONE_JT_NAME", "")
            if jt_name:
                yield _sse(f"")
                yield _sse(f"Updating Job Template '{jt_name}' extra_vars → removing static cloudinit_ssh_pubkey…")
                r = await c.get(
                    f"{awx_url}/api/v2/job_templates/",
                    params={"name": jt_name},
                )
                if r.status_code == 200 and r.json().get("results"):
                    jt_id = r.json()["results"][0]["id"]
                    # Read current extra_vars
                    import json as _json
                    current_ev = r.json()["results"][0].get("extra_vars", "{}")
                    try:
                        ev_dict = _json.loads(current_ev) if current_ev else {}
                    except Exception:
                        ev_dict = {}
                    # Remove the static key (playbook now reads from file)
                    ev_dict.pop("cloudinit_ssh_pubkey", None)
                    r2 = await c.patch(
                        f"{awx_url}/api/v2/job_templates/{jt_id}/",
                        json={"extra_vars": _json.dumps(ev_dict, indent=2)},
                    )
                    if r2.status_code in (200, 204):
                        yield _sse(f"JT '{jt_name}' extra_vars updated — cloudinit_ssh_pubkey removed ✔")
                    else:
                        yield _sse(f"[WARN] Could not update JT extra_vars (HTTP {r2.status_code}) — manual cleanup needed")
                else:
                    yield _sse(f"[WARN] JT '{jt_name}' not found — skipping extra_vars cleanup")

        yield _sse("")
        yield _sse("[SUCCESS] AWX sync complete!")
        yield _sse("  Next jobs using 'lab-ssh-key' will authenticate with the new private key.")
        yield _sse("  The public key is read dynamically from /var/lib/awx/.ssh/id_ed25519.pub")
        yield _sse("  by 01_clone_vm.yml — no extra_vars update needed.")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/ssh/rotate")
async def ssh_rotate(request: Request):
    """
    SSE: full key rotation workflow.
      1. Backup current key
      2. Generate new key pair
      3. Sync new private key to AWX Machine credential
      4. Display next steps for existing VMs (ssh-copy-id)
    """
    _audit(request, "ssh.rotate")

    async def stream():
        yield _sse("=== SSH Key Rotation ===")
        yield _sse("")
        yield _sse("Step 1/3 — Generating new key pair…")
        yield _sse("────────────────────────────────")

        if not shutil.which("ssh-keygen"):
            yield _sse("[ERROR] ssh-keygen not available.")
            yield _sse("[DONE]")
            return

        # Backup + generate
        old_pubkey = _read_pubkey()
        if _PRIVATE_KEY.exists():
            ts = int(time.time())
            bak = SSH_DIR / f"id_ed25519.{ts}.bak"
            shutil.copy2(_PRIVATE_KEY, bak)
            bak.chmod(0o600)
            yield _sse(f"Old key backed up → ssh/id_ed25519.{ts}.bak")
            yield _sse(f"Old fingerprint: {_fingerprint()}")

        try:
            _generate_key()
        except subprocess.CalledProcessError as exc:
            yield _sse(f"[ERROR] Key generation failed: {exc}")
            yield _sse("[DONE]")
            return

        new_pubkey = _read_pubkey()
        yield _sse(f"New key generated ✔")
        yield _sse(f"New fingerprint : {_fingerprint()}")
        yield _sse(f"New public key  : {new_pubkey}")
        yield _sse("")

        # ── Sync to AWX ───────────────────────────────────────────────────
        yield _sse("Step 2/3 — Syncing new private key to AWX credential…")
        yield _sse("─────────────────────────────────────────────────────")

        import httpx as _httpx

        config = _load_env()
        awx_domain = config.get("DOMAIN", "localhost")
        awx_user   = config.get("AWX_ADMIN_USER", "admin")
        awx_pass   = config.get("AWX_ADMIN_PASSWORD", "")
        awx_url    = f"https://awx.{awx_domain}"
        cred_name  = config.get("AWX_SSH_CREDENTIAL_NAME", "lab-ssh-key")

        if not awx_pass:
            yield _sse("[WARN] AWX_ADMIN_PASSWORD not set — skipping AWX sync.")
            yield _sse("       Run /api/ssh/sync-awx manually after setting AWX_ADMIN_PASSWORD.")
        else:
            private_key = _PRIVATE_KEY.read_text()
            try:
                async with _httpx.AsyncClient(verify=False, auth=(awx_user, awx_pass), timeout=20) as c:
                    r = await c.get(
                        f"{awx_url}/api/v2/credentials/",
                        params={"name": cred_name, "kind": "ssh"},
                    )
                    if r.status_code == 200 and r.json().get("results"):
                        cred_id = r.json()["results"][0]["id"]
                        pr = await c.patch(
                            f"{awx_url}/api/v2/credentials/{cred_id}/",
                            json={"inputs": {"ssh_key_data": private_key}},
                        )
                        if pr.status_code in (200, 204):
                            yield _sse(f"AWX credential '{cred_name}' updated ✔")
                        else:
                            yield _sse(f"[WARN] PATCH credential failed (HTTP {pr.status_code})")
                    else:
                        yield _sse(f"[WARN] Credential '{cred_name}' not found in AWX.")
            except Exception as exc:
                yield _sse(f"[WARN] Could not reach AWX: {exc}")

        yield _sse("")
        yield _sse("Step 3/3 — Post-rotation checklist")
        yield _sse("──────────────────────────────────")
        yield _sse("New VMs: will automatically use the new public key via cloud-init ✔")
        yield _sse("")
        yield _sse("Existing VMs (need manual key update):")
        yield _sse("  For each VM already provisioned with the OLD key:")
        yield _sse("  a) Use the BACKUP private key to connect and add the new public key:")
        yield _sse("     OLD_KEY=ssh/id_ed25519.<timestamp>.bak")
        yield _sse(f"    ssh -i $OLD_KEY ansible@<VM_IP> \\")
        yield _sse(f'       "echo \\"{new_pubkey}\\" >> ~/.ssh/authorized_keys"')
        yield _sse("")
        yield _sse("  b) Or re-provision the VM via the AWX workflow (destroys existing VM).")
        yield _sse("")
        yield _sse("  c) Remove the backup key once migration is complete:")
        yield _sse("     rm autoflow/ssh/id_ed25519.*.bak")
        yield _sse("")
        yield _sse("  Restart awx_task to pick up the new mounted key:")
        yield _sse("     docker compose restart awx_task")
        yield _sse("")
        yield _sse("[SUCCESS] Rotation complete!")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
