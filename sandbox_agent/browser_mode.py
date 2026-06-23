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

from sandbox_agent.grounding import annotate_screenshot, compress_for_llm, extract_from_page, render_for_prompt
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
  {"thought": "...", "action": "click", "ref": "<ref_id>"}
  {"thought": "...", "action": "click_text", "text": "<visible text>"}
  {"thought": "...", "action": "fill", "ref": "<ref_id>", "text": "<value>"}
  {"thought": "...", "action": "fill_field_by_label", "label": "<visible label>", "text": "<value>"}
  {"thought": "...", "action": "press", "key": "Enter"}
  {"thought": "...", "action": "navigate", "url": "https://..."}
  {"thought": "...", "action": "open_app", "provider": "salesforce"}      # enter a connected app (e.g. Salesforce) already logged in
  {"thought": "...", "action": "scroll", "direction": "down|up|left|right"}
  {"thought": "...", "action": "wait", "seconds": <int>}
  {"thought": "...", "action": "dismiss_obstruction", "ref": "<ref_id>"}   # close a popup/modal/cookie banner blocking the goal
  {"thought": "...", "action": "captcha_detected"}                    # a CAPTCHA or bot-check is blocking progress
  {"thought": "...", "action": "done", "evidence": "<observation proving the goal is met>"}
  {"thought": "...", "action": "give_up", "reason": "<why the goal cannot be completed>"}

Rules:
  - Refer to elements only by the # ref shown in ELEMENTS. Never invent a ref.
  - If you can SEE an element in the screenshot but it has NO #ref in ELEMENTS (e.g. a Salesforce datatable case-number link that renders inside closed shadow DOM), use `click_text` with its exact visible label. Do NOT scroll — the element is already visible and scrolling will not add a ref for it.
  - To fill or select a modal/record form field, use `fill_field_by_label` with the field's visible label text (e.g. label="Due Date", text="6/20/2026"). This crosses shadow DOM for both text inputs and comboboxes/dropdowns. Do NOT use `click_text` on a label — labels are not interactive inputs. Use `fill_field_by_label` for Subject, Due Date, Comments, Status, and all other form fields regardless of whether they have a #ref.
  - Use `dismiss_obstruction` when a modal, popup, overlay, toast, or cookie banner is in the way — pick the ref of its close/accept control. This action escalates automatically: it tries a normal click, then a FORCE click that bypasses an intercepting overlay, then hides the element as a last resort. So if a toast or banner keeps blocking clicks (e.g. "intercepts pointer events"), use `dismiss_obstruction` on its close button rather than repeatedly trying to click through it.
  - IMPORTANT — cosmetic toasts do NOT block you: a persistent error toast/banner (e.g. a telephony "Couldn't Connect", connection, or notification error) is usually cosmetic. It does NOT prevent navigation or clicking other elements. Do NOT spend turns trying to close it. Try your real target directly (click the App Launcher, navigate to the URL, etc.) — if that works, the toast was never blocking you. Only treat something as a real blocker if it is a large centered modal dialog covering the page content.
  - Do NOT dismiss UI that YOU opened: if you click something (App Launcher, a dropdown, a menu, a date picker) and a panel/menu appears, that panel is the RESULT of your click — interact with it (search/select inside it), do not `dismiss_obstruction` it. Dismissing what you just opened only undoes your own progress.
  - Use `captcha_detected` if you see a CAPTCHA, "verify you are human", or similar bot-check. Do NOT try to solve it.
  - Use `open_app` when the task requires a connected app like Salesforce. This lands you ALREADY LOGGED IN on the app's home page. Do NOT navigate to the app's login page or try to log in yourself — emit `open_app` with the provider name and you will arrive authenticated. After that, navigate the app's UI normally.
  - STRONGLY prefer `navigate` to a direct URL over clicking through menus, app launchers, or list-view pickers. If a URL reaches the target in one step, use it — do NOT waste turns clicking App Launcher → searching → clicking again. Salesforce URL patterns:
      Object list (with filter): /lightning/o/<Object>/list?filterName=<FilterApiName>
      Specific record (by ID):   /lightning/r/<Object>/<RecordId>/view
      New-record form (modal):   /lightning/o/<Object>/new
    Reserve click/fill for actions that genuinely have NO URL equivalent: creating or editing records in modals, changing field values in forms, clicking buttons inside a record. Do NOT click the App Launcher or a list-view dropdown when `navigate` to a URL reaches the same destination.
  - Emit `done` only when the GOAL is clearly accomplished, citing concrete on-page evidence.
  - Emit `give_up` only after the trajectory shows you genuinely cannot proceed (needed element absent, repeated failures).
  - If a previous action in the TRAJECTORY did not work, try a DIFFERENT approach — never repeat the same failed action.
  - Your ENTIRE response must be a single COMPLETE JSON object. Keep "thought" to one sentence so the JSON is never truncated. Never write long prose.
  - In Salesforce Console, opening a record opens a NEW workspace tab next to the list tab. 
After you open a record, you are ON the record — do NOT click the list tab again thinking 
you need to navigate there; that takes you BACKWARD. If you see both a list tab and a 
record tab, the record is already open; proceed with the task on the record (click 
Related, etc.). Only return to the list if the task explicitly requires another record.
"""


# How many of the most recent iterations to render verbatim in the trajectory.
# Older turns are compressed to one line each to bound prompt size.
RECENT_TURNS = 5


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
    # Wait for any LWC spinners to clear (covers post-click panel hydration).
    _wait_for_lwc_spinners(page, timeout=2.0)
    # Small fixed settle for late layout shifts / animations.
    time.sleep(settle_ms / 1000.0)


def _wait_for_lwc_spinners(page: Page, timeout: float = 2.0) -> None:
    """Poll until Salesforce Lightning spinners/loading indicators are gone."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            spinning = page.evaluate(
                """() => !!(
                    document.querySelector(
                        '.slds-spinner_container:not([style*="display:none"])'
                        + ':not([style*="display: none"])'
                    ) ||
                    document.querySelector('lightning-spinner') ||
                    document.querySelector('.auraLoadingIndicator') ||
                    document.querySelector('.slds-is-loading')
                )"""
            )
            if not spinning:
                return
        except Exception:
            return
        time.sleep(0.2)


def _wait_for_lwc_panel(page: Page, timeout: float = 3.0) -> None:
    """After a dropdown click, poll until the opened LWC panel has its items.

    Salesforce LWC comboboxes and list-view dropdowns render their option list
    asynchronously via component hydration after the click event. Without this
    wait the next OBSERVE screenshot shows an empty panel; the LLM can't find
    the option ("Acme Cases", "Escalated", etc.) and loops forever.

    Important: SF list-view pickers render inside shadow DOM under custom
    elements like <force-list-view-manager> / <lightning-base-combobox>.
    document.querySelector('[role="listbox"]') misses these — we check both
    regular DOM and one level of shadow DOM for the known SF host elements.
    """
    # Always wait a minimum before checking — LWC needs at least one
    # microtask/render frame to start inserting the panel into the DOM.
    time.sleep(0.8)
    deadline = time.monotonic() + (timeout - 0.8)
    while time.monotonic() < deadline:
        try:
            ready = page.evaluate(
                """() => {
                    // --- Regular DOM (fast path) ---
                    const lb = document.querySelector('[role="listbox"]');
                    if (lb) {
                        const items = lb.querySelectorAll(
                            '[role="option"], .slds-listbox__item, lightning-base-combobox-item'
                        );
                        // listbox exists but still empty → keep waiting
                        return items.length > 0;
                    }

                    // --- Shallow shadow-DOM scan for known SF list-picker hosts ---
                    // SF renders <lightning-list-view-picker-panel> inside the
                    // shadow root of these custom elements.
                    const SF_HOSTS = [
                        'lightning-list-view-picker',
                        'force-list-view-manager',
                        'lightning-base-combobox',
                        'lightning-combobox',
                        'lightning-grouped-combobox',
                    ];
                    for (const tag of SF_HOSTS) {
                        const host = document.querySelector(tag);
                        if (!host || !host.shadowRoot) continue;
                        const lb2 = host.shadowRoot.querySelector('[role="listbox"]');
                        if (lb2) {
                            // listbox found in shadow DOM — wait for its children
                            return lb2.childElementCount > 0;
                        }
                    }

                    // --- aria-expanded check: if something is expanded but we
                    // haven't found its panel yet, keep waiting (it may still
                    // be hydrating). Return false to spin one more loop.
                    const expanded = document.querySelector('[aria-expanded="true"]');
                    if (expanded) return false;

                    // No open panel anywhere — click probably didn't open one,
                    // or it already closed. Bail out.
                    return true;
                }"""
            )
            if ready:
                return
        except Exception:
            return
        time.sleep(0.2)


def _sf_dismiss_toasts(page: Page) -> None:
    """Auto-hide Salesforce toast / notification banners before each OBSERVE.

    Toasts from the telephony/CTI adapter (and others) are rendered inside
    <lightning-notification-library> and have pointer-events that intercept
    clicks on the top navigation bar — including the App Launcher icon.
    We HIDE them (not click-close) because LWC onClick handlers don't fire
    reliably from Playwright synthetic events; CSS visibility is instant and
    doesn't depend on the framework.

    Only notifications are hidden — modal dialogs, record forms, and other
    interactive UI are unaffected.
    """
    try:
        page.evaluate(
            """() => {
                // 1. SF notification library — wraps all toast types
                for (const el of document.querySelectorAll('lightning-notification-library')) {
                    el.style.setProperty('display', 'none', 'important');
                    el.style.setProperty('pointer-events', 'none', 'important');
                }

                // 2. Toast / banner containers rendered directly (including
                //    shadow DOM children surfaced to regular DOM by LWC)
                const TOAST_SELS = [
                    '.slds-notify--toast',
                    '.slds-notify_container',
                    'div[data-key="error"]',
                    'div[data-key="warning"]',
                    'div[data-key="info"]',
                    // "Salesforce Inbox is enabled" banner
                    '.slds-global-notification',
                    '.slds-global-notification_container',
                ];
                for (const sel of TOAST_SELS) {
                    for (const el of document.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.height > 0) {
                            el.style.setProperty('display', 'none', 'important');
                            el.style.setProperty('pointer-events', 'none', 'important');
                        }
                    }
                }

                // 3. Any [role="status"] bar pinned to the top of the viewport
                //    (covers "Salesforce Inbox is enabled", trial/promo banners)
                for (const el of document.querySelectorAll('[role="status"]')) {
                    const r = el.getBoundingClientRect();
                    if (r.height > 0 && r.top < 80 && r.width > 300) {
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('pointer-events', 'none', 'important');
                    }
                }
            }"""
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Trajectory rendering — full history, compact
# ---------------------------------------------------------------------------

def _circuit_breaker(trace, kind, action_args, grounding) -> str | None:
    """MECHANICAL loop-breaker. Returns a forced observation string if this
    exact action should be REFUSED (not executed), else None.

    Rule: if the same (kind, ref) appeared in the last 3 iterations and none of
    them made progress (error, or observation that did not actually unblock),
    refuse the 3rd+ repeat. Unlike a prompt nudge, the action never runs — so
    the model physically cannot spin on it.

    Special-cased for the failure we keep seeing: dismiss_obstruction reporting
    "hidden" success while the page stays blocked because the chosen ref is the
    wrong element (e.g. the invisible forceSkipLink), not the modal.
    """
    ref = action_args.get("ref") if isinstance(action_args, dict) else None

    # Canonical signature for oscillation detection.
    # For navigate, the URL is the identity so two navigates to DIFFERENT URLs
    # are never treated as the same "slot" in a toggle pattern — navigating to
    # a new URL is a valid strategy change, not a toggle.
    def _sig(act, args):
        a = args or {}
        if act == "navigate":
            return ("navigate", a.get("url"))
        return (act, a.get("ref"))

    # --- Oscillation breaker: A-B-A-B toggling (the "toggle trap") ---------
    # e.g. click #28 (App Launcher) -> dismiss #50 (the menu that opened) ->
    # click #28 -> dismiss #50 ... The agent is fighting UI IT opened. Detect
    # two alternating (action,ref) pairs repeating over the last 4 turns.
    if len(trace) >= 4:
        last4 = trace[-4:]
        sig = [_sig(it.action, it.action_args) for it in last4]
        cur = _sig(kind, action_args)
        # pattern X,Y,X,Y and the next action would continue it (==X)
        if sig[0] == sig[2] and sig[1] == sig[3] and sig[0] != sig[1] and cur == sig[0]:
            a_act, a_ref = sig[0]
            b_act, b_ref = sig[1]
            return (
                f"BLOCKED: you are TOGGLING — alternating `{a_act}` on #{a_ref} and "
                f"`{b_act}` on #{b_ref} repeatedly. Whatever appears after you "
                f"`{a_act}` #{a_ref} is the UI that action OPENED (a menu/panel/"
                f"dropdown), NOT a blocker — do not dismiss it. If you opened the "
                f"App Launcher, the panel that appears IS the App Launcher: type "
                f"into its search box or click an app tile inside it. Stop "
                f"dismissing what you just opened. If you actually want to reach a "
                f"specific page, `navigate` directly to its URL instead."
            )

    if ref is None:
        return None

    # Count identical (kind, ref) in the last 3 turns.
    recent = trace[-3:]
    same = [
        it for it in recent
        if it.action == kind and isinstance(it.action_args, dict)
        and it.action_args.get("ref") == ref
    ]
    if len(same) < 3:
        return None

    # Are those repeats actually getting nowhere? "hidden by"/"FAILED"/"WARNING"
    # /errors all count as no-progress for dismiss; any error counts generally.
    def _no_progress(it) -> bool:
        if it.error:
            return True
        obs = it.observation or ""
        return ("by hiding it" in obs or obs.startswith(("FAILED", "CANNOT", "WARNING"))
                or "already" in obs or "still" in obs)

    if not all(_no_progress(it) for it in same):
        return None

    # Build a hard, specific message. Identify what ref actually is, so the
    # model stops believing it's the modal.
    what = ""
    if grounding is not None:
        el = grounding.by_ref(str(ref)) if hasattr(grounding, "by_ref") else None
        if el is not None:
            what = f" Element #{ref} is actually a {el.role} named {el.name!r}"
            if "skip" in (el.name or "").lower() or "skip" in (el.tag or "").lower():
                what += " — an invisible accessibility skip-link, NOT a modal"
            what += "."

    return (
        f"BLOCKED: refusing to run `{kind}` on #{ref} again — you have tried it "
        f"3+ times with no progress and the page is still blocked.{what} "
        f"The refs you are using do NOT match the modal you see in the "
        f"screenshot. STOP targeting #{ref}. Do ONE of these instead: "
        f"(1) look at the CURRENT ELEMENTS list and pick the toast/modal's real "
        f"close button by its name (often 'Close' or an X near the red banner), "
        f"(2) if the blocker is the telephony error, IGNORE it and proceed to "
        f"your real target — the App Launcher or a direct record URL — since the "
        f"banner does not actually prevent navigation, or (3) `navigate` straight "
        f"to the list/record URL you need. Do not call dismiss_obstruction on "
        f"#{ref} again."
    )


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

    # Pattern C: the SAME ref keeps failing across the last 3 turns even if the
    # action kind varies (the ref:47 'not visible' loop). The ref is almost
    # certainly stale — the page changed and the numbering shifted since it was
    # observed. Tell the agent the refs are unreliable and to re-read the page.
    def _ref_of(it):
        return it.action_args.get("ref") if isinstance(it.action_args, dict) else None
    recent3 = trace[-3:]
    if len(recent3) == 3:
        refs = [_ref_of(it) for it in recent3]
        if refs[0] is not None and refs.count(refs[0]) == 3 and all(_failed(it) for it in recent3):
            bad = refs[0]
            return (
                f"ESCALATION: you have targeted #{bad} three times and it keeps "
                f"failing (often 'element is not visible'). The ref is STALE — the "
                f"page changed since these numbers were assigned, so #{bad} no longer "
                f"points at what you think. STOP using #{bad}. Look at the CURRENT "
                f"ELEMENTS list fresh and pick the ref that now matches the control "
                f"you want by its role and name — do NOT reuse the old number. If the "
                f"control you want is not in the list, scroll or navigate instead."
            )


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


def _summarize_iteration(it: LoopIteration) -> str:
    """Compress one past iteration to a single line for the history header."""
    act = it.action
    if it.action_args:
        act += " " + json.dumps(it.action_args, separators=(",", ":"))
    outcome = it.error or it.observation or "ok"
    if len(outcome) > 80:
        outcome = outcome[:77] + "..."
    return f"iter {it.iteration}: {act} → {outcome}"


def _render_trajectory(trace: list[LoopIteration]) -> str:
    """Render past iterations as compact text.

    The last RECENT_TURNS are shown verbatim (full thought/action/observation)
    so the model has rich context for its immediate decision. Older turns are
    compressed to one line each — enough to know what was already tried without
    ballooning the prompt at high iteration counts.

    Screenshots are never re-embedded — only referenced by path — so image
    payload cost stays flat regardless of how many turns have elapsed.
    """
    if not trace:
        return "(no previous actions — this is the first turn)"

    lines: list[str] = []

    if len(trace) > RECENT_TURNS:
        older = trace[:-RECENT_TURNS]
        recent = trace[-RECENT_TURNS:]
        lines.append(f"[{len(older)} earlier turns compressed]")
        for it in older:
            lines.append(_summarize_iteration(it))
        lines.append(f"[last {len(recent)} turns in full]")
    else:
        recent = trace

    for it in recent:
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
    max_iterations: int = 20,
    max_seconds: float = 400.0,
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
        _sf_dismiss_toasts(page)   # hide toasts BEFORE grounding so they don't intercept clicks
        url = page.url
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        elements = extract_from_page(page, already_stable=True)
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

        screenshot = annotate_screenshot(screenshot, elements)
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
                    screenshot = annotate_screenshot(page.screenshot(type="png"), elements)
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

        # ---------- CIRCUIT-BREAKER (mechanical, model cannot override) ----------
        # If this exact (action, ref) was already tried in the last few turns
        # and kept failing/no-progress, REFUSE to run it again. Prompt nudges
        # have proven insufficient (the model ignores them and loops), so we
        # block the call in code and force a re-observation with a hard message.
        _blocked_obs = _circuit_breaker(trace, kind, action_args, elements)
        if _blocked_obs is not None:
            trace.append(LoopIteration(
                iteration=iteration, thought=thought, action=kind,
                action_args=action_args, observation=_blocked_obs,
                error="blocked_repeat", screenshot_ref=screenshot_ref,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            continue

        # ---------- ACT ----------
        try:
            observation = _execute_action(page, kind, action, grounding=elements)
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

def _find_locator(page: Page, ref: str):
    """Return a Playwright Locator for [data-agent-ref=ref].

    Checks the main frame first, then all child frames (covers iframe elements
    that EXTRACT_JS now collects).  Falls back to a main-frame locator that will
    raise a normal 'not found' error if the ref truly doesn't exist anywhere.
    """
    sel = f'[data-agent-ref="{ref}"]'
    loc = page.locator(sel)
    if loc.count() > 0:
        return loc.first
    for frame in page.frames[1:]:  # frames[0] is the main frame
        try:
            floc = frame.locator(sel)
            if floc.count() > 0:
                return floc.first
        except Exception:
            continue
    return page.locator(sel).first  # not found — let the caller get the normal error


def _execute_action(page: Page, kind: str, action: dict, grounding) -> str:
    """Execute one action, return a compact text observation describing
    what happened (fed back into the next turn's trajectory)."""
    if kind == "click":
        ref = str(action["ref"])
        _find_locator(page, ref).click(timeout=2500)
        # After any click, wait for LWC dropdowns/menus to hydrate their items
        # before the next OBSERVE screenshot — prevents the "empty panel" loop.
        _wait_for_lwc_panel(page)
        return f"clicked element #{ref}"

    if kind == "click_text":
        text = str(action.get("text", "")).strip()
        if not text:
            return "FAILED: click_text requires a non-empty 'text' argument"
        # Playwright's role/text locators use the AX tree and pierce shadow DOM —
        # the escape hatch for elements visible in the screenshot but missing from
        # the ELEMENTS list (e.g. lightning-datatable links in closed shadow roots).
        # Try as a link first (most common case), then as any visible text element.
        for exact in (True, False):
            try:
                page.get_by_role("link", name=text, exact=exact).first.click(timeout=3000)
                _wait_for_lwc_panel(page)
                return f"clicked link with text {text!r} (exact={exact})"
            except Exception:
                pass
        for exact in (True, False):
            try:
                page.get_by_text(text, exact=exact).first.click(timeout=3000)
                _wait_for_lwc_panel(page)
                return f"clicked element with text {text!r} (exact={exact})"
            except Exception:
                pass
        return f"FAILED: no clickable element with visible text {text!r} found"

    if kind == "fill_field_by_label":
        label = str(action.get("label", "")).strip()
        text  = str(action.get("text",  "")).strip()
        if not label:
            return "FAILED: fill_field_by_label requires a non-empty 'label'"

        # --- textbox path (text input, date field, textarea) ---
        # get_by_label uses the <label> element association; get_by_role("textbox")
        # uses the AX accessible name — both pierce shadow DOM.
        for exact in (True, False):
            for loc in (
                page.get_by_label(label, exact=exact),
                page.get_by_role("textbox", name=label, exact=exact),
            ):
                try:
                    loc.first.fill(text, timeout=3000)
                    return f"filled {label!r} with {text!r}"
                except Exception:
                    pass

        # --- combobox / select path (Status, Subject picklists) ---
        for exact in (True, False):
            try:
                combo = page.get_by_role("combobox", name=label, exact=exact).first
                combo.click(timeout=3000)
                _wait_for_lwc_panel(page)
                # Click the matching option in the opened dropdown
                for opt_exact in (True, False):
                    try:
                        page.get_by_role("option", name=text, exact=opt_exact).first.click(timeout=2000)
                        return f"selected {text!r} in {label!r}"
                    except Exception:
                        pass
                # Subject-style: type to filter, then pick the top suggestion
                try:
                    combo.fill(text, timeout=2000)
                    time.sleep(0.4)
                    page.get_by_role("option", name=text, exact=True).first.click(timeout=2000)
                    return f"typed and selected {text!r} in {label!r}"
                except Exception:
                    pass
                return (f"opened {label!r} combobox but could not select {text!r} "
                        f"— check available options or use click_text on the option")
            except Exception:
                pass

        return f"FAILED: could not find a field with label {label!r}"

    if kind == "fill":
        ref = str(action["ref"])
        text = str(action.get("text", ""))
        _find_locator(page, ref).fill(text, timeout=2500)
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
        # domcontentloaded fires before LWC initializes any components — use it
        # only as a fast signal that the redirect completed, then do a
        # provider-specific readiness poll so the next OBSERVE sees real UI.
        page.goto(backend_base + path, wait_until="domcontentloaded")
        if provider == "salesforce":
            return _wait_for_salesforce_ready(page)
        return f"opened {provider}, logged in"

    if kind == "scroll":
        d = action.get("direction", "down")
        delta = 700
        dx, dy = (
            (0, delta) if d == "down" else
            (0, -delta) if d == "up" else
            (delta, 0) if d == "right" else
            (-delta, 0)
        )
        page.mouse.wheel(dx, dy)
        return f"scrolled {d}"

    if kind == "wait":
        secs = min(float(action.get("seconds", 1)), 10)
        time.sleep(secs)
        return f"waited {secs}s"

    if kind == "dismiss_obstruction":
        ref = str(action["ref"])
        loc = _find_locator(page, ref)

        def _still_blocking() -> bool:
            """True if the element still exists AND is visible — i.e. the
            dismissal did NOT actually work. This is the honesty check the
            force-click loop was missing: on LWC a raw click 'succeeds'
            mechanically but the framework's real handler never fires, so the
            modal stays. We verify the RESULT, not the click."""
            try:
                return bool(page.evaluate(
                    """(ref) => {
                        const el = document.querySelector('[data-agent-ref="' + ref + '"]');
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) return false;
                        const s = window.getComputedStyle(el);
                        if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) return false;
                        return true;
                    }""",
                    ref,
                ))
            except Exception:
                return False  # can't tell → assume gone, don't loop

        # Rung 1: normal click, THEN verify it closed.
        try:
            loc.click(timeout=3000)
            time.sleep(0.3)
            if not _still_blocking():
                return f"dismissed obstruction via element #{ref}"
        except Exception:
            pass

        # Rung 2: force click, THEN verify. On Lightning/LWC this often fires a
        # raw DOM event that does NOT trigger the framework's handler — so the
        # modal stays. We must check, not trust. If it's still there, fall
        # through to hide rather than (the old bug) reporting false success.
        try:
            loc.click(force=True, timeout=3000)
            time.sleep(0.3)
            if not _still_blocking():
                return f"dismissed obstruction via element #{ref} (forced click)"
        except Exception:
            pass

        # Rung 3: HIDE the element (+ its toast/dialog container). This is the
        # rung that actually works on LWC, because it does not depend on
        # triggering Salesforce's onClick — it just stops the overlay from
        # rendering and intercepting pointer events. HIDE, never remove():
        # reversible and far less likely to break the reactive framework.
        try:
            hidden = page.evaluate(
                """(ref) => {
                    const el = document.querySelector('[data-agent-ref="' + ref + '"]');
                    if (!el) return false;
                    const targets = [el];
                    let p = el;
                    for (let i = 0; i < 6 && p; i++) {
                        p = p.parentElement;
                        if (p && (
                            /toast|modal|dialog|overlay|popup|banner|docked|minimizedItems/i.test(p.className || '') ||
                            (p.getAttribute && (p.getAttribute('role') === 'dialog' ||
                                                p.getAttribute('role') === 'alert' ||
                                                p.getAttribute('role') === 'alertdialog'))
                        )) { targets.push(p); break; }
                    }
                    for (const t of targets) {
                        t.style.setProperty('visibility', 'hidden', 'important');
                        t.style.setProperty('pointer-events', 'none', 'important');
                        t.style.setProperty('display', 'none', 'important');
                    }
                    return true;
                }""",
                ref,
            )
            time.sleep(0.2)
            if hidden and not _still_blocking():
                return (f"dismissed obstruction #{ref} by hiding it (clicks did not "
                        f"close it — element and its container hidden so they no "
                        f"longer block the page)")
            if hidden:
                return (f"hid element #{ref}, but something at that position may still "
                        f"be present — if it still blocks you, try a different approach "
                        f"(navigate directly, or act on a different element).")
            return f"FAILED to dismiss #{ref}: element not found to hide. It may already be gone — re-check the page."
        except Exception as e:
            return f"FAILED to dismiss #{ref}: {type(e).__name__}: {e}"

    return f"unknown action: {kind}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_salesforce_ready(page: Page, timeout: float = 15.0) -> str:
    """Poll for Lightning/LWC readiness after open_app navigation.

    domcontentloaded fires before the Aura framework has rendered any
    components. We block here (up to `timeout` seconds) so the next OBSERVE
    iteration sees the real, interactive UI instead of an empty shell.

    Two-stage check:
      1. Wait for [data-aura-rendered-by] — set by Aura on every rendered
         component. Its presence means the JS framework is live.
      2. Wait for known Lightning spinners to clear — indicates the initial
         data fetch is also done.
    Both stages degrade gracefully: a timeout at either stage is logged but
    does NOT raise, so the agent can still attempt the next action.
    """
    deadline = time.monotonic() + timeout

    # Stage 1 — Aura/LWC framework rendered at least one component.
    while time.monotonic() < deadline:
        try:
            if page.evaluate("() => !!document.querySelector('[data-aura-rendered-by]')"):
                break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        return "opened Salesforce (Aura not detected within timeout — page still loading)"

    # Stage 2 — Lightning spinners clear (initial data load done).
    # Cap this stage at 6 s or whatever remains of the total budget.
    spin_deadline = min(deadline, time.monotonic() + 6.0)
    while time.monotonic() < spin_deadline:
        try:
            spinning = page.evaluate(
                """() => !!(
                    document.querySelector('.slds-spinner_container:not([style*="display:none"]):not([style*="display: none"])') ||
                    document.querySelector('lightning-spinner') ||
                    document.querySelector('.auraLoadingIndicator')
                )"""
            )
            if not spinning:
                break
        except Exception:
            break
        time.sleep(0.3)

    return "opened Salesforce, Lightning app ready"


def _maybe_save_screenshot(img_bytes: bytes, screenshot_dir: str | None, iteration: int) -> str | None:
    if not screenshot_dir:
        return None
    import os
    try:
        os.makedirs(screenshot_dir, exist_ok=True)
        path = os.path.join(screenshot_dir, f"iter_{iteration:03d}.jpg")
        with open(path, "wb") as f:
            f.write(img_bytes)
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