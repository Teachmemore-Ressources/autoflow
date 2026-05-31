# Autoflow — routers/ssh.py — Apache 2.0
"""
SSH credential management — multi-target, multi-organisation.

New endpoints (Phase 2)
───────────────────────
  AWX helpers
    GET  /api/ssh/awx/ping
    GET  /api/ssh/awx/organizations
    GET  /api/ssh/awx/inventories
    GET  /api/ssh/awx/inventories/{inventory_id}/targets

  Credential lifecycle
    GET    /api/ssh/credentials
    POST   /api/ssh/credentials
    GET    /api/ssh/credentials/{cred_id}
    DELETE /api/ssh/credentials/{cred_id}
    POST   /api/ssh/credentials/{cred_id}/activate      (force-activate, no SSH test)
    PATCH  /api/ssh/credentials/{cred_id}/rotation-policy

  Verification (SSE stream)
    GET  /api/ssh/credentials/{cred_id}/verify/stream

  Rotation
    POST   /api/ssh/credentials/{cred_id}/rotate
    GET    /api/ssh/credentials/{cred_id}/rotate/stream
    POST   /api/ssh/credentials/{cred_id}/rotate/confirm
    DELETE /api/ssh/credentials/{cred_id}/rotate

Legacy endpoints (backward-compatible, kept as-is)
───────────────────────────────────────────────────
    GET /api/ssh/status
    GET /api/ssh/public-key
    GET /api/ssh/generate        (SSE)
    GET /api/ssh/sync-awx        (SSE)
    GET /api/ssh/rotate          (SSE — global keypair rotation)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone

from core.auth import _audit
from core.awx_client import AWXClient, AWXError
from core.env import SSH_DIR, _load_env
from core.shell import _sse
from core.ssh_manager import (
    TargetSpec,
    activate,
    cancel_rotation,
    cleanup_expired_pending,
    confirm_rotation,
    create_credential,
    force_activate,
    get_verify_result,
    is_expired,
    is_rotation_due,
    launch_verify,
    list_managed_credentials,
    parse_cred_meta,
    start_rotation,
    update_rotation_policy,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()
_log = logging.getLogger("autoflow.ssh")

# ── Legacy constants (global keypair) ─────────────────────────────────────────

_PRIVATE_KEY = SSH_DIR / "id_ed25519"
_PUBLIC_KEY = SSH_DIR / "id_ed25519.pub"
_SSH_CONFIG = SSH_DIR / "config"
_KNOWN_HOSTS = SSH_DIR / "known_hosts"

_SSH_CONFIG_CONTENT = """\
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /var/lib/awx/.ssh/known_hosts
    IdentityFile /var/lib/awx/.ssh/id_ed25519
    ConnectTimeout 10
    ServerAliveInterval 30
    ServerAliveCountMax 3
"""

# ── AWX client factory ─────────────────────────────────────────────────────────


def _awx() -> AWXClient:
    """Return a fresh AWXClient from current .env values."""
    return AWXClient.from_env()


def _ttl_hours() -> int:
    cfg = _load_env()
    v = cfg.get("SSH_PENDING_TTL_HOURS", "24").strip()
    return int(v) if v.isdigit() else 24


# ── Helpers ───────────────────────────────────────────────────────────────────


def _awx_error_to_http(exc: AWXError) -> HTTPException:
    """Map an AWXError to a FastAPI HTTPException."""
    # Keep 4xx as-is; wrap 5xx as 502 Bad Gateway
    status = exc.status if 400 <= exc.status < 500 else 502
    return HTTPException(status_code=status, detail=exc.detail)


def _verify_stream(
    client: AWXClient,
    cred_id: int,
    poll_interval: float = 3.0,
) -> StreamingResponse:
    """
    Build a StreamingResponse that launches the SSH verify job, polls it,
    and updates the credential metadata on completion.

    Used for both initial verification and rotation verification.
    """

    async def generate():
        # Load credential metadata
        try:
            raw = await client.get_credential(cred_id)
        except AWXError as exc:
            yield _sse(f"[ERROR] Cannot load credential id={cred_id}: {exc.detail}")
            yield _sse("[DONE]")
            return

        meta = parse_cred_meta(raw)
        if meta is None:
            yield _sse(f"[ERROR] Credential id={cred_id} is not managed by Autoflow")
            yield _sse("[DONE]")
            return

        yield _sse(f"🔑 Target  : {meta.target.name} ({meta.target.type})")
        yield _sse(f"   Inventory : {meta.target.inventory_name} (id={meta.target.inventory_id})")
        if meta.target.limit:
            yield _sse(f"   Limit     : {meta.target.limit}")
        yield _sse(f"   Username  : {meta.ssh_username}")
        yield _sse("")

        # Ensure the verify job template exists
        yield _sse("⚙  Ensuring SSH verify job template exists in AWX…")
        try:
            tpl_id = await client.ensure_verify_template()
            yield _sse(f"   Job template id={tpl_id} ✔")
        except AWXError as exc:
            yield _sse(f"[ERROR] Cannot provision verify template: {exc.detail}")
            yield _sse("[DONE]")
            return

        # Launch the job
        yield _sse("")
        yield _sse("🚀 Launching SSH verification job…")
        try:
            job_id = await launch_verify(client, cred_id, meta)
            yield _sse(f"   Job id={job_id} started ✔")
        except AWXError as exc:
            yield _sse(f"[ERROR] Launch failed: {exc.detail}")
            yield _sse("[DONE]")
            return

        # Poll until terminal
        yield _sse("")
        yield _sse("⏳ Waiting for job to complete…")
        last_status = ""
        while True:
            await asyncio.sleep(poll_interval)
            try:
                result = await get_verify_result(client, job_id)
            except AWXError as exc:
                yield _sse(f"[WARN] Poll error: {exc.detail} — retrying…")
                continue

            status = result["status"]
            if status != last_status:
                yield _sse(f"   Status: {status}")
                last_status = status

            if result["finished"]:
                break

        # Update credential metadata
        yield _sse("")
        if result["success"]:
            try:
                await activate(client, cred_id, meta, verified_job_id=job_id)
                yield _sse(f"✅ {result['message']}")
                yield _sse(f"   Credential id={cred_id} marked ACTIVE")
                yield _sse(f"   Fingerprint: {meta.fingerprint}")
            except AWXError as exc:
                yield _sse(f"[ERROR] Could not mark ACTIVE: {exc.detail}")
        else:
            yield _sse(f"❌ {result['message']}")
            yield _sse("   Credential remains PENDING — fix SSH access and retry")

        import json as _json

        yield f"data: RESULT:{_json.dumps(result)}\n\n"
        yield _sse("[DONE]")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AWX helper endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/ssh/awx/ping")
async def awx_ping():
    """Test AWX connectivity. Returns {ok, url}."""
    client = _awx()
    ok = await client.ping()
    return {"ok": ok, "url": client.base_url}


@router.get("/api/ssh/awx/organizations")
async def awx_organizations():
    """List AWX organizations available for SSH credential scoping."""
    client = _awx()
    try:
        orgs = await client.list_organizations()
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc
    return {"organizations": orgs, "count": len(orgs)}


@router.get("/api/ssh/awx/inventories")
async def awx_inventories(org_id: int | None = None):
    """List AWX inventories, optionally filtered by organization."""
    client = _awx()
    try:
        inventories = await client.list_inventories(org_id=org_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc
    return {"inventories": inventories, "count": len(inventories)}


@router.get("/api/ssh/awx/inventories/{inventory_id}/targets")
async def awx_inventory_targets(inventory_id: int):
    """
    Return groups and hosts for an inventory — used to populate
    the 'limit' autocomplete when creating a credential target.
    """
    client = _awx()
    try:
        groups = await client.list_inventory_groups(inventory_id)
        hosts = await client.list_inventory_hosts(inventory_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc
    return {
        "groups": groups,
        "hosts": hosts,
        "inventory_id": inventory_id,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Credential lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/ssh/credentials")
async def list_credentials(org_id: int | None = None):
    """
    List all Autoflow-managed SSH credentials.

    Optionally filtered by AWX organization id.
    Each item includes the current status, target info, fingerprint,
    expiry, and whether a time-based rotation is due.
    """
    client = _awx()
    try:
        creds = await list_managed_credentials(client, org_id=org_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc
    return {"credentials": creds, "count": len(creds)}


@router.post("/api/ssh/credentials", status_code=201)
async def create_credential_endpoint(request: Request, data: dict):
    """
    Generate a new Ed25519 keypair and create an AWX Machine credential.

    Request body
    ────────────
    {
      "target_name":     "prod-webservers",        # free label
      "target_type":     "host|group|inventory|custom",
      "inventory_id":    3,
      "inventory_name":  "Production",
      "limit":           "webservers",             # ansible --limit value
      "org_id":          1,
      "org_name":        "Default",
      "ssh_username":    "ubuntu",
      "ttl_hours":       24                        # 0 = no expiry
    }

    Response
    ────────
    {
      "cred_id":    42,
      "public_key": "ssh-ed25519 AAAA… autoflow-managed",
      "fingerprint": "SHA256:…",
      "expires_at":  "2026-06-01T10:00:00+00:00"   # or null
    }

    The private key is sent directly to AWX and NEVER returned.
    """
    _audit(request, "ssh.create_credential", target=data.get("target_name"))

    required = (
        "target_name",
        "target_type",
        "inventory_id",
        "inventory_name",
        # "limit" is intentionally absent: empty string is valid for target_type=inventory
        "org_id",
        "org_name",
        "ssh_username",
    )
    missing = [f for f in required if not data.get(f) and data.get(f) != 0]
    if missing:
        raise HTTPException(422, f"Missing required fields: {missing}")

    target = TargetSpec(
        name=data["target_name"],
        type=data["target_type"],
        inventory_id=int(data["inventory_id"]),
        inventory_name=data["inventory_name"],
        limit=data.get("limit", ""),
        org_id=int(data["org_id"]),
        org_name=data["org_name"],
    )

    client = _awx()
    try:
        result = await create_credential(
            client,
            target=target,
            ssh_username=data["ssh_username"],
            ttl_hours=int(data.get("ttl_hours", _ttl_hours())),
        )
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    return result


@router.get("/api/ssh/credentials/{cred_id}")
async def get_credential_detail(cred_id: int):
    """
    Return details of a single Autoflow-managed credential.

    Includes: status, target, fingerprint, public_key, rotation policy, expiry.
    Never returns the private key.
    """
    client = _awx()
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} is not managed by Autoflow")

    from dataclasses import asdict

    return {
        "id": cred_id,
        "name": raw.get("name", ""),
        "meta": asdict(meta),
        "expired": is_expired(meta),
        "rotation_due": is_rotation_due(meta),
    }


@router.delete("/api/ssh/credentials/{cred_id}", status_code=204)
async def delete_credential_endpoint(request: Request, cred_id: int):
    """Delete an Autoflow-managed credential from AWX."""
    _audit(request, "ssh.delete_credential", cred_id=cred_id)

    client = _awx()
    # Guard: verify it's ours before deleting
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    if parse_cred_meta(raw) is None:
        raise HTTPException(403, "Cannot delete a credential not managed by Autoflow")

    try:
        await client.delete_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc


@router.post("/api/ssh/credentials/{cred_id}/activate")
async def force_activate_endpoint(request: Request, cred_id: int):
    """
    Force-activate a PENDING credential without SSH verification.

    Use when the target host is not yet reachable (e.g. not provisioned)
    but the public key has been manually placed in authorized_keys.

    ⚠ Records '[unverified]' in the fingerprint for audit purposes.
    """
    _audit(request, "ssh.force_activate", cred_id=cred_id)

    client = _awx()
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} not found or not managed")

    try:
        updated = await force_activate(client, cred_id, meta)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    from dataclasses import asdict

    return {
        "status": "activated",
        "warning": "Credential activated without SSH verification",
        "meta": asdict(updated),
    }


@router.patch("/api/ssh/credentials/{cred_id}/rotation-policy")
async def set_rotation_policy(request: Request, cred_id: int, data: dict):
    """
    Configure the time-based rotation policy for a credential.

    Request body: {"enabled": true, "days": 90}
    """
    _audit(request, "ssh.set_rotation_policy", cred_id=cred_id)

    enabled = bool(data.get("enabled", False))
    days = int(data.get("days", 90))

    client = _awx()
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} not found or not managed")

    try:
        updated = await update_rotation_policy(client, cred_id, meta, enabled=enabled, days=days)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    from dataclasses import asdict

    return {
        "status": "updated",
        "rotation_policy": asdict(updated.rotation_policy),
        "next_rotation_at": updated.rotation_policy.next_rotation_at,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Verification (SSE)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/ssh/credentials/{cred_id}/verify/stream")
async def verify_stream(cred_id: int):
    """
    SSE: launch the AWX SSH verify job for a PENDING credential and stream progress.

    Events are plain-text log lines. The last event starts with ``RESULT:``
    followed by a JSON summary. On success the credential is marked ACTIVE.
    """
    return _verify_stream(_awx(), cred_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Rotation
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/api/ssh/credentials/{cred_id}/rotate")
async def start_rotation_endpoint(request: Request, cred_id: int, data: dict | None = None):
    """
    Phase 1 — Generate a new keypair and create the "-rotating" credential.

    The old credential remains ACTIVE. Returns the new public key for the
    user to deploy to authorized_keys ALONGSIDE the existing key.

    Response: {new_cred_id, public_key, fingerprint, expires_at}
    """
    _audit(request, "ssh.start_rotation", cred_id=cred_id)

    ttl = int((data or {}).get("ttl_hours", _ttl_hours()))

    client = _awx()
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} not found or not managed")

    try:
        result = await start_rotation(client, cred_id, meta, ttl_hours=ttl)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    return result


@router.get("/api/ssh/credentials/{cred_id}/rotate/stream")
async def rotation_verify_stream(cred_id: int):
    """
    SSE: launch the AWX SSH verify job for the "-rotating" credential.

    Streams progress. On success the new credential is NOT yet promoted —
    call POST …/rotate/confirm to complete the rotation.
    """
    client = _awx()

    # Load original credential to find the rotating one
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} not found or not managed")
    if meta.status != "rotating":
        raise HTTPException(400, "No rotation in progress on this credential")
    if not meta.rotating_credential_id:
        raise HTTPException(500, "rotating_credential_id missing from metadata")

    # Verify the rotating credential (not the original)
    async def generate():
        new_id = meta.rotating_credential_id
        yield _sse(f"🔄 Verifying NEW key (rotating credential id={new_id})…")
        yield _sse(f"   Original credential id={cred_id} remains ACTIVE during this test")
        yield _sse("")

        async for chunk in _verify_stream_generator(client, new_id):
            yield chunk

        yield _sse("")
        yield _sse("   If verification succeeded: call POST …/rotate/confirm to complete")
        yield _sse("   If it failed: call DELETE …/rotate to cancel and keep the old key")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _verify_stream_generator(client: AWXClient, cred_id: int, poll_interval: float = 3.0):
    """Async generator yielding SSE lines for a verify job on cred_id."""
    import json as _json

    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        yield _sse(f"[ERROR] Cannot load credential id={cred_id}: {exc.detail}")
        yield _sse("[DONE]")
        return

    meta = parse_cred_meta(raw)
    if meta is None:
        yield _sse(f"[ERROR] Credential id={cred_id} is not managed by Autoflow")
        yield _sse("[DONE]")
        return

    yield _sse(f"🔑 Target  : {meta.target.name} ({meta.target.type})")
    yield _sse(f"   Inventory : {meta.target.inventory_name}")
    if meta.target.limit:
        yield _sse(f"   Limit     : {meta.target.limit}")
    yield _sse("")

    yield _sse("⚙  Ensuring SSH verify job template exists in AWX…")
    try:
        tpl_id = await client.ensure_verify_template()
        yield _sse(f"   Job template id={tpl_id} ✔")
    except AWXError as exc:
        yield _sse(f"[ERROR] Cannot provision verify template: {exc.detail}")
        yield _sse("[DONE]")
        return

    yield _sse("")
    yield _sse("🚀 Launching SSH verification job…")
    try:
        job_id = await launch_verify(client, cred_id, meta)
        yield _sse(f"   Job id={job_id} started ✔")
    except AWXError as exc:
        yield _sse(f"[ERROR] Launch failed: {exc.detail}")
        yield _sse("[DONE]")
        return

    yield _sse("")
    yield _sse("⏳ Waiting for job to complete…")
    last_status = ""
    result = {}
    while True:
        await asyncio.sleep(poll_interval)
        try:
            result = await get_verify_result(client, job_id)
        except AWXError as exc:
            yield _sse(f"[WARN] Poll error: {exc.detail} — retrying…")
            continue

        status = result["status"]
        if status != last_status:
            yield _sse(f"   Status: {status}")
            last_status = status

        if result["finished"]:
            break

    yield _sse("")
    if result.get("success"):
        yield _sse(f"✅ {result['message']}")
    else:
        yield _sse(f"❌ {result.get('message', 'Verification failed')}")

    yield f"data: RESULT:{_json.dumps(result)}\n\n"
    yield _sse("[DONE]")


@router.post("/api/ssh/credentials/{cred_id}/rotate/confirm")
async def confirm_rotation_endpoint(request: Request, cred_id: int, data: dict | None = None):
    """
    Phase 2 — Promote the new key, delete the old credential.

    Call this after the rotation verify stream reports success.
    Optionally pass {"verified_job_id": N} to record the job in metadata.
    """
    _audit(request, "ssh.confirm_rotation", cred_id=cred_id)

    job_id = int((data or {}).get("verified_job_id", 0)) or None

    client = _awx()
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} not found or not managed")

    try:
        new_meta = await confirm_rotation(client, cred_id, meta, verified_job_id=job_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    from dataclasses import asdict

    return {
        "status": "rotation_complete",
        "new_cred_id": meta.rotating_credential_id,
        "fingerprint": new_meta.fingerprint,
        "meta": asdict(new_meta),
    }


@router.delete("/api/ssh/credentials/{cred_id}/rotate", status_code=200)
async def cancel_rotation_endpoint(request: Request, cred_id: int):
    """
    Cancel an in-progress rotation.

    Deletes the new ("-rotating") credential and restores the original to ACTIVE.
    Safe to call if the new key was never deployed.
    """
    _audit(request, "ssh.cancel_rotation", cred_id=cred_id)

    client = _awx()
    try:
        raw = await client.get_credential(cred_id)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    meta = parse_cred_meta(raw)
    if meta is None:
        raise HTTPException(404, f"Credential id={cred_id} not found or not managed")

    try:
        updated = await cancel_rotation(client, cred_id, meta)
    except AWXError as exc:
        raise _awx_error_to_http(exc) from exc

    return {"status": "rotation_canceled", "cred_id": cred_id, "credential_status": updated.status}


# ═══════════════════════════════════════════════════════════════════════════════
# Background cleanup (called from main.py lifespan)
# ═══════════════════════════════════════════════════════════════════════════════


async def run_cleanup_task() -> None:
    """
    Periodic background task: delete PENDING credentials past their TTL.

    Runs every hour. Skipped if TTL is 0 (disabled) or AWX is unreachable.
    """
    while True:
        await asyncio.sleep(3600)
        ttl = _ttl_hours()
        if ttl <= 0:
            continue
        client = _awx()
        if not await client.ping():
            _log.debug("AWX unreachable — skipping PENDING cleanup")
            continue
        try:
            n = await cleanup_expired_pending(client)
            if n:
                _log.info("Cleanup: deleted %d expired PENDING credential(s)", n)
        except Exception as exc:
            _log.warning("PENDING cleanup error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy endpoints — global keypair (backward-compatible)
# ═══════════════════════════════════════════════════════════════════════════════


def _key_exists() -> bool:
    return _PRIVATE_KEY.exists() and _PUBLIC_KEY.exists()


def _fingerprint() -> str | None:
    if not _PUBLIC_KEY.exists():
        return None
    r = subprocess.run(
        ["ssh-keygen", "-l", "-E", "sha256", "-f", str(_PUBLIC_KEY)],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _read_pubkey() -> str | None:
    return _PUBLIC_KEY.read_text().strip() if _PUBLIC_KEY.exists() else None


def _generate_legacy_key(comment: str = "awx-provisioning@autoflow") -> None:
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for f in (_PRIVATE_KEY, _PUBLIC_KEY):
        if f.exists():
            f.unlink()
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-C", comment, "-f", str(_PRIVATE_KEY), "-N", ""],
        check=True,
        capture_output=True,
    )
    _PRIVATE_KEY.chmod(0o600)
    _PUBLIC_KEY.chmod(0o644)
    if not _SSH_CONFIG.exists():
        _SSH_CONFIG.write_text(_SSH_CONFIG_CONTENT)
        _SSH_CONFIG.chmod(0o600)
    if not _KNOWN_HOSTS.exists():
        _KNOWN_HOSTS.touch(mode=0o644)
    gitkeep = SSH_DIR / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()


@router.get("/api/ssh/status")
def ssh_status():
    exists = _key_exists()
    fp = _fingerprint() if exists else None
    pubkey = _read_pubkey() if exists else None
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
    if not _PUBLIC_KEY.exists():
        raise HTTPException(404, "SSH public key not found — run POST /api/ssh/generate first.")
    return {"public_key": _read_pubkey(), "path": str(_PUBLIC_KEY)}


@router.get("/api/ssh/generate")
async def ssh_generate(request: Request):
    """SSE: generate a new ed25519 key pair in autoflow/ssh/ (legacy global keypair)."""
    _audit(request, "ssh.generate")

    async def stream():
        yield _sse("=== SSH Key Generation (global keypair) ===")
        yield _sse(f"Target directory: {SSH_DIR}")

        if not shutil.which("ssh-keygen"):
            yield _sse("[ERROR] ssh-keygen is not available in this environment.")
            yield _sse("[DONE]")
            return

        if _PRIVATE_KEY.exists():
            ts = int(time.time())
            import shutil as _shutil

            bak_priv = SSH_DIR / f"id_ed25519.{ts}.bak"
            bak_pub = SSH_DIR / f"id_ed25519.pub.{ts}.bak"
            _shutil.copy2(_PRIVATE_KEY, bak_priv)
            _shutil.copy2(_PUBLIC_KEY, bak_pub)
            bak_priv.chmod(0o600)
            yield _sse(f"Existing key backed up → id_ed25519.{ts}.bak")
            yield _sse("⚠  Any VM provisioned with the OLD key will need re-provisioning.")

        yield _sse("Generating ed25519 key pair…")
        try:
            _generate_legacy_key()
        except subprocess.CalledProcessError as exc:
            yield _sse(f"[ERROR] ssh-keygen failed: {exc.stderr.decode() if exc.stderr else exc}")
            yield _sse("[DONE]")
            return

        yield _sse(f"Key generated ✔  Fingerprint: {_fingerprint()}")
        yield _sse(f"Public key: {_read_pubkey()}")
        yield _sse("")
        yield _sse("Next: sync the private key to AWX via /api/ssh/sync-awx")
        yield _sse("[SUCCESS] Done!")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/ssh/sync-awx")
async def ssh_sync_awx(request: Request):
    """SSE: push the global private key to a named AWX Machine credential."""
    _audit(request, "ssh.sync_awx")

    async def stream():
        import httpx as _httpx

        if not _PRIVATE_KEY.exists():
            yield _sse(f"[ERROR] No private key at {_PRIVATE_KEY}")
            yield _sse("[DONE]")
            return

        cfg = _load_env()
        awx_domain = cfg.get("DOMAIN", "localhost")
        awx_user = cfg.get("AWX_ADMIN_USER", "admin")
        awx_pass = cfg.get("AWX_ADMIN_PASSWORD", "")
        awx_url = f"https://awx.{awx_domain}"
        cred_name = cfg.get("AWX_SSH_CREDENTIAL_NAME", "lab-ssh-key")

        if not awx_pass:
            yield _sse("[ERROR] AWX_ADMIN_PASSWORD not set in .env")
            yield _sse("[DONE]")
            return

        private_key = _PRIVATE_KEY.read_text()
        yield _sse(f"=== Sync SSH key → AWX credential '{cred_name}' ===")

        async with _httpx.AsyncClient(verify=False, auth=(awx_user, awx_pass), timeout=20) as c:
            try:
                r = await c.get(f"{awx_url}/api/v2/ping/")
                if r.status_code != 200:
                    yield _sse(f"[ERROR] AWX /ping returned HTTP {r.status_code}")
                    yield _sse("[DONE]")
                    return
                yield _sse("AWX reachable ✔")
            except Exception as exc:
                yield _sse(f"[ERROR] Cannot reach AWX: {exc}")
                yield _sse("[DONE]")
                return

            r = await c.get(f"{awx_url}/api/v2/credentials/", params={"name": cred_name, "kind": "ssh"})
            results = r.json().get("results", []) if r.status_code == 200 else []

            if not results:
                yield _sse(f"[WARN] Credential '{cred_name}' not found in AWX.")
                yield _sse("[DONE]")
                return

            cred_id = results[0]["id"]
            pr = await c.patch(
                f"{awx_url}/api/v2/credentials/{cred_id}/", json={"inputs": {"ssh_key_data": private_key}}
            )
            if pr.status_code in (200, 204):
                yield _sse(f"Credential '{cred_name}' (id={cred_id}) updated ✔")
            else:
                yield _sse(f"[ERROR] PATCH failed (HTTP {pr.status_code}): {pr.text[:200]}")
                yield _sse("[DONE]")
                return

        yield _sse("[SUCCESS] AWX sync complete!")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/ssh/rotate")
async def ssh_rotate_legacy(request: Request):
    """SSE: rotate the global keypair + sync to AWX (legacy endpoint)."""
    _audit(request, "ssh.rotate")

    async def stream():
        yield _sse("=== SSH Key Rotation (global keypair) ===")

        if not shutil.which("ssh-keygen"):
            yield _sse("[ERROR] ssh-keygen not available.")
            yield _sse("[DONE]")
            return

        if _PRIVATE_KEY.exists():
            ts = int(time.time())
            import shutil as _shutil

            bak = SSH_DIR / f"id_ed25519.{ts}.bak"
            _shutil.copy2(_PRIVATE_KEY, bak)
            bak.chmod(0o600)
            yield _sse(f"Old key backed up → ssh/id_ed25519.{ts}.bak")
            old_fp = _fingerprint()
            yield _sse(f"Old fingerprint: {old_fp}")

        try:
            _generate_legacy_key()
        except subprocess.CalledProcessError as exc:
            yield _sse(f"[ERROR] Key generation failed: {exc}")
            yield _sse("[DONE]")
            return

        yield _sse(f"New key: {_fingerprint()}")
        yield _sse(f"Public : {_read_pubkey()}")
        yield _sse("")
        yield _sse("Run /api/ssh/sync-awx to push the new key to AWX.")
        yield _sse("Run: docker compose restart awx_task  to reload the mount.")
        yield _sse("[SUCCESS] Rotation complete!")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
