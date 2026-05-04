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
from app.core.llm.factory import get_llm_client
from app.schemas.plan import (
    Credential,
    DecisionRule,
    Plan,
    PlanStatus,
    Step,
    StepKind,
)


PLAN_SYSTEM_PROMPT = """You are an expert at converting recorded user actions into reusable automation plans.

You will be given a chronological list of frame captions describing a screen recording. Output a structured JSON plan.

Plan schema (strict):
{
  "goal": "<one-sentence statement of what this automation does>",
  "summary": "<2-4 sentence overview, optional>",
  "steps": [
    {
      "id": "step_001",
      "kind": "<navigate|ui_action|mcp_call|extract|decision|loop|wait|human_input|notify>",
      "description": "<one-line description>",
      "details": { ...kind-specific... }
    }
  ],
  "decision_rules": [
    {"rule": "<inferred business rule>", "inferred_from": "<why you think so>"}
  ],
  "required_credentials": [
    {"name": "<service>", "scopes": ["<scope>"]}
  ]
}

Step kind details schemas:
  navigate:    {"url": "...", "expected_title_contains": "..."}
  ui_action:   {"intent": "...", "target_description": "...", "value": "..."}
  mcp_call:    {"server": "...", "tool": "...", "arguments": {}}
  extract:     {"variable_name": "...", "description": "..."}
  decision:    {"condition": "...", "if_true": ["step_id"], "if_false": ["step_id"]}
  loop:        {"over": "${var}", "item_variable": "item", "body": ["step_id"]}
  wait:        {"seconds": 2.0, "reason": "..."}
  human_input: {"prompt": "...", "options": ["a","b"]}
  notify:      {"channel": "dashboard", "message": "..."}

Rules:
  - Use stable IDs: step_001, step_002, ...
  - Prefer navigate + ui_action over deeply structured kinds for simple flows.
  - Capture decision rules ONLY when behavior visibly differs across cases in the recording.
  - List EVERY service the plan touches in required_credentials.
  - Output ONLY the JSON object. No prose, no markdown fences.
  - For navigate steps: only emit a URL if you saw it explicitly in a caption. If a URL is implied but not seen, use a ui_action step describing the link to click instead.
  - Capture exact text observed in captions (subject lines, sender names, button labels) verbatim in step descriptions and target_descriptions.
  - Add an `extract` step whenever the recording shows the user reading or copying information from the screen.
  - Infer decision rules: if the user is filtering, sorting, or selecting based on a property visible on screen, add a rule explaining the criterion.
"""


def generate_plan(
    captions: list[FrameCaption],
    *,
    source_video_id: str | None = None,
    llm: LLMClient | None = None,
) -> Plan:
    """Synthesize a Plan from per-frame captions."""
    llm = llm or get_llm_client()

    timeline = "\n".join(
        f"[{c.timestamp_seconds:>6.2f}s] {c.description}" for c in captions
    )
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
        max_tokens=4000,
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

    timeline = "\n".join(
        f"[{c.timestamp_seconds:>6.2f}s] {c.description}" for c in captions
    )
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
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Plan generator: model output was not valid JSON. Got:\n{text[:1000]}"
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