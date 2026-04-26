"""
Compliance & Audit Report Generator
=====================================
Runs Trivy against all stack images, maps CVE/CWE findings to security
frameworks (NIST SP 800-53 rev5, CIS Controls v8, SOC2 TSC 2017,
ISO 27001:2022, PCI-DSS v4.0) and produces a structured JSON + Markdown report.

CLI usage (docker exec):
  python3 compliance.py             – generate report, print progress to stdout
  python3 compliance.py --json      – dump final JSON to stdout

API usage:
  from compliance import run_report
  report = await run_report(progress_cb=callback)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Callable

from settings import settings

logger = logging.getLogger("compliance")

REPORTS_DIR = Path("/tmp/compliance")

# ── CWE → NIST SP 800-53 rev5 mapping ────────────────────────────────────────
# Maps CWE numeric ID (int) → list of NIST control IDs

CWE_TO_NIST: dict[int, list[str]] = {
    # Injection / code execution
    78:   ["SI-10", "SI-3"],           # OS Command Injection
    79:   ["SI-10"],                   # XSS
    88:   ["SI-10"],                   # Argument Injection
    89:   ["SI-10"],                   # SQL Injection
    90:   ["SI-10"],                   # LDAP Injection
    94:   ["SI-10", "SI-3"],           # Code Injection
    95:   ["SI-10"],                   # Eval Injection
    116:  ["SI-10"],                   # Encoding / escaping
    434:  ["SI-3"],                    # Unrestricted File Upload
    502:  ["SI-10"],                   # Unsafe Deserialization
    601:  ["SI-10"],                   # Open Redirect
    611:  ["SI-10"],                   # XXE
    917:  ["SI-10"],                   # Expression Language Injection
    # Memory safety
    119:  ["SI-16"],                   # Buffer Overflow (generic)
    120:  ["SI-16"],                   # Classic Buffer Copy
    121:  ["SI-16"],                   # Stack-based Buffer Overflow
    122:  ["SI-16"],                   # Heap-based Buffer Overflow
    125:  ["SI-16"],                   # Out-of-bounds Read
    190:  ["SI-16"],                   # Integer Overflow
    416:  ["SI-16"],                   # Use After Free
    476:  ["SI-16"],                   # NULL Pointer Dereference
    787:  ["SI-16"],                   # Out-of-bounds Write
    # Cryptography
    310:  ["SC-13"],                   # Crypto Issues (generic)
    326:  ["SC-13"],                   # Inadequate Encryption Strength
    327:  ["SC-13"],                   # Broken Crypto Algorithm
    330:  ["SC-13"],                   # Insufficient Random Values
    338:  ["SC-13"],                   # Weak PRNG
    347:  ["SC-13", "SC-8"],           # Improper Signature Verification
    # Authentication & credentials
    256:  ["IA-5", "SC-28"],           # Unprotected Storage of Credentials
    259:  ["IA-5"],                    # Hardcoded Password
    287:  ["IA-2", "IA-8"],            # Improper Authentication
    306:  ["IA-2"],                    # Missing Authentication
    521:  ["IA-5"],                    # Weak Password Requirements
    798:  ["IA-5", "CM-6"],            # Hardcoded Credentials
    # Authorization & access control
    200:  ["SC-28", "AC-4"],           # Information Exposure
    284:  ["AC-3"],                    # Improper Access Control
    285:  ["AC-3"],                    # Improper Authorization
    639:  ["AC-4"],                    # IDOR
    732:  ["AC-6"],                    # Incorrect Permission Assignment
    862:  ["AC-3"],                    # Missing Authorization
    863:  ["AC-3"],                    # Incorrect Authorization
    1220: ["AC-3"],                    # Insufficient Granularity of Access Control
    # Transport / network
    295:  ["SC-8", "SC-23"],           # Improper Certificate Validation
    297:  ["SC-8"],                    # Improper Validation of Certificate Expiry
    311:  ["SC-8"],                    # Missing Encryption of Sensitive Data
    319:  ["SC-8"],                    # Cleartext Transmission
    918:  ["SC-7"],                    # SSRF
    # Configuration & system
    16:   ["CM-6"],                    # Configuration
    209:  ["SI-11"],                   # Info Exposure Through Error Message
    400:  ["SC-5"],                    # Uncontrolled Resource Consumption
    426:  ["CM-7"],                    # Untrusted Search Path
    # Patch management
    937:  ["SI-2"],                    # OWASP 2017 A9 - Known Vulns
    1035: ["SI-2"],                    # OWASP 2017 A9
}

# ── NIST SP 800-53 → CIS Controls v8 ─────────────────────────────────────────

NIST_TO_CIS: dict[str, list[str]] = {
    "AC-2":  ["CIS 5"],   # Account Management
    "AC-3":  ["CIS 6"],   # Access Control Management
    "AC-4":  ["CIS 12"],  # Network Infrastructure Management
    "AC-6":  ["CIS 6"],   # Least Privilege
    "CM-6":  ["CIS 4"],   # Secure Configuration
    "CM-7":  ["CIS 4"],   # Secure Configuration
    "IA-2":  ["CIS 5"],   # Account Management
    "IA-5":  ["CIS 5"],   # Account Management
    "IA-8":  ["CIS 5"],   # Account Management
    "SC-5":  ["CIS 13"],  # Network Monitoring and Defense
    "SC-7":  ["CIS 12"],  # Network Infrastructure Management
    "SC-8":  ["CIS 3"],   # Data Protection
    "SC-13": ["CIS 3"],   # Data Protection
    "SC-23": ["CIS 3"],   # Data Protection
    "SC-28": ["CIS 3"],   # Data Protection
    "SI-2":  ["CIS 7"],   # Continuous Vulnerability Management
    "SI-3":  ["CIS 10"],  # Malware Defenses
    "SI-10": ["CIS 16"],  # Application Software Security
    "SI-11": ["CIS 8"],   # Audit Log Management
    "SI-16": ["CIS 16"],  # Application Software Security
}

# ── NIST SP 800-53 → SOC2 TSC 2017 ───────────────────────────────────────────

NIST_TO_SOC2: dict[str, list[str]] = {
    "AC-2":  ["CC6.2"],
    "AC-3":  ["CC6.1", "CC6.3"],
    "AC-4":  ["CC6.6"],
    "AC-6":  ["CC6.1"],
    "CM-6":  ["CC6.6"],
    "CM-7":  ["CC6.6"],
    "IA-2":  ["CC6.1"],
    "IA-5":  ["CC6.1"],
    "IA-8":  ["CC6.1"],
    "SC-5":  ["CC6.6", "A1.1"],
    "SC-7":  ["CC6.6"],
    "SC-8":  ["CC6.7"],
    "SC-13": ["CC6.7"],
    "SC-23": ["CC9.2"],
    "SC-28": ["CC6.7"],
    "SI-2":  ["CC7.1"],
    "SI-3":  ["CC7.2"],
    "SI-10": ["CC7.2"],
    "SI-11": ["CC7.3"],
    "SI-16": ["CC7.2"],
}

# ── NIST SP 800-53 → ISO 27001:2022 ──────────────────────────────────────────

NIST_TO_ISO: dict[str, list[str]] = {
    "AC-2":  ["A.8.2"],
    "AC-3":  ["A.8.3"],
    "AC-4":  ["A.8.20"],
    "AC-6":  ["A.8.2"],
    "CM-6":  ["A.8.9"],
    "CM-7":  ["A.8.9"],
    "IA-2":  ["A.8.5"],
    "IA-5":  ["A.8.5"],
    "IA-8":  ["A.8.5"],
    "SC-5":  ["A.8.20"],
    "SC-7":  ["A.8.20"],
    "SC-8":  ["A.8.24"],
    "SC-13": ["A.8.24"],
    "SC-23": ["A.8.24"],
    "SC-28": ["A.8.10"],
    "SI-2":  ["A.8.8"],
    "SI-3":  ["A.8.7"],
    "SI-10": ["A.8.28"],
    "SI-11": ["A.8.16"],
    "SI-16": ["A.8.28"],
}

# ── NIST SP 800-53 → PCI-DSS v4.0 ────────────────────────────────────────────

NIST_TO_PCI: dict[str, list[str]] = {
    "AC-2":  ["7.2"],
    "AC-3":  ["7.2", "7.3"],
    "AC-4":  ["1.3"],
    "AC-6":  ["7.2"],
    "CM-6":  ["2.2"],
    "CM-7":  ["2.2"],
    "IA-2":  ["8.2"],
    "IA-5":  ["8.3"],
    "IA-8":  ["8.2"],
    "SC-5":  ["6.4"],
    "SC-7":  ["1.3"],
    "SC-8":  ["4.2"],
    "SC-13": ["3.5"],
    "SC-23": ["4.2"],
    "SC-28": ["3.5"],
    "SI-2":  ["6.3"],
    "SI-3":  ["5.2"],
    "SI-10": ["6.2"],
    "SI-11": ["10.4"],
    "SI-16": ["6.2"],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_cwe_ids(cwe_list: list[str] | None) -> list[int]:
    """Convert ['CWE-78', 'CWE-119'] → [78, 119], skip malformed."""
    result = []
    for entry in (cwe_list or []):
        entry = str(entry).upper().replace("CWE-", "").strip()
        try:
            result.append(int(entry))
        except ValueError:
            pass
    return result


def _map_finding(cwe_ids: list[int]) -> dict[str, list[str]]:
    """Return framework mappings for a set of CWE IDs."""
    nist: set[str] = set()
    for cwe in cwe_ids:
        nist.update(CWE_TO_NIST.get(cwe, []))

    cis:  set[str] = set()
    soc2: set[str] = set()
    iso:  set[str] = set()
    pci:  set[str] = set()

    for ctrl in nist:
        cis.update(NIST_TO_CIS.get(ctrl, []))
        soc2.update(NIST_TO_SOC2.get(ctrl, []))
        iso.update(NIST_TO_ISO.get(ctrl, []))
        pci.update(NIST_TO_PCI.get(ctrl, []))

    return {
        "nist_controls": sorted(nist),
        "cis_controls":  sorted(cis),
        "soc2_criteria": sorted(soc2),
        "iso_controls":  sorted(iso),
        "pci_requirements": sorted(pci),
    }


# ── Trivy scan (returns raw findings) ────────────────────────────────────────

async def _scan_image_findings(image: str, tag: str) -> list[dict]:
    """Run Trivy against *image:tag* and return raw vulnerability list."""
    full_ref = f"{image}:{tag}"
    cmd = [
        "trivy", "image",
        "--format", "json",
        "--exit-code", "0",
        "--no-progress",
        "--scanners", "vuln",
        "--cache-dir", settings.trivy_cache_dir,
        "--timeout", f"{settings.trivy_timeout}s",
        full_ref,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.trivy_timeout + 10,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"Trivy timed out scanning {full_ref}")

    if proc.returncode != 0:
        raise RuntimeError(f"Trivy exited {proc.returncode} scanning {full_ref}")

    findings: list[dict] = []
    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return findings

    for result in data.get("Results", []):
        for vuln in result.get("Vulnerabilities") or []:
            findings.append({
                "image":             full_ref,
                "vuln_id":           vuln.get("VulnerabilityID", ""),
                "cwe_ids_raw":       vuln.get("CweIDs") or [],
                "severity":          vuln.get("Severity", "UNKNOWN").upper(),
                "pkg_name":          vuln.get("PkgName", ""),
                "installed_version": vuln.get("InstalledVersion", ""),
                "fixed_version":     vuln.get("FixedVersion", ""),
                "title":             vuln.get("Title", ""),
            })
    return findings


# ── Image list (mirrors scanner.py) ──────────────────────────────────────────

def _build_image_list() -> list[tuple[str, str]]:
    return [
        ("ghcr.io/ansible/awx",                     settings.awx_version),
        ("quay.io/ansible/receptor",                "latest"),
        ("postgres",                                 "15.17-alpine"),
        ("redis",                                    "7.4.8-alpine"),
        ("prom/prometheus",                          "latest"),
        ("grafana/grafana",                          "latest"),
        ("prom/alertmanager",                        "latest"),
        ("prometheuscommunity/postgres-exporter",   "latest"),
        ("oliver006/redis_exporter",                "latest"),
    ]


# ── Report builder ────────────────────────────────────────────────────────────

def _build_report(
    findings_raw: list[dict],
    images_scanned: list[str],
    scan_errors: list[str],
) -> dict:
    findings_out: list[dict] = []
    severity_totals: dict[str, int] = {
        "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0
    }
    nist_all:  set[str] = set()
    cis_all:   set[str] = set()
    soc2_all:  set[str] = set()
    iso_all:   set[str] = set()
    pci_all:   set[str] = set()

    for f in findings_raw:
        cwe_ids = _parse_cwe_ids(f["cwe_ids_raw"])
        mapping = _map_finding(cwe_ids)

        sev = f["severity"] if f["severity"] in severity_totals else "UNKNOWN"
        severity_totals[sev] += 1

        nist_all.update(mapping["nist_controls"])
        cis_all.update(mapping["cis_controls"])
        soc2_all.update(mapping["soc2_criteria"])
        iso_all.update(mapping["iso_controls"])
        pci_all.update(mapping["pci_requirements"])

        findings_out.append({
            "image":             f["image"],
            "vuln_id":           f["vuln_id"],
            "cwe_ids":           [f"CWE-{c}" for c in cwe_ids],
            "severity":          f["severity"],
            "pkg_name":          f["pkg_name"],
            "installed_version": f["installed_version"],
            "fixed_version":     f["fixed_version"],
            "title":             f["title"],
            **mapping,
        })

    return {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "images_scanned":  images_scanned,
        "scan_errors":     scan_errors,
        "summary": {
            "total_findings": len(findings_out),
            "by_severity":    severity_totals,
            "by_framework": {
                "NIST_800_53": {
                    "controls_affected": len(nist_all),
                    "controls":          sorted(nist_all),
                },
                "CIS_v8": {
                    "controls_affected": len(cis_all),
                    "controls":          sorted(cis_all),
                },
                "SOC2_TSC": {
                    "criteria_affected": len(soc2_all),
                    "criteria":          sorted(soc2_all),
                },
                "ISO_27001": {
                    "controls_affected": len(iso_all),
                    "controls":          sorted(iso_all),
                },
                "PCI_DSS_v4": {
                    "requirements_affected": len(pci_all),
                    "requirements":          sorted(pci_all),
                },
            },
        },
        "findings": findings_out,
    }


def build_markdown(report: dict) -> str:
    ts  = report["generated_at"]
    s   = report["summary"]
    sev = s["by_severity"]
    fw  = s["by_framework"]

    lines: list[str] = [
        "# Autoflow — Compliance & Audit Report",
        "",
        f"**Generated:** {ts}  ",
        f"**Images scanned:** {len(report['images_scanned'])}  ",
        f"**Total findings:** {s['total_findings']}",
        "",
        "## Severity Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ]
    for sev_name in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"):
        lines.append(f"| {sev_name} | {sev.get(sev_name, 0)} |")

    lines += [
        "",
        "## Framework Impact",
        "",
        "| Framework | Affected Controls/Criteria |",
        "|-----------|---------------------------|",
        f"| NIST SP 800-53 rev5 | {fw['NIST_800_53']['controls_affected']} |",
        f"| CIS Controls v8 | {fw['CIS_v8']['controls_affected']} |",
        f"| SOC2 TSC 2017 | {fw['SOC2_TSC']['criteria_affected']} |",
        f"| ISO 27001:2022 | {fw['ISO_27001']['controls_affected']} |",
        f"| PCI-DSS v4.0 | {fw['PCI_DSS_v4']['requirements_affected']} |",
        "",
        "### NIST SP 800-53 Affected Controls",
        "",
        ", ".join(fw["NIST_800_53"]["controls"]) or "_none_",
        "",
        "### CIS Controls v8 Affected",
        "",
        ", ".join(fw["CIS_v8"]["controls"]) or "_none_",
        "",
        "### SOC2 Trust Services Criteria Affected",
        "",
        ", ".join(fw["SOC2_TSC"]["criteria"]) or "_none_",
        "",
        "### ISO 27001:2022 Controls Affected",
        "",
        ", ".join(fw["ISO_27001"]["controls"]) or "_none_",
        "",
        "### PCI-DSS v4.0 Requirements Affected",
        "",
        ", ".join(fw["PCI_DSS_v4"]["requirements"]) or "_none_",
        "",
    ]

    if report.get("scan_errors"):
        lines += ["## Scan Errors", ""]
        for err in report["scan_errors"]:
            lines.append(f"- {err}")
        lines.append("")

    # Top CRITICAL/HIGH findings table (max 50)
    top = [f for f in report["findings"] if f["severity"] in ("CRITICAL", "HIGH")][:50]
    if top:
        lines += [
            "## Critical & High Findings (top 50)",
            "",
            "| Image | CVE | Severity | Package | Fixed | NIST |",
            "|-------|-----|----------|---------|-------|------|",
        ]
        for f in top:
            img  = f["image"].split("/")[-1]          # last segment for brevity
            nist = ", ".join(f["nist_controls"]) or "—"
            fixed = f["fixed_version"] or "—"
            lines.append(
                f"| {img} | {f['vuln_id']} | {f['severity']} "
                f"| {f['pkg_name']} | {fixed} | {nist} |"
            )
        lines.append("")

    return "\n".join(lines)


# ── Main async entry point ────────────────────────────────────────────────────

async def run_report(
    progress_cb: Callable[[str], None] | None = None,
) -> dict:
    """
    Scan all images, build the compliance report, save JSON + Markdown.
    Returns the report dict.  *progress_cb* receives human-readable status lines.
    """
    def emit(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)
        else:
            logger.info(msg)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    images = _build_image_list()
    all_findings: list[dict] = []
    images_scanned: list[str] = []
    scan_errors: list[str] = []

    emit(f"Starting compliance scan — {len(images)} images")

    for idx, (image, tag) in enumerate(images, 1):
        full_ref = f"{image}:{tag}"
        emit(f"[{idx}/{len(images)}] Scanning {full_ref} …")
        t0 = time.monotonic()
        try:
            findings = await _scan_image_findings(image, tag)
            elapsed  = time.monotonic() - t0
            emit(
                f"[{idx}/{len(images)}] {full_ref} — "
                f"{len(findings)} findings in {elapsed:.1f}s"
            )
            all_findings.extend(findings)
            images_scanned.append(full_ref)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            msg = f"{full_ref}: {exc}"
            emit(f"[{idx}/{len(images)}] ERROR — {msg} ({elapsed:.1f}s)")
            scan_errors.append(msg)

    emit("Building compliance report …")
    report = _build_report(all_findings, images_scanned, scan_errors)

    json_path = REPORTS_DIR / "latest.json"
    md_path   = REPORTS_DIR / "latest.md"

    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(build_markdown(report))

    emit(
        f"Report saved: {json_path}  "
        f"({report['summary']['total_findings']} findings, "
        f"CRITICAL={report['summary']['by_severity']['CRITICAL']}, "
        f"HIGH={report['summary']['by_severity']['HIGH']})"
    )
    return report


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Autoflow compliance report")
    parser.add_argument("--json", action="store_true", help="Dump final JSON to stdout")
    args = parser.parse_args()

    def _cb(msg: str) -> None:
        print(msg, flush=True)

    report = asyncio.run(run_report(progress_cb=_cb))

    if args.json:
        print(json.dumps(report, indent=2))
