"""
Backend configuration. All settings come from env vars (12-factor style).

Critical knobs:
  LLM_PROVIDER       mock | gemini | anthropic | openai
  LLM_MODEL          model id (provider-specific)
  GEMINI_API_KEY     etc — backend reads these once, passes to sandboxes
  SANDBOX_RUNNER     local_docker | modal | fargate
  REPO_BACKEND       json | postgres

User never has to type API keys for individual runs - backend handles it.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- LLM provider -----
    llm_provider: Literal["mock", "gemini", "anthropic", "openai"] = "mock"
    llm_model: str = "mock-model"
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # ----- Sandbox runner -----
    sandbox_runner: Literal["local_docker", "modal", "fargate"] = "local_docker"
    sandbox_image: str = "agent-sandbox:latest"
    sandbox_default_max_steps: int = 50
    sandbox_default_max_seconds: int = 600

    # ----- Repository -----
    repo_backend: Literal["json", "postgres"] = "json"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/agent"

    # ----- Storage -----
    local_storage_root: str = "./.local_storage"
    s3_bucket_videos: str = "local-videos"
    s3_bucket_traces: str = "local-traces"

    # ----- Budget defaults (per run) -----
    default_max_model_calls_per_run: int = 50
    default_max_usd_per_run: float = 2.00
    default_max_wall_clock_seconds: int = 600

    # ----- Video processing -----
    keyframe_extraction_fps: float = 0.5
    keyframe_max_count: int = 120

    # ----- Backend HTTP server -----
    backend_host: str = "0.0.0.0"
    backend_port: int = 8001  # 8000 reserved for sandbox

    def llm_env_for_sandbox(self) -> dict[str, str]:
        """Build the env vars to pass into a sandbox container so it can
        make LLM calls. Resolves provider + key from backend's own env.
        Sandbox doesn't need to know which provider - it picks based on
        the LLM_PROVIDER value we forward."""
        env: dict[str, str] = {
            "LLM_PROVIDER": self.llm_provider,
            "LLM_MODEL": self.llm_model,
        }
        # Forward whichever API key is relevant. Provider-specific keys
        # are kept under their canonical names so sandbox code is agnostic.
        if self.gemini_api_key:
            env["GEMINI_API_KEY"] = self.gemini_api_key
        if self.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if self.openai_api_key:
            env["OPENAI_API_KEY"] = self.openai_api_key
        return env


@lru_cache
def get_settings() -> Settings:
    return Settings()