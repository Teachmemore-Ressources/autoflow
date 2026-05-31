# Autoflow — core/ssh_manager.py — Apache 2.0
"""
SSH credential lifecycle manager — multi-target, multi-organisation.

State machine
─────────────
  NONE      → generate()        → PENDING
  PENDING   → launch_verify()   → (job running, still PENDING)
  PENDING   → activate()        → ACTIVE   (job succeeded)
  PENDING   → force_activate()  → ACTIVE   (override, no verify)
  ACTIVE    → start_rotation()  → ROTATING (old cred kept, new cred "-rotating" created)
  ROTATING  → confirm_rotation()→ ACTIVE   (new promoted, old deleted)
  ROTATING  → cancel_rotation() → ACTIVE   (new deleted, old restored)

Rules
─────
- The wizard NEVER logs or returns the private key after creation.
- The private key is generated in Python memory and immediately POSTed to AWX;
  it is never written to disk.
- During rotation the old credential is preserved until the new one is verified.
- The credential description (JSON) is the single source of truth for state.
- All mutations update the AWX description atomically via PATCH.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from core.awx_client import AWXClient, AWXError

_log = logging.getLogger("autoflow.ssh_manager")

# ── Types ─────────────────────────────────────────────────────────────────────

Status = Literal["pending", "active", "rotating", "revoked"]
TargetType = Literal["host", "group", "inventory", "custom"]

_SCHEMA_VERSION = 1
_CRED_PREFIX = "autoflow-ssh"


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class RotationPolicy:
    enabled: bool = False
    days: int = 90
    next_rotation_at: str | None = None


@dataclass
class TargetSpec:
    name: str
    type: TargetType
    inventory_id: int
    inventory_name: str
    limit: str
    org_id: int
    org_name: str


@dataclass
class SSHCredMeta:
    """Metadata stored in the AWX credential description field (JSON)."""

    status: Status
    public_key: str
    fingerprint: str
    target: TargetSpec
    ssh_username: str
    created_at: str
    autoflow_managed: bool = True
    schema_version: int = _SCHEMA_VERSION
    pending_expires_at: str | None = None
    last_verified_at: str | None = None
    last_verified_job_id: int | None = None
    rotation_policy: RotationPolicy = field(default_factory=RotationPolicy)
    # id of the "-rotating" credential during a rotation
    rotating_credential_id: int | None = None


# ── Naming convention ─────────────────────────────────────────────────────────


def _cred_name(org_id: int, target_name: str) -> str:
    """AWX credential name for the active credential of a target."""
    safe = target_name.replace(" ", "-").lower()
    return f"{_CRED_PREFIX}-{org_id}-{safe}"


def _rotating_name(org_id: int, target_name: str) -> str:
    """AWX credential name used as mutex during rotation."""
    return f"{_cred_name(org_id, target_name)}-rotating"


# ── Serialization ─────────────────────────────────────────────────────────────


def _meta_to_json(meta: SSHCredMeta) -> str:
    d = asdict(meta)
    return json.dumps(d, default=str)


def _meta_from_dict(d: dict) -> SSHCredMeta | None:
    """Parse raw dict into SSHCredMeta. Returns None if not autoflow-managed."""
    if not d.get("autoflow_managed"):
        return None
    try:
        target_d = d["target"]
        target = TargetSpec(**target_d)
        rp_d = d.get("rotation_policy", {})
        rp = RotationPolicy(**rp_d) if rp_d else RotationPolicy()
        return SSHCredMeta(
            autoflow_managed=d.get("autoflow_managed", True),
            schema_version=d.get("schema_version", _SCHEMA_VERSION),
            status=d["status"],
            public_key=d["public_key"],
            fingerprint=d.get("fingerprint", ""),
            target=target,
            ssh_username=d.get("ssh_username", ""),
            created_at=d.get("created_at", ""),
            pending_expires_at=d.get("pending_expires_at"),
            last_verified_at=d.get("last_verified_at"),
            last_verified_job_id=d.get("last_verified_job_id"),
            rotation_policy=rp,
            rotating_credential_id=d.get("rotating_credential_id"),
        )
    except (KeyError, TypeError) as exc:
        _log.warning("Could not parse SSHCredMeta: %s — %s", exc, d)
        return None


def parse_cred_meta(awx_cred: dict) -> SSHCredMeta | None:
    """Extract and parse Autoflow metadata from an AWX credential object."""
    desc = awx_cred.get("description", "")
    if not desc:
        return None
    try:
        d = json.loads(desc)
    except json.JSONDecodeError:
        return None
    return _meta_from_dict(d)


# ── Keypair generation (in-memory, never written to disk) ─────────────────────


def generate_keypair() -> tuple[str, str]:
    """
    Generate an Ed25519 keypair entirely in memory.

    Returns
    -------
    private_pem : str  — OpenSSH private key (PEM format, no passphrase)
    public_openssh : str  — OpenSSH public key (one-liner, ``ssh-ed25519 AAAA…``)

    The private key is returned once and must be immediately POSTed to AWX.
    It is never stored anywhere in the wizard.
    """
    private_key = Ed25519PrivateKey.generate()

    private_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.OpenSSH,
        encryption_algorithm=NoEncryption(),
    ).decode()

    public_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )

    # Build OpenSSH public key wire format: [len][type][len][key]
    key_type = b"ssh-ed25519"
    encoded = _openssh_encode(key_type, public_bytes)
    public_openssh = f"ssh-ed25519 {base64.b64encode(encoded).decode()} autoflow-managed"

    return private_pem, public_openssh


def compute_fingerprint(public_openssh: str) -> str:
    """Return SHA-256 fingerprint of an OpenSSH public key (``SHA256:…`` format)."""
    # Extract the base64 blob (second word)
    parts = public_openssh.split()
    if len(parts) < 2:
        return ""
    try:
        raw = base64.b64decode(parts[1])
    except Exception:
        return ""
    digest = hashlib.sha256(raw).digest()
    fp = base64.b64encode(digest).decode().rstrip("=")
    return f"SHA256:{fp}"


def _openssh_encode(*parts: bytes) -> bytes:
    """Encode byte strings in OpenSSH wire format (uint32 length + data)."""
    import struct

    buf = b""
    for p in parts:
        buf += struct.pack(">I", len(p)) + p
    return buf


# ── TTL helpers ───────────────────────────────────────────────────────────────


def _pending_expiry(ttl_hours: int) -> str | None:
    if ttl_hours <= 0:
        return None
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    return expires.isoformat()


def is_expired(meta: SSHCredMeta) -> bool:
    """Return True if a PENDING credential has passed its TTL."""
    if meta.status != "pending" or not meta.pending_expires_at:
        return False
    expires = datetime.fromisoformat(meta.pending_expires_at)
    return datetime.now(timezone.utc) > expires


def is_rotation_due(meta: SSHCredMeta) -> bool:
    """Return True if a time-based rotation is overdue."""
    rp = meta.rotation_policy
    if not rp.enabled or not rp.next_rotation_at:
        return False
    next_rot = datetime.fromisoformat(rp.next_rotation_at)
    return datetime.now(timezone.utc) >= next_rot


# ── List ──────────────────────────────────────────────────────────────────────


async def list_managed_credentials(
    client: AWXClient,
    org_id: int | None = None,
) -> list[dict]:
    """
    Return all Autoflow-managed SSH credentials with their parsed metadata.

    Each item is a dict with keys: ``id``, ``name``, ``meta``, ``rotation_due``.
    Non-managed credentials (no autoflow_managed in description) are filtered out.
    """
    raw_creds = await client.list_credentials(org_id=org_id, kind="ssh")
    result = []
    for cred in raw_creds:
        meta = parse_cred_meta(cred)
        if meta is None:
            continue
        result.append(
            {
                "id": cred["id"],
                "name": cred["name"],
                "meta": asdict(meta),
                "expired": is_expired(meta),
                "rotation_due": is_rotation_due(meta),
            }
        )
    return result


# ── Create ────────────────────────────────────────────────────────────────────


async def create_credential(
    client: AWXClient,
    target: TargetSpec,
    ssh_username: str,
    ttl_hours: int = 24,
) -> dict:
    """
    Generate a new Ed25519 keypair and create an AWX Machine credential.

    Returns
    -------
    dict with keys:
        ``cred_id``      : AWX credential id
        ``public_key``   : OpenSSH public key string (display to user)
        ``fingerprint``  : SHA-256 fingerprint
        ``expires_at``   : ISO timestamp when PENDING expires (or null)

    The private key is NOT returned — it is sent directly to AWX and discarded.
    """
    # Check for existing credential (avoid accidental overwrite)
    existing = await client.list_credentials(
        org_id=target.org_id,
        name=_cred_name(target.org_id, target.name),
    )
    if existing:
        raise AWXError(  # noqa: E501
            409,
            f"Credential '{_cred_name(target.org_id, target.name)}' already exists."
            " Use rotate to change the key.",
        )

    # Generate keypair in memory
    private_pem, public_openssh = generate_keypair()
    fingerprint = compute_fingerprint(public_openssh)
    now = datetime.now(timezone.utc).isoformat()

    meta = SSHCredMeta(
        status="pending",
        public_key=public_openssh,
        fingerprint=fingerprint,
        target=target,
        ssh_username=ssh_username,
        created_at=now,
        pending_expires_at=_pending_expiry(ttl_hours),
    )

    cred = await client.create_credential(
        org_id=target.org_id,
        name=_cred_name(target.org_id, target.name),
        username=ssh_username,
        private_key=private_pem,
        description=_meta_to_json(meta),
    )
    cred_id = cred["id"]

    _log.info(
        "Created SSH credential id=%s name=%s org=%s target=%s status=pending",
        cred_id,
        _cred_name(target.org_id, target.name),
        target.org_id,
        target.name,
    )

    return {
        "cred_id": cred_id,
        "public_key": public_openssh,
        "fingerprint": fingerprint,
        "expires_at": meta.pending_expires_at,
    }


# ── Verify ────────────────────────────────────────────────────────────────────


async def launch_verify(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
) -> int:
    """
    Launch the SSH verify job for a credential in PENDING or ROTATING state.

    Returns the AWX job id (poll with get_verify_result()).
    """
    if meta.status not in ("pending", "rotating"):
        raise AWXError(400, f"Cannot verify credential in status '{meta.status}'")

    tpl_id = await client.ensure_verify_template()
    job_id = await client.launch_job(
        template_id=tpl_id,
        inventory_id=meta.target.inventory_id,
        credential_id=cred_id,
        limit=meta.target.limit,
    )
    _log.info("Launched verify job id=%s for credential id=%s", job_id, cred_id)
    return job_id


async def get_verify_result(client: AWXClient, job_id: int) -> dict:
    """
    Poll the verify job.

    Returns dict with:
        ``status``   : AWX job status (pending/running/successful/failed/error/canceled)
        ``finished`` : bool — True when job has reached a terminal state
        ``success``  : bool — True only if status == "successful" AND hosts matched
        ``message``  : human-readable summary
    """
    job = await client.get_job(job_id)
    status = job.get("status", "unknown")
    finished = status in ("successful", "failed", "error", "canceled")

    # Check for "no hosts matched" — AWX returns successful but with 0 tasks
    hosts_ok = int(job.get("hosts_with_active_failures", 0)) == 0
    no_hosts = finished and status == "successful" and int(job.get("elapsed", 0)) < 2

    success = finished and status == "successful" and not no_hosts and hosts_ok

    messages = {
        "pending": "Job queued…",
        "waiting": "Job waiting for capacity…",
        "running": "SSH verification in progress…",
        "successful": (
            "✅ SSH connection verified"
            if success
            else "⚠ Job succeeded but no hosts matched — check inventory limit"
        ),
        "failed": "❌ SSH connection failed — check public key is in authorized_keys",
        "error": "❌ Job error — check AWX logs",
        "canceled": "⚠ Job was canceled",
    }

    return {
        "status": status,
        "finished": finished,
        "success": success,
        "message": messages.get(status, f"Unknown status: {status}"),
        "job_id": job_id,
    }


# ── Activate ──────────────────────────────────────────────────────────────────


async def activate(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
    verified_job_id: int | None = None,
) -> SSHCredMeta:
    """Mark a PENDING credential as ACTIVE after successful verification."""
    if meta.status != "pending":
        raise AWXError(400, f"Cannot activate credential in status '{meta.status}'")

    now = datetime.now(timezone.utc).isoformat()
    meta.status = "active"
    meta.last_verified_at = now
    meta.last_verified_job_id = verified_job_id
    meta.pending_expires_at = None

    # Update rotation policy next_rotation if enabled
    if meta.rotation_policy.enabled and meta.rotation_policy.days > 0:
        next_rot = datetime.now(timezone.utc) + timedelta(days=meta.rotation_policy.days)
        meta.rotation_policy.next_rotation_at = next_rot.isoformat()

    await client.update_credential_description(cred_id, _meta_to_json(meta))
    _log.info("Credential id=%s activated (job=%s)", cred_id, verified_job_id)
    return meta


async def force_activate(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
) -> SSHCredMeta:
    """
    Force-activate a PENDING credential without SSH verification.

    Records a warning in the metadata. Allowed by design (D6).
    """
    if meta.status not in ("pending", "rotating"):
        raise AWXError(400, f"Cannot force-activate credential in status '{meta.status}'")

    now = datetime.now(timezone.utc).isoformat()
    meta.status = "active"
    meta.last_verified_at = None
    meta.last_verified_job_id = None
    meta.pending_expires_at = None
    # Store a sentinel so audit logs show this was not verified
    meta.fingerprint = f"[unverified] {meta.fingerprint}"

    await client.update_credential_description(cred_id, _meta_to_json(meta))
    _log.warning("Credential id=%s force-activated WITHOUT SSH verification at %s", cred_id, now)
    return meta


# ── Rotation ──────────────────────────────────────────────────────────────────


async def start_rotation(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
    ttl_hours: int = 24,
) -> dict:
    """
    Phase 1 of rotation: generate a new keypair and create a "-rotating" credential.

    The old credential is untouched — AWX jobs continue to use it.

    Returns the same dict as create_credential:
        ``new_cred_id``, ``public_key``, ``fingerprint``, ``expires_at``
    """
    if meta.status == "rotating":
        raise AWXError(409, "Rotation already in progress — cancel or confirm first")
    if meta.status != "active":
        raise AWXError(400, f"Can only rotate an ACTIVE credential, current status: '{meta.status}'")

    # Generate new keypair
    private_pem, public_openssh = generate_keypair()
    fingerprint = compute_fingerprint(public_openssh)
    now = datetime.now(timezone.utc).isoformat()

    # Create the -rotating credential (acts as distributed lock)
    rotating_name = _rotating_name(meta.target.org_id, meta.target.name)
    new_meta = SSHCredMeta(
        status="rotating",
        public_key=public_openssh,
        fingerprint=fingerprint,
        target=meta.target,
        ssh_username=meta.ssh_username,
        created_at=now,
        pending_expires_at=_pending_expiry(ttl_hours),
        rotation_policy=meta.rotation_policy,
    )

    new_cred = await client.create_credential(
        org_id=meta.target.org_id,
        name=rotating_name,
        username=meta.ssh_username,
        private_key=private_pem,
        description=_meta_to_json(new_meta),
    )
    new_cred_id = new_cred["id"]

    # Update the original credential to reference the rotating one
    meta.status = "rotating"
    meta.rotating_credential_id = new_cred_id
    await client.update_credential_description(cred_id, _meta_to_json(meta))

    _log.info(
        "Rotation started: original id=%s, new id=%s, target=%s",
        cred_id,
        new_cred_id,
        meta.target.name,
    )
    return {
        "new_cred_id": new_cred_id,
        "public_key": public_openssh,
        "fingerprint": fingerprint,
        "expires_at": new_meta.pending_expires_at,
    }


async def confirm_rotation(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
    verified_job_id: int | None = None,
) -> SSHCredMeta:
    """
    Phase 2 of rotation: promote the new credential, delete the old.

    - Renames the "-rotating" credential to the canonical name.
    - Updates its status to ACTIVE.
    - Deletes the old credential.
    """
    if meta.status != "rotating":
        raise AWXError(400, "No rotation in progress on this credential")
    if not meta.rotating_credential_id:
        raise AWXError(500, "rotating_credential_id missing from metadata")

    new_cred_id = meta.rotating_credential_id
    now = datetime.now(timezone.utc).isoformat()

    # Load the new credential metadata
    new_cred_raw = await client.get_credential(new_cred_id)
    new_meta = parse_cred_meta(new_cred_raw)
    if new_meta is None:
        raise AWXError(500, f"Cannot parse metadata from rotating credential id={new_cred_id}")

    # Promote: update name + mark ACTIVE
    new_meta.status = "active"
    new_meta.last_verified_at = now
    new_meta.last_verified_job_id = verified_job_id
    new_meta.pending_expires_at = None
    new_meta.rotating_credential_id = None

    if new_meta.rotation_policy.enabled and new_meta.rotation_policy.days > 0:
        next_rot = datetime.now(timezone.utc) + timedelta(days=new_meta.rotation_policy.days)
        new_meta.rotation_policy.next_rotation_at = next_rot.isoformat()

    canonical_name = _cred_name(meta.target.org_id, meta.target.name)
    await client._patch(
        f"/api/v2/credentials/{new_cred_id}/",
        {"name": canonical_name, "description": _meta_to_json(new_meta)},
    )

    # Delete old credential
    await client.delete_credential(cred_id)

    _log.info(
        "Rotation confirmed: old id=%s deleted, new id=%s promoted as '%s'",
        cred_id,
        new_cred_id,
        canonical_name,
    )
    return new_meta


async def cancel_rotation(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
) -> SSHCredMeta:
    """
    Cancel an in-progress rotation: delete the "-rotating" credential,
    restore the original to ACTIVE status.
    """
    if meta.status != "rotating":
        raise AWXError(400, "No rotation in progress on this credential")

    new_cred_id = meta.rotating_credential_id

    # Delete the new (rotating) credential if it still exists
    if new_cred_id:
        try:
            await client.delete_credential(new_cred_id)
            _log.info("Deleted rotating credential id=%s", new_cred_id)
        except AWXError as exc:
            _log.warning("Could not delete rotating credential id=%s: %s", new_cred_id, exc)

    # Restore original to ACTIVE
    meta.status = "active"
    meta.rotating_credential_id = None
    await client.update_credential_description(cred_id, _meta_to_json(meta))

    _log.info("Rotation canceled: credential id=%s restored to ACTIVE", cred_id)
    return meta


# ── Rotation policy ───────────────────────────────────────────────────────────


async def update_rotation_policy(
    client: AWXClient,
    cred_id: int,
    meta: SSHCredMeta,
    enabled: bool,
    days: int,
) -> SSHCredMeta:
    """Enable or disable time-based rotation for a credential."""
    if days < 1 or days > 365:
        raise AWXError(400, "Rotation interval must be between 1 and 365 days")

    meta.rotation_policy.enabled = enabled
    meta.rotation_policy.days = days

    if enabled and meta.status == "active" and meta.last_verified_at:
        last = datetime.fromisoformat(meta.last_verified_at)
        next_rot = last + timedelta(days=days)
        meta.rotation_policy.next_rotation_at = next_rot.isoformat()
    elif not enabled:
        meta.rotation_policy.next_rotation_at = None

    await client.update_credential_description(cred_id, _meta_to_json(meta))
    _log.info(
        "Rotation policy updated: cred_id=%s enabled=%s days=%s next=%s",
        cred_id,
        enabled,
        days,
        meta.rotation_policy.next_rotation_at,
    )
    return meta


# ── Cleanup ───────────────────────────────────────────────────────────────────


async def cleanup_expired_pending(
    client: AWXClient,
    org_id: int | None = None,
) -> int:
    """
    Delete all PENDING credentials that have passed their TTL.

    Called by the wizard background task (configurable via SSH_PENDING_TTL_HOURS).
    Returns the number of credentials deleted.
    """
    managed = await list_managed_credentials(client, org_id=org_id)
    deleted = 0
    for item in managed:
        if not item["expired"]:
            continue
        cred_id = item["id"]
        try:
            await client.delete_credential(cred_id)
            deleted += 1
            _log.info("Cleaned up expired PENDING credential id=%s name=%s", cred_id, item["name"])
        except AWXError as exc:
            _log.warning("Could not delete expired credential id=%s: %s", cred_id, exc)
    return deleted
