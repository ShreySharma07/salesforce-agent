"""
Spike 001 launcher.

Supports two modes:
  - manual       Free tier. Gemini 2.5 Flash + structured JSON actions. [DEFAULT]
  - computer_use Paid tier. Gemini Computer Use tool. Requires billing enabled.

Interactive by default: asks you for the URL + task, then runs.
Non-interactive: pass --url / --task, or set TASK_URL / TASK_PROMPT in .env.

Examples:
  python spike.py                      # interactive, manual mode
  python spike.py --mode computer_use  # interactive, CU mode (needs billing)
  python spike.py --url https://... --task "do the thing"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

import termcolor
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from agents import manual_agent, computer_use_agent
from budget import Budget, BudgetExceeded
from llm_client import LLMClient


TURN_LIMIT = 15
SCREEN_WIDTH = 1440
SCREEN_HEIGHT = 900

# Default model per mode (overridable by --model or .env CU_MODEL)
DEFAULT_MODELS = {
    "manual": "gemini-2.5-flash",
    "computer_use": "gemini-2.5-computer-use-preview-10-2025",
}


def c(msg: str, color: str = "white") -> None:
    termcolor.cprint(msg, color=color)


def prompt_for_task(default_url: str, default_task: str) -> tuple[str, str]:
    c("\n== Task configuration ==", "magenta")
    c("Task (describe what you want the agent to do):", "white")
    task = input("> ").strip() or default_task

    c(f"\nStarting URL (optional - press Enter to start on Google):", "white")
    c(f"Default: {default_url}", "white")
    url = input("URL: ").strip() or "https://www.google.com"
    return url, task


def run(
    task_prompt: str,
    task_url: str,
    mode: str,
    model: str,
    budget: Budget,
    trace_dir: Path,
) -> dict:
    trace_dir.mkdir(parents=True, exist_ok=True)
    llm = LLMClient(run_id=trace_dir.name, model=model, trace_dir=trace_dir)
    budget.start()

    summary = {
        "task_prompt": task_prompt,
        "task_url": task_url,
        "mode": mode,
        "model": model,
        "outcome": None,
        "turns_used": 0,
        "final_message": None,
        "error": None,
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT}
        )
        page = context.new_page()

        try:
            page.goto(task_url)
            page.wait_for_load_state("domcontentloaded", timeout=10_000)

            loop = manual_agent.run_loop if mode == "manual" else computer_use_agent.run_loop
            result = loop(page, task_prompt, llm, budget, trace_dir, TURN_LIMIT, c)
            summary.update(result)

        except BudgetExceeded as be:
            summary["outcome"] = "budget_exceeded"
            summary["error"] = str(be)
            c(f"!! Budget exceeded: {be}", "red")
        except Exception:
            summary["outcome"] = "exception"
            summary["error"] = traceback.format_exc()
            c("!! Unhandled exception:", "red")
            c(summary["error"], "red")
        finally:
            browser.close()

    summary["budget_final"] = budget.snapshot(llm)
    summary["total_cost_usd"] = round(llm.total_cost(), 6)
    (trace_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Gemini + Playwright agent spike")
    parser.add_argument(
        "--mode", choices=["manual", "computer_use"],
        default=os.getenv("AGENT_MODE", "manual"),
        help="manual = free tier JSON loop (default); computer_use = paid tier CU tool",
    )
    parser.add_argument("--model", default=None, help="Override the model id")
    parser.add_argument("--url", default=None, help="Starting URL")
    parser.add_argument("--task", default=None, help="Goal description")
    parser.add_argument("--yes", action="store_true",
                        help="Skip interactive prompt; use .env defaults or flags")
    args = parser.parse_args()

    if not os.getenv("GEMINI_API_KEY"):
        c("GEMINI_API_KEY not set. Copy .env.example to .env and fill it.", "red")
        return 1

    mode = args.mode
    model = args.model or os.getenv("CU_MODEL") or DEFAULT_MODELS[mode]

    default_url = os.getenv("TASK_URL", "https://www.google.com")
    default_task = os.getenv(
        "TASK_PROMPT",
        'Log into this website with username "tomsmith" and password '
        '"SuperSecretPassword!". After you see the secure area page, '
        'click the "Logout" button and confirm the login page reappears.',
    )

    if args.url or args.task or args.yes:
        task_url = args.url or default_url
        task_prompt = args.task or default_task
    else:
        task_url, task_prompt = prompt_for_task(default_url, default_task)

    budget = Budget(
        max_model_calls=int(os.getenv("MAX_MODEL_CALLS", 25)),
        max_usd=float(os.getenv("MAX_USD", 0.50)),
        max_wall_clock_seconds=int(os.getenv("MAX_WALL_CLOCK_SECONDS", 300)),
    )

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_dir = Path("traces") / stamp

    c(f"\nMode:   {mode}", "white")
    c(f"Model:  {model}", "white")
    c(f"URL:    {task_url}", "white")
    c(f"Task:   {task_prompt}", "white")
    c(f"Budget: {asdict(budget)}", "white")
    c(f"Trace:  {trace_dir}\n", "white")

    summary = run(task_prompt, task_url, mode, model, budget, trace_dir)

    c("\n==== SUMMARY ====", "magenta")
    c(json.dumps(summary, indent=2), "magenta")
    return 0 if summary["outcome"] == "model_finished" else 2


if __name__ == "__main__":
    sys.exit(main())