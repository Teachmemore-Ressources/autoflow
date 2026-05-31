"""
routers/preflight.py — System pre-flight checks: Docker, RAM, disk, DNS, NTP/time.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from pathlib import Path

from core.auth import _audit
from core.env import ROOT, _load_env
from core.shell import _async_sudo_exec, _sse, _sudo_run
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


# ── DNS helpers ───────────────────────────────────────────────────────────────


def _dns_candidates() -> list[str]:
    """Read non-loopback nameservers from resolv.conf files, append public fallbacks."""
    import ipaddress

    candidates: list[str] = []
    for path in ["/run/systemd/resolve/resolv.conf", "/etc/resolv.conf"]:
        try:
            for line in Path(path).read_text().splitlines():
                parts = line.strip().split()
                if parts and parts[0] == "nameserver" and len(parts) >= 2:
                    ip = parts[1]
                    try:
                        addr = ipaddress.ip_address(ip)
                        if not addr.is_loopback and not (addr.version == 6 and addr.is_link_local):
                            if ip not in candidates:
                                candidates.append(ip)
                    except ValueError:
                        pass
        except Exception:
            pass
    for fb in ["1.1.1.1", "8.8.8.8"]:
        if fb not in candidates:
            candidates.append(fb)
    return candidates


# ── NTP helpers ───────────────────────────────────────────────────────────────


def _detect_ntp_service() -> str:
    """Return 'chrony', 'timesyncd', or 'none'."""
    import shutil

    if shutil.which("chronyc"):
        r = subprocess.run(["systemctl", "is-active", "chrony"], capture_output=True, text=True)
        if r.stdout.strip() in ("active", "activating"):
            return "chrony"
        # Debian/Ubuntu package name
        r2 = subprocess.run(["systemctl", "is-active", "chronyd"], capture_output=True, text=True)
        if r2.stdout.strip() in ("active", "activating"):
            return "chrony"
    r = subprocess.run(["systemctl", "is-active", "systemd-timesyncd"], capture_output=True, text=True)
    if r.stdout.strip() == "active":
        return "timesyncd"
    return "none"


def _chrony_tracking() -> dict:
    """Parse `chronyc tracking` into a dict."""
    r = subprocess.run(["chronyc", "tracking"], capture_output=True, text=True)
    result: dict = {}
    if r.returncode != 0:
        return result
    for line in r.stdout.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def _chrony_sources() -> list[dict]:
    """Parse `chronyc sources -v` → list of {name, stratum, offset_ms, reachable}."""
    r = subprocess.run(["chronyc", "sources", "-v"], capture_output=True, text=True)
    sources = []
    if r.returncode != 0:
        return sources
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("M") or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 7:
            try:
                sources.append(
                    {
                        "mode": parts[0],
                        "name": parts[1],
                        "stratum": parts[2],
                        "offset_ms": parts[6],
                        "reachable": parts[0] in ("^*", "^+", "=*", "=+"),
                    }
                )
            except Exception:
                pass
    return sources


def _timesyncd_status() -> dict:
    """Return useful fields from `timedatectl show`."""
    r = subprocess.run(["timedatectl", "show"], capture_output=True, text=True)
    result: dict = {}
    if r.returncode != 0:
        # Fallback: timedatectl (human-readable)
        r2 = subprocess.run(["timedatectl"], capture_output=True, text=True)
        for line in r2.stdout.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
        return result
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _container_time_drift(container: str) -> str | None:
    """Return ISO timestamp from a running container, or None."""
    r = subprocess.run(
        ["docker", "exec", container, "date", "-u", "+%Y-%m-%dT%H:%M:%S"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return r.stdout.strip() if r.returncode == 0 else None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/api/system/preflight")
def system_preflight():
    """Check Docker version, RAM, disk space and DNS before deployment."""
    import re
    import shutil as _sh

    checks: list[dict] = []

    # ── Docker engine ─────────────────────────────────────────────────────────
    r = subprocess.run(["docker", "--version"], capture_output=True, text=True)
    if r.returncode == 0:
        m = re.search(r"(\d+)\.(\d+)", r.stdout)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            ok = major >= 24
            checks.append(
                {
                    "id": "docker",
                    "label": "Docker ≥ 24",
                    "ok": ok,
                    "detail": f"Docker {major}.{minor}"
                    + ("" if ok else " — need ≥ 24, upgrade: https://docs.docker.com/engine/install/"),
                }
            )
        else:
            checks.append({"id": "docker", "label": "Docker ≥ 24", "ok": False, "detail": r.stdout.strip()})
    else:
        checks.append(
            {"id": "docker", "label": "Docker ≥ 24", "ok": False, "detail": "docker not found in PATH"}
        )

    # ── Docker Compose plugin ─────────────────────────────────────────────────
    rc = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if rc.returncode == 0:
        version_line = rc.stdout.strip().split("\n")[0]
        checks.append({"id": "compose", "label": "Docker Compose plugin", "ok": True, "detail": version_line})
    else:
        checks.append(
            {
                "id": "compose",
                "label": "Docker Compose plugin",
                "ok": False,
                "detail": "docker compose plugin not found — install: sudo apt install docker-compose-plugin",
            }
        )

    # ── RAM ≥ 4 GB ────────────────────────────────────────────────────────────
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gb = kb / 1024 / 1024
                    ok = gb >= 4.0
                    checks.append(
                        {
                            "id": "ram",
                            "label": "RAM ≥ 4 GB",
                            "ok": ok,
                            "detail": f"{gb:.1f} GB available" + ("" if ok else " — AWX alone needs ≥ 4 GB"),
                        }
                    )
                    break
    except Exception as exc:
        checks.append(
            {
                "id": "ram",
                "label": "RAM ≥ 4 GB",
                "ok": False,
                "detail": f"could not read /proc/meminfo: {exc}",
            }
        )

    # ── Disk ≥ 20 GB free ─────────────────────────────────────────────────────
    try:
        usage = _sh.disk_usage(str(ROOT))
        free = usage.free / 1024**3
        total = usage.total / 1024**3
        ok = free >= 20.0
        checks.append(
            {
                "id": "disk",
                "label": "Disk ≥ 20 GB free",
                "ok": ok,
                "detail": f"{free:.1f} GB free / {total:.1f} GB total"
                + ("" if ok else " — AWX images + DB need ≥ 20 GB"),
            }
        )
    except Exception as exc:
        checks.append({"id": "disk", "label": "Disk ≥ 20 GB free", "ok": False, "detail": str(exc)})

    # ── vm.overcommit_memory ─────────────────────────────────────────────────
    # Required for Redis: without it, fork() for background saves / AOF rewrites
    # may be refused even when RAM is available, causing the incremental AOF to
    # grow without bound and become corrupted on a hard kill.
    try:
        val = Path("/proc/sys/vm/overcommit_memory").read_text().strip()
        ok = val == "1"
        checks.append(
            {
                "id": "overcommit",
                "label": "vm.overcommit_memory = 1",
                "ok": ok,
                "detail": f"Current value: {val}"
                + (
                    ""
                    if ok
                    else " — Redis background saves will fail, risking AOF corruption."
                    " Fix: echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/10-redis.conf"
                    " && sudo sysctl -p /etc/sysctl.d/10-redis.conf"
                ),
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "overcommit",
                "label": "vm.overcommit_memory = 1",
                "ok": False,
                "detail": f"Cannot read /proc/sys/vm/overcommit_memory: {exc}",
            }
        )

    # ── DNS resolution ────────────────────────────────────────────────────────
    dns_hosts = ["github.com", "registry-1.docker.io"]
    dns_ok = True
    dns_parts: list[str] = []
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(3)
    try:
        for host in dns_hosts:
            try:
                socket.getaddrinfo(host, 443)
                dns_parts.append(f"{host} OK")
            except Exception:
                dns_ok = False
                dns_parts.append(f"{host} FAILED")
    finally:
        socket.setdefaulttimeout(prev_timeout)

    checks.append(
        {
            "id": "dns",
            "label": "DNS resolution",
            "ok": dns_ok,
            "detail": "  |  ".join(dns_parts)
            + ("" if dns_ok else " — check /etc/resolv.conf and network connectivity"),
        }
    )

    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}


@router.get("/api/preflight/ee-dns")
def preflight_ee_dns():
    """Fast config-only status check — no Docker, instant response.

    Returns the current EE_DNS_SERVER value and the host DNS candidates.
    Use /api/preflight/ee-dns/stream for the actual bridge-network test.
    """
    current = _load_env().get("EE_DNS_SERVER", "").strip()
    candidates = _dns_candidates()
    if not current:
        return {
            "mode": "auto",
            "status": "ok",
            "current": "",
            "candidates": candidates,
        }
    return {
        "mode": "manual",
        "status": "manual",
        "current": current,
        "candidates": candidates,
    }


@router.get("/api/preflight/ee-dns/stream")
def preflight_ee_dns_stream():
    """SSE streaming endpoint — runs a bridge-network DNS test with live log output.

    Each event is a plain-text log line.  The last event starts with RESULT:
    and contains the JSON summary the UI needs to update the status card.
    """

    def generate():
        def sse(line: str) -> str:
            return f"data: {line}\n\n"

        candidates = _dns_candidates()

        yield sse("🔍 Reading upstream DNS servers from the host system…")
        for path in ["/run/systemd/resolve/resolv.conf", "/etc/resolv.conf"]:
            try:
                for raw in Path(path).read_text().splitlines():
                    parts = raw.strip().split()
                    if parts and parts[0] == "nameserver" and len(parts) >= 2:
                        ip = parts[1]
                        if ip in candidates:
                            yield sse(f"   → {ip}  (lu depuis {path})")
            except Exception:
                pass
        yield sse("   → 1.1.1.1  (fallback public)")
        yield sse("   → 8.8.8.8  (fallback public)")
        yield sse("")

        # Ensure alpine image is available locally
        check_img = subprocess.run(
            ["docker", "image", "inspect", "alpine", "--format", "ok"],
            capture_output=True,
            text=True,
        )
        if check_img.stdout.strip() != "ok":
            yield sse("⬇️  Alpine image not in local cache — pulling…")
            pull = subprocess.run(
                ["docker", "pull", "alpine"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if pull.returncode != 0:
                yield sse(f"❌ docker pull alpine failed: {pull.stderr.strip()}")
                result_data = json.dumps(
                    {
                        "candidates": candidates,
                        "results": [],
                        "working": None,
                        "current": _load_env().get("EE_DNS_SERVER", "").strip(),
                    }
                )
                yield f"data: RESULT:{result_data}\n\n"
                return
            yield sse("   ✅ alpine pulled")
        else:
            yield sse("🐳 Alpine image already in local cache")

        yield sse("")
        yield sse("🌐 Testing from the Docker bridge network (same context as AWX EEs)…")
        yield sse("   Domain tested: galaxy.ansible.com")
        yield sse("   Timeout par serveur : 3 s")
        yield sse("")

        # Build a script that tests each candidate — busybox nslookup syntax
        test_lines = []
        for ip in candidates:
            # busybox nslookup does not support -timeout; wrap with timeout(1)
            test_lines.append(
                f"if timeout 3 nslookup galaxy.ansible.com {ip} >/dev/null 2>&1; "
                f'then echo "OK:{ip}"; else echo "FAIL:{ip}"; fi'
            )
        test_script = "\n".join(test_lines)

        results: list[dict] = []
        working: str | None = None

        try:
            r = subprocess.run(
                ["docker", "run", "--rm", "--network", "bridge", "alpine", "sh", "-c", test_script],
                capture_output=True,
                text=True,
                timeout=len(candidates) * 5 + 15,
            )
            for line in r.stdout.splitlines():
                if line.startswith("OK:"):
                    ip = line[3:]
                    results.append({"dns": ip, "ok": True})
                    if working is None:
                        working = ip
                    yield sse(f"   ✅ {ip:<18}  responds from bridge (galaxy.ansible.com OK)")
                elif line.startswith("FAIL:"):
                    ip = line[5:]
                    results.append({"dns": ip, "ok": False})
                    yield sse(f"   ❌ {ip:<18}  timeout ou NXDOMAIN depuis bridge")
            if r.stderr.strip():
                yield sse(f"   [stderr] {r.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            yield sse("⏱ Global timeout exceeded")
            results = [{"dns": ip, "ok": False, "error": "timeout"} for ip in candidates]
        except Exception as exc:
            yield sse(f"❌ Unexpected error: {exc}")
            results = [{"dns": ip, "ok": False, "error": str(exc)} for ip in candidates]

        current = _load_env().get("EE_DNS_SERVER", "").strip()
        yield sse("")
        if working:
            if not current:
                yield sse(f"✅ DNS working: {working}")
                yield sse("   EE_DNS_SERVER is empty — Docker handles it automatically (recommended)")
            elif current == working:
                yield sse(f"✅ EE_DNS_SERVER={current} — responds from bridge")
            else:
                yield sse(f"⚠  EE_DNS_SERVER={current} does not respond from bridge")
                yield sse(f"   Working server available: {working}")
        else:
            yield sse("❌ No DNS server responds from the Docker bridge")
            yield sse("   Check network connectivity or leave EE_DNS_SERVER empty (Docker handles it)")

        final = {
            "candidates": candidates,
            "results": results,
            "working": working,
            "current": current,
            "needs_update": bool(current) and current != working,
        }
        yield f"data: RESULT:{json.dumps(final)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/api/preflight/apply-dns")
def preflight_apply_dns(data: dict):
    """Save EE_DNS_SERVER to .env (empty string = let Docker manage)."""
    value = str(data.get("value", "")).strip()
    current = _load_env()
    current["EE_DNS_SERVER"] = value
    from core.env import _write_env

    _write_env(current)
    return {"ok": True, "value": value}


@router.get("/api/preflight/time")
def preflight_time():
    """Return NTP sync status, drift, timezone and container clock comparison."""
    import datetime

    host_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ntp_svc = _detect_ntp_service()

    # ── Sync state ────────────────────────────────────────────────────────────
    synced = False
    drift_ms = None  # float ms
    ntp_servers: list[str] = []
    detail = {}

    if ntp_svc == "chrony":
        tracking = _chrony_tracking()
        detail["tracking"] = tracking
        ref = tracking.get("Reference ID", "")
        leap = tracking.get("Leap status", "")
        synced = "Normal" in leap or ref not in ("", "00000000 ()")
        # Parse offset  e.g. "0.000123456 seconds slow of NTP time"
        sys_time = tracking.get("System time", "")
        try:
            drift_ms = abs(float(sys_time.split()[0])) * 1000
        except Exception:
            pass
        sources = _chrony_sources()
        ntp_servers = [s["name"] for s in sources if s["name"] not in ("127.127.1.0",)]
        detail["sources"] = sources

    elif ntp_svc == "timesyncd":
        ts = _timesyncd_status()
        detail["timedatectl"] = ts
        synced_val = ts.get("NTPSynchronized", ts.get("NTP synchronized", "no"))
        synced = synced_val.lower() in ("yes", "true", "1")
        server = ts.get("NTPServer", ts.get("Server", ""))
        if server:
            ntp_servers = [server]

    else:
        # Fallback: read timedatectl anyway (may still work without a named service)
        ts = _timesyncd_status()
        detail["timedatectl"] = ts
        synced_val = ts.get("NTPSynchronized", ts.get("NTP synchronized", "no"))
        synced = synced_val.lower() in ("yes", "true", "1")

    # ── Timezone ──────────────────────────────────────────────────────────────
    tz_r = subprocess.run(
        ["timedatectl", "show", "--property=Timezone", "--value"], capture_output=True, text=True
    )
    timezone = tz_r.stdout.strip() if tz_r.returncode == 0 else "unknown"
    if not timezone:
        tz_r2 = subprocess.run(["cat", "/etc/timezone"], capture_output=True, text=True)
        timezone = tz_r2.stdout.strip() or "unknown"

    # ── Container clock check ─────────────────────────────────────────────────
    containers_to_check = ["autoflow_grafana", "autoflow_loki", "autoflow_awx_web"]
    container_clocks: list[dict] = []
    import datetime as _dt

    host_ts = _dt.datetime.now(_dt.timezone.utc)
    for cname in containers_to_check:
        ct = _container_time_drift(cname)
        if ct:
            try:
                ct_ts = _dt.datetime.fromisoformat(ct).replace(tzinfo=_dt.timezone.utc)
                delta_ms = abs((host_ts - ct_ts).total_seconds() * 1000)
                container_clocks.append(
                    {
                        "container": cname,
                        "time": ct,
                        "delta_ms": round(delta_ms, 1),
                        "ok": delta_ms < 2000,
                    }
                )
            except Exception:
                pass

    # ── Drift severity ────────────────────────────────────────────────────────
    severity = "ok"
    if not synced:
        severity = "warn"
    if drift_ms is not None:
        if drift_ms > 5000:
            severity = "critical"  # TLS / JWT at risk
        elif drift_ms > 1000:
            severity = "warn"
        elif drift_ms > 100:
            severity = "info"

    return {
        "host_utc": host_utc,
        "timezone": timezone,
        "ntp_service": ntp_svc,
        "synced": synced,
        "drift_ms": round(drift_ms, 3) if drift_ms is not None else None,
        "severity": severity,
        "ntp_servers": ntp_servers,
        "container_clocks": container_clocks,
        "detail": detail,
    }


@router.get("/api/system/ntp")
async def configure_ntp(
    request: Request,
    servers: str = "",  # comma-separated NTP server list
    timezone: str = "UTC",
    force_sync: bool = True,
):
    """SSE: configure NTP servers (Chrony or timesyncd), set timezone, force sync."""
    _audit(request, "ntp.configure", servers=servers, timezone=timezone, force_sync=force_sync)

    async def stream():
        svc = _detect_ntp_service()
        server_list = [s.strip() for s in servers.split(",") if s.strip()]

        yield _sse(f"NTP service detected: {svc or 'none'}")
        yield _sse(f"Target timezone     : {timezone}")
        if server_list:
            yield _sse(f"NTP servers         : {', '.join(server_list)}")
        yield _sse("─" * 50)

        # ── 1. Install chrony if no NTP service found ─────────────────────
        if svc == "none":
            yield _sse("[1/4] No active NTP service — installing chrony…")
            install = await _async_sudo_exec(
                ["apt-get", "install", "-y", "chrony"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for raw in install.stdout:
                line = raw.decode().rstrip()
                if line:
                    yield _sse(f"  {line}")
            rc = await install.wait()
            if rc == 0:
                svc = "chrony"
                yield _sse("  chrony installed ✔")
            else:
                yield _sse("[WARN] chrony installation failed — falling back to timesyncd.")
                svc = "timesyncd"
        else:
            yield _sse(f"[1/4] Existing NTP service ({svc}) — no installation needed.")

        # ── 2. Write NTP server config ────────────────────────────────────
        if server_list:
            if svc == "chrony":
                yield _sse("[2/4] Configuring NTP servers in chrony…")

                # Build new chrony.conf — keep existing file, replace pool/server lines
                read_r = _sudo_run(
                    ["cat", "/etc/chrony/chrony.conf"],
                    capture_output=True,
                    text=True,
                )
                existing = read_r.stdout if read_r.returncode == 0 else ""

                # Filter out existing pool/server lines
                kept = [
                    ln
                    for ln in existing.splitlines()
                    if not ln.strip().startswith(("pool ", "server ")) and ln.strip() != ""
                ]
                new_servers = [f"server {s} iburst" for s in server_list]
                # Add pool.ntp.org as fallback if not already in list
                if not any("ntp.org" in s for s in server_list):
                    new_servers.append("pool pool.ntp.org iburst")
                new_conf = "\n".join(new_servers + [""] + kept) + "\n"

                import tempfile

                with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as tf:
                    tf.write(new_conf)
                    tf_path = tf.name

                wr = _sudo_run(
                    [
                        "bash",
                        "-c",
                        f"cp '{tf_path}' /etc/chrony/chrony.conf && chmod 644 /etc/chrony/chrony.conf",
                    ],
                    capture_output=True,
                    text=True,
                )
                Path(tf_path).unlink(missing_ok=True)
                if wr.returncode == 0:
                    yield _sse("  /etc/chrony/chrony.conf updated ✔")
                else:
                    yield _sse(f"  [WARN] chrony config write failed: {wr.stderr.strip()}")

            else:  # timesyncd
                yield _sse("[2/4] Configuring NTP servers in systemd-timesyncd…")
                ntp_line = f"NTP={' '.join(server_list)}"
                fallback = "FallbackNTP=pool.ntp.org"
                conf = f"[Time]\n{ntp_line}\n{fallback}\n"

                import tempfile

                with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as tf:
                    tf.write(conf)
                    tf_path = tf.name

                wr = _sudo_run(
                    [
                        "bash",
                        "-c",
                        f"mkdir -p /etc/systemd/timesyncd.conf.d && "
                        f"cp '{tf_path}' /etc/systemd/timesyncd.conf.d/autoflow.conf && "
                        f"chmod 644 /etc/systemd/timesyncd.conf.d/autoflow.conf",
                    ],
                    capture_output=True,
                    text=True,
                )
                Path(tf_path).unlink(missing_ok=True)
                if wr.returncode == 0:
                    yield _sse("  /etc/systemd/timesyncd.conf.d/autoflow.conf updated ✔")
                else:
                    yield _sse(f"  [WARN] timesyncd config write failed: {wr.stderr.strip()}")
        else:
            yield _sse("[2/4] No NTP servers provided — existing configuration kept.")

        # ── 3. Set timezone ───────────────────────────────────────────────
        yield _sse(f"[3/4] Setting timezone → {timezone}…")
        tz_r = _sudo_run(
            ["timedatectl", "set-timezone", timezone],
            capture_output=True,
            text=True,
        )
        if tz_r.returncode == 0:
            yield _sse(f"  Timezone set to {timezone} ✔")
        else:
            yield _sse(f"  [WARN] timedatectl set-timezone failed: {tz_r.stderr.strip()}")
            # Fallback: symlink /etc/localtime
            link_r = _sudo_run(
                ["ln", "-sf", f"/usr/share/zoneinfo/{timezone}", "/etc/localtime"],
                capture_output=True,
                text=True,
            )
            if link_r.returncode == 0:
                yield _sse(f"  /etc/localtime → {timezone} (fallback) ✔")
            else:
                yield _sse("  [WARN] Fallback ln failed — timezone not changed.")

        # ── 4. Restart NTP + force immediate sync ─────────────────────────
        yield _sse("[4/4] Restarting NTP service + forcing immediate sync…")

        svc_name = "chrony" if svc == "chrony" else "systemd-timesyncd"
        # Some distros use chronyd
        restart = _sudo_run(
            ["systemctl", "restart", svc_name],
            capture_output=True,
            text=True,
        )
        if restart.returncode != 0 and svc == "chrony":
            restart = _sudo_run(
                ["systemctl", "restart", "chronyd"],
                capture_output=True,
                text=True,
            )
        if restart.returncode == 0:
            yield _sse(f"  {svc_name} restarted ✔")
        else:
            yield _sse(f"  [WARN] {svc_name} restart failed: {restart.stderr.strip()}")

        if force_sync and svc == "chrony":
            await asyncio.sleep(2)  # let chrony connect to servers
            step_r = _sudo_run(
                ["chronyc", "makestep"],
                capture_output=True,
                text=True,
            )
            if step_r.returncode == 0:
                yield _sse("  chronyc makestep → immediate sync ✔")
            else:
                yield _sse(f"  [WARN] makestep: {step_r.stderr.strip() or step_r.stdout.strip()}")

        # ── Final status ──────────────────────────────────────────────────
        await asyncio.sleep(2)
        import datetime as _dt

        host_utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        yield _sse("─" * 50)
        yield _sse(f"Current UTC time   : {host_utc}")

        tracking = _chrony_tracking() if svc == "chrony" else {}
        if tracking:
            leap = tracking.get("Leap status", "?")
            sys_t = tracking.get("System time", "?")
            yield _sse(f"Leap status       : {leap}")
            yield _sse(f"System time offset: {sys_t}")

        ts = _timesyncd_status()
        ntpsynced = ts.get("NTPSynchronized", ts.get("NTP synchronized", "?"))
        yield _sse(f"NTP synchronized  : {ntpsynced}")
        yield _sse("[SUCCESS] NTP configuration complete ✔")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
