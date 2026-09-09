"""Runtime configuration. Every secret comes from the environment - nothing is hardcoded."""
import os
from functools import lru_cache

# Vercel's Neon/Postgres integrations inject different variable names depending on
# which marketplace product you attach, so we probe them in order of preference.
_DB_ENV_CANDIDATES = (
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "NEON_DATABASE_URL",
)

_LOCAL_SQLITE_FALLBACK = "sqlite:///./patients.db"


def _normalize_db_url(raw: str) -> str:
    """SQLAlchemy needs an explicit driver; hosted providers hand out bare `postgres://`."""
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)
    elif raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


class Settings:
    def __init__(self) -> None:
        self.app_env = os.getenv("APP_ENV", "development")
        self.database_url = self._resolve_database_url()
        self.is_sqlite = self.database_url.startswith("sqlite")

        # Shared secret Vapi sends back on every webhook call (X-Vapi-Secret header).
        # Empty string == verification disabled, which is only acceptable locally.
        self.vapi_server_secret = os.getenv("VAPI_SERVER_SECRET", "")

        # Optional bearer token guarding the mutating REST endpoints + dashboard.
        self.admin_api_key = os.getenv("ADMIN_API_KEY", "")

        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    @staticmethod
    def _resolve_database_url() -> str:
        for key in _DB_ENV_CANDIDATES:
            value = os.getenv(key)
            if value:
                return _normalize_db_url(value.strip())
        return _LOCAL_SQLITE_FALLBACK


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
