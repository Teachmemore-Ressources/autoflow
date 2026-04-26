"""
Autoflow PKI Service v2.0 — Security hardened
"""
import os
import json
import time
import logging
import ipaddress
import fcntl
import re
import secrets
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, Depends
import ldap3
from ldap3 import Server, Connection, ALL, NTLM, SIMPLE, Tls, SUBTREE
from ldap3.core.exceptions import LDAPException, LDAPBindError, LDAPSocketOpenError
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, field_validator
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import CertificateRevocationListBuilder, RevokedCertificateBuilder
import jwt
import bcrypt
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

# ── Config ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("PKI_DATA_DIR", "/data/pki"))
CA_DIR   = DATA_DIR / "ca"
CERTS_DIR = DATA_DIR / "certs"
CRL_DIR   = DATA_DIR / "crl"
DB_FILE   = DATA_DIR / "db.json"
USERS_FILE = DATA_DIR / "users.json"

TRAEFIK_CERTS_DIR = Path(os.getenv("TRAEFIK_CERTS_DIR", "/traefik/certs"))
DOMAIN            = os.getenv("DOMAIN", "localhost")
# How many days before expiry to auto-renew the deployed wildcard cert
AUTO_RENEW_DAYS   = int(os.getenv("PKI_AUTO_RENEW_DAYS", "30"))
# Renewal validity (days) when auto-renewing
AUTO_RENEW_VALIDITY = int(os.getenv("PKI_AUTO_RENEW_VALIDITY", "365"))

# ── LDAP / Active Directory Configuration ────────────────────────────────────
#
# Mettre LDAP_ENABLED=true pour activer l'authentification LDAP/AD.
# Les comptes locaux (users.json) restent actifs en parallèle.
# En cas d'indisponibilité du serveur LDAP, le fallback local s'applique
# automatiquement si LDAP_FALLBACK_LOCAL=true.
#
# LDAP_MODE : "ldap" pour OpenLDAP/standard, "ad" pour Active Directory
#   - ldap : utilise uid={username} comme filtre de recherche par défaut
#   - ad   : utilise sAMAccountName={username} et supporte les groupes AD
#             avec recherche récursive (LDAP_MATCHING_RULE_IN_CHAIN)
#
# LDAP_URL : URL du serveur LDAP
#   - LDAP  standard  : ldap://ldap.mondomaine.com:389
#   - LDAPs (SSL/TLS) : ldaps://ldap.mondomaine.com:636  ← recommandé
#   - STARTTLS        : utiliser ldap:// + LDAP_STARTTLS=true
#
# LDAP_BIND_DN : DN du compte de service utilisé pour rechercher les utilisateurs
#   - OpenLDAP  : cn=svc-pki,ou=services,dc=mondomaine,dc=com
#   - Active Directory : svc-pki@mondomaine.com  (ou DOMAIN\svc-pki)
#
# LDAP_BASE_DN : Base de recherche des utilisateurs
#   Exemple : ou=users,dc=mondomaine,dc=com
#   Active Directory : CN=Users,DC=mondomaine,DC=com
#
# LDAP_USER_FILTER : Filtre de recherche de l'utilisateur
#   - OpenLDAP      : (uid={username})
#   - Active Directory : (sAMAccountName={username})
#   - AD avec UPN   : (|(sAMAccountName={username})(userPrincipalName={username}@mondomaine.com))
#
# LDAP_GROUP_BASE_DN : Base de recherche des groupes (peut différer de LDAP_BASE_DN)
#   Exemple AD : OU=Groups,DC=mondomaine,DC=com
#
# LDAP_GROUP_ADMIN / LDAP_GROUP_OPERATOR / LDAP_GROUP_VIEWER :
#   DN complet du groupe LDAP/AD mappé à chaque rôle PKI.
#   Les utilisateurs membres de plusieurs groupes obtiennent le rôle le plus élevé.
#   Exemple AD : CN=PKI-Admins,OU=Groups,DC=mondomaine,DC=com
#
# LDAP_TLS_CA_CERT : Chemin vers le certificat CA pour valider LDAPs.
#   Si le serveur LDAP utilise un certificat signé par votre PKI interne,
#   pointez ici vers le PEM de votre CA (ex: /data/pki/ca/autoflow_cert.pem).
#   Laisser vide pour utiliser les CA système (Let's Encrypt, etc.).
#
# LDAP_TLS_VERIFY : true = vérifie le certificat serveur (recommandé en prod)
#                   false = désactive la vérification (à éviter en production)
#
# LDAP_STARTTLS : true = upgrade STARTTLS sur une connexion ldap:// (port 389)
#   Alternative à LDAPs. Non compatible avec ldaps://.
#
# LDAP_TIMEOUT : Délai de connexion en secondes (défaut : 5)
#
# LDAP_FALLBACK_LOCAL : true = si LDAP injoignable, les comptes locaux restent actifs
#                       false = blocage total si LDAP est down (plus strict)

LDAP_CONFIG_FILE    = DATA_DIR / "ldap_config.json"

LDAP_ENABLED        = os.getenv("LDAP_ENABLED", "false").lower() == "true"
LDAP_MODE           = os.getenv("LDAP_MODE", "ldap")          # "ldap" | "ad"
LDAP_URL            = os.getenv("LDAP_URL", "")
LDAP_STARTTLS       = os.getenv("LDAP_STARTTLS", "false").lower() == "true"
LDAP_BIND_DN        = os.getenv("LDAP_BIND_DN", "")
LDAP_BIND_PASSWORD  = os.getenv("LDAP_BIND_PASSWORD", "")
LDAP_BASE_DN        = os.getenv("LDAP_BASE_DN", "")
LDAP_USER_FILTER    = os.getenv(
    "LDAP_USER_FILTER",
    "(sAMAccountName={username})" if os.getenv("LDAP_MODE", "ldap") == "ad" else "(uid={username})",
)
LDAP_GROUP_BASE_DN  = os.getenv("LDAP_GROUP_BASE_DN", "")
LDAP_GROUP_ADMIN    = os.getenv("LDAP_GROUP_ADMIN", "")
LDAP_GROUP_OPERATOR = os.getenv("LDAP_GROUP_OPERATOR", "")
LDAP_GROUP_VIEWER   = os.getenv("LDAP_GROUP_VIEWER", "")
LDAP_TLS_CA_CERT    = os.getenv("LDAP_TLS_CA_CERT", "")
LDAP_TLS_VERIFY     = os.getenv("LDAP_TLS_VERIFY", "true").lower() == "true"
LDAP_TIMEOUT        = int(os.getenv("LDAP_TIMEOUT", "5"))
LDAP_FALLBACK_LOCAL = os.getenv("LDAP_FALLBACK_LOCAL", "true").lower() == "true"

_jwt_secret_env = os.getenv("PKI_JWT_SECRET", "")
if not _jwt_secret_env:
    _jwt_secret_env = secrets.token_hex(32)
    print(
        "WARNING: PKI_JWT_SECRET not set — using ephemeral secret. "
        "All sessions will be lost on restart. Set PKI_JWT_SECRET in production.",
        flush=True,
    )
JWT_SECRET    = _jwt_secret_env
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("PKI_JWT_EXPIRE_HOURS", "8"))

ADMIN_USER         = os.getenv("PKI_ADMIN_USER", "admin")
ADMIN_PASSWORD_ENV = os.getenv("PKI_ADMIN_PASSWORD", "")

_passphrase_raw = os.getenv("PKI_KEY_PASSPHRASE", "")
KEY_PASSPHRASE: Optional[bytes] = _passphrase_raw.encode() if _passphrase_raw else None

_origins_raw = os.getenv("PKI_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",") if o.strip()] or [
    "http://localhost:8004",
    "http://127.0.0.1:8004",
]

PKI_BASE_URL = os.getenv("PKI_BASE_URL", "http://localhost:8004")

for _d in [CA_DIR, CERTS_DIR, CRL_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
    _d.chmod(0o750)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log       = logging.getLogger("pki")
audit_log = logging.getLogger("pki.audit")

from tracing import instrument_app, setup_tracing  # noqa: E402
setup_tracing("autoflow-pki")

# ── Auth ─────────────────────────────────────────────────────────────────────
def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password[:72].encode(), bcrypt.gensalt(rounds=12)).decode()

def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain[:72].encode(), hashed.encode())
    except Exception:
        return False

# ── LDAP config persistence ───────────────────────────────────────────────────

def _ldap_defaults() -> dict:
    """Return LDAP config built from environment variables (lowest priority)."""
    return {
        "enabled":        LDAP_ENABLED,
        "mode":           LDAP_MODE,
        "url":            LDAP_URL,
        "starttls":       LDAP_STARTTLS,
        "bind_dn":        LDAP_BIND_DN,
        "bind_password":  LDAP_BIND_PASSWORD,
        "base_dn":        LDAP_BASE_DN,
        "user_filter":    LDAP_USER_FILTER,
        "group_base_dn":  LDAP_GROUP_BASE_DN,
        "group_admin":    LDAP_GROUP_ADMIN,
        "group_operator": LDAP_GROUP_OPERATOR,
        "group_viewer":   LDAP_GROUP_VIEWER,
        "tls_ca_cert":    LDAP_TLS_CA_CERT,
        "tls_verify":     LDAP_TLS_VERIFY,
        "timeout":        LDAP_TIMEOUT,
        "fallback_local": LDAP_FALLBACK_LOCAL,
    }


def load_ldap_config() -> dict:
    """
    Return the effective LDAP configuration.
    File config (/data/pki/ldap_config.json) takes precedence over env vars.
    """
    cfg = _ldap_defaults()
    if LDAP_CONFIG_FILE.exists():
        try:
            saved = json.loads(LDAP_CONFIG_FILE.read_text())
            cfg.update(saved)
        except Exception as e:
            log.warning("Failed to read LDAP config file: %s", e)
    return cfg


def save_ldap_config(cfg: dict) -> None:
    """Persist LDAP configuration to file (password stored, chmod 600)."""
    LDAP_CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    LDAP_CONFIG_FILE.chmod(0o600)


# ── LDAP helpers ─────────────────────────────────────────────────────────────

def _build_ldap_server(cfg: dict) -> Server:
    """Build an ldap3 Server object from the given config dict."""
    import ssl
    url     = cfg["url"]
    use_ssl = url.lower().startswith("ldaps://")
    tls     = None

    if use_ssl or cfg.get("starttls"):
        ca_cert = cfg.get("tls_ca_cert", "")
        verify  = cfg.get("tls_verify", True)
        tls = Tls(
            validate=ssl.CERT_REQUIRED if verify else ssl.CERT_NONE,
            ca_certs_file=ca_cert if ca_cert and Path(ca_cert).exists() else None,
        )

    return Server(url, use_ssl=use_ssl, tls=tls,
                  connect_timeout=cfg.get("timeout", 5), get_info=ALL)


def _ldap_get_user_dn(conn: Connection, username: str, cfg: dict) -> Optional[str]:
    """Search for a user and return their full DN, or None if not found."""
    search_filter = cfg["user_filter"].replace(
        "{username}", ldap3.utils.conv.escape_filter_chars(username)
    )
    conn.search(
        search_base=cfg["base_dn"],
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=["distinguishedName", "dn", "memberOf"],
    )
    return conn.entries[0].entry_dn if conn.entries else None


def _ldap_get_user_groups(conn: Connection, user_dn: str, cfg: dict) -> list:
    """
    Return the list of group DNs the user belongs to.
    Active Directory : uses recursive memberOf (LDAP_MATCHING_RULE_IN_CHAIN).
    Standard LDAP    : searches groups where member=user_dn.
    """
    base = cfg.get("group_base_dn") or cfg["base_dn"]

    if cfg.get("mode") == "ad":
        escaped_dn = ldap3.utils.conv.escape_filter_chars(user_dn)
        f = f"(member:1.2.840.113556.1.4.1941:={escaped_dn})"
    else:
        escaped_dn  = ldap3.utils.conv.escape_filter_chars(user_dn)
        escaped_uid = ldap3.utils.conv.escape_filter_chars(user_dn.split(",")[0].split("=")[-1])
        f = f"(|(member={escaped_dn})(memberUid={escaped_uid}))"

    conn.search(base, f, search_scope=SUBTREE, attributes=["distinguishedName"])
    return [e.entry_dn for e in conn.entries]


def _ldap_resolve_role(groups: list, cfg: dict) -> Optional[str]:
    """
    Map group membership to a PKI role.
    Priority order: admin > operator > viewer.
    Returns None if the user is not in any mapped group.
    """
    def _match(dn: str) -> bool:
        return bool(dn) and any(g.lower() == dn.lower() for g in groups)

    if _match(cfg.get("group_admin",    "")): return "admin"
    if _match(cfg.get("group_operator", "")): return "operator"
    if _match(cfg.get("group_viewer",   "")): return "viewer"
    return None


def _ldap_authenticate(username: str, password: str) -> Optional[str]:
    """
    Authenticate *username/password* against the configured LDAP/AD server.
    Uses the persisted file config (merged with env vars).

    Returns the PKI role string on success, None if credentials are invalid
    or the user has no mapped group, raises RuntimeError if unreachable.
    """
    cfg = load_ldap_config()
    if not cfg.get("enabled") or not cfg.get("url"):
        return None

    try:
        server   = _build_ldap_server(cfg)
        starttls = cfg.get("starttls", False)

        # Step 1 — service account bind to search for the user
        with Connection(
            server,
            user=cfg["bind_dn"],
            password=cfg["bind_password"],
            authentication=SIMPLE,
            auto_bind=not starttls,
            raise_exceptions=True,
        ) as search_conn:
            if starttls:
                search_conn.start_tls()
                search_conn.bind()
            user_dn = _ldap_get_user_dn(search_conn, username, cfg)
            if not user_dn:
                log.info("LDAP: user '%s' not found in directory.", username)
                return None
            groups = _ldap_get_user_groups(search_conn, user_dn, cfg)

        # Step 2 — bind with user credentials to verify password
        with Connection(
            server,
            user=user_dn,
            password=password,
            authentication=SIMPLE,
            auto_bind=not starttls,
            raise_exceptions=True,
        ) as user_conn:
            if starttls:
                user_conn.start_tls()
                user_conn.bind()

        # Step 3 — resolve role from group membership
        role = _ldap_resolve_role(groups, cfg)
        if role is None:
            log.info("LDAP: '%s' authenticated but not in any mapped group. Groups: %s",
                     username, groups)
        return role

    except LDAPBindError:
        return None
    except LDAPSocketOpenError as e:
        raise RuntimeError(f"LDAP server unreachable: {e}") from e
    except LDAPException as e:
        raise RuntimeError(f"LDAP error: {e}") from e


bearer_scheme  = HTTPBearer(auto_error=False)

ROLES_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "ca:create", "ca:list",
        "cert:issue", "cert:list", "cert:revoke", "cert:renew",
        "cert:download_cert", "cert:download_bundle", "cert:download_key",
        "user:manage",
    },
    "operator": {
        "ca:list",
        "cert:issue", "cert:list", "cert:revoke", "cert:renew",
        "cert:download_cert", "cert:download_bundle",
    },
    "viewer": {
        "ca:list",
        "cert:list",
        "cert:download_cert", "cert:download_bundle",
    },
}


def load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return {}


def save_users(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2))
    USERS_FILE.chmod(0o600)


def bootstrap_admin() -> None:
    users = load_users()
    if ADMIN_USER in users:
        return
    password = ADMIN_PASSWORD_ENV
    if not password:
        password = secrets.token_urlsafe(24)
        log.warning("PKI_ADMIN_PASSWORD not set — auto-generated admin password: %s", password)
    users[ADMIN_USER] = {
        "password_hash": _hash_password(password),
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_users(users)
    log.info("Admin user '%s' initialised", ADMIN_USER)


def create_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_permission(permission: str):
    async def checker(user: dict = Depends(get_current_user)) -> dict:
        role = user.get("role", "viewer")
        if permission not in ROLES_PERMISSIONS.get(role, set()):
            raise HTTPException(status_code=403, detail=f"Permission '{permission}' required")
        return user
    return checker


# ── DB helpers ────────────────────────────────────────────────────────────────
def _empty_db() -> dict:
    return {"certificates": {}, "cas": {}, "revoked_serials": [], "revocations": {}}


@contextmanager
def locked_db():
    """Exclusive file-level lock + atomic write via rename."""
    lock_path = DB_FILE.with_suffix(".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            db = json.loads(DB_FILE.read_text()) if DB_FILE.exists() else _empty_db()
            db.setdefault("revocations", {})
            db.setdefault("revoked_serials", [])
            yield db
            tmp = DB_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(db, indent=2, default=str))
            tmp.replace(DB_FILE)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def load_db() -> dict:
    if DB_FILE.exists():
        db = json.loads(DB_FILE.read_text())
        db.setdefault("revocations", {})
        db.setdefault("revoked_serials", [])
        return db
    return _empty_db()


# ── Prometheus Metrics ────────────────────────────────────────────────────────
CERTS_TOTAL      = Gauge("pki_certificates_total", "Total certificates issued")
CERTS_ACTIVE     = Gauge("pki_certificates_active", "Active certificates")
CERTS_REVOKED    = Gauge("pki_certificates_revoked_total", "Revoked certificates")
CERTS_EXPIRED    = Gauge("pki_certificates_expired_total", "Expired certificates")
CERTS_EXP_7D     = Gauge("pki_certificates_expiring_7d", "Expiring within 7 days")
CERTS_EXP_30D    = Gauge("pki_certificates_expiring_30d", "Expiring within 30 days")
CERTS_EXP_90D    = Gauge("pki_certificates_expiring_90d", "Expiring within 90 days")
CA_EXPIRY_DAYS   = Gauge("pki_ca_expiry_days", "Days until CA expires", ["ca_name"])
CERT_ISSUE_CNT   = Counter("pki_certificate_issues_total", "Total issuance operations")
CERT_REVOKE_CNT  = Counter("pki_certificate_revocations_total", "Total revocations")
CERT_RENEW_CNT   = Counter("pki_certificate_renewals_total", "Total renewals")
CERT_DEPLOY_CNT  = Counter("pki_certificate_deployments_total", "Total Traefik deployments")
CERT_AUTORENEW_CNT = Counter("pki_certificate_autorenewals_total", "Total auto-renewals")
ISSUE_LATENCY    = Histogram("pki_issue_duration_seconds", "Certificate issuance latency")


def update_metrics() -> None:
    db  = load_db()
    now = datetime.now(timezone.utc)
    total = len(db["certificates"])
    revoked = len(db["revoked_serials"])
    expired = active = exp_7 = exp_30 = exp_90 = 0
    for sn, info in db["certificates"].items():
        exp = _parse_dt(info["not_after"])
        if sn in db["revoked_serials"]:
            continue
        if exp < now:
            expired += 1
        else:
            active += 1
            dl = (exp - now).days
            if dl <= 7:  exp_7  += 1
            if dl <= 30: exp_30 += 1
            if dl <= 90: exp_90 += 1
    CERTS_TOTAL.set(total)
    CERTS_ACTIVE.set(active)
    CERTS_REVOKED.set(revoked)
    CERTS_EXPIRED.set(expired)
    CERTS_EXP_7D.set(exp_7)
    CERTS_EXP_30D.set(exp_30)
    CERTS_EXP_90D.set(exp_90)
    for ca_name, ca_info in db["cas"].items():
        exp = _parse_dt(ca_info["not_after"])
        CA_EXPIRY_DAYS.labels(ca_name=ca_name).set(max(0, (exp - now).days))


def _metrics_loop() -> None:
    while True:
        try:
            update_metrics()
        except Exception as e:
            log.error("Metrics update error: %s", e)
        time.sleep(30)


# ── Crypto helpers ────────────────────────────────────────────────────────────
DOMAIN_RE = re.compile(
    r"^(\*\.)?([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)
SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
SERIAL_RE    = re.compile(r"^[0-9A-Fa-f]+$")

REVOCATION_REASONS = {
    "unspecified":            x509.ReasonFlags.unspecified,
    "key_compromise":         x509.ReasonFlags.key_compromise,
    "ca_compromise":          x509.ReasonFlags.ca_compromise,
    "affiliation_changed":    x509.ReasonFlags.affiliation_changed,
    "superseded":             x509.ReasonFlags.superseded,
    "cessation_of_operation": x509.ReasonFlags.cessation_of_operation,
    "privilege_withdrawn":    x509.ReasonFlags.privilege_withdrawn,
}


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _serial_hex(serial: int) -> str:
    return format(serial, "x").upper()


def _gen_rsa_key(size: int):
    return rsa.generate_private_key(public_exponent=65537, key_size=size, backend=default_backend())


def _save_key(key, path: Path) -> None:
    enc = (
        serialization.BestAvailableEncryption(KEY_PASSPHRASE)
        if KEY_PASSPHRASE
        else serialization.NoEncryption()
    )
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    )
    path.write_bytes(pem)
    path.chmod(0o600)


def _load_key(path: Path):
    raw = path.read_bytes()
    if KEY_PASSPHRASE:
        try:
            return serialization.load_pem_private_key(raw, password=KEY_PASSPHRASE)
        except Exception:
            log.warning("Could not decrypt %s with passphrase — trying unencrypted", path.name)
    return serialization.load_pem_private_key(raw, password=None)


def _export_key_unencrypted(path: Path) -> bytes:
    key = _load_key(path)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _generate_crl(ca_name: str, db: dict) -> None:
    ca_cert_path = CA_DIR / f"{ca_name}_cert.pem"
    ca_key_path  = CA_DIR / f"{ca_name}_key.pem"
    if not ca_cert_path.exists() or not ca_key_path.exists():
        return
    try:
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        ca_key  = _load_key(ca_key_path)
        now     = datetime.now(timezone.utc)
        builder = (
            CertificateRevocationListBuilder()
            .issuer_name(ca_cert.subject)
            .last_update(now)
            .next_update(now + timedelta(hours=24))
        )
        for serial_hex, rev_info in db.get("revocations", {}).items():
            if db["certificates"].get(serial_hex, {}).get("ca_name") != ca_name:
                continue
            try:
                reason   = REVOCATION_REASONS.get(rev_info.get("reason", "unspecified"), x509.ReasonFlags.unspecified)
                rev_date = _parse_dt(rev_info["revoked_at"])
                revoked  = (
                    RevokedCertificateBuilder()
                    .serial_number(int(serial_hex, 16))
                    .revocation_date(rev_date)
                    .add_extension(x509.CRLReason(reason), critical=False)
                    .build()
                )
                builder = builder.add_revoked_certificate(revoked)
            except Exception as e:
                log.error("CRL entry error for %s: %s", serial_hex, e)
        crl = builder.sign(ca_key, hashes.SHA256())
        crl_path = CRL_DIR / f"{ca_name}.crl"
        crl_path.write_bytes(crl.public_bytes(serialization.Encoding.DER))
        crl_path.chmod(0o644)
        log.info("CRL regenerated for CA %s", ca_name)
    except Exception as e:
        log.error("CRL generation failed for %s: %s", ca_name, e)


def _audit(request: Request, action: str, user: dict, resource: str = "", detail: str = "") -> None:
    audit_log.info(
        "action=%s user=%s role=%s ip=%s resource=%s detail=%r",
        action,
        user.get("sub", "?"),
        user.get("role", "?"),
        request.client.host if request.client else "?",
        resource,
        detail,
    )


def _cert_status(sn: str, info: dict, db: dict, now: datetime) -> tuple[str, int]:
    exp      = _parse_dt(info["not_after"])
    days     = max(0, (exp - now).days)
    revoked  = sn in db["revoked_serials"]
    expired  = exp < now
    if revoked:       status = "revoked"
    elif expired:     status = "expired"
    elif days <= 7:   status = "critical"
    elif days <= 30:  status = "warning"
    else:             status = "active"
    return status, days


# ── Traefik deploy helpers ────────────────────────────────────────────────────
def _traefik_certs_available() -> bool:
    """Return True if the Traefik certs dir is mounted and writable."""
    return TRAEFIK_CERTS_DIR.exists() and os.access(TRAEFIK_CERTS_DIR, os.W_OK)


def _deploy_to_traefik(serial: str, db: dict) -> dict:
    """
    Copy cert+key for *serial* into TRAEFIK_CERTS_DIR as the wildcard files.
    Marks serial as deployed in db (caller must persist via locked_db).
    Returns a dict with deployed file paths.
    """
    if not _traefik_certs_available():
        raise RuntimeError(
            f"Traefik certs dir '{TRAEFIK_CERTS_DIR}' is not accessible. "
            "Check that the volume is mounted in docker-compose.yml."
        )

    cert_src = CERTS_DIR / f"{serial}_cert.pem"
    key_src  = CERTS_DIR / f"{serial}_key.pem"

    if not cert_src.exists():
        raise FileNotFoundError(f"Cert file not found: {cert_src}")
    if not key_src.exists():
        raise FileNotFoundError(f"Key file not found: {key_src}")

    cert_dst = TRAEFIK_CERTS_DIR / f"wildcard.{DOMAIN}.crt"
    key_dst  = TRAEFIK_CERTS_DIR / f"wildcard.{DOMAIN}.key"

    # Also deploy CA cert so clients can optionally trust it
    cert_info = db["certificates"].get(serial, {})
    ca_name   = cert_info.get("ca_name")
    ca_src    = CA_DIR / f"{ca_name}_cert.pem" if ca_name else None

    # Write cert (chain = leaf + CA if available)
    leaf_pem = cert_src.read_bytes()
    if ca_src and ca_src.exists():
        chain_pem = leaf_pem + ca_src.read_bytes()
    else:
        chain_pem = leaf_pem
    cert_dst.write_bytes(chain_pem)
    cert_dst.chmod(0o644)

    # Write key (unencrypted — Traefik reads it directly)
    key_dst.write_bytes(_export_key_unencrypted(key_src))
    key_dst.chmod(0o600)

    # Persist deployed serial in db
    db.setdefault("deployed", {})
    db["deployed"]["traefik_wildcard"] = {
        "serial":      serial,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "cert_path":   str(cert_dst),
        "key_path":    str(key_dst),
        "domain":      DOMAIN,
    }

    log.info("Deployed cert %s → %s", serial, cert_dst)
    CERT_DEPLOY_CNT.inc()
    return {"cert": str(cert_dst), "key": str(key_dst)}


def _auto_renewal_check() -> None:
    """
    Check whether the currently deployed Traefik wildcard cert is within
    AUTO_RENEW_DAYS of expiry. If so, renew it and re-deploy automatically.
    Called from the background renewal loop.
    """
    if not _traefik_certs_available():
        log.debug("Auto-renewal: Traefik certs dir not available, skipping.")
        return

    db = load_db()
    deployed = db.get("deployed", {}).get("traefik_wildcard")
    if not deployed:
        log.debug("Auto-renewal: no deployed cert recorded, skipping.")
        return

    serial = deployed["serial"]
    cert_info = db["certificates"].get(serial)
    if not cert_info:
        log.warning("Auto-renewal: deployed serial %s not found in DB.", serial)
        return

    # Skip if already revoked
    if serial in db["revoked_serials"]:
        log.debug("Auto-renewal: deployed serial %s is revoked, skipping.", serial)
        return

    exp  = _parse_dt(cert_info["not_after"])
    days = (exp - datetime.now(timezone.utc)).days

    if days > AUTO_RENEW_DAYS:
        log.debug("Auto-renewal: cert %s expires in %d days — no action needed.", serial, days)
        return

    log.info(
        "Auto-renewal: cert %s expires in %d days (threshold=%d) — renewing.",
        serial, days, AUTO_RENEW_DAYS,
    )

    try:
        # Reuse renew logic inside a locked_db context
        with locked_db() as db2:
            old = db2["certificates"][serial]
            domains = [s[4:] for s in old.get("sans", []) if s.startswith("DNS:") and not s[4:].startswith("*.")]
            ips     = [s[3:] for s in old.get("sans", []) if s.startswith("IP:")]
            new_req = CertCreateRequest(
                ca_name=old["ca_name"],
                common_name=old["common_name"],
                organization=old.get("organization", "Autoflow"),
                domains=domains,
                ips=ips,
                validity_days=AUTO_RENEW_VALIDITY,
                wildcard=old.get("wildcard", False),
                cert_type=old.get("cert_type", "server"),
                key_size=old.get("key_size", 2048),
            )
            new_serial, _ = _issue_cert_logic(new_req, db2, "auto-renewal")

            # Revoke old cert
            if serial not in db2["revoked_serials"]:
                _revoke_cert_logic(serial, "superseded", "auto-renewal", db2)
                _generate_crl(old.get("ca_name"), db2)

            # Deploy new cert to Traefik
            _deploy_to_traefik(new_serial, db2)

        CERT_ISSUE_CNT.inc()
        CERT_REVOKE_CNT.inc()
        CERT_RENEW_CNT.inc()
        CERT_AUTORENEW_CNT.inc()
        update_metrics()
        log.info("Auto-renewal: cert %s renewed → %s and deployed to Traefik.", serial, new_serial)

    except Exception as e:
        log.error("Auto-renewal failed for serial %s: %s", serial, e)


def _auto_renewal_loop() -> None:
    """Background thread: check every 12 h for certs needing renewal."""
    # Initial delay so the service finishes starting up
    time.sleep(60)
    while True:
        try:
            _auto_renewal_check()
        except Exception as e:
            log.error("Auto-renewal loop error: %s", e)
        # Re-check every 12 hours
        time.sleep(43200)


# ── Input Models ──────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ROLES_PERMISSIONS:
            raise ValueError(f"role must be one of {list(ROLES_PERMISSIONS)}")
        return v

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not SAFE_NAME_RE.match(v) or len(v) > 64:
            raise ValueError("username must be alphanumeric with - and _ only (max 64 chars)")
        return v


class CACreateRequest(BaseModel):
    name: str
    common_name: str
    organization: str = "Autoflow"
    country: str = "FR"
    validity_days: int = 3650
    key_size: int = 4096

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not SAFE_NAME_RE.match(v) or len(v) > 64:
            raise ValueError("CA name must be alphanumeric with - and _ only (max 64 chars)")
        return v

    @field_validator("key_size")
    @classmethod
    def validate_key_size(cls, v: int) -> int:
        if v not in (2048, 3072, 4096):
            raise ValueError("key_size must be 2048, 3072 or 4096")
        return v

    @field_validator("validity_days")
    @classmethod
    def validate_validity(cls, v: int) -> int:
        if not (1 <= v <= 7300):
            raise ValueError("validity_days must be between 1 and 7300")
        return v


class CertCreateRequest(BaseModel):
    ca_name: str
    common_name: str
    domains: List[str] = []
    ips: List[str] = []
    organization: str = "Autoflow"
    country: str = "FR"
    validity_days: int = 365
    wildcard: bool = False
    cert_type: str = "server"
    key_size: int = 2048

    @field_validator("common_name")
    @classmethod
    def validate_cn(cls, v: str) -> str:
        if len(v) > 64:
            raise ValueError("CN must be <= 64 characters (X.509 limit)")
        return v

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, domains: List[str]) -> List[str]:
        for d in domains:
            if not DOMAIN_RE.match(d):
                raise ValueError(f"Invalid domain: {d!r}")
        return domains

    @field_validator("ips")
    @classmethod
    def validate_ips(cls, ips: List[str]) -> List[str]:
        for ip in ips:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ValueError(f"Invalid IP address: {ip!r}")
        return ips

    @field_validator("validity_days")
    @classmethod
    def validate_validity(cls, v: int) -> int:
        if not (1 <= v <= 825):
            raise ValueError("validity_days must be between 1 and 825 (browser limit)")
        return v

    @field_validator("cert_type")
    @classmethod
    def validate_cert_type(cls, v: str) -> str:
        if v not in ("server", "client", "both"):
            raise ValueError("cert_type must be 'server', 'client', or 'both'")
        return v

    @field_validator("key_size")
    @classmethod
    def validate_key_size(cls, v: int) -> int:
        if v not in (2048, 3072, 4096):
            raise ValueError("key_size must be 2048, 3072 or 4096")
        return v


class RevokeRequest(BaseModel):
    serial: str
    reason: str = "unspecified"

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        if v not in REVOCATION_REASONS:
            raise ValueError(f"reason must be one of {sorted(REVOCATION_REASONS)}")
        return v


class RenewRequest(BaseModel):
    serial: str
    validity_days: int = 365

    @field_validator("validity_days")
    @classmethod
    def validate_validity(cls, v: int) -> int:
        if not (1 <= v <= 825):
            raise ValueError("validity_days must be between 1 and 825")
        return v


# ── Middleware ────────────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]         = "DENY"
        response.headers["X-XSS-Protection"]        = "1; mode=block"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]       = "geolocation=(), microphone=(), camera=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ── Core logic (called within locked_db context) ──────────────────────────────
def _issue_cert_logic(req: CertCreateRequest, db: dict, issued_by: str) -> tuple[str, float]:
    if req.ca_name not in db["cas"]:
        raise HTTPException(404, f"CA '{req.ca_name}' not found")

    t0 = time.time()
    ca_key  = _load_key(CA_DIR / f"{req.ca_name}_key.pem")
    ca_cert = x509.load_pem_x509_certificate((CA_DIR / f"{req.ca_name}_cert.pem").read_bytes())

    key = _gen_rsa_key(req.key_size)
    now = datetime.now(timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME,      req.country),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, req.organization),
        x509.NameAttribute(NameOID.COMMON_NAME,       req.common_name),
    ])

    san_list: list = []
    if req.wildcard:
        base = req.common_name.lstrip("*").lstrip(".")
        if base and DOMAIN_RE.match(f"*.{base}"):
            san_list.append(x509.DNSName(f"*.{base}"))
            san_list.append(x509.DNSName(base))
    for d in req.domains:
        san_list.append(x509.DNSName(d))
    for ip in req.ips:
        san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))

    crl_url = f"{PKI_BASE_URL}/api/ca/{req.ca_name}/crl.der"

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=req.validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False)
        .add_extension(
            x509.CRLDistributionPoints([
                x509.DistributionPoint(
                    full_name=[x509.UniformResourceIdentifier(crl_url)],
                    relative_name=None, reasons=None, crl_issuer=None,
                )
            ]),
            critical=False,
        )
    )

    if san_list:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_list), critical=False)

    if req.cert_type == "client":
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
    elif req.cert_type == "both":
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
    else:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )

    cert       = builder.sign(ca_key, hashes.SHA256(), default_backend())
    serial_hex = _serial_hex(cert.serial_number)
    exp        = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=timezone.utc)

    _save_key(key, CERTS_DIR / f"{serial_hex}_key.pem")
    (CERTS_DIR / f"{serial_hex}_cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    db["certificates"][serial_hex] = {
        "common_name":      req.common_name,
        "organization":     req.organization,
        "ca_name":          req.ca_name,
        "not_before":       now.isoformat(),
        "not_after":        exp.isoformat(),
        "cert_type":        req.cert_type,
        "wildcard":         req.wildcard,
        "sans":             [str(s) for s in san_list],
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex().upper(),
        "issued_at":        now.isoformat(),
        "issued_by":        issued_by,
        "key_size":         req.key_size,
    }
    return serial_hex, t0


def _revoke_cert_logic(serial: str, reason: str, revoked_by: str, db: dict) -> None:
    if serial not in db["certificates"]:
        raise HTTPException(404, "Certificate not found")
    if serial in db["revoked_serials"]:
        raise HTTPException(400, "Already revoked")
    db["revoked_serials"].append(serial)
    db["revocations"][serial] = {
        "reason":     reason,
        "revoked_at": datetime.now(timezone.utc).isoformat(),
        "revoked_by": revoked_by,
    }


# ── App setup ─────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    bootstrap_admin()
    threading.Thread(target=_metrics_loop,      daemon=True).start()
    threading.Thread(target=_auto_renewal_loop, daemon=True).start()
    log.info("PKI Service v2.0 started — auto-renewal threshold: %d days", AUTO_RENEW_DAYS)
    yield


app = FastAPI(
    title="Autoflow PKI",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
instrument_app(app, "autoflow-pki")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": "Rate limit exceeded. Please slow down."}, status_code=429)


# ── Auth endpoints ────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest):
    ip   = request.client.host if request.client else "?"
    role = None
    auth_source = "local"

    # ── LDAP / AD authentication ──────────────────────────────────────────
    if LDAP_ENABLED:
        try:
            role = _ldap_authenticate(req.username, req.password)
            if role is not None:
                auth_source = f"ldap({LDAP_MODE})"
                log.info("Login[LDAP]: user=%s role=%s ip=%s", req.username, role, ip)
                token = create_token(req.username, role)
                return {
                    "access_token": token,
                    "token_type":   "bearer",
                    "expires_in":   JWT_EXPIRE_HOURS * 3600,
                    "role":         role,
                    "username":     req.username,
                    "auth_source":  auth_source,
                }
            # role is None → user not found or not in any group
            if role is None and not LDAP_FALLBACK_LOCAL:
                raise HTTPException(status_code=401, detail="Invalid credentials")
        except RuntimeError as ldap_err:
            # LDAP server unreachable
            log.warning("LDAP unavailable: %s — falling back to local auth.", ldap_err)
            if not LDAP_FALLBACK_LOCAL:
                raise HTTPException(
                    status_code=503,
                    detail=f"LDAP server unavailable and local fallback is disabled. {ldap_err}",
                )

    # ── Local authentication (fallback or LDAP disabled) ─────────────────
    users = load_users()
    user  = users.get(req.username)
    if not user or not _verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    log.info("Login[local]: user=%s role=%s ip=%s", req.username, user["role"], ip)
    token = create_token(req.username, user["role"])
    return {
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   JWT_EXPIRE_HOURS * 3600,
        "role":         user["role"],
        "username":     req.username,
        "auth_source":  "local",
    }


@app.get("/api/auth/me")
async def whoami(user: dict = Depends(get_current_user)):
    return {"username": user["sub"], "role": user["role"]}


@app.get("/api/ldap/config")
def ldap_get_config(user: dict = Depends(require_permission("user:manage"))):
    """Return the current LDAP configuration (password redacted)."""
    cfg = load_ldap_config()
    cfg["bind_password"] = "••••••••" if cfg.get("bind_password") else ""
    return cfg


@app.put("/api/ldap/config")
def ldap_save_config(
    cfg:     dict,
    request: Request,
    user:    dict = Depends(require_permission("user:manage")),
):
    """
    Persist the LDAP configuration to /data/pki/ldap_config.json.
    If bind_password is the placeholder '••••••••', the existing password is kept.
    """
    # Keep existing password if not changed
    if cfg.get("bind_password") == "••••••••":
        existing = load_ldap_config()
        cfg["bind_password"] = existing.get("bind_password", "")

    # Apply sensible defaults for missing fields
    cfg.setdefault("mode",           "ldap")
    cfg.setdefault("starttls",       False)
    cfg.setdefault("tls_verify",     True)
    cfg.setdefault("timeout",        5)
    cfg.setdefault("fallback_local", True)
    cfg.setdefault("user_filter",
        "(sAMAccountName={username})" if cfg.get("mode") == "ad" else "(uid={username})")

    save_ldap_config(cfg)
    _audit(request, "ldap.config.update", user, detail=f"enabled={cfg.get('enabled')}, url={cfg.get('url')}")
    return {"status": "saved", "enabled": cfg.get("enabled")}


@app.post("/api/ldap/test")
@limiter.limit("5/minute")
def ldap_test(request: Request, user: dict = Depends(require_permission("user:manage"))):
    """
    Test the LDAP connection using the service account credentials.
    Does NOT test user credentials — only verifies server reachability and bind.
    """
    cfg = load_ldap_config()

    if not cfg.get("enabled"):
        return {"success": False, "message": "LDAP est désactivé."}
    if not cfg.get("url"):
        return {"success": False, "message": "L'URL du serveur LDAP n'est pas configurée."}

    try:
        server   = _build_ldap_server(cfg)
        starttls = cfg.get("starttls", False)
        with Connection(
            server,
            user=cfg["bind_dn"],
            password=cfg["bind_password"],
            authentication=SIMPLE,
            auto_bind=not starttls,
            raise_exceptions=True,
        ) as conn:
            if starttls:
                conn.start_tls()
                conn.bind()
            conn.search(cfg["base_dn"], "(objectClass=*)",
                        search_scope=SUBTREE, size_limit=1, attributes=["dn"])
            entries = len(conn.entries)

        vendor = ""
        if server.info and server.info.vendor_name:
            vendor = str(server.info.vendor_name[0]) if isinstance(server.info.vendor_name, list) else str(server.info.vendor_name)

        return {
            "success":               True,
            "message":               f"Connexion réussie à {cfg['url']} — compte de service OK.",
            "server_info":           vendor or "n/a",
            "base_dn_entries_found": entries,
        }
    except LDAPBindError as e:
        return {"success": False, "message": f"Échec du bind (vérifiez Bind DN / mot de passe) : {e}"}
    except LDAPSocketOpenError as e:
        return {"success": False, "message": f"Serveur LDAP inaccessible ({cfg['url']}) : {e}"}
    except LDAPException as e:
        return {"success": False, "message": f"Erreur LDAP : {e}"}


# ── User management (admin only) ──────────────────────────────────────────────
@app.get("/api/users")
def list_users(user: dict = Depends(require_permission("user:manage"))):
    users = load_users()
    return [
        {"username": u, "role": info["role"], "created_at": info.get("created_at")}
        for u, info in users.items()
    ]


@app.post("/api/users")
def create_user(
    req: UserCreateRequest,
    request: Request,
    user: dict = Depends(require_permission("user:manage")),
):
    users = load_users()
    if req.username in users:
        raise HTTPException(400, f"User '{req.username}' already exists")
    users[req.username] = {
        "password_hash": _hash_password(req.password),
        "role":          req.role,
        "created_at":    datetime.now(timezone.utc).isoformat(),
    }
    save_users(users)
    _audit(request, "user.create", user, resource=req.username, detail=f"role={req.role}")
    return {"status": "created", "username": req.username, "role": req.role}


@app.delete("/api/users/{username}")
def delete_user(
    username: str,
    request: Request,
    user: dict = Depends(require_permission("user:manage")),
):
    if username == user["sub"]:
        raise HTTPException(400, "Cannot delete your own account")
    users = load_users()
    if username not in users:
        raise HTTPException(404, "User not found")
    del users[username]
    save_users(users)
    _audit(request, "user.delete", user, resource=username)
    return {"status": "deleted", "username": username}


# ── CA endpoints ──────────────────────────────────────────────────────────────
@app.post("/api/ca/create")
def create_ca(
    req: CACreateRequest,
    request: Request,
    user: dict = Depends(require_permission("ca:create")),
):
    with locked_db() as db:
        if req.name in db["cas"]:
            raise HTTPException(400, f"CA '{req.name}' already exists")

        key     = _gen_rsa_key(req.key_size)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME,      req.country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, req.organization),
            x509.NameAttribute(NameOID.COMMON_NAME,       req.common_name),
        ])
        now  = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=req.validity_days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256(), default_backend())
        )

        _save_key(key, CA_DIR / f"{req.name}_key.pem")
        (CA_DIR / f"{req.name}_cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        exp        = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=timezone.utc)
        serial_hex = _serial_hex(cert.serial_number)
        db["cas"][req.name] = {
            "common_name":  req.common_name,
            "organization": req.organization,
            "country":      req.country,
            "not_after":    exp.isoformat(),
            "serial":       serial_hex,
            "fingerprint":  cert.fingerprint(hashes.SHA256()).hex().upper(),
            "created_at":   now.isoformat(),
        }
        _generate_crl(req.name, db)

    update_metrics()
    _audit(request, "ca.create", user, resource=req.name)
    return {"status": "created", "ca_name": req.name, "serial": serial_hex}


@app.get("/api/ca/list")
def list_cas(user: dict = Depends(require_permission("ca:list"))):
    db  = load_db()
    now = datetime.now(timezone.utc)
    return [
        {**info, "name": name, "days_left": max(0, (_parse_dt(info["not_after"]) - now).days)}
        for name, info in db["cas"].items()
    ]


@app.get("/api/ca/{ca_name}/cert.pem")
def download_ca_cert(ca_name: str):
    if not SAFE_NAME_RE.match(ca_name):
        raise HTTPException(400, "Invalid CA name")
    f = CA_DIR / f"{ca_name}_cert.pem"
    if not f.exists():
        raise HTTPException(404, "CA not found")
    safe_name = ca_name.replace(" ", "_")
    return Response(
        content=f.read_bytes(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-ca.crt"'},
    )


@app.get("/api/ca/{ca_name}/crl.der")
def download_crl(ca_name: str):
    if not SAFE_NAME_RE.match(ca_name):
        raise HTTPException(400, "Invalid CA name")
    crl_path = CRL_DIR / f"{ca_name}.crl"
    if not crl_path.exists():
        raise HTTPException(404, "CRL not available")
    return Response(
        content=crl_path.read_bytes(),
        media_type="application/pkix-crl",
        headers={
            "Cache-Control":        "max-age=3600",
            "Content-Disposition":  f'attachment; filename="{ca_name}.crl"',
        },
    )


# ── Certificate endpoints ─────────────────────────────────────────────────────
@app.post("/api/certs/issue")
@limiter.limit("20/minute")
def issue_cert(
    req: CertCreateRequest,
    request: Request,
    user: dict = Depends(require_permission("cert:issue")),
):
    with locked_db() as db:
        serial_hex, t0 = _issue_cert_logic(req, db, user["sub"])

    CERT_ISSUE_CNT.inc()
    ISSUE_LATENCY.observe(time.time() - t0)
    update_metrics()
    _audit(request, "cert.issue", user, resource=serial_hex, detail=req.common_name)
    return {"status": "issued", "serial": serial_hex}


@app.get("/api/certs/list")
def list_certs(
    ca:     Optional[str] = None,
    status: Optional[str] = None,
    limit:  int = 100,
    offset: int = 0,
    user:   dict = Depends(require_permission("cert:list")),
):
    limit = min(limit, 500)
    db    = load_db()
    now   = datetime.now(timezone.utc)
    result = []
    for sn, info in db["certificates"].items():
        s, days = _cert_status(sn, info, db, now)
        if ca     and info.get("ca_name") != ca: continue
        if status and s != status:               continue
        rev_info = db["revocations"].get(sn, {})
        result.append({
            **info,
            "serial":   sn,
            "days_left": days,
            "status":   s,
            "revocation_reason": rev_info.get("reason"),
            "revoked_at":        rev_info.get("revoked_at"),
        })
    result.sort(key=lambda x: x["not_after"], reverse=True)
    return result[offset: offset + limit]


@app.get("/api/certs/{serial}/cert.pem")
def download_cert(serial: str, user: dict = Depends(require_permission("cert:download_cert"))):
    if not SERIAL_RE.match(serial):
        raise HTTPException(400, "Invalid serial format")
    f = CERTS_DIR / f"{serial}_cert.pem"
    if not f.exists():
        raise HTTPException(404)
    db = load_db()
    cn = db["certificates"].get(serial, {}).get("common_name", serial)
    safe_cn = cn.replace("*", "wildcard").replace(" ", "_").replace("/", "_")
    return Response(
        content=f.read_bytes(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{safe_cn}.crt"'},
    )


@app.get("/api/certs/{serial}/key.pem", response_class=PlainTextResponse)
def download_key(
    serial:           str,
    request:          Request,
    x_request_reason: Optional[str] = None,
    user:             dict = Depends(require_permission("cert:download_key")),
):
    if not SERIAL_RE.match(serial):
        raise HTTPException(400, "Invalid serial format")
    key_path = CERTS_DIR / f"{serial}_key.pem"
    if not key_path.exists():
        raise HTTPException(404)
    _audit(
        request, "cert.download_key", user,
        resource=serial,
        detail=x_request_reason or "no reason given",
    )
    db = load_db()
    cn = db["certificates"].get(serial, {}).get("common_name", serial)
    safe_cn = cn.replace("*", "wildcard").replace(" ", "_").replace("/", "_")
    return Response(
        content=_export_key_unencrypted(key_path),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{safe_cn}.key"'},
    )


@app.get("/api/certs/{serial}/bundle.pem")
def download_bundle(serial: str, user: dict = Depends(require_permission("cert:download_bundle"))):
    if not SERIAL_RE.match(serial):
        raise HTTPException(400, "Invalid serial format")
    db        = load_db()
    cert_info = db["certificates"].get(serial)
    if not cert_info:
        raise HTTPException(404, "Certificate not found")
    cert_f = CERTS_DIR / f"{serial}_cert.pem"
    ca_f   = CA_DIR / f"{cert_info['ca_name']}_cert.pem"
    if not cert_f.exists() or not ca_f.exists():
        raise HTTPException(404)
    cn      = cert_info.get("common_name", serial)
    safe_cn = cn.replace("*", "wildcard").replace(" ", "_").replace("/", "_")
    content = cert_f.read_text() + ca_f.read_text()
    return Response(
        content=content.encode(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{safe_cn}-bundle.pem"'},
    )


@app.post("/api/certs/revoke")
def revoke_cert(
    req:     RevokeRequest,
    request: Request,
    user:    dict = Depends(require_permission("cert:revoke")),
):
    with locked_db() as db:
        _revoke_cert_logic(req.serial, req.reason, user["sub"], db)
        ca_name = db["certificates"][req.serial].get("ca_name")
        _generate_crl(ca_name, db)

    CERT_REVOKE_CNT.inc()
    update_metrics()
    _audit(request, "cert.revoke", user, resource=req.serial, detail=req.reason)
    return {"status": "revoked", "serial": req.serial}


@app.post("/api/certs/renew")
def renew_cert(
    req:     RenewRequest,
    request: Request,
    user:    dict = Depends(require_permission("cert:renew")),
):
    with locked_db() as db:
        if req.serial not in db["certificates"]:
            raise HTTPException(404, "Certificate not found")
        old = db["certificates"][req.serial]

        domains = [s[4:] for s in old.get("sans", []) if s.startswith("DNS:") and not s[4:].startswith("*.")]
        ips     = [s[3:] for s in old.get("sans", []) if s.startswith("IP:")]

        new_req = CertCreateRequest(
            ca_name=old["ca_name"],
            common_name=old["common_name"],
            organization=old.get("organization", "Autoflow"),
            domains=domains,
            ips=ips,
            validity_days=req.validity_days,
            wildcard=old.get("wildcard", False),
            cert_type=old.get("cert_type", "server"),
            key_size=old.get("key_size", 2048),
        )
        new_serial, t0 = _issue_cert_logic(new_req, db, user["sub"])

        if req.serial not in db["revoked_serials"]:
            _revoke_cert_logic(req.serial, "superseded", user["sub"], db)
            _generate_crl(old.get("ca_name"), db)

    CERT_ISSUE_CNT.inc()
    CERT_REVOKE_CNT.inc()
    CERT_RENEW_CNT.inc()
    ISSUE_LATENCY.observe(time.time() - t0)
    update_metrics()
    _audit(request, "cert.renew", user, resource=req.serial, detail=f"new={new_serial}")
    return {"status": "issued", "serial": new_serial, "renewed_from": req.serial}


# ── Deploy endpoint ──────────────────────────────────────────────────────────
@app.post("/api/certs/{serial}/deploy")
def deploy_cert(
    serial:  str,
    request: Request,
    user:    dict = Depends(require_permission("cert:issue")),
):
    """
    Deploy the cert+key for *serial* as the active Traefik wildcard certificate.
    Traefik hot-reloads TLS without restart.
    """
    if not SERIAL_RE.match(serial):
        raise HTTPException(400, "Invalid serial format")

    if not _traefik_certs_available():
        raise HTTPException(503, (
            f"Traefik certs directory '{TRAEFIK_CERTS_DIR}' is not accessible. "
            "Ensure the volume is mounted (docker-compose.yml → pki.volumes)."
        ))

    with locked_db() as db:
        if serial not in db["certificates"]:
            raise HTTPException(404, "Certificate not found")
        if serial in db["revoked_serials"]:
            raise HTTPException(400, "Cannot deploy a revoked certificate")

        try:
            paths = _deploy_to_traefik(serial, db)
        except (FileNotFoundError, RuntimeError) as e:
            raise HTTPException(500, str(e))

    _audit(request, "cert.deploy", user, resource=serial, detail=f"domain={DOMAIN}")
    return {
        "status":    "deployed",
        "serial":    serial,
        "domain":    DOMAIN,
        "cert_path": paths["cert"],
        "key_path":  paths["key"],
        "note":      "Traefik hot-reloads TLS — no restart required.",
    }


@app.get("/api/deploy/status")
def deploy_status(user: dict = Depends(require_permission("cert:list"))):
    """Return info about the currently deployed Traefik wildcard cert."""
    db       = load_db()
    deployed = db.get("deployed", {}).get("traefik_wildcard")
    if not deployed:
        return {"deployed": False, "traefik_certs_available": _traefik_certs_available()}

    serial    = deployed["serial"]
    cert_info = db["certificates"].get(serial, {})
    now       = datetime.now(timezone.utc)
    days_left = 0
    status    = "unknown"
    if cert_info:
        exp       = _parse_dt(cert_info["not_after"])
        days_left = max(0, (exp - now).days)
        status, _ = _cert_status(serial, cert_info, db, now)

    return {
        "deployed":               True,
        "serial":                 serial,
        "domain":                 deployed.get("domain"),
        "deployed_at":            deployed.get("deployed_at"),
        "cert_path":              deployed.get("cert_path"),
        "common_name":            cert_info.get("common_name"),
        "not_after":              cert_info.get("not_after"),
        "days_left":              days_left,
        "status":                 status,
        "auto_renew_threshold":   AUTO_RENEW_DAYS,
        "traefik_certs_available": _traefik_certs_available(),
    }


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats(user: dict = Depends(require_permission("cert:list"))):
    db  = load_db()
    now = datetime.now(timezone.utc)
    total  = len(db["certificates"])
    revoked = len(db["revoked_serials"])
    expired = active = exp_7 = exp_30 = exp_90 = 0
    for sn, info in db["certificates"].items():
        s, dl = _cert_status(sn, info, db, now)
        if s == "expired":  expired += 1
        elif s != "revoked":
            active += 1
            if dl <= 7:  exp_7  += 1
            if dl <= 30: exp_30 += 1
            if dl <= 90: exp_90 += 1
    return {
        "total":        total,
        "active":       active,
        "revoked":      revoked,
        "expired":      expired,
        "expiring_7d":  exp_7,
        "expiring_30d": exp_30,
        "expiring_90d": exp_90,
        "ca_count":     len(db["cas"]),
    }


# ── Health & Metrics ──────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "autoflow-pki", "version": "2.0.0"}


@app.get("/metrics")
def metrics():
    update_metrics()
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent / "templates" / "index.html").read_text()
