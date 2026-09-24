"""Runtime configuration, read once from the environment."""

import os
from dataclasses import dataclass, field


def _normalize_db_url(url: str) -> str:
    # Render (and most hosts) hand out postgres:// or postgresql:// URLs; SQLAlchemy
    # needs the driver spelled out to use psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _csv(value: str) -> list[str]:
    return [v.strip().lower() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    allowed_domain: str
    admin_emails: list[str] = field(default_factory=list)
    google_client_id: str = ""
    google_client_secret: str = ""
    # Demo-only login bypass; enabled only when a passcode is configured. See README.
    dev_login_passcode: str = ""
    session_https_only: bool = True

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def dev_login_enabled(self) -> bool:
        return bool(self.dev_login_passcode)


def load_settings() -> Settings:
    return Settings(
        database_url=_normalize_db_url(
            os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/csm")
        ),
        secret_key=os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me"),
        allowed_domain=os.environ.get("ALLOWED_DOMAIN", "shearwaterdata.com").lower(),
        admin_emails=_csv(os.environ.get("ADMIN_EMAILS", "")),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        dev_login_passcode=os.environ.get("DEV_LOGIN_PASSCODE", ""),
        session_https_only=os.environ.get("SESSION_HTTPS_ONLY", "true").lower() != "false",
    )


settings = load_settings()
