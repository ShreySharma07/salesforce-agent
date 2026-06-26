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

Thoroughness Rules:
  - Be THOROUGH: produce one step for EVERY distinct action visible in the
    captions — every click, type, navigation, selection, and read. Do not
    collapse multiple actions into one step or skip "small" steps. A faithful
    plan has as many steps as the recording has actions.
  - For each ui_action, capture the EXACT on-screen label/text of the target
    (button text, field label, link text, tab name) verbatim in
    target_description, and the exact typed value in value.
  - Preserve ORDER precisely as shown in the captions.
  - If the recording shows the user reading/looking at a value (a case number,
    an amount, a status), add an `extract` step for it — those reads are part
    of the task.
  - Keep each step's "description" to one concise line, but never omit a step
    to save space.

Navigation Rules (critical for Salesforce and web apps):
  - PREFER `navigate` steps with direct URLs over ui_action steps that click
    through menus, app launchers, or list-view pickers. If a URL reaches the
    target, emit `navigate` — not "click App Launcher → search → click item →
    click dropdown → select view". Those multi-click UI paths are fragile;
    direct URL navigation is instant and reliable.
    Emit `ui_action` ONLY for things with NO direct URL: modal forms for
    creating/editing records, field dropdowns inside a form, buttons that
    trigger an action on an already-open record.
  - Salesforce URL patterns for `navigate` steps:
      List view:            /lightning/o/<Object>/list?filterName=<FilterApiName>
      New-record modal:     /lightning/o/<Object>/new
      (Do NOT build /lightning/r/<Object>/<RecordId>/view unless you already
       have the exact 18-char record ID from a prior step — see below.)
  - Opening a record from a list (recency rule — READ CAREFULLY):
    The Salesforce 18-character record ID is NEVER visible on screen. Screen
    text shows only the human-readable case/record NUMBER (e.g. 00001368), NOT
    the record ID. An `extract` step that reads "the first case's ID" will
    return a full sentence like "The first case in the list is 00001368." —
    NOT an 18-char ID — making any subsequent navigate to
    /lightning/r/Case/${case_id}/view produce a garbage URL and an infinite
    recovery loop.
    CORRECT PATTERN for "open the most recent / newest / first / top record":
      1. A `navigate` step to the filtered list URL.
      2. A `ui_action` step: intent "click", target_description naming the
         FIRST ROW'S LINK precisely (e.g. "first case number link in the Acme
         Cases list"). Clicking the row link carries the correct 18-char record
         ID automatically — no extraction or URL construction needed.
    DO NOT emit an `extract` + `navigate ${record_id}` pair for this pattern.
    Hardcode a /lightning/r/<Object>/<RecordId>/view URL ONLY when the exact
    18-char record ID is explicitly present in the captions (extremely rare).
  - Always start a Salesforce plan with an `open_app` step (fresh container
    has no session): kind "ui_action", intent "open_app",
    target_description "Salesforce Lightning".
  - A plan that edits or sets a field via inline edit MUST end with a Save
    step (kind "ui_action", intent "click", target_description "Save") so
    the change is persisted.

Authentication Rules (CRITICAL — never skip):
  - Authentication is ALWAYS handled by the single open_app step above.
    NEVER emit steps that perform manual login, regardless of what the
    recording or narration shows.
  - Specifically, NEVER emit any step that:
      • navigates to login.salesforce.com or any URL containing "/login"
      • types a username, email address, or password into a field
      • clicks a "Log In", "Sign In", "Next", or "Continue" button on a
        login / identity / SSO page
  - If the recording begins on a login page, or the narration says "every
    morning I log into Salesforce" / "I open Salesforce" / "I log in first":
    treat that entire login sequence as already satisfied by open_app.
    Strip it — do not include it anywhere in the plan's steps array.
  - The first step after open_app must be a navigate or ui_action that
    operates inside the authenticated Salesforce app (e.g. navigating to a
    list view), never a step on a pre-auth page.

Recurrence and Loop Generalization Rules:
  - When narration expresses RECURRENCE or iteration — phrases like
    "every morning", "I do this for each / any / every", "any new case",
    "whenever there is a case", "for all cases that", "the moment I see a
    new X" — the plan MUST GENERALIZE rather than hardcode the single
    example record shown in the recording.
  - NEVER hardcode a specific case/record number (e.g. 00001386, 00001382)
    when narration says the action applies to "any", "every", or "all"
    matching records. That number is just the demo example; the real
    automation runs on every qualifying record.
  - Correct pattern when narration expresses "for each matching record":
      1. `navigate` — to the filtered list view URL.
      2. `extract` — variable_name "<plural>_list" (e.g. "new_cases"),
         description quoting the EXACT filter criterion from the narration
         (e.g. "All Cases with Status=New in the Acme Cases list view").
      3. `loop`    — over "${<plural>_list}", item_variable matching the
         record type (e.g. "case"), body = [step_ids of the per-item
         actions that follow].
      4. Loop body steps — the per-item actions (click row link, inspect,
         fill fields, save) exactly as demonstrated for the one example
         case, renumbered sequentially after the loop step.
  - The extract step's description must capture the narration's filter
    verbatim. Example: narration "any new case with status New for Acme" →
    description "All Cases with Status=New in the Acme Cases list view".
  - Add a decision_rule explaining the filter criterion and cite the
    narration phrase that established it.
  - LOOP BODY COMPLETENESS (critical): when restructuring into a loop,
    the loop body MUST contain EVERY per-item action from the source —
    do NOT drop, merge, or simplify steps. Every field fill, every status
    change, every Save, and every assignee/contact update that existed in
    the detailed single-case plan must appear inside the loop body. The
    loop body is simply the original per-case sequence parameterised for
    the loop variable — nothing may be omitted to save space.
  - PEOPLE AND ROLES must never be conflated. A recording may involve
    multiple distinct people with distinct roles:
      • A CASE CONTACT is the person who reported or owns the case
        (updated via the Contact Name field ON THE CASE RECORD).
      • A TASK ASSIGNEE is the person who must perform the follow-up action
        (set via the Name / Assigned To field ON THE TASK form).
    If narration mentions two people in different roles, emit separate steps
    for each: one updating the case's Contact Name, one setting the task's
    Name/Assigned-To. Never merge these into a single step or omit one.

  - DRAIN-THE-QUEUE PATTERN (use instead of extract-then-iterate whenever the
    list is pre-filtered and processed items exit the filter automatically):
    When a recording shows the user working through a filtered list where each
    processed record will change status and thereby leave the filter (e.g. a
    Status=New list where processing sets Status=Escalated), use the drain
    pattern:
      1. `navigate` — to the pre-filtered list URL (before the loop).
      2. `loop` — details.over = "__drain__", no item_variable needed. Loop
         body must be:
           a. First step: a `ui_action` that clicks the FIRST ROW LINK in
              the list (e.g. "first case row link in the Acme Cases list").
              This is the DRAIN SENTINEL — when the list is empty this step
              fails and the executor treats it as "loop complete" (success).
           b. Middle steps: completion check + per-item processing.
           c. LAST step (mandatory): a `navigate` step back to the filtered
              list URL. This reloads the filtered list so the next drain
              iteration sees the updated rows. The loop will NOT work correctly
              without this final navigate-back step.
      DO NOT emit an `extract` step before the loop to pre-enumerate items.
      There is nothing to enumerate upfront — the sentinel first step handles
      empty-list detection.

  - COMPLETION CHECK (resumability / idempotency): Inside the drain loop body,
    add immediately after the first-row-click step:
      1. `extract` — variable_name "has_<marker>_task", on_failure "continue".
         Description: instruct the agent to check the Related tab or Activities
         section for the task that marks full case completion (e.g. a task with
         Subject='Call'). Agent should respond ONLY 'YES' or 'NO'.
      2. `decision` — condition "'YES' in str(has_<marker>_task).upper()",
         if_true = [] (empty — skip processing; the unconditional navigate-back
         at the end of the body still runs), if_false = [all processing step
         IDs].
      The navigate-back step is ALWAYS the last step in the loop body (not
      inside the decision's branches) so it runs unconditionally every
      iteration whether the case was skipped or processed.

Voice Narration Rules (when NARRATION lines are present in the timeline):
  - NARRATION lines contain the user's spoken explanation of what they were
    doing at that moment. This is the most reliable signal for INTENT —
    treat it as the user's own annotation of the recording.
  - When a NARRATION line states the purpose of an action (e.g. "now I'm
    selecting the case assigned to Acme to check the SLA status"), use that
    stated purpose in the step's "description" and "intent" fields verbatim
    where possible. Do not invent intent from visual inference alone when
    narration provides it directly.
  - When narration mentions a specific value, name, status, or date, treat
    it as ground truth over anything inferred from a blurry or cropped frame.
  - When narration describes an upcoming action ("I'm about to click New
    Task"), map it to the next visual action in the timeline.
  - When narration and visual disagree (e.g. narration says "close the
    modal" but the frame shows no modal), trust the narration — the frame
    may be from just before or after the UI transition.
  - NARRATION is optional: many frames will have no NARRATION line. In
    that case, infer intent from the visual description as normal.
"""


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