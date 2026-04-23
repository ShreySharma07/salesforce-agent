"""
Computer Use agent loop - uses Gemini's built-in Computer Use tool.

REQUIRES BILLING ENABLED on your Google AI project. Free tier has 0 quota
for Computer Use Preview models.

Supported models:
  - gemini-2.5-computer-use-preview-10-2025
  - gemini-3-flash-preview   (built-in CU support)

This is the path you switch to once you enable billing. See README.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import termcolor
from google.genai import types
from google.genai.types import Content, FunctionResponse, Part
from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from budget import Budget
from grounding import Grounding
from llm_client import LLMClient


SCREEN_WIDTH = 1440
SCREEN_HEIGHT = 900


def denorm_x(x: int, w: int) -> int: return int(x / 1000 * w)
def denorm_y(y: int, h: int) -> int: return int(y / 1000 * h)


def _wait_for_settle(page: Page) -> None:
    try: page.wait_for_load_state("domcontentloaded", timeout=3000)
    except PWTimeoutError: pass
    try: page.wait_for_load_state("networkidle", timeout=2000)
    except PWTimeoutError: pass
    time.sleep(0.5)


def execute_cu_action(name: str, args: dict, page: Page, w: int, h: int) -> dict:
    result: dict = {}
    try:
        if name == "open_web_browser":
            pass
        elif name == "wait_5_seconds":
            time.sleep(5)
        elif name == "go_back":
            page.go_back()
        elif name == "go_forward":
            page.go_forward()
        elif name == "search":
            page.goto("https://www.google.com")
        elif name == "navigate":
            page.goto(args["url"])
        elif name == "click_at":
            page.mouse.click(denorm_x(args["x"], w), denorm_y(args["y"], h))
        elif name == "hover_at":
            page.mouse.move(denorm_x(args["x"], w), denorm_y(args["y"], h))
        elif name == "type_text_at":
            x, y = denorm_x(args["x"], w), denorm_y(args["y"], h)
            page.mouse.click(x, y)
            if args.get("clear_before_typing", True):
                page.keyboard.press("ControlOrMeta+A")
                page.keyboard.press("Backspace")
            page.keyboard.type(args["text"])
            if args.get("press_enter", True):
                page.keyboard.press("Enter")
        elif name == "key_combination":
            page.keyboard.press(args["keys"])
        elif name == "scroll_document":
            delta = 800
            d = args["direction"]
            dx, dy = ({"down": (0, delta), "up": (0, -delta),
                       "right": (delta, 0), "left": (-delta, 0)}[d])
            page.mouse.wheel(dx, dy)
        elif name == "scroll_at":
            mag = args.get("magnitude", 800)
            d = args["direction"]
            page.mouse.move(denorm_x(args["x"], w), denorm_y(args["y"], h))
            dx, dy = ({"down": (0, mag), "up": (0, -mag),
                       "right": (mag, 0), "left": (-mag, 0)}[d])
            page.mouse.wheel(dx, dy)
        elif name == "drag_and_drop":
            page.mouse.move(denorm_x(args["x"], w), denorm_y(args["y"], h))
            page.mouse.down()
            page.mouse.move(denorm_x(args["destination_x"], w),
                            denorm_y(args["destination_y"], h), steps=10)
            page.mouse.up()
        else:
            result["error"] = f"unimplemented CU action: {name}"
            return result
        _wait_for_settle(page)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def get_safety_confirmation(safety_decision: dict) -> str:
    termcolor.cprint("!! Safety system requires explicit confirmation", "red")
    termcolor.cprint(safety_decision.get("explanation", "(no explanation)"), "yellow")
    while True:
        a = input("Proceed with this action? [y/N] ").strip().lower()
        if a in ("y", "yes"): return "CONTINUE"
        if a in ("", "n", "no"): return "TERMINATE"


def run_loop(
    page: Page,
    goal: str,
    llm: LLMClient,
    budget: Budget,
    trace_dir: Path,
    turn_limit: int,
    log,
) -> dict:
    """Run the Computer Use loop. Requires paid tier + CU-capable model."""
    config = types.GenerateContentConfig(
        tools=[types.Tool(
            computer_use=types.ComputerUse(
                environment=types.Environment.ENVIRONMENT_BROWSER,
            )
        )],
    )

    # Seed the conversation with task + initial screenshot + a11y context
    initial_shot = page.screenshot(type="png")
    grounding = Grounding(page)
    grounding.refresh()
    initial_a11y = grounding.render_for_prompt()

    contents: list = [
        Content(role="user", parts=[
            Part(text=goal),
            Part(text=f"Additional context - interactive elements on the page:\n{initial_a11y}"),
            Part.from_bytes(data=initial_shot, mime_type="image/png"),
        ])
    ]

    for turn in range(1, turn_limit + 1):
        budget.check(llm)
        log(f"\n--- Turn {turn} --- {budget.snapshot(llm)}", "cyan")

        url_before = page.url
        shot_before = page.screenshot(type="png")

        response = llm.generate(
            contents=contents, config=config, purpose=f"cu_turn_{turn}",
        )
        candidate = response.candidate
        if candidate is None:
            return {"outcome": "no_candidate", "turns_used": turn, "final_message": None}
        contents.append(candidate.content)

        fcalls = [p.function_call for p in candidate.content.parts if p.function_call]
        texts = [p.text for p in candidate.content.parts if p.text]
        if texts: log("Model: " + " ".join(texts), "green")

        if not fcalls:
            msg = " ".join(texts) if texts else None
            log(">> Model signaled completion.", "green")
            return {"outcome": "model_finished", "turns_used": turn, "final_message": msg}

        terminated = False
        executed = []
        for fc in fcalls:
            args = dict(fc.args) if fc.args else {}
            extra = {}
            if "safety_decision" in args:
                if get_safety_confirmation(args["safety_decision"]) == "TERMINATE":
                    terminated = True
                    break
                extra["safety_acknowledgement"] = "true"
            log(f"  action: {fc.name} {args}", "yellow")
            res = execute_cu_action(fc.name, args, page, SCREEN_WIDTH, SCREEN_HEIGHT)
            budget.record_action()
            executed.append((fc.name, res, extra))

        if terminated:
            return {"outcome": "safety_terminated", "turns_used": turn, "final_message": None}

        shot_after = page.screenshot(type="png")
        url_after = page.url

        # Save trace for this turn
        d = trace_dir / "turns"
        d.mkdir(exist_ok=True)
        (d / f"{turn:03d}_before.png").write_bytes(shot_before)
        (d / f"{turn:03d}_after.png").write_bytes(shot_after)
        (d / f"{turn:03d}.json").write_text(json.dumps({
            "turn": turn,
            "url_before": url_before,
            "url_after": url_after,
            "model_texts": texts,
            "function_calls": [{"name": n, "args": a, "result": r}
                               for (n, r, _), (_, a) in zip(executed,
                                    [(fc.name, dict(fc.args) if fc.args else {}) for fc in fcalls])],
        }, indent=2))

        fr_parts = []
        for name, res, extra in executed:
            payload = {"url": url_after, **res, **extra}
            fr_parts.append(Part(function_response=FunctionResponse(
                name=name,
                response=payload,
                parts=[types.FunctionResponsePart(
                    inline_data=types.FunctionResponseBlob(
                        mime_type="image/png", data=shot_after))],
            )))
        contents.append(Content(role="user", parts=fr_parts))

    return {"outcome": "turn_limit", "turns_used": turn_limit, "final_message": None}