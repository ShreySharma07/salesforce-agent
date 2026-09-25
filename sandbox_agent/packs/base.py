"""
The app-pack contract.

The browser agent's core (grounding, the ReAct loop, trajectory, budgets,
computer-mode fallback) is app-agnostic. Everything that only makes sense for
one application lives in a pack:

  prompt_rules         extra system-prompt rules for this app's UI
  host_suffixes        hosts the agent may navigate to besides the plan's own
  before_observe       page hooks run before every observation (e.g. hide toasts)
  settle               wait for the app's own loading indicators
  sequence_primitives  deterministic SEQUENCE sub-actions (no LLM)
  check_condition      cheap Playwright check of a SEQUENCE success_condition
  frontdoor_provider   the connected-app login `open_app` uses

A plan names its pack via `plan.app`; otherwise the registry infers it (see
`sandbox_agent.packs.pack_for_plan`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from playwright.sync_api import Page

SequencePrimitive = Callable[["Page", dict], str]


def _noop_page_hook(page: "Page") -> None:
    return None


def _noop_settle(page: "Page", timeout: float = 2.0) -> None:
    return None


@dataclass(frozen=True)
class AppPack:
    name: str
    display_name: str
    host_suffixes: tuple[str, ...] = ()
    prompt_rules: str = ""
    before_observe: Callable[["Page"], None] = _noop_page_hook
    settle: Callable[..., None] = _noop_settle
    sequence_primitives: dict[str, SequencePrimitive] = field(default_factory=dict)
    check_condition: Callable[["Page", str], bool] | None = None
    frontdoor_provider: str | None = None

    def owns_host(self, host: str) -> bool:
        host = host.lower()
        return any(host.endswith(sfx) for sfx in self.host_suffixes)

    def execute_sequence_sub_action(self, page: "Page", kind: str, sub: dict) -> str:
        """Run one SEQUENCE sub-action. A result starting with 'FAILED'
        tells the executor to abort the sequence."""
        fn = self.sequence_primitives.get(kind)
        if fn is None:
            return f"FAILED: unknown sequence sub-action kind '{kind}' for the {self.display_name} pack"
        return fn(page, sub)
