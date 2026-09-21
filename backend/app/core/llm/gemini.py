"""
Gemini implementation of LLMClient.

NOT YET WIRED — waiting on billing setup. The structure mirrors the
spike's working Gemini code so the swap is trivial when ready.

Usage will be:
  from app.core.llm.gemini import GeminiLLMClient
  client = GeminiLLMClient(model="gemini-3-flash-preview")
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from app.core.llm.client import LLMClient, LLMResponse, UsageEvent


PRICING_PER_1M_TOKENS = {
    "gemini-2.5-flash": {"input": 0.0, "output": 0.0},      # free tier
    "gemini-2.5-flash-lite": {"input": 0.0, "output": 0.0},
    "gemini-2.5-computer-use-preview-10-2025": {"input": 1.25, "output": 10.00},
    "gemini-3-flash-preview": {"input": 0.30, "output": 2.50},
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
}


class GeminiLLMClient(LLMClient):
    """Google Gemini provider. Activate by setting LLM_PROVIDER=gemini in .env."""

    provider_name = "google"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        from google import genai  # local import so missing key doesn't break import time

        self.model = model
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        self._client = genai.Client()

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
        """Single-turn generate (text + optional PNG images) with usage/cost accounting."""
        from google.genai import types
        from google.genai.types import Content, Part

        parts: list[Any] = [Part(text=prompt)]
        if images:
            for img in images:
                parts.append(Part.from_bytes(data=img, mime_type="image/png"))
        contents = [Content(role="user", parts=parts)]

        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system:
            config_kwargs["system_instruction"] = system
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**config_kwargs)

        start = time.monotonic()
        raw = self._client.models.generate_content(
            model=self.model, contents=contents, config=config,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        usage_meta = getattr(raw, "usage_metadata", None)
        in_tok = getattr(usage_meta, "prompt_token_count", 0) or 0
        out_tok = (
            (getattr(usage_meta, "candidates_token_count", 0) or 0)
            + (getattr(usage_meta, "thoughts_token_count", 0) or 0)
        )
        pricing = PRICING_PER_1M_TOKENS.get(self.model, {"input": 0.0, "output": 0.0})
        cost = in_tok * pricing["input"] / 1_000_000 + out_tok * pricing["output"] / 1_000_000

        text = ""
        cand = raw.candidates[0] if raw.candidates else None
        if cand and cand.content and cand.content.parts:
            text = "".join(p.text for p in cand.content.parts if getattr(p, "text", None))

        # An empty response is never useful to a caller here: captions become
        # blank and plan JSON becomes unparseable. Fail with a reason instead.
        if not text.strip():
            raise RuntimeError(_explain_empty_gemini_response(raw, self.model, max_tokens))

        usage = UsageEvent(
            model=self.model, provider=self.provider_name,
            input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=cost, latency_ms=latency_ms, purpose=purpose,
        )
        return LLMResponse(text=text, raw=raw, usage=usage)

    def generate_structured(
        self,
        prompt: str,
        *,
        purpose: str,
        schema: type,
        system: str | None = None,
        images: list[bytes] | None = None,
    ) -> tuple[Any, UsageEvent]:
        """JSON-mode generate parsed into `schema`; returns (None, usage) on bad JSON."""
        # Use json_mode and parse — full schema-aware mode comes later.
        response = self.generate(
            prompt, purpose=purpose, system=system, images=images, json_mode=True,
        )
        try:
            data = json.loads(response.text)
            instance = schema(**data) if hasattr(schema, "__init__") else data
        except (json.JSONDecodeError, TypeError, ValueError):
            instance = None
        return instance, response.usage


def _explain_empty_gemini_response(raw, model: str, max_tokens: int) -> str:
    """Why did Gemini return no text? Turn silence into a usable error.

    Gemini 2.5+/3.x models THINK, and thinking tokens are charged against
    `max_output_tokens`. When the budget is small the model can spend all of
    it reasoning and emit no visible text at all: the call succeeds, usage is
    non-zero, and the response text is "".

    Left unchecked that empty string flows downstream as an empty caption or
    as unparseable plan JSON, and the user sees "Gemini is not working" with
    nothing to act on. This produces the sentence that actually helps.
    """
    usage = getattr(raw, "usage_metadata", None)
    thoughts = getattr(usage, "thoughts_token_count", 0) or 0
    cand = raw.candidates[0] if getattr(raw, "candidates", None) else None
    finish = getattr(cand, "finish_reason", None)
    finish_name = getattr(finish, "name", str(finish))

    if finish_name == "MAX_TOKENS":
        return (
            f"{model} returned NO text: it hit max_output_tokens={max_tokens} "
            f"after spending {thoughts} tokens on internal thinking. Thinking "
            f"counts against the output budget on this model. Raise max_tokens, "
            f"or use a non-thinking model."
        )
    if finish_name in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION"):
        return f"{model} returned no text: blocked with finish_reason={finish_name}."
    return (
        f"{model} returned no text (finish_reason={finish_name}, "
        f"thinking tokens={thoughts})."
    )
