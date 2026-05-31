#!/bin/sh
set -e

# ── Kernel memory overcommit check ───────────────────────────────────────────
# Redis uses fork() for background saves (BGSAVE) and AOF rewrites (BGREWRITEAOF).
# When vm.overcommit_memory = 0, the kernel may refuse the fork() even when there
# is enough physical RAM, causing background saves to silently fail.  Without
# background saves the incremental AOF grows unbounded and will be corrupted if
# the process is killed mid-write.
#
# Fix on the host (one-time, survives reboots):
#   echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/10-redis.conf
#   sudo sysctl -p /etc/sysctl.d/10-redis.conf
# ─────────────────────────────────────────────────────────────────────────────
OVERCOMMIT=$(cat /proc/sys/vm/overcommit_memory 2>/dev/null || echo 0)
if [ "$OVERCOMMIT" != "1" ]; then
    echo "╔══════════════════════════════════════════════════════════════╗" >&2
    echo "║  WARNING: vm.overcommit_memory = $OVERCOMMIT  (required: 1)            ║" >&2
    echo "║                                                              ║" >&2
    echo "║  Redis background saves and AOF rewrites may silently fail.  ║" >&2
    echo "║  This WILL cause AOF corruption on hard shutdown/OOM kill.   ║" >&2
    echo "║                                                              ║" >&2
    echo "║  Fix (run once on the Docker host):                          ║" >&2
    echo "║    echo 'vm.overcommit_memory = 1' | \\                      ║" >&2
    echo "║      sudo tee /etc/sysctl.d/10-redis.conf                   ║" >&2
    echo "║    sudo sysctl -p /etc/sysctl.d/10-redis.conf               ║" >&2
    echo "╚══════════════════════════════════════════════════════════════╝" >&2
fi

# ── Write password to a runtime config ───────────────────────────────────────
# Avoids exposing REDIS_PASSWORD in docker inspect / ps output.
RUNTIME_CONF="/tmp/redis-runtime.conf"
if [ -n "$REDIS_PASSWORD" ]; then
    echo "requirepass $REDIS_PASSWORD" > "$RUNTIME_CONF"
else
    echo "" > "$RUNTIME_CONF"
fi
cat /etc/redis/redis.conf >> "$RUNTIME_CONF"

exec redis-server "$RUNTIME_CONF"
