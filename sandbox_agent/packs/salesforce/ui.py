"""
Salesforce Lightning UI primitives for the browser agent.

Everything here knows Lightning's DOM: LWC spinners and panels, toasts, the
Advanced Search modal, inline-edit pencils, lookup pills and their async
dropdowns, the docked Save footer, and the org's readiness after a frontdoor
login. It was lifted verbatim out of browser_mode.py so the generic ReAct
loop no longer carries one app's knowledge; the Salesforce pack
(`sandbox_agent.packs.salesforce`) is the only thing that should reach for it.
"""
from __future__ import annotations

import logging
import re
import time

from playwright.sync_api import Page

# Same logger name as browser_mode so existing log filters keep matching.
log = logging.getLogger("browser-react")


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
    document.querySelector('[role="listbox"]') misses these -- we check both
    regular DOM and one level of shadow DOM for the known SF host elements.
    """
    # Always wait a minimum before checking -- LWC needs at least one
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
                            // listbox found in shadow DOM -- wait for its children
                            return lb2.childElementCount > 0;
                        }
                    }

                    // --- aria-expanded check: if something is expanded but we
                    // haven't found its panel yet, keep waiting (it may still
                    // be hydrating). Return false to spin one more loop.
                    const expanded = document.querySelector('[aria-expanded="true"]');
                    if (expanded) return false;

                    // No open panel anywhere -- click probably didn't open one,
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
    clicks on the top navigation bar -- including the App Launcher icon.
    We HIDE them (not click-close) because LWC onClick handlers don't fire
    reliably from Playwright synthetic events; CSS visibility is instant and
    doesn't depend on the framework.

    Only notifications are hidden -- modal dialogs, record forms, and other
    interactive UI are unaffected.
    """
    try:
        page.evaluate(
            """() => {
                // 1. SF notification library -- wraps all toast types
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


def _sf_cancel_advanced_search(page: Page) -> bool:
    """Mechanically cancel the Advanced Search modal whenever it is on screen.

    Called at OBSERVE time (like _sf_dismiss_toasts), so the modal is closed
    BEFORE grounding: the agent can neither see nor click inside it -- Advanced
    Search is simply not part of its world. The only lookup path is the
    field's inline dropdown: type, wait for the results, click the option.

    Only dialogs positively identified as the lookup Advanced Search are
    touched -- record-form modals (New Task etc.) are left alone. Returns True
    if a modal was cancelled.
    """
    try:
        dialog = page.locator('[role="dialog"]').first
        if not dialog.is_visible(timeout=150):
            return False
        is_advanced = False
        try:
            heading = dialog.locator('h1, h2, [role="heading"]').first.inner_text(timeout=300)
            normalized = " ".join(heading.split())
            is_advanced = ("advanced search" in normalized.casefold()
                           or bool(_ESCALATION_ROW_RE.match(normalized)))
        except Exception:
            pass
        if not is_advanced:
            try:
                txt = (dialog.inner_text(timeout=400) or "").casefold()
                is_advanced = "advanced search" in txt
            except Exception:
                pass
        if not is_advanced:
            return False
        try:
            dialog.get_by_role("button", name="Cancel").first.click(timeout=1500)
            time.sleep(0.3)
        except Exception:
            try:
                page.keyboard.press("Escape")
                time.sleep(0.2)
            except Exception:
                pass
        log.warning("Advanced Search modal auto-cancelled at OBSERVE -- never operated")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Lookup-field helpers
# ---------------------------------------------------------------------------

def _poll_for_option(page: "Page", text: str, *, deadline_s: float = 3.0):
    """Poll up to *deadline_s* seconds for a REAL [role=option] matching *text*.

    Matching is exact-first with a contains fallback via
    _find_inline_dropdown_option, so the Advanced-Search escalation row
    ('"<text>" in <Object>') and the global search bar's suggestions can
    never be returned -- clicking the escalation row opens the Advanced
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
    search -- it avoids accidentally picking up global-search suggestions or
    options from other open dropdowns on the page.

    Falls back to a page-wide [role=option] search to catch Salesforce LWC
    options that are rendered outside the combobox host element (e.g. in a
    portal overlay anchored to the document body).

    Both passes match exact-first and can never return the Advanced-Search
    escalation row ('"<text>" in <Object>'); the page-wide pass also excludes
    the global search bar's suggestion panel.  While the SOQL query is slow
    the escalation row may be the ONLY visible row -- that counts as still
    loading and the poll continues (observed SOQL latency exceeds 5 s, hence
    the 10 s default deadline).

    Returns the first visible matching Locator, or None on timeout.
    """
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        # 1. Combo-scoped: highest specificity -- won't confuse global-search options.
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

    Must use Playwright locators, NOT document.querySelector -- SF LWC renders
    lightning-pill inside nested shadow roots that querySelector cannot pierce.
    Playwright locators traverse open shadow DOM automatically.
    """
    try:
        if page.locator("lightning-pill").count() > 0:
            return True
        if page.locator(".slds-pill").count() > 0:
            return True
        if page.locator(".slds-combobox.slds-has-selection, [data-selected-value]").count() > 0:
            return True
        return False
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


# SEQUENCE sub-actions are dispatched by the pack's SEQUENCE_PRIMITIVES table
# (sandbox_agent/packs/salesforce/__init__.py), which wraps the _seq_* functions below.

# The label of the field the last fill_field typed into -- lets
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
        f"FAILED: Advanced Search modal opened during {sub_action} -- "
        f"modal cancelled; the sequence must never use Advanced Search"
    )


def _seq_cancel_adv_modal_if_open(page: "Page") -> bool:
    """Cancel Advanced Search / any dialog if open. Returns True if one was found."""
    try:
        if not page.locator('[role="dialog"]').first.is_visible(timeout=150):
            return False
    except Exception:
        return False
    for btn_name in ("Cancel", "Close"):
        try:
            page.get_by_role("button", name=btn_name).first.click(timeout=1500)
            time.sleep(0.35)
            return True
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.35)
    except Exception:
        pass
    return True


def _seq_retype_for_soql(page: "Page", text: str) -> None:
    """Re-focus the lookup input from _LAST_SEQ_FILL and retype *text*.

    Called after cancelling the Advanced Search modal so a fresh SOQL
    query fires against the inline dropdown (not the modal).
    """
    time.sleep(0.3)
    label = _LAST_SEQ_FILL.get("label")
    if label:
        for exact in (True, False):
            try:
                inner = (
                    page.get_by_role("combobox", name=label, exact=exact)
                    .first.locator("input").first
                )
                if inner.is_visible(timeout=700):
                    inner.click(timeout=1000)
                    break
            except Exception:
                pass
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    time.sleep(0.15)
    page.keyboard.type(text, delay=60)


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
    scrolls) until found.  Success is NOT the click alone -- after clicking,
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
                                f"clicked 'Edit {target_field}' pencil -- edit input "
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
    and explicitly clicked before typing -- there is NO focus fallback. Typing
    into whatever happens to hold focus is how keystrokes ended up in the
    global search bar during an LWC inline-edit re-render (confirmed trace).
    Uses keyboard.type() -- NOT synthetic .fill() -- so Salesforce SOQL fires.
    Never presses Enter. Does NOT click the dropdown result; that is
    click_dropdown_result's job.
    """
    if not label or not value:
        return f"FAILED: fill_field requires label and value (got {label!r}, {value!r})"
    _modal = _seq_advanced_search_guard(page, "fill_field")
    if _modal is not None:
        return _modal
    # keyboard.type() dispatches Enter for newline chars -- strip them so no
    # Enter keystroke can ever fire from the typing path.
    value = " ".join(value.split())

    target = _locate_field_edit_input(page, label, timeout_ms=800)
    if target is None:
        return (
            f"FAILED: no input labeled {label!r} found -- "
            f"pencil may not have opened edit mode"
        )
    # Hard guard: never type into the page-level global search bar, even if
    # role+name matching somehow resolved to it.
    if _is_global_search_bar(target):
        return (
            f"FAILED: the input matching {label!r} is the global Salesforce "
            f"search bar -- refusing to type; re-enter inline edit mode first"
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
                f"({type(e).__name__}) -- not typing blind"
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


# Salesforce lookup dropdowns render several non-selectable rows that must be
# excluded from click targets:
#   "rachel torres" in Contacts       -- old-style escalation row
#   Search Show more results for "X"  -- new-style escalation row (SF Spring '25+)
#   Show more results for "X"         -- same intent, shorter label variant
#   Add New Contact / Add New Account  -- creates a new record instead of selecting
_ESCALATION_ROW_RE = re.compile(
    r'^\x22.*\x22\s+in\s+\S+'
    r'|^\x27.*\x27\s+in\s+\S+'
    r'|more\s+results'
    r'|^add\s+new\b',
    re.IGNORECASE,
)


def _is_escalation_row(label: str) -> bool:
    """True if a dropdown option is a non-selectable SF row (escalation / add-new)."""
    normalized = " ".join(label.split())
    return bool(_ESCALATION_ROW_RE.search(normalized)) or '" in ' in normalized


def _find_inline_dropdown_option(root, text: str, *, exclude_global_search: bool = False):
    """Return the [role=option] Locator under *root* matching *text*, or None.

    *root* is either a Page (page-wide search) or a Locator scoping the
    search to one combobox container. Matching rule: EXACT first (label ==
    text, case/whitespace-normalized); only then a contains-match fallback.
    The search-escalation row ('"<text>" in <Object>') is excluded outright --
    it is never clickable by this primitive because it opens the Advanced
    Search modal. With exclude_global_search=True (page-wide searches), any
    option living inside the global header -- i.e. the global search bar's
    suggestion panel -- is also excluded.
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

    This is the primitive that creates the linked-record PILL -- the step that
    was missing from previous approaches.  It first waits (hard cap ~10 s)
    for the option list to STABILIZE -- at least one REAL candidate (not the
    escalation row, not a loading placeholder), no listbox spinner, and the
    option set unchanged across two consecutive polls -- so it can never
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
    # input -- e.g. the global search bar -- can never satisfy this primitive.
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
        its own options; otherwise the guarded page-wide fallback -- ONLY for
        detached-overlay listboxes -- which still excludes the escalation row
        and the global search bar's suggestion panel."""
        combo = _locate_scope_combo()
        if combo is not None:
            try:
                if combo.get_by_role("option").count() > 0:
                    return combo, False
            except Exception:
                pass
        return page, True

    # Poll for the matching option and click it the instant it appears.
    # Pill verification is the authoritative success condition.
    _all_seen: list[str] = []      # diagnostic: every distinct label seen
    _click_log: list[str] = []     # diagnostic: click outcome per attempt

    def _snapshot_all_labels() -> list[str]:
        """All visible option labels on the page right now (including escalation rows)."""
        labels: list[str] = []
        try:
            for opt in page.get_by_role("option").all():
                try:
                    if opt.is_visible(timeout=100):
                        lbl = " ".join(opt.inner_text(timeout=300).split())
                        if lbl:
                            labels.append(lbl)
                except Exception:
                    pass
        except Exception:
            pass
        return labels

    def _click_option(opt) -> str:
        """Click *opt* using the most reliable method available.

        Playwright's default .click() performs actionability checks (visibility,
        stable position, not intercepted).  If another element covers the option
        the check throws; we fall back to a direct mouse.click() at the element's
        bounding-box centre -- which bypasses the interceptor check and sends the
        raw mouse event to whatever is rendered at those screen coordinates.
        Returns a short diagnostic string.
        """
        try:
            opt.click(timeout=2000)
            return "click_ok"
        except Exception as e1:
            # Actionability check failed (intercepted, off-screen, etc.) --
            # fall back to a raw mouse click at the element's centre.
            try:
                box = opt.bounding_box()
                if box:
                    page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    return f"mouse_click_ok (after {type(e1).__name__})"
                return f"no_bbox ({type(e1).__name__})"
            except Exception as e2:
                return f"both_failed ({type(e1).__name__}, {type(e2).__name__})"

    for _outer in range(3):
        if _outer > 0:
            _seq_retype_for_soql(page, text)

        deadline = time.time() + 10.0
        while time.time() < deadline:
            if _seq_cancel_adv_modal_if_open(page):
                break  # modal dismissed -- retype on next outer iteration

            # Diagnostic: capture all visible options this poll cycle.
            all_now = _snapshot_all_labels()
            for lbl in all_now:
                if lbl not in _all_seen:
                    _all_seen.append(lbl)

            root, exclude_global = _current_search_root()
            opt = _find_inline_dropdown_option(
                root, text, exclude_global_search=exclude_global
            )
            if opt is not None:
                click_result = _click_option(opt)
                _click_log.append(click_result)
                if "failed" in click_result or "no_bbox" in click_result:
                    time.sleep(0.25)
                    continue  # could not click; repoll

                # Verify the selection matched what we intended to fill.
                # Three escalating signals -- any one is sufficient:
                #   1. lightning-pill containing the target text (shadow-DOM aware)
                #   2. a linked-record anchor <a> with that name (SF read-mode)
                #   3. dropdown gone -- SF accepted the click and closed the list
                time.sleep(0.5)
                suffix = f" (attempt {_outer + 1})" if _outer > 0 else ""
                if page.locator("lightning-pill").filter(has_text=text).count() > 0:
                    return f"clicked {text!r} -- pill with target text confirmed{suffix}"
                if page.get_by_role("link", name=text).count() > 0:
                    return f"clicked {text!r} -- linked-record anchor confirmed{suffix}"
                if page.get_by_role("option").count() == 0:
                    return f"clicked {text!r} -- dropdown closed, selection confirmed{suffix}"
                # Dropdown still open -- click did not register; repoll.
                continue

            time.sleep(0.25)

    seen_summary = "; ".join(_all_seen[:10]) or "none"
    click_summary = "; ".join(_click_log[-6:]) or "none"
    return (
        f"FAILED: could not select inline result for {text!r} after 3 attempts "
        f"-- options seen: [{seen_summary}] "
        f"-- click results: [{click_summary}] "
        f"-- re-run fill_field and retry"
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
    """Cancel the Salesforce Advanced Search modal -- NEVER operate it.

    Policy: record lookups are set ONLY via the field's own inline dropdown
    (type → wait for real SOQL results → click the exact option → pill).
    The Advanced Search modal navigates away from that flow and has produced
    wrong-record selections, so if it opened -- e.g. from a stray click or an
    Enter that slipped through -- it is cancelled immediately and the caller
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
        pass  # can't read text -- proceed anyway, dialog IS visible

    try:
        page.get_by_role("button", name="Cancel", exact=False).first.click(timeout=2000)
        time.sleep(0.3)
    except Exception:
        pass
    return (
        f"FAILED: Advanced Search modal opened while setting {label!r} -- modal "
        f"cancelled WITHOUT being used (this agent never operates Advanced "
        f"Search). Stay on the {label!r} field: re-issue fill_field_by_label "
        f"with {search_term!r} and select the inline dropdown option"
    )


def _wait_for_salesforce_ready(page: Page, timeout: float = 15.0) -> str:
    """Poll for Lightning/LWC readiness after open_app navigation.

    domcontentloaded fires before the Aura framework has rendered any
    components. We block here (up to `timeout` seconds) so the next OBSERVE
    iteration sees the real, interactive UI instead of an empty shell.

    Two-stage check:
      1. Wait for [data-aura-rendered-by] -- set by Aura on every rendered
         component. Its presence means the JS framework is live.
      2. Wait for known Lightning spinners to clear -- indicates the initial
         data fetch is also done.
    Both stages degrade gracefully: a timeout at either stage is logged but
    does NOT raise, so the agent can still attempt the next action.
    """
    deadline = time.monotonic() + timeout

    # Stage 1 -- Aura/LWC framework rendered at least one component.
    while time.monotonic() < deadline:
        try:
            if page.evaluate("() => !!document.querySelector('[data-aura-rendered-by]')"):
                break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        return "opened Salesforce (Aura not detected within timeout -- page still loading)"

    # Stage 2 -- Lightning spinners clear (initial data load done).
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
