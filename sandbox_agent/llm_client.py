"""
Minimal Gemini client for the sandbox. Same shape the executor + modes
expect: a `GeminiClient` with a `.generate(prompt, system, images, json_mode, max_tokens)`
method that returns an object with a `.text` attribute.

Reads from env:
  GEMINI_API_KEY   required
  LLM_MODEL        default 'gemini-2.5-flash'
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class GeminiResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class GeminiClient:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        from google import genai

        api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set inside the container")
        os.environ["GEMINI_API_KEY"] = api_key

        self.model = model or os.getenv("LLM_MODEL", "gemini-2.5-flash")
        self._client = genai.Client()

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[bytes] | None = None,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        _retry: int = 2,
    ) -> GeminiResponse:
        from google.genai import types
        from google.genai.types import Content, Part

        parts: list[Any] = [Part(text=prompt)]
        if images:
            for img in images:
                parts.append(Part.from_bytes(data=img, mime_type="image/png"))
        contents = [Content(role="user", parts=parts)]

        cfg: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system:
            cfg["system_instruction"] = system
        if json_mode:
            cfg["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**cfg)

        start = time.monotonic()
        for attempt in range(_retry + 1):
            try:
                raw = self._client.models.generate_content(
                    model=self.model, contents=contents, config=config,
                )
                break
            except Exception as exc:
                err_str = str(exc)
                # Retry on 429 rate-limit using the server's suggested delay
                if attempt < _retry and "429" in err_str:
                    delay_match = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", err_str, re.I)
                    wait = min(float(delay_match.group(1)) if delay_match else 30.0, 60.0)
                    time.sleep(wait)
                    continue
                raise
        latency_ms = int((time.monotonic() - start) * 1000)

        usage = getattr(raw, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) or 0
        out_tok = (
            (getattr(usage, "candidates_token_count", 0) or 0)
            + (getattr(usage, "thoughts_token_count", 0) or 0)
        )
        cand = raw.candidates[0] if raw.candidates else None
        text = ""
        if cand and cand.content and cand.content.parts:
            text = "".join(p.text for p in cand.content.parts if getattr(p, "text", None))

        return GeminiResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=latency_ms,
        )