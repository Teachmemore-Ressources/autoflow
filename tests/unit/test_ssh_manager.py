# Autoflow — tests/unit/test_ssh_manager — Apache 2.0
"""
Unit tests for core/ssh_manager.py — pure logic, no AWX API calls.

All AWXClient calls are mocked. Tests cover:
  - Keypair generation + fingerprint
  - Metadata serialization round-trip
  - State machine transitions
  - TTL expiry helpers
  - Rotation due detection
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add deploy-wizard to path via conftest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "deploy-wizard"))

from core.ssh_manager import (
    RotationPolicy,
    SSHCredMeta,
    TargetSpec,
    _cred_name,
    _meta_from_dict,
    _meta_to_json,
    _pending_expiry,
    _rotating_name,
    activate,
    cancel_rotation,
    compute_fingerprint,
    confirm_rotation,
    create_credential,
    force_activate,
    generate_keypair,
    is_expired,
    is_rotation_due,
    parse_cred_meta,
    start_rotation,
    update_rotation_policy,
)
from core.awx_client import AWXError


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_target(
    name: str = "prod-web",
    org_id: int = 1,
    inventory_id: int = 3,
    limit: str = "webservers",
) -> TargetSpec:
    return TargetSpec(
        name=name,
        type="group",
        inventory_id=inventory_id,
        inventory_name="Production",
        limit=limit,
        org_id=org_id,
        org_name="Default",
    )


def _make_meta(status="pending", **kwargs) -> SSHCredMeta:
    target = _make_target()
    return SSHCredMeta(
        status=status,
        public_key="ssh-ed25519 AAAA test",
        fingerprint="SHA256:abc123",
        target=target,
        ssh_username="ubuntu",
        created_at="2026-05-31T10:00:00+00:00",
        **kwargs,
    )


def _mock_client(**overrides) -> MagicMock:
    client = MagicMock()
    client.list_credentials = AsyncMock(return_value=[])
    client.create_credential = AsyncMock(return_value={"id": 42})
    client.update_credential_description = AsyncMock()
    client.update_credential_key = AsyncMock()
    client.delete_credential = AsyncMock()
    client.get_credential = AsyncMock(return_value={"id": 99, "description": ""})
    client.ensure_verify_template = AsyncMock(return_value=7)
    client.launch_job = AsyncMock(return_value=101)
    client._patch = AsyncMock(return_value={})
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


# ── Naming convention ─────────────────────────────────────────────────────────


def test_cred_name_basic():
    assert _cred_name(1, "prod-web") == "autoflow-ssh-1-prod-web"


def test_cred_name_spaces_lowercased():
    assert _cred_name(2, "Prod Web Servers") == "autoflow-ssh-2-prod-web-servers"


def test_rotating_name():
    assert _rotating_name(1, "prod-web") == "autoflow-ssh-1-prod-web-rotating"


# ── Keypair generation ────────────────────────────────────────────────────────


def test_generate_keypair_returns_two_strings():
    priv, pub = generate_keypair()
    assert isinstance(priv, str)
    assert isinstance(pub, str)


def test_generate_keypair_private_key_format():
    priv, _ = generate_keypair()
    assert "BEGIN OPENSSH PRIVATE KEY" in priv


def test_generate_keypair_public_key_format():
    _, pub = generate_keypair()
    assert pub.startswith("ssh-ed25519 ")
    assert "autoflow-managed" in pub


def test_generate_keypair_each_call_is_unique():
    _, pub1 = generate_keypair()
    _, pub2 = generate_keypair()
    assert pub1 != pub2


def test_compute_fingerprint_format():
    _, pub = generate_keypair()
    fp = compute_fingerprint(pub)
    assert fp.startswith("SHA256:")
    assert len(fp) > 10


def test_compute_fingerprint_deterministic():
    _, pub = generate_keypair()
    assert compute_fingerprint(pub) == compute_fingerprint(pub)


def test_compute_fingerprint_different_keys():
    _, pub1 = generate_keypair()
    _, pub2 = generate_keypair()
    assert compute_fingerprint(pub1) != compute_fingerprint(pub2)


def test_compute_fingerprint_bad_input():
    assert compute_fingerprint("not a key") == ""


# ── Metadata serialization ────────────────────────────────────────────────────


def test_meta_round_trip():
    meta = _make_meta()
    serialized = _meta_to_json(meta)
    d = json.loads(serialized)
    restored = _meta_from_dict(d)
    assert restored is not None
    assert restored.status == meta.status
    assert restored.public_key == meta.public_key
    assert restored.target.name == meta.target.name
    assert restored.target.org_id == meta.target.org_id


def test_meta_from_dict_ignores_non_managed():
    d = {"status": "active", "public_key": "x"}
    assert _meta_from_dict(d) is None


def test_meta_from_dict_missing_key_returns_none():
    d = {"autoflow_managed": True}  # missing required fields
    assert _meta_from_dict(d) is None


def test_parse_cred_meta_empty_description():
    cred = {"description": ""}
    assert parse_cred_meta(cred) is None


def test_parse_cred_meta_invalid_json():
    cred = {"description": "not-json"}
    assert parse_cred_meta(cred) is None


def test_parse_cred_meta_valid():
    meta = _make_meta()
    cred = {"description": _meta_to_json(meta)}
    parsed = parse_cred_meta(cred)
    assert parsed is not None
    assert parsed.status == "pending"


# ── TTL helpers ───────────────────────────────────────────────────────────────


def test_pending_expiry_returns_none_for_zero_ttl():
    assert _pending_expiry(0) is None


def test_pending_expiry_returns_iso_string():
    exp = _pending_expiry(24)
    assert exp is not None
    dt = datetime.fromisoformat(exp)
    assert dt > datetime.now(timezone.utc)


def test_is_expired_active_credential():
    meta = _make_meta(status="active")
    assert not is_expired(meta)


def test_is_expired_pending_no_ttl():
    meta = _make_meta(status="pending", pending_expires_at=None)
    assert not is_expired(meta)


def test_is_expired_pending_future():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    meta = _make_meta(status="pending", pending_expires_at=future)
    assert not is_expired(meta)


def test_is_expired_pending_past():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    meta = _make_meta(status="pending", pending_expires_at=past)
    assert is_expired(meta)


# ── Rotation due ──────────────────────────────────────────────────────────────


def test_is_rotation_due_policy_disabled():
    meta = _make_meta(status="active")
    meta.rotation_policy = RotationPolicy(enabled=False)
    assert not is_rotation_due(meta)


def test_is_rotation_due_no_next_rotation():
    meta = _make_meta(status="active")
    meta.rotation_policy = RotationPolicy(enabled=True, days=90, next_rotation_at=None)
    assert not is_rotation_due(meta)


def test_is_rotation_due_future():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    meta = _make_meta(status="active")
    meta.rotation_policy = RotationPolicy(enabled=True, days=90, next_rotation_at=future)
    assert not is_rotation_due(meta)


def test_is_rotation_due_past():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    meta = _make_meta(status="active")
    meta.rotation_policy = RotationPolicy(enabled=True, days=90, next_rotation_at=past)
    assert is_rotation_due(meta)


# ── create_credential ─────────────────────────────────────────────────────────


async def test_create_credential_calls_awx():
    client = _mock_client()
    target = _make_target()
    result = await create_credential(client, target, "ubuntu", ttl_hours=24)

    client.list_credentials.assert_awaited_once()
    client.create_credential.assert_awaited_once()
    assert result["cred_id"] == 42
    assert result["public_key"].startswith("ssh-ed25519")
    assert result["fingerprint"].startswith("SHA256:")


async def test_create_credential_raises_if_exists():
    client = _mock_client(
        list_credentials=AsyncMock(return_value=[{"id": 10, "name": "autoflow-ssh-1-prod-web"}])
    )
    target = _make_target()
    with pytest.raises(AWXError) as exc:
        await create_credential(client, target, "ubuntu")
    assert exc.value.status == 409


async def test_create_credential_no_ttl():
    client = _mock_client()
    target = _make_target()
    result = await create_credential(client, target, "ubuntu", ttl_hours=0)
    assert result["expires_at"] is None


# ── activate ─────────────────────────────────────────────────────────────────


async def test_activate_sets_status_active():
    client = _mock_client()
    meta = _make_meta(status="pending")
    updated = await activate(client, 42, meta, verified_job_id=101)

    assert updated.status == "active"
    assert updated.last_verified_job_id == 101
    assert updated.pending_expires_at is None
    client.update_credential_description.assert_awaited_once()


async def test_activate_wrong_status_raises():
    client = _mock_client()
    meta = _make_meta(status="active")
    with pytest.raises(AWXError) as exc:
        await activate(client, 42, meta)
    assert exc.value.status == 400


async def test_activate_sets_next_rotation_when_policy_enabled():
    client = _mock_client()
    meta = _make_meta(status="pending")
    meta.rotation_policy = RotationPolicy(enabled=True, days=30)
    updated = await activate(client, 42, meta)

    assert updated.rotation_policy.next_rotation_at is not None
    dt = datetime.fromisoformat(updated.rotation_policy.next_rotation_at)
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((dt - expected).total_seconds()) < 5


# ── force_activate ────────────────────────────────────────────────────────────


async def test_force_activate_marks_active():
    client = _mock_client()
    meta = _make_meta(status="pending")
    updated = await force_activate(client, 42, meta)

    assert updated.status == "active"
    assert "[unverified]" in updated.fingerprint
    client.update_credential_description.assert_awaited_once()


async def test_force_activate_wrong_status_raises():
    client = _mock_client()
    meta = _make_meta(status="active")
    with pytest.raises(AWXError):
        await force_activate(client, 42, meta)


# ── start_rotation ────────────────────────────────────────────────────────────


async def test_start_rotation_creates_new_credential():
    client = _mock_client()
    meta = _make_meta(status="active")
    result = await start_rotation(client, 10, meta, ttl_hours=24)

    assert result["new_cred_id"] == 42
    assert result["public_key"].startswith("ssh-ed25519")
    client.create_credential.assert_awaited_once()
    client.update_credential_description.assert_awaited_once()


async def test_start_rotation_wrong_status_raises():
    client = _mock_client()
    meta = _make_meta(status="pending")
    with pytest.raises(AWXError) as exc:
        await start_rotation(client, 10, meta)
    assert exc.value.status == 400


async def test_start_rotation_already_rotating_raises():
    client = _mock_client()
    meta = _make_meta(status="rotating")
    with pytest.raises(AWXError) as exc:
        await start_rotation(client, 10, meta)
    assert exc.value.status == 409


# ── cancel_rotation ───────────────────────────────────────────────────────────


async def test_cancel_rotation_restores_active():
    client = _mock_client()
    meta = _make_meta(status="rotating", rotating_credential_id=99)
    updated = await cancel_rotation(client, 10, meta)

    assert updated.status == "active"
    assert updated.rotating_credential_id is None
    client.delete_credential.assert_awaited_once_with(99)
    client.update_credential_description.assert_awaited_once()


async def test_cancel_rotation_wrong_status_raises():
    client = _mock_client()
    meta = _make_meta(status="active")
    with pytest.raises(AWXError):
        await cancel_rotation(client, 10, meta)


async def test_cancel_rotation_tolerates_missing_new_cred():
    """If the new credential was already deleted, cancel should not crash."""
    client = _mock_client(
        delete_credential=AsyncMock(side_effect=AWXError(404, "not found"))
    )
    meta = _make_meta(status="rotating", rotating_credential_id=99)
    updated = await cancel_rotation(client, 10, meta)
    assert updated.status == "active"


# ── confirm_rotation ──────────────────────────────────────────────────────────


async def test_confirm_rotation_promotes_new_deletes_old():
    new_meta = _make_meta(status="rotating")
    new_meta_json = _meta_to_json(new_meta)

    client = _mock_client(
        get_credential=AsyncMock(
            return_value={"id": 99, "description": new_meta_json}
        )
    )
    meta = _make_meta(status="rotating", rotating_credential_id=99)
    updated = await confirm_rotation(client, 10, meta, verified_job_id=200)

    assert updated.status == "active"
    assert updated.last_verified_job_id == 200
    client.delete_credential.assert_awaited_once_with(10)
    client._patch.assert_awaited_once()


async def test_confirm_rotation_wrong_status_raises():
    client = _mock_client()
    meta = _make_meta(status="active")
    with pytest.raises(AWXError):
        await confirm_rotation(client, 10, meta)


# ── update_rotation_policy ────────────────────────────────────────────────────


async def test_update_rotation_policy_enable():
    client = _mock_client()
    meta = _make_meta(
        status="active",
        last_verified_at="2026-05-31T10:00:00+00:00",
    )
    updated = await update_rotation_policy(client, 42, meta, enabled=True, days=30)

    assert updated.rotation_policy.enabled is True
    assert updated.rotation_policy.days == 30
    assert updated.rotation_policy.next_rotation_at is not None


async def test_update_rotation_policy_disable():
    client = _mock_client()
    meta = _make_meta(status="active")
    meta.rotation_policy = RotationPolicy(
        enabled=True, days=30, next_rotation_at="2026-12-01T00:00:00+00:00"
    )
    updated = await update_rotation_policy(client, 42, meta, enabled=False, days=30)

    assert updated.rotation_policy.enabled is False
    assert updated.rotation_policy.next_rotation_at is None


async def test_update_rotation_policy_invalid_days():
    client = _mock_client()
    meta = _make_meta(status="active")
    with pytest.raises(AWXError) as exc:
        await update_rotation_policy(client, 42, meta, enabled=True, days=0)
    assert exc.value.status == 400

    with pytest.raises(AWXError):
        await update_rotation_policy(client, 42, meta, enabled=True, days=366)
