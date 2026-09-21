"""
Sandbox LLM client.

IMPORTANT: despite the class name, this does NOT talk to Gemini. It is a thin
HTTP client for the backend's /sandbox/llm/generate proxy. Which provider and
model actually serve a request is decided ENTIRELY by the backend, from
SANDBOX_LLM_PROVIDER / SANDBOX_LLM_MODEL (falling back to LLM_PROVIDER /
LLM_MODEL). The sandbox can therefore run on Claude while the video-to-plan
pipeline runs on Gemini, with no change in here.

The class keeps the name `GeminiClient` only because browser_mode,
computer_mode and executor import it under that name; `LLMProxyClient` is an
alias for new code.

The sandbox does NOT hold an LLM API key. The backend makes the real provider
call with its own key, and the sandbox authenticates with its per-run
RUN_TOKEN.

Public surface is unchanged from the old direct-Gemini client:
  GeminiClient().generate(prompt, system=, images=, json_mode=, max_tokens=)
  -> GeminiResponse(text, input_tokens, output_tokens, latency_ms)

so browser_mode / computer_mode need no changes.

Environment variables (injected by the backend at sandbox spawn):
  BACKEND_MCP_URL   base URL of the backend (e.g. http://host.docker.internal:8001)
  RUN_ID            the run this sandbox is executing
  RUN_TOKEN         per-run bearer token
  LLM_MODEL         informational only; the backend decides the real model
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class GeminiResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class LLMClientError(Exception):
    pass


class QuotaExhaustedError(LLMClientError):
    """The run can make no further model calls — daily provider quota is gone,
    or the run spent its own call/dollar budget. Either way the executor aborts
    the whole run: retrying or continuing to later steps cannot succeed."""
    pass


class GeminiClient:
    """Calls the backend LLM proxy, NOT Gemini.

    The name is historical and kept because the executor and both execution
    modes import it. Use the `LLMProxyClient` alias in new code.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None):
        # api_key is accepted but ignored — the sandbox never holds a key.
        self.base_url = os.getenv("BACKEND_MCP_URL", "").rstrip("/")
        self.run_id = os.getenv("RUN_ID")
        self.run_token = os.getenv("RUN_TOKEN")
        self.model = model or os.getenv("LLM_MODEL", "gemini-3-flash-preview")
        if not self.base_url:
            raise LLMClientError(
                "BACKEND_MCP_URL not set — the backend must inject it at "
                "sandbox spawn so the LLM proxy can be reached."
            )

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[bytes] | None = None,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        _retry: int = 0,  # retained for signature compatibility; backend retries
    ) -> GeminiResponse:
        images_b64: list[str] = []
        if images:
            for img in images:
                images_b64.append(base64.b64encode(img).decode("ascii"))

        body = {
            "run_id": self.run_id,
            "prompt": prompt,
            "system": system,
            "images_b64": images_b64,
            "json_mode": json_mode,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {}
        if self.run_token:
            headers["Authorization"] = f"Bearer {self.run_token}"

        url = f"{self.base_url}/sandbox/llm/generate"
        start = time.monotonic()
        # Generous timeout — the backend itself retries transient errors,
        # which can legitimately take up to ~a minute.
        try:
            with httpx.Client(timeout=180.0) as client:
                r = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMClientError(f"LLM proxy unreachable: {e}") from e

        latency_ms = int((time.monotonic() - start) * 1000)

        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text[:500]
            raise LLMClientError(f"LLM proxy returned {r.status_code}: {detail}")

        data: dict[str, Any] = r.json()
        if not data.get("ok"):
            # Backend caught a real Gemini error — surface it like the old
            # client did (as a raised exception the loop records in its trace).
            # Both flags are terminal for the run: provider quota will not
            # refill today, and the run budget ceiling will not move.
            if data.get("quota_exhausted") or data.get("budget_exceeded"):
                raise QuotaExhaustedError(
                    f"LLM error: {data.get('error', 'daily quota exhausted')}"
                )
            raise LLMClientError(f"LLM error: {data.get('error', 'unknown')}")

        return GeminiResponse(
            text=data.get("text", ""),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            latency_ms=data.get("latency_ms", latency_ms),
        )


# Accurate name for new code. Same object, so existing imports keep working.
LLMProxyClient = GeminiClient
