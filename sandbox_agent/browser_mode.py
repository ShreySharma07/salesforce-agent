"""
Browser execution mode.

Executes one Plan step by:
  1. Capturing screenshot + DOM grounding from the current Chromium page
  2. Asking the LLM what action to take, given the step's intent
  3. Executing the chosen action via Playwright
  4. Returning a result so the caller can decide what to do next

The model emits structured JSON action; we resolve refs to the
data-agent-ref attribute the grounding stamps onto each element.
"""
from __future__ import annotations

import json
import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from sandbox_agent.grounding import extract_from_page, render_for_prompt
from sandbox_agent.llm_client import GeminiClient


SYSTEM_PROMPT = """You are a browser automation step executor. You will receive ONE step of a larger plan and must emit ONE action that will accomplish it on the current page.

Inputs each turn:
  - STEP: what this step is supposed to accomplish
  - URL: current page URL
  - TITLE: current page title
  - ELEMENTS: numbered interactive elements on the current page
  - SCREENSHOT: a snapshot of the current viewport

Output exactly one JSON object - no prose, no fences:
  {"thought": "<short reasoning>", "action": "<one of: click | fill | press | navigate | scroll | wait | done | stuck>", ...args}

Action schemas:
  {"thought": "...", "action": "click", "ref": <int>}
  {"thought": "...", "action": "fill", "ref": <int>, "text": "<value>"}
  {"thought": "...", "action": "press", "key": "Enter"}
  {"thought": "...", "action": "navigate", "url": "https://..."}
  {"thought": "...", "action": "scroll", "direction": "down"}
  {"thought": "...", "action": "wait", "seconds": 2}
  {"thought": "...", "action": "done", "evidence": "<observation that proves the step succeeded>"}
  {"thought": "...", "action": "stuck", "reason": "<what is missing>"}

Rules:
  - Refer to elements by # ref from ELEMENTS only.
  - Emit `done` ONLY when this step's intent is clearly accomplished, citing evidence from the page.
  - Emit `stuck` if the element you need is not in ELEMENTS (computer mode will be tried as a fallback).
  - Never invent refs not in the list.
"""


def parse_action(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(t[s : e + 1])
            except json.JSONDecodeError:
                return None
        return None


def _wait_settle(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except PWTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=2000)
    except PWTimeoutError:
        pass
    time.sleep(0.4)


def execute_step(
    page: Page,
    llm: GeminiClient,
    step_intent: str,
    *,
    max_turns: int = 5,
) -> dict[str, Any]:
    """Run a small loop until the step says done/stuck or we hit max_turns.
    Returns: {"status": "succeeded"|"stuck"|"failed", "evidence": str, "turns": int}."""
    last_observation: dict | None = None

    for turn in range(1, max_turns + 1):
        url = page.url
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        elements = extract_from_page(page)
        elements_text = render_for_prompt(elements)
        screenshot = page.screenshot(type="png")

        prompt_parts = [
            f"STEP: {step_intent}",
            f"URL: {url}",
            f"TITLE: {title}",
            "ELEMENTS:",
            elements_text,
        ]
        if last_observation:
            prompt_parts.append(f"PREV OBSERVATION: {json.dumps(last_observation)}")
        prompt_parts.append("Emit one action.")
        prompt = "\n\n".join(prompt_parts)

        response = llm.generate(
            prompt=prompt, system=SYSTEM_PROMPT,
            images=[screenshot], json_mode=True, max_tokens=1024,
        )
        action = parse_action(response.text)
        if action is None:
            last_observation = {"error": "could not parse model output"}
            continue

        kind = action.get("action")

        if kind == "done":
            return {"status": "succeeded",
                    "evidence": action.get("evidence", ""),
                    "turns": turn}

        if kind == "stuck":
            return {"status": "stuck",
                    "evidence": action.get("reason", ""),
                    "turns": turn}

        try:
            obs = _execute_action(page, action)
            _wait_settle(page)
            last_observation = obs
        except Exception as e:
            last_observation = {"error": f"{type(e).__name__}: {e}"}

    return {"status": "failed", "evidence": "max turns exceeded", "turns": max_turns}


def _execute_action(page: Page, action: dict) -> dict:
    kind = action["action"]
    result: dict = {"action": kind}
    if kind == "click":
        ref = int(action["ref"])
        page.locator(f'[data-agent-ref="{ref}"]').first.click(timeout=5000)
        result["ref"] = ref
    elif kind == "fill":
        ref = int(action["ref"])
        text = str(action.get("text", ""))
        page.locator(f'[data-agent-ref="{ref}"]').first.fill(text, timeout=5000)
        result["ref"] = ref
        result["text_len"] = len(text)
    elif kind == "press":
        page.keyboard.press(str(action.get("key", "")))
    elif kind == "navigate":
        page.goto(str(action["url"]))
    elif kind == "scroll":
        d = action.get("direction", "down")
        delta = 700
        dx, dy = (
            (0, delta) if d == "down" else
            (0, -delta) if d == "up" else
            (delta, 0) if d == "right" else
            (-delta, 0)
        )
        page.mouse.wheel(dx, dy)
    elif kind == "wait":
        time.sleep(min(float(action.get("seconds", 1)), 10))
    else:
        result["error"] = f"unknown action: {kind}"
    return result