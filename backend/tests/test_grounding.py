"""
Tests for grounding v2 (visual + DOM). All deterministic, zero LLM, zero
browser — the JS extractor itself needs a live page (verified at runtime),
but every Python-side behavior is provable here.

Run from repo root:  python -m tests_sandbox.test_grounding
"""
from __future__ import annotations

import io
import sys

from sandbox_agent.grounding import (
    GroundedElement,
    PageGrounding,
    render_for_prompt,
    annotate_screenshot,
    MAX_MARKED,
)


def _el(ref, role="button", name="New", region="main", x=10, y=10, w=80, h=24,
        in_viewport=True, states=None, value=""):
    return GroundedElement(ref=ref, role=role, name=name, value=value,
                           x=x, y=y, w=w, h=h, in_viewport=in_viewport,
                           region=region, states=states or [])


# ---------------------------------------------------------------------------
# D2 — stable identity
# ---------------------------------------------------------------------------

def test_stable_id_survives_layout_shift():
    a = _el(ref=3, x=100, y=200)
    b = _el(ref=17, x=400, y=50)   # same control, different ref & position
    assert a.stable_id == b.stable_id
    print(f"  stable_id survives ref/position change: {a.stable_id}")


def test_stable_id_distinguishes_controls():
    new_btn = _el(ref=1, name="New")
    save_btn = _el(ref=2, name="Save")
    same_name_other_region = _el(ref=3, name="New", region="dialog")
    assert new_btn.stable_id != save_btn.stable_id
    assert new_btn.stable_id != same_name_other_region.stable_id
    print("  different controls / regions get different stable_ids")


def test_descriptor_is_semantic():
    e = _el(ref=9, role="button", name="New", region="header")
    assert e.descriptor == "button 'New' in header"
    e2 = _el(ref=4, role="textbox", name="Last Name", region="main")
    assert e2.descriptor == "textbox 'Last Name'"
    print(f"  descriptors read semantically: {e.descriptor!r}")


# ---------------------------------------------------------------------------
# D4 — viewport discipline
# ---------------------------------------------------------------------------

def test_render_groups_by_region_and_summarizes_offscreen():
    g = PageGrounding(
        elements=[
            _el(0, name="App Launcher", region="header"),
            _el(1, name="Leads", region="nav"),
            _el(2, name="New", region="main"),
            _el(3, name="Hidden Below", in_viewport=False, y=2000),
            _el(4, name="Hidden Above", in_viewport=False, y=-300),
        ],
        viewport_w=1440, viewport_h=900, scroll_y=300, page_height=3000,
    )
    text = render_for_prompt(g)
    assert "[HEADER]" in text and "[NAV]" in text and "[MAIN]" in text
    assert "Hidden Below" not in text and "Hidden Above" not in text
    assert "2 more interactive elements off-screen" in text
    assert "1 below the fold" in text and "1 above" in text
    assert "scroll_y=300" in text
    print("  regions grouped; offscreen summarized; scroll context present")


def test_render_caps_marked_elements():
    g = PageGrounding(
        elements=[_el(i, name=f"btn{i}") for i in range(80)],
        viewport_w=1440, viewport_h=900, page_height=900,
    )
    text = render_for_prompt(g)
    assert f"#{MAX_MARKED - 1}" in text
    assert f"#{MAX_MARKED}" not in text  # 51st not listed
    print(f"  marked element list capped at {MAX_MARKED}")


# ---------------------------------------------------------------------------
# D6 — modal awareness
# ---------------------------------------------------------------------------

def test_modal_blocks_background_elements():
    g = PageGrounding(
        elements=[
            _el(0, name="Save", region="dialog"),
            _el(1, name="Cancel", region="dialog"),
            _el(2, name="New", region="main"),
            _el(3, name="Home", region="nav"),
        ],
        viewport_w=1440, viewport_h=900, modal_open=True,
    )
    text = render_for_prompt(g)
    assert "MODAL DIALOG IS OPEN" in text
    assert "'Save'" in text and "'Cancel'" in text
    assert "2 elements behind the dialog are NOT actionable" in text
    assert "'New'" not in text  # background element not listed as actionable
    print("  modal mode: dialog-only actionable, background flagged")


# ---------------------------------------------------------------------------
# D1 — Set-of-Mark annotation
# ---------------------------------------------------------------------------

def _blank_png(w=1440, h=900):
    from PIL import Image
    img = Image.new("RGB", (w, h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# annotate_screenshot deliberately halves the image and re-encodes it as JPEG
# (q=85) before returning: it cuts transfer size ~15x with no visible UI loss.
# So a mark drawn at CSS (x, y) lands at (x/2, y/2) in the output, and JPEG is
# lossy — "was something drawn here" must be a tolerance check, not equality.
OUTPUT_SCALE = 0.5
_NEAR_WHITE = 245


def _is_marked(img, x: float, y: float, *, radius: int = 3) -> bool:
    """True if any pixel within `radius` of the output point is non-white.

    Samples a small neighborhood because JPEG smears a 2px border across
    adjacent pixels, and the downscale can land the border a pixel either way.
    """
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            px, py = int(x + dx), int(y + dy)
            if 0 <= px < img.width and 0 <= py < img.height:
                if any(c < _NEAR_WHITE for c in img.getpixel((px, py))[:3]):
                    return True
    return False


def test_annotation_draws_marks():
    from PIL import Image
    png = _blank_png()
    g = PageGrounding(
        elements=[_el(0, x=100, y=100, w=120, h=30),
                  _el(1, name="Save", region="dialog", x=400, y=300, w=90, h=28)],
        viewport_w=1440, viewport_h=900,
    )
    out = annotate_screenshot(png, g)
    assert out != png, "annotated image must differ from blank input"
    img = Image.open(io.BytesIO(out))
    # Output is half-size, so a CSS-space corner maps to half its coordinates.
    assert _is_marked(img, 100 * OUTPUT_SCALE, 100 * OUTPUT_SCALE), "expected a mark on element 0"
    assert _is_marked(img, 400 * OUTPUT_SCALE, 300 * OUTPUT_SCALE), "expected a mark on the dialog element"
    print("  SoM marks drawn (incl. dialog color)")


def test_annotation_skips_offscreen_and_degrades_gracefully():
    from PIL import Image
    png = _blank_png()
    g = PageGrounding(
        elements=[_el(0, x=100, y=2000, w=50, h=20, in_viewport=False)],
        viewport_w=1440, viewport_h=900,
    )
    out = annotate_screenshot(png, g)
    img = Image.open(io.BytesIO(out))
    # Nothing should be drawn in-frame for an offscreen element; sample a
    # spread of pixels (in OUTPUT space) and require all of them blank.
    for xy in [(10, 10), (360, 225), (710, 440)]:
        assert not _is_marked(img, *xy), f"unexpected mark at {xy}"
    # Garbage input degrades to passthrough, never raises.
    assert annotate_screenshot(b"not a png", g) == b"not a png"
    print("  offscreen unmarked; garbage input passes through")


def test_annotation_scales_for_retina():
    from PIL import Image
    # Screenshot at 2x device scale: 2880px wide for a 1440 CSS viewport.
    png = _blank_png(2880, 1800)
    g = PageGrounding(
        elements=[_el(0, x=100, y=100, w=100, h=30)],
        viewport_w=1440, viewport_h=900,
    )
    out = annotate_screenshot(png, g)
    img = Image.open(io.BytesIO(out))
    # The box is drawn at 2x device scale, then the output is halved again —
    # so the right-edge midpoint lands back at its CSS coordinates.
    assert _is_marked(img, (100 + 100) * 2 * OUTPUT_SCALE, (100 + 15) * 2 * OUTPUT_SCALE), \
        "expected the mark to scale with device pixel ratio"
    print("  marks scale with device pixel ratio")


# ---------------------------------------------------------------------------
# PageGrounding utility
# ---------------------------------------------------------------------------

def test_by_ref_lookup():
    g = PageGrounding(elements=[_el(0), _el(7, name="Save")])
    assert g.by_ref(7).name == "Save"
    assert g.by_ref(99) is None
    print("  by_ref lookup works")


def main():
    tests = [
        test_stable_id_survives_layout_shift,
        test_stable_id_distinguishes_controls,
        test_descriptor_is_semantic,
        test_render_groups_by_region_and_summarizes_offscreen,
        test_render_caps_marked_elements,
        test_modal_blocks_background_elements,
        test_annotation_draws_marks,
        test_annotation_skips_offscreen_and_degrades_gracefully,
        test_annotation_scales_for_retina,
        test_by_ref_lookup,
    ]
    passed = 0
    for t in tests:
        print(f"- {t.__name__}")
        t(); passed += 1
    print(f"\nAll {passed} grounding tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())