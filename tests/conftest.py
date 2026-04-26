"""
Root conftest — sets environment variables for all tests before any
service module is imported. Module-level code runs at collection time,
which is guaranteed to precede any test file's import.
"""
import os

# ── Event Engine ──────────────────────────────────────────────────────────────
os.environ.setdefault("AWX_URL",                "http://awx-stub:8052")
os.environ.setdefault("AWX_TOKEN",              "test-awx-token")
os.environ.setdefault("AWX_JOB_TEMPLATE_ID",   "1")
os.environ.setdefault("REDIS_URL",              "")      # fire-and-forget (no Redis)
os.environ.setdefault("DEDUP_TTL",              "60")
os.environ.setdefault("RULES_FILE",             "")
os.environ.setdefault("SCHEDULES_FILE",         "")
os.environ.setdefault("ADMIN_TOKEN",            "test-admin-token")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET",  "")
os.environ.setdefault("RATE_LIMIT",             "10000/minute")
os.environ.setdefault("RATE_LIMIT_WEBHOOKS",    "10000/minute")

# ── API ───────────────────────────────────────────────────────────────────────
os.environ.setdefault("API_SECRET_KEY",         "test-api-secret-32chars-long-xxxxxxxx")
os.environ.setdefault("API_USERNAME",           "admin")
os.environ.setdefault("API_PASSWORD",           "")
os.environ.setdefault("AWX_ADMIN_USER",         "admin")
os.environ.setdefault("AWX_ADMIN_PASSWORD",     "test-awx-password")
os.environ.setdefault("JOB_WATCHER_INTERVAL",   "1")    # 1s for fast notification tests
os.environ.setdefault("AWX_METRICS_INTERVAL",   "0")    # disabled
os.environ.setdefault("CORS_ORIGINS",           "*")
os.environ.setdefault("JWT_EXPIRE_MINUTES",     "60")
os.environ.setdefault("NOTIFICATION_WEBHOOK_URL", "")   # override per-test
os.environ.setdefault("NOTIFICATION_SLACK_WEBHOOK", "")

# ── Shared ────────────────────────────────────────────────────────────────────
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "")  # disable tracing
