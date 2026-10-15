"""Environment-based configuration.

All values can be overridden with environment variables or a `.env` file
(see `.env.example`). Nothing environment-specific is hard-coded.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CTDMS_", extra="ignore")

    app_name: str = "CTDMS"
    app_title: str = "Central Transport & Dispatch"
    environment: str = "development"  # development | production | test
    debug: bool = False

    # Security
    secret_key: str = "change-me-in-production-please-use-a-long-random-string"
    session_cookie_name: str = "ctdms_session"
    session_idle_minutes: int = 480
    session_cookie_secure: bool = False  # set True behind HTTPS
    jwt_expire_minutes: int = 720
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Database. SQLite is fine for demo/single-site; use PostgreSQL in production.
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'ctdms.db').as_posix()}"
    db_echo: bool = False

    # Paths
    data_dir: Path = BASE_DIR / "data"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    backup_dir: Path = BASE_DIR / "data" / "backups"
    log_dir: Path = BASE_DIR / "data" / "logs"

    # Locale
    timezone: str = "Asia/Kolkata"
    currency_symbol: str = "₹"

    # Show the demo-credential helper on the login page (turn OFF in production)
    show_demo_logins: bool | None = None  # default: on in development, OFF in production
    api_docs_public: bool | None = None  # /api/docs + /api/openapi.json; default: on except in production

    # Upload limits
    max_upload_mb: int = 20

    # Reading photographed / scanned transporter bills with Claude (optional; without it lines are typed by hand)
    anthropic_api_key: str | None = None

    # Customer dispatch alerts: bearer token of the WhatsApp Cloud API / SMS gateway (kept out of the settings page)
    notify_token: str | None = None

    @model_validator(mode="after")
    def _environment_defaults(self):
        if self.show_demo_logins is None:
            self.show_demo_logins = not self.is_production
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for p in (s.data_dir, s.upload_dir, s.backup_dir, s.log_dir):
        Path(p).mkdir(parents=True, exist_ok=True)
    return s
