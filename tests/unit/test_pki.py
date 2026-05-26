# Autoflow — tests/unit/test_pki — Apache 2.0
"""
Unit tests for the PKI service (services/pki/app/main.py).

Coverage:
- Utility helpers: _parse_dt, _serial_hex, _cert_status
- Password helpers: _hash_password, _verify_password
- User bootstrap: bootstrap_admin
- JWT: create_token, get_current_user
- LDAP role resolution: _ldap_resolve_role (pure, no server needed)
- LDAP auth mock: _ldap_authenticate with ldap3 MOCK_SYNC strategy
- Certificate issuance: _issue_cert_logic (with in-memory CA files)
- Certificate revocation: _revoke_cert_logic
- CRL generation and verification: _generate_crl

No Docker / external server required.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Module loading ────────────────────────────────────────────────────────────

_PKI_APP = Path(__file__).parent.parent.parent / "services" / "pki" / "app"


@pytest.fixture(scope="module")
def pki_data(tmp_path_factory):
    """Session-scoped temp data dir for the PKI service."""
    return tmp_path_factory.mktemp("pki_data")


@pytest.fixture(scope="module")
def pki(pki_data):
    """
    Load services/pki/app/main.py once per test module.

    We mock:
    - ``tracing``              — OTel instrumentation (no exporter in tests)
    - ``prometheus_client``    — avoid re-registration errors when multiple
                                 test modules load the module
    - The Prometheus metric classes — replaced with no-op MagicMocks

    IMPORTANT: sys.modules mocks use *manual save/restore*, NOT patch.dict.

    patch.dict(sys.modules, ...) takes a full snapshot of sys.modules on
    __enter__ and does _clear_dict + update(snapshot) on __exit__.  Any
    module imported during exec_module (fastapi, cryptography, ldap3 …) is
    NOT in the snapshot and therefore gets wiped when the context exits.
    After the fixture returns, ``fastapi`` is absent from sys.modules, so the
    next ``from fastapi import HTTPException`` reimports it and creates a
    brand-new class — different id from pki.HTTPException.  The same identity
    break affects cryptography.x509.Name, causing the
    ``isinstance(ca_cert.subject, Name)`` check inside _issue_cert_logic to
    return False.  Manual save/restore of only the two mocked keys avoids this.
    """
    alias = "pki_main_test"
    if alias in sys.modules:
        return sys.modules[alias]

    # Prometheus stubs — Gauge, Counter, Histogram must return objects
    # that support .set(), .inc(), .observe(), and .labels()
    def _metric_stub(*args, **kwargs):
        m = MagicMock()
        m.labels.return_value = m
        return m

    prometheus_mock = MagicMock()
    prometheus_mock.Gauge.side_effect = _metric_stub
    prometheus_mock.Counter.side_effect = _metric_stub
    prometheus_mock.Histogram.side_effect = _metric_stub
    prometheus_mock.generate_latest.return_value = b""
    prometheus_mock.CONTENT_TYPE_LATEST = "text/plain"

    tracing_mock = MagicMock()
    tracing_mock.instrument_app = MagicMock()
    tracing_mock.setup_tracing = MagicMock()

    env = {
        "PKI_DATA_DIR": str(pki_data),
        "PKI_JWT_SECRET": "test-pki-secret-32chars-xxxxxxxxxxx",
        "PKI_ADMIN_PASSWORD": "AdminTestPass99",
        "PKI_ADMIN_USER": "admin",
    }

    spec = importlib.util.spec_from_file_location(alias, _PKI_APP / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod

    # Manually install the two mocks and save whatever was there before.
    # We do NOT use patch.dict so that fastapi, cryptography, ldap3, etc.
    # imported during exec_module stay in sys.modules after this fixture runs.
    _mock_keys = {"tracing": tracing_mock, "prometheus_client": prometheus_mock}
    _saved = {k: sys.modules.get(k) for k in _mock_keys}
    for k, v in _mock_keys.items():
        sys.modules[k] = v

    orig_path = sys.path[:]
    if str(_PKI_APP) not in sys.path:
        sys.path.insert(0, str(_PKI_APP))

    # patch.dict for os.environ is fine: exec_module only *reads* env vars,
    # so a full snapshot + restore of os.environ is safe here.
    with patch.dict("os.environ", env):
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = orig_path
            # Restore only the two mocked sys.modules entries; leave everything
            # else (fastapi, cryptography …) intact so class identities hold.
            for k, v in _saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    return mod


@pytest.fixture(autouse=True)
def reset_pki_db(pki, pki_data):
    """Reset DB + CA + certs dirs before every test that uses pki."""
    import shutil

    db_path = pki_data / "db.json"
    if db_path.exists():
        db_path.unlink()
    for subdir in ("ca", "certs", "crl"):
        d = pki_data / subdir
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    yield


# ── Helper: create a CA in memory ────────────────────────────────────────────


def _make_ca(pki, ca_name: str = "test-ca") -> str:
    """
    Create a CA using PKI's internal logic.
    Returns the CA serial hex.
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_dir = pki.CA_DIR

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Org"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Test CA"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    serial = pki._serial_hex(cert.serial_number)

    (ca_dir / f"{ca_name}_key.pem").write_bytes(key_pem)
    (ca_dir / f"{ca_name}_cert.pem").write_bytes(cert_pem)
    (ca_dir / f"{ca_name}_key.pem").chmod(0o600)

    return serial


def _db_with_ca(pki, ca_name: str = "test-ca") -> dict:
    """Return a DB dict that already contains the given CA entry."""
    serial = _make_ca(pki, ca_name)
    db = pki._empty_db()
    db["cas"][ca_name] = {
        "common_name": "Test CA",
        "organization": "Test Org",
        "country": "FR",
        "not_after": (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat(),
        "serial": serial,
        "fingerprint": "AABBCC",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return db


# ── _parse_dt ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_dt_naive(pki):
    """Naive datetimes must be returned with UTC tzinfo."""
    dt = pki._parse_dt("2025-01-01T12:00:00")
    assert dt.tzinfo is not None
    assert dt.year == 2025 and dt.month == 1 and dt.day == 1


@pytest.mark.unit
def test_parse_dt_aware(pki):
    """Timezone-aware ISO strings must be preserved."""
    dt = pki._parse_dt("2025-06-15T08:30:00+00:00")
    assert dt.tzinfo is not None
    assert dt.hour == 8 and dt.minute == 30


# ── _serial_hex ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_serial_hex_basic(pki):
    assert pki._serial_hex(255) == "FF"


@pytest.mark.unit
def test_serial_hex_zero(pki):
    assert pki._serial_hex(0) == "0"


@pytest.mark.unit
def test_serial_hex_large(pki):
    result = pki._serial_hex(0xDEADBEEF)
    assert result == "DEADBEEF"


# ── _cert_status ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cert_status_active(pki):
    now = datetime.now(timezone.utc)
    info = {"not_after": (now + timedelta(days=90)).isoformat()}
    db = {"revoked_serials": [], "revocations": {}}
    status, days = pki._cert_status("SN1", info, db, now)
    assert status == "active"
    assert days > 30


@pytest.mark.unit
def test_cert_status_warning(pki):
    now = datetime.now(timezone.utc)
    info = {"not_after": (now + timedelta(days=20)).isoformat()}
    db = {"revoked_serials": [], "revocations": {}}
    status, days = pki._cert_status("SN2", info, db, now)
    assert status == "warning"


@pytest.mark.unit
def test_cert_status_critical(pki):
    now = datetime.now(timezone.utc)
    info = {"not_after": (now + timedelta(days=5)).isoformat()}
    db = {"revoked_serials": [], "revocations": {}}
    status, days = pki._cert_status("SN3", info, db, now)
    assert status == "critical"


@pytest.mark.unit
def test_cert_status_expired(pki):
    now = datetime.now(timezone.utc)
    info = {"not_after": (now - timedelta(days=1)).isoformat()}
    db = {"revoked_serials": [], "revocations": {}}
    status, _ = pki._cert_status("SN4", info, db, now)
    assert status == "expired"


@pytest.mark.unit
def test_cert_status_revoked(pki):
    now = datetime.now(timezone.utc)
    info = {"not_after": (now + timedelta(days=200)).isoformat()}
    db = {"revoked_serials": ["SNREV"], "revocations": {}}
    status, _ = pki._cert_status("SNREV", info, db, now)
    assert status == "revoked"


# ── Password helpers ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_hash_and_verify_password(pki):
    hashed = pki._hash_password("secret123")
    assert pki._verify_password("secret123", hashed) is True
    assert pki._verify_password("wrongpassword", hashed) is False


@pytest.mark.unit
def test_verify_password_invalid_hash(pki):
    """Garbage hash must return False without exception."""
    result = pki._verify_password("anything", "not-a-valid-hash")
    assert result is False


# ── bootstrap_admin ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_bootstrap_admin_creates_file(pki, pki_data):
    """bootstrap_admin must create USERS_FILE with an admin entry."""
    users_file = pki_data / "users.json"
    if users_file.exists():
        users_file.unlink()

    pki.bootstrap_admin()
    assert users_file.exists()
    users = json.loads(users_file.read_text())
    assert "admin" in users
    assert users["admin"]["role"] == "admin"


@pytest.mark.unit
def test_bootstrap_admin_idempotent(pki, pki_data):
    """Calling bootstrap_admin twice must not overwrite an existing admin."""
    users_file = pki_data / "users.json"
    if users_file.exists():
        users_file.unlink()

    pki.bootstrap_admin()
    # Manually change the hash (simulates a password change)
    users = json.loads(users_file.read_text())
    original_hash = users["admin"]["password_hash"]
    pki.bootstrap_admin()  # second call
    users2 = json.loads(users_file.read_text())
    assert users2["admin"]["password_hash"] == original_hash


# ── JWT ───────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_create_token_contains_claims(pki):
    """create_token must include sub, role, exp, iat, jti."""
    import jwt

    token = pki.create_token("alice", "operator")
    payload = jwt.decode(token, pki.JWT_SECRET, algorithms=[pki.JWT_ALGORITHM])
    assert payload["sub"] == "alice"
    assert payload["role"] == "operator"
    assert "exp" in payload and "iat" in payload and "jti" in payload


@pytest.mark.unit
async def test_get_current_user_valid(pki):
    """A valid token must resolve to the correct user dict."""
    from fastapi.security import HTTPAuthorizationCredentials

    token = pki.create_token("bob", "admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = await pki.get_current_user(creds)
    assert user["sub"] == "bob"
    assert user["role"] == "admin"


@pytest.mark.unit
async def test_get_current_user_expired(pki):
    """An expired token must raise HTTP 401."""
    import jwt
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    payload = {
        "sub": "ghost",
        "role": "viewer",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        "iat": datetime.now(timezone.utc) - timedelta(hours=1),
        "jti": "expired-jti",
    }
    token = jwt.encode(payload, pki.JWT_SECRET, algorithm=pki.JWT_ALGORITHM)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        await pki.get_current_user(creds)
    assert exc_info.value.status_code == 401


@pytest.mark.unit
async def test_get_current_user_missing(pki):
    """No credentials must raise HTTP 401."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await pki.get_current_user(None)
    assert exc_info.value.status_code == 401


# ── LDAP role resolution ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_ldap_resolve_role_admin(pki):
    cfg = {
        "group_admin": "CN=PKI-Admins,OU=Groups,DC=example,DC=com",
        "group_operator": "CN=PKI-Operators,OU=Groups,DC=example,DC=com",
        "group_viewer": "CN=PKI-Viewers,OU=Groups,DC=example,DC=com",
    }
    groups = ["CN=PKI-Admins,OU=Groups,DC=example,DC=com"]
    assert pki._ldap_resolve_role(groups, cfg) == "admin"


@pytest.mark.unit
def test_ldap_resolve_role_operator(pki):
    cfg = {
        "group_admin": "CN=PKI-Admins,OU=Groups,DC=example,DC=com",
        "group_operator": "CN=PKI-Operators,OU=Groups,DC=example,DC=com",
        "group_viewer": "CN=PKI-Viewers,OU=Groups,DC=example,DC=com",
    }
    groups = ["CN=PKI-Operators,OU=Groups,DC=example,DC=com"]
    assert pki._ldap_resolve_role(groups, cfg) == "operator"


@pytest.mark.unit
def test_ldap_resolve_role_viewer(pki):
    cfg = {
        "group_admin": "CN=PKI-Admins,OU=Groups,DC=example,DC=com",
        "group_operator": "CN=PKI-Operators,OU=Groups,DC=example,DC=com",
        "group_viewer": "CN=PKI-Viewers,OU=Groups,DC=example,DC=com",
    }
    groups = ["CN=PKI-Viewers,OU=Groups,DC=example,DC=com"]
    assert pki._ldap_resolve_role(groups, cfg) == "viewer"


@pytest.mark.unit
def test_ldap_resolve_role_none(pki):
    cfg = {
        "group_admin": "CN=PKI-Admins,OU=Groups,DC=example,DC=com",
        "group_operator": "",
        "group_viewer": "",
    }
    groups = ["CN=SomeOtherGroup,OU=Groups,DC=example,DC=com"]
    assert pki._ldap_resolve_role(groups, cfg) is None


@pytest.mark.unit
def test_ldap_resolve_role_admin_wins_over_operator(pki):
    """Admin takes priority when user is in both groups."""
    cfg = {
        "group_admin": "CN=PKI-Admins,OU=Groups,DC=example,DC=com",
        "group_operator": "CN=PKI-Operators,OU=Groups,DC=example,DC=com",
        "group_viewer": "",
    }
    groups = [
        "CN=PKI-Admins,OU=Groups,DC=example,DC=com",
        "CN=PKI-Operators,OU=Groups,DC=example,DC=com",
    ]
    assert pki._ldap_resolve_role(groups, cfg) == "admin"


# ── LDAP authenticate (mock server) ──────────────────────────────────────────


@pytest.mark.unit
def test_ldap_authenticate_disabled(pki):
    """When LDAP is not enabled, _ldap_authenticate returns None immediately."""
    cfg = {"enabled": False, "url": "ldap://ldap.example.com"}
    with patch.object(pki, "load_ldap_config", return_value=cfg):
        result = pki._ldap_authenticate("alice", "password")
    assert result is None


@pytest.mark.unit
def test_ldap_authenticate_no_url(pki):
    """When LDAP URL is empty, _ldap_authenticate returns None immediately."""
    cfg = {"enabled": True, "url": ""}
    with patch.object(pki, "load_ldap_config", return_value=cfg):
        result = pki._ldap_authenticate("alice", "password")
    assert result is None


@pytest.mark.unit
def test_ldap_authenticate_invalid_credentials(pki):
    """
    When _ldap_get_user_dn returns None (user not found),
    _ldap_authenticate must return None.
    """
    cfg = {
        "enabled": True,
        "url": "ldap://ldap.example.com",
        "bind_dn": "cn=svc,dc=example,dc=com",
        "bind_password": "svcpass",
        "base_dn": "dc=example,dc=com",
        "user_filter": "(uid={username})",
        "group_base_dn": "",
        "group_admin": "",
        "group_operator": "",
        "group_viewer": "",
        "tls_ca_cert": "",
        "tls_verify": False,
        "starttls": False,
        "timeout": 5,
        "fallback_local": True,
        "mode": "ldap",
    }

    # Patch pki.Connection (the name captured at import time in the PKI
    # module's namespace), NOT ldap3.Connection — patching the ldap3 module
    # attribute has no effect because the PKI module already holds its own
    # local reference to Connection.
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(pki, "load_ldap_config", return_value=cfg),
        patch.object(pki, "_build_ldap_server", return_value=MagicMock()),
        patch.object(pki, "Connection", return_value=mock_conn),
        patch.object(pki, "_ldap_get_user_dn", return_value=None),
    ):
        result = pki._ldap_authenticate("nobody", "wrongpass")

    assert result is None


# ── _issue_cert_logic ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_issue_cert_logic_success(pki):
    """_issue_cert_logic must return a serial hex and write cert/key files."""
    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="test.example.com",
        domains=["test.example.com"],
        validity_days=90,
        cert_type="server",
        key_size=2048,
    )
    serial, _ = pki._issue_cert_logic(req, db, "test-user")
    assert serial
    assert (pki.CERTS_DIR / f"{serial}_cert.pem").exists()
    assert (pki.CERTS_DIR / f"{serial}_key.pem").exists()
    assert serial in db["certificates"]
    assert db["certificates"][serial]["common_name"] == "test.example.com"


@pytest.mark.unit
def test_issue_cert_logic_missing_ca(pki):
    """_issue_cert_logic must raise HTTP 404 when the CA does not exist."""
    from fastapi import HTTPException

    db = pki._empty_db()
    req = pki.CertCreateRequest(
        ca_name="nonexistent-ca",
        common_name="x.example.com",
        validity_days=90,
    )
    with pytest.raises(HTTPException) as exc_info:
        pki._issue_cert_logic(req, db, "admin")
    assert exc_info.value.status_code == 404


@pytest.mark.unit
def test_issue_cert_logic_wildcard(pki):
    """Wildcard certs must include *.base and base in their SAN list."""
    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="*.example.com",
        wildcard=True,
        validity_days=90,
    )
    serial, _ = pki._issue_cert_logic(req, db, "admin")
    info = db["certificates"][serial]
    sans = info["sans"]
    # sans entries are str(x509.DNSName(...)) → "<DNSName(value='...')>"
    assert any("*.example.com" in s for s in sans)
    # Base domain (without the wildcard prefix) must also be in the SAN list
    assert any("example.com" in s and "*.example.com" not in s for s in sans)


@pytest.mark.unit
def test_issue_cert_logic_with_ip(pki):
    """IP SANs must be stored in the cert record."""
    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="myservice",
        ips=["10.0.0.1", "192.168.1.10"],
        validity_days=30,
    )
    serial, _ = pki._issue_cert_logic(req, db, "admin")
    info = db["certificates"][serial]
    assert any("10.0.0.1" in s for s in info["sans"])


# ── _revoke_cert_logic ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_revoke_cert_logic_success(pki):
    """Revoking a cert must add it to revoked_serials and revocations."""
    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="revoke.example.com",
        validity_days=30,
    )
    serial, _ = pki._issue_cert_logic(req, db, "admin")

    pki._revoke_cert_logic(serial, "key_compromise", "test-admin", db)
    assert serial in db["revoked_serials"]
    assert db["revocations"][serial]["reason"] == "key_compromise"
    assert db["revocations"][serial]["revoked_by"] == "test-admin"


@pytest.mark.unit
def test_revoke_cert_logic_already_revoked(pki):
    """Revoking an already-revoked cert must raise HTTP 400."""
    from fastapi import HTTPException

    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="double.example.com",
        validity_days=30,
    )
    serial, _ = pki._issue_cert_logic(req, db, "admin")
    pki._revoke_cert_logic(serial, "unspecified", "admin", db)

    with pytest.raises(HTTPException) as exc_info:
        pki._revoke_cert_logic(serial, "unspecified", "admin", db)
    assert exc_info.value.status_code == 400


@pytest.mark.unit
def test_revoke_cert_logic_not_found(pki):
    """Revoking a non-existent serial must raise HTTP 404."""
    from fastapi import HTTPException

    db = pki._empty_db()
    with pytest.raises(HTTPException) as exc_info:
        pki._revoke_cert_logic("DEADBEEF", "unspecified", "admin", db)
    assert exc_info.value.status_code == 404


# ── _generate_crl ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_generate_crl_empty(pki):
    """CRL generation with no revocations must produce a valid DER file."""
    from cryptography import x509 as cx509

    db = _db_with_ca(pki, "test-ca")
    pki._generate_crl("test-ca", db)

    crl_path = pki.CRL_DIR / "test-ca.crl"
    assert crl_path.exists()
    # Parse the DER CRL to verify it's valid
    crl = cx509.load_der_x509_crl(crl_path.read_bytes())
    assert list(crl) == []  # no revoked certs


@pytest.mark.unit
def test_generate_crl_contains_revoked(pki):
    """After revoking a cert, the CRL must list that serial."""
    from cryptography import x509 as cx509

    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="crl.example.com",
        validity_days=30,
    )
    serial, _ = pki._issue_cert_logic(req, db, "admin")
    pki._revoke_cert_logic(serial, "key_compromise", "admin", db)
    pki._generate_crl("test-ca", db)

    crl_path = pki.CRL_DIR / "test-ca.crl"
    crl = cx509.load_der_x509_crl(crl_path.read_bytes())
    revoked_serials = [pki._serial_hex(r.serial_number) for r in crl]
    assert serial in revoked_serials


@pytest.mark.unit
def test_generate_crl_reason_preserved(pki):
    """The revocation reason must appear in the CRL entry."""
    from cryptography import x509 as cx509

    db = _db_with_ca(pki, "test-ca")
    req = pki.CertCreateRequest(
        ca_name="test-ca",
        common_name="reason.example.com",
        validity_days=30,
    )
    serial, _ = pki._issue_cert_logic(req, db, "admin")
    pki._revoke_cert_logic(serial, "superseded", "admin", db)
    pki._generate_crl("test-ca", db)

    crl_path = pki.CRL_DIR / "test-ca.crl"
    crl = cx509.load_der_x509_crl(crl_path.read_bytes())
    entry = next(r for r in crl if pki._serial_hex(r.serial_number) == serial)
    reason_ext = entry.extensions.get_extension_for_class(cx509.CRLReason)
    assert reason_ext.value.reason == cx509.ReasonFlags.superseded


# ── _generate_crl missing CA ──────────────────────────────────────────────────


@pytest.mark.unit
def test_generate_crl_missing_ca_files(pki):
    """_generate_crl must silently return (no exception) when CA files are absent."""
    db = pki._empty_db()
    # No CA files on disk — should not raise
    pki._generate_crl("nonexistent-ca", db)
    crl_path = pki.CRL_DIR / "nonexistent-ca.crl"
    assert not crl_path.exists()


# ── DB helpers ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_empty_db_structure(pki):
    """_empty_db must return a dict with required keys."""
    db = pki._empty_db()
    assert "certificates" in db
    assert "cas" in db
    assert "revoked_serials" in db
    assert "revocations" in db


@pytest.mark.unit
def test_load_db_returns_empty_when_no_file(pki, pki_data):
    """load_db must return _empty_db() when DB_FILE does not exist."""
    db_file = pki_data / "db.json"
    if db_file.exists():
        db_file.unlink()
    db = pki.load_db()
    assert db["certificates"] == {}
    assert db["cas"] == {}
