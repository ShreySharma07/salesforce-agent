"""
Computer-use execution mode.

Operates the entire Xvfb desktop via OS-level tools:
  - scrot         takes screenshots of the whole display
  - xdotool       sends mouse + keyboard events
  - wmctrl-style  not used (Openbox is intentionally minimal)

Works for ANY app — browser, desktop applications, terminals, anything
visible on the virtual display. No DOM, no accessibility tree — pure
pixel-level vision-driven action.

The model receives the raw screenshot + step intent, returns a JSON
action with x,y coordinates in actual pixel space (1440x900 by default).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from sandbox_agent.llm_client import GeminiClient


SYSTEM_PROMPT = """You are a desktop automation step executor. You see the full Linux desktop screenshot and must emit ONE action that accomplishes the given step.

The screen is 1440x900 pixels. Coordinates are pixel space, (0,0) top-left.

Output exactly one JSON object - no prose, no fences:
  {"thought": "<short reasoning>", "action": "<one of: click | double_click | right_click | type | press | hotkey | move | scroll | wait | done | stuck>", ...args}

Action schemas:
  {"thought": "...", "action": "click", "x": <int>, "y": <int>}
  {"thought": "...", "action": "double_click", "x": <int>, "y": <int>}
  {"thought": "...", "action": "right_click", "x": <int>, "y": <int>}
  {"thought": "...", "action": "type", "text": "<text to type at current focus>"}
  {"thought": "...", "action": "press", "key": "Return"}            # single keysym
  {"thought": "...", "action": "hotkey", "keys": "ctrl+shift+t"}    # combo
  {"thought": "...", "action": "move", "x": <int>, "y": <int>}
  {"thought": "...", "action": "scroll", "x": <int>, "y": <int>, "direction": "up"}
  {"thought": "...", "action": "wait", "seconds": 2}
  {"thought": "...", "action": "done", "evidence": "<observation>"}
  {"thought": "...", "action": "stuck", "reason": "<what is missing>"}

Rules:
  - Estimate coordinates from the screenshot, top-left is (0, 0).
  - For typing into a field: click it first (one action), wait, then type next turn.
  - Emit `done` ONLY when step intent is visibly accomplished.
  - Use xdotool keysyms for `press` (Return, Tab, Escape, BackSpace, etc.).
"""


SCREEN_W = int(os.getenv("SCREEN_WIDTH", "1440"))
SCREEN_H = int(os.getenv("SCREEN_HEIGHT", "900"))
DISPLAY = os.getenv("DISPLAY", ":99")


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, env={**os.environ, "DISPLAY": DISPLAY},
        check=check, capture_output=True, text=True,
    )


def screenshot() -> bytes:
    """Capture the whole Xvfb display via scrot. Returns PNG bytes."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = Path(f.name)
    try:
        _run(["scrot", "-z", str(path)])
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


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


def _xdotool(*args: str) -> None:
    _run(["xdotool", *args])


def execute_step(
    llm: GeminiClient,
    step_intent: str,
    *,
    max_turns: int = 5,
) -> dict[str, Any]:
    last_observation: dict | None = None

    for turn in range(1, max_turns + 1):
        shot = screenshot()

        prompt = (
            f"STEP: {step_intent}\n"
            f"DISPLAY: {SCREEN_W}x{SCREEN_H}\n"
        )
        if last_observation:
            prompt += f"PREV OBSERVATION: {json.dumps(last_observation)}\n"
        prompt += "Emit one action."

        try:
            response = llm.generate(
                prompt=prompt, system=SYSTEM_PROMPT,
                images=[shot], json_mode=True, max_tokens=512,
            )
        except Exception as llm_err:
            err_str = str(llm_err)
            if "Unable to process input image" in err_str or "INVALID_ARGUMENT" in err_str:
                time.sleep(2)
                shot = screenshot()
                try:
                    response = llm.generate(
                        prompt=prompt, system=SYSTEM_PROMPT,
                        images=[shot], json_mode=True, max_tokens=512,
                    )
                except Exception as retry_err:
                    return {"status": "failed",
                            "evidence": f"LLM image error after retry: {type(retry_err).__name__}: {retry_err}",
                            "turns": turn}
            else:
                return {"status": "failed",
                        "evidence": f"LLM error: {type(llm_err).__name__}: {llm_err}",
                        "turns": turn}
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
            obs = _execute_action(action)
            time.sleep(0.4)
            last_observation = obs
        except Exception as e:
            last_observation = {"error": f"{type(e).__name__}: {e}"}

    return {"status": "failed", "evidence": "max turns exceeded", "turns": max_turns}


def _execute_action(action: dict) -> dict:
    kind = action["action"]
    result: dict = {"action": kind}
    if kind == "click":
        x, y = int(action["x"]), int(action["y"])
        _xdotool("mousemove", "--sync", str(x), str(y))
        _xdotool("click", "1")
        result["x"], result["y"] = x, y
    elif kind == "double_click":
        x, y = int(action["x"]), int(action["y"])
        _xdotool("mousemove", "--sync", str(x), str(y))
        _xdotool("click", "--repeat", "2", "--delay", "100", "1")
    elif kind == "right_click":
        x, y = int(action["x"]), int(action["y"])
        _xdotool("mousemove", "--sync", str(x), str(y))
        _xdotool("click", "3")
    elif kind == "type":
        # `type` types literal text. Use --delay for reliability with apps
        # that have key-by-key input handling.
        _xdotool("type", "--delay", "30", str(action.get("text", "")))
    elif kind == "press":
        _xdotool("key", str(action.get("key", "")))
    elif kind == "hotkey":
        # xdotool key takes combos like ctrl+shift+t directly.
        _xdotool("key", str(action.get("keys", "")))
    elif kind == "move":
        x, y = int(action["x"]), int(action["y"])
        _xdotool("mousemove", str(x), str(y))
    elif kind == "scroll":
        x, y = int(action["x"]), int(action["y"])
        d = action.get("direction", "down")
        # buttons: 4 = up, 5 = down, 6 = left, 7 = right
        button = {"up": "4", "down": "5", "left": "6", "right": "7"}.get(d, "5")
        _xdotool("mousemove", str(x), str(y))
        _xdotool("click", "--repeat", "3", button)
    elif kind == "wait":
        time.sleep(min(float(action.get("seconds", 1)), 10))
    else:
        result["error"] = f"unknown action: {kind}"
    return result