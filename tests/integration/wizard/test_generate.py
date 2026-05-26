"""
Integration tests — /api/generate/{type} endpoint.

Validates:
  - hex32  → 64-character lowercase hex string
  - hex64  → 128-character lowercase hex string
  - urlsafe32 → non-empty URL-safe base64 string
  - Unknown type → 400 error
  - Consecutive calls produce distinct values (randomness)
"""

from __future__ import annotations

import re

import pytest


@pytest.mark.integration
async def test_generate_hex32_status(client):
    resp = await client.get("/api/generate/hex32")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_generate_hex32_length(client):
    value = (await client.get("/api/generate/hex32")).json()["value"]
    assert len(value) == 64, f"hex32 should be 64 chars, got {len(value)}"


@pytest.mark.integration
async def test_generate_hex32_is_hex(client):
    value = (await client.get("/api/generate/hex32")).json()["value"]
    assert re.match(r"^[0-9a-f]+$", value), f"hex32 contains non-hex: {value!r}"


@pytest.mark.integration
async def test_generate_hex64_status(client):
    resp = await client.get("/api/generate/hex64")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_generate_hex64_length(client):
    value = (await client.get("/api/generate/hex64")).json()["value"]
    assert len(value) == 128, f"hex64 should be 128 chars, got {len(value)}"


@pytest.mark.integration
async def test_generate_hex64_is_hex(client):
    value = (await client.get("/api/generate/hex64")).json()["value"]
    assert re.match(r"^[0-9a-f]+$", value), f"hex64 contains non-hex: {value!r}"


@pytest.mark.integration
async def test_generate_urlsafe32_status(client):
    resp = await client.get("/api/generate/urlsafe32")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_generate_urlsafe32_non_empty(client):
    value = (await client.get("/api/generate/urlsafe32")).json()["value"]
    assert len(value) > 0


@pytest.mark.integration
async def test_generate_urlsafe32_charset(client):
    value = (await client.get("/api/generate/urlsafe32")).json()["value"]
    # URL-safe base64 uses A-Z a-z 0-9 _ -  (no + / = from standard base64)
    assert re.match(r"^[A-Za-z0-9_\-]+$", value), f"urlsafe32 contains unexpected chars: {value!r}"


@pytest.mark.integration
async def test_generate_returns_unique_values(client):
    v1 = (await client.get("/api/generate/hex32")).json()["value"]
    v2 = (await client.get("/api/generate/hex32")).json()["value"]
    assert v1 != v2, "Two consecutive token_hex(32) calls returned the same value"


@pytest.mark.integration
async def test_generate_response_has_value_key(client):
    body = (await client.get("/api/generate/hex32")).json()
    assert "value" in body


@pytest.mark.integration
async def test_generate_unknown_type_returns_400(client):
    resp = await client.get("/api/generate/md5")
    assert resp.status_code == 400


@pytest.mark.integration
async def test_generate_unknown_type_error_message(client):
    body = (await client.get("/api/generate/md5")).json()
    # FastAPI wraps HTTPException detail in {"detail": "..."}
    assert "detail" in body
