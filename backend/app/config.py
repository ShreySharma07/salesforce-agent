"""
Backend configuration. All settings from env vars (12-factor style).

CRITICAL env vars:
  DATABASE_URL              SQLite path (dev) or Postgres URI (prod)
  VAULT_ENCRYPTION_KEY      Fernet key for secret encryption (32 url-safe base64 bytes)
  PUBLIC_BACKEND_BASE_URL   How the OUTSIDE WORLD reaches this backend.
                            For OAuth callbacks to work, this must be a real URL
                            the OAuth provider can hit (e.g. https://yourapp.com
                            in prod, or http://localhost:8001 for local dev).

Per-provider OAuth credentials:
  SALESFORCE_CLIENT_ID, SALESFORCE_CLIENT_SECRET
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  SLACK_CLIENT_ID, SLACK_CLIENT_SECRET

Leave provider keys empty until you register your app with the provider —
the OAuth endpoint will return a clear error if asked to use an
unconfigured provider.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor all relative-path defaults to backend/ so the app works correctly
# regardless of which directory Python was launched from.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_STORAGE = str(_BACKEND_DIR / ".local_storage")
_DEFAULT_DB_URL = (
    "sqlite+aiosqlite:///"
    + (_BACKEND_DIR / ".local_storage" / "dev.db").as_posix()
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Also anchor .env lookup so `cd /repo && uvicorn app.main:app` still
        # picks up backend/.env instead of /repo/.env (or nothing).
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- LLM provider -----
    llm_provider: Literal["mock", "gemini", "anthropic", "openai"] = "gemini"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # ----- Sandbox runner -----
    sandbox_runner: Literal["local_docker", "modal", "fargate"] = "local_docker"
    sandbox_image: str = "agent-sandbox:latest"
    sandbox_default_max_steps: int = 50
    sandbox_default_max_seconds: int = 600

    # Optional: live-mount sandbox_agent/ for fast iteration without rebuilding
    sandbox_dev_mount: str | None = None

    # ----- Database -----
    # Default is an absolute path anchored to backend/.  If DATABASE_URL is
    # set in .env or the environment, that value is used as-is.
    database_url: str = _DEFAULT_DB_URL
    auto_migrate: bool = True

    # ----- Vault / secrets -----
    # Generate with: openssl rand -base64 32 | sed 's/+/-/g; s/\//_/g; s/=//g'
    # (Fernet wants url-safe base64 of 32 raw bytes.)
    vault_encryption_key: str | None = None

    # ----- OAuth -----
    public_backend_base_url: str = "http://localhost:8001"
    # Salesforce
    salesforce_client_id: str | None = None
    salesforce_client_secret: str | None = None
    salesforce_auth_url: str = "https://login.salesforce.com"  # use test.salesforce.com for sandboxes
    # Google
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # Slack
    slack_client_id: str | None = None
    slack_client_secret: str | None = None

    # ----- Storage / video processing -----
    local_storage_root: str = _DEFAULT_STORAGE
    keyframe_extraction_fps: float = 0.5
    keyframe_max_count: int = 120

    # ----- Backend HTTP server -----
    backend_host: str = "0.0.0.0"
    backend_port: int = 8001

    # ----- Default user (single-user mode) -----
    default_user_id: str = "user_local"

    auth_dev_mode: bool = True

    # ----- Budget defaults -----
    default_max_model_calls_per_run: int = 50
    default_max_usd_per_run: float = 2.00

    def llm_env_for_sandbox(self) -> dict[str, str]:
        # API keys are intentionally NOT forwarded — the sandbox calls the
        # backend's /sandbox/llm proxy, which injects credentials server-side.
        # LLM_PROVIDER / LLM_MODEL are non-secret and used for informational
        # purposes (logging, model selection hint passed to the proxy).
        return {"LLM_PROVIDER": self.llm_provider, "LLM_MODEL": self.llm_model}

    def oauth_redirect_uri(self, provider: str) -> str:
        return f"{self.public_backend_base_url.rstrip('/')}/oauth/{provider}/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()