"""
LLM client abstraction.

Every LLM call in the project goes through this module. Provider-specific
code is isolated here so that swapping Gemini <-> Anthropic later is a
config change, not a refactor.

Also the single source of truth for usage + cost tracking + rate limiting.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError


# ---------------------------------------------------------------------------
# Pricing (USD per 1M tokens). Free-tier models show 0.0 - no real charge.
# ---------------------------------------------------------------------------
PRICING_PER_1M_TOKENS = {
    # Free tier (no charge, but we track tokens for quota awareness)
    "gemini-2.5-flash": {"input": 0.0, "output": 0.0},
    "gemini-2.5-flash-lite": {"input": 0.0, "output": 0.0},
    "gemini-2.5-pro": {"input": 0.0, "output": 0.0},
    # Paid tier - for when we upgrade
    "gemini-2.5-computer-use-preview-10-2025": {"input": 1.25, "output": 10.00},
    "gemini-3-flash-preview": {"input": 0.30, "output": 2.50},
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
}

# Free-tier rate limits (approximate - Google adjusts these periodically).
# Source: https://ai.google.dev/gemini-api/docs/rate-limits
FREE_TIER_RPM = {
    "gemini-2.5-flash": 10,
    "gemini-2.5-flash-lite": 15,
    "gemini-2.5-pro": 5,
}


@dataclass
class UsageEvent:
    event_id: str
    run_id: str
    timestamp: float
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    purpose: str


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = PRICING_PER_1M_TOKENS.get(model)
    if not pricing:
        return 0.0
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
    )


@dataclass
class LLMResponse:
    raw: Any
    candidate: Any
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    model: str


class RateLimiter:
    """Simple client-side rate limiter to stay under free-tier RPM.
    Sleeps when needed. Not thread-safe - fine for spike."""

    def __init__(self, requests_per_minute: int):
        self.rpm = requests_per_minute
        self.window: deque[float] = deque()

    def wait_if_needed(self) -> float:
        """Block if we'd exceed RPM. Returns seconds slept."""
        now = time.monotonic()
        # Drop timestamps older than 60s
        while self.window and now - self.window[0] > 60.0:
            self.window.popleft()
        slept = 0.0
        if len(self.window) >= self.rpm:
            oldest = self.window[0]
            sleep_for = 60.0 - (now - oldest) + 0.5  # small buffer
            if sleep_for > 0:
                time.sleep(sleep_for)
                slept = sleep_for
        self.window.append(time.monotonic())
        return slept


class LLMClient:
    """Provider-agnostic interface with free-tier-aware rate limiting."""

    def __init__(
        self,
        run_id: str,
        model: str,
        trace_dir: Path,
        api_key: Optional[str] = None,
    ):
        self.run_id = run_id
        self.model = model
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.usage_log_path = trace_dir / "usage.jsonl"
        self.provider = "google"

        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        self._client = genai.Client()

        rpm = FREE_TIER_RPM.get(model, 60)
        self._rate_limiter = RateLimiter(requests_per_minute=rpm)

    def generate(
        self,
        contents: list,
        config: types.GenerateContentConfig,
        purpose: str,
    ) -> LLMResponse:
        """One API call with tracking, rate limiting, and 429 backoff."""
        slept = self._rate_limiter.wait_if_needed()
        if slept > 0:
            print(f"  (rate-limit pacing: slept {slept:.1f}s)")

        start = time.monotonic()
        raw = self._generate_with_retry(contents, config)
        latency_ms = int((time.monotonic() - start) * 1000)

        usage = getattr(raw, "usage_metadata", None)
        if usage is not None:
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = (
                (getattr(usage, "candidates_token_count", 0) or 0)
                + (getattr(usage, "thoughts_token_count", 0) or 0)
            )
        else:
            input_tokens = 0
            output_tokens = 0

        cost = estimate_cost_usd(self.model, input_tokens, output_tokens)
        candidate = raw.candidates[0] if raw.candidates else None

        # Extract flat text (used by non-Computer-Use structured outputs)
        text = ""
        if candidate and candidate.content and candidate.content.parts:
            text = "".join(
                p.text for p in candidate.content.parts if getattr(p, "text", None)
            )

        event = UsageEvent(
            event_id=str(uuid.uuid4()),
            run_id=self.run_id,
            timestamp=time.time(),
            model=self.model,
            provider=self.provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            purpose=purpose,
        )
        self._append_usage(event)

        return LLMResponse(
            raw=raw, candidate=candidate, text=text,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost, latency_ms=latency_ms, model=self.model,
        )

    def _generate_with_retry(self, contents, config):
        """Retry on 429 (rate-limit) and 503 (overloaded) with backoff.
        Max 4 attempts total."""
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                return self._client.models.generate_content(
                    model=self.model, contents=contents, config=config,
                )
            except ClientError as e:
                if attempt >= max_attempts:
                    raise
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = self._extract_retry_delay(e) or (10 * attempt)
                    print(f"  (429 attempt {attempt}/{max_attempts} - waiting {wait}s)")
                    time.sleep(wait)
                    continue
                raise
            except ServerError as e:
                if attempt >= max_attempts:
                    raise
                if "503" in str(e) or "UNAVAILABLE" in str(e) or "500" in str(e):
                    wait = 5 * (2 ** (attempt - 1))  # 5, 10, 20s
                    print(f"  (503 UNAVAILABLE attempt {attempt}/{max_attempts} - waiting {wait}s)")
                    time.sleep(wait)
                    continue
                raise

    @staticmethod
    def _extract_retry_delay(err: ClientError) -> Optional[int]:
        """Best-effort parse of 'Please retry in Ns' from the error message."""
        import re
        m = re.search(r"retry in ([\d.]+)s", str(err))
        return int(float(m.group(1))) + 1 if m else None

    def _append_usage(self, event: UsageEvent) -> None:
        with self.usage_log_path.open("a") as f:
            f.write(json.dumps(asdict(event)) + "\n")

    def total_cost(self) -> float:
        if not self.usage_log_path.exists():
            return 0.0
        with self.usage_log_path.open() as f:
            return sum(json.loads(line)["cost_usd"] for line in f)

    def total_calls(self) -> int:
        if not self.usage_log_path.exists():
            return 0
        with self.usage_log_path.open() as f:
            return sum(1 for _ in f)