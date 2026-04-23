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

        else:
            result["error"] = f"unknown action: {name}"
            return result

        _wait_for_settle(page)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result