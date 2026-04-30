"""
Backend configuration. All settings come from env vars (12-factor style).
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

    # ----- LLM provider switch -----
    # When billing arrives, set LLM_PROVIDER=gemini and add GEMINI_API_KEY.
    llm_provider: Literal["mock", "gemini", "anthropic"] = Field(default="mock")
    llm_model: str = Field(default="mock-model")
    gemini_api_key: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None)

    # ----- Storage -----
    s3_bucket_videos: str = Field(default="local-videos")
    s3_bucket_traces: str = Field(default="local-traces")
    local_storage_root: str = Field(
        default="./.local_storage",
        description="When AWS credentials aren't set, files go here.",
    )

    # ----- Database -----
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/agent",
    )

    # ----- Budget defaults (per run) -----
    default_max_model_calls_per_run: int = 50
    default_max_usd_per_run: float = 2.00
    default_max_wall_clock_seconds: int = 600

    # ----- Video processing -----
    keyframe_extraction_fps: float = Field(
        default=0.5,
        description="Frames per second to extract from videos. 0.5 = 1 frame every 2s.",
    )
    keyframe_max_count: int = Field(
        default=120,
        description="Hard cap on frames per video to control LLM costs.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()