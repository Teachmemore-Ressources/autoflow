"""
routers/ee.py — AWX image build, Execution Environments, Docker CA trust, Gitea network init.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from core.auth import _audit
from core.env import AWX_DOCKERFILE, CERTS_DIR, ROOT, _load_env
from core.shell import (
    PKI_URL,
    _async_sudo_exec,
    _gitea_api_url,
    _sse,
    _sudo_run,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── EE definitions ────────────────────────────────────────────────────────────

EE_DEFINITIONS = [
    {
        "id": "base",
        "label": "EE Base",
        "description": "Collections communes — community.general, ansible.posix, community.crypto",
        "version": "1.0.0",
    },
    {
        "id": "security",
        "label": "EE Security",
        "description": "Sécurité et conformité — boto3, openssl, community.crypto, ansible.utils",
        "version": "1.0.0",
    },
    {
        "id": "network",
        "label": "EE Network",
        "description": "Multi-vendor networking — NAPALM, Netmiko, Nornir, cisco.ios, junipernetworks.junos",
        "version": "1.0.0",
    },
]


def _find_ansible_builder() -> str | None:
    """Locate the ansible-builder binary (PATH → ~/.local/bin → pipx venv)."""
    import shutil
    found = shutil.which("ansible-builder")
    if found:
        return found
    for candidate in [
        Path.home() / ".local/bin/ansible-builder",
        Path.home() / ".local/pipx/venvs/ansible-builder/bin/ansible-builder",
    ]:
        if candidate.exists():
            return str(candidate)
    return None


# ── AWX custom image build ─────────────────────────────────────────────────────

@router.get("/api/build-awx")
async def build_awx(request: Request):
    """Build the custom AWX patched image (SSE stream)."""
    _audit(request, "awx.build_image")
    awx_version = _load_env().get("AWX_VERSION", "24.6.1")
    tag = f"autoflow/awx-patched:{awx_version}"

    async def stream():
        yield _sse(f"Building {tag} from awx/Dockerfile.patched")
        yield _sse("This may take 5–15 minutes depending on your connection…")

        proc = await asyncio.create_subprocess_exec(
            "docker", "build",
            "-f", str(AWX_DOCKERFILE),
            "--build-arg", f"AWX_VERSION={awx_version}",
            "-t", tag,
            str(ROOT / "awx"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()

        if proc.returncode == 0:
            yield _sse(f"[SUCCESS] {tag} built successfully.")
        else:
            yield _sse(f"[ERROR] docker build exited with code {proc.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/awx-image-status")
def awx_image_status():
    version = _load_env().get("AWX_VERSION", "24.6.1")
    tag = f"autoflow/awx-patched:{version}"
    r = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        capture_output=True, text=True,
    )
    return {"tag": tag, "exists": r.returncode == 0}


# ── Execution Environments ────────────────────────────────────────────────────

@router.get("/api/ee/status")
def ee_status():
    config     = _load_env()
    domain     = config.get("DOMAIN", "localhost")
    registry   = f"git.{domain}"
    gitea_user = config.get("GITEA_ADMIN_USER", "admin")

    ab = _find_ansible_builder()
    ab_version = None
    if ab:
        r = subprocess.run([ab, "--version"], capture_output=True, text=True)
        ab_version = r.stdout.strip() if r.returncode == 0 else None

    ee_images = []
    for ee in EE_DEFINITIONS:
        tag = f"{registry}/{gitea_user}/ee-{ee['id']}:{ee['version']}"
        r   = subprocess.run(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
            capture_output=True, text=True,
        )
        ee_images.append({**ee, "tag": tag, "built": r.returncode == 0})

    cert_dir      = Path(f"/etc/docker/certs.d/{registry}")
    docker_trusted = (cert_dir / "ca.crt").exists()

    return {
        "ansible_builder":         ab,
        "ansible_builder_version": ab_version,
        "registry":                registry,
        "docker_trusted":          docker_trusted,
        "ees":                     ee_images,
    }


@router.post("/api/ee/install-deps")
async def ee_install_deps():
    """SSE: install ansible-builder via pipx (compatible Debian/Ubuntu PEP-668)."""

    async def stream():
        ab = _find_ansible_builder()
        if ab:
            r = subprocess.run([ab, "--version"], capture_output=True, text=True)
            yield _sse(f"[SUCCESS] ansible-builder already installed: {r.stdout.strip()}")
            yield _sse("[DONE]")
            return

        # Priority 1: pipx
        pipx_check = subprocess.run(["which", "pipx"], capture_output=True, text=True)
        if pipx_check.returncode == 0:
            yield _sse("Installation via pipx...")
            proc = await asyncio.create_subprocess_exec(
                "pipx", "install", "ansible-builder",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in proc.stdout:
                yield _sse(line.decode().rstrip())
            await proc.wait()
            if proc.returncode == 0:
                yield _sse("[SUCCESS] ansible-builder installed via pipx ✔")
                yield _sse("[DONE]")
                return
            yield _sse("[WARN] pipx failed — trying alternative…")

        # Priority 2: pip --break-system-packages (Debian/Ubuntu 22+)
        yield _sse("Installation via pip3 --break-system-packages...")
        proc = await asyncio.create_subprocess_exec(
            "pip3", "install", "--user", "--break-system-packages", "ansible-builder",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()
        if proc.returncode == 0:
            yield _sse("[SUCCESS] ansible-builder installed ✔")
            yield _sse("Note: if the command is not found, reload the wizard (PATH updated).")
        else:
            yield _sse("[ERROR] Installation failed.")
            yield _sse("Run manually in a terminal: pipx install ansible-builder")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/ee/build")
async def ee_build(ee: str = "base", version: str = "1.0.0"):
    """SSE: ansible-builder build + docker push for a single EE."""

    async def stream():
        config     = _load_env()
        domain     = config.get("DOMAIN", "localhost")
        gitea_user = config.get("GITEA_ADMIN_USER", "admin")
        registry   = f"git.{domain}"

        ee_file = ROOT / "execution-environments" / ee / "execution-environment.yml"
        if not ee_file.exists():
            yield _sse(f"[ERROR] Definition not found: {ee_file}")
            yield _sse("[DONE]")
            return

        ab = _find_ansible_builder()
        if not ab:
            yield _sse("[ERROR] ansible-builder not found — run 'Install ansible-builder' first.")
            yield _sse("[DONE]")
            return

        tag     = f"{registry}/{gitea_user}/ee-{ee}:{version}"
        ctx_dir = f"/tmp/ee-build-{ee}"

        yield _sse(f"▶  Build EE '{ee}' → {tag}")
        yield _sse(f"   ansible-builder : {ab}")
        yield _sse("   This may take 10–20 min (downloading collections + pip)…")
        yield _sse("")

        # Docker login
        token = config.get("GITEA_REGISTRY_TOKEN", "") or config.get("GITEA_ADMIN_PASSWORD", "")
        if token:
            yield _sse(f"docker login {registry}...")
            login = subprocess.run(
                ["docker", "login", registry, "-u", gitea_user, "--password-stdin"],
                input=token, capture_output=True, text=True,
            )
            if login.returncode == 0:
                yield _sse("docker login OK ✔")
            else:
                yield _sse(f"[WARN] docker login: {login.stderr.strip()}")

        # ansible-builder build
        yield _sse("ansible-builder build...")
        build_proc = await asyncio.create_subprocess_exec(
            ab, "build",
            "--file",      str(ee_file),
            "--tag",       tag,
            "--context",   ctx_dir,
            "--build-arg", "PYCMD=/usr/bin/python3.12",
            "--verbosity", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in build_proc.stdout:
            yield _sse(line.decode().rstrip())
        await build_proc.wait()

        if build_proc.returncode != 0:
            yield _sse(f"[ERROR] ansible-builder failed (code {build_proc.returncode})")
            yield _sse("[DONE]")
            return

        yield _sse(f"Build OK — push vers {registry}...")

        push_rc = -1
        push_lines: list[str] = []
        push_proc = await asyncio.create_subprocess_exec(
            "docker", "push", tag,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in push_proc.stdout:
            ln = raw.decode().rstrip()
            push_lines.append(ln)
            yield _sse(ln)
        await push_proc.wait()
        push_rc = push_proc.returncode

        if push_rc == 0:
            # Push immutable dated tag for rollback capability
            from datetime import datetime as _dt
            from datetime import timezone as _tz
            dated_tag = _dt.now(_tz.utc).strftime("%Y%m%d-%H%M%S")
            dated_image = f"{registry}/{gitea_user}/ee-{ee}:{dated_tag}"
            yield _sse(f"Pushing dated tag {dated_tag} for rollback…")
            tag_r = subprocess.run(
                ["docker", "tag", tag, dated_image],
                capture_output=True, text=True,
            )
            if tag_r.returncode == 0:
                dt_push = await asyncio.create_subprocess_exec(
                    "docker", "push", dated_image,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                await dt_push.wait()
                if dt_push.returncode == 0:
                    yield _sse(f"  Dated tag pushed: {dated_tag} ✔")
                else:
                    yield _sse("  [WARN] Dated tag push failed — rollback won't include this build")
            else:
                yield _sse(f"  [WARN] docker tag failed: {tag_r.stderr.strip()}")
            yield _sse(f"[SUCCESS] ee-{ee}:{version} built and pushed ✔")
        else:
            push_out = "\n".join(push_lines)
            if "certificate signed by unknown authority" in push_out or "x509" in push_out:
                yield _sse("[WARN] TLS error detected — auto-configuring system CA…")
                _ca_file = CERTS_DIR / f"ca.{domain}.crt"
                if _ca_file.exists():
                    _ca_pem = _ca_file.read_text()
                    _sys_ca = "/usr/local/share/ca-certificates/autoflow-registry-ca.crt"
                    import tempfile as _t2
                    with _t2.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as _tf2:
                        _tf2.write(_ca_pem)
                        _tp2 = _tf2.name
                    _sr = _sudo_run(
                        ["bash", "-c",
                         f"cp '{_tp2}' '{_sys_ca}' && chmod 644 '{_sys_ca}'"
                         f" && update-ca-certificates --fresh 2>&1 | tail -3"
                         f" && systemctl restart docker"],
                        capture_output=True, text=True,
                    )
                    Path(_tp2).unlink(missing_ok=True)
                    if _sr.returncode == 0:
                        yield _sse("System CA updated + Docker restarted ✔")
                        yield _sse("Re-run the build to push the image.")
                    else:
                        yield _sse(f"[WARN] Auto-fix failed: {_sr.stderr.strip()[:200]}")
                        yield _sse("[WARN] Run 'Configure Docker CA' manually then retry the build.")
                else:
                    yield _sse("[WARN] CA file not found — run 'Configure Docker CA' first.")
            else:
                yield _sse(f"[ERROR] docker push failed (code {push_rc})")
                yield _sse("[WARN] Check: 1) 'Configure Docker CA' 2) GITEA_REGISTRY_TOKEN in .env")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/ee/versions/{ee}")
def ee_versions(ee: str):
    """List available tags in the Gitea container registry for a given EE."""
    import base64
    import json as _j
    import urllib.error
    import urllib.request
    config     = _load_env()
    domain     = config.get("DOMAIN", "localhost")
    gitea_user = config.get("GITEA_ADMIN_USER", "admin")
    registry   = f"git.{domain}"
    token      = config.get("GITEA_REGISTRY_TOKEN", "") or config.get("GITEA_ADMIN_PASSWORD", "")
    creds      = base64.b64encode(f"{gitea_user}:{token}".encode()).decode()

    # Use Docker Registry v2 API on the intra-stack hostname (no TLS needed)
    # Fallback to the external hostname if intra-stack is not available
    for base in ("http://gitea:3001", f"https://{registry}"):
        url = f"{base}/v2/{gitea_user}/ee-{ee}/tags/list"
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
        try:
            ctx = None
            if base.startswith("https"):
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode    = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
                data  = _j.loads(resp.read())
                tags  = data.get("tags") or []
                dated = sorted([t for t in tags if len(t) == 15 and t[8] == "-"], reverse=True)
                other = [t for t in tags if t not in dated]
                return {
                    "ee":         ee,
                    "image_base": f"{registry}/{gitea_user}/ee-{ee}",
                    "dated_tags": dated,
                    "other_tags": other,
                    "total":      len(tags),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"ee": ee, "image_base": f"{registry}/{gitea_user}/ee-{ee}",
                        "dated_tags": [], "other_tags": [], "total": 0}
        except Exception:
            continue

    raise HTTPException(502, "Could not reach Gitea container registry — is Gitea running?")


@router.get("/api/ee/rollback")
async def ee_rollback(ee: str, version: str):
    """SSE: roll back an EE image by re-tagging a dated version as 'latest' and pushing."""

    async def stream():
        config     = _load_env()
        domain     = config.get("DOMAIN", "localhost")
        gitea_user = config.get("GITEA_ADMIN_USER", "admin")
        registry   = f"git.{domain}"
        default_v  = config.get("EE_DEFAULT_VERSION", "latest")

        source = f"{registry}/{gitea_user}/ee-{ee}:{version}"
        target = f"{registry}/{gitea_user}/ee-{ee}:{default_v}"

        yield _sse(f"Rolling back ee-{ee} to {version}…")
        yield _sse(f"Source : {source}")
        yield _sse(f"Target : {target}")
        yield _sse("")

        # Step 1: pull the dated image
        yield _sse(f"[1/3] docker pull {source}…")
        pull_proc = await asyncio.create_subprocess_exec(
            "docker", "pull", source,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in pull_proc.stdout:
            ln = raw.decode().rstrip()
            if ln:
                yield _sse(f"  {ln}")
        await pull_proc.wait()
        if pull_proc.returncode != 0:
            yield _sse(f"[ERROR] docker pull failed (exit {pull_proc.returncode})")
            yield _sse("[DONE]")
            return
        yield _sse("[1/3] Pull OK ✔")

        # Step 2: re-tag
        yield _sse(f"[2/3] docker tag → {target}…")
        tag_r = subprocess.run(["docker", "tag", source, target], capture_output=True, text=True)
        if tag_r.returncode != 0:
            yield _sse(f"[ERROR] docker tag failed: {tag_r.stderr.strip()}")
            yield _sse("[DONE]")
            return
        yield _sse("[2/3] Tag OK ✔")

        # Step 3: push floating tag
        yield _sse(f"[3/3] docker push {target}…")
        push_proc = await asyncio.create_subprocess_exec(
            "docker", "push", target,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in push_proc.stdout:
            ln = raw.decode().rstrip()
            if ln:
                yield _sse(f"  {ln}")
        await push_proc.wait()
        if push_proc.returncode != 0:
            yield _sse(f"[ERROR] docker push failed (exit {push_proc.returncode})")
            yield _sse("[DONE]")
            return
        yield _sse("[3/3] Push OK ✔")
        yield _sse("")
        yield _sse(f"[SUCCESS] ee-{ee} rolled back to {version} → now serves as {default_v} ✔")
        yield _sse("AWX will use the rolled-back image on next job run.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/docker/trust-ca")
async def docker_trust_ca():
    """SSE: install the PKI Root CA into Docker certs.d for the Gitea registry."""

    async def stream():
        import shlex

        import httpx as _httpx

        config   = _load_env()
        domain   = config.get("DOMAIN", "localhost")
        registry = f"git.{domain}"

        yield _sse(f"Configuration du CA Docker pour le registry {registry}...")

        ca_pem = ""

        # Try 1: PKI API (local port)
        try:
            async with _httpx.AsyncClient() as c:
                r = await c.get(f"{PKI_URL}/api/ca/list", timeout=4)
                if r.status_code == 200:
                    cas = r.json()
                    ca_name = (cas[0].get("name") if isinstance(cas, list) and cas
                               else cas.get("cas", [{}])[0].get("name", ""))
                    if ca_name:
                        rc = await c.get(f"{PKI_URL}/api/ca/{ca_name}/cert.pem", timeout=4)
                        if rc.status_code == 200 and "BEGIN CERTIFICATE" in rc.text:
                            ca_pem = rc.text
                            yield _sse(f"CA '{ca_name}' retrieved from PKI service.")
        except Exception as e:
            yield _sse(f"Local PKI not available ({type(e).__name__}) — trying local file…")

        # Try 2: ca.<domain>.crt written by generate-cert
        if not ca_pem:
            for candidate in [
                CERTS_DIR / f"ca.{domain}.crt",
                CERTS_DIR / f"wildcard.{domain}.crt",
            ]:
                if candidate.exists():
                    ca_pem = candidate.read_text()
                    yield _sse(f"CA loaded from {candidate.name}")
                    break

        if not ca_pem:
            yield _sse("[ERROR] CA certificate not found.")
            yield _sse("Run 'Setup PKI & Generate cert' in Pre-flight first.")
            yield _sse("[DONE]")
            return

        cert_dir = Path(f"/etc/docker/certs.d/{registry}")
        try:
            cert_dir.mkdir(parents=True, exist_ok=True)
            (cert_dir / "ca.crt").write_text(ca_pem)
            (cert_dir / "ca.crt").chmod(0o644)
            yield _sse(f"CA written to {cert_dir}/ca.crt ✔")
        except PermissionError:
            yield _sse("Permission denied — retrying via sudo…")
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as tmp:
                tmp.write(ca_pem)
                tmp_path = tmp.name
            r = _sudo_run(
                ["bash", "-c",
                 f"mkdir -p '{cert_dir}' && cp '{tmp_path}' '{cert_dir}/ca.crt' "
                 f"&& chmod 644 '{cert_dir}/ca.crt'"],
                capture_output=True, text=True,
            )
            Path(tmp_path).unlink(missing_ok=True)
            if r.returncode != 0:
                yield _sse(f"[ERROR] sudo failed: {r.stderr.strip()}")
                yield _sse("Run manually: make docker-trust-ca")
                yield _sse("[DONE]")
                return
            yield _sse(f"CA installed via sudo in {cert_dir}/ca.crt ✔")

        # Install CA in the system trust store so the docker credential helper trusts it
        # (the credential helper uses system TLS, NOT /etc/docker/certs.d/)
        import tempfile as _tempfile_sys
        with _tempfile_sys.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as _sf:
            _sf.write(ca_pem)
            _sys_tmp = _sf.name
        _sys_ca_dst = "/usr/local/share/ca-certificates/autoflow-registry-ca.crt"
        _sys_r = _sudo_run(
            ["bash", "-c",
             f"cp '{_sys_tmp}' '{_sys_ca_dst}' && chmod 644 '{_sys_ca_dst}' "
             f"&& update-ca-certificates --fresh 2>&1 | tail -3"],
            capture_output=True, text=True,
        )
        Path(_sys_tmp).unlink(missing_ok=True)
        if _sys_r.returncode == 0:
            yield _sse("CA added to system store + update-ca-certificates ✔")
            if _sys_r.stdout.strip():
                yield _sse(_sys_r.stdout.strip())
        else:
            yield _sse(f"[WARN] System CA store not updated: {_sys_r.stderr.strip()}")

        # Also add to insecure-registries in daemon.json so docker push bypasses
        # TLS verification for this local registry (belt + suspenders approach)
        import json as _json_mod
        daemon_json = Path("/etc/docker/daemon.json")
        try:
            existing_cfg = _json_mod.loads(daemon_json.read_text()) if daemon_json.exists() else {}
        except Exception:
            existing_cfg = {}
        insecure = existing_cfg.get("insecure-registries", [])
        if registry not in insecure:
            insecure.append(registry)
            existing_cfg["insecure-registries"] = insecure
            daemon_content = _json_mod.dumps(existing_cfg, indent=2)
            import tempfile as _tmp_mod
            with _tmp_mod.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
                tf.write(daemon_content)
                tf_path = tf.name
            ir = _sudo_run(
                ["bash", "-c",
                 f"cp '{tf_path}' /etc/docker/daemon.json && chmod 644 /etc/docker/daemon.json"],
                capture_output=True, text=True,
            )
            Path(tf_path).unlink(missing_ok=True)
            if ir.returncode == 0:
                yield _sse(f"insecure-registries → {registry} added to daemon.json ✔")
            else:
                yield _sse(f"[WARN] daemon.json not updated: {ir.stderr.strip()}")
        else:
            yield _sse(f"insecure-registries already configured for {registry}.")

        # Restart Docker daemon so it trusts the new CA cert
        yield _sse("Restarting Docker daemon to load the new CA…")
        yield _sse("(Stopping running containers — this may take 30–60 s…)")
        restart_proc = await _async_sudo_exec(
            ["systemctl", "restart", "docker"],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Heartbeat while waiting (up to 90 s)
        deadline = asyncio.get_running_loop().time() + 90
        while True:
            try:
                rc = await asyncio.wait_for(restart_proc.wait(), timeout=10)
                break
            except asyncio.TimeoutError:
                if asyncio.get_running_loop().time() > deadline:
                    restart_proc.kill()
                    yield _sse("[WARN] Docker restart timed out — run: sudo systemctl restart docker")
                    rc = -1
                    break
                yield _sse("…waiting for Docker daemon…")
        if rc == 0:
            yield _sse("Docker daemon restarted ✔ (containers will come back automatically)")
            await asyncio.sleep(3)
        else:
            stderr_out = (await restart_proc.stderr.read()).decode().strip() if restart_proc.stderr else ""
            yield _sse(f"[WARN] Docker restart failed (code {rc}): {stderr_out}")
            yield _sse("[WARN] Run manually: sudo systemctl restart docker")

        # Ensure registry hostname resolves (add to /etc/hosts if needed)
        import socket as _socket
        try:
            _socket.getaddrinfo(registry, 443)
        except _socket.gaierror:
            yield _sse(f"[WARN] {registry} not resolved by DNS — adding to /etc/hosts…")
            hosts_line = f"127.0.0.1  {registry}"
            check = subprocess.run(["grep", "-qF", registry, "/etc/hosts"], capture_output=True)
            if check.returncode != 0:
                # Use shlex.quote to prevent shell injection via a crafted DOMAIN value.
                add = _sudo_run(
                    ["bash", "-c", f"printf '%s\\n' {shlex.quote(hosts_line)} >> /etc/hosts"],
                    capture_output=True, text=True,
                )
                if add.returncode == 0:
                    yield _sse(f"Entry added to /etc/hosts: {hosts_line} ✔")
                else:
                    yield _sse(f"[WARN] Cannot write to /etc/hosts: {add.stderr.strip()}")
                    yield _sse(f"[WARN] Add manually: echo {shlex.quote(hosts_line)} | sudo tee -a /etc/hosts")  # noqa: E501
            else:
                yield _sse(f"Entry already present in /etc/hosts for {registry}.")

        # Test docker login — auto-generate registry token if needed
        user     = config.get("GITEA_ADMIN_USER", "admin")
        password = config.get("GITEA_ADMIN_PASSWORD", "")
        reg_token = config.get("GITEA_REGISTRY_TOKEN", "")

        def _try_docker_login(secret: str) -> bool:
            r = subprocess.run(
                ["docker", "login", registry, "-u", user, "--password-stdin"],
                input=secret, capture_output=True, text=True,
            )
            return r.returncode == 0

        yield _sse(f"Test docker login {registry}...")

        if reg_token and _try_docker_login(reg_token):
            yield _sse(f"[SUCCESS] docker login {registry} → OK ✔")
        else:
            if reg_token:
                yield _sse("[WARN] GITEA_REGISTRY_TOKEN invalid — generating a new token via the Gitea API…")
            else:
                yield _sse("GITEA_REGISTRY_TOKEN missing — auto-generating via the Gitea API…")

            # Try to generate a token via the Gitea API using admin credentials
            gitea_api = _gitea_api_url()
            new_token = ""
            api_returned_404 = False
            try:
                async with _httpx.AsyncClient(verify=False) as c:
                    # Delete existing token with same name (ignore errors)
                    await c.delete(
                        f"{gitea_api}/users/{user}/tokens/autoflow-registry",
                        auth=(user, password), timeout=5,
                    )
                    # Create new token with package scope
                    resp = await c.post(
                        f"{gitea_api}/users/{user}/tokens",
                        auth=(user, password),
                        json={"name": "autoflow-registry", "scopes": ["read:package", "write:package"]},
                        timeout=5,
                    )
                    if resp.status_code == 201:
                        new_token = resp.json().get("sha1", "")
                    elif resp.status_code == 404:
                        api_returned_404 = True
                        yield _sse("[WARN] Gitea API 404 — admin not created yet, initializing…")
                    else:
                        yield _sse(f"[WARN] API Gitea {resp.status_code}: {resp.text[:200]}")
            except Exception as exc:
                yield _sse(f"[WARN] Gitea API connection failed: {exc}")

            # If admin doesn't exist yet, create it then generate token via CLI
            if api_returned_404:
                email = config.get("GITEA_ADMIN_EMAIL", f"{user}@localhost")
                yield _sse(f"Creating Gitea admin account '{user}'…")
                init_proc = await asyncio.create_subprocess_exec(
                    "docker", "exec", "autoflow_gitea",
                    "gitea", "admin", "user", "create",
                    "--username", user, "--password", password,
                    "--email", email, "--admin", "--must-change-password=false",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                async for line in init_proc.stdout:
                    yield _sse(line.decode().rstrip())
                await init_proc.wait()

                if init_proc.returncode == 0:
                    yield _sse(f"Admin '{user}' created — generating token via CLI…")
                else:
                    yield _sse("[WARN] Admin creation failed (may already exist) — trying CLI token…")

                # Generate token directly via Gitea CLI (no HTTP dependency)
                tok_proc = await asyncio.create_subprocess_exec(
                    "docker", "exec", "autoflow_gitea",
                    "gitea", "admin", "user", "generate-access-token",
                    "--username", user, "--token-name", "autoflow-registry",
                    "--scopes", "read:package,write:package", "--raw",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                tok_stdout, tok_stderr = await tok_proc.communicate()
                if tok_proc.returncode == 0:
                    new_token = tok_stdout.decode().strip()
                    yield _sse("Token generated via Gitea CLI ✔")
                else:
                    yield _sse(f"[WARN] CLI token failed: {tok_stderr.decode().strip()[:200]}")

            if new_token:
                yield _sse("Token generated — writing to .env (GITEA_REGISTRY_TOKEN)…")
                from core.env import _write_env as _we
                current = _load_env()
                current["GITEA_REGISTRY_TOKEN"] = new_token
                _we(current)
                if _try_docker_login(new_token):
                    yield _sse(f"[SUCCESS] docker login {registry} → OK ✔")
                else:
                    yield _sse("[WARN] Token generated but docker login still failing.")
                    yield _sse("[WARN] Ensure Gitea is running and packages are enabled.")
            else:
                # Last resort: try with admin password directly
                if password and _try_docker_login(password):
                    yield _sse("[SUCCESS] docker login with admin password → OK ✔")
                    yield _sse("[WARN] Using admin password — generate a dedicated token in Gitea > Settings > Applications")  # noqa: E501
                else:
                    yield _sse("[WARN] docker login failed — is Gitea running and reachable?")
                    yield _sse(
                        f"[WARN] Generate manually: Gitea > {user} > Settings > Applications "
                        f"> Generate Token (scopes: package)"
                    )
                    yield _sse("[WARN] Then set in .env: GITEA_REGISTRY_TOKEN=<token>")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/gitea/init-network")
async def gitea_init_network():
    """SSE: create network-playbooks repo in Gitea and push initial content."""

    async def stream():
        script = ROOT / "scripts" / "gitea-init-network.sh"
        if not script.exists():
            yield _sse(f"[ERROR] Script not found: {script}")
            yield _sse("[DONE]")
            return

        config = _load_env()
        env    = {
            **os.environ,
            "GITEA_ROOT_URL":       config.get("GITEA_ROOT_URL", ""),
            "GITEA_ADMIN_USER":     config.get("GITEA_ADMIN_USER", "admin"),
            "GITEA_ADMIN_PASSWORD": config.get("GITEA_ADMIN_PASSWORD", ""),
            "AUTOFLOW_ROOT":        str(ROOT),
        }

        yield _sse("Initialisation du repo Gitea 'network-playbooks'...")

        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()

        if proc.returncode == 0:
            gitea_url  = config.get("GITEA_ROOT_URL", "")
            gitea_user = config.get("GITEA_ADMIN_USER", "admin")
            yield _sse(f"[SUCCESS] Repo available at {gitea_url}/{gitea_user}/network-playbooks ✔")
        else:
            yield _sse(f"[ERROR] Initialization failed (code {proc.returncode})")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
