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
from pathlib import Path

import httpx


async def run_e2e(plan_path: Path, backend: str, name: str, watch: bool, auto_open: bool = True) -> int:
    plan_data = json.loads(plan_path.read_text())
    plan_id = plan_data["id"]

    async with httpx.AsyncClient(base_url=backend, timeout=15) as client:
        # Step 1: always upsert the plan from the file so the DB is never stale.
        # Checking GET first and skipping the upload when the plan "exists" is the
        # classic stale-plan trap — the DB keeps the old version even after the
        # JSON file is updated.  POST /plans is an upsert: it overwrites in place.
        r = await client.post("/plans", json=plan_data)
        r.raise_for_status()
        version = plan_data.get("version", "?")
        print(f"  ✓ upserted plan {plan_id} (version {version}, "
              f"{len(plan_data.get('steps', []))} steps)")

        # Step 2: approve it (idempotent — already approved plans stay approved)
        r = await client.post(f"/plans/{plan_id}/approve")
        r.raise_for_status()
        print(f"  ✓ approved plan {plan_id} v{version}")

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
            if status in (
                "completed", "completed_with_failures", "failed",
                "paused_for_input", "canceled", "budget_exceeded",
                "paused", "aborted",
            ):
                break
            await asyncio.sleep(2)

        print(f"\nFinal status: {status}")
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