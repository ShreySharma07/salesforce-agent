"""
Plan guardrails — the safety gate between a generated Plan and a real browser.

A Plan is produced by an LLM from a screen recording, so it can be malformed
in ways Pydantic cannot catch: a `loop` whose body names a step that does not
exist, a `navigate` with no URL, a login step the plan should never contain,
an `on_failure` value the executor does not implement. Running such a plan
wastes a container and can leave a record half-edited.

`validate_plan` is called at two chokepoints — when a plan is saved
(POST /plans) and when it is approved (POST /plans/{id}/approve) — so an
invalid plan can never reach a sandbox.

Findings are split deliberately:
  errors   — the plan cannot execute correctly; the API rejects it (422)
  warnings — suspicious but runnable; surfaced to the user, not blocking

This module is pure and dependency-free (no DB, no LLM, no network), so it is
cheap to call on every write and trivially testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.schemas.plan import Plan, Step, StepKind

# The executor's real vocabulary. `retry` and `skip` are honored by the
# executor's failure router; `continue` is accepted as a legacy alias for skip.
VALID_ON_FAILURE = {"pause", "skip", "abort", "retry", "continue"}

# Sub-action kinds `sequence` steps may contain (browser_mode dispatch table).
VALID_SEQUENCE_KINDS = {
    "click_pencil_icon",
    "fill_field",
    "click_dropdown_result",
    "select_dropdown_option",
    "click_save_footer",
}

# Steps that change state should declare what "done" looks like, so the
# executor can skip them when already satisfied and verify them after acting.
STATE_CHANGING_KINDS = {StepKind.UI_ACTION, StepKind.SEQUENCE}

# Authentication is the platform's job (open_app + the frontdoor), never the
# agent's. A plan that types credentials is always a generation mistake.
_LOGIN_URL_MARKERS = ("/login", "login.salesforce.com", "accounts.google.com", "/signin")
_CREDENTIAL_WORDS = re.compile(
    r"\b(password|passwd|username|user name|log ?in|sign ?in|credential)\b", re.I
)

_ALLOWED_URL_SCHEMES = {"http", "https"}


class PlanValidationError(Exception):
    """Raised by `validate_plan_or_raise` when a plan has blocking errors."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ValidationReport:
    """Outcome of validating one plan. `ok` is False when anything blocks."""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_plan(plan: Plan) -> ValidationReport:
    """Check a Plan for structural and safety problems before it can run.

    Returns a report rather than raising, so callers can surface warnings
    alongside a successful save. Use `validate_plan_or_raise` when only the
    blocking outcome matters.
    """
    report = ValidationReport()
    if not plan.steps:
        report.errors.append("plan has no steps")
        return report

    ids = [s.id for s in plan.steps]
    _check_unique_ids(ids, report)
    known = set(ids)

    for step in plan.steps:
        _check_on_failure(step, report)
        _check_details_for_kind(step, known, report)
        _check_no_manual_login(step, report)
        _check_success_condition(step, report)

    _check_control_flow_reachability(plan, report)
    return report


def validate_plan_or_raise(plan: Plan) -> ValidationReport:
    """`validate_plan`, but raises PlanValidationError when it finds errors."""
    report = validate_plan(plan)
    if report.errors:
        raise PlanValidationError(report.errors)
    return report


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_unique_ids(ids: list[str], report: ValidationReport) -> None:
    """Duplicate step ids make `step_by_id` lose steps and break loop bodies."""
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            report.errors.append(f"duplicate step id {sid!r}")
        seen.add(sid)


def _check_on_failure(step: Step, report: ValidationReport) -> None:
    """An unknown on_failure silently degrades to 'skip' inside the executor."""
    if step.on_failure not in VALID_ON_FAILURE:
        report.errors.append(
            f"{step.id}: on_failure={step.on_failure!r} is not one of "
            f"{sorted(VALID_ON_FAILURE)}"
        )


def _check_details_for_kind(step: Step, known_ids: set[str], report: ValidationReport) -> None:
    """Each step kind needs specific `details` keys; a missing one fails at runtime."""
    d = step.details or {}

    if step.kind == StepKind.NAVIGATE:
        url = d.get("url")
        if not url:
            report.errors.append(f"{step.id}: navigate step has no 'url'")
        else:
            _check_url(step.id, str(url), report)

    elif step.kind == StepKind.MCP_CALL:
        if not d.get("server"):
            report.errors.append(f"{step.id}: mcp_call has no 'server'")
        if not d.get("tool"):
            report.errors.append(f"{step.id}: mcp_call has no 'tool'")
        # The executor accepts either key; flag the case where both disagree.
        if "arguments" in d and "args" in d and d["arguments"] != d["args"]:
            report.errors.append(
                f"{step.id}: mcp_call declares both 'arguments' and 'args' with "
                f"different values — keep one"
            )

    elif step.kind == StepKind.EXTRACT:
        if not d.get("variable_name"):
            report.errors.append(f"{step.id}: extract step has no 'variable_name'")

    elif step.kind == StepKind.WAIT:
        try:
            float(d.get("seconds", 0))
        except (TypeError, ValueError):
            report.errors.append(f"{step.id}: wait step has a non-numeric 'seconds'")

    elif step.kind == StepKind.SEQUENCE:
        subs = d.get("steps")
        if not subs:
            report.errors.append(f"{step.id}: sequence step has no sub-actions")
        else:
            for i, sub in enumerate(subs):
                kind = (sub or {}).get("kind")
                if kind not in VALID_SEQUENCE_KINDS:
                    report.errors.append(
                        f"{step.id}: sub-action {i} has unknown kind {kind!r} "
                        f"(valid: {sorted(VALID_SEQUENCE_KINDS)})"
                    )

    elif step.kind == StepKind.LOOP:
        body = d.get("body") or []
        if not body:
            report.errors.append(f"{step.id}: loop has an empty body")
        for sid in body:
            if sid not in known_ids:
                report.errors.append(f"{step.id}: loop body references unknown step {sid!r}")
        over = d.get("over")
        if not over:
            report.errors.append(f"{step.id}: loop has no 'over' (use '__drain__' to drain a list)")
        elif over != "__drain__" and not str(over).startswith("${"):
            report.warnings.append(
                f"{step.id}: loop 'over'={over!r} is neither '__drain__' nor a ${{variable}}"
            )

    elif step.kind == StepKind.DECISION:
        branches = list(d.get("if_true") or []) + list(d.get("if_false") or [])
        if not branches:
            report.errors.append(f"{step.id}: decision has no if_true/if_false branches")
        for sid in branches:
            if sid not in known_ids:
                report.errors.append(f"{step.id}: decision references unknown step {sid!r}")
        if not d.get("condition"):
            report.errors.append(f"{step.id}: decision has no 'condition'")


def _check_url(step_id: str, url: str, report: ValidationReport) -> None:
    """Only http(s) URLs are navigable; file:// and javascript: are never valid."""
    if url.startswith("${"):
        return  # fully interpolated at runtime; the executor guards the value
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in _ALLOWED_URL_SCHEMES:
        report.errors.append(
            f"{step_id}: navigate URL scheme {parsed.scheme!r} is not allowed "
            f"(only http/https)"
        )
    elif not parsed.scheme and not url.startswith("/"):
        report.warnings.append(f"{step_id}: navigate URL {url!r} has no scheme")


def _check_no_manual_login(step: Step, report: ValidationReport) -> None:
    """Authentication runs through open_app + the frontdoor, never as plan steps.

    A plan that navigates to a login page or types a password would put
    credentials inside the sandbox — exactly what the architecture prevents.
    """
    d = step.details or {}
    url = str(d.get("url", ""))
    if url and any(marker in url.lower() for marker in _LOGIN_URL_MARKERS):
        report.errors.append(
            f"{step.id}: navigates to a login page ({url!r}). Authentication is "
            f"handled by the open_app frontdoor — remove this step."
        )

    intent = str(d.get("intent", ""))
    if intent.strip().lower() == "open_app":
        return  # the sanctioned auth path
    haystack = " ".join(str(d.get(k, "")) for k in ("intent", "target_description", "value"))
    if _CREDENTIAL_WORDS.search(haystack):
        report.warnings.append(
            f"{step.id}: mentions credentials ({haystack[:80]!r}). The agent never "
            f"logs in manually — confirm this is not a login step."
        )


def _check_success_condition(step: Step, report: ValidationReport) -> None:
    """State-changing steps without a success_condition lose idempotency.

    Without one the executor cannot skip an already-done step or verify the
    result, so a re-run duplicates work. Warn rather than block: the drain
    sentinel step is legitimately condition-free.
    """
    if step.kind in STATE_CHANGING_KINDS and not step.success_condition:
        report.warnings.append(
            f"{step.id}: state-changing {step.kind.value} step has no "
            f"success_condition — it cannot self-skip or self-verify"
        )


def _check_control_flow_reachability(plan: Plan, report: ValidationReport) -> None:
    """Every step must be reachable: top-level, or owned by a loop/decision.

    The executor skips steps claimed by a control-flow parent during its
    linear walk. A step claimed by nobody still runs; a step claimed by two
    parents runs twice — both are generation bugs worth surfacing.
    """
    owners: dict[str, list[str]] = {}
    for step in plan.steps:
        d = step.details or {}
        children: list[str] = []
        if step.kind == StepKind.LOOP:
            children = list(d.get("body") or [])
        elif step.kind == StepKind.DECISION:
            children = list(d.get("if_true") or []) + list(d.get("if_false") or [])
        for child in children:
            owners.setdefault(child, []).append(step.id)

    for child, parents in owners.items():
        if len(parents) > 1:
            report.warnings.append(
                f"{child}: claimed by multiple control-flow parents {parents} — "
                f"it will execute more than once"
            )
