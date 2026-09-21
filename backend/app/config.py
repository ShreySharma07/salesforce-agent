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
    # Two DIFFERENT jobs, so they can use two different models.
    #
    #   llm_provider / llm_model
    #       The backend pipeline: keyframe captioning and plan synthesis.
    #       One long, vision-heavy call per batch of frames, run once per
    #       recording. Gemini is cheap and fast at this.
    #
    #   sandbox_llm_provider / sandbox_llm_model
    #       The ReAct loop inside the sandbox: many small screenshot +
    #       reason + act calls, every one of which must return valid JSON and
    #       follow a long list of rules. This is where model quality shows up
    #       most, and it is the run's cost centre.
    #
    # Leave the sandbox_* values unset to use the same model for both.
    llm_provider: Literal["mock", "gemini", "anthropic", "openai"] = "gemini"
    llm_model: str = "gemini-3.1-pro-preview"
    sandbox_llm_provider: Literal["mock", "gemini", "anthropic", "openai"] | None = None
    sandbox_llm_model: str | None = None

    # The pipeline itself has two very different stages, and on a small budget
    # the difference matters:
    #
    #   caption_llm_model  Describing what is on screen, one call per batch of
    #                      frames. Token-heavy (every frame is an image) but
    #                      undemanding, so a cheap model does it well.
    #   plan_llm_model     Turning those descriptions into a structured plan.
    #                      ONE call, but it must follow a long rule list and
    #                      emit valid JSON — worth a strong model.
    #
    # Both default to llm_model when unset.
    caption_llm_model: str | None = None
    plan_llm_model: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # ----- Sandbox runner -----
    sandbox_runner: Literal["local_docker", "modal", "fargate"] = "local_docker"
    sandbox_image: str = "agent-sandbox:latest"
    sandbox_default_max_steps: int = 120
    sandbox_default_max_seconds: int = 1800

    # Optional: live-mount sandbox_agent/ for fast iteration without rebuilding
    sandbox_dev_mount: str | None = None
    # Host interface the sandbox's agent + noVNC ports are published on.
    # Defaults to loopback: the sandbox control API and the live view are
    # UNAUTHENTICATED for viewing, so they must not be reachable from the
    # network. Only change this if you have put an authenticating proxy in
    # front of them.
    sandbox_bind_host: str = "127.0.0.1"
    # Optional password for the live-view VNC server inside the sandbox.
    # REQUIRED before exposing the live view beyond loopback: an unauthenticated
    # VNC session is full keyboard and mouse control of a logged-in browser.
    sandbox_vnc_password: str | None = None

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
    keyframe_extraction_fps: float = 1
    keyframe_max_count: int = 120

    # ----- Backend HTTP server -----
    backend_host: str = "0.0.0.0"
    backend_port: int = 8001
    # Comma-separated browser origins allowed to call the API with credentials.
    # Never "*" — allow_credentials=True requires explicit origins.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ----- Run execution limits -----
    # Concurrent sandboxes this backend will run at once. local_docker maps a
    # host port per container, so unbounded spawning exhausts ports and RAM.
    max_concurrent_runs: int = 3

    # ----- Default user (single-user mode) -----
    default_user_id: str = "user_local"

    auth_dev_mode: bool = True

    # ----- Budget defaults -----
    # Enforced per run by the LLM proxy (app/core/budget). A run that exceeds
    # either ceiling is refused further model calls and aborts.
    default_max_model_calls_per_run: int = 80000
    default_max_usd_per_run: float = 900.00

    def cors_origin_list(self) -> list[str]:
        """Parse `cors_origins` into the list CORSMiddleware expects."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Which pipeline stage each `purpose` string belongs to.
    _CAPTION_PURPOSES = ("keyframe_understanding",)
    _PLAN_PURPOSES = ("plan_synthesis", "plan_correction")

    def model_for_purpose(self, purpose: str | None) -> str:
        """The model to use for one pipeline stage.

        Captioning and plan synthesis have opposite cost profiles, so they can
        be pointed at different models. An unknown or missing purpose falls
        back to `llm_model`, which is also what happens when neither override
        is configured.
        """
        if purpose in self._CAPTION_PURPOSES and self.caption_llm_model:
            return self.caption_llm_model
        if purpose in self._PLAN_PURPOSES and self.plan_llm_model:
            return self.plan_llm_model
        return self.llm_model

    def effective_sandbox_provider(self) -> str:
        """Which provider serves the sandbox's ReAct loop.

        Falls back to the pipeline provider when `sandbox_llm_provider` is
        unset, so a single-model setup keeps working unchanged.
        """
        return self.sandbox_llm_provider or self.llm_provider

    def effective_sandbox_model(self) -> str:
        """Which model serves the sandbox's ReAct loop.

        If a sandbox PROVIDER is set without a model, falling back to
        `llm_model` would send e.g. a Gemini model id to Anthropic, so the
        fallback only applies when the providers actually match.
        """
        if self.sandbox_llm_model:
            return self.sandbox_llm_model
        if self.sandbox_llm_provider and self.sandbox_llm_provider != self.llm_provider:
            raise ValueError(
                f"SANDBOX_LLM_PROVIDER={self.sandbox_llm_provider!r} differs from "
                f"LLM_PROVIDER={self.llm_provider!r}, so SANDBOX_LLM_MODEL must be "
                f"set too — {self.llm_model!r} belongs to a different provider."
            )
        return self.llm_model

    def llm_env_for_sandbox(self) -> dict[str, str]:
        # API keys are intentionally NOT forwarded — the sandbox calls the
        # backend's /sandbox/llm proxy, which injects credentials server-side.
        # These two are non-secret and informational only (container logs);
        # the backend, not the container, decides which model actually serves
        # a request.
        return {
            "LLM_PROVIDER": self.effective_sandbox_provider(),
            "LLM_MODEL": self.effective_sandbox_model(),
        }

    def oauth_redirect_uri(self, provider: str) -> str:
        return f"{self.public_backend_base_url.rstrip('/')}/oauth/{provider}/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()