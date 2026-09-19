"""
Per-run cost and call budgets.

A ReAct loop decides for itself how many model calls a step needs, so a
pathological run (a step the agent cannot ground, a loop that never drains)
can burn far more than intended. The per-step iteration cap bounds one step;
this module bounds the WHOLE run.

Where it runs: the LLM proxy (`/sandbox/llm/generate`) is the single point
every sandbox model call passes through, so the budget is checked there. A
run that exceeds its ceiling is refused further calls and the sandbox aborts
the run — the same path as daily-quota exhaustion.

State is per-process and in-memory (the backend already runs sandboxes as
in-process background tasks). A restart clears it, which is correct: the runs
it was tracking are marked failed at startup anyway.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.config import get_settings

# USD per 1M tokens. Keys are matched by longest prefix so dated snapshots
# ("claude-opus-5-20260401") inherit their family's price.
PRICING_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    # Gemini
    "gemini-2.5-flash-lite": {"input": 0.0, "output": 0.0},
    "gemini-2.5-flash": {"input": 0.0, "output": 0.0},
    "gemini-2.5-computer-use-preview": {"input": 1.25, "output": 10.00},
    "gemini-3-flash": {"input": 0.30, "output": 2.50},
    "gemini-3-pro": {"input": 2.00, "output": 12.00},
    "gemini-3.1-pro": {"input": 2.00, "output": 12.00},
    # Claude
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-fable-5-1": {"input": 10.00, "output": 50.00},
    "claude-fable-5": {"input": 10.00, "output": 50.00},
}


class BudgetExceeded(Exception):
    """Raised/flagged when a run has spent its call or dollar ceiling."""


def price_for_model(model: str) -> dict[str, float]:
    """Return {input, output} USD per 1M tokens for a model id.

    Matches the longest known prefix so a dated or suffixed variant inherits
    its family's pricing. Unknown models price at 0.0 (cost tracking degrades
    to call counting rather than guessing a number).
    """
    best: dict[str, float] = {"input": 0.0, "output": 0.0}
    best_len = -1
    for prefix, price in PRICING_PER_1M_TOKENS.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost of one call. Used for per-run accounting and the Run record."""
    price = price_for_model(model)
    return (
        input_tokens * price["input"] / 1_000_000
        + output_tokens * price["output"] / 1_000_000
    )


@dataclass
class RunSpend:
    """Running total for one run. `model` is the last model actually billed."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


@dataclass
class BudgetTracker:
    """Thread-safe per-run spend ledger.

    The proxy route runs the provider call in a worker thread, so both the
    check and the record can land off the event loop — hence the lock.
    """
    max_calls: int
    max_usd: float
    _spend: dict[str, RunSpend] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, run_id: str | None) -> str | None:
        """Return a human-readable reason if this run may NOT make another
        model call, else None. Called before every provider request."""
        if not run_id:
            return None
        with self._lock:
            spend = self._spend.get(run_id)
            if spend is None:
                return None
            if spend.calls >= self.max_calls:
                return (
                    f"run budget exhausted: {spend.calls} model calls "
                    f"(limit {self.max_calls})"
                )
            if spend.cost_usd >= self.max_usd:
                return (
                    f"run budget exhausted: ${spend.cost_usd:.2f} spent "
                    f"(limit ${self.max_usd:.2f})"
                )
        return None

    def record(self, run_id: str | None, *, model: str,
               input_tokens: int, output_tokens: int) -> RunSpend | None:
        """Add one completed call to the run's ledger and return the new total."""
        if not run_id:
            return None
        cost = estimate_cost(model, input_tokens, output_tokens)
        with self._lock:
            spend = self._spend.setdefault(run_id, RunSpend())
            spend.calls += 1
            spend.input_tokens += input_tokens
            spend.output_tokens += output_tokens
            spend.cost_usd += cost
            spend.model = model
            return RunSpend(**vars(spend))

    def get(self, run_id: str) -> RunSpend | None:
        """Current totals for a run, or None if it has made no calls."""
        with self._lock:
            spend = self._spend.get(run_id)
            return RunSpend(**vars(spend)) if spend else None

    def clear(self, run_id: str) -> RunSpend | None:
        """Drop a finished run's ledger and return its final totals."""
        with self._lock:
            spend = self._spend.pop(run_id, None)
            return RunSpend(**vars(spend)) if spend else None


_tracker: BudgetTracker | None = None


def get_budget_tracker() -> BudgetTracker:
    """Process-wide tracker, sized from settings on first use."""
    global _tracker
    if _tracker is None:
        settings = get_settings()
        _tracker = BudgetTracker(
            max_calls=settings.default_max_model_calls_per_run,
            max_usd=settings.default_max_usd_per_run,
        )
    return _tracker


__all__ = [
    "BudgetExceeded",
    "BudgetTracker",
    "RunSpend",
    "estimate_cost",
    "get_budget_tracker",
    "price_for_model",
    "PRICING_PER_1M_TOKENS",
]
