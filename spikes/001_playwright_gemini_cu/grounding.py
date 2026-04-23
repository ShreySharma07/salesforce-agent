"""
Grounding for the manual agent loop.

Primary source: DOM-based extraction via Playwright's evaluate() — queries
live interactive elements directly from the page. This is more reliable
than accessibility.snapshot() which can return empty on simple pages or
when the accessibility tree hasn't been populated yet.

Each interactive element gets a stable integer ref. The model emits refs
in its actions; we resolve them back to Playwright locators at exec time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, Locator


# JS snippet that runs in the page and returns a list of interactive elements.
# Traverses shadow DOM roots recursively. Each element gets a data-agent-ref
# attribute so we can locate it later regardless of DOM changes.
EXTRACT_JS = r"""
() => {
  const SELECTORS = [
    'a[href]',
    'button',
    'input:not([type="hidden"])',
    'select',
    'textarea',
    '[role="button"]',
    '[role="link"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="switch"]',
    '[role="textbox"]',
    '[role="combobox"]',
    '[contenteditable="true"]',
  ];
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity) === 0) return false;
    return true;
  };
  const accessibleName = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const node = document.getElementById(labelledby);
      if (node && node.textContent.trim()) return node.textContent.trim();
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label && label.textContent.trim()) return label.textContent.trim();
    }
    // For inputs, look at parent <label>
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      let p = el.parentElement;
      while (p && p.tagName !== 'LABEL' && p !== document.body) p = p.parentElement;
      if (p && p.tagName === 'LABEL' && p.textContent.trim()) {
        return p.textContent.trim().slice(0, 100);
      }
    }
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return placeholder.trim();
    const title = el.getAttribute('title');
    if (title && title.trim()) return title.trim();
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    if (text) return text.slice(0, 100);
    const alt = el.getAttribute('alt');
    if (alt && alt.trim()) return alt.trim();
    // Fallback for inputs - use name attribute
    const nameAttr = el.getAttribute('name');
    if (nameAttr) return nameAttr;
    return '';
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (['text','email','url','search','tel','number','password'].includes(t)) return 'textbox';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
      return 'textbox';
    }
    return tag;
  };

  // Collect all roots (document + every open shadow root, recursively)
  const roots = [document];
  const collectShadowRoots = (root) => {
    const walker = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of walker) {
      if (el.shadowRoot) {
        roots.push(el.shadowRoot);
        collectShadowRoots(el.shadowRoot);
      }
    }
  };
  collectShadowRoots(document);

  const seen = new Set();
  const out = [];
  let refCounter = 0;
  for (const root of roots) {
    for (const sel of SELECTORS) {
      let nodes;
      try { nodes = root.querySelectorAll(sel); } catch { continue; }
      for (const el of nodes) {
        if (seen.has(el)) continue;
        seen.add(el);
        if (!isVisible(el)) continue;
        const role = roleOf(el);
        const name = accessibleName(el);
        const value = (el.value !== undefined && el.value !== null) ? String(el.value) : '';
        const ref = refCounter++;
        try { el.setAttribute('data-agent-ref', String(ref)); } catch {}
        const states = [];
        if (el.disabled) states.push('disabled');
        if (el.checked) states.push('checked');
        if (el.readOnly) states.push('readonly');
        if (el.getAttribute('aria-expanded') === 'true') states.push('expanded');
        out.push({ ref, role, name: name.slice(0, 100), value: value.slice(0, 100), states });
      }
    }
  }
  return out;
}
"""


@dataclass
class GroundedElement:
    ref: int
    role: str
    name: str
    value: str = ""
    states: tuple[str, ...] = ()

    def render(self) -> str:
        line = f"#{self.ref} [{self.role}] {self.name!r}"
        if self.value:
            line += f" value={self.value!r}"
        if self.states:
            line += f" ({', '.join(self.states)})"
        return line


class Grounding:
    """Per-page ref table. Refresh before each turn."""

    def __init__(self, page: Page):
        self.page = page
        self.elements: list[GroundedElement] = []

    def refresh(self) -> None:
        """Refresh the ref table. Waits for page to settle, retries once
        if first attempt finds nothing (common on SPAs with lazy render)."""
        self.elements = []
        self._wait_for_settle()

        for attempt in (1, 2):
            try:
                raw = self.page.evaluate(EXTRACT_JS)
            except Exception as e:
                print(f"  (grounding: evaluate failed: {e})")
                return
            if raw:
                for item in raw:
                    self.elements.append(
                        GroundedElement(
                            ref=item["ref"],
                            role=item.get("role", ""),
                            name=item.get("name", ""),
                            value=item.get("value", ""),
                            states=tuple(item.get("states", [])),
                        )
                    )
                return
            # Empty — wait a moment and retry (SPA still rendering)
            if attempt == 1:
                print("  (grounding: empty tree, waiting 2s for SPA render and retrying)")
                import time as _time
                _time.sleep(2)

    def _wait_for_settle(self) -> None:
        """Wait for network quiescence and a brief extra delay so
        deferred React/Vue components mount before we snapshot."""
        import time as _time
        try:
            self.page.wait_for_load_state("networkidle", timeout=3500)
        except Exception:
            pass
        _time.sleep(0.4)

    def render_for_prompt(self) -> str:
        if not self.elements:
            return "(no interactive elements detected)"
        return "\n".join(e.render() for e in self.elements)

    def locator_for_ref(self, ref: int) -> Locator | None:
        """Use the data-agent-ref attribute set by the extractor."""
        try:
            return self.page.locator(f'[data-agent-ref="{ref}"]').first
        except Exception:
            return None

    def describe_ref(self, ref: int) -> str:
        for e in self.elements:
            if e.ref == ref:
                return e.render()
        return f"#{ref} (invalid)"