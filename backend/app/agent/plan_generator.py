"""
Plan generator. Takes per-frame captions and synthesizes a structured Plan.

This is the second LLM call in the pipeline (the first is captioning).
Splitting into two stages keeps each prompt focused and reduces total
token usage versus stuffing everything into one giant prompt.
"""
from __future__ import annotations

import json
import uuid

from app.agent.keyframe_captioner import FrameCaption
from app.core.llm.client import LLMClient
from app.core.prompts import PLAN_SYSTEM_PROMPT
from app.core.llm.factory import get_llm_client
from app.schemas.plan import (
    Credential,
    DecisionRule,
    Plan,
    PlanStatus,
    Step,
    StepKind,
)




def _build_timeline(captions: list[FrameCaption]) -> str:
    """Format captions into the prompt timeline, including narration when present."""
    lines = []
    for c in captions:
        line = f"[{c.timestamp_seconds:>6.2f}s] VISUAL: {c.description}"
        if c.narration:
            line += f'\n{"":>12}NARRATION: "{c.narration}"'
        lines.append(line)
    return "\n".join(lines)


def generate_plan(
    captions: list[FrameCaption],
    *,
    source_video_id: str | None = None,
    llm: LLMClient | None = None,
) -> Plan:
    """Synthesize a Plan from per-frame captions."""
    llm = llm or get_llm_client()

    timeline = _build_timeline(captions)
    prompt = (
        "Captions of the recorded task, in order:\n\n"
        f"{timeline}\n\n"
        "Output the JSON plan."
    )

    response = llm.generate(
        prompt=prompt,
        purpose="plan_synthesis",
        system=PLAN_SYSTEM_PROMPT,
        json_mode=True,
        max_tokens=8192,
    )

    plan_data = _parse_plan_json(response.text)
    return _build_plan_object(plan_data, source_video_id=source_video_id)


def regenerate_plan_with_feedback(
    captions: list[FrameCaption],
    previous_plan: Plan,
    user_feedback: str,
    *,
    llm: LLMClient | None = None,
) -> Plan:
    """Take a previous plan + the user's correction, ask the LLM to revise."""
    llm = llm or get_llm_client()

    timeline = _build_timeline(captions)
    prompt = (
        "Captions of the recorded task:\n\n"
        f"{timeline}\n\n"
        "Previous plan:\n"
        f"{previous_plan.model_dump_json(indent=2)}\n\n"
        "User correction:\n"
        f"{user_feedback}\n\n"
        "Output a revised JSON plan that incorporates the correction."
    )
    response = llm.generate(
        prompt=prompt,
        purpose="plan_correction",
        system=PLAN_SYSTEM_PROMPT,
        json_mode=True,
        max_tokens=4000,
    )

    plan_data = _parse_plan_json(response.text)
    revised = _build_plan_object(
        plan_data,
        source_video_id=previous_plan.source_video_id,
    )
    revised.id = previous_plan.id
    revised.version = previous_plan.version + 1
    revised.correction_history = previous_plan.correction_history + [user_feedback]
    return revised


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_plan_json(text: str) -> dict:
    """Parse the model's JSON plan, tolerating code fences and trailing commas."""
    import re
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()

    # First attempt: parse as-is.
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # Second attempt: strip trailing commas before ] or } (common LLM mistake).
    repaired = re.sub(r",\s*([}\]])", r"\1", t)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Plan generator: model output was not valid JSON "
            f"(tried trailing-comma repair too). "
            f"First 1000 chars:\n{text[:1000]}\n"
            f"JSON error: {e}"
        ) from e


def _build_plan_object(data: dict, *, source_video_id: str | None) -> Plan:
    """Convert the LLM's JSON dict into our Plan Pydantic model."""
    steps_raw = data.get("steps", [])
    steps: list[Step] = []
    for raw in steps_raw:
        try:
            kind = StepKind(raw.get("kind", "ui_action"))
        except ValueError:
            kind = StepKind.UI_ACTION
        steps.append(
            Step(
                id=raw.get("id") or f"step_{len(steps) + 1:03d}",
                kind=kind,
                description=raw.get("description", ""),
                details=raw.get("details", {}) or {},
                success_condition=raw.get("success_condition") or None,
                on_failure=raw.get("on_failure", "pause"),
            )
        )

    decision_rules = [
        DecisionRule(
            rule=r.get("rule", ""),
            inferred_from=r.get("inferred_from"),
        )
        for r in data.get("decision_rules", [])
        if r.get("rule")
    ]
    creds = [
        Credential(name=c.get("name", ""), scopes=c.get("scopes", []) or [])
        for c in data.get("required_credentials", [])
        if c.get("name")
    ]

    return Plan(
        id=f"plan_{uuid.uuid4().hex[:10]}",
        goal=data.get("goal", "(no goal extracted)"),
        summary=data.get("summary"),
        steps=steps,
        decision_rules=decision_rules,
        required_credentials=creds,
        status=PlanStatus.PENDING_APPROVAL,
        source_video_id=source_video_id,
    )


def regenerate_plan_from_intent(
    previous_plan: "Plan",
    user_feedback: str,
    *,
    captions: "list | None" = None,
    llm: "LLMClient | None" = None,
) -> "Plan":
    """Revise a plan from the user's stated intent.
 
    If `captions` are available, they anchor the revision to the recording
    (most faithful). If not, the previous plan's steps ARE the record of what
    was demonstrated, and we apply only the user's correction on top — without
    inventing steps that weren't there.
    """
    from app.core.llm.factory import get_llm_client
    llm = llm or get_llm_client()
 
    if captions:
        timeline = _build_timeline(captions)
        anchor = f"Captions of the recorded task:\n\n{timeline}\n\n"
        faithfulness = (
            "Stay faithful to the DEMONSTRATED actions in the captions. Apply the "
            "user's correction on top — do not invent steps that were not shown."
        )
    else:
        anchor = ""
        faithfulness = (
            "The previous plan's steps ARE the record of what the user demonstrated "
            "in the recording. Treat them as ground truth for WHAT was done. Apply "
            "ONLY the user's correction — change which records/cases/items the steps "
            "operate over, or how they branch/loop, as the user asks. Do NOT invent "
            "new UI actions that weren't in the original plan, and do NOT drop "
            "demonstrated steps unless the user says to."
        )
 
    prompt = (
        f"{anchor}"
        "Previous plan:\n"
        f"{previous_plan.model_dump_json(indent=2)}\n\n"
        "User's correction / intent:\n"
        f"{user_feedback}\n\n"
        f"{faithfulness}\n\n"
        "If the user asks to generalize from one specific record to 'all new' or "
        "'matching' records, express that with a `decision` and/or `loop` step over "
        "the appropriate collection, rather than a hardcoded ID.\n\n"
        "Output a revised JSON plan."
    )
    response = llm.generate(
        prompt=prompt,
        purpose="plan_correction",
        system=PLAN_SYSTEM_PROMPT,
        json_mode=True,
        max_tokens=20000,
    )
    plan_data = _parse_plan_json(response.text)
    revised = _build_plan_object(plan_data, source_video_id=previous_plan.source_video_id)
    revised.id = previous_plan.id
    revised.version = previous_plan.version + 1
    revised.correction_history = previous_plan.correction_history + [user_feedback]
    revised.status = PlanStatus.PENDING_APPROVAL
    return revised