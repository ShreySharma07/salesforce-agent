"""
DOM grounding for browser mode.

Injects a JS extractor that finds visible interactive elements (incl. open
shadow DOM), stamps each with `data-agent-ref`, and returns a structured
list. The browser executor renders that list into the LLM prompt and
references elements by `ref` in click/fill actions.
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page


EXTRACT_JS = r"""
() => {
  const SELECTORS = [
    'a[href]','button','input:not([type="hidden"])','select','textarea',
    '[role="button"]','[role="link"]','[role="checkbox"]','[role="radio"]',
    '[role="tab"]','[role="menuitem"]','[role="option"]','[role="switch"]',
    '[role="textbox"]','[role="combobox"]','[contenteditable="true"]',
  ];
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = window.getComputedStyle(el);
    return !(s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0);
  };
  const accName = (el) => {
    const a = el.getAttribute('aria-label'); if (a && a.trim()) return a.trim();
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const n = document.getElementById(lb); if (n && n.textContent.trim()) return n.textContent.trim(); }
    if (el.id) { const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (lab && lab.textContent.trim()) return lab.textContent.trim(); }
    if (['INPUT','TEXTAREA','SELECT'].includes(el.tagName)) {
      let p = el.parentElement;
      while (p && p.tagName !== 'LABEL' && p !== document.body) p = p.parentElement;
      if (p && p.tagName === 'LABEL' && p.textContent.trim()) return p.textContent.trim().slice(0,100);
    }
    const ph = el.getAttribute('placeholder'); if (ph && ph.trim()) return ph.trim();
    const t = el.getAttribute('title'); if (t && t.trim()) return t.trim();
    const tx = (el.innerText || el.textContent || '').trim().replace(/\s+/g,' ');
    if (tx) return tx.slice(0,100);
    const al = el.getAttribute('alt'); if (al && al.trim()) return al.trim();
    const nm = el.getAttribute('name'); if (nm) return nm;
    return '';
  };
  const roleOf = (el) => {
    const ex = el.getAttribute('role'); if (ex) return ex;
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
      if (['submit','button','reset'].includes(t)) return 'button';
      return 'textbox';
    }
    return tag;
  };
  const roots = [document];
  const collectShadow = (root) => {
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) if (el.shadowRoot) { roots.push(el.shadowRoot); collectShadow(el.shadowRoot); }
  };
  collectShadow(document);
  const seen = new Set();
  const out = [];
  let counter = 0;
  for (const root of roots) {
    for (const sel of SELECTORS) {
      let nodes; try { nodes = root.querySelectorAll(sel); } catch { continue; }
      for (const el of nodes) {
        if (seen.has(el)) continue;
        seen.add(el);
        if (!isVisible(el)) continue;
        const ref = counter++;
        try { el.setAttribute('data-agent-ref', String(ref)); } catch {}
        out.push({
          ref, role: roleOf(el), name: accName(el).slice(0, 100),
          value: (el.value !== undefined && el.value !== null) ? String(el.value).slice(0, 100) : '',
        });
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


def extract_from_page(page: Page) -> list[GroundedElement]:
    """Run the extractor in the page; return a list of GroundedElement."""
    import time

    # Best-effort wait for SPA mount
    try:
        page.wait_for_load_state("networkidle", timeout=3500)
    except Exception:
        pass
    time.sleep(0.4)

    raw = None
    for attempt in (1, 2):
        try:
            raw = page.evaluate(EXTRACT_JS)
        except Exception:
            return []
        if raw:
            break
        if attempt == 1:
            time.sleep(2)

    if not raw:
        return []

    return [
        GroundedElement(
            ref=item["ref"],
            role=item.get("role", ""),
            name=item.get("name", ""),
            value=item.get("value", ""),
        )
        for item in raw
    ]


def render_for_prompt(elements: list[GroundedElement]) -> str:
    if not elements:
        return "(no interactive elements detected)"
    lines = []
    for e in elements:
        line = f"#{e.ref} [{e.role}] {e.name!r}"
        if e.value:
            line += f" value={e.value!r}"
        lines.append(line)
    return "\n".join(lines)