"""
E2E test fixtures — connect to a live docker-compose stack.

These tests are skipped unless the required environment variables are set.
Run them with:

    EE_URL=http://localhost:8001 \
    CALLBACK_URL=http://localhost:9999 \
    AWX_STUB_URL=http://localhost:8052 \
    pytest -m e2e tests/e2e/

Or via the Makefile:
    make test-e2e
"""
from __future__ import annotations

import os

import pytest
from httpx import AsyncClient


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        pytest.skip(f"E2E test skipped — {name} is not set")
    return val


# ── Base URL fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def ee_url() -> str:
    return _require_env("EE_URL")


@pytest.fixture(scope="session")
def awx_stub_url() -> str:
    return _require_env("AWX_STUB_URL")


@pytest.fixture(scope="session")
def callback_url() -> str:
    return _require_env("CALLBACK_URL")


# ── HTTP clients ──────────────────────────────────────────────────────────────

@pytest.fixture
async def ee(ee_url):
    """Async HTTP client pointing at the live event engine."""
    async with AsyncClient(base_url=ee_url, timeout=15.0) as client:
        yield client


@pytest.fixture
async def awx_stub(awx_stub_url):
    """Async HTTP client pointing at the AWX stub test-control endpoints."""
    async with AsyncClient(base_url=awx_stub_url, timeout=10.0) as client:
        await client.delete("/_test/reset")
        yield client


@pytest.fixture
async def callback(callback_url):
    """Async HTTP client pointing at the callback stub."""
    async with AsyncClient(base_url=callback_url, timeout=10.0) as client:
        await client.delete("/_test/reset")
        yield client
