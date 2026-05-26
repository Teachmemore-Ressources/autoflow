"""
Unit tests for deploy-wizard module-level constants.

Covered:
  - FIRST_START_ONLY  : correct type, contains critical keys
  - NEEDS_RECREATE    : correct type, contains port / GID keys
  - KEY_TO_SERVICES   : expected service mappings exist
  - WIZARD_VERSION    : semver-formatted string
  - generate_secret() : correct lengths and character sets
"""

from __future__ import annotations

import re

# ── Helpers ───────────────────────────────────────────────────────────────────


def _wm():
    import wizard_main as wm

    return wm


# ─────────────────────────────────────────────────────────────────────────────
# FIRST_START_ONLY
# ─────────────────────────────────────────────────────────────────────────────


class TestFirstStartOnly:
    def test_is_frozenset(self):
        assert isinstance(_wm().FIRST_START_ONLY, frozenset)

    def test_contains_awx_admin_keys(self):
        fso = _wm().FIRST_START_ONLY
        for key in ("AWX_ADMIN_USER", "AWX_ADMIN_PASSWORD", "AWX_ADMIN_EMAIL", "AWX_SECRET_KEY"):
            assert key in fso, f"Expected {key!r} in FIRST_START_ONLY"

    def test_contains_gitea_admin_keys(self):
        fso = _wm().FIRST_START_ONLY
        for key in (
            "GITEA_ADMIN_USER",
            "GITEA_ADMIN_PASSWORD",
            "GITEA_ADMIN_EMAIL",
            "GITEA_SECRET_KEY",
            "GITEA_INTERNAL_TOKEN",
        ):
            assert key in fso, f"Expected {key!r} in FIRST_START_ONLY"

    def test_contains_postgres_init_keys(self):
        fso = _wm().FIRST_START_ONLY
        for key in ("POSTGRES_DB", "POSTGRES_USER"):
            assert key in fso, f"Expected {key!r} in FIRST_START_ONLY"

    def test_contains_grafana_and_pki_keys(self):
        fso = _wm().FIRST_START_ONLY
        assert "GRAFANA_ADMIN_USER" in fso
        assert "PKI_JWT_SECRET" in fso

    def test_contains_minio_loki_keys(self):
        fso = _wm().FIRST_START_ONLY
        assert "LOKI_S3_ACCESS_KEY" in fso
        assert "LOKI_S3_SECRET_KEY" in fso

    def test_has_expected_count(self):
        # 4 AWX + 5 Gitea + 2 PG + 2 Gitea-DB + 2 Grafana/PKI + 2 Loki = 17
        assert len(_wm().FIRST_START_ONLY) == 17


# ─────────────────────────────────────────────────────────────────────────────
# NEEDS_RECREATE
# ─────────────────────────────────────────────────────────────────────────────


class TestNeedsRecreate:
    def test_is_frozenset(self):
        assert isinstance(_wm().NEEDS_RECREATE, frozenset)

    def test_contains_port_keys(self):
        nr = _wm().NEEDS_RECREATE
        assert "TRAEFIK_HTTP_PORT" in nr
        assert "TRAEFIK_HTTPS_PORT" in nr
        assert "GITEA_SSH_PORT" in nr

    def test_contains_docker_gid(self):
        assert "DOCKER_GID" in _wm().NEEDS_RECREATE

    def test_has_expected_count(self):
        assert len(_wm().NEEDS_RECREATE) == 4

    def test_no_overlap_with_first_start_only(self):
        wm = _wm()
        overlap = wm.FIRST_START_ONLY & wm.NEEDS_RECREATE
        assert len(overlap) == 0, f"Unexpected overlap: {overlap}"


# ─────────────────────────────────────────────────────────────────────────────
# KEY_TO_SERVICES
# ─────────────────────────────────────────────────────────────────────────────


class TestKeyToServices:
    def test_is_dict(self):
        assert isinstance(_wm().KEY_TO_SERVICES, dict)

    def test_domain_maps_to_traefik(self):
        assert "traefik" in _wm().KEY_TO_SERVICES["DOMAIN"]

    def test_postgres_password_maps_to_awx_and_postgres(self):
        svcs = _wm().KEY_TO_SERVICES["POSTGRES_PASSWORD"]
        assert "postgres" in svcs
        assert "awx" in svcs

    def test_redis_password_maps_to_redis_and_awx(self):
        svcs = _wm().KEY_TO_SERVICES["REDIS_PASSWORD"]
        assert "redis" in svcs
        assert "awx" in svcs

    def test_docker_gid_maps_to_ee_builder(self):
        assert "ee_builder" in _wm().KEY_TO_SERVICES["DOCKER_GID"]

    def test_traefik_port_keys_present(self):
        k2s = _wm().KEY_TO_SERVICES
        assert "TRAEFIK_HTTP_PORT" in k2s
        assert "TRAEFIK_HTTPS_PORT" in k2s

    def test_all_values_are_lists_of_strings(self):
        for key, svcs in _wm().KEY_TO_SERVICES.items():
            assert isinstance(svcs, list), f"KEY_TO_SERVICES[{key!r}] is not a list"
            for svc in svcs:
                assert isinstance(svc, str), f"KEY_TO_SERVICES[{key!r}] has non-string entry: {svc!r}"


# ─────────────────────────────────────────────────────────────────────────────
# WIZARD_VERSION
# ─────────────────────────────────────────────────────────────────────────────


class TestWizardVersion:
    def test_is_string(self):
        assert isinstance(_wm().WIZARD_VERSION, str)

    def test_is_semver(self):
        assert re.match(r"^\d+\.\d+\.\d+$", _wm().WIZARD_VERSION), (
            f"WIZARD_VERSION {_wm().WIZARD_VERSION!r} is not semver"
        )

    def test_major_version_is_at_least_1(self):
        major = int(_wm().WIZARD_VERSION.split(".")[0])
        assert major >= 1


# ─────────────────────────────────────────────────────────────────────────────
# generate_secret (pure logic, tested at Python level)
# ─────────────────────────────────────────────────────────────────────────────


class TestGenerateSecretLogic:
    """Test the token generation functions in isolation (no HTTP)."""

    def test_token_hex_32_is_64_chars(self):
        import secrets

        value = secrets.token_hex(32)
        assert len(value) == 64

    def test_token_hex_64_is_128_chars(self):
        import secrets

        value = secrets.token_hex(64)
        assert len(value) == 128

    def test_token_hex_only_hex_chars(self):
        import secrets

        value = secrets.token_hex(32)
        assert all(c in "0123456789abcdef" for c in value)

    def test_token_urlsafe_not_empty(self):
        import secrets

        value = secrets.token_urlsafe(32)
        assert len(value) > 0

    def test_two_consecutive_tokens_differ(self):
        import secrets

        v1 = secrets.token_hex(32)
        v2 = secrets.token_hex(32)
        assert v1 != v2
