"""
Plan executor. Walks a Plan top-to-bottom and dispatches each step to
either browser_mode or computer_mode based on:
  - Explicit details.execution_mode (if the plan author set one)
  - Auto-routing rules (default)
  - Fallback: if browser mode says 'stuck', retry the same step in computer mode

This is the single orchestration point for executing plans inside the sandbox.
"""
from __future__ import annotations

import os
import time
from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright

from sandbox_agent import browser_mode, computer_mode
from sandbox_agent.llm_client import GeminiClient
from sandbox_agent.schemas import (
    ExecutionMode,
    Plan,
    RunRequest,
    RunResponse,
    Step,
    StepKind,
    StepResult,
)


# ---------------------------------------------------------------------------
# Mode auto-routing
# ---------------------------------------------------------------------------

# Keywords in step description / intent that strongly suggest a desktop
# (non-browser) operation. When matched, default to computer mode.
_DESKTOP_KEYWORDS = {
    "notes app", "open application", "desktop", "spreadsheet",
    "libreoffice", "excel", "terminal", "slack desktop", "finder",
    "file manager", "vs code", "preview", "open file",
}


def _route_step(step: Step) -> ExecutionMode:
    """Decide which mode to use for this step. Plan-author overrides win."""
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


# ---------------------------------------------------------------------------
# Step intent — what we hand to the per-step executor
# ---------------------------------------------------------------------------

def _step_intent(step: Step) -> str:
    """Boil a Plan step down to one or two sentences for the per-step
    executor. The executor is single-purpose and shouldn't have to
    figure out the schema."""
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
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# Top-level run loop
# ---------------------------------------------------------------------------

def run_plan(req: RunRequest) -> RunResponse:
    """Execute the plan against a fresh Chromium + the existing Xvfb desktop.
    Returns a RunResponse summarizing per-step outcomes."""
    started = time.monotonic()
    llm = GeminiClient()
    step_results: list[StepResult] = []

    # Launch Chromium ON the Xvfb display so noVNC viewers see it.
    # We use sync_playwright directly so the agent can switch modes mid-plan.
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
            for i, step in enumerate(req.plan.steps):
                if i >= req.max_steps:
                    return _finish("aborted", step_results, page, "max_steps reached", started)
                if time.monotonic() - started > req.max_seconds:
                    return _finish("aborted", step_results, page, "max_seconds reached", started)

                result = _run_step(page, llm, step)
                step_results.append(result)

                if result.status == "failed" and step.on_failure == "abort":
                    return _finish("failed", step_results, page,
                                   f"step {step.id} failed: {result.detail}", started)

                if result.status == "paused":
                    return _finish("paused", step_results, page,
                                   f"paused at step {step.id}", started)

            return _finish("completed", step_results, page, None, started)

        finally:
            try:
                browser.close()
            except Exception:
                pass


def _run_step(page: Page, llm: GeminiClient, step: Step) -> StepResult:
    """Execute a single step. Tries the routed mode first; if browser mode
    says 'stuck' (element not in DOM), retries in computer mode."""
    # ---- Non-UI step kinds first ----
    if step.kind == StepKind.WAIT:
        secs = float(step.details.get("seconds", 1))
        time.sleep(min(secs, 30))
        return StepResult(step_id=step.id, status="succeeded",
                          detail=f"waited {secs}s")

    if step.kind == StepKind.HUMAN_INPUT:
        # Phase 1 doesn't have a UX channel back to the user yet — we just
        # pause and let the orchestrator pick this up. When the FastAPI/UX
        # layer is wired, we'll send the prompt out and await a response.
        return StepResult(step_id=step.id, status="paused",
                          detail=str(step.details.get("prompt", "human input requested")))

    # if step.kind == StepKind.MCP_CALL:
    #     import asyncio
    #     from sandbox_agent.mcp_client import MCPClient, MCPClientError
    #     server = step.details.get("server", "")
    #     tool = step.details.get("tool", "")
    #     args = step.details.get("args") or {}
    #     variable_name = step.details.get("variable_name") or "result"
    #     try:
    #         result = asyncio.run(MCPClient().call(server, tool, args))
    #         return StepResult(step_id=step.id, status="succeeded",
    #                           extracted={variable_name: result})
    #     except (MCPClientError, Exception) as e:
    #         return StepResult(step_id=step.id, status="failed",
    #                           error=str(e), extracted={})

    if step.kind == StepKind.MCP_CALL:
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
        try:
            page.goto(url)
            return StepResult(step_id=step.id, status="succeeded",
                              detail=f"navigated to {url}")
        except Exception as e:
            return StepResult(step_id=step.id, status="failed",
                              detail=f"navigate failed: {e}")

    # ---- UI / extract / decision / loop  go through per-step LLM loop ----
    intent = _step_intent(step)
    mode = _route_step(step)

    if mode == ExecutionMode.BROWSER:
        outcome = browser_mode.execute_step(page, llm, intent)
        if outcome["status"] == "stuck":
            # Fallback: try computer mode for the same step
            outcome = computer_mode.execute_step(llm, intent)
    else:
        outcome = computer_mode.execute_step(llm, intent)

    status = {
        "succeeded": "succeeded",
        "stuck": "failed",       # both modes ran out of options
        "failed": "failed",
    }.get(outcome["status"], "failed")

    extracted: dict[str, Any] = {}
    if step.kind == StepKind.EXTRACT and status == "succeeded":
        var_name = step.details.get("variable_name", "value")
        extracted = {var_name: outcome.get("evidence", "")}

    return StepResult(step_id=step.id, status=status, detail=outcome.get("evidence", ""),
                      extracted=extracted)


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
        status=status,                       # type: ignore[arg-type]
        step_results=step_results,
        final_url=final_url,
        error=error,
        elapsed_seconds=round(time.monotonic() - started, 2),
    )