from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── AWX connection ────────────────────────────────────────────────────────
    awx_url: str = "http://awxweb"
    awx_token: str                    # AWX API token (User → Tokens in AWX UI)
    awx_job_template_id: int          # Default fallback template when no rule matches

    # ── Rule engine ───────────────────────────────────────────────────────────
    rules_file: str = "/etc/event-engine/rules.yml"

    # ── Scheduler ─────────────────────────────────────────────────────────────
    schedules_file: str = "/etc/event-engine/schedules.yml"

    # ── Deduplication ─────────────────────────────────────────────────────────
    dedup_ttl: int = 60               # Seconds — 0 disables deduplication

    # ── Admin endpoints ───────────────────────────────────────────────────────
    admin_token: str = ""             # Bearer token pour /admin/* — vide = endpoints bloqués

    # ── Webhooks ──────────────────────────────────────────────────────────────
    github_webhook_secret: str = ""   # Leave empty to skip HMAC validation

    # ── Rate limiting (slowapi) ───────────────────────────────────────────────
    rate_limit: str = "200/minute"
    rate_limit_webhooks: str = "60/minute"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "info"

    # ── Redis (event persistence + retry queue + dedup) ───────────────────────
    # redis://[:password@]host[:port]/db  — empty = disable persistence (fire-and-forget)
    redis_url: str = ""


settings = Settings()
