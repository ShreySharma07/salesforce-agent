"""
Episodic memory: capture, store, retrieve.

Procedural memory learns "how to do task X" from SUCCESSES. Episodic memory
learns "what notable thing happened in situation Y" — mostly from the unhappy
path: failures, give-ups, captchas, obstructions, and human fixes.

It reads the fields the procedural distiller deliberately ignored:
  - StepExecution.error / pause_reason       (what went wrong)
  - LoopIteration with action give_up / captcha_detected / dismiss_obstruction
  - Run.interventions (HumanIntervention)     (what a human did about it)

Episodes are concrete recollections, retrieved by a `situation_key` (a
normalized descriptor of the step's intent/goal) and surfaced as cautionary
hints alongside procedural priming. Built and tested with zero LLM / zero
Salesforce, against the real Run/StepExecution/LoopIteration/HumanIntervention
shapes.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from app.schemas.memory import Episode, EpisodeKind
from app.services.memory.signature import normalize_goal


# ---------------------------------------------------------------------------
# Situation key — the retrieval anchor for an episode
# ---------------------------------------------------------------------------

def situation_key(*, goal: str, step_intent: str = "") -> str:
    """A normalized descriptor of the situation an episode happened in.

    Reuses the same normalization as task signatures so "creating a lead for
    Acme" and "creating a lead for Globex" share a situation. Folds in the
    step intent when present so episodes are scoped to the specific step, not
    just the whole task.
    """
    base = normalize_goal(goal)
    if step_intent:
        step_part = normalize_goal(step_intent)
        # Keep step tokens not already in the goal, so the key is
        # goal-context + what's distinctive about this step.
        extra = [t for t in step_part.split() if t and t not in base.split()]
        if extra:
            return (base + " :: " + " ".join(sorted(set(extra)))).strip(" :")
    return base


def _se_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Capture — Run -> list[Episode]
# ---------------------------------------------------------------------------

def capture_episodes(
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
    step_executions: list[Any],
    interventions: list[Any],
    run_id: str,
) -> list[Episode]:
    """Scan a run for notable events and produce Episodes.

    Unlike the procedural distiller, this learns from BOTH outcomes — a
    failed run is often the most instructive. Returns possibly-empty list.
    """
    episodes: list[Episode] = []
    now = datetime.utcnow()

    # Map step_id -> a readable intent, for situation keys + messages.
    # step_descriptions align with the plan's steps positionally; we also use
    # the step's own id when present.
    for idx, se in enumerate(step_executions):
        step_id = _se_get(se, "step_id", None)
        status = _se_get(se, "status", "")
        error = _se_get(se, "error", None)
        pause_reason = _se_get(se, "pause_reason", None)
        trace = _se_get(se, "trace", []) or []
        step_desc = step_descriptions[idx] if idx < len(step_descriptions) else ""
        sit = situation_key(goal=goal, step_intent=step_desc)

        # --- step-level failure ---
        if status == "failed" and error:
            episodes.append(Episode(
                id=f"ep_{uuid.uuid4().hex[:10]}",
                user_id=user_id, kind=EpisodeKind.STEP_FAILURE,
                situation_key=sit,
                what_happened=f"Step failed: {_clip(error)}",
                run_id=run_id, step_id=step_id, goal=goal, occurred_at=now,
            ))

        # --- scroll-stuck pattern ---
        # When a step fails and the trace shows 5+ scroll actions, the root
        # cause is almost certainly "agent couldn't ground a visible element
        # and scrolled in place hoping it would appear". The wall-time timeout
        # string recorded above is accurate but not actionable. Emit a SECOND
        # episode with a concrete resolution so the next run gets a "do this
        # instead" hint rather than "the step timed out (again)".
        # A resolution-carrying episode is is_actionable=True on FIRST occurrence
        # (bypasses the seen_count >= 2 threshold), so the agent benefits
        # immediately on run 2 — not after a third failure.
        if status == "failed":
            scroll_actions = [it for it in trace if _se_get(it, "action", "") == "scroll"]
            if len(scroll_actions) >= 5:
                episodes.append(Episode(
                    id=f"ep_{uuid.uuid4().hex[:10]}",
                    user_id=user_id, kind=EpisodeKind.STEP_FAILURE,
                    situation_key=sit,
                    what_happened=(
                        f"Agent scrolled {len(scroll_actions)}x searching for an "
                        f"element that had no grounded ref — step timed out."
                    ),
                    resolution=(
                        "If the target element is VISIBLE on screen but absent from "
                        "ELEMENTS, use click_text with its exact visible text or "
                        "fill_field_by_label with its label — both pierce shadow DOM "
                        "without needing a ref. "
                        "Do NOT scroll: scrolling cannot expose elements rendered "
                        "in closed shadow roots. "
                        "After an inline field edit, the Save button is in a docked "
                        "form footer at the bottom of the record — look for a button "
                        "named 'Save' in ELEMENTS (sf#### ref) or use "
                        "click_text \"Save\" directly."
                    ),
                    run_id=run_id, step_id=step_id, goal=goal, occurred_at=now,
                ))

        # --- captcha / pause reasons ---
        if pause_reason == "captcha":
            episodes.append(Episode(
                id=f"ep_{uuid.uuid4().hex[:10]}",
                user_id=user_id, kind=EpisodeKind.CAPTCHA,
                situation_key=sit,
                what_happened="A CAPTCHA / bot-check blocked this step.",
                resolution="Pause and hand off to a human; do not attempt to solve.",
                run_id=run_id, step_id=step_id, goal=goal, occurred_at=now,
            ))

        # --- notable trace events ---
        for it in trace:
            action = (_se_get(it, "action", "") or "").strip()
            thought = _se_get(it, "thought", "") or ""
            args = _se_get(it, "action_args", {}) or {}
            if action == "give_up":
                reason = args.get("reason") if isinstance(args, dict) else ""
                episodes.append(Episode(
                    id=f"ep_{uuid.uuid4().hex[:10]}",
                    user_id=user_id, kind=EpisodeKind.GAVE_UP,
                    situation_key=sit,
                    what_happened=f"Agent gave up: {_clip(reason or thought)}",
                    run_id=run_id, step_id=step_id, goal=goal, occurred_at=now,
                ))
            elif action == "dismiss_obstruction":
                episodes.append(Episode(
                    id=f"ep_{uuid.uuid4().hex[:10]}",
                    user_id=user_id, kind=EpisodeKind.OBSTRUCTION,
                    situation_key=sit,
                    what_happened=f"An obstruction appeared: {_clip(thought)}",
                    resolution="Dismiss it (close/accept control) before proceeding.",
                    run_id=run_id, step_id=step_id, goal=goal, occurred_at=now,
                ))

    # --- human interventions: the highest-value episodic memory ---
    # What a person did when the agent was stuck is gold for next time.
    # Key it by the TASK situation (goal + step context) so it's recalled when
    # this task recurs — not by the intervention reason alone, which recall
    # wouldn't reconstruct. We fold the reason in as extra context.
    task_sit = situation_key(goal=goal)
    for iv in interventions:
        reason = _se_get(iv, "reason", "") or ""
        user_response = _se_get(iv, "user_response", None)
        if not user_response:
            continue  # an unanswered intervention teaches nothing yet
        episodes.append(Episode(
            id=f"ep_{uuid.uuid4().hex[:10]}",
            user_id=user_id, kind=EpisodeKind.HUMAN_FIX,
            situation_key=task_sit,
            what_happened=f"Agent was stuck: {_clip(reason)}",
            resolution=f"A human resolved it by: {_clip(user_response)}",
            run_id=run_id, goal=goal, occurred_at=now,
        ))

    return episodes


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class EpisodeStore(Protocol):
    async def add(self, ep: Episode) -> None: ...
    async def find(self, user_id: str, situation_key: str) -> list[Episode]: ...
    async def list_for_user(self, user_id: str) -> list[Episode]: ...


class InMemoryEpisodeStore:
    """Dict-backed episodic store. Mirrors the procedural store pattern;
    a SQL implementation slots in later without changing callers.

    De-dupes on (user_id, situation_key, kind, what_happened): a recurring
    event bumps seen_count instead of piling up duplicates — so 'this fails
    here a lot' becomes a measurable signal.
    """

    def __init__(self) -> None:
        # (user_id, situation_key) -> list[Episode]
        self._by_sit: dict[tuple[str, str], list[Episode]] = {}

    async def add(self, ep: Episode) -> None:
        key = (ep.user_id, ep.situation_key)
        bucket = self._by_sit.setdefault(key, [])
        for existing in bucket:
            if existing.kind == ep.kind and existing.what_happened == ep.what_happened:
                existing.seen_count += 1
                existing.occurred_at = ep.occurred_at
                if ep.resolution and not existing.resolution:
                    existing.resolution = ep.resolution
                return
        bucket.append(ep)

    async def find(self, user_id: str, situation_key: str) -> list[Episode]:
        return list(self._by_sit.get((user_id, situation_key), []))

    async def list_for_user(self, user_id: str) -> list[Episode]:
        out: list[Episode] = []
        for (u, _), bucket in self._by_sit.items():
            if u == user_id:
                out.extend(bucket)
        return out


# ---------------------------------------------------------------------------
# The two operations the pipeline calls
# ---------------------------------------------------------------------------

async def remember_run(
    store: EpisodeStore,
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
    step_executions: list[Any],
    interventions: list[Any],
    run_id: str,
) -> list[Episode]:
    """Task-end: capture and store all notable episodes from this run."""
    eps = capture_episodes(
        user_id=user_id, goal=goal, step_descriptions=step_descriptions,
        step_executions=step_executions, interventions=interventions,
        run_id=run_id,
    )
    for ep in eps:
        await store.add(ep)
    return eps


async def recall(
    store: EpisodeStore,
    *,
    user_id: str,
    goal: str,
    step_descriptions: list[str],
) -> list[Episode]:
    """Task-start: recall actionable episodes for the situations this task
    will encounter. Returns only episodes worth surfacing (is_actionable),
    so a single benign one-off failure doesn't nag the agent forever.
    """
    seen_ids: set[str] = set()
    out: list[Episode] = []
    # The whole-goal situation, plus each step's situation.
    keys = [situation_key(goal=goal)]
    for desc in step_descriptions:
        keys.append(situation_key(goal=goal, step_intent=desc))
    for k in keys:
        if not k:
            continue
        for ep in await store.find(user_id, k):
            if ep.id in seen_ids:
                continue
            if ep.is_actionable:
                out.append(ep)
                seen_ids.add(ep.id)
    return out


# ---------------------------------------------------------------------------
# Priming
# ---------------------------------------------------------------------------

def build_episodic_priming(episodes: list[Episode]) -> str:
    """Render recalled episodes as a compact cautionary hint block.

    Framed as warnings/tips, distinct from the procedural recipe: 'here's what
    has gone wrong here before, and what fixed it'.
    """
    if not episodes:
        return ""
    lines = ["MEMORY \u2014 things that happened in similar situations before:"]
    # Resolutions first (most actionable), then warnings.
    with_fix = [e for e in episodes if e.resolution]
    warnings = [e for e in episodes if not e.resolution]
    for e in with_fix:
        seen = f" (seen {e.seen_count}x)" if e.seen_count > 1 else ""
        lines.append(f"  - {e.what_happened}{seen} \u2192 {e.resolution}")
    for e in warnings:
        seen = f" (seen {e.seen_count}x)" if e.seen_count > 1 else ""
        lines.append(f"  - Watch out: {e.what_happened}{seen}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clip(text: str | None, max_len: int = 160) -> str:
    t = " ".join((text or "").split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "\u2026"