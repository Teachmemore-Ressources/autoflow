"""
Image Version Checker
=====================
Queries GitHub Releases and DockerHub Tags APIs to detect outdated images
and exposes the results as Prometheus metrics.

Metrics
-------
  image_version_outdated{image,current_version,latest_version}
      – 1 if the running tag is behind latest, 0 otherwise
  version_check_errors_total{image}
      – Counter incremented on API failures
  version_last_check_timestamp_seconds
      – Unix ts of the last completed version check cycle
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx
from packaging.version import InvalidVersion, Version
from prometheus_client import Counter, Gauge

from settings import settings

logger = logging.getLogger("version_check")

# ── Prometheus metrics ────────────────────────────────────────────────────────

_outdated = Gauge(
    "image_version_outdated",
    "1 if the running image tag is behind the latest available version",
    ["image", "current_version", "latest_version"],
)

_check_errors = Counter(
    "version_check_errors_total",
    "Total number of version check API failures per image",
    ["image"],
)

_last_check_ts = Gauge(
    "version_last_check_timestamp_seconds",
    "Unix timestamp of the last completed version check cycle",
)

# ── Image registry ────────────────────────────────────────────────────────────
# Each entry: (image_name, current_tag, check_type, source_ref)
#   check_type "github"   → source_ref is "owner/repo"
#   check_type "dockerhub" → source_ref is "namespace/name" (library/* for official)
#   check_type "latest"   → tag is "latest"; comparison is not meaningful, skip

_IMAGE_REGISTRY: list[tuple[str, str, str, str]] = [
    ("ghcr.io/ansible/awx",                       None,       "github",    "ansible/awx"),
    ("quay.io/ansible/receptor",                   "latest",   "latest",    "ansible/receptor"),
    ("postgres",                                   "15.17-alpine","dockerhub", "library/postgres"),
    ("redis",                                      "7.4.8-alpine", "dockerhub", "library/redis"),
    ("prom/prometheus",                            "latest",   "latest",    "prometheus/prometheus"),
    ("grafana/grafana",                            "latest",   "latest",    "grafana/grafana"),
    ("prom/alertmanager",                          "latest",   "latest",    "prometheus/alertmanager"),
    ("prometheuscommunity/postgres-exporter",      "latest",   "latest",    "prometheus-community/postgres_exporter"),
    ("oliver006/redis_exporter",                   "latest",   "latest",    "oliver006/redis_exporter"),
]


def _build_registry() -> list[tuple[str, str, str, str]]:
    """Return registry with the AWX version filled in from settings."""
    result = []
    for image, tag, check_type, ref in _IMAGE_REGISTRY:
        if image == "ghcr.io/ansible/awx":
            tag = settings.awx_version
        result.append((image, tag, check_type, ref))
    return result


def _pre_init_metrics() -> None:
    for image, tag, _, _ in _build_registry():
        _check_errors.labels(image=image)


_pre_init_metrics()


# ── Version check loop ────────────────────────────────────────────────────────

class VersionCheckLoop:
    def __init__(self) -> None:
        headers = {"User-Agent": "autoflow-security-scanner/1.0"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        auth = None
        if settings.dockerhub_user and settings.dockerhub_password:
            auth = (settings.dockerhub_user, settings.dockerhub_password)

        self._http = httpx.AsyncClient(
            headers=headers,
            auth=auth,
            timeout=15.0,
            follow_redirects=True,
        )

    async def run(self, interval: int) -> None:
        logger.info("Version checker started (interval=%ds)", interval)
        try:
            while True:
                await self._check_all()
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    logger.info("Version checker stopped")
                    return
        finally:
            await self._http.aclose()

    async def _check_all(self) -> None:
        registry = _build_registry()
        for image, current_tag, check_type, ref in registry:
            try:
                if check_type == "github":
                    await self._check_github(image, current_tag, ref)
                elif check_type == "dockerhub":
                    await self._check_dockerhub(image, current_tag, ref)
                else:
                    # "latest" tag — can't compare; emit 0 (not outdated / unknown)
                    _set_outdated(image, current_tag, current_tag, 0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Version check failed for %s: %s", image, exc)
                _check_errors.labels(image=image).inc()

        _last_check_ts.set(time.time())
        logger.info("Version check cycle complete")

    async def _check_github(self, image: str, current_tag: str, repo: str) -> None:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = await self._http.get(url)
        if resp.status_code == 404:
            # Fall back to tags endpoint for repos without formal releases
            resp = await self._http.get(f"https://api.github.com/repos/{repo}/tags?per_page=1")
            if resp.status_code != 200:
                raise RuntimeError(f"GitHub API {resp.status_code} for {repo}")
            tags = resp.json()
            latest_tag = tags[0]["name"] if tags else None
        elif resp.status_code != 200:
            raise RuntimeError(f"GitHub API {resp.status_code} for {repo}")
        else:
            latest_tag = resp.json().get("tag_name")

        if not latest_tag:
            logger.debug("No release found for %s", repo)
            return

        outdated = _is_outdated(current_tag, latest_tag)
        _set_outdated(image, current_tag, latest_tag, int(outdated))
        logger.debug("%s: current=%s latest=%s outdated=%s", image, current_tag, latest_tag, outdated)

    async def _check_dockerhub(self, image: str, current_tag: str, repo: str) -> None:
        # Determine the variant pattern (e.g. "-alpine") to stay on the same flavour
        variant = _extract_variant(current_tag)
        major = _extract_major(current_tag)

        url = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"
        resp = await self._http.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"DockerHub API {resp.status_code} for {repo}")

        tags = [t["name"] for t in resp.json().get("results", [])]

        # Filter to tags matching the same major version and variant
        candidates = [t for t in tags if _tag_matches_flavor(t, major, variant)]
        if not candidates:
            logger.debug("No matching tags found for %s (major=%s variant=%s)", image, major, variant)
            return

        latest_tag = _pick_latest(candidates, variant)
        if not latest_tag:
            return

        outdated = _is_outdated(current_tag, latest_tag)
        _set_outdated(image, current_tag, latest_tag, int(outdated))
        logger.debug("%s: current=%s latest=%s outdated=%s", image, current_tag, latest_tag, outdated)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_outdated(image: str, current: str, latest: str, value: int) -> None:
    """Set the image_version_outdated gauge, clearing stale label combos."""
    _outdated.labels(
        image=image,
        current_version=current or "unknown",
        latest_version=latest or "unknown",
    ).set(value)


def _strip_v(tag: str) -> str:
    return tag.lstrip("v")


def _is_outdated(current: str, latest: str) -> bool:
    """Return True if *current* is strictly older than *latest*."""
    try:
        return Version(_strip_v(current)) < Version(_strip_v(latest))
    except InvalidVersion:
        # Non-semver tags (e.g. "15-alpine") — fall back to string comparison
        return _strip_v(current) != _strip_v(latest)


def _extract_variant(tag: str) -> str:
    """Return the variant suffix, e.g. '-alpine' from '15-alpine', or ''."""
    match = re.search(r"(-[a-zA-Z][\w]*)$", tag)
    return match.group(1) if match else ""


def _extract_major(tag: str) -> str:
    """Return the major version number string from a tag like '15-alpine' → '15'."""
    match = re.match(r"^v?(\d+)", tag)
    return match.group(1) if match else ""


def _tag_matches_flavor(tag: str, major: str, variant: str) -> bool:
    """Return True if *tag* shares the same major version and variant suffix."""
    if major and not tag.startswith(major):
        return False
    if variant and not tag.endswith(variant):
        return False
    # Exclude tags that look like pre-release or have extra qualifiers
    core = tag
    if variant:
        core = core[: -len(variant)]
    # core should look like "15", "15.3", "15.3.1", etc.
    return bool(re.match(r"^\d+(\.\d+)*$", core))


def _pick_latest(candidates: list[str], variant: str) -> str | None:
    """Return the highest semver tag from *candidates*."""
    best_tag: str | None = None
    best_ver: Version | None = None

    for tag in candidates:
        core = tag
        if variant:
            core = core[: -len(variant)]
        try:
            ver = Version(core)
        except InvalidVersion:
            continue
        if best_ver is None or ver > best_ver:
            best_ver = ver
            best_tag = tag

    return best_tag
