"""
Autoflow PKI Service - Certificate Authority Management
"""
import os
import json
import time
import logging
import ipaddress
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import (
    Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
)
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import CertificateRevocationListBuilder, RevokedCertificateBuilder
import secrets
import hashlib

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pki")

# ── Config ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("PKI_DATA_DIR", "/data/pki"))
CA_DIR = DATA_DIR / "ca"
CERTS_DIR = DATA_DIR / "certs"
CRL_DIR = DATA_DIR / "crl"
DB_FILE = DATA_DIR / "db.json"

for d in [CA_DIR, CERTS_DIR, CRL_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Prometheus Metrics ────────────────────────────────────────────────────────
CERTS_TOTAL = Gauge("pki_certificates_total", "Total certificates issued")
CERTS_ACTIVE = Gauge("pki_certificates_active", "Active (non-revoked, non-expired) certificates")
CERTS_REVOKED = Gauge("pki_certificates_revoked_total", "Total revoked certificates")
CERTS_EXPIRED = Gauge("pki_certificates_expired_total", "Total expired certificates")
CERTS_EXPIRING_7D = Gauge("pki_certificates_expiring_7d", "Certificates expiring within 7 days")
CERTS_EXPIRING_30D = Gauge("pki_certificates_expiring_30d", "Certificates expiring within 30 days")
CERTS_EXPIRING_90D = Gauge("pki_certificates_expiring_90d", "Certificates expiring within 90 days")
CA_EXPIRY_DAYS = Gauge("pki_ca_expiry_days", "Days until CA certificate expires", ["ca_name"])
CERT_ISSUE_COUNT = Counter("pki_certificate_issues_total", "Total certificate issuance operations")
CERT_REVOKE_COUNT = Counter("pki_certificate_revocations_total", "Total certificate revocations")
CERT_RENEW_COUNT = Counter("pki_certificate_renewals_total", "Total certificate renewals")
ISSUE_LATENCY = Histogram("pki_issue_duration_seconds", "Certificate issuance latency")

# ── DB helpers ────────────────────────────────────────────────────────────────
def load_db() -> dict:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text())
    return {"certificates": {}, "cas": {}, "revoked_serials": []}

def save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, indent=2, default=str))

def update_metrics():
    db = load_db()
    now = datetime.now(timezone.utc)
    total = len(db["certificates"])
    revoked = len(db["revoked_serials"])
    expired = 0
    active = 0
    exp_7 = exp_30 = exp_90 = 0
    for sn, cert_info in db["certificates"].items():
        exp = datetime.fromisoformat(cert_info["not_after"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        is_revoked = sn in db["revoked_serials"]
        is_expired = exp < now
        if is_expired:
            expired += 1
        elif not is_revoked:
            active += 1
            days_left = (exp - now).days
            if days_left <= 7:
                exp_7 += 1
            if days_left <= 30:
                exp_30 += 1
            if days_left <= 90:
                exp_90 += 1
    CERTS_TOTAL.set(total)
    CERTS_ACTIVE.set(active)
    CERTS_REVOKED.set(revoked)
    CERTS_EXPIRED.set(expired)
    CERTS_EXPIRING_7D.set(exp_7)
    CERTS_EXPIRING_30D.set(exp_30)
    CERTS_EXPIRING_90D.set(exp_90)
    for ca_name, ca_info in db["cas"].items():
        exp = datetime.fromisoformat(ca_info["not_after"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = max(0, (exp - now).days)
        CA_EXPIRY_DAYS.labels(ca_name=ca_name).set(days)

def metrics_loop():
    while True:
        try:
            update_metrics()
        except Exception as e:
            log.error(f"Metrics update error: {e}")
        time.sleep(30)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Autoflow PKI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    threading.Thread(target=metrics_loop, daemon=True).start()
    log.info("PKI Service started")

# ── Models ────────────────────────────────────────────────────────────────────
class CACreateRequest(BaseModel):
    name: str
    common_name: str
    organization: str = "Autoflow"
    country: str = "FR"
    validity_days: int = 3650
    key_size: int = 4096

class CertCreateRequest(BaseModel):
    ca_name: str
    common_name: str
    domains: List[str] = []
    ips: List[str] = []
    organization: str = "Autoflow"
    country: str = "FR"
    validity_days: int = 365
    wildcard: bool = False
    cert_type: str = "server"  # server | client | both

class RevokeRequest(BaseModel):
    serial: str
    reason: str = "unspecified"

class RenewRequest(BaseModel):
    serial: str
    validity_days: int = 365

# ── PKI Core ──────────────────────────────────────────────────────────────────
def serial_to_hex(serial: int) -> str:
    return format(serial, 'x').upper()

def generate_rsa_key(size: int = 4096):
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=size,
        backend=default_backend()
    )

def cert_to_info(cert: x509.Certificate, serial_hex: str, ca_name: str, db: dict) -> dict:
    now = datetime.now(timezone.utc)
    exp = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after.replace(tzinfo=timezone.utc)
    is_revoked = serial_hex in db.get("revoked_serials", [])
    is_expired = exp < now
    days_left = max(0, (exp - now).days)
    if is_revoked:
        status = "revoked"
    elif is_expired:
        status = "expired"
    elif days_left <= 7:
        status = "critical"
    elif days_left <= 30:
        status = "warning"
    else:
        status = "active"
    
    subject = {}
    for attr in cert.subject:
        subject[attr.oid.dotted_string] = attr.value
    
    sans = []
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for name in san_ext.value:
            if isinstance(name, x509.DNSName):
                sans.append(f"DNS:{name.value}")
            elif isinstance(name, x509.IPAddress):
                sans.append(f"IP:{name.value}")
    except Exception:
        pass

    return {
        "serial": serial_hex,
        "common_name": subject.get("2.5.4.3", ""),
        "organization": subject.get("2.5.4.10", ""),
        "ca_name": ca_name,
        "not_before": cert.not_valid_before.isoformat() if not hasattr(cert, 'not_valid_before_utc') else cert.not_valid_before_utc.isoformat(),
        "not_after": exp.isoformat(),
        "days_left": days_left,
        "status": status,
        "sans": sans,
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex().upper(),
    }

# ── CA Endpoints ──────────────────────────────────────────────────────────────
@app.post("/api/ca/create")
def create_ca(req: CACreateRequest):
    db = load_db()
    if req.name in db["cas"]:
        raise HTTPException(400, f"CA '{req.name}' already exists")
    
    key = generate_rsa_key(req.key_size)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, req.country),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, req.organization),
        x509.NameAttribute(NameOID.COMMON_NAME, req.common_name),
    ])
    now = datetime.now(timezone.utc)
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
            encipher_only=False, decipher_only=False
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256(), default_backend())
    )
    
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    
    (CA_DIR / f"{req.name}_key.pem").write_bytes(key_pem)
    (CA_DIR / f"{req.name}_cert.pem").write_bytes(cert_pem)
    
    exp = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after.replace(tzinfo=timezone.utc)
    db["cas"][req.name] = {
        "common_name": req.common_name,
        "organization": req.organization,
        "country": req.country,
        "not_after": exp.isoformat(),
        "serial": serial_to_hex(cert.serial_number),
        "fingerprint": cert.fingerprint(hashes.SHA256()).hex().upper(),
        "created_at": now.isoformat(),
    }
    save_db(db)
    update_metrics()
    return {"status": "created", "ca_name": req.name, "serial": serial_to_hex(cert.serial_number)}

@app.get("/api/ca/list")
def list_cas():
    db = load_db()
    now = datetime.now(timezone.utc)
    result = []
    for name, info in db["cas"].items():
        exp = datetime.fromisoformat(info["not_after"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days = max(0, (exp - now).days)
        result.append({**info, "name": name, "days_left": days})
    return result

@app.get("/api/ca/{ca_name}/cert.pem", response_class=PlainTextResponse)
def download_ca_cert(ca_name: str):
    f = CA_DIR / f"{ca_name}_cert.pem"
    if not f.exists():
        raise HTTPException(404, "CA not found")
    return f.read_text()

# ── Certificate Endpoints ─────────────────────────────────────────────────────
@app.post("/api/certs/issue")
def issue_cert(req: CertCreateRequest):
    db = load_db()
    if req.ca_name not in db["cas"]:
        raise HTTPException(404, f"CA '{req.ca_name}' not found")
    
    t0 = time.time()
    ca_key_pem = (CA_DIR / f"{req.ca_name}_key.pem").read_bytes()
    ca_cert_pem = (CA_DIR / f"{req.ca_name}_cert.pem").read_bytes()
    ca_key = serialization.load_pem_private_key(ca_key_pem, None, default_backend())
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem, default_backend())
    
    key = generate_rsa_key(2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, req.country),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, req.organization),
        x509.NameAttribute(NameOID.COMMON_NAME, req.common_name),
    ])
    
    san_list = []
    if req.wildcard:
        domain_base = req.common_name.lstrip("*.")
        san_list.append(x509.DNSName(f"*.{domain_base}"))
        san_list.append(x509.DNSName(domain_base))
    for d in req.domains:
        san_list.append(x509.DNSName(d))
    for ip in req.ips:
        san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
    
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
    )
    
    if san_list:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_list), critical=False)
    
    # Key usage based on cert type
    if req.cert_type == "client":
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False
        ), critical=True)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    elif req.cert_type == "both":
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=True,
            key_encipherment=True, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False
        ), critical=True)
        builder = builder.add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH
        ]), critical=False)
    else:  # server
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=True, data_encipherment=False, key_agreement=False,
            key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False
        ), critical=True)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    
    cert = builder.sign(ca_key, hashes.SHA256(), default_backend())
    serial_hex = serial_to_hex(cert.serial_number)
    
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    
    (CERTS_DIR / f"{serial_hex}_key.pem").write_bytes(key_pem)
    (CERTS_DIR / f"{serial_hex}_cert.pem").write_bytes(cert_pem)
    
    exp = cert.not_valid_after_utc if hasattr(cert, 'not_valid_after_utc') else cert.not_valid_after.replace(tzinfo=timezone.utc)
    db["certificates"][serial_hex] = {
        "common_name": req.common_name,
        "organization": req.organization,
        "ca_name": req.ca_name,
        "not_before": now.isoformat(),
        "not_after": exp.isoformat(),
        "cert_type": req.cert_type,
        "wildcard": req.wildcard,
        "sans": [str(s) for s in san_list],
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex().upper(),
        "issued_at": now.isoformat(),
    }
    save_db(db)
    CERT_ISSUE_COUNT.inc()
    ISSUE_LATENCY.observe(time.time() - t0)
    update_metrics()
    return {"status": "issued", "serial": serial_hex}

@app.get("/api/certs/list")
def list_certs(ca: Optional[str] = None, status: Optional[str] = None):
    db = load_db()
    now = datetime.now(timezone.utc)
    result = []
    for sn, info in db["certificates"].items():
        exp = datetime.fromisoformat(info["not_after"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        is_revoked = sn in db.get("revoked_serials", [])
        is_expired = exp < now
        days_left = max(0, (exp - now).days)
        if is_revoked:
            s = "revoked"
        elif is_expired:
            s = "expired"
        elif days_left <= 7:
            s = "critical"
        elif days_left <= 30:
            s = "warning"
        else:
            s = "active"
        if ca and info["ca_name"] != ca:
            continue
        if status and s != status:
            continue
        result.append({**info, "serial": sn, "days_left": days_left, "status": s})
    result.sort(key=lambda x: x["not_after"], reverse=True)
    return result

@app.get("/api/certs/{serial}/cert.pem", response_class=PlainTextResponse)
def download_cert(serial: str):
    f = CERTS_DIR / f"{serial}_cert.pem"
    if not f.exists():
        raise HTTPException(404)
    return f.read_text()

@app.get("/api/certs/{serial}/key.pem", response_class=PlainTextResponse)
def download_key(serial: str):
    f = CERTS_DIR / f"{serial}_key.pem"
    if not f.exists():
        raise HTTPException(404)
    return f.read_text()

@app.get("/api/certs/{serial}/bundle.pem", response_class=PlainTextResponse)
def download_bundle(serial: str, ca: str):
    cert_f = CERTS_DIR / f"{serial}_cert.pem"
    ca_f = CA_DIR / f"{ca}_cert.pem"
    if not cert_f.exists() or not ca_f.exists():
        raise HTTPException(404)
    return cert_f.read_text() + ca_f.read_text()

@app.post("/api/certs/revoke")
def revoke_cert(req: RevokeRequest):
    db = load_db()
    if req.serial not in db["certificates"]:
        raise HTTPException(404, "Certificate not found")
    if req.serial in db.get("revoked_serials", []):
        raise HTTPException(400, "Already revoked")
    if "revoked_serials" not in db:
        db["revoked_serials"] = []
    db["revoked_serials"].append(req.serial)
    if "revocations" not in db:
        db["revocations"] = {}
    db["revocations"][req.serial] = {
        "reason": req.reason,
        "revoked_at": datetime.now(timezone.utc).isoformat()
    }
    save_db(db)
    CERT_REVOKE_COUNT.inc()
    update_metrics()
    return {"status": "revoked", "serial": req.serial}

@app.post("/api/certs/renew")
def renew_cert(req: RenewRequest):
    db = load_db()
    if req.serial not in db["certificates"]:
        raise HTTPException(404, "Certificate not found")
    old = db["certificates"][req.serial]
    # Build new cert request from old
    new_req = CertCreateRequest(
        ca_name=old["ca_name"],
        common_name=old["common_name"],
        organization=old.get("organization", "Autoflow"),
        validity_days=req.validity_days,
        wildcard=old.get("wildcard", False),
        cert_type=old.get("cert_type", "server"),
    )
    result = issue_cert(new_req)
    # Revoke old
    if req.serial not in db.get("revoked_serials", []):
        revoke_cert(RevokeRequest(serial=req.serial, reason="superseded"))
    CERT_RENEW_COUNT.inc()
    update_metrics()
    return {**result, "renewed_from": req.serial}

# ── Stats ──────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats():
    db = load_db()
    now = datetime.now(timezone.utc)
    total = len(db["certificates"])
    revoked = len(db.get("revoked_serials", []))
    expired = active = exp_7 = exp_30 = exp_90 = 0
    for sn, info in db["certificates"].items():
        exp = datetime.fromisoformat(info["not_after"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        is_revoked = sn in db.get("revoked_serials", [])
        is_expired = exp < now
        if is_expired:
            expired += 1
        elif not is_revoked:
            active += 1
            days_left = (exp - now).days
            if days_left <= 7:
                exp_7 += 1
            if days_left <= 30:
                exp_30 += 1
            if days_left <= 90:
                exp_90 += 1
    ca_count = len(db["cas"])
    return {
        "total": total, "active": active, "revoked": revoked, "expired": expired,
        "expiring_7d": exp_7, "expiring_30d": exp_30, "expiring_90d": exp_90,
        "ca_count": ca_count,
    }

# ── Health & Metrics ───────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "autoflow-pki"}

@app.get("/metrics")
def metrics():
    update_metrics()
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent / "templates" / "index.html").read_text()
