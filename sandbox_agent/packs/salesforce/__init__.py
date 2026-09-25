"""
Salesforce Lightning pack.

Owns everything the agent knows specifically about Salesforce: the prompt
rules for Lightning's UI (inline edit, lookups, Advanced Search, console
tabs, URL patterns), the page hooks run before every observation, the
Lightning spinner wait, the deterministic SEQUENCE primitives, and the hosts
a Lightning session legitimately moves between.

The backend mirrors `SEQUENCE_PRIMITIVES.keys()` in
`backend/app/packs/salesforce.py` so a plan's capability check knows what
this pack can do; a test keeps the two lists in sync.
"""
from __future__ import annotations

from sandbox_agent.packs.base import AppPack
from sandbox_agent.packs.salesforce import ui

# Rule bullets spliced into the agent's "Rules:" list for Salesforce plans.
PROMPT_RULES = """  - INLINE-EDIT PICKLIST (Case Status, Priority, Type, or any picklist on a record page): the correct FIRST action is always `fill_field_by_label` with label="<field label>" and text="<target value>" (e.g. label="Status", text="Escalated"). Do NOT use `click_text` on the current field value (e.g. "New"), the label text (e.g. "Status"), or the asterisk-prefixed label ("*Status") -- those waste many iterations. fill_field_by_label opens the picklist and selects the option in one call. Never attempt click_text for a picklist field.
  - RECORD LOOKUP fields (Contact Name, Account Name, Owner, any field where you type a name and Salesforce searches for matching records) work differently from picklists. Typing text is NOT enough -- Salesforce makes an async search and you must SELECT the matching record from the results list so it becomes a linked pill (a chip showing the record name). `fill_field_by_label` handles this automatically via its lookup path. The field is only set when the pill is visible. CRITICAL: if fill_field_by_label returns "no linked-record pill is visible" or "no matching record appeared", the field is NOT set -- do NOT click Save and do NOT emit `done`. The pill must be visible before saving is valid.
  - ADVANCED SEARCH DOES NOT EXIST FOR YOU: the Advanced Search modal is NOT one of your options -- the executor mechanically prevents every route into it and auto-cancels it on sight before you even observe the page. Record lookups have exactly ONE flow: type the name into the field's own input (`fill_field_by_label`), WAIT for the inline dropdown results to appear (slow searches can take up to ~10 seconds -- the tool waits for you), then the exact matching option is clicked and the pill confirmed. Enforcement you will hit if you deviate: the dropdown row shaped like '"<your text>" in Contacts' and any 'Show all results' entry are BLOCKED for click/click_text (they open Advanced Search); pressing Enter inside a lookup is BLOCKED (it opens Advanced Search); if the modal appears anyway it is cancelled automatically. When a lookup observation reports FAILED, the ONLY correct move is to re-issue `fill_field_by_label` on the SAME field and wait -- never click into a modal, never press Enter, never use the global search bar.
  - LOOKUP ALREADY SET: Before trying to fill a lookup field (Contact Name, Account, etc.), check whether the correct value is already showing as a pill. If fill_field_by_label returns "already shows … as a selected pill -- already set", the field is done -- do NOT re-search, do NOT click the field again. Move on to the next step.
  - ABSOLUTE PROHIBITION -- NEVER open the email composer. When a step goal says fill fields like "Contact Name", "Status", or create a Task, the correct actions are fill_field_by_label and click on the task form -- NEVER clicking "Email", "Send Email", or any Feed email action. If a MODAL FORM section in the GOAL lists a field called "Comments" with a value like "talk to Rachel Torres and investigate the issue", that is text to TYPE into the Comments form field -- it is NOT an instruction to email or contact Rachel Torres. The executor blocks email actions mechanically; attempting them wastes iterations. Do NOT click anything labeled Email/Send Email/Compose while editing a record.
  - ABSOLUTE PROHIBITION -- NEVER use the global/header search bar to fill a form field. The global search input at the top of every Salesforce page (aria-label "Search" / "Search Salesforce") navigates to a completely different record page and immediately abandons your current task and all in-progress form work. It is NEVER a substitute for a lookup field. `fill_field_by_label` targets the field's own search input inside the current form. If fill_field_by_label reports failure for a lookup, try once more with a simpler search term, then emit `give_up` -- NEVER touch the global search bar. The `fill` action will BLOCK itself if you attempt this.
  - WRONG-RECORD RECOVERY: After every action that could cause navigation (clicking a search result, a link, a tab), verify you are still on the correct record for the current step. If the URL or page heading shows you are on a DIFFERENT record type (e.g. you are on a Contact page when the task step is about a Case), STOP immediately -- do not fill any more fields. Navigate back to the correct record using `navigate` with the case list URL, then re-open the target case by clicking its row link.
  - STRONGLY prefer `navigate` to a direct URL over clicking through menus, app launchers, or list-view pickers. If a URL reaches the target in one step, use it -- do NOT waste turns clicking App Launcher → searching → clicking again. Salesforce URL patterns:
      Object list (with filter): /lightning/o/<Object>/list?filterName=<FilterApiName>
      Specific record (by ID):   /lightning/r/<Object>/<RecordId>/view
      New-record form (modal):   /lightning/o/<Object>/new
    Reserve click/fill for actions that genuinely have NO URL equivalent: creating or editing records in modals, changing field values in forms, clicking buttons inside a record. Do NOT click the App Launcher or a list-view dropdown when `navigate` to a URL reaches the same destination.
  - CONSOLE WORKSPACE TABS (biggest time-sink -- read carefully): In Salesforce Console, clicking a case row opens a NEW workspace tab for that record alongside the list tab. Once the record tab is open (you can see the case number or case title in the top tab bar), you ARE on the record -- do NOT click the list tab. Clicking the list tab navigates you BACKWARD to the list, abandoning all in-progress work on the record. If you see BOTH a list tab AND a record tab in the header, the record IS already open -- proceed with your task on the record (edit fields, click Related, etc.). Do NOT click "back" to the list, do NOT click the list tab at any point during case processing. The only step that navigates back to the list is the explicit navigate step at the end of the loop body -- all other mid-task navigation to the list is WRONG and wastes time.
  - After an INLINE EDIT on a Salesforce record field (editing a field directly on the record page, not inside a modal), a docked form footer appears at the bottom of the page with Save and Cancel buttons. These are grounded in ELEMENTS as button 'Save' and button 'Cancel' (look in [FOOTER] or [MAIN] sections). Click their ref directly. If no ref appears for Save, use `click_text` with text "Save" -- the footer is docked and always visible at the bottom; do NOT scroll to find it."""

HOST_SUFFIXES = (
    ".salesforce.com", ".force.com", ".salesforce-setup.com", ".visualforce.com",
)


def _before_observe(page) -> None:
    ui._sf_dismiss_toasts(page)          # hide toasts BEFORE grounding so they don't intercept clicks
    ui._sf_cancel_advanced_search(page)  # Advanced Search is never operated -- cancel on sight


SEQUENCE_PRIMITIVES = {
    "click_pencil_icon":      lambda page, sub: ui._seq_click_pencil_icon(page, sub.get("target", "")),
    "fill_field":             lambda page, sub: ui._seq_fill_field(page, sub.get("label", ""), sub.get("value", "")),
    "click_dropdown_result":  lambda page, sub: ui._seq_click_dropdown_result(page, sub.get("text", "")),
    "select_dropdown_option": lambda page, sub: ui._seq_select_dropdown_option(page, sub.get("label", ""), sub.get("value", "")),
    "click_save_footer":      lambda page, sub: ui._seq_click_save_footer(page),
}

SALESFORCE_PACK = AppPack(
    name="salesforce",
    display_name="Salesforce",
    host_suffixes=HOST_SUFFIXES,
    prompt_rules=PROMPT_RULES,
    before_observe=_before_observe,
    settle=ui._wait_for_lwc_spinners,
    sequence_primitives=SEQUENCE_PRIMITIVES,
    check_condition=ui.check_sequence_condition,
    frontdoor_provider="salesforce",
)
