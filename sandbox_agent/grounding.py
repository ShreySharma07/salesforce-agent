"""
Visual + DOM grounding (v2) — the agent's eyes.

Upgrades over v1 (flat element list + raw screenshot):

  D1  Set-of-Mark (SoM): numbered boxes drawn ONTO the screenshot so the
      visual and the refs are fused — the model never has to mentally join
      "#12 in the text" with "that button in the image".
  D2  Stable semantic identity: every element carries a stable_id +
      descriptor (role + name + region). Actions are recorded against
      descriptors, so traces — and therefore procedural memories — are
      replayable across runs instead of storing meaningless ref ints.
  D3  Shadow-DOM-aware extraction (open shadow roots; Lightning/LWC) with
      semantic roles, states (disabled/checked/expanded), and region
      detection from landmarks (header/nav/main/dialog).
  D4  Viewport discipline: only in-viewport elements get marks; off-screen
      interactables are summarized; the agent is told its scroll position.
  D5  Bounding boxes exposed so click-at-center is available as a fallback
      when locator-based actions fail (Lightning re-renders detach nodes).
  D6  Cheap regions + modal awareness: when a dialog is open, elements
      behind it are flagged not-actionable.

Everything here is deterministic sandbox-side code — no LLM calls.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

from playwright.sync_api import Page


# How many in-viewport elements get visual marks (token + clutter budget).
MAX_MARKED = 50


EXTRACT_JS = r"""
() => {
  const SELECTORS = [
    'a[href]','button','input:not([type="hidden"])','select','textarea',
    '[role="button"]','[role="link"]','[role="checkbox"]','[role="radio"]',
    '[role="tab"]','[role="menuitem"]','[role="option"]','[role="switch"]',
    '[role="textbox"]','[role="combobox"]','[contenteditable="true"]',
  ];
  const vw = window.innerWidth, vh = window.innerHeight;
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
  // Region: nearest landmark ancestor. Dialogs win (modal awareness);
  // walking parentNode/host handles shadow DOM boundaries.
  const regionOf = (el) => {
    let cur = el;
    while (cur) {
      if (cur.nodeType === 1) {
        const role = cur.getAttribute && cur.getAttribute('role');
        const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
        if (role === 'dialog' || role === 'alertdialog' || tag === 'dialog') return 'dialog';
        if (role === 'navigation' || tag === 'nav') return 'nav';
        if (role === 'banner' || tag === 'header') return 'header';
        if (role === 'contentinfo' || tag === 'footer') return 'footer';
        if (role === 'main' || tag === 'main') return 'main';
      }
      cur = cur.parentNode || (cur.host ? cur.host : null);
    }
    return 'main';
  };
  const stateOf = (el) => {
    const s = [];
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') s.push('disabled');
    if (el.checked || el.getAttribute('aria-checked') === 'true') s.push('checked');
    const exp = el.getAttribute('aria-expanded');
    if (exp === 'true') s.push('expanded'); else if (exp === 'false') s.push('collapsed');
    if (el.getAttribute('aria-selected') === 'true') s.push('selected');
    if (el.readOnly) s.push('readonly');
    return s;
  };

  const roots = [document];
  const collectShadow = (root) => {
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) if (el.shadowRoot) { roots.push(el.shadowRoot); collectShadow(el.shadowRoot); }
  };
  collectShadow(document);

  // Is a modal dialog open anywhere?
  let modalOpen = false;
  for (const root of roots) {
    try {
      if (root.querySelector('[role="dialog"], [role="alertdialog"], dialog[open]')) { modalOpen = true; break; }
    } catch {}
  }

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
        const r = el.getBoundingClientRect();
        const ref = counter++;
        try { el.setAttribute('data-agent-ref', String(ref)); } catch {}
        out.push({
          ref, role: roleOf(el), name: accName(el).slice(0, 100),
          value: (el.value !== undefined && el.value !== null) ? String(el.value).slice(0, 100) : '',
          x: Math.round(r.left), y: Math.round(r.top),
          w: Math.round(r.width), h: Math.round(r.height),
          in_viewport: r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw,
          region: regionOf(el), states: stateOf(el),
        });
      }
    }
  }
  return {
    elements: out,
    viewport: { width: vw, height: vh,
                scroll_y: Math.round(window.scrollY),
                page_height: Math.round(Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)) },
    modal_open: modalOpen,
  };
}
"""


@dataclass
class GroundedElement:
    ref: int
    role: str
    name: str
    value: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    in_viewport: bool = True
    region: str = "main"
    states: list[str] = field(default_factory=list)

    @property
    def stable_id(self) -> str:
        """Content-derived identity, stable across iterations and runs.
        Deliberately excludes position/ref so the same logical control
        matches even after layout shifts."""
        basis = f"{self.role}|{self.name.strip().lower()}|{self.region}"
        return hashlib.sha1(basis.encode()).hexdigest()[:10]

    @property
    def descriptor(self) -> str:
        """Human/agent-readable semantic identity — what traces and
        procedural memory record instead of the ephemeral ref int."""
        d = f"{self.role} '{self.name}'" if self.name else self.role
        if self.region != "main":
            d += f" in {self.region}"
        return d


@dataclass
class PageGrounding:
    elements: list[GroundedElement]
    viewport_w: int = 0
    viewport_h: int = 0
    scroll_y: int = 0
    page_height: int = 0
    modal_open: bool = False

    def by_ref(self, ref: int) -> GroundedElement | None:
        for e in self.elements:
            if e.ref == ref:
                return e
        return None


def extract_from_page(page: Page) -> PageGrounding:
    """Run the extractor; return full PageGrounding (elements + viewport
    context + modal state)."""
    import time

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
            return PageGrounding(elements=[])
        if raw and raw.get("elements"):
            break
        if attempt == 1:
            time.sleep(2)

    if not raw:
        return PageGrounding(elements=[])

    els = [
        GroundedElement(
            ref=i["ref"], role=i.get("role", ""), name=i.get("name", ""),
            value=i.get("value", ""),
            x=i.get("x", 0), y=i.get("y", 0), w=i.get("w", 0), h=i.get("h", 0),
            in_viewport=bool(i.get("in_viewport", True)),
            region=i.get("region", "main"),
            states=list(i.get("states", [])),
        )
        for i in raw.get("elements", [])
    ]
    vp = raw.get("viewport", {}) or {}
    return PageGrounding(
        elements=els,
        viewport_w=vp.get("width", 0), viewport_h=vp.get("height", 0),
        scroll_y=vp.get("scroll_y", 0), page_height=vp.get("page_height", 0),
        modal_open=bool(raw.get("modal_open", False)),
    )


# ---------------------------------------------------------------------------
# Prompt rendering (D4 + D6)
# ---------------------------------------------------------------------------

def render_for_prompt(g: PageGrounding) -> str:
    """Structured, viewport-disciplined rendering.

    - In-viewport elements first (these carry visual marks), grouped by region.
    - When a modal is open, only dialog elements are listed as actionable;
      the rest are explicitly flagged blocked.
    - Off-screen interactables summarized, with scroll position context.
    """
    if not g.elements:
        return "(no interactive elements detected)"

    visible = [e for e in g.elements if e.in_viewport][:MAX_MARKED]
    offscreen = [e for e in g.elements if not e.in_viewport]
    lines: list[str] = []

    if g.modal_open:
        dialog_els = [e for e in visible if e.region == "dialog"]
        blocked = [e for e in visible if e.region != "dialog"]
        lines.append("A MODAL DIALOG IS OPEN — only dialog elements are actionable:")
        lines += [_line(e) for e in dialog_els]
        if blocked:
            lines.append(f"({len(blocked)} elements behind the dialog are NOT actionable until it is closed)")
    else:
        by_region: dict[str, list[GroundedElement]] = {}
        for e in visible:
            by_region.setdefault(e.region, []).append(e)
        for region in ("header", "nav", "main", "footer", "dialog"):
            if region in by_region:
                lines.append(f"[{region.upper()}]")
                lines += [_line(e) for e in by_region[region]]

    # Scroll / overflow context (D4)
    if g.page_height > g.viewport_h > 0:
        pct = int(100 * (g.scroll_y + g.viewport_h) / max(g.page_height, 1))
        lines.append(f"(viewport shows ~{min(pct,100)}% down the page; scroll_y={g.scroll_y})")
    if offscreen:
        below = sum(1 for e in offscreen if e.y >= g.viewport_h)
        above = len(offscreen) - below
        parts = []
        if below:
            parts.append(f"{below} below the fold")
        if above:
            parts.append(f"{above} above")
        lines.append(f"({len(offscreen)} more interactive elements off-screen: "
                     + ", ".join(parts) + " — scroll to reveal)")
    return "\n".join(lines)


def _line(e: GroundedElement) -> str:
    line = f"#{e.ref} [{e.role}] {e.name!r}"
    if e.value:
        line += f" value={e.value!r}"
    if e.states:
        line += f" ({', '.join(e.states)})"
    return line


# ---------------------------------------------------------------------------
# Set-of-Mark screenshot annotation (D1)
# ---------------------------------------------------------------------------

def annotate_screenshot(png_bytes: bytes, g: PageGrounding) -> bytes:
    """Draw numbered boxes for in-viewport elements onto the screenshot.

    Degrades gracefully: if Pillow is unavailable or drawing fails, the raw
    screenshot is returned unchanged (the agent still has the text list).
    Marks match the refs in the ELEMENTS list exactly.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return png_bytes
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        # Screenshot pixels may differ from CSS pixels (device scale).
        scale = img.width / g.viewport_w if g.viewport_w else 1.0

        visible = [e for e in g.elements if e.in_viewport][:MAX_MARKED]
        for e in visible:
            x0, y0 = e.x * scale, e.y * scale
            x1, y1 = (e.x + e.w) * scale, (e.y + e.h) * scale
            color = (255, 64, 64) if e.region == "dialog" else (37, 99, 235)
            draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
            label = str(e.ref)
            tw = draw.textlength(label, font=font) if font else 8 * len(label)
            th = 12
            lx = max(0, min(x0, img.width - tw - 6))
            ly = max(0, y0 - th - 4) if y0 - th - 4 > 0 else y0 + 2
            draw.rectangle([lx, ly, lx + tw + 6, ly + th + 4], fill=color)
            draw.text((lx + 3, ly + 2), label, fill=(255, 255, 255), font=font)

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return png_bytes