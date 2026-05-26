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

    # ── Environment ──────────────────────────────────────────────────────────
    # Set ENV=production to enable production-only safety guards
    # (e.g. raises an error if CORS_ORIGINS is still "*").
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
    # Set to false to disable in local development (e.g. when accessing via HTTP).
    security_headers_enabled: bool = True

    # ── Rate limiting (slowapi) ───────────────────────────────────────────────
    rate_limit: str = "100/minute"

    # ── AWX job metrics ───────────────────────────────────────────────────────
    # Background polling interval in seconds (0 = disabled)
    awx_metrics_interval: int = 60

    # ── User store (RBAC) ────────────────────────────────────────────────────
    # Path to the JSON file containing user accounts and hashed passwords.
    # On first boot, a default admin user is created (password = API_SECRET_KEY).
    # Managed via POST/DELETE /api/v1/users  (admin only).
    users_file: str = "/etc/autoflow/users.json"

    # ── JWT Revocation (optional Redis blacklist) ─────────────────────────────
    # When set, issued tokens carry a `jti` claim and POST /api/v1/auth/logout
    # blacklists the jti in Redis with TTL = remaining token lifetime.
    # Leave empty to disable server-side revocation (logout still works
    # client-side — the token is simply discarded by the caller).
    redis_url: str = ""

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
