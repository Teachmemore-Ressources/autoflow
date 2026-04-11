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
    trivy_timeout: int = 120

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

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: str = "*"


settings = Settings()
