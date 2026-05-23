"""
LLM proxy endpoint.

The sandbox calls this instead of calling Gemini directly, so the LLM API
key never enters the container. The sandbox authenticates with its per-run
RUN_TOKEN (the same token used for /mcp), validated against the Run's
stored hash.

  POST /sandbox/llm/generate
  Authorization: Bearer <RUN_TOKEN>
  Body: {
    "run_id": "run_xxx",
    "prompt": "...",
    "system": "..." | null,
    "images_b64": ["<base64 png>", ...],
    "json_mode": true,
    "max_tokens": 1024,
    "temperature": 0.0
  }

  -> { "ok": true, "text": "...", "input_tokens": N, "output_tokens": N,
       "latency_ms": N }

The actual Gemini call reuses the backend's own LLM client/config. The
key comes from backend settings (env / .env on the backend only).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.services.run_repo import get_repository


router = APIRouter(prefix="/sandbox/llm", tags=["sandbox-llm"])
log = logging.getLogger("sandbox-llm")


class LLMGenerateRequest(BaseModel):
    run_id: str | None = None
    prompt: str
    system: str | None = None
    images_b64: list[str] = []
    json_mode: bool = False
    max_tokens: int = 1024
    temperature: float = 0.0


class LLMGenerateResponse(BaseModel):
    ok: bool
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Run-token validation (shared shape with /mcp)
# ---------------------------------------------------------------------------

async def _validate_run_token(run_id: str | None, authorization: str | None) -> None:
    """Reject the call unless the bearer token matches the Run's stored hash.
    A run with no stored hash (older runs) is allowed through for compatibility,
    same policy as the /mcp endpoint."""
    if not run_id:
        # No run context — allowed only in single-user/testing, same as /mcp.
        return
    repo = get_repository()
    run = await repo.get_run(run_id)
    if run is None:
        return
    if run.mcp_token_hash is not None:
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ")
        if token is None or hashlib.sha256(token.encode()).hexdigest() != run.mcp_token_hash:
            raise HTTPException(401, "invalid run token")


# ---------------------------------------------------------------------------
# The Gemini call (runs on the backend, with the backend's key)
# ---------------------------------------------------------------------------

def _gemini_generate(req: LLMGenerateRequest) -> LLMGenerateResponse:
    """Call Gemini using the backend's configured key. Synchronous —
    wrapped in a threadpool by FastAPI since the route is declared sync."""
    from google import genai
    from google.genai import types
    from google.genai.types import Content, Part

    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(500, "backend has no GEMINI_API_KEY configured")

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.llm_model

    parts: list[Any] = [Part(text=req.prompt)]
    for img_b64 in req.images_b64:
        try:
            img_bytes = base64.b64decode(img_b64)
        except Exception as e:
            raise HTTPException(400, f"bad base64 image: {e}")
        parts.append(Part.from_bytes(data=img_bytes, mime_type="image/png"))
    contents = [Content(role="user", parts=parts)]

    cfg: dict[str, Any] = {
        "temperature": req.temperature,
        "max_output_tokens": req.max_tokens,
    }
    if req.system:
        cfg["system_instruction"] = req.system
    if req.json_mode:
        cfg["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**cfg)

    # Retry transient errors: 429 (rate limit), 503 (overloaded),
    # and the 400 "Unable to process input image" blip.
    start = time.monotonic()
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            raw = client.models.generate_content(
                model=model, contents=contents, config=config,
            )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            err = str(exc)
            transient = (
                "429" in err
                or "503" in err
                or "UNAVAILABLE" in err
                or "RESOURCE_EXHAUSTED" in err
                or "Unable to process input image" in err
                or "INVALID_ARGUMENT" in err
            )
            if attempt < max_retries and transient:
                # Honor a server-suggested delay if present, else backoff.
                m = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", err, re.I)
                wait = float(m.group(1)) if m else (2.0 * (attempt + 1))
                wait = min(wait, 60.0)
                log.warning("transient LLM error (attempt %d), retrying in %.1fs: %s",
                            attempt + 1, wait, err[:200])
                time.sleep(wait)
                continue
            raise
    latency_ms = int((time.monotonic() - start) * 1000)

    if last_exc is not None:
        raise last_exc

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

    return LLMGenerateResponse(
        ok=True, text=text,
        input_tokens=in_tok, output_tokens=out_tok, latency_ms=latency_ms,
    )


@router.post("/generate", response_model=LLMGenerateResponse)
async def generate(
    req: LLMGenerateRequest,
    authorization: str | None = Header(default=None),
) -> LLMGenerateResponse:
    await _validate_run_token(req.run_id, authorization)
    try:
        # _gemini_generate is sync + blocking; run it off the event loop.
        import anyio
        return await anyio.to_thread.run_sync(_gemini_generate, req)
    except HTTPException:
        raise
    except Exception as e:
        # Surface the real error to the sandbox so its trace records it.
        return LLMGenerateResponse(
            ok=False, error=f"{type(e).__name__}: {e}",
        )