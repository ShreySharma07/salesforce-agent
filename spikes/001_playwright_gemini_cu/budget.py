"""
Per-run budget enforcement.

Four independent caps. Exceed any and the run stops. In production these
same checks live at the workflow activity level; for the spike they gate
the main loop.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from llm_client import LLMClient


class BudgetExceeded(Exception):
    """Raised when any budget cap is hit. The agent loop catches this
    and performs a graceful shutdown (save trace, close browser)."""


@dataclass
class Budget:
    max_model_calls: int
    max_usd: float
    max_wall_clock_seconds: int
    max_actions: int = 50  # defense against infinite loops that don't call model

    def __post_init__(self):
        self._started_at: float | None = None
        self._actions_executed: int = 0

    def start(self) -> None:
        self._started_at = time.monotonic()

    def record_action(self) -> None:
        self._actions_executed += 1

    def check(self, llm: LLMClient) -> None:
        """Raise BudgetExceeded if any cap is hit. Call before each model
        call and after each action execution."""
        assert self._started_at is not None, "call start() before check()"

        elapsed = time.monotonic() - self._started_at
        if elapsed > self.max_wall_clock_seconds:
            raise BudgetExceeded(
                f"Wall clock {elapsed:.1f}s > {self.max_wall_clock_seconds}s"
            )

        calls = llm.total_calls()
        if calls >= self.max_model_calls:
            raise BudgetExceeded(
                f"Model calls {calls} >= {self.max_model_calls}"
            )

        cost = llm.total_cost()
        if cost >= self.max_usd:
            raise BudgetExceeded(f"Cost ${cost:.4f} >= ${self.max_usd}")

        if self._actions_executed >= self.max_actions:
            raise BudgetExceeded(
                f"Actions {self._actions_executed} >= {self.max_actions}"
            )

    def snapshot(self, llm: LLMClient) -> dict:
        """For logging."""
        elapsed = (
            time.monotonic() - self._started_at
            if self._started_at is not None
            else 0.0
        )
        return {
            "elapsed_seconds": round(elapsed, 2),
            "model_calls": llm.total_calls(),
            "cost_usd": round(llm.total_cost(), 6),
            "actions_executed": self._actions_executed,
        }