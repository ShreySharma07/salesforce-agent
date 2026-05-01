"""
Action executor for the manual agent loop.

The model emits actions as structured JSON. We support a small, precise
set of actions that are reliable and easy to reason about.

Each executor returns a dict that becomes part of the observation fed
back to the model.
"""
from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from grounding import Grounding


def _wait_for_settle(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except PWTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=2000)
    except PWTimeoutError:
        pass
    time.sleep(0.4)


def execute_action(
    action: dict,
    page: Page,
    grounding: Grounding,
) -> dict[str, Any]:
    """Execute one structured action. Returns observation dict."""
    name = action.get("action")
    result: dict[str, Any] = {"action": name}

    try:
        if name == "click":
            ref = int(action["ref"])
            loc = grounding.locator_for_ref(ref)
            if loc is None:
                result["error"] = f"ref #{ref} not found"
                return result
            result["target"] = grounding.describe_ref(ref)
            loc.click(timeout=5000)

        elif name == "click_xy":
            # Vision-based fallback for elements that don't appear in our
            # grounding (closed shadow DOM, canvas widgets, custom components).
            # Coordinates are in actual viewport pixels.
            x = int(action["x"])
            y = int(action["y"])
            page.mouse.click(x, y)
            result["x"] = x
            result["y"] = y

        elif name == "fill_xy":
            # Click at xy, clear, type. Used when a text input isn't grounded.
            x = int(action["x"])
            y = int(action["y"])
            text = str(action.get("text", ""))
            page.mouse.click(x, y)
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(text)
            result["x"] = x
            result["y"] = y
            result["text"] = text

        elif name == "fill":
            ref = int(action["ref"])
            text = str(action.get("text", ""))
            loc = grounding.locator_for_ref(ref)
            if loc is None:
                result["error"] = f"ref #{ref} not found"
                return result
            result["target"] = grounding.describe_ref(ref)
            result["text"] = text
            loc.fill(text, timeout=5000)

        elif name == "press":
            key = str(action.get("key", ""))
            page.keyboard.press(key)
            result["key"] = key

        elif name == "navigate":
            url = str(action["url"])
            page.goto(url)
            result["url"] = url

        elif name == "scroll":
            direction = action.get("direction", "down")
            delta = 600
            dx, dy = 0, 0
            if direction == "down": dy = delta
            elif direction == "up": dy = -delta
            elif direction == "right": dx = delta
            elif direction == "left": dx = -delta
            page.mouse.wheel(dx, dy)
            result["direction"] = direction

        elif name == "wait":
            seconds = float(action.get("seconds", 2))
            time.sleep(min(seconds, 10))  # cap at 10s defensive
            result["seconds"] = seconds

        elif name == "done":
            result["done"] = True
            result["reason"] = action.get("reason", "")

        elif name == "stuck":
            result["stuck"] = True
            result["reason"] = action.get("reason", "")
        
        elif name == "click_text":
            # Locate element by visible text and click it. Works even when
            # the element isn't in our DOM grounding (web components, etc.)
            text = str(action["text"])
            exact = bool(action.get("exact", False))
            try:
                loc = page.get_by_text(text, exact=exact).first
                loc.click(timeout=5000)
                result["text"] = text
            except Exception as e:
                result["error"] = f"click_text failed: {e}"
                return result

        else:
            result["error"] = f"unknown action: {name}"
            return result

        _wait_for_settle(page)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result