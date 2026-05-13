"""
Phase 1b.5 end-to-end test driver.

Tying together what we have so far — a CLI that:
  1. Loads a plan JSON file (the output of process_video.py)
  2. Saves it as APPROVED
  3. Wraps it in an Automation
  4. Calls the backend's POST /automations/{id}/run
  5. Polls the run until it finishes
  6. Prints the result + live_view_url

Usage:
    python -m scripts.run_plan_e2e path/to/plan.json --backend http://localhost:8001

Requires the backend to be running:
    cd backend && python -m app.main
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx


async def run_e2e(plan_path: Path, backend: str, name: str, watch: bool, auto_open: bool = True) -> int:
    plan_data = json.loads(plan_path.read_text())
    plan_id = plan_data["id"]

    async with httpx.AsyncClient(base_url=backend, timeout=15) as client:
        # Step 1: ensure plan exists. We don't have an upload endpoint yet,
        # so we drop the file in the repo dir directly. (Phase 1c adds upload.)
        # For now, the plan must already be in .local_storage/plans/<id>.json.
        r = await client.get(f"/plans/{plan_id}")
        if r.status_code == 404:
            print(
                f"Plan {plan_id} not found in backend repo. "
                f"Copy your plan JSON to backend/.local_storage/plans/{plan_id}.json first.",
                file=sys.stderr,
            )
            return 1
        r.raise_for_status()

        # Step 2: approve it
        r = await client.post(f"/plans/{plan_id}/approve")
        r.raise_for_status()
        print(f"  ✓ approved plan {plan_id}")

        # Step 3: wrap as automation (or reuse if exists by name)
        autos = (await client.get("/automations")).json()
        existing = next((a for a in autos if a["name"] == name and a["plan_id"] == plan_id), None)
        if existing:
            auto = existing
            print(f"  ✓ reusing automation {auto['id']}")
        else:
            r = await client.post("/automations", json={"name": name, "plan_id": plan_id})
            r.raise_for_status()
            auto = r.json()
            print(f"  ✓ created automation {auto['id']}")

        # Step 4: run it
        r = await client.post(f"/automations/{auto['id']}/run")
        r.raise_for_status()
        run = r.json()
        run_id = run["id"]
        print(f"  ✓ started run {run_id}")

        # Step 5: poll until done
        print("\nPolling run status...")
        opened_view = False
        while True:
            r = await client.get(f"/runs/{run_id}")
            r.raise_for_status()
            run = r.json()
            status = run["status"]
            live = run.get("live_view_url")
            print(f"  [{status}]" + (f"  watch: {live}" if live and watch else ""))

            # Auto-open the live view in browser the moment it becomes available
            if auto_open and live and not opened_view:
                import subprocess
                subprocess.Popen(
                    ["open", live],  # macOS - use "xdg-open" on Linux, "start" on Windows
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                opened_view = True
                print(f"  → opened in browser")
            if status in ("completed", "failed", "canceled", "budget_exceeded"):
                break
            await asyncio.sleep(2)

        print("\nFinal:")
        print(json.dumps(run, indent=2))
        return 0 if status == "completed" else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", type=Path, help="path to plan JSON file")
    ap.add_argument("--backend", default="http://localhost:8001")
    ap.add_argument("--name", default="E2E Test", help="automation name")
    ap.add_argument("--watch", action="store_true", help="print live-view URL each poll")
    ap.add_argument("--no-open", action="store_true",
                    help="don't auto-open the live view in the browser (default: auto-open)")
    args = ap.parse_args()
    return asyncio.run(run_e2e(args.plan, args.backend, args.name, args.watch, not args.no_open))


if __name__ == "__main__":
    sys.exit(main())