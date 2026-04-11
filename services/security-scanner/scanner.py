"""
Trivy Scanner
=============
Runs Trivy against each image in the stack and exposes CVE counts as
Prometheus gauges.  Designed to run as an asyncio background task.

Metrics
-------
  trivy_vulnerabilities_total{image,tag,severity}  – CVE count per image/severity
  trivy_last_scan_timestamp_seconds{image}          – Unix ts of last completed scan
  trivy_scan_duration_seconds{image}                – Wall-clock duration of last scan
  trivy_scan_errors_total{image}                    – Scan failure counter
  security_scanner_healthy                          – 1 if last full cycle succeeded
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from prometheus_client import Counter, Gauge

from settings import settings

logger = logging.getLogger("scanner")

# ── Prometheus metrics ────────────────────────────────────────────────────────

_vuln_gauge = Gauge(
    "trivy_vulnerabilities_total",
    "Number of vulnerabilities found by Trivy per image and severity",
    ["image", "tag", "severity"],
)

_last_scan_ts = Gauge(
    "trivy_last_scan_timestamp_seconds",
    "Unix timestamp of the last completed Trivy scan for this image",
    ["image"],
)

_scan_duration = Gauge(
    "trivy_scan_duration_seconds",
    "Wall-clock duration of the last Trivy scan for this image in seconds",
    ["image"],
)

_scan_errors = Counter(
    "trivy_scan_errors_total",
    "Total number of Trivy scan failures per image",
    ["image"],
)

_scanner_healthy = Gauge(
    "security_scanner_healthy",
    "1 if the security scanner completed its last full cycle successfully",
)

# ── Image list ────────────────────────────────────────────────────────────────
# Static list mirroring docker-compose.yml.  Tag is kept separate so it can be
# used as a Prometheus label without embedding it in the image name.

_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def _build_image_list() -> list[tuple[str, str]]:
    """Return (image_name, tag) pairs for all stack images."""
    return [
        ("ghcr.io/ansible/awx", settings.awx_version),
        ("quay.io/ansible/receptor", "latest"),
        ("postgres", "15-alpine"),
        ("redis", "7-alpine"),
        ("prom/prometheus", "latest"),
        ("grafana/grafana", "latest"),
        ("prom/alertmanager", "latest"),
        ("prometheuscommunity/postgres-exporter", "latest"),
        ("oliver006/redis_exporter", "latest"),
    ]


def _pre_init_metrics() -> None:
    """Pre-initialise all label combos so Grafana can query before first scan."""
    for image, tag in _build_image_list():
        for severity in _SEVERITIES:
            _vuln_gauge.labels(image=image, tag=tag, severity=severity)
        _last_scan_ts.labels(image=image)
        _scan_duration.labels(image=image)
        _scan_errors.labels(image=image)


_pre_init_metrics()


# ── Scanner loop ──────────────────────────────────────────────────────────────

async def scan_loop(interval: int) -> None:
    """Infinite loop: run a full scan cycle then sleep for *interval* seconds."""
    logger.info("Trivy scanner started (interval=%ds)", interval)
    while True:
        await _scan_all()
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Trivy scanner stopped")
            return


async def _scan_all() -> None:
    """Run one full scan cycle across all images."""
    images = _build_image_list()
    errors = 0

    for image, tag in images:
        try:
            await _scan_image(image, tag)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Scan failed for %s:%s — %s", image, tag, exc)
            _scan_errors.labels(image=image).inc()
            errors += 1

    _scanner_healthy.set(0 if errors == len(images) else 1)
    logger.info("Scan cycle complete — %d/%d images succeeded", len(images) - errors, len(images))


async def _scan_image(image: str, tag: str) -> None:
    """Run Trivy against *image:tag* and update Prometheus gauges."""
    full_ref = f"{image}:{tag}"
    logger.debug("Scanning %s", full_ref)
    t0 = time.monotonic()

    cmd = [
        "trivy", "image",
        "--format", "json",
        "--exit-code", "0",   # never exit non-zero on findings
        "--no-progress",
        "--cache-dir", settings.trivy_cache_dir,
        "--timeout", f"{settings.trivy_timeout}s",
        full_ref,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Give the scan a hard deadline: configured timeout + 10s grace
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.trivy_timeout + 10,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"Trivy timed out after {settings.trivy_timeout + 10}s")

    elapsed = time.monotonic() - t0

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Trivy exited {proc.returncode}: {err_msg[:200]}")

    counts = _parse_trivy_output(stdout.decode(errors="replace"))

    for severity in _SEVERITIES:
        _vuln_gauge.labels(image=image, tag=tag, severity=severity).set(
            counts.get(severity, 0)
        )

    _last_scan_ts.labels(image=image).set(time.time())
    _scan_duration.labels(image=image).set(round(elapsed, 2))

    total = sum(counts.values())
    logger.info(
        "Scanned %s — %d vulns (CRITICAL=%d HIGH=%d) in %.1fs",
        full_ref,
        total,
        counts.get("CRITICAL", 0),
        counts.get("HIGH", 0),
        elapsed,
    )


def _parse_trivy_output(raw: str) -> dict[str, int]:
    """Parse Trivy JSON output and return {severity: count} totals."""
    counts: dict[str, int] = {s: 0 for s in _SEVERITIES}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse Trivy JSON output: %s", exc)
        return counts

    for result in data.get("Results", []):
        for vuln in result.get("Vulnerabilities") or []:
            severity = vuln.get("Severity", "UNKNOWN").upper()
            if severity not in counts:
                severity = "UNKNOWN"
            counts[severity] += 1

    return counts
