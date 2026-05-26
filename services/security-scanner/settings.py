from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Scan intervals ────────────────────────────────────────────────────────
    # Seconds between full Trivy CVE scan cycles (default: 6h)
    scan_interval: int = 21600
    # Seconds between image version check cycles (default: 1h)
    version_check_interval: int = 3600

    # ── Trivy ─────────────────────────────────────────────────────────────────
    trivy_cache_dir: str = "/home/autoflow/.cache/trivy"
    # Max seconds to wait for a single image scan before aborting
    # AWX image is ~2GB and needs more time; 300s (5min) is safe
    trivy_timeout: int = 300

    # ── External API credentials ──────────────────────────────────────────────
    # Optional GitHub PAT — increases rate limit from 60 to 5000 req/hr
    github_token: str = ""
    # Optional DockerHub credentials for authenticated tag lookups
    dockerhub_user: str = ""
    dockerhub_password: str = ""

    # ── AWX image tag (used to build the full image reference for scanning) ───
    awx_version: str = "24.6.1"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "info"

    # ── Rate limiting (slowapi) ───────────────────────────────────────────────
    rate_limit: str = "60/minute"

    # ── Environment ──────────────────────────────────────────────────────────
    # Set ENV=production to enable production-only safety guards.
    env: str = "development"

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins.
    # Empty string → no origin allowed (safest default).
    # "*" → all origins (development only — blocked in production).
    cors_origins: str = ""
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = ["GET", "POST", "PUT", "DELETE"]
    cors_allow_headers: list[str] = ["Authorization", "Content-Type"]

    # ── Security headers ─────────────────────────────────────────────────────
    security_headers_enabled: bool = True

    # ── Compliance ────────────────────────────────────────────────────────────
    # Bearer token required for all /compliance/* endpoints (leave empty to
    # disable auth — not recommended for production)
    compliance_admin_token: str = ""


settings = Settings()
