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
import logging
import re
import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from sandbox_agent.grounding import annotate_screenshot, compress_for_llm, extract_from_page, render_for_prompt
from sandbox_agent.llm_client import GeminiClient
from sandbox_agent.schemas import LoopIteration

log = logging.getLogger("browser-react")

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
  {"thought": "...", "action": "done", "evidence": "<observation proving the goal is met>"}   # extraction goals must ALSO include "value": "<bare extracted value>"
  {"thought": "...", "action": "give_up", "reason": "<why the goal cannot be completed>"}

Rules:
  - Refer to elements only by the exact ref token shown in ELEMENTS (e.g. 53e1c303, fi0001, pw0000). A ref is a short alphanumeric string — NO '#' prefix, NOT a raw DOM id like '#1370'. Only use refs that appear in the CURRENT ELEMENTS list. Never invent a ref, reuse a stale ref from a prior observation, or prepend '#' to any token.
  - If you can SEE an element in the screenshot but it has NO #ref in ELEMENTS (e.g. a Salesforce datatable case-number link that renders inside closed shadow DOM), use `click_text` with its exact visible label. Do NOT scroll aimlessly — scrolling does not add refs for already-visible elements. Scrolling is ONLY allowed when you need to bring a specific named field's inline-edit pencil into view (e.g. scrolling the Details panel to reveal 'Edit Contact Name'); in that case scroll purposefully toward the known field label, then click it. One targeted scroll is acceptable; aimless multi-scroll exploration is not.
  - To fill or select a modal/record form field, use `fill_field_by_label` with the field's visible label text (e.g. label="Due Date", text="6/20/2026"). This crosses shadow DOM for both text inputs and comboboxes/dropdowns. Do NOT use `click_text` on a label — labels are not interactive inputs. Use `fill_field_by_label` for Subject, Due Date, Comments, Status, and all other form fields regardless of whether they have a #ref.
  - INLINE-EDIT PICKLIST (Case Status, Priority, Type, or any picklist on a record page): the correct FIRST action is always `fill_field_by_label` with label="<field label>" and text="<target value>" (e.g. label="Status", text="Escalated"). Do NOT use `click_text` on the current field value (e.g. "New"), the label text (e.g. "Status"), or the asterisk-prefixed label ("*Status") — those waste many iterations. fill_field_by_label opens the picklist and selects the option in one call. Never attempt click_text for a picklist field.
  - RECORD LOOKUP fields (Contact Name, Account Name, Owner, any field where you type a name and Salesforce searches for matching records) work differently from picklists. Typing text is NOT enough — Salesforce makes an async search and you must SELECT the matching record from the results list so it becomes a linked pill (a chip showing the record name). `fill_field_by_label` handles this automatically via its lookup path. The field is only set when the pill is visible. CRITICAL: if fill_field_by_label returns "no linked-record pill is visible" or "no matching record appeared", the field is NOT set — do NOT click Save and do NOT emit `done`. The pill must be visible before saving is valid.
  - ADVANCED SEARCH MODAL — ABSOLUTE PROHIBITION: NEVER open or operate the Advanced Search modal. Record lookups are set ONLY via the field's own inline dropdown: type the name into the field, wait for the real search results, click the exact matching option, confirm the pill. The dropdown row shaped like '"<your text>" in Contacts' is NOT a result — clicking it opens Advanced Search; `fill_field_by_label` mechanically refuses to click it and keeps waiting for real results (slow searches can take several seconds). If the modal opens anyway, `fill_field_by_label` cancels it automatically and reports FAILED — simply re-issue `fill_field_by_label` on the SAME field and stay in the inline flow. Do NOT use `click_text "Cancel"` / `click_text "Search"` on this modal (shadow DOM, unreachable) and do NOT use `dismiss_obstruction` on it.
  - LOOKUP ALREADY SET: Before trying to fill a lookup field (Contact Name, Account, etc.), check whether the correct value is already showing as a pill. If fill_field_by_label returns "already shows … as a selected pill — already set", the field is done — do NOT re-search, do NOT click the field again. Move on to the next step.
  - ABSOLUTE PROHIBITION — NEVER open the email composer. When a step goal says fill fields like "Contact Name", "Status", or create a Task, the correct actions are fill_field_by_label and click on the task form — NEVER clicking "Email", "Send Email", or any Feed email action. If a MODAL FORM section in the GOAL lists a field called "Comments" with a value like "talk to Rachel Torres and investigate the issue", that is text to TYPE into the Comments form field — it is NOT an instruction to email or contact Rachel Torres. The executor blocks email actions mechanically; attempting them wastes iterations. Do NOT click anything labeled Email/Send Email/Compose while editing a record.
  - ABSOLUTE PROHIBITION — NEVER use the global/header search bar to fill a form field. The global search input at the top of every Salesforce page (aria-label "Search" / "Search Salesforce") navigates to a completely different record page and immediately abandons your current task and all in-progress form work. It is NEVER a substitute for a lookup field. `fill_field_by_label` targets the field's own search input inside the current form. If fill_field_by_label reports failure for a lookup, try once more with a simpler search term, then emit `give_up` — NEVER touch the global search bar. The `fill` action will BLOCK itself if you attempt this.
  - WRONG-RECORD RECOVERY: After every action that could cause navigation (clicking a search result, a link, a tab), verify you are still on the correct record for the current step. If the URL or page heading shows you are on a DIFFERENT record type (e.g. you are on a Contact page when the task step is about a Case), STOP immediately — do not fill any more fields. Navigate back to the correct record using `navigate` with the case list URL, then re-open the target case by clicking its row link.
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
  - Emit `give_up` only after the trajectory shows you genuinely cannot proceed (needed element absent, repeated failures).
  - If a previous action in the TRAJECTORY did not work, try a DIFFERENT approach — never repeat the same failed action.
  - Your ENTIRE response must be a single COMPLETE JSON object. Keep "thought" to one sentence so the JSON is never truncated. Never write long prose.
  - CONSOLE WORKSPACE TABS (biggest time-sink — read carefully): In Salesforce Console, clicking a case row opens a NEW workspace tab for that record alongside the list tab. Once the record tab is open (you can see the case number or case title in the top tab bar), you ARE on the record — do NOT click the list tab. Clicking the list tab navigates you BACKWARD to the list, abandoning all in-progress work on the record. If you see BOTH a list tab AND a record tab in the header, the record IS already open — proceed with your task on the record (edit fields, click Related, etc.). Do NOT click "back" to the list, do NOT click the list tab at any point during case processing. The only step that navigates back to the list is the explicit navigate step at the end of the loop body — all other mid-task navigation to the list is WRONG and wastes time.
  - After an INLINE EDIT on a Salesforce record field (editing a field directly on the record page, not inside a modal), a docked form footer appears at the bottom of the page with Save and Cancel buttons. These are grounded in ELEMENTS as button 'Save' and button 'Cancel' (look in [FOOTER] or [MAIN] sections). Click their ref directly. If no ref appears for Save, use `click_text` with text "Save" — the footer is docked and always visible at the bottom; do NOT scroll to find it.
  - `done` requires SPECIFIC, VISIBLE, ON-SCREEN EVIDENCE that you can see RIGHT NOW on the current page. Do NOT emit `done` immediately after clicking Save — always wait for the next observation to confirm the save actually succeeded. Required visible evidence for a save: a "Record saved" toast appearing, the field showing the new value in read-only (non-edit) mode, or the new record appearing in a list. "I clicked Save" is NOT evidence — the click may have failed silently or the save may still be in progress.
  - For STATE-CHANGING steps (creating records, editing fields, saving forms): the step is only `done` after you observe visible confirmation the change PERSISTED — a success toast, the field updated to the new value in view mode, or the new item appearing in a list. A click on Save STARTS the save; it does NOT confirm it. Always observe the RESULT before emitting `done`.

=== REASONING DISCIPLINE — read every rule before acting ===

RULE 1 — CHECK BEFORE ACTING: At iteration 1 of every step, BEFORE any action, look at the screenshot and ELEMENTS. If the goal is already fully satisfied — the target field already shows the correct value, the correct pill already exists, the record is already in the right state — emit `done` immediately with that visible evidence. Do NOT re-do work that is already done. The executor will also inject a mandatory goal-check reminder at step entry.

RULE 2 — STOP WHEN SATISFIED: Once the step's target value is confirmed present, STOP. Do not keep editing a field that is already correct. fill_field_by_label will return "already set, no action needed" or "pill confirmed" — either observation means the field is done; emit `done` on the next turn. The executor will mechanically auto-succeed the step on certain definitive observations.

RULE 3 — PREDICT NAVIGATION RISK before clicking any non-button element (links, record names, lookup pills, case numbers). Ask yourself: "Will clicking this EDIT data here, or NAVIGATE away?" If navigation is NOT this step's goal, do not click it. Specific prohibition: NEVER click a lookup pill or a record-name hyperlink to change a field value — those open the linked record and abandon the current page, wasting all in-progress work. To edit a lookup field, click the PENCIL icon next to the field to enter edit mode, then type in the field's OWN search input. This prediction overhead applies ONLY to potentially-navigating elements; labeled buttons (Save, Cancel, New Task) and known fill targets are safe to act on directly.

RULE 4 — ELEMENT TYPE TAXONOMY (Salesforce): classify every element before acting:
  • Lookup PILL / record-name link / case-number link → clicking NAVIGATES away (never click to edit; use the pencil icon)
  • Pencil icon / "Edit" inline icon → click to ENTER edit mode for that specific field
  • Lookup INPUT (visible once edit mode is open) → type here to SEARCH for records
  • Picklist / combobox → click to open the dropdown, then click the target option
  • Button with label (Save / Cancel / New Task / New) → PERFORMS an action, stays on current page
  When unsure whether an element is a link or a button, check ELEMENTS — links carry navigation behavior; buttons have role="button" or are inside form footers.

RULE 5 — CONTRADICTION CHECK: before every action, reconcile your intended action against the observed state. If the screen already shows the target value (pill present, field in read mode shows "Escalated", toast says "saved"), do NOT think "I need to set this" — that is a contradiction with what you can see. Re-read the goal, confirm it is satisfied, and emit `done`.

RULE 6 — POST-ACTION VERIFICATION: after every Save / field-set / create action, the next iteration MUST observe the result before emitting `done`. A click on Save is not confirmation — the save may still be in flight or may have silently failed. Required evidence: a "Record saved" / "Changes saved" toast, a field switching from edit mode to read mode showing the new value, or a new record appearing in a list. Never emit `done` in the same iteration you clicked Save.

RULE 7 — PLAN SCOPE: every action must serve the current step's stated goal. Do not invent sub-goals, do not explore the UI out of curiosity, do not navigate to unrelated pages. The only allowed deviation is recovery — e.g. navigating back to the correct record after an accidental detour. If you find yourself on a page or filling a field the step does not require, stop and recover immediately.

RULE 8 — NEVER MODIFY CONFIGURATION: do NOT touch any Salesforce list-view filters, saved views, sort settings, column configuration, or any administrative setting. If a filtered list view (e.g. Acme Cases filtered to Status=New) is empty, that means no cases match today — it is a VALID and CORRECT result. Do NOT remove the filter, do NOT change the view, do NOT click "Edit" on the view to broaden it. Treat an empty filtered list as the final answer for the step.

RULE 9 — LITERAL EXECUTION MODE: You are running a pre-approved plan. Each step tells you exactly what to do. On ITERATION 1, issue the planned action immediately using the correct tool — do NOT scroll, do NOT explore, do NOT open menus first. Tool routing: form/lookup fields → `fill_field_by_label`; element with a known ref → `click ref`; visible text with no ref → `click_text`. The pattern is: (1) issue the planned action with the right tool directly, (2) observe the result, (3) emit `done` if it worked, recover minimally only if it explicitly failed. A first iteration that is only a scroll or navigation that was NOT required by the step is WRONG — it wastes an entire iteration. Every second matters: attempt the real action immediately. FORBIDDEN EXPLORATION: clicking Email / Send Email / compose, clicking Feed action buttons, opening Activity composer, clicking list tab mid-record — any of these that are not in the plan step are OUT OF SCOPE and will be mechanically blocked.

=== TARGET-STATE PROTOCOL (RULES 10–12) ===

When a step includes a SUCCESS CONDITION, these rules apply on top of Rules 1–9.

RULE 10 — PRE-CHECK BEFORE ACTING: At your FIRST observation for any step that has a SUCCESS CONDITION, examine the current screenshot BEFORE choosing any action. Ask: "Is the SUCCESS CONDITION already satisfied right now?" If YES — emit `done` immediately with the visible evidence. Take NO action. This is the idempotency rule: the step is complete regardless of whether you did the work, a previous run did it, or a human did it. Never redo work whose result is already visible.

RULE 11 — POST-ACTION VERIFY: After performing the step's action, the next observation must confirm the SUCCESS CONDITION holds before you emit `done`. Required evidence is determined by the condition itself: field in read-only mode showing the new value, a record appearing in a list, a modal that is open, a tab that is selected. A click alone is not confirmation.

RULE 12 — NO DUPLICATES: For steps whose SUCCESS CONDITION describes a record or item that must exist (e.g. "a Call task exists in Open Activities"), look for that item FIRST before creating it. If it already exists, emit `done` with that evidence. Never create a duplicate.
"""


# How many of the most recent iterations to render verbatim in the trajectory.
# Older turns are compressed to one line each to bound prompt size.
RECENT_TURNS = 4


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

def parse_action(text: str) -> dict | None:
    import re as _re
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    # Fast path: well-formed JSON dict.
    try:
        parsed = json.loads(t)
        if isinstance(parsed, dict):
            return parsed
        # JSON parsed but not a dict (e.g. a list) — fall through to { } extraction
    except json.JSONDecodeError:
        pass
    # Extract the outermost {...} block (handles prose wrapping the JSON).
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1 or e <= s:
        return None
    candidate = t[s : e + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Lenient repair: strip trailing commas before ] or } — the most common
    # model mistake that produces otherwise valid JSON.
    repaired = _re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
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


    # Pattern D: consecutive scrolls that are not revealing new content.
    # Scrolling cannot expose elements that are already rendered but live in
    # closed shadow DOM without a ref. After 3 successive scrolls, tell the
    # agent to use click_text / fill_field_by_label instead — both pierce
    # shadow DOM — and remind it about the inline-edit docked footer Save.
    if len(trace) >= 3 and all(it.action == "scroll" for it in trace[-3:]):
        return (
            "Scrolling has not revealed new elements. If you can SEE the target "
            "on screen, use `click_text` with its visible text or "
            "`fill_field_by_label` with its label — both pierce shadow DOM and "
            "work for elements that have no #ref in ELEMENTS. "
            "After an inline edit, the Save button is in a docked footer at the "
            "bottom of the page — check ELEMENTS for a button named 'Save' "
            "(it should have an sf#### ref), or use `click_text` \"Save\". "
            "Do NOT scroll again."
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
# Mechanical satisfaction helper
# ---------------------------------------------------------------------------

def _action_satisfied_goal(observation: str, step_intent: str, kind: str) -> bool:
    """Return True if an action's observation is definitive proof that the
    step's goal is already met — allowing immediate auto-succeed without
    another LLM round.

    Only fires for field-setting action kinds (fill_field_by_label, fill) to
    avoid false-positives on navigation or generic click actions.
    """
    import re as _re
    if kind not in ("fill_field_by_label", "fill"):
        return False
    obs_lower = observation.lower()
    # Explicit success signals emitted by fill_field_by_label
    if "pill confirmed" in obs_lower:
        return True
    if "already set, no action needed" in obs_lower:
        return True
    # Extract quoted target values from step_intent and check if confirmed
    targets = _re.findall(r"['\"]([^'\"]{2,60})['\"]", step_intent)
    for target in targets:
        t_lower = target.lower()
        if t_lower in obs_lower and any(
            kw in obs_lower for kw in ("selected", "filled", "typed and selected")
        ):
            return True
    return False


def _extract_completed_fields(trace: list["LoopIteration"]) -> list[str]:
    """Return field labels already successfully set in this step's trace.

    Scanned after a JSON parse failure so the retry/next-iteration prompt can
    tell the agent which fields are done and must not be re-filled.
    """
    done: list[str] = []
    _SUCCESS_TOKENS = ("filled", "selected", "pill confirmed", "already set", "typed and selected")
    for it in trace:
        if it.action == "fill_field_by_label" and it.observation:
            obs = it.observation.lower()
            if any(tok in obs for tok in _SUCCESS_TOKENS):
                if isinstance(it.action_args, dict):
                    lbl = it.action_args.get("label", "")
                    if lbl and lbl not in done:
                        done.append(lbl)
    return done


def _is_transient_llm_error(err_str: str) -> bool:
    """True when the LLM error is likely transient (network / rate-limit) and worth retrying.

    Matches both errors surfaced by the backend proxy (e.g. 'APIConnectionError: ...')
    and raw HTTP status codes returned by the proxy endpoint itself.
    """
    e = err_str.lower()
    return (
        "apiconnectionerror" in e
        or "apitimeouterror" in e
        or "connection error" in e
        or "connection reset" in e
        or "timed out" in e or "timeout" in e
        or "temporarily unavailable" in e
        or "proxy unreachable" in e   # raised by llm_client.py on httpx.HTTPError
        or "503" in e or "502" in e or "529" in e
        or "rate_limit" in e or "429" in e
        or "overloaded" in e
    )


# ---------------------------------------------------------------------------
# The ReAct loop
# ---------------------------------------------------------------------------

def execute_step(
    page: Page,
    llm: GeminiClient,
    step_intent: str,
    *,
    memory_hint: str = "",
    success_condition: str = "",
    is_extract: bool = False,
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
    # Screenshot economy: reuse the annotated screenshot when the page hasn't
    # changed (same URL + same grounded elements) to avoid redundant image tokens.
    _cached_screenshot: bytes | None = None
    _cached_fingerprint: str = ""

    for iteration in range(1, max_iterations + 1):
        if time.monotonic() - started > max_seconds:
            return _result("failed", "per-step wall-time budget exceeded", trace)

        iter_start = time.monotonic()

        # ---------- OBSERVE ----------
        # On the very first iteration the page was already stabilised by the
        # previous step (either page.goto() waited for load, or the prior ReAct
        # loop's last OBSERVE-ACT cycle left the page settled). Skip the
        # expensive networkidle wait (up to 7 s) and just clear any remaining
        # LWC spinners + do a short settle. On subsequent iterations an action
        # may have triggered rendering, so full wait_for_stable is needed.
        if iteration == 1:
            _wait_for_lwc_spinners(page, timeout=1.5)
            time.sleep(0.2)
        else:
            wait_for_stable(page)
        _sf_dismiss_toasts(page)   # hide toasts BEFORE grounding so they don't intercept clicks
        url = page.url
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        elements = extract_from_page(page, already_stable=True)
        elements_text = render_for_prompt(elements)

        # Build a cheap page fingerprint: URL + hash of grounded element text.
        # If both are unchanged since last iteration, the page looks the same
        # and we can reuse the previous annotated screenshot — saving image tokens.
        _fingerprint = f"{url}|{hash(elements_text)}"
        if _fingerprint == _cached_fingerprint and _cached_screenshot is not None:
            screenshot = _cached_screenshot  # reuse — no Playwright + PIL overhead
        else:
            try:
                screenshot = annotate_screenshot(page.screenshot(type="png"), elements)
                _cached_screenshot = screenshot
                _cached_fingerprint = _fingerprint
            except Exception as e:
                if _cached_screenshot is not None:
                    screenshot = _cached_screenshot  # fall back to cache on error
                else:
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
        if is_extract:
            prompt_parts.append(
                "EXTRACT OUTPUT FORMAT: this is an EXTRACT step — its result is stored "
                "in a variable and later typed verbatim into form fields and URLs. "
                "When you emit `done`, you MUST include TWO fields:\n"
                '  "value": the bare extracted token/name ONLY — no sentences, no '
                "quotes, no field names, no punctuation.\n"
                '  "evidence": a human-readable sentence proving where you read the '
                "value (this goes to the trace, not the variable).\n"
                'Example: {"thought": "sender name visible in feed", "action": "done", '
                '"value": "Rachel Torres", "evidence": "The From field shows Rachel '
                'Torres as the email sender."}'
            )
        if success_condition:
            prompt_parts.append(f"SUCCESS CONDITION: {success_condition}")
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
        if iteration == 1:
            if success_condition:
                prompt_parts.append(
                    "MANDATORY PRE-CHECK (RULE 10 — idempotency): This is your FIRST "
                    "observation for this step. Look at the current screenshot RIGHT NOW "
                    "and determine whether the SUCCESS CONDITION is already satisfied: "
                    f"{success_condition}\n"
                    "If YES — emit `done` immediately with the specific visible evidence. "
                    "Take NO action. Only proceed to act if the condition is clearly NOT "
                    "yet met on screen."
                )
            else:
                prompt_parts.append(
                    "MANDATORY STEP-ENTRY GOAL CHECK: This is your FIRST observation for "
                    "this step. Before choosing any action, examine the screenshot and "
                    "ELEMENTS carefully. If the step goal is already fully satisfied — "
                    "the target field already shows the correct value, the correct lookup "
                    "pill already exists, the record is already in the right state — emit "
                    "`done` immediately with specific visible evidence. Do NOT take any "
                    "action on a goal that is already met on screen."
                )
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
            # Transient image error — one fresh-screenshot retry.
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
                        error=f"LLM image error (bad frame — skipping iteration): {type(retry_err).__name__}: {retry_err}",
                        screenshot_ref=screenshot_ref,
                        latency_ms=int((time.monotonic() - iter_start) * 1000),
                    ))
                    continue  # re-observe on next iteration with a fresh screenshot
            # Transient network / API error — exponential backoff, 3 retries (1s/2s/4s).
            # Covers APIConnectionError, httpx timeouts, 429/502/503 from the backend proxy.
            elif _is_transient_llm_error(err_str):
                response = None
                _retry_err_str = err_str
                for _attempt in range(3):
                    _wait_s = 2 ** _attempt  # 1 s → 2 s → 4 s
                    log.warning(
                        "transient LLM error (attempt %d/3), retry in %ds: %s",
                        _attempt + 1, _wait_s, err_str[:200],
                    )
                    time.sleep(_wait_s)
                    try:
                        response = llm.generate(
                            prompt=prompt, system=SYSTEM_PROMPT,
                            images=[screenshot], json_mode=True, max_tokens=2048,
                        )
                        break
                    except Exception as _re:
                        _retry_err_str = str(_re)
                        if not _is_transient_llm_error(_retry_err_str):
                            break  # escalated to non-transient; stop retrying
                if response is None:
                    trace.append(LoopIteration(
                        iteration=iteration, action="(reason)",
                        error=f"LLM transient error (retries exhausted): {_retry_err_str}",
                        screenshot_ref=screenshot_ref,
                        latency_ms=int((time.monotonic() - iter_start) * 1000),
                    ))
                    continue  # skip iteration; next turn re-observes the page
            else:
                # Non-transient error (auth failure, bad request, etc.) — fail the step.
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
            # One corrective retry: demand ONLY minified JSON with a shorter
            # max_tokens budget so the response is less likely to be truncated.
            try:
                retry_prompt = (
                    prompt
                    + "\n\n=== JSON PARSE RETRY ===\n"
                    "Your last response could not be parsed as JSON. "
                    "Output ONLY a single minified JSON object — no prose, "
                    "no markdown, no code fences. Start immediately with { "
                    "and end with }. Keep \"thought\" to 5 words max. "
                    'Example: {"thought":"clicking Save","action":"click","ref":"sf0001"}'
                )
                response = llm.generate(
                    prompt=retry_prompt, system=SYSTEM_PROMPT,
                    images=[screenshot], json_mode=True, max_tokens=512,
                )
                action = parse_action(response.text)
                in_tok += getattr(response, "input_tokens", 0) or 0
                out_tok += getattr(response, "output_tokens", 0) or 0
            except Exception:
                action = None

        if action is None:
            # Include which fields were already set so the next iteration
            # doesn't re-fill them — the agent reads this from the trajectory.
            _done_fields = _extract_completed_fields(trace)
            _done_note = (
                f" FIELDS ALREADY SET THIS STEP (do NOT re-fill): "
                f"{', '.join(_done_fields)}." if _done_fields else ""
            )
            trace.append(LoopIteration(
                iteration=iteration, action="(unparseable)",
                observation=(
                    f"could not parse model output as JSON (after retry).{_done_note} "
                    f"On the next turn continue with remaining fields only."
                ),
                error="parse_failure", screenshot_ref=screenshot_ref,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            continue

        kind = action.get("action", "")
        # Repair: Claude sometimes emits {"action":"","click":"click","ref":"..."} —
        # the verb ends up as a top-level key with an empty "action" field.
        if not kind:
            _ACTION_VERBS = {
                "click", "click_text", "fill", "fill_field_by_label", "press",
                "navigate", "open_app", "scroll", "wait", "dismiss_obstruction",
                "captcha_detected", "done", "give_up",
            }
            for _k in action:
                if _k in _ACTION_VERBS:
                    kind = _k
                    break
        thought = action.get("thought", "")
        # Exclude the action kind key itself so repaired dicts don't leak it into args
        # (e.g. {"action":"","click":"click","ref":"..."} → args={"ref":"..."} not {"click":"click","ref":"..."})
        action_args = {k: v for k, v in action.items() if k not in ("action", "thought", kind)}

        # ---------- terminal actions ----------
        if kind == "done":
            trace.append(LoopIteration(
                iteration=iteration, thought=thought, action="done",
                action_args=action_args, observation=action.get("evidence", ""),
                screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            return _result("succeeded", action.get("evidence", ""), trace,
                           value=action.get("value"))

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

        # ---------- EMAIL / COMPOSE GUARD (mechanical, model cannot override) ----------
        # Opening the email composer is NEVER correct for tasks that only edit
        # fields and create Task records. If the step's intent doesn't involve
        # email, refuse any action whose target text matches email-UI patterns.
        # This fires on click_text (unlabeled buttons like "Email" in the feed)
        # and on click (grounded elements whose name contains email patterns).
        if "email" not in step_intent.lower() and kind in ("click", "click_text", "navigate"):
            _act_target = str(action.get("text", "")).lower()
            if kind == "click":
                _act_target += " " + str(action.get("ref", ""))
                # Supplement with grounded element name so the guard catches
                # ref-based clicks on the Email action button too.
                _ref_str = str(action.get("ref", ""))
                if elements is not None and _ref_str:
                    _gel = elements.by_ref(_ref_str) if hasattr(elements, "by_ref") else None
                    if _gel is not None:
                        _act_target += " " + (_gel.name or "").lower()
            if kind == "navigate":
                _act_target = str(action.get("url", "")).lower()
            _EMAIL_UI = (
                "email", "send email", "choose email addresses",
                "compose", "email address", "email message",
                "/email", "emailmessage",
            )
            if any(_pat in _act_target for _pat in _EMAIL_UI):
                _email_blocked = (
                    "BLOCKED: this action targets the email composer or email UI, which "
                    "is NOT part of this task. This task ONLY edits case fields (Contact "
                    "Name, Status) and creates a Task record — it NEVER sends, composes, "
                    "or interacts with email. The Comments field value in the task form is "
                    "text to TYPE into the form box, not an instruction to email anyone. "
                    "Abandon this approach. Go back to the planned action: use "
                    "fill_field_by_label for form fields, click the pencil edit icon if a "
                    "field is read-only, or navigate back to the case record."
                )
                trace.append(LoopIteration(
                    iteration=iteration, thought=thought, action=kind,
                    action_args=action_args, observation=_email_blocked,
                    error="blocked_email", screenshot_ref=screenshot_ref,
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

        # Stop-when-satisfied (mechanical): if the action itself returned a
        # definitive success observation confirming the step's goal value is
        # set, auto-succeed without another LLM round.  See RULE 2 in SYSTEM_PROMPT.
        if err is None and _action_satisfied_goal(observation, step_intent, kind):
            trace.append(LoopIteration(
                iteration=iteration, thought=thought, action=kind,
                action_args=action_args, observation=observation, error=None,
                screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=int((time.monotonic() - iter_start) * 1000),
            ))
            return _result("succeeded", f"[auto] {observation}", trace)

        trace.append(LoopIteration(
            iteration=iteration, thought=thought, action=kind,
            action_args=action_args, observation=observation, error=err,
            screenshot_ref=screenshot_ref, input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=int((time.monotonic() - iter_start) * 1000),
        ))

    return _result("failed", f"max iterations ({max_iterations}) exhausted", trace)


# ---------------------------------------------------------------------------
# Lookup-field helpers
# ---------------------------------------------------------------------------

def _poll_for_option(page: "Page", text: str, *, deadline_s: float = 3.0):
    """Poll up to *deadline_s* seconds for a REAL [role=option] matching *text*.

    Matching is exact-first with a contains fallback via
    _find_inline_dropdown_option, so the Advanced-Search escalation row
    ('"<text>" in <Object>') and the global search bar's suggestions can
    never be returned — clicking the escalation row opens the Advanced
    Search modal, which this agent must never do.
    Returns the matching Locator, or None."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            opt = _find_inline_dropdown_option(page, text, exclude_global_search=True)
        except Exception:
            opt = None
        if opt is not None:
            return opt
        time.sleep(0.3)
    return None


def _poll_for_inline_option(page: "Page", combo, text: str, *, deadline_s: float = 10.0):
    """Poll for the inline lookup dropdown option, scoped to *combo* first.

    Searching within the combobox element is more specific than a page-wide
    search — it avoids accidentally picking up global-search suggestions or
    options from other open dropdowns on the page.

    Falls back to a page-wide [role=option] search to catch Salesforce LWC
    options that are rendered outside the combobox host element (e.g. in a
    portal overlay anchored to the document body).

    Both passes match exact-first and can never return the Advanced-Search
    escalation row ('"<text>" in <Object>'); the page-wide pass also excludes
    the global search bar's suggestion panel.  While the SOQL query is slow
    the escalation row may be the ONLY visible row — that counts as still
    loading and the poll continues (observed SOQL latency exceeds 5 s, hence
    the 10 s default deadline).

    Returns the first visible matching Locator, or None on timeout.
    """
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        # 1. Combo-scoped: highest specificity — won't confuse global-search options.
        try:
            opt = _find_inline_dropdown_option(combo, text)
        except Exception:
            opt = None
        if opt is not None:
            return opt
        # 2. Page-wide fallback: catches SF LWC options rendered in a portal
        #    overlay outside the combobox DOM subtree.
        try:
            opt = _find_inline_dropdown_option(page, text, exclude_global_search=True)
        except Exception:
            opt = None
        if opt is not None:
            return opt
        time.sleep(0.25)
    return None


def _verify_lookup_pill(page: "Page") -> bool:
    """Return True when a Salesforce lookup field shows a selected-record pill.

    Salesforce renders a chosen lookup record as a 'pill' — a linked chip that
    replaces the search input.  We check three different DOM signals that can
    indicate a pill is present across different SF LWC component versions.
    """
    try:
        return bool(page.evaluate("""
            () => {
                if (document.querySelector('lightning-pill, .slds-pill')) return true;
                if (document.querySelector('[role="option"][aria-selected="true"]')) return true;
                if (document.querySelector(
                    '.slds-combobox.slds-has-selection, ' +
                    '.slds-combobox_container .slds-has-selection, ' +
                    '[data-selected-value]'
                )) return true;
                return false;
            }
        """))
    except Exception:
        return False


def _lookup_pill_has_value(page: "Page", text: str) -> bool:
    """Return True if a lookup pill showing *text* already exists on the page.

    Used as a pre-check: if the field is already correctly set, skip all search
    interaction so we don't disturb an existing pill value.
    """
    try:
        for loc in [page.locator("lightning-pill"), page.locator(".slds-pill")]:
            if loc.filter(has_text=text).count() > 0:
                return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SEQUENCE step primitives — deterministic sub-actions (no LLM involvement)
# ---------------------------------------------------------------------------

def check_sequence_condition(page: "Page", condition: str) -> bool:
    """Lightweight Playwright check for SEQUENCE step success conditions.

    Covers the two patterns used in inline-edit sequences:
      "shows '...' as a linked-record pill" → _lookup_pill_has_value
      "shows '...' in read-only view"        → visible text check
    Returns False (don't skip) for unknown patterns.
    """
    import re as _re
    m = _re.search(r"shows '([^']+)' as a linked-record pill", condition, _re.I)
    if m:
        return _lookup_pill_has_value(page, m.group(1))
    m = _re.search(r"shows '([^']+)' in read-only view", condition, _re.I)
    if m:
        val = m.group(1)
        try:
            return page.get_by_text(val, exact=True).first.is_visible(timeout=500)
        except Exception:
            return False
    return False


def execute_sequence_sub_action(page: "Page", kind: str, sub: dict) -> str:
    """Dispatch one SEQUENCE sub-action. Returns an observation string.
    A string starting with 'FAILED' signals the executor to abort the sequence."""
    dispatch = {
        "click_pencil_icon":      lambda: _seq_click_pencil_icon(page, sub.get("target", "")),
        "fill_field":             lambda: _seq_fill_field(page, sub.get("label", ""), sub.get("value", "")),
        "click_dropdown_result":  lambda: _seq_click_dropdown_result(page, sub.get("text", "")),
        "select_dropdown_option": lambda: _seq_select_dropdown_option(page, sub.get("label", ""), sub.get("value", "")),
        "click_save_footer":      lambda: _seq_click_save_footer(page),
    }
    fn = dispatch.get(kind)
    if fn is None:
        return f"FAILED: unknown sequence sub-action kind '{kind}'"
    return fn()


# The label of the field the last fill_field typed into — lets
# click_dropdown_result scope its option search to that same combobox
# instead of matching options anywhere on the page.
_LAST_SEQ_FILL: dict[str, str | None] = {"label": None}


def _seq_advanced_search_guard(page: "Page", sub_action: str) -> str | None:
    """The Advanced Search modal must never open during a SEQUENCE.

    If a dialog is visible, cancel it and return a FAILED observation naming
    the sub-action that was running; return None when no modal is present.
    """
    try:
        if not page.locator('[role="dialog"]').first.is_visible(timeout=150):
            return None
    except Exception:
        return None
    try:
        page.get_by_role("button", name="Cancel").first.click(timeout=1500)
        time.sleep(0.3)
    except Exception:
        pass
    return (
        f"FAILED: Advanced Search modal opened during {sub_action} — "
        f"modal cancelled; the sequence must never use Advanced Search"
    )


def _locate_field_edit_input(page: "Page", label: str, *, timeout_ms: int = 400):
    """Positively locate a field's edit input by role+name matching *label*.

    Returns the Locator (combobox preferred, textbox fallback) or None.
    Never returns an unverified/assumed-focus target.
    """
    for role in ("combobox", "textbox"):
        for exact in (True, False):
            try:
                el = page.get_by_role(role, name=label, exact=exact).first
                if el.is_visible(timeout=timeout_ms):
                    return el
            except Exception:
                pass
    return None


def _seq_click_pencil_icon(page: "Page", target_field: str) -> str:
    """Scroll to find + click the inline-edit pencil for target_field.

    SF renders inline-edit pencils as button[aria-label='Edit <Field>'].
    They may be off-screen; scroll toward the Details panel (up to 3 targeted
    scrolls) until found.  Success is NOT the click alone — after clicking,
    poll up to ~3 s until the field's edit input (combobox/textbox named
    target_field) actually exists and is visible.  This is the postcondition
    fill_field depends on: without it, keystrokes can land in the wrong input
    during the LWC inline-edit re-render.
    """
    if not target_field:
        return "FAILED: click_pencil_icon requires a target field name"
    for _scroll_attempt in range(4):
        for exact in (True, False):
            try:
                btn = page.get_by_role("button", name=f"Edit {target_field}", exact=exact).first
                if btn.is_visible(timeout=400):
                    btn.scroll_into_view_if_needed(timeout=1500)
                    btn.click(timeout=2000)
                    # Postcondition: the edit input must actually appear.
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline:
                        _modal = _seq_advanced_search_guard(page, "click_pencil_icon")
                        if _modal is not None:
                            return _modal
                        if _locate_field_edit_input(page, target_field, timeout_ms=150) is not None:
                            return (
                                f"clicked 'Edit {target_field}' pencil — edit input "
                                f"for '{target_field}' is present"
                            )
                        time.sleep(0.25)
                    return (
                        f"FAILED: clicked pencil but no edit input for "
                        f"'{target_field}' appeared"
                    )
            except Exception:
                pass
        if _scroll_attempt < 3:
            try:
                page.mouse.wheel(0, 350)
                time.sleep(0.35)
            except Exception:
                pass
    return f"FAILED: could not find 'Edit {target_field}' inline pencil after 3 scroll attempts"


def _seq_fill_field(page: "Page", label: str, value: str) -> str:
    """Type *value* into the field's own edit input using real keyboard events.

    The target input must be POSITIVELY located by role+name matching *label*
    and explicitly clicked before typing — there is NO focus fallback. Typing
    into whatever happens to hold focus is how keystrokes ended up in the
    global search bar during an LWC inline-edit re-render (confirmed trace).
    Uses keyboard.type() — NOT synthetic .fill() — so Salesforce SOQL fires.
    Never presses Enter. Does NOT click the dropdown result; that is
    click_dropdown_result's job.
    """
    if not label or not value:
        return f"FAILED: fill_field requires label and value (got {label!r}, {value!r})"
    _modal = _seq_advanced_search_guard(page, "fill_field")
    if _modal is not None:
        return _modal
    # keyboard.type() dispatches Enter for newline chars — strip them so no
    # Enter keystroke can ever fire from the typing path.
    value = " ".join(value.split())

    target = _locate_field_edit_input(page, label, timeout_ms=800)
    if target is None:
        return (
            f"FAILED: no input labeled {label!r} found — "
            f"pencil may not have opened edit mode"
        )
    # Hard guard: never type into the page-level global search bar, even if
    # role+name matching somehow resolved to it.
    if _is_global_search_bar(target):
        return (
            f"FAILED: the input matching {label!r} is the global Salesforce "
            f"search bar — refusing to type; re-enter inline edit mode first"
        )
    # Real focus on the located element (a combobox wraps its actual <input>).
    try:
        inner = target.locator("input").first
        inner.click(timeout=1200)
    except Exception:
        try:
            target.click(timeout=1500)
        except Exception as e:
            return (
                f"FAILED: could not click the input labeled {label!r} "
                f"({type(e).__name__}) — not typing blind"
            )
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    time.sleep(0.1)
    page.keyboard.type(value, delay=60)
    _modal = _seq_advanced_search_guard(page, "fill_field")
    if _modal is not None:
        return _modal
    _LAST_SEQ_FILL["label"] = label
    return f"typed {value!r} into the located input for {label!r}"


# Salesforce lookup dropdowns append a search-escalation row whose label looks
# like:  "rachel" in Contacts  /  "acme corp" in Accounts.  Clicking it opens
# the Advanced Search modal instead of setting the field.
_ESCALATION_ROW_RE = re.compile(
    r'^["\'“‘].*["\'”’]\s+in\s+\S+'
)


def _is_escalation_row(label: str) -> bool:
    """True if a dropdown option label is Salesforce's search-escalation row."""
    normalized = " ".join(label.split())
    return bool(_ESCALATION_ROW_RE.match(normalized)) or '" in ' in normalized


def _find_inline_dropdown_option(root, text: str, *, exclude_global_search: bool = False):
    """Return the [role=option] Locator under *root* matching *text*, or None.

    *root* is either a Page (page-wide search) or a Locator scoping the
    search to one combobox container. Matching rule: EXACT first (label ==
    text, case/whitespace-normalized); only then a contains-match fallback.
    The search-escalation row ('"<text>" in <Object>') is excluded outright —
    it is never clickable by this primitive because it opens the Advanced
    Search modal. With exclude_global_search=True (page-wide searches), any
    option living inside the global header — i.e. the global search bar's
    suggestion panel — is also excluded.
    """
    target = " ".join(text.split()).casefold()
    contains_match = None
    try:
        options = root.get_by_role("option").all()
    except Exception:
        return None
    for opt in options:
        try:
            if not opt.is_visible(timeout=100):
                continue
            label = opt.inner_text(timeout=300)
        except Exception:
            continue
        if _is_escalation_row(label):
            continue
        if exclude_global_search and _is_global_search_bar(opt):
            continue
        normalized = " ".join(label.split()).casefold()
        if normalized == target:
            return opt
        if contains_match is None and target in normalized:
            contains_match = opt
    return contains_match


def _seq_click_dropdown_result(page: "Page", text: str) -> str:
    """Wait for the inline lookup dropdown and click the matching option.

    This is the primitive that creates the linked-record PILL — the step that
    was missing from previous approaches.  It first waits (hard cap ~10 s)
    for the option list to STABILIZE — at least one REAL candidate (not the
    escalation row, not a loading placeholder), no listbox spinner, and the
    option set unchanged across two consecutive polls — so it can never
    click the transient recent-items panel while SOQL results are replacing
    it, and never treats an escalation-row-only loading state as settled.  Matching is exact-first with
    a contains fallback that never matches the Advanced-Search escalation
    row, scoped to the combobox that fill_field typed into; page-wide
    matching is reserved for detached-overlay listboxes and always excludes
    the global search bar's suggestion panel.  The option is re-located fresh
    immediately before clicking; after the click the pill is polled up to
    3 s, with ONE re-locate + re-click retry before failing.  Does NOT fall
    back to Advanced Search.  A click that does not produce a pill is a hard
    FAILURE: raw text without a pill always triggers Salesforce's "Select an
    option from the picklist" validation error, so Save can never succeed.
    """
    if not text:
        return "FAILED: click_dropdown_result requires text"
    # Scope the option poll to the combobox fill_field typed into (re-located
    # by role+name from the recorded label) so a dropdown spawned by a WRONG
    # input — e.g. the global search bar — can never satisfy this primitive.
    scope_label = _LAST_SEQ_FILL.get("label")

    def _locate_scope_combo():
        if not scope_label:
            return None
        for exact in (True, False):
            try:
                c = page.get_by_role("combobox", name=scope_label, exact=exact).first
                if c.is_visible(timeout=200):
                    return c
            except Exception:
                pass
        return None

    def _current_search_root():
        """Return (root, exclude_global): the scoped combobox when it renders
        its own options; otherwise the guarded page-wide fallback — ONLY for
        detached-overlay listboxes — which still excludes the escalation row
        and the global search bar's suggestion panel."""
        combo = _locate_scope_combo()
        if combo is not None:
            try:
                if combo.get_by_role("option").count() > 0:
                    return combo, False
            except Exception:
                pass
        return page, True

    def _visible_option_labels():
        """Snapshot the currently visible option labels (normalized) under
        the current search root — the stabilization fingerprint."""
        root, exclude_global = _current_search_root()
        labels: list[str] = []
        try:
            options = root.get_by_role("option").all()
        except Exception:
            return labels
        for opt in options:
            try:
                if not opt.is_visible(timeout=100):
                    continue
                lbl = opt.inner_text(timeout=300)
            except Exception:
                continue
            if exclude_global and _is_global_search_bar(opt):
                continue
            labels.append(" ".join(lbl.split()))
        return labels

    def _is_loading_label(label: str) -> bool:
        """True for loading/spinner placeholder rows rendered inside the listbox."""
        normalized = " ".join(label.split()).casefold().strip(".…… ")
        return (normalized == "" or normalized.startswith("loading")
                or normalized.startswith("searching"))

    def _has_real_candidate(labels: list[str]) -> bool:
        """A REAL candidate is an option that is neither the escalation row
        nor a loading placeholder — the only thing worth clicking."""
        return any(
            not _is_escalation_row(l) and not _is_loading_label(l) for l in labels
        )

    def _listbox_loading() -> bool:
        """True while a spinner/loading indicator is visible inside the listbox."""
        root, _ = _current_search_root()
        try:
            spinner = root.locator(
                '[role="listbox"] lightning-spinner, [role="listbox"] .slds-spinner'
            ).first
            return spinner.is_visible(timeout=100)
        except Exception:
            return False

    # --- STABILIZE: never click the transient recent-items panel. ----------
    # On focus, SF lookups instantly show a recent-items panel (which can
    # contain the target name from prior runs), then replace it with async
    # SOQL results. Clicking during that swap is a stale/detached-node click
    # that selects nothing. And while the SOQL query is still in flight,
    # Salesforce can render the escalation row ('"<text>" in <Object>')
    # ALONE and stable — that is a LOADING state, not a settled result list.
    # Settled means: at least one REAL candidate option (not the escalation
    # row, not a loading placeholder), no spinner in the listbox, and the
    # option set unchanged across two consecutive polls (~300 ms apart).
    # Hard cap ~10 s (observed SOQL latency has exceeded 4.7 s).
    stabilize_deadline = time.time() + 100.0
    prev_labels: list[str] | None = None
    while time.time() < stabilize_deadline:
        _modal = _seq_advanced_search_guard(page, "click_dropdown_result")
        if _modal is not None:
            return _modal
        labels = _visible_option_labels()
        if (_has_real_candidate(labels) and labels == prev_labels
                and not _listbox_loading()):
            break  # settled: real candidate present, identical across two polls
        prev_labels = labels
        time.sleep(0.3)
    else:
        if not _has_real_candidate(prev_labels or []):
            return (
                f"FAILED: no real result option appeared within 10s (only the "
                f"escalation row) — SOQL returned nothing or is too slow"
            )
        # Cap hit while real candidates exist but the list was still churning —
        # proceed best-effort; the fresh re-location below minimizes staleness.

    # --- MATCH + CLICK, with ONE retry if no pill forms. --------------------
    for attempt in (1, 2):
        _modal = _seq_advanced_search_guard(page, "click_dropdown_result")
        if _modal is not None:
            return _modal
        # Re-locate the option FRESH immediately before clicking — never
        # click a locator captured in an earlier poll cycle: the listbox may
        # have re-rendered and the old node be detached.
        root, exclude_global = _current_search_root()
        opt = _find_inline_dropdown_option(root, text, exclude_global_search=exclude_global)
        if opt is None:
            if attempt == 1:
                time.sleep(0.4)
                continue
            return (
                f"FAILED: settled dropdown has no option matching {text!r} "
                f"(escalation row excluded) — re-run fill_field and retry"
            )
        try:
            opt.click(timeout=2000)
        except Exception:
            if attempt == 1:
                continue  # list re-rendered mid-click; re-locate once and retry
            return (
                f"FAILED: clicked dropdown result for {text!r} but no pill "
                f"appeared — field is NOT set; clear and retry"
            )
        # Pill verification: poll up to 3 s (LWC pill render is slower than
        # the old fixed ~1.1 s check), early-exit on success.
        pill_deadline = time.time() + 100.0
        while time.time() < pill_deadline:
            if _verify_lookup_pill(page):
                suffix = " (after retry)" if attempt == 2 else ""
                return f"clicked inline dropdown result {text!r} — pill confirmed{suffix}"
            time.sleep(0.25)
        # No pill after 3 s — loop once more: re-locate and re-click.
    return (
        f"FAILED: clicked dropdown result for {text!r} but no pill "
        f"appeared — field is NOT set; clear and retry"
    )


def _seq_select_dropdown_option(page: "Page", label: str, value: str) -> str:
    """Open a Salesforce inline-edit picklist and select *value*.

    Works for Status, Priority, Type, and similar picklist fields.
    """
    if not label or not value:
        return "FAILED: select_dropdown_option requires label and value"
    for exact in (True, False):
        try:
            combo = page.get_by_role("combobox", name=label, exact=exact).first
            combo.click(timeout=3000)
            _wait_for_lwc_panel(page)
            for opt_exact in (True, False):
                try:
                    page.get_by_role("option", name=value, exact=opt_exact).first.click(timeout=2000)
                    return f"selected {value!r} from {label!r} picklist"
                except Exception:
                    pass
        except Exception:
            pass
    # Fallback: lightning-combobox sometimes exposes a button
    try:
        page.get_by_role("button", name=label).first.click(timeout=2000)
        _wait_for_lwc_panel(page)
        page.get_by_role("option", name=value, exact=True).first.click(timeout=2000)
        return f"selected {value!r} from {label!r} (button path)"
    except Exception:
        pass
    return f"FAILED: could not select {value!r} from {label!r} dropdown"


def _seq_click_save_footer(page: "Page") -> str:
    """Click the docked inline-edit footer Save button."""
    for exact in (True, False):
        try:
            btn = page.get_by_role("button", name="Save", exact=exact).first
            if btn.is_visible(timeout=1000):
                btn.click(timeout=2000)
                time.sleep(0.5)
                return "clicked Save in inline-edit footer"
        except Exception:
            pass
    try:
        page.get_by_text("Save", exact=True).first.click(timeout=2000)
        time.sleep(0.5)
        return "clicked Save (text fallback)"
    except Exception:
        pass
    return "FAILED: could not find Save button in inline-edit footer"


def _is_global_search_bar(loc) -> bool:
    """Return True if *loc* resolves to the page-level Salesforce search bar.

    The global search bar lives inside lightning-global-header / one-global-header.
    Typing into it triggers navigation to a record page, abandoning the current task.
    We block it in the `fill` handler to prevent catastrophic detours.
    """
    try:
        return bool(loc.evaluate("""
            el => {
                let node = el;
                while (node && node !== document.body) {
                    const tag = (node.tagName || '').toLowerCase();
                    if (tag === 'lightning-global-header' ||
                        tag === 'one-global-header' ||
                        tag === 'lightning-app-nav-bar') return true;
                    if (node.getAttribute && node.getAttribute('role') === 'banner')
                        return true;
                    node = node.parentElement || node.parentNode;
                }
                return false;
            }
        """))
    except Exception:
        return False


def _cancel_advanced_search_modal(page: "Page", search_term: str, label: str) -> str | None:
    """Cancel the Salesforce Advanced Search modal — NEVER operate it.

    Policy: record lookups are set ONLY via the field's own inline dropdown
    (type → wait for real SOQL results → click the exact option → pill).
    The Advanced Search modal navigates away from that flow and has produced
    wrong-record selections, so if it opened — e.g. from a stray click or an
    Enter that slipped through — it is cancelled immediately and the caller
    reports failure so the inline flow is retried on the field itself.

    Returns an observation string when a modal was found and cancelled,
    or None when no modal is present.
    """
    try:
        dialog = page.locator('[role="dialog"]').first
        if not dialog.is_visible(timeout=600):
            return None
    except Exception:
        return None

    # Confirm this looks like a lookup/search modal, not an unrelated dialog
    try:
        dialog_text = (dialog.inner_text(timeout=1000) or "").lower()
        if not any(w in dialog_text for w in ("search", "lookup", "find", "select")):
            return None
    except Exception:
        pass  # can't read text — proceed anyway, dialog IS visible

    try:
        page.get_by_role("button", name="Cancel", exact=False).first.click(timeout=2000)
        time.sleep(0.3)
    except Exception:
        pass
    return (
        f"FAILED: Advanced Search modal opened while setting {label!r} — modal "
        f"cancelled WITHOUT being used (this agent never operates Advanced "
        f"Search). Stay on the {label!r} field: re-issue fill_field_by_label "
        f"with {search_term!r} and select the inline dropdown option"
    )


def _validate_ref(ref: str, grounding) -> str | None:
    """Return an error observation string if ref is invalid, else None.

    Guards against three failure modes that produce silent Playwright timeouts:
      1. '#'-prefixed raw DOM ids (e.g. '#1370') — Claude occasionally emits these
         when it confuses element IDs with agent refs.
      2. Invented refs not present in the current ELEMENTS list.
      3. Stale refs from a prior observation (the page changed, ref numbering shifted).
    """
    if ref.startswith("#"):
        return (
            f"FAILED: '{ref}' has a '#' prefix — that is a raw DOM id, not an agent ref. "
            f"Agent refs are the alphanumeric tokens from ELEMENTS (e.g. 53e1c303, fi0001). "
            f"Pick a ref from the current ELEMENTS list or use click_text for visible text."
        )
    if grounding is not None:
        known = {e.ref for e in grounding.elements}
        if ref not in known:
            return (
                f"FAILED: ref '{ref}' is not in the current ELEMENTS — refs are only valid "
                f"for the observation they came from. Do not invent or reuse stale refs. "
                f"Pick a ref from ELEMENTS or use click_text for visible text."
            )
    return None


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _find_locator(page: "Page", ref: str):
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
        _ref_err = _validate_ref(ref, grounding)
        if _ref_err:
            return _ref_err
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

        # Pre-check: if a lookup pill already shows the target text, the field is
        # already correctly set — skip all interaction to avoid disturbing the pill.
        if text and _lookup_pill_has_value(page, text):
            return f"{label!r} already shows {text!r} as a selected pill — already set, no action needed"

        # Early modal check: if an Advanced Search modal is already open from a
        # prior action, cancel it — the underlying form is blocked while it is
        # open, and this agent never operates Advanced Search.
        if text:
            try:
                if page.locator('[role="dialog"]').is_visible(timeout=200):
                    _early_modal = _cancel_advanced_search_modal(page, text, label)
                    if _early_modal is not None:
                        return _early_modal
            except Exception:
                pass

        # --- textbox path (text inputs, date fields, textareas, and lookup inputs) ---
        # After a successful fill, poll briefly for an inline dropdown.  If one
        # appears this is a lookup field and we must click the option to create
        # the pill — a bare fill is not enough.
        # IMPORTANT: if no dropdown appears AND the element is inside a lookup/
        # combobox container, do NOT return "filled" — synthetic fill cannot
        # trigger SOQL; fall through to Path C which uses real keyboard events.
        _lookup_skip = False
        for exact in (True, False):
            if _lookup_skip:
                break
            for loc in (
                page.get_by_label(label, exact=exact),
                page.get_by_role("textbox", name=label, exact=exact),
            ):
                if _lookup_skip:
                    break
                try:
                    loc.first.fill(text, timeout=3000)
                    # Verify the field accepted the value (rejects silently cleared fields).
                    if text:
                        try:
                            actual = loc.first.evaluate(
                                "(el) => el.value != null ? String(el.value) : (el.innerText || el.textContent || '')"
                            )
                            if not str(actual).strip():
                                continue  # fill silently failed; try next locator
                        except Exception:
                            pass
                    # Poll briefly for an inline dropdown (indicates lookup field).
                    post_fill_opt = _poll_for_option(page, text, deadline_s=1.5)
                    if post_fill_opt is not None:
                        post_fill_opt.click(timeout=2000)
                        time.sleep(0.5)
                        if _verify_lookup_pill(page):
                            return f"selected lookup record {text!r} for {label!r} (pill confirmed)"
                        return f"filled {label!r} with {text!r} and selected from dropdown"
                    # No dropdown appeared after synthetic fill.
                    # Check if the element is nested inside a Salesforce lookup /
                    # combobox container — if so, synthetic fill cannot trigger SOQL
                    # and leaving raw text here will cause a "select an option" save
                    # error.  Signal to fall through to the combobox / Path C path
                    # that uses real keyboard events to fire the SOQL search.
                    try:
                        _in_lookup = loc.first.evaluate("""
                            el => {
                                let node = el;
                                while (node && node !== document.body) {
                                    const role = (node.getAttribute &&
                                                  node.getAttribute('role')) || '';
                                    if (role === 'combobox') return true;
                                    const tag = (node.tagName || '').toLowerCase();
                                    if (tag === 'lightning-lookup' ||
                                        tag === 'force-lookup' ||
                                        tag === 'lightning-base-combobox' ||
                                        tag === 'lightning-grouped-combobox') return true;
                                    node = node.parentElement || node.parentNode;
                                }
                                return false;
                            }
                        """)
                        if _in_lookup:
                            _lookup_skip = True
                            break  # fall through to combobox / Path C
                    except Exception:
                        pass
                    return f"filled {label!r} with {text!r}"
                except Exception:
                    pass

        # --- combobox / select path (picklists, Subject, and record lookups) ---

        # Pre-check: options may already be visible from a prior "Edit X" click.
        # Re-clicking the combobox would CLOSE an already-open dropdown — pick directly.
        for opt_exact in (True, False):
            try:
                page.get_by_role("option", name=text, exact=opt_exact).first.click(timeout=1000)
                return f"selected {text!r} (dropdown was already open)"
            except Exception:
                pass

        for exact in (True, False):
            try:
                combo = page.get_by_role("combobox", name=label, exact=exact).first
                combo.click(timeout=3000)
                _wait_for_lwc_panel(page)

                # Path A: picklist — static options already loaded after click
                for opt_exact in (True, False):
                    try:
                        page.get_by_role("option", name=text, exact=opt_exact).first.click(timeout=2000)
                        return f"selected {text!r} in {label!r}"
                    except Exception:
                        pass

                # Path B: Subject-style type-to-filter — fast local filtering
                try:
                    combo.fill(text, timeout=2000)
                    time.sleep(0.4)
                    page.get_by_role("option", name=text, exact=True).first.click(timeout=2000)
                    return f"typed and selected {text!r} in {label!r}"
                except Exception:
                    pass

                # Path C: record-lookup — real keyboard events (keydown/keyup) to fire
                # the async SOQL search. combo.fill() sends synthetic events that SF
                # lookup components may ignore; keyboard.type() dispatches real ones.
                # CLEAR using Control+a + Delete (real keyboard events, not synthetic
                # .fill("") which can disturb SF's LWC state and suppress SOQL).
                try:
                    try:
                        inner = combo.locator("input").first
                        inner.click(timeout=1500)
                    except Exception:
                        try:
                            combo.click(timeout=2000)
                        except Exception:
                            pass
                    page.keyboard.press("Control+a")   # select all existing text
                    page.keyboard.press("Delete")       # delete it (real key events)
                    time.sleep(0.1)
                    page.keyboard.type(text, delay=60)  # char-by-char → SOQL fires
                    # Poll within the combobox scope first to avoid picking up
                    # global-search suggestions or other page-level options.
                    # Only real inline results match — the escalation row is
                    # excluded, so a slow SOQL keeps polling (up to 10 s)
                    # instead of clicking into Advanced Search.
                    option_loc = _poll_for_inline_option(page, combo, text, deadline_s=10.0)
                    if option_loc is None:
                        # If an Advanced Search modal opened anyway, cancel it
                        # and report — the modal is never operated; the inline
                        # flow on the field itself is the only allowed path.
                        _adv = _cancel_advanced_search_modal(page, text, label)
                        if _adv is not None:
                            return _adv
                        return (
                            f"typed {text!r} into lookup field {label!r} but no real "
                            f"inline result appeared after 10s — record may not exist or "
                            f"spelling is wrong; do NOT use global search as a substitute"
                        )
                    option_loc.click(timeout=2000)
                    time.sleep(0.6)
                    if _verify_lookup_pill(page):
                        return f"selected lookup record {text!r} in {label!r} (pill confirmed)"
                    return (
                        f"typed {text!r} into lookup {label!r} and clicked a result "
                        f"but no linked-record pill appeared — field NOT set; do NOT Save"
                    )
                except Exception:
                    pass

                return (f"opened {label!r} combobox but could not select {text!r} "
                        f"— check available options or use click_text on the option")
            except Exception:
                pass

        return f"FAILED: could not find a field with label {label!r}"

    if kind == "fill":
        ref = str(action["ref"])
        _ref_err = _validate_ref(ref, grounding)
        if _ref_err:
            return _ref_err
        text = str(action.get("text", ""))
        loc = _find_locator(page, ref)
        # Guard: block the page-level global search bar — typing into it navigates
        # away from the current record, abandoning all in-progress form work.
        if _is_global_search_bar(loc):
            return (
                "BLOCKED: that element is the global Salesforce search bar. "
                "Filling it navigates to a different record and abandons the current task. "
                "To set a lookup/contact field, use fill_field_by_label with the field's label."
            )
        loc.fill(text, timeout=2500)
        # Verify the field actually accepted the value — a silent empty result
        # means the fill failed (field rejected it or is the wrong element type).
        if text:
            try:
                actual = loc.evaluate(
                    "(el) => el.value != null ? String(el.value) : (el.innerText || el.textContent || '')"
                )
                if not str(actual).strip():
                    return (
                        f"FAILED: field #{ref} is still empty after fill (expected {text!r}) "
                        f"— the element may not accept direct fill; try fill_field_by_label instead"
                    )
            except Exception:
                pass
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
        # '#'-prefixed refs are invalid but don't need full ELEMENTS check here —
        # dismiss_obstruction is often called on elements that may not be grounded.
        if ref.startswith("#"):
            return (
                f"FAILED: '{ref}' has a '#' prefix — not a valid agent ref. "
                f"Pick the ref of the modal's close button from ELEMENTS."
            )
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
    value: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "evidence": evidence,
        "pause_reason": pause_reason,
        "trace": trace,
        "quota_exhausted": quota_exhausted,
        # Extract steps: the bare extracted value from the done action.
        # The executor stores THIS in the variable, never the evidence prose.
        "value": value,
    }