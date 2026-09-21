"""
Anthropic implementation of LLMClient.

The sandbox LLM proxy could already talk to Claude, but the BACKEND's own
factory could not — so the video → plan pipeline raised NotImplementedError
whenever LLM_PROVIDER=anthropic. This closes that gap: captioning and plan
synthesis now work on either provider.

Activate with:
  LLM_PROVIDER=anthropic
  LLM_MODEL=claude-opus-5
  ANTHROPIC_API_KEY=sk-ant-...
"""
from __future__ import annotations

import json
import time
from typing import Any

from app.core.budget import estimate_cost
from app.core.llm.client import LLMClient, LLMResponse, UsageEvent


def build_anthropic_client(api_key: str | None = None):
    """Construct an Anthropic client that can actually read its responses.

    WORKAROUND for an upstream bug. `httpx2` (which the Anthropic SDK builds
    on) advertises Brotli whenever the `brotli` package is installed, then
    decodes the response with:

        self._decompress(data, output_buffer_limit=...)

    `brotli.Decompressor.process()` takes no keyword arguments, so EVERY
    Brotli-encoded response raises `TypeError` inside the decoder, which the
    SDK surfaces as a misleading `APIConnectionError: Connection error.`
    Anthropic returns Brotli by default, so without this the provider looks
    unreachable while the network is perfectly fine.

    Dropping `br` from Accept-Encoding sidesteps the broken code path. gzip
    still applies, so this costs nothing measurable. Remove this once httpx2
    fixes the decoder or the SDK stops offering Brotli.
    """
    # Local import so a missing package never breaks module import for users
    # on the Gemini path.
    import anthropic

    http_client = anthropic.DefaultHttpxClient(
        headers={"accept-encoding": "gzip, deflate"},
    )
    if api_key:
        return anthropic.Anthropic(api_key=api_key, http_client=http_client)
    return anthropic.Anthropic(http_client=http_client)


class AnthropicLLMClient(LLMClient):
    """Claude provider. Vision + JSON output, same interface as Gemini."""

    provider_name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self._client = build_anthropic_client(api_key)

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
        """One request. Images go before the text so the model sees the
        screenshot before the instruction that refers to it."""
        import base64

        content: list[dict[str, Any]] = []
        for img in images or []:
            # Detect JPEG (FF D8) vs PNG (89 50) from the magic bytes.
            media_type = "image/jpeg" if img[:2] == b"\xff\xd8" else "image/png"
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(img).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system
        if temperature:
            # Current Claude models reject sampling params alongside thinking;
            # the default (omitted) is what this pipeline wants anyway.
            kwargs["temperature"] = temperature

        start = time.monotonic()
        raw = self._client.messages.create(**kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        text = "".join(b.text for b in raw.content if getattr(b, "text", None))
        if json_mode:
            text = _strip_json_fence(text)

        usage = UsageEvent(
            model=self.model,
            provider=self.provider_name,
            input_tokens=raw.usage.input_tokens,
            output_tokens=raw.usage.output_tokens,
            cost_usd=estimate_cost(self.model, raw.usage.input_tokens, raw.usage.output_tokens),
            latency_ms=latency_ms,
            purpose=purpose,
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
        """JSON-mode generate parsed into `schema`; (None, usage) on bad JSON."""
        response = self.generate(
            prompt, purpose=purpose, system=system, images=images,
            json_mode=True, max_tokens=8192,
        )
        try:
            data = json.loads(response.text)
            instance = schema(**data) if isinstance(data, dict) else data
        except (json.JSONDecodeError, TypeError, ValueError):
            instance = None
        return instance, response.usage


def _strip_json_fence(text: str) -> str:
    """Remove a ```json fence if the model wrapped its JSON in one.

    Claude follows a JSON-only instruction reliably but occasionally fences
    the output; the callers parse raw JSON, so normalize it here.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
