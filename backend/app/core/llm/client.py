"""
LLM provider abstraction. All LLM calls in the backend go through this
interface. Concrete implementations (Gemini, Anthropic, Mock) are isolated.

Swap providers via config in app/config.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class UsageEvent:
    """One LLM call's accounting record. Persisted for cost tracking."""
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    purpose: str


@dataclass
class LLMResponse:
    """Provider-agnostic response wrapper."""
    text: str
    raw: Any                # original SDK response, for power users
    usage: UsageEvent


class LLMClient(ABC):
    """Base interface every provider implementation must satisfy."""

    provider_name: str

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        purpose: str,
        system: str | None = None,
        images: list[bytes] | None = None,
        json_mode: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Synchronous generate. Returns text + usage."""
        ...

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        *,
        purpose: str,
        schema: type,
        system: str | None = None,
        images: list[bytes] | None = None,
    ) -> tuple[Any, UsageEvent]:
        """Generate output matching a Pydantic schema. Returns (instance, usage)."""
        ...