"""
Visual + DOM grounding (v2.1) — the agent's eyes.

v2 features (SoM, stable IDs, shadow DOM, viewport discipline, regions,
modal awareness) PLUS v2.1 fixes from the first live runs:

  FIX-1  Fillability: every element is tagged `fillable` (is it text-enterable?)
         so the executor can REFUSE a fill on a link/button, and the prompt
         marks text fields with "(type here)". Root cause of the Wikipedia
         search loop: a link was being filled as if it were the search box.
  FIX-2  searchbox / input[type=search] explicitly selected and recognised.

Everything here is deterministic sandbox-side code — no LLM calls.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

from playwright.sync_api import Page

MAX_MARKED = 50

EXTRACT_JS = r"""
() => {
  const SELECTORS = [
    'a[href]',
    'a[data-recordid]',    // SF datatable record links rendered without href
    'button','input:not([type="hidden"])','select','textarea',
    '[role="button"]','[role="link"]','[role="checkbox"]','[role="radio"]',
    '[role="tab"]','[role="menuitem"]','[role="option"]','[role="switch"]',
    '[role="textbox"]','[role="combobox"]','[role="searchbox"]',
    '[contenteditable="true"]','input[type="search"]',
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
      if (t === 'search') return 'searchbox';
      if (['text','email','url','tel','number','password'].includes(t)) return 'textbox';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (['submit','button','reset'].includes(t)) return 'button';
      return 'textbox';
    }
    return tag;
  };
  const regionOf = (el) => {
    let cur = el;
    while (cur) {
      if (cur.nodeType === 1) {
        const role = cur.getAttribute && cur.getAttribute('role');
        const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
        if (role === 'dialog' || role === 'alertdialog' || tag === 'dialog') return 'dialog';
        // Salesforce Lightning modals often lack role="dialog" on the outer host —
        // detect by class (.slds-modal__container) or LWC overlay custom element.
        const cls = (cur.className && typeof cur.className === 'string') ? cur.className : '';
        if (cls.indexOf('slds-modal__container') !== -1 || tag === 'lightning-overlay-container') return 'dialog';
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

  // rootOffset maps every root (Document or ShadowRoot) to its pixel offset
  // inside the main viewport.  Shadow roots share the coordinate space of their
  // host document; same-origin iframes add their own getBoundingClientRect().
  // Cross-origin iframe access throws — those are caught and skipped silently.
  const rootOffset = new Map();
  rootOffset.set(document, {dx: 0, dy: 0});

  const collectShadow = (root, dx, dy) => {
    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of all) {
      if (el.shadowRoot && !rootOffset.has(el.shadowRoot)) {
        rootOffset.set(el.shadowRoot, {dx, dy});
        collectShadow(el.shadowRoot, dx, dy);
      }
    }
  };
  collectShadow(document, 0, 0);

  const collectIframes = (root, dx, dy) => {
    let iframes; try { iframes = root.querySelectorAll('iframe'); } catch { return; }
    for (const iframe of iframes) {
      let doc; try { doc = iframe.contentDocument; } catch { continue; }
      if (!doc || doc === document || rootOffset.has(doc)) continue;
      const r = iframe.getBoundingClientRect();
      const iDx = dx + Math.round(r.left), iDy = dy + Math.round(r.top);
      rootOffset.set(doc, {dx: iDx, dy: iDy});
      collectShadow(doc, iDx, iDy);
      collectIframes(doc, iDx, iDy);
    }
  };
  collectIframes(document, 0, 0);

  const roots = Array.from(rootOffset.keys());

  // Modal detection: a modal is only flagged as "open" when an overlay
  // element is ACTUALLY BLOCKING — visible, non-zero size, AND covering
  // ≥15% of the viewport area.  This prevents SF Console's dormant
  // lightning-overlay-container (kept in DOM as a zero-size shell ready to
  // host future modals) and other invisible SF overlay elements from
  // producing false-positive modal_open=true, which would blank the element
  // list and leave the agent with nothing to act on.
  const _isActuallyBlocking = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = window.getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) return false;
    const visW = Math.min(r.right, vw) - Math.max(r.left, 0);
    const visH = Math.min(r.bottom, vh) - Math.max(r.top, 0);
    if (visW <= 0 || visH <= 0) return false;
    return (visW * visH) / (vw * vh) >= 0.15;
  };
  const MODAL_SELECTORS = [
    '[role="dialog"]', '[role="alertdialog"]', 'dialog[open]',
    '.slds-backdrop--open', '.slds-modal__container', 'lightning-overlay-container',
  ];
  let modalOpen = false;
  outer: for (const root of roots) {
    try {
      for (const sel of MODAL_SELECTORS) {
        for (const el of root.querySelectorAll(sel)) {
          if (_isActuallyBlocking(el)) { modalOpen = true; break outer; }
        }
      }
    } catch {}
  }

  const stableRefFor = (() => {
    const seenRefs = {};
    return (role, name, region) => {
      const basis = role + '|' + (name || '').trim().toLowerCase() + '|' + region;
      let h = 0x811c9dc5;
      for (let i = 0; i < basis.length; i++) {
        h ^= basis.charCodeAt(i);
        h = Math.imul(h, 0x01000193) >>> 0;
      }
      const base = h.toString(16).padStart(8, '0');
      seenRefs[base] = (seenRefs[base] || 0);
      const n = seenRefs[base]++;
      return n === 0 ? base : base + '_' + n;
    };
  })();
  const seen = new Set();
  const out = [];
  for (const root of roots) {
    const {dx, dy} = rootOffset.get(root) || {dx: 0, dy: 0};
    for (const sel of SELECTORS) {
      let nodes; try { nodes = root.querySelectorAll(sel); } catch { continue; }
      for (const el of nodes) {
        if (seen.has(el)) continue;
        seen.add(el);
        if (!isVisible(el)) continue;
        const r = el.getBoundingClientRect();
        // r is relative to the iframe's own viewport; dx/dy shifts it into the
        // main-document coordinate space so SoM annotations align with the screenshot.
        const ax = Math.round(r.left) + dx, ay = Math.round(r.top) + dy;
        const role = roleOf(el);
        const name = accName(el);
        const region = regionOf(el);
        const ref = stableRefFor(role, name, region);
        try { el.setAttribute('data-agent-ref', ref); } catch {}
        const tag = el.tagName.toLowerCase();
        const inputType = (el.getAttribute('type') || 'text').toLowerCase();
        const fillable =
          (tag === 'input' && !['checkbox','radio','submit','button','reset','file','range','color'].includes(inputType)) ||
          tag === 'textarea' ||
          role === 'textbox' || role === 'combobox' || role === 'searchbox' ||
          el.isContentEditable === true;
        out.push({
          ref, role, name: name.slice(0, 100),
          value: (el.value !== undefined && el.value !== null) ? String(el.value).slice(0, 100) : '',
          x: ax, y: ay,
          w: Math.round(r.width), h: Math.round(r.height),
          in_viewport: (r.bottom + dy) > 0 && (r.top + dy) < vh && (r.right + dx) > 0 && (r.left + dx) < vw,
          region: region, states: stateOf(el),
          fillable: fillable, tag: tag,
        });
      }
    }
  }
  // Second pass: catch <a> elements that Salesforce renders WITHOUT an href
  // (JS-navigation anchors inside lightning-datatable cells, breadcrumbs,
  // LWC listbox items, etc.) that the first pass missed because a[href] and
  // a[data-recordid] didn't match them.  Bounded by `seen` — no duplicates.
  for (const root of roots) {
    const {dx, dy} = rootOffset.get(root) || {dx: 0, dy: 0};
    let anchors; try { anchors = root.querySelectorAll('a'); } catch { continue; }
    for (const el of anchors) {
      if (seen.has(el)) continue;
      if (!isVisible(el)) continue;
      const name = accName(el);
      if (!name) continue;                // skip anchors with no accessible text
      seen.add(el);
      const r = el.getBoundingClientRect();
      const ax = Math.round(r.left) + dx, ay = Math.round(r.top) + dy;
      const role = 'link';
      const region = regionOf(el);
      const ref = stableRefFor(role, name, region);
      try { el.setAttribute('data-agent-ref', ref); } catch {}
      out.push({
        ref, role, name: name.slice(0, 100),
        value: (el.getAttribute('href') || '').slice(0, 100),
        x: ax, y: ay,
        w: Math.round(r.width), h: Math.round(r.height),
        in_viewport: (r.bottom + dy) > 0 && (r.top + dy) < vh && (r.right + dx) > 0 && (r.left + dx) < vw,
        region: region, states: stateOf(el),
        fillable: false, tag: 'a',
      });
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
    ref: str
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
    fillable: bool = False
    tag: str = ""

    @property
    def stable_id(self) -> str:
        basis = f"{self.role}|{self.name.strip().lower()}|{self.region}"
        return hashlib.sha1(basis.encode()).hexdigest()[:10]

    @property
    def descriptor(self) -> str:
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

    def by_ref(self, ref: str) -> GroundedElement | None:
        for e in self.elements:
            if e.ref == ref:
                return e
        return None


def extract_from_page(page: Page, *, already_stable: bool = False) -> PageGrounding:
    import time
    if not already_stable:
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
            ref=str(i["ref"]), role=i.get("role", ""), name=i.get("name", ""),
            value=i.get("value", ""),
            x=i.get("x", 0), y=i.get("y", 0), w=i.get("w", 0), h=i.get("h", 0),
            in_viewport=bool(i.get("in_viewport", True)),
            region=i.get("region", "main"),
            states=list(i.get("states", [])),
            fillable=bool(i.get("fillable", False)),
            tag=i.get("tag", ""),
        )
        for i in raw.get("elements", [])
    ]
    vp = raw.get("viewport", {}) or {}

    # Playwright-native supplement: find datatable record links inside closed /
    # deeply-nested shadow roots that EXTRACT_JS couldn't traverse.
    # get_by_role() uses the browser's AX tree and pierces shadow boundaries.
    # Only runs when lightning-datatable is present to avoid unnecessary work.
    try:
        if page.locator("lightning-datatable").count() > 0:
            _supplement_shadow_links(page, els)
    except Exception:
        pass

    # Supplement: capture LWC modal form inputs (lightning-input, lightning-combobox,
    # lightning-datepicker, lightning-textarea) whose native <input>/<select> live
    # inside shadow roots. The AX-tree scan gives them their label name ("Due Date",
    # "Comments", "Status") so the agent can fill/select them by ref.
    try:
        if page.locator(
            "lightning-input, lightning-combobox, lightning-datepicker, lightning-textarea"
        ).count() > 0:
            _supplement_modal_inputs(page, els)
    except Exception:
        pass

    return PageGrounding(
        elements=els,
        viewport_w=vp.get("width", 0), viewport_h=vp.get("height", 0),
        scroll_y=vp.get("scroll_y", 0), page_height=vp.get("page_height", 0),
        modal_open=bool(raw.get("modal_open", False)),
    )


def render_for_prompt(g: PageGrounding) -> str:
    if not g.elements:
        return "(no interactive elements detected)"

    visible = [e for e in g.elements if e.in_viewport][:MAX_MARKED]
    offscreen = [e for e in g.elements if not e.in_viewport]
    lines: list[str] = []

    if g.modal_open:
        dialog_els = [e for e in visible if e.region == "dialog"]
        blocked = [e for e in visible if e.region != "dialog"]
        if dialog_els:
            lines.append("A MODAL DIALOG IS OPEN — only dialog elements are actionable:")
            lines += [_line(e) for e in dialog_els]
            if blocked:
                lines.append(f"({len(blocked)} elements behind the dialog are NOT actionable until it is closed)")
        else:
            # Contradiction: modal_open set but no elements grounded in a dialog
            # region — happens when an invisible SF overlay shell triggered the
            # flag.  Show all in-viewport elements so the agent is never left
            # with an empty list.
            by_region_m: dict[str, list[GroundedElement]] = {}
            for e in visible:
                by_region_m.setdefault(e.region, []).append(e)
            for region in ("header", "nav", "main", "footer", "dialog"):
                if region in by_region_m:
                    lines.append(f"[{region.upper()}]")
                    lines += [_line(e) for e in by_region_m[region]]
    else:
        by_region: dict[str, list[GroundedElement]] = {}
        for e in visible:
            by_region.setdefault(e.region, []).append(e)
        for region in ("header", "nav", "main", "footer", "dialog"):
            if region in by_region:
                lines.append(f"[{region.upper()}]")
                lines += [_line(e) for e in by_region[region]]

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
    if e.fillable:
        line += " (type here)"
    if e.value:
        line += f" value={e.value!r}"
    if e.states:
        line += f" ({', '.join(e.states)})"
    return line


def _supplement_shadow_links(page: Page, els: list[GroundedElement]) -> None:
    """Supplement *els* in-place with visible links that EXTRACT_JS missed.

    Uses Playwright's accessibility-tree-based ``get_by_role("link")`` which
    pierces shadow DOM natively — including closed shadow roots where
    ``el.shadowRoot`` returns null and the JS traversal is blocked.

    Strategy:
    - Skip any link whose accessible text (lower-cased) already appears in els.
    - Assign a ``pw####`` ref (distinct from the FNV-1a hex refs the JS uses).
    - Set ``data-agent-ref`` on the DOM element so ``_find_locator`` can click it.
    """
    existing_names = {e.name.lower().strip() for e in els}
    existing_refs  = {e.ref for e in els}
    vp = page.viewport_size or {"width": 1440, "height": 900}
    counter = 0

    try:
        for loc in page.get_by_role("link").all():
            try:
                bb = loc.bounding_box()
                if not bb or bb["width"] == 0 or bb["height"] == 0:
                    continue
                if bb["y"] > vp["height"] or bb["y"] + bb["height"] < 0:
                    continue  # off-screen
                # Fetch text and href in one round-trip
                pair = loc.evaluate(
                    "(el) => [(el.innerText||el.textContent||"
                    "el.getAttribute('aria-label')||'').trim(),"
                    " el.getAttribute('href')||'']"
                )
                text = (pair[0] or "")[:100].strip()
                href = (pair[1] or "")[:100]
                if not text or text.lower() in existing_names:
                    continue
                # Unique pw-prefixed ref
                ref = f"pw{counter:04d}"
                while ref in existing_refs:
                    counter += 1
                    ref = f"pw{counter:04d}"
                counter += 1
                try:
                    loc.evaluate(f'(el) => el.setAttribute("data-agent-ref", "{ref}")')
                except Exception:
                    pass
                existing_names.add(text.lower())
                existing_refs.add(ref)
                els.append(GroundedElement(
                    ref=ref, role="link", name=text, value=href,
                    x=int(bb["x"]), y=int(bb["y"]),
                    w=int(bb["width"]), h=int(bb["height"]),
                    in_viewport=True, region="main",
                    states=[], fillable=False, tag="a",
                ))
            except Exception:
                continue
    except Exception:
        pass


def _supplement_modal_inputs(page: Page, els: list[GroundedElement]) -> None:
    """Supplement *els* in-place with LWC modal form inputs that EXTRACT_JS missed.

    Salesforce modal form fields (lightning-input, lightning-combobox,
    lightning-datepicker, lightning-textarea) render the native <input>/<select>
    inside shadow roots. The SELECTORS loop finds them only if the shadow root
    is open AND the host was reached by collectShadow. Playwright's
    get_by_role() uses the browser AX tree, which crosses ALL shadow boundaries.

    Accessible name resolution: LWC sets aria-labelledby on the native input
    pointing to a <label> in the same (or parent) shadow root. We walk up the
    shadow root chain via el.getRootNode() / root.host to resolve it.
    """
    existing_names = {e.name.lower().strip() for e in els}
    existing_refs  = {e.ref for e in els}
    vp = page.viewport_size or {"width": 1440, "height": 900}
    counter = 0

    # Resolves the visible label for a form input element.
    # Walks shadow-root chain to find the element named by aria-labelledby.
    label_js = r"""(el) => {
      const al = el.getAttribute('aria-label');
      if (al && al.trim()) return [al.trim(), el.value || ''];
      const lb = el.getAttribute('aria-labelledby');
      if (lb) {
        const texts = [];
        for (const id of lb.split(/\s+/)) {
          let root = el.getRootNode();
          while (root) {
            const found = root.querySelector ? root.querySelector('#' + CSS.escape(id)) : null;
            if (found) { texts.push(found.textContent.trim()); break; }
            root = root.host ? root.host.getRootNode() : null;
          }
          if (!texts.length) {
            const d = document.getElementById(id);
            if (d) texts.push(d.textContent.trim());
          }
        }
        if (texts.length) return [texts.join(' ').trim(), el.value || ''];
      }
      const ph = el.getAttribute('placeholder');
      if (ph && ph.trim()) return [ph.trim(), el.value || ''];
      return ['', el.value || ''];
    }"""

    for pw_role, fillable in [("textbox", True), ("combobox", False)]:
        try:
            for loc in page.get_by_role(pw_role).all():
                try:
                    bb = loc.bounding_box()
                    if not bb or bb["width"] == 0 or bb["height"] == 0:
                        continue
                    if bb["y"] > vp["height"] or bb["y"] + bb["height"] < 0:
                        continue
                    pair = loc.evaluate(label_js)
                    name  = (pair[0] or "")[:100].strip()
                    value = (pair[1] or "")[:100]
                    if not name or name.lower() in existing_names:
                        continue
                    ref = f"fi{counter:04d}"
                    while ref in existing_refs:
                        counter += 1
                        ref = f"fi{counter:04d}"
                    counter += 1
                    try:
                        loc.evaluate(f'(el) => el.setAttribute("data-agent-ref", "{ref}")')
                    except Exception:
                        pass
                    existing_names.add(name.lower())
                    existing_refs.add(ref)
                    els.append(GroundedElement(
                        ref=ref, role=pw_role, name=name, value=value,
                        x=int(bb["x"]), y=int(bb["y"]),
                        w=int(bb["width"]), h=int(bb["height"]),
                        in_viewport=True, region="main",
                        states=[], fillable=fillable, tag="input",
                    ))
                except Exception:
                    continue
        except Exception:
            pass


def compress_for_llm(png_bytes: bytes) -> bytes:
    """Resize to 50% and re-encode as JPEG q=85 for LLM image inputs.

    1440x900 PNG ≈ 400KB–1.5MB.  After resize+JPEG: ~40–120KB (10–20x smaller)
    with no meaningful quality loss for UI element recognition.
    """
    try:
        from PIL import Image
    except Exception:
        return png_bytes
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception:
        return png_bytes


def annotate_screenshot(png_bytes: bytes, g: PageGrounding) -> bytes:
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
        scale = img.width / g.viewport_w if g.viewport_w else 1.0
        visible = [e for e in g.elements if e.in_viewport][:MAX_MARKED]
        for e in visible:
            x0, y0 = e.x * scale, e.y * scale
            x1, y1 = (e.x + e.w) * scale, (e.y + e.h) * scale
            # fillable=green, dialog=red, else blue
            if e.region == "dialog":
                color = (255, 64, 64)
            elif e.fillable:
                color = (34, 197, 94)
            else:
                color = (37, 99, 235)
            draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
            label = str(e.ref)
            tw = draw.textlength(label, font=font) if font else 8 * len(label)
            th = 12
            lx = max(0, min(x0, img.width - tw - 6))
            ly = max(0, y0 - th - 4) if y0 - th - 4 > 0 else y0 + 2
            draw.rectangle([lx, ly, lx + tw + 6, ly + th + 4], fill=color)
            draw.text((lx + 3, ly + 2), label, fill=(255, 255, 255), font=font)
        # Resize to 50% AFTER drawing so label text stays legible at half res,
        # then JPEG q=85 — drops transfer size ~15x with no visible UI loss.
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception:
        return png_bytes