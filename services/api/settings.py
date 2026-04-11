from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── AWX connection ────────────────────────────────────────────────────────
    awx_url: str = "http://awxweb"  # hostname — underscores break Django host validation
    awx_admin_user: str = "admin"
    awx_admin_password: str

    # ── Authentication ────────────────────────────────────────────────────────
    api_secret_key: str          # used as X-API-Key value (legacy)

    # Credentials for POST /auth/token
    # api_password defaults to api_secret_key if left empty
    api_username: str = "admin"
    api_password: str = ""

    # ── JWT ───────────────────────────────────────────────────────────────────
    # jwt_secret_key defaults to api_secret_key if left empty
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "info"

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated origins. Use "*" for dev, restrict in production.
    cors_origins: str = "*"

    # ── Rate limiting (slowapi) ───────────────────────────────────────────────
    rate_limit: str = "100/minute"

    # ── AWX job metrics ───────────────────────────────────────────────────────
    # Background polling interval in seconds (0 = disabled)
    awx_metrics_interval: int = 60

    # ── Notifications ─────────────────────────────────────────────────────────
    # Generic POST webhook called when a watched job reaches terminal status
    notification_webhook_url: str = ""
    # Slack incoming webhook URL (Block Kit message)
    notification_slack_webhook: str = ""
    # How often to poll AWX for watched-job completion (seconds)
    job_watcher_interval: int = 15

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def effective_jwt_secret(self) -> str:
        """JWT signing secret — falls back to api_secret_key if not set."""
        return self.jwt_secret_key or self.api_secret_key

    @property
    def effective_api_password(self) -> str:
        """Login password for /auth/token — falls back to api_secret_key if not set."""
        return self.api_password or self.api_secret_key


settings = Settings()
