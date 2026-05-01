"""
Manual agent loop. Works on free tier (no Computer Use tool required).

The model receives a screenshot + numbered accessibility-like tree built
from the DOM, and returns one structured JSON action per turn.
"""
from __future__ import annotations

import json
from pathlib import Path

from google.genai import types
from google.genai.types import Content, Part
from playwright.sync_api import Page

from actions import execute_action
from budget import Budget
from grounding import Grounding
from llm_client import LLMClient


SYSTEM_PROMPT = """You are a browser automation agent. Your job is to complete the user's goal by emitting ONE action per turn.

At each turn you will receive:
  - The user's GOAL
  - The current URL
  - The page TITLE
  - A numbered list of interactive elements on the page (the "accessibility tree")
  - A screenshot of the current page (1440 x 900 viewport)

You MUST respond with a single JSON object - no prose, no markdown fences. Schema:

  {"thought": "<one short sentence of reasoning>", "action": "<one of: click | fill | click_xy | fill_xy | press | navigate | scroll | wait | done | stuck>", ...args}

Action schemas:
  {"thought": "...", "action": "click", "ref": <int>}
  {"thought": "...", "action": "fill", "ref": <int>, "text": "<value>"}
  {"thought": "...", "action": "click_xy", "x": <int>, "y": <int>}      # FALLBACK - see below
  {"thought": "...", "action": "fill_xy", "x": <int>, "y": <int>, "text": "<value>"}  # FALLBACK
  {"thought": "...", "action": "click_text", "text": "<visible text on the element or nearby label>", "exact": false}
  {"thought": "...", "action": "press", "key": "Enter"}
  {"thought": "...", "action": "navigate", "url": "https://..."}
  {"thought": "...", "action": "scroll", "direction": "down"}
  {"thought": "...", "action": "wait", "seconds": 2}
  {"thought": "...", "action": "done", "evidence": "<specific observation from the CURRENT page that proves the goal is complete>", "reason": "<short summary>"}
  {"thought": "...", "action": "stuck", "reason": "<what you need from the human>"}

Rules:
  - Action priority for clicking elements not in the tree:
    1. FIRST try click_text with the element's visible label text (e.g. "I agree to the Main Services Agreement").
    2. If click_text fails or no nearby text exists, try click_xy with coordinates estimated from the screenshot
       (viewport is 1440 wide by 900 tall, (0,0) top-left).
  - If a previous action did NOT change the page state (same URL, same error message), do NOT repeat it.
    Try a different approach: different text, different coordinates, scroll first, or emit `stuck`.
  - Refer to elements by their # number from the accessibility tree wherever possible.
  - Prefer `fill` over `click`+`press` for text inputs.
  - Emit `done` ONLY when the page title, URL, or visible content demonstrably shows the goal is met.
    The `evidence` field MUST quote something specific from the current page (title text, a headline,
    or a URL fragment) - not a generic "I clicked it" claim. If you cannot cite concrete evidence,
    DO NOT emit done; keep working.
  - Emit `stuck` only after trying click_xy / fill_xy fallbacks for missing elements.
  - Never invent refs not present in the current tree.
  - Never include any text outside the JSON object.
  - If the page is google.com or another search engine and you need information from elsewhere,
    use `fill` on the search box and `press` Enter, OR use `navigate` directly to a known URL.
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
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def build_turn_prompt(goal: str, url: str, title: str, a11y: str,
                      recent_actions: list[dict]) -> str:
    parts = [
        f"GOAL: {goal}",
        f"CURRENT URL: {url}",
        f"PAGE TITLE: {title}",
        "ACCESSIBILITY TREE:",
        a11y,
    ]
    if recent_actions:
        parts.append("RECENT ACTIONS (most recent last - if you see a repeat that didn't work, try something different):")
        for i, a in enumerate(recent_actions[-5:], 1):
            parts.append(f"  {i}. {json.dumps(a)}")
    parts.append("Emit one action as a single JSON object.")
    return "\n\n".join(parts)


def dump_turn(trace_dir: Path, turn_idx: int, url_before: str, a11y: str,
              screenshot: bytes, raw_model_text: str, parsed_action: dict | None,
              observation: dict) -> None:
    d = trace_dir / "turns"
    d.mkdir(exist_ok=True)
    (d / f"{turn_idx:03d}.png").write_bytes(screenshot)
    (d / f"{turn_idx:03d}.json").write_text(json.dumps({
        "turn": turn_idx,
        "url_before": url_before,
        "accessibility_tree": a11y,
        "model_raw": raw_model_text,
        "parsed_action": parsed_action,
        "observation": observation,
    }, indent=2))


def run_loop(
    page: Page,
    goal: str,
    llm: LLMClient,
    budget: Budget,
    trace_dir: Path,
    turn_limit: int,
    log,  # callable(msg, color)
) -> dict:
    """Drive the manual loop until done/stuck/limit. Returns outcome dict."""
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.0,
        response_mime_type="application/json",
    )
    grounding = Grounding(page)
    recent_actions: list[dict] = []

    for turn in range(1, turn_limit + 1):
        budget.check(llm)

        url_before = page.url
        try:
            title_before = page.title() or ""
        except Exception:
            title_before = ""
        grounding.refresh()
        a11y = grounding.render_for_prompt()
        shot = page.screenshot(type="png")

        log(f"\n--- Turn {turn} --- {budget.snapshot(llm)}", "cyan")
        log(f"URL: {url_before}", "white")
        log(f"Title: {title_before}", "white")

        user_text = build_turn_prompt(goal, url_before, title_before, a11y, recent_actions)

        response = llm.generate(
            contents=[Content(role="user", parts=[
                Part(text=user_text),
                Part.from_bytes(data=shot, mime_type="image/png"),
            ])],
            config=config,
            purpose=f"manual_turn_{turn}",
        )

        raw_text = response.text or ""
        log(f"Model: {raw_text}", "green")

        action = parse_action(raw_text)
        if action is None:
            observation = {"error": "could not parse JSON from model output"}
            dump_turn(trace_dir, turn, url_before, a11y, shot, raw_text, None, observation)
            last_observation = observation
            continue

        if action.get("action") == "done":
            evidence = action.get("evidence", "") or action.get("reason", "")
            dump_turn(trace_dir, turn, url_before, a11y, shot, raw_text, action,
                      {"done": True, "evidence": evidence,
                       "reason": action.get("reason", ""),
                       "url_at_done": url_before,
                       "title_at_done": title_before})
            log(f">> Agent reports done. Evidence: {evidence}", "green")
            return {"outcome": "model_finished",
                    "turns_used": turn,
                    "final_message": action.get("reason", ""),
                    "evidence": evidence,
                    "url_at_done": url_before,
                    "title_at_done": title_before}

        if action.get("action") == "stuck":
            dump_turn(trace_dir, turn, url_before, a11y, shot, raw_text, action,
                      {"stuck": True, "reason": action.get("reason", "")})
            log(f">> Agent stuck: {action.get('reason', '')}", "yellow")
            return {"outcome": "agent_stuck",
                    "turns_used": turn,
                    "final_message": action.get("reason", "")}

        observation = execute_action(action, page, grounding)
        budget.record_action()
        log(f"  -> {observation}", "yellow")
        dump_turn(trace_dir, turn, url_before, a11y, shot, raw_text, action, observation)
        recent_actions.append({"action": action, "observation": observation})

    return {"outcome": "turn_limit", "turns_used": turn_limit, "final_message": None}