"""
Factory that returns the configured LLM client. The ONE place that knows
which provider is active. Everything else just calls get_llm_client().
"""
from __future__ import annotations

from app.config import get_settings
from app.core.llm.client import LLMClient
from app.core.llm.mock import MockLLMClient


def get_llm_client(purpose: str | None = None) -> LLMClient:
    """Return the configured client. `purpose` is reserved for future
    routing (e.g., use Flash for cheap tasks, Pro for plan synthesis)."""
    settings = get_settings()

    if settings.llm_provider == "mock":
        return MockLLMClient(model=settings.llm_model)

    if settings.llm_provider == "gemini":
        from app.core.llm.gemini import GeminiLLMClient
        return GeminiLLMClient(
            model=settings.llm_model,
            api_key=settings.gemini_api_key,
        )

    if settings.llm_provider == "anthropic":
        # Wired in when we add the Anthropic implementation.
        raise NotImplementedError("Anthropic client not yet implemented")

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")