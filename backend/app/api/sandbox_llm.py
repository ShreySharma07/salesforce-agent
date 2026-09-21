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
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.core.budget import get_budget_tracker
from app.services.run_repo import get_repository


router = APIRouter(prefix="/sandbox/llm", tags=["sandbox-llm"])
log = logging.getLogger("sandbox-llm")


class QuotaExhaustedError(Exception):
    """Raised when the LLM daily quota is exhausted. Distinct from transient
    errors: retrying within the run is pointless, so the run should abort."""
    pass


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
    # Which model actually served the call (for per-run cost accounting).
    model: str = ""
    error: str | None = None
    # True when the failure is daily-quota exhaustion — signals the sandbox
    # to abort the whole run rather than retry or continue.
    quota_exhausted: bool = False
    # True when the RUN's own call/dollar budget is spent. Also terminal:
    # the sandbox aborts rather than retrying (the ceiling will not move).
    budget_exceeded: bool = False


# ---------------------------------------------------------------------------
# Run-token validation (shared shape with /mcp)
# ---------------------------------------------------------------------------

async def _validate_run_token(run_id: str | None, authorization: str | None):
    """Reject the call unless (run_id, bearer token) matches a Run's stored hash.
    No anonymous / no-run path: the proxy spends the backend's LLM key."""
    from app.services.run_auth import authenticate_run
    return await authenticate_run(run_id, authorization)


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
        raise HTTPException(
            500,
            "the sandbox is configured to use Gemini but the backend has no "
            "GEMINI_API_KEY set",
        )

    client = genai.Client(api_key=settings.gemini_api_key)
    # The SANDBOX model, which may differ from the one used to build plans.
    model = settings.effective_sandbox_model()

    parts: list[Any] = [Part(text=req.prompt)]
    for img_b64 in req.images_b64:
        try:
            img_bytes = base64.b64decode(img_b64)
        except Exception as e:
            raise HTTPException(400, f"bad base64 image: {e}")
        # Detect JPEG (FF D8) vs PNG (89 50) from magic bytes — Fix 7 sends JPEG.
        mime_type = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
        parts.append(Part.from_bytes(data=img_bytes, mime_type=mime_type))
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

            # Daily-quota exhaustion is NOT transient — it won't refill for
            # hours. Retrying just burns ~60s per attempt for nothing. Detect
            # it and fail fast so the run can abort instead of hanging.
            is_daily_quota = (
                "PerDay" in err
                or "per day" in err.lower()
                or "GenerateRequestsPerDayPerProject" in err
            )
            if is_daily_quota:
                log.warning("daily LLM quota exhausted — failing fast (no retry)")
                raise QuotaExhaustedError(
                    "LLM daily quota exhausted; the run cannot continue today"
                ) from exc

            # Genuine transients: per-minute rate limits, server overload,
            # and the occasional bad-image blip. These DO benefit from a retry.
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

    # The ReAct loop cannot act on an empty response; surfacing the reason
    # puts it in the step's trace instead of looking like a parse failure.
    if not text.strip():
        from app.core.llm.gemini import _explain_empty_gemini_response
        raise RuntimeError(
            _explain_empty_gemini_response(raw, model, req.max_tokens))

    return LLMGenerateResponse(
        ok=True, text=text, model=model,
        input_tokens=in_tok, output_tokens=out_tok, latency_ms=latency_ms,
    )


def _claude_generate(req: LLMGenerateRequest) -> LLMGenerateResponse:
    """Call Claude (Anthropic) using the backend's configured key. Synchronous —
    wrapped in a threadpool by FastAPI since the route is declared sync."""
    from app.core.llm.anthropic_client import build_anthropic_client

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(
            500,
            "the sandbox is configured to use Claude but the backend has no "
            "ANTHROPIC_API_KEY set",
        )

    # Shared constructor: carries the Brotli workaround, without which every
    # Claude call fails as a bogus "Connection error".
    client = build_anthropic_client(settings.anthropic_api_key)
    # The SANDBOX model, which may differ from the one used to build plans.
    model = settings.effective_sandbox_model()

    # Build the user content block: images first, then the text prompt.
    # Claude processes images before text so spatial reasoning sees the
    # screenshot before reading the elements/goal.
    content: list[Any] = []
    for img_b64 in req.images_b64:
        try:
            img_bytes = base64.b64decode(img_b64)
        except Exception as e:
            raise HTTPException(400, f"bad base64 image: {e}")
        mime_type = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": img_b64},
        })
    content.append({"type": "text", "text": req.prompt})

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": req.max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if req.system:
        kwargs["system"] = req.system

    start = time.monotonic()
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            raw = client.messages.create(**kwargs)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            err = str(exc)

            is_daily_quota = "daily" in err.lower() and "quota" in err.lower()
            if is_daily_quota:
                log.warning("daily LLM quota exhausted (Claude) — failing fast")
                raise QuotaExhaustedError(
                    "LLM daily quota exhausted; the run cannot continue today"
                ) from exc

            transient = (
                "429" in err or "529" in err
                or "503" in err or "502" in err
                or "rate_limit" in err.lower()
                or "overloaded" in err.lower()
                or "apiconnectionerror" in err.lower()
                or "apitimeouterror" in err.lower()
                or "connection error" in err.lower()
                or "connection reset" in err.lower()
                or "timed out" in err.lower()
                or "temporarily unavailable" in err.lower()
            )
            if attempt < max_retries and transient:
                wait = 2.0 * (attempt + 1)
                log.warning("transient Claude error (attempt %d), retrying in %.1fs: %s",
                            attempt + 1, wait, err[:200])
                time.sleep(wait)
                continue
            raise
    latency_ms = int((time.monotonic() - start) * 1000)

    if last_exc is not None:
        raise last_exc

    text = "".join(b.text for b in raw.content if hasattr(b, "text"))

    # If json_mode was requested but the response has prose around the JSON,
    # strip it — Claude follows the system prompt's JSON-only instruction
    # reliably but may occasionally wrap it in a code fence on a retry.
    if req.json_mode and text:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if not stripped.startswith("{"):
            m = re.search(r"\{.*\}", stripped, re.DOTALL)
            if m:
                text = m.group(0)

    return LLMGenerateResponse(
        ok=True, text=text, model=model,
        input_tokens=raw.usage.input_tokens,
        output_tokens=raw.usage.output_tokens,
        latency_ms=latency_ms,
    )


@router.post("/generate", response_model=LLMGenerateResponse)
async def generate(
    req: LLMGenerateRequest,
    authorization: str | None = Header(default=None),
) -> LLMGenerateResponse:
    """LLM proxy for the sandbox.

    Validates the per-run token, enforces the run's call/dollar budget, then
    calls the configured provider in a worker thread with the backend's key.
    Provider errors come back as ok=False (rather than raised) so the sandbox
    records them in its trace; quota and budget exhaustion are flagged
    separately because both are terminal for the run.
    """
    await _validate_run_token(req.run_id, authorization)
    settings = get_settings()

    # Budget gate: refuse the call BEFORE spending anything.
    tracker = get_budget_tracker()
    reason = tracker.check(req.run_id)
    if reason:
        log.warning("run %s refused an LLM call: %s", req.run_id, reason)
        return LLMGenerateResponse(ok=False, error=reason, budget_exceeded=True)

    # The ReAct loop's provider, which is independent of the one that built
    # the plan. Config errors surface as 500 here rather than as a confusing
    # provider-side rejection.
    try:
        provider = settings.effective_sandbox_provider()
        settings.effective_sandbox_model()
    except ValueError as e:
        raise HTTPException(500, str(e))
    _fn = _claude_generate if provider == "anthropic" else _gemini_generate
    try:
        # Both generators are sync + blocking; run off the event loop.
        import anyio
        result = await anyio.to_thread.run_sync(_fn, req)
    except HTTPException:
        raise
    except QuotaExhaustedError as e:
        # Distinct signal: the run should abort, not retry or continue.
        return LLMGenerateResponse(ok=False, error=str(e), quota_exhausted=True)
    except Exception as e:
        # Surface the real error to the sandbox so its trace records it.
        return LLMGenerateResponse(ok=False, error=f"{type(e).__name__}: {e}")

    # Record what this call cost against the run's ledger.
    spend = tracker.record(
        req.run_id, model=result.model or settings.effective_sandbox_model(),
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
    )
    if spend is not None:
        log.debug(
            "run %s spend: %d calls, $%.4f", req.run_id, spend.calls, spend.cost_usd,
        )
    return result
