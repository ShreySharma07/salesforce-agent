"""
Browser execution mode — ReAct loop (Phase 2b).

Executes one Plan step as a goal, not a fixed instruction. Each iteration:
  1. OBSERVE — wait for the page to stabilize, capture screenshot + DOM grounding
  2. REASON  — the LLM sees the goal, current page, AND the full trajectory
               of past (thought, action, observation), then chooses one action
  3. ACT     — execute the action via Playwright
  4. record the iteration into the trace
Repeat until the step's goal is met, the agent gives up, a captcha is hit,
or the iteration / wall-time budget is exhausted.

The full trajectory is fed back each turn so the agent doesn't repeat a
failed approach. To keep token cost bounded, only the CURRENT turn sends a
live screenshot; past turns contribute their text observation + a ref.
"""
from __future__ import annotations

import json
import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from sandbox_agent.grounding import extract_from_page, render_for_prompt
from sandbox_agent.llm_client import GeminiClient
from sandbox_agent.schemas import LoopIteration


SYSTEM_PROMPT = """You are a browser automation agent running a Reason-Act-Observe loop. You are given ONE goal and must accomplish it on a live web page by choosing one action at a time.

Each turn you receive:
  - GOAL: what this step must accomplish
  - URL / TITLE: where you are now
  - ELEMENTS: numbered interactive elements currently on the page
  - SCREENSHOT: the current viewport
  - TRAJECTORY: every previous (thought, action, observation) this step — study it so you do not repeat a failed approach

Output exactly one JSON object — no prose, no code fences. Keep "thought" to ONE short sentence (the JSON must always be complete and valid; a long thought that gets truncated is useless):
  {"thought": "<one short sentence referencing the trajectory>", "action": "<one of below>", ...args}

Actions:
  {"thought": "...", "action": "click", "ref": <int>}
  {"thought": "...", "action": "fill", "ref": <int>, "text": "<value>"}
  {"thought": "...", "action": "press", "key": "Enter"}
  {"thought": "...", "action": "navigate", "url": "https://..."}
  {"thought": "...", "action": "open_app", "provider": "salesforce"}      # enter a connected app (e.g. Salesforce) already logged in
  {"thought": "...", "action": "scroll", "direction": "down|up|left|right"}
  {"thought": "...", "action": "wait", "seconds": <int>}
  {"thought": "...", "action": "dismiss_obstruction", "ref": <int>}   # close a popup/modal/cookie banner blocking the goal
  {"thought": "...", "action": "captcha_detected"}                    # a CAPTCHA or bot-check is blocking progress
  {"thought": "...", "action": "done", "evidence": "<observation proving the goal is met>"}
  {"thought": "...", "action": "give_up", "reason": "<why the goal cannot be completed>"}

Rules:
  - Refer to elements only by the # ref shown in ELEMENTS. Never invent a ref.
  - Use `dismiss_obstruction` when a modal, popup, overlay, toast, or cookie banner is in the way — pick the ref of its close/accept control. This action escalates automatically: it tries a normal click, then a FORCE click that bypasses an intercepting overlay, then hides the element as a last resort. So if a toast or banner keeps blocking clicks (e.g. "intercepts pointer events"), use `dismiss_obstruction` on its close button rather than repeatedly trying to click through it.
  - Use `captcha_detected` if you see a CAPTCHA, "verify you are human", or similar bot-check. Do NOT try to solve it.
  - Use `open_app` when the task requires a connected app like Salesforce. This lands you ALREADY LOGGED IN on the app's home page. Do NOT navigate to the app's login page or try to log in yourself — emit `open_app` with the provider name and you will arrive authenticated. After that, navigate the app's UI normally.
  - Emit `done` only when the GOAL is clearly accomplished, citing concrete on-page evidence.
  - Emit `give_up` only after the trajectory shows you genuinely cannot proceed (needed element absent, repeated failures).
  - If a previous action in the TRAJECTORY did not work, try a DIFFERENT approach — never repeat the same failed action.
  - Your ENTIRE response must be a single COMPLETE JSON object. Keep "thought" to one sentence so the JSON is never truncated. Never write long prose.
"""


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

def parse_action(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(t[s : e + 1])
            except json.JSONDecodeError:
                return None
        return None


# ---------------------------------------------------------------------------
# Wait-for-stable — the single biggest reliability lever
# ---------------------------------------------------------------------------

def wait_for_stable(page: Page, *, settle_ms: int = 400) -> None:
    """Wait until the page is plausibly done rendering before we observe it.
    Most 'screenshotted mid-render' flake is fixed here, not in reasoning."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=4000)
    except PWTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except PWTimeoutError:
        # networkidle legitimately never fires on pages with long-poll/websockets
        pass
    # Small fixed settle for late layout shifts / animations.
    time.sleep(settle_ms / 1000.0)


# ---------------------------------------------------------------------------
# Trajectory rendering — full history, compact
# ---------------------------------------------------------------------------

def _detect_stuck(trace: list[LoopIteration]) -> str:
    """Look at recent history and, if the agent is spinning, return an
    escalation nudge to inject into the next prompt. Empty string when fine.

    Two stuck patterns:
      A. Same action+ref repeated with an error/failure 2+ times in a row.
      B. 3+ of the last 4 iterations made no progress (errors, parse failures,
         or observations starting with FAILED/CANNOT/WARNING).
    """
    if len(trace) < 2:
        return ""

    def _failed(it: "LoopIteration") -> bool:
        if it.error:
            return True
        obs = (it.observation or "")
        return obs.startswith(("FAILED", "CANNOT", "WARNING")) or "could not parse" in obs

    last = trace[-1]
    prev = trace[-2]

    # Pattern A: identical action+args repeated and not working.
    same_action = (last.action == prev.action and last.action_args == prev.action_args)
    if same_action and _failed(last) and _failed(prev):
        target = ""
        if isinstance(last.action_args, dict) and "ref" in last.action_args:
            target = f" on #{last.action_args.get('ref')}"
        return (
            f"ESCALATION: you have now tried `{last.action}`{target} twice and it "
            f"failed both times. DO NOT repeat it. An element may be covered by an "
            f"overlay/toast intercepting clicks. Try, in order: (1) "
            f"`dismiss_obstruction` with force on the blocking overlay's close "
            f"button, (2) a DIFFERENT element or ref, (3) `navigate` directly to "
            f"the target URL, or (4) `scroll` to reveal a different control. "
            f"`dismiss_obstruction` now uses a force-click and, if that fails, "
            f"hides the blocking element — so prefer it for stubborn toasts/banners."
        )

    # Pattern B: broad lack of progress over the recent window.
    window = trace[-4:]
    if len(window) >= 3:
        failures = sum(1 for it in window if _failed(it))
        if failures >= 3:
            return (
                "ESCALATION: the last several actions have not made progress. Stop "
                "repeating the same approach. If an overlay/toast is blocking the "
                "page, use `dismiss_obstruction` (it now force-clicks and will hide "
                "a stubborn blocker). Otherwise change strategy entirely: navigate "
                "directly to the target, or pick a clearly different element."
            )
    return ""


def _render_trajectory(trace: list[LoopIteration]) -> str:
    """Render every past iteration as compact text. Screenshots are NOT
    re-embedded — only referenced — so prompt growth stays linear in text,
    not in image payloads."""
    if not trace:
        return "(no previous actions — this is the first turn)"
    lines: list[str] = []
    for it in trace:
        lines.append(f"--- iteration {it.iteration} ---")
        lines.append(f"thought: {it.thought}")
        act = it.action
        if it.action_args:
            act += " " + json.dumps(it.action_args, separators=(",", ":"))
        lines.append(f"action: {act}")
        if it.observation:
            lines.append(f"observation: {it.observation}")
        if it.error:
            lines.append(f"error: {it.error}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The ReAct loop
# ---------------------------------------------------------------------------

def execute_step(
    page: Page,
    llm: GeminiClient,
    step_intent: str,
    *,
    memory_hint: str = "",
    max_iterations: int = 12,
    max_seconds: float = 180.0,
    screenshot_dir: str | None = None,
) -> dict[str, Any]:
    """Run a ReAct loop until the step's goal is met or a budget is hit.

    Returns a dict:
      {
        "status": "succeeded" | "stuck" | "failed" | "paused",
        "evidence": str,
        "pause_reason": str | None,
        "trace": list[LoopIteration],
      }
    'stuck' is an internal signal meaning "try computer mode"; the executor
    maps it. 'paused' carries pause_reason (e.g. 'captcha').
    """
    trace: list[LoopIteration] = []
    started = time.monotonic()

    for iteration in range(1, max_iterations + 1):
        if time.monotonic() - started > max_seconds:
            return _result("failed", "per-step wall-time budget exceeded", trace)

        iter_start = time.monotonic()

        # ---------- OBSERVE ----------
        wait_for_stable(page)
        url = page.url
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        elements = extract_from_page(page)
        elements_text = render_for_prompt(elements)
        try:
            screenshot = page.screenshot(type="png")
        except Exception as e:
            # A failed screenshot shouldn't kill the step — record and retry.
            trace.append(LoopIteration(
                iteration=iteration, thought="", action="(observe)",
                observation="", error=f"screenshot failed: {e}",
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            time.sleep(1)
            continue

        screenshot_ref = _maybe_save_screenshot(screenshot, screenshot_dir, iteration)

        # ---------- REASON ----------
        prompt_parts = [f"GOAL: {step_intent}"]
        if memory_hint:
            prompt_parts.append(memory_hint)
        prompt_parts += [
            f"URL: {url}",
            f"TITLE: {title}",
            "ELEMENTS:",
            elements_text,
            "TRAJECTORY (every previous turn this step):",
            _render_trajectory(trace),
        ]
        _stuck_nudge = _detect_stuck(trace)
        if _stuck_nudge:
            prompt_parts.append(_stuck_nudge)
        prompt_parts.append("Emit one action as JSON.")
        prompt = "\n\n".join(prompt_parts)

        try:
            response = llm.generate(
                prompt=prompt, system=SYSTEM_PROMPT,
                images=[screenshot], json_mode=True, max_tokens=2048,
            )
        except Exception as llm_err:
            err_str = str(llm_err)
            # Daily quota exhausted — abort immediately. No retry (pointless),
            # and signal the executor to stop the whole run.
            if type(llm_err).__name__ == "QuotaExhaustedError" or "quota exhausted" in err_str.lower():
                trace.append(LoopIteration(
                    iteration=iteration, action="(reason)",
                    error=f"LLM daily quota exhausted: {llm_err}",
                    screenshot_ref=screenshot_ref,
                    latency_ms=int((time.monotonic() - iter_start) * 1000),
                ))
                return _result("failed", "LLM daily quota exhausted", trace,
                               quota_exhausted=True)
            # Transient Gemini image error — wait, fresh screenshot, one retry.
            if "Unable to process input image" in err_str or "INVALID_ARGUMENT" in err_str:
                time.sleep(2)
                try:
                    screenshot = page.screenshot(type="png")
                    response = llm.generate(
                        prompt=prompt, system=SYSTEM_PROMPT,
                        images=[screenshot], json_mode=True, max_tokens=2048,
                    )
                except Exception as retry_err:
                    trace.append(LoopIteration(
                        iteration=iteration, action="(reason)",
                        error=f"LLM image error after retry: {type(retry_err).__name__}: {retry_err}",
                        screenshot_ref=screenshot_ref,
                        latency_ms=int((time.monotonic() - iter_start) * 1000),
                    ))
                    return _result("failed", "LLM image error after retry", trace)
            else:
                trace.append(LoopIteration(
                    iteration=iteration, action="(reason)",
                    error=f"LLM error: {type(llm_err).__name__}: {llm_err}",
                    screenshot_ref=screenshot_ref,
                    latency_ms=int((time.monotonic() - iter_start) * 1000),
                ))
                return _result("failed", f"LLM error: {llm_err}", trace)

        action = parse_action(response.text)
        in_tok = getattr(response, "input_tokens", 0) or 0
        out_tok = getattr(response, "output_tokens", 0) or 0

        if action is None:
            # One corrective retry: occasionally the model wraps JSON in prose
            # or emits a partial object. Re-ask tersely for JSON only before
            # burning the whole iteration.
            try:
                retry_prompt = (
                    prompt
                    + "\n\nYour previous response could not be parsed as JSON. "
                    "Respond with EXACTLY ONE complete JSON object and nothing "
                    "else — no prose, no markdown, no code fences. Keep "
                    "\"thought\" to one short sentence."
                )
                response = llm.generate(
                    prompt=retry_prompt, system=SYSTEM_PROMPT,
                    images=[screenshot], json_mode=True, max_tokens=2048,
                )
                action = parse_action(response.text)
                in_tok += getattr(response, "input_tokens", 0) or 0
                out_tok += getattr(response, "output_tokens", 0) or 0
            except Exception:
                action = None

        if action is None:
            trace.append(LoopIteration(
                iteration=iteration, action="(unparseable)",
                observation="could not parse model output as JSON (after one retry)",
                error="parse_failure", screenshot_ref=screenshot_ref,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            continue

        kind = action.get("action", "")
        thought = action.get("thought", "")
        action_args = {k: v for k, v in action.items() if k not in ("action", "thought")}

        # ---------- terminal actions ----------
        if kind == "done":
            trace.append(LoopIteration(
                iteration=iteration, thought=thought, action="done",
                action_args=action_args, observation=action.get("evidence", ""),
                screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            return _result("succeeded", action.get("evidence", ""), trace)

        if kind == "give_up":
            reason = action.get("reason", "agent gave up")
            trace.append(LoopIteration(
                iteration=iteration, thought=thought, action="give_up",
                action_args=action_args, observation=reason,
                screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            # 'stuck' so the executor can try computer mode as a fallback.
            return _result("stuck", reason, trace)

        if kind == "captcha_detected":
            trace.append(LoopIteration(
                iteration=iteration, thought=thought, action="captcha_detected",
                action_args=action_args,
                observation="CAPTCHA / bot-check detected — handing off to human",
                screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            return _result("paused", "captcha detected", trace, pause_reason="captcha")

        # ---------- ACT ----------
        try:
            observation = _execute_action(page, kind, action)
            err = None
        except Exception as e:
            observation = ""
            err = f"{type(e).__name__}: {e}"

        trace.append(LoopIteration(
            iteration=iteration, thought=thought, action=kind,
            action_args=action_args, observation=observation, error=err,
            screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=int((time.monotonic() - iter_start) * 1000),
        ))

    return _result("failed", f"max iterations ({max_iterations}) exhausted", trace)


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _execute_action(page: Page, kind: str, action: dict) -> str:
    """Execute one action, return a compact text observation describing
    what happened (fed back into the next turn's trajectory)."""
    if kind == "click":
        ref = int(action["ref"])
        page.locator(f'[data-agent-ref="{ref}"]').first.click(timeout=5000)
        return f"clicked element #{ref}"

    if kind == "fill":
        ref = int(action["ref"])
        text = str(action.get("text", ""))
        page.locator(f'[data-agent-ref="{ref}"]').first.fill(text, timeout=5000)
        return f"filled element #{ref} with {len(text)} chars"

    if kind == "press":
        key = str(action.get("key", ""))
        page.keyboard.press(key)
        return f"pressed key {key}"

    if kind == "navigate":
        url = str(action["url"])
        page.goto(url)
        return f"navigated to {url}"

    if kind == "open_app":
        # Enter a connected app already logged in. The backend injected a
        # frontdoor path per provider; navigating to it 302-redirects into a
        # logged-in session. The sandbox never sees the underlying token.
        provider = str(action.get("provider", "salesforce")).lower()
        import os
        backend_base = os.getenv("BACKEND_MCP_URL", "").rstrip("/")
        # Provider-specific path, injected by the backend at spawn, e.g.
        # SALESFORCE_FRONTDOOR_PATH = "/sandbox/frontdoor/salesforce?run_token=..."
        path = os.getenv(f"{provider.upper()}_FRONTDOOR_PATH", "")
        if not backend_base or not path:
            return (f"cannot open app '{provider}': not connected or no "
                    f"frontdoor path available for this run")
        page.goto(backend_base + path, wait_until="domcontentloaded")
        return f"opened {provider}, logged in"

    if kind == "scroll":
        direction = action.get("direction", "down")
        ref = action.get("ref") 
        
        # 1. Targeted Scroll
        if ref is not None:
            try:
                # Cast to int to handle strict string/int JSON type hallucinations
                locator = page.locator(f'[data-agent-ref="{int(ref)}"]').first
                locator.scroll_into_view_if_needed(timeout=2000)
                # Sleep needed here too for the same visual stabilization reasons
                time.sleep(0.5) 
                return f"scrolled element #{ref} into view"
            except Exception as e:
                return f"failed to scroll element #{ref}: {e}"

        # 2. Global Exploratory Scroll
        amount = 700 
        script = ""
        if direction == "down":
            script = f"window.scrollBy(0, {amount});"
        elif direction == "up":
            script = f"window.scrollBy(0, -{amount});"
        elif direction == "right":
            script = f"window.scrollBy({amount}, 0);"
        elif direction == "left":
            script = f"window.scrollBy(-{amount}, 0);"
        
        page.evaluate(script)
        
        # CRITICAL: Wait for the DOM and any lazy-loaded images to settle 
        time.sleep(0.5) 
        return f"scrolled global window {direction}"

    if kind == "wait":
        secs = min(float(action.get("seconds", 1)), 10)
        time.sleep(secs)
        return f"waited {secs}s"

    if kind == "dismiss_obstruction":
        ref = int(action["ref"])
        loc = page.locator(f'[data-agent-ref="{ref}"]').first
        # Rung 1: a normal click on the close/accept control.
        try:
            loc.click(timeout=3000)
            return f"dismissed obstruction via element #{ref}"
        except Exception:
            pass
        # Rung 2: FORCE click — bypasses the actionability/pointer-intercept
        # check, so a transparent overlay sitting on top no longer blocks the
        # close button. This is the right override for Salesforce toasts whose
        # close 'X' is covered by an intercepting layer.
        try:
            loc.click(force=True, timeout=3000)
            return f"dismissed obstruction via element #{ref} (forced click)"
        except Exception:
            pass
        # Rung 3 (LAST RESORT): hide the element. We HIDE (visibility:hidden +
        # pointer-events:none), never remove() — hiding is reversible and far
        # less likely to break Lightning's reactive framework than detaching a
        # node it owns. This unblocks the page when the close button itself is
        # unreachable.
        try:
            hidden = page.evaluate(
                """(ref) => {
                    const el = document.querySelector('[data-agent-ref="' + ref + '"]');
                    if (!el) return false;
                    // Hide the element and its nearest toast/dialog container,
                    // so an overlay wrapper stops intercepting pointer events.
                    const targets = [el];
                    let p = el;
                    for (let i = 0; i < 4 && p; i++) {
                        p = p.parentElement;
                        if (p && (
                            /toast|modal|dialog|overlay|popup|banner/i.test(p.className || '') ||
                            p.getAttribute && (p.getAttribute('role') === 'dialog' ||
                                               p.getAttribute('role') === 'alert')
                        )) { targets.push(p); break; }
                    }
                    for (const t of targets) {
                        t.style.setProperty('visibility', 'hidden', 'important');
                        t.style.setProperty('pointer-events', 'none', 'important');
                    }
                    return true;
                }""",
                ref,
            )
            if hidden:
                return (f"dismissed obstruction #{ref} by hiding it (close button was "
                        f"unreachable; element hidden so it no longer blocks the page)")
            return f"FAILED to dismiss #{ref}: element not found to hide."
        except Exception as e:
            return f"FAILED to dismiss #{ref}: {type(e).__name__}: {e}"

    return f"unknown action: {kind}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _maybe_save_screenshot(png: bytes, screenshot_dir: str | None, iteration: int) -> str | None:
    if not screenshot_dir:
        return None
    import os
    try:
        os.makedirs(screenshot_dir, exist_ok=True)
        path = os.path.join(screenshot_dir, f"iter_{iteration:03d}.png")
        with open(path, "wb") as f:
            f.write(png)
        return path
    except Exception:
        return None


def _result(
    status: str,
    evidence: str,
    trace: list[LoopIteration],
    *,
    pause_reason: str | None = None,
    quota_exhausted: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "evidence": evidence,
        "pause_reason": pause_reason,
        "trace": trace,
        "quota_exhausted": quota_exhausted,
    }