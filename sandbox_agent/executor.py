"""
Plan executor. Walks a Plan top-to-bottom and dispatches each step.

Phase 2b:
  - UI steps (ui_action / extract / decision / loop) run a ReAct loop in
    browser_mode and the full trajectory is captured into StepResult.trace
  - mcp_call steps keep their direct-dispatch path — no loop, no LLM cost
  - per-step iteration + wall-time budgets are passed through from RunRequest
  - browser-mode 'stuck' still falls back to computer mode
  - browser-mode 'paused' (e.g. captcha) surfaces as a paused StepResult
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from playwright.sync_api import Page, sync_playwright

from sandbox_agent import browser_mode, computer_mode
from sandbox_agent.llm_client import GeminiClient
from sandbox_agent.schemas import (
    ExecutionMode,
    LoopIteration,
    RunRequest,
    RunResponse,
    Step,
    StepKind,
    StepResult,
)


# ---------------------------------------------------------------------------
# Mode auto-routing
# ---------------------------------------------------------------------------

_DESKTOP_KEYWORDS = {
    "notes app", "open application", "desktop", "spreadsheet",
    "libreoffice", "excel", "terminal", "slack desktop", "finder",
    "file manager", "vs code", "preview", "open file",
}


def _route_step(step: Step) -> ExecutionMode:
    explicit = step.details.get("execution_mode")
    if explicit:
        try:
            return ExecutionMode(explicit)
        except ValueError:
            pass

    if step.kind == StepKind.NAVIGATE:
        return ExecutionMode.BROWSER

    haystack = (
        step.description.lower()
        + " "
        + str(step.details.get("intent", "")).lower()
        + " "
        + str(step.details.get("target_description", "")).lower()
    )
    if any(kw in haystack for kw in _DESKTOP_KEYWORDS):
        return ExecutionMode.COMPUTER

    return ExecutionMode.BROWSER


def _interpolate(text: str, variables: dict[str, Any]) -> str:
    """Substitute ${var} and ${var.key} tokens from the variables dict."""
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        parts = key.split(".", 1)
        val = variables.get(parts[0], m.group(0))
        if len(parts) == 2 and isinstance(val, dict):
            val = val.get(parts[1], m.group(0))
        return str(val)
    return re.sub(r"\$\{([^}]+)\}", _sub, text)


# Salesforce record IDs are exactly 15 or 18 alphanumeric chars with no spaces.
# Anything longer or containing whitespace is prose (a sentence from an extract
# step), not an ID — navigating to such a URL would produce a garbage page and
# an infinite recovery loop.
_MAX_URL_TOKEN_LEN = 30


def _interpolate_for_url(url: str, variables: dict[str, Any]) -> tuple[str, str | None]:
    """Substitute ${var} tokens for use inside a URL, with safety checks.

    Returns (interpolated_url, error_or_None).  error is set when any
    substituted value contains whitespace or exceeds _MAX_URL_TOKEN_LEN chars,
    indicating the variable holds a prose sentence rather than a bare ID/token.
    The caller should treat a non-None error as a step failure and re-evaluate
    instead of navigating to the garbage URL.
    """
    bad: list[str] = []

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        parts = key.split(".", 1)
        val = variables.get(parts[0], m.group(0))
        if len(parts) == 2 and isinstance(val, dict):
            val = val.get(parts[1], m.group(0))
        val_str = str(val)
        # Only flag values that were actually substituted (not left as ${…})
        if val_str != m.group(0) and (" " in val_str or len(val_str) > _MAX_URL_TOKEN_LEN):
            bad.append(
                f"${{{key}}} resolved to {val_str!r} "
                f"(contains whitespace or length {len(val_str)} > {_MAX_URL_TOKEN_LEN} — "
                f"looks like a sentence, not a record ID)"
            )
        return val_str

    result = re.sub(r"\$\{([^}]+)\}", _sub, url)
    return result, ("; ".join(bad) if bad else None)


def _step_intent(step: Step, variables: dict[str, Any] | None = None) -> str:
    base = step.description
    intent = step.details.get("intent")
    target = step.details.get("target_description")
    value = step.details.get("value")
    parts = [base]
    if intent and intent.lower() not in base.lower():
        parts.append(f"Intent: {intent}")
    if target:
        parts.append(f"Target: {target}")
    if value:
        parts.append(f"Value: {value}")
    raw = ". ".join(parts)
    return _interpolate(raw, variables) if variables else raw


# ---------------------------------------------------------------------------
# Control-flow helpers (DECISION / LOOP)
# ---------------------------------------------------------------------------

def _evaluate_condition(condition: str, variables: dict[str, Any]) -> bool:
    """Evaluate a plan condition string against collected variables.
    Defaults to True on any error so the run isn't silently aborted."""
    if not condition:
        return True
    try:
        return bool(eval(condition, {"__builtins__": {}}, dict(variables)))  # noqa: S307
    except Exception:
        return True


def _resolve_loop_items(expr: str, variables: dict[str, Any]) -> list:
    """Resolve a ${var} expression or literal list to a Python list."""
    if isinstance(expr, list):
        return expr
    if isinstance(expr, str) and expr.startswith("${") and expr.endswith("}"):
        key = expr[2:-1]
        val = variables.get(key, [])
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            import json
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                return [line for line in val.splitlines() if line.strip()]
        return [val] if val else []
    return []


def _run_decision(
    step: Step,
    step_by_id: dict[str, Step],
    page: "Page",
    llm: "GeminiClient",
    req: "RunRequest",
    variables: dict[str, Any],
) -> list[StepResult]:
    condition = step.details.get("condition", "")
    if_true = step.details.get("if_true", [])
    if_false = step.details.get("if_false", [])

    branch_taken = _evaluate_condition(condition, variables)
    branch_ids: list[str] = if_true if branch_taken else if_false
    branch_label = "true" if branch_taken else "false"

    results: list[StepResult] = [
        StepResult(
            step_id=step.id,
            status="succeeded",
            detail=f"condition '{condition}' → {branch_label}; executing {branch_ids}",
        )
    ]
    for sid in branch_ids:
        branch_step = step_by_id.get(sid)
        if branch_step is None:
            continue
        result = _run_step(page, llm, branch_step, req, variables=variables)
        results.append(result)
        variables.update(result.extracted)
        if result.status in ("failed", "paused"):
            break
    return results


def _run_loop(
    step: Step,
    step_by_id: dict[str, Step],
    page: "Page",
    llm: "GeminiClient",
    req: "RunRequest",
    variables: dict[str, Any],
) -> list[StepResult]:
    over_expr = step.details.get("over", "")
    item_var = step.details.get("item_variable", "item")
    body_ids: list[str] = step.details.get("body", [])
    max_iters = int(step.details.get("max_iterations", 50))

    is_drain = (over_expr == "__drain__")

    if not is_drain:
        items = _resolve_loop_items(over_expr, variables)
        if not items:
            return [StepResult(
                step_id=step.id, status="succeeded",
                detail=f"loop over '{over_expr}' — 0 items, nothing executed",
            )]
    else:
        items = []  # not used in drain mode

    results: list[StepResult] = []
    loop_range = range(max_iters) if is_drain else range(min(len(items), max_iters))

    for loop_idx in loop_range:
        if is_drain:
            variables["drain_iteration"] = loop_idx
        else:
            variables[item_var] = items[loop_idx]
            variables[f"{item_var}_index"] = loop_idx

        abort_loop = False
        for body_idx, sid in enumerate(body_ids):
            body_step = step_by_id.get(sid)
            if body_step is None:
                continue

            # Dispatch nested DECISION/LOOP steps so they are not treated as UI steps.
            if body_step.kind == StepKind.DECISION:
                nested = _run_decision(body_step, step_by_id, page, llm, req, variables)
                for r in nested:
                    results.append(r)
                    if r.status in ("failed", "paused"):
                        return results
                continue

            if body_step.kind == StepKind.LOOP:
                nested = _run_loop(body_step, step_by_id, page, llm, req, variables)
                for r in nested:
                    results.append(r)
                    if r.status in ("failed", "paused"):
                        return results
                continue

            result = _run_step(page, llm, body_step, req, variables=variables)

            # Drain sentinel: first body step failing means the list is now empty.
            # Return a succeeded loop result so the run continues past the loop.
            if is_drain and body_idx == 0 and result.status == "failed":
                return results + [StepResult(
                    step_id=step.id,
                    status="succeeded",
                    detail=f"drain loop complete after {loop_idx} iteration(s): no more rows in list",
                )]

            results.append(result)
            variables.update(result.extracted)

            on_fail = (body_step.on_failure or "pause").lower()
            if result.status == "paused":
                return results  # always propagate paused
            if result.status == "failed":
                if on_fail == "continue":
                    continue  # step says continue despite failure
                abort_loop = True
                break

        if abort_loop:
            return results

    return results


# ---------------------------------------------------------------------------
# Top-level run loop
# ---------------------------------------------------------------------------

def run_plan(req: RunRequest) -> RunResponse:
    started = time.monotonic()
    llm = GeminiClient()
    step_results: list[StepResult] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            viewport={
                "width": int(os.getenv("SCREEN_WIDTH", "1440")),
                "height": int(os.getenv("SCREEN_HEIGHT", "900")),
            },
        )
        page = context.new_page()

        if req.initial_url:
            try:
                page.goto(req.initial_url)
            except Exception as e:
                return RunResponse(
                    status="failed",
                    error=f"initial navigation failed: {e}",
                    elapsed_seconds=round(time.monotonic() - started, 2),
                )

        try:
            step_by_id = {s.id: s for s in req.plan.steps}
            # Steps that are owned by a DECISION/LOOP parent are skipped in the
            # linear walk — the parent dispatches them at the right time.
            child_ids: set[str] = set()
            for s in req.plan.steps:
                if s.kind == StepKind.DECISION:
                    child_ids.update(s.details.get("if_true", []))
                    child_ids.update(s.details.get("if_false", []))
                elif s.kind == StepKind.LOOP:
                    child_ids.update(s.details.get("body", []))
            variables: dict[str, Any] = {}
            step_count = 0

            for step in req.plan.steps:
                if step.id in child_ids:
                    continue  # handled by its DECISION/LOOP parent
                if step_count >= req.max_steps:
                    return _finish("aborted", step_results, page, "max_steps reached", started)
                if time.monotonic() - started > req.max_seconds:
                    return _finish("aborted", step_results, page, "max_seconds reached", started)

                if step.kind == StepKind.DECISION:
                    new_results = _run_decision(step, step_by_id, page, llm, req, variables)
                elif step.kind == StepKind.LOOP:
                    new_results = _run_loop(step, step_by_id, page, llm, req, variables)
                else:
                    result = _run_step(page, llm, step, req, variables=variables)
                    variables.update(result.extracted)
                    new_results = [result]

                for result in new_results:
                    step_results.append(result)
                    step_count += 1

                    if getattr(result, "quota_exhausted", False):
                        return _finish("aborted", step_results, page,
                                       f"run aborted at step {result.step_id}: LLM daily quota exhausted",
                                       started)
                    if result.status == "paused":
                        return _finish("paused", step_results, page,
                                       f"paused at step {result.step_id}: {result.pause_reason or ''}",
                                       started)
                    if result.status == "failed":
                        src = step_by_id.get(result.step_id, step)
                        policy = (src.on_failure or "pause").lower()
                        if policy == "abort":
                            return _finish("failed", step_results, page,
                                           f"step {result.step_id} failed (abort): {result.detail}",
                                           started)
                        if policy == "pause":
                            return _finish("paused", step_results, page,
                                           f"step {result.step_id} failed (pausing for review): {result.detail}",
                                           started)
                        # policy == "continue": log and proceed

            return _finish("completed", step_results, page, None, started)

        finally:
            try:
                browser.close()
            except Exception:
                pass


def _browser_context_for_computer_mode(
    trace: list[LoopIteration], give_up_reason: str
) -> str:
    """Summarise what browser mode already tried so computer mode doesn't repeat it."""
    parts: list[str] = []
    if give_up_reason:
        parts.append(f"Browser mode gave up: {give_up_reason}")

    failed = [
        it for it in trace
        if it.action not in {"done", "give_up", "(observe)", "(reason)", "(unparseable)"}
        and (it.error or (it.observation or "").startswith(
            ("FAILED", "CANNOT", "WARNING", "BLOCKED", "ESCALATION")
        ))
    ][-3:]

    if failed:
        parts.append("Last failed browser attempts (do NOT repeat these):")
        for it in failed:
            act = it.action
            if it.action_args:
                act += " " + json.dumps(it.action_args, separators=(",", ":"))
            outcome = (it.error or it.observation or "")[:100]
            parts.append(f"  - {act}: {outcome}")

    return "\n".join(parts)


def _run_step(page: Page, llm: GeminiClient, step: Step, req: RunRequest, *, variables: dict[str, Any] | None = None) -> StepResult:
    """Execute a single step."""
    # ---- Non-UI step kinds ----
    if step.kind == StepKind.WAIT:
        secs = float(step.details.get("seconds", 1))
        time.sleep(min(secs, 30))
        return StepResult(step_id=step.id, status="succeeded", detail=f"waited {secs}s")

    if step.kind == StepKind.HUMAN_INPUT:
        return StepResult(step_id=step.id, status="paused",
                          detail=str(step.details.get("prompt", "human input requested")),
                          pause_reason="human_input")

    if step.kind == StepKind.MCP_CALL:
        # Direct dispatch — no ReAct loop, no LLM cost. Phase 2a.1 path, unchanged.
        import traceback
        server = step.details.get("server", "")
        tool = step.details.get("tool", "")
        args = step.details.get("args") or {}
        variable_name = step.details.get("variable_name") or "result"
        try:
            from sandbox_agent.mcp_client import MCPClient
            result = MCPClient().call(server, tool, args)
            return StepResult(
                step_id=step.id,
                status="succeeded",
                extracted={variable_name: result},
            )
        except Exception as e:
            return StepResult(
                step_id=step.id,
                status="failed",
                detail=f"MCP {server}/{tool} failed: {type(e).__name__}: {e}\n{traceback.format_exc()}",
                extracted={},
            )

    if step.kind == StepKind.NOTIFY:
        return StepResult(step_id=step.id, status="succeeded",
                          detail=f"(notify simulated): {step.details.get('message', '')}")

    if step.kind == StepKind.NAVIGATE:
        url = step.details.get("url")
        if not url:
            return StepResult(step_id=step.id, status="failed",
                              detail="navigate step missing url")
        # Substitute ${variable} tokens, but guard against prose values that
        # would produce a garbage URL (e.g. an extract that returned a sentence
        # instead of a bare 18-char Salesforce record ID).
        if variables:
            url, url_err = _interpolate_for_url(url, variables)
            if url_err:
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    detail=(
                        f"navigate aborted — interpolated variable is not a valid "
                        f"record ID/token: {url_err}. "
                        f"Re-evaluate the plan: use a ui_action click on the target "
                        f"row instead of constructing a URL from extracted text."
                    ),
                )
        try:
            page.goto(url)
            return StepResult(step_id=step.id, status="succeeded",
                              detail=f"navigated to {url}")
        except Exception as e:
            return StepResult(step_id=step.id, status="failed",
                              detail=f"navigate failed: {e}")

    # ---- UI / extract — ReAct loop ----
    intent = _step_intent(step, variables)
    mode = _route_step(step)
    trace: list[LoopIteration] = []

    if mode == ExecutionMode.BROWSER:
        outcome = browser_mode.execute_step(
            page, llm, intent,
            memory_hint=req.memory_hints.get(step.id, ""),
            max_iterations=req.max_iterations_per_step,
            max_seconds=req.max_seconds_per_step,
        )
        trace = outcome.get("trace", [])
        if outcome["status"] == "stuck":
            # Fallback: try computer mode, passing a summary of what browser
            # mode already tried so it doesn't repeat the same failed actions.
            browser_ctx = _browser_context_for_computer_mode(
                trace, outcome.get("evidence", "")
            )
            cm_outcome = computer_mode.execute_step(llm, intent, browser_context=browser_ctx)
            # Keep the browser trace so the give-up reasoning is still visible.
            outcome = cm_outcome
    else:
        outcome = computer_mode.execute_step(llm, intent)

    status_map = {
        "succeeded": "succeeded",
        "stuck": "failed",       # both modes exhausted
        "failed": "failed",
        "paused": "paused",
    }
    status = status_map.get(outcome["status"], "failed")

    extracted: dict[str, Any] = {}
    if step.kind == StepKind.EXTRACT and status == "succeeded":
        var_name = step.details.get("variable_name", "value")
        extracted = {var_name: outcome.get("evidence", "")}

    return StepResult(
        step_id=step.id,
        status=status,  # type: ignore[arg-type]
        detail=outcome.get("evidence", ""),
        extracted=extracted,
        trace=trace,
        pause_reason=outcome.get("pause_reason"),
        quota_exhausted=outcome.get("quota_exhausted", False),
    )


def _finish(
    status: str,
    step_results: list[StepResult],
    page: Page | None,
    error: str | None,
    started: float,
) -> RunResponse:
    final_url = None
    try:
        if page is not None:
            final_url = page.url
    except Exception:
        pass
    return RunResponse(
        status=status,  # type: ignore[arg-type]
        step_results=step_results,
        final_url=final_url,
        error=error,
        elapsed_seconds=round(time.monotonic() - started, 2),
    )