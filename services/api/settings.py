from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    awx_url: str = "http://awxweb"  # Use hostname (not service name) — underscores break Django host validation
    awx_admin_user: str = "admin"
    awx_admin_password: str

    api_secret_key: str
    log_level: str = "info"


settings = Settings()
