"""
routers/cleanup.py — Docker environment cleanup and disk usage reporting.
"""

from __future__ import annotations

import asyncio
import glob
import json
import subprocess

from core.auth import _audit
from core.env import ENV_FILE, ROOT, _load_env
from core.shell import _sse
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

# Volumes created by docker-compose (may or may not be external)
_AUTOFLOW_VOLUMES: list[str] = [
    "autoflow_postgres_data",
    "autoflow_redis_data",
    "autoflow_pki_data",
    "autoflow_gitea_data",
    "autoflow_gitea_postgres_data",
    "autoflow_loki_data",
    "autoflow_minio_data",
    "autoflow_grafana_data",
    "autoflow_prometheus_data",
    "autoflow_receptor_run",
    "autoflow_awx_projects",
]

# Build-context temp dirs created by ansible-builder
_EE_TMP_GLOB = "/tmp/ee-build-*"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _docker_df() -> dict:
    """Return parsed `docker system df --format json` output."""
    r = subprocess.run(
        ["docker", "system", "df", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
    )
    result = {"images": "?", "containers": "?", "volumes": "?", "build_cache": "?"}
    if r.returncode != 0:
        return result
    for line in r.stdout.strip().splitlines():
        try:
            obj = json.loads(line)
            t = obj.get("Type", "")
            size = obj.get("Size", obj.get("TotalCount", "?"))
            reclaimable = obj.get("Reclaimable", "")
            if "Image" in t:
                result["images"] = f"{obj.get('Active', '?')} images, {size}" + (
                    f" ({reclaimable} reclaimable)" if reclaimable else ""
                )
            elif "Container" in t:
                active = obj.get("Active", "?")
                total_c = obj.get("TotalCount", "?")
                result["containers"] = f"{active} running / {total_c} total"
            elif "Volume" in t:
                result["volumes"] = f"{obj.get('TotalCount', '?')} volumes, {size}" + (
                    f" ({reclaimable} reclaimable)" if reclaimable else ""
                )
            elif "Build" in t:
                result["build_cache"] = f"{size}" + (f" ({reclaimable} reclaimable)" if reclaimable else "")
        except Exception:
            pass
    return result


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/api/cleanup/status")
def cleanup_status():
    """Return current disk usage and autoflow resource inventory."""
    config = _load_env()
    domain = config.get("DOMAIN", "localhost")
    registry = f"git.{domain}"

    # Running containers
    containers_r = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=autoflow_", "--format", "{{.Names}}|{{.Status}}"],
        capture_output=True,
        text=True,
    )
    containers = []
    for line in containers_r.stdout.strip().splitlines():
        if "|" in line:
            name, status = line.split("|", 1)
            containers.append({"name": name.strip(), "status": status.strip()})

    # Volumes
    volumes_r = subprocess.run(
        ["docker", "volume", "ls", "--filter", "name=autoflow", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
    )
    existing_volumes = [v.strip() for v in volumes_r.stdout.strip().splitlines() if v.strip()]

    # Autoflow images
    images_r = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}|{{.Size}}"],
        capture_output=True,
        text=True,
    )
    images = []
    for line in images_r.stdout.strip().splitlines():
        if "|" not in line:
            continue
        tag, size = line.split("|", 1)
        if "autoflow/" in tag or f"{registry}/" in tag or "ghcr.io/ansible/awx" in tag:
            images.append({"tag": tag.strip(), "size": size.strip()})

    # Build cache
    tmp_dirs = glob.glob(_EE_TMP_GLOB)

    df = _docker_df()

    return {
        "containers": containers,
        "volumes": existing_volumes,
        "images": images,
        "tmp_dirs": tmp_dirs,
        "disk": df,
    }


@router.get("/api/cleanup")
async def cleanup(
    request: Request,
    stop_containers: bool = True,
    remove_volumes: bool = False,
    remove_images: bool = False,
    remove_ee: bool = False,
    prune_cache: bool = False,
    clean_tmp: bool = False,
    remove_env: bool = False,
):
    """SSE: progressive Autoflow environment cleanup.

    Query params (all default to conservative / safe values):
      stop_containers  – docker compose down  (default: True)
      remove_volumes   – delete all autoflow_* volumes (default: False)
      remove_images    – delete autoflow/awx-patched images (default: False)
      remove_ee        – delete EE images from local Docker (default: False)
      prune_cache      – docker builder prune -af (default: False)
      clean_tmp        – delete /tmp/ee-build-* dirs (default: False)
      remove_env       – delete .env file (default: False, nuclear option)
    """
    _audit(
        request,
        "cleanup.run",
        stop_containers=stop_containers,
        remove_volumes=remove_volumes,
        remove_images=remove_images,
        remove_ee=remove_ee,
        prune_cache=prune_cache,
        clean_tmp=clean_tmp,
        remove_env=remove_env,
    )

    async def stream():
        config = _load_env()
        domain = config.get("DOMAIN", "localhost")
        awx_version = config.get("AWX_VERSION", "24.6.1")
        gitea_user = config.get("GITEA_ADMIN_USER", "admin")
        registry = f"git.{domain}"

        yield _sse("🧹 Starting Autoflow cleanup…")
        yield _sse("─" * 55)
        errors: list[str] = []

        # ── 1. Stop + remove containers / networks ────────────────────────
        if stop_containers:
            yield _sse("[1/7] Stopping containers (docker compose down)…")
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "compose",
                "--env-file",
                str(ENV_FILE),
                "down",
                "--remove-orphans",
                "--timeout",
                "30",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ROOT),
            )
            async for raw in proc.stdout:
                line = raw.decode().rstrip()
                if line:
                    yield _sse(f"  {line}")
            rc = await proc.wait()
            if rc == 0:
                yield _sse("  Containers stopped and removed ✔")
            else:
                yield _sse(f"  [WARN] docker compose down returned code {rc}")
                errors.append("docker compose down")
        else:
            yield _sse("[1/7] Containers — skipped (stop_containers=false)")

        # ── 2. Volumes ────────────────────────────────────────────────────
        if remove_volumes:
            yield _sse("[2/7] Removing Autoflow volumes…")
            vols_r = subprocess.run(
                ["docker", "volume", "ls", "--filter", "name=autoflow", "--format", "{{.Name}}"],
                capture_output=True,
                text=True,
            )
            found_vols = [v.strip() for v in vols_r.stdout.strip().splitlines() if v.strip()]
            if not found_vols:
                yield _sse("  No autoflow volumes found.")
            for vol in found_vols:
                r = subprocess.run(
                    ["docker", "volume", "rm", vol],
                    capture_output=True,
                    text=True,
                )
                if r.returncode == 0:
                    yield _sse(f"  ✔ volume removed: {vol}")
                else:
                    msg = r.stderr.strip()
                    yield _sse(f"  [WARN] {vol} : {msg}")
                    if "in use" in msg:
                        yield _sse("  → Stop all containers using this volume first.")
                    errors.append(f"volume rm {vol}")
        else:
            yield _sse("[2/7] Volumes — skipped (remove_volumes=false)")

        # ── 3. AWX patched image ──────────────────────────────────────────
        if remove_images:
            yield _sse("[3/7] Removing custom AWX image…")
            images_r = subprocess.run(
                [
                    "docker",
                    "images",
                    "--format",
                    "{{.Repository}}:{{.Tag}}",
                    "--filter",
                    "reference=autoflow/awx-patched:*",
                ],
                capture_output=True,
                text=True,
            )
            awx_images = [t.strip() for t in images_r.stdout.strip().splitlines() if t.strip()]
            # Also check ghcr.io base image
            base_tag = f"ghcr.io/ansible/awx:{awx_version}"
            base_r = subprocess.run(
                ["docker", "image", "inspect", base_tag, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
            )
            if base_r.returncode == 0:
                awx_images.append(base_tag)

            if not awx_images:
                yield _sse("  No patched AWX image found.")
            for img in awx_images:
                r = subprocess.run(["docker", "rmi", img], capture_output=True, text=True)
                if r.returncode == 0:
                    yield _sse(f"  ✔ image removed: {img}")
                else:
                    yield _sse(f"  [WARN] {img} : {r.stderr.strip()}")
                    errors.append(f"rmi {img}")
        else:
            yield _sse("[3/7] AWX image — skipped (remove_images=false)")

        # ── 4. EE images ──────────────────────────────────────────────────
        if remove_ee:
            yield _sse("[4/7] Removing Execution Environment images…")
            all_images_r = subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                capture_output=True,
                text=True,
            )
            ee_images = [
                t.strip()
                for t in all_images_r.stdout.strip().splitlines()
                if t.strip()
                and (f"{registry}/{gitea_user}/ee-" in t or "/ee-" in t)
                and "autoflow" in t.lower()
            ]
            # Also catch images without registry prefix (local builds)
            for ee in ["base", "security", "network"]:
                for candidate in [f"ee-{ee}:latest", f"ee-{ee}:1.0.0"]:
                    cr = subprocess.run(
                        ["docker", "image", "inspect", candidate, "--format", "{{.Id}}"],
                        capture_output=True,
                        text=True,
                    )
                    if cr.returncode == 0 and candidate not in ee_images:
                        ee_images.append(candidate)

            if not ee_images:
                yield _sse("  No local EE image found.")
            for img in ee_images:
                r = subprocess.run(["docker", "rmi", img], capture_output=True, text=True)
                if r.returncode == 0:
                    yield _sse(f"  ✔ EE image removed: {img}")
                else:
                    yield _sse(f"  [WARN] {img} : {r.stderr.strip()}")
                    errors.append(f"rmi EE {img}")
        else:
            yield _sse("[4/7] EE images — skipped (remove_ee=false)")

        # ── 5. Dangling / unused images ────────────────────────────────────
        if remove_images or remove_ee:
            yield _sse("  Pruning dangling images…")
            r = subprocess.run(
                ["docker", "image", "prune", "-f"],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                out = r.stdout.strip()
                yield _sse(f"  {out if out else 'No dangling images.'}")
            else:
                yield _sse(f"  [WARN] image prune: {r.stderr.strip()}")

        # ── 6. Build cache ─────────────────────────────────────────────────
        if prune_cache:
            yield _sse("[5/7] Purging Docker Buildx cache…")
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "builder",
                "prune",
                "-af",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for raw in proc.stdout:
                line = raw.decode().rstrip()
                if line:
                    yield _sse(f"  {line}")
            rc = await proc.wait()
            if rc == 0:
                yield _sse("  Buildx cache purged ✔")
            else:
                yield _sse(f"  [WARN] builder prune returned code {rc}")
                errors.append("builder prune")
        else:
            yield _sse("[5/7] Buildx cache — skipped (prune_cache=false)")

        # ── 7. Temp build dirs ─────────────────────────────────────────────
        if clean_tmp:
            yield _sse("[6/7] Removing temporary EE build directories…")
            import shutil

            tmp_dirs = glob.glob(_EE_TMP_GLOB)
            if not tmp_dirs:
                yield _sse("  No /tmp/ee-build-* directories found.")
            for d in tmp_dirs:
                try:
                    shutil.rmtree(d)
                    yield _sse(f"  ✔ removed: {d}")
                except Exception as exc:
                    yield _sse(f"  [WARN] {d} : {exc}")
                    errors.append(f"rmtree {d}")
        else:
            yield _sse("[6/7] /tmp directories — skipped (clean_tmp=false)")

        # ── 8. .env file ──────────────────────────────────────────────────
        if remove_env:
            yield _sse("[7/7] Removing .env file…")
            if ENV_FILE.exists():
                try:
                    ENV_FILE.unlink()
                    yield _sse("  ✔ .env removed — reconfigure via the wizard before redeploying.")
                except Exception as exc:
                    yield _sse(f"  [WARN] Cannot remove .env: {exc}")
                    errors.append(".env rm")
            else:
                yield _sse("  .env does not exist — nothing to remove.")
        else:
            yield _sse("[7/7] .env — kept (remove_env=false)")

        # ── Summary ────────────────────────────────────────────────────────
        yield _sse("─" * 55)
        if errors:
            yield _sse(f"[WARN] Cleanup completed with {len(errors)} warning(s): {', '.join(errors)}")
        else:
            yield _sse("[SUCCESS] Environment cleaned successfully ✔")

        # Show updated disk usage
        yield _sse("Disk usage after cleanup:")
        df = _docker_df()
        yield _sse(f"  Images      : {df['images']}")
        yield _sse(f"  Volumes     : {df['volumes']}")
        yield _sse(f"  Build cache : {df['build_cache']}")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
