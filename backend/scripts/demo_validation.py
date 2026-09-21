"""
End-to-end demo validation, with evidence.

This is the script to run before recording a demo or publishing the repo. It
walks the REAL user journey against a running backend and writes a markdown
report of what actually happened: timings, costs, step outcomes, corrections,
and whether a second run created duplicates.

It deliberately does NOT substitute a prepared plan. It uploads a recording,
waits for the agent to write the plan, and runs whatever the agent produced,
because "the demo works when I hand it the plan I already fixed" is not
evidence that the product works.

Usage
-----
    # 1. start the backend, with a real LLM key and Salesforce connected
    cd backend && python -m app.main

    # 2. in another shell
    python -m scripts.demo_validation recording.mp4 --name "Case triage"

    # re-run the SAME automation to check for duplicates
    python -m scripts.demo_validation --rerun auto_xxxxx

Options
-------
    --backend URL     backend base URL (default http://localhost:8001)
    --name NAME       automation name
    --out PATH        report path (default demo_evidence_<timestamp>.md)
    --verify-soql Q   after each run, execute this SOQL through the MCP
                      gateway and record the row count. Use it to prove the
                      run changed what it claimed, and that a rerun did not
                      duplicate anything.
    --yes             do not pause for the manual review step

Exit codes: 0 all good, 1 usage/setup problem, 2 the run did not succeed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

POLL_SECONDS = 3
PIPELINE_TIMEOUT = 900
RUN_TIMEOUT = 2400
TERMINAL_RUN = {
    "completed", "completed_with_failures", "failed",
    "canceled", "budget_exceeded", "paused_for_input",
}


class Evidence:
    """Accumulates everything worth putting in front of a reviewer."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.started = datetime.utcnow()
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        self.lines.append(f"\n## {title}\n")

    def row(self, key: str, value: object) -> None:
        self.lines.append(f"- **{key}:** {value}")

    def block(self, text: str, lang: str = "") -> None:
        self.lines.append(f"\n```{lang}\n{text}\n```\n")

    def fail(self, why: str) -> None:
        self.failures.append(why)
        self.lines.append(f"- ❌ **{why}**")

    def ok(self, what: str) -> None:
        self.lines.append(f"- ✅ {what}")

    def render(self, title: str) -> str:
        head = [
            f"# {title}",
            "",
            f"Generated {self.started.isoformat()}Z by `scripts/demo_validation.py`.",
            "",
            ("**Result: PASS**" if not self.failures
             else f"**Result: {len(self.failures)} PROBLEM(S)**"),
        ]
        if self.failures:
            head.append("")
            head += [f"1. {f}" for f in self.failures]
        return "\n".join(head + self.lines) + "\n"


async def _poll_video(client: httpx.AsyncClient, video_id: str, ev: Evidence) -> dict:
    """Wait for the recording → plan pipeline, reporting each stage."""
    deadline = time.monotonic() + PIPELINE_TIMEOUT
    last_stage = ""
    while time.monotonic() < deadline:
        r = await client.get(f"/videos/{video_id}")
        r.raise_for_status()
        status = r.json()
        if status["stage"] != last_stage:
            last_stage = status["stage"]
            print(f"   [{status['status']}] {last_stage}")
        if status["status"] in ("completed", "failed"):
            return status
        await asyncio.sleep(POLL_SECONDS)
    ev.fail(f"pipeline did not finish within {PIPELINE_TIMEOUT}s")
    return {}


async def _poll_run(client: httpx.AsyncClient, run_id: str, ev: Evidence) -> dict:
    """Wait for a run to reach a terminal state, printing the live view once."""
    deadline = time.monotonic() + RUN_TIMEOUT
    shown = False
    last = ""
    while time.monotonic() < deadline:
        r = await client.get(f"/runs/{run_id}")
        r.raise_for_status()
        run = r.json()
        if run["status"] != last:
            last = run["status"]
            print(f"   [{last}]")
        if not shown and run.get("live_view_url"):
            print(f"   watch: {run['live_view_url']}")
            shown = True
        if run["status"] in TERMINAL_RUN:
            return run
        await asyncio.sleep(POLL_SECONDS)
    ev.fail(f"run did not finish within {RUN_TIMEOUT}s")
    return {}


def _record_run(ev: Evidence, run: dict, label: str) -> None:
    """Write one run's outcome into the report."""
    ev.section(label)
    ev.row("run id", run.get("id"))
    ev.row("status", run.get("status"))
    ev.row("summary", run.get("summary"))

    started, finished = run.get("started_at"), run.get("finished_at")
    if started and finished:
        secs = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
        ev.row("duration", f"{secs:.1f}s")

    cost = run.get("cost") or {}
    ev.row("model calls", cost.get("llm_calls"))
    ev.row("tokens", f"{cost.get('input_tokens')} in / {cost.get('output_tokens')} out")
    ev.row("cost (USD)", f"{cost.get('cost_usd', 0):.4f}")

    steps = run.get("step_executions") or []
    ev.row("steps", f"{sum(1 for s in steps if s['status'] == 'succeeded')}/{len(steps)} succeeded")
    if steps:
        rows = ["| step | status | attempts | detail |", "|---|---|---|---|"]
        for s in steps:
            detail = (s.get("error") or "")[:90].replace("\n", " ").replace("|", "\\|")
            rows.append(f"| {s['step_id']} | {s['status']} | {s.get('attempts', 1)} | {detail} |")
        ev.lines.append("\n" + "\n".join(rows) + "\n")

    if run.get("error"):
        ev.block(str(run["error"])[:1500])

    interventions = run.get("interventions") or []
    if interventions:
        ev.row("human interventions", len(interventions))
        for i in interventions:
            ev.lines.append(f"  - {i.get('reason')} → {i.get('user_response')}")

    if run.get("status") != "completed":
        ev.fail(f"{label}: finished as {run.get('status')}, not a clean completion")


async def _verify_soql(client: httpx.AsyncClient, run_id: str, soql: str,
                       ev: Evidence, label: str) -> int | None:
    """Query Salesforce through the MCP gateway and record the row count.

    This is the only independent check in the script: everything else is the
    agent reporting on itself. Note the run token is revoked when a run ends,
    so this needs the plan to declare `salesforce/query` as an mcp_call step,
    or it will be refused — which is itself correct behaviour.
    """
    try:
        r = await client.post("/mcp/salesforce/query",
                              json={"args": {"soql": soql}, "run_id": run_id})
        if r.status_code >= 400:
            ev.row(f"{label} verification", f"not available (HTTP {r.status_code}: {r.text[:120]})")
            return None
        total = r.json().get("result", {}).get("totalSize")
        ev.row(f"{label} verification", f"`{soql}` → {total} row(s)")
        return total
    except Exception as e:
        ev.row(f"{label} verification", f"failed: {type(e).__name__}: {e}")
        return None


async def run_demo(args) -> int:
    ev = Evidence()
    ev.section("Environment")

    async with httpx.AsyncClient(base_url=args.backend, timeout=120) as client:
        try:
            health = (await client.get("/health")).json()
        except Exception as e:
            print(f"ERROR: backend not reachable at {args.backend}: {e}", file=sys.stderr)
            return 1
        for k, v in health.items():
            ev.row(k, v)

        if health.get("llm_provider") == "mock":
            ev.fail("LLM_PROVIDER=mock — this is not a real demo run")

        providers = (await client.get("/oauth/providers")).json()
        sf = next((p for p in providers if p["name"] == "salesforce"), None)
        ev.row("salesforce connected", bool(sf and sf.get("connected")))
        if not (sf and sf.get("connected")):
            ev.fail("Salesforce is not connected — the run cannot change real records")

        # ---------------- rerun mode: duplicate check only ----------------
        if args.rerun:
            ev.section("Duplicate check (re-running an existing automation)")
            ev.row("automation", args.rerun)
            before = (await client.get(f"/automations/{args.rerun}")).json()
            ev.row("counters before", f"total={before['total_runs']} clean={before['successful_runs']} "
                                      f"partial={before.get('partial_runs')} failed={before.get('failed_runs')}")

            r = await client.post(f"/automations/{args.rerun}/run")
            r.raise_for_status()
            run = await _poll_run(client, r.json()["id"], ev)
            _record_run(ev, run, "Re-run")

            skipped = [s for s in (run.get("step_executions") or []) if s["status"] == "skipped"]
            ev.row("steps self-skipped as already done", len(skipped))
            if not skipped:
                ev.lines.append(
                    "\n> ⚠️ No step reported itself already-satisfied. Either the work was\n"
                    "> genuinely outstanding, or the success conditions are not protecting\n"
                    "> against repeats. **Check the target records by hand for duplicates.**\n")
            if args.verify_soql:
                await _verify_soql(client, run["id"], args.verify_soql, ev, "after re-run")

            _write(ev, args, "Demo validation — duplicate re-run")
            return 2 if ev.failures else 0

        # ---------------- full journey ----------------
        video = Path(args.recording)
        if not video.exists():
            print(f"ERROR: recording not found: {video}", file=sys.stderr)
            return 1

        ev.section("1. Upload the recording")
        ev.row("file", f"{video.name} ({video.stat().st_size / 1e6:.1f} MB)")
        print(f"→ uploading {video.name}")
        with video.open("rb") as fh:
            r = await client.post("/videos", files={"file": (video.name, fh, "video/mp4")})
        r.raise_for_status()
        video_id = r.json()["video_id"]
        ev.row("video id", video_id)

        ev.section("2. Recording → plan")
        t0 = time.monotonic()
        status = await _poll_video(client, video_id, ev)
        ev.row("pipeline duration", f"{time.monotonic() - t0:.1f}s")
        ev.row("keyframes", status.get("frame_count"))
        ev.row("narration segments", status.get("narration_segments"))
        ev.row("narration status", status.get("narration_status"))
        if status.get("narration_warning"):
            ev.lines.append(f"\n> ⚠️ {status['narration_warning']}\n")
            if status.get("narration_status") not in ("ok", "approximate"):
                ev.fail("narration was not captured — spoken rules are missing from the plan")
        if status.get("status") != "completed":
            ev.fail(f"pipeline failed: {status.get('error')}")
            _write(ev, args, "Demo validation")
            return 2

        plan_id = status["plan_id"]
        plan = (await client.get(f"/plans/{plan_id}")).json()
        ev.row("plan", f"{plan_id} v{plan['version']}, {len(plan['steps'])} steps")
        ev.block("\n".join(
            f"{i:2d}. [{s['kind']}] {s['description']}" for i, s in enumerate(plan["steps"], 1)))

        # Transcript is evidence that spoken rules reached the planner.
        try:
            tr = (await client.get(f"/videos/{video_id}/transcript")).json()
            if tr.get("full_text"):
                ev.section("3. What the agent heard")
                if tr.get("timestamps_approximate"):
                    ev.lines.append("\n> ⚠️ Timestamps are estimated, not measured.\n")
                ev.block(tr["full_text"][:2000])
        except Exception:
            pass

        ev.section("4. Human review")
        if not args.yes:
            print("\n--- REVIEW THE PLAN ABOVE ---")
            print("Correct it if needed:")
            print(f"  curl -X POST {args.backend}/plans/{plan_id}/correct \\")
            print('       -H "Content-Type: application/json" -d \'{"feedback":"..."}\'')
            input("\nPress Enter when the plan is ready to approve… ")
            plan = (await client.get(f"/plans/{plan_id}")).json()
            ev.row("plan version at approval", plan["version"])
            ev.row("corrections applied", plan["version"] - 1)
        else:
            ev.row("review", "skipped (--yes)")

        r = await client.post(f"/plans/{plan_id}/approve")
        if r.status_code >= 400:
            ev.fail(f"approval rejected: {r.text[:300]}")
            _write(ev, args, "Demo validation")
            return 2
        ev.row("approved by", r.json().get("approved_by"))

        r = await client.post("/automations", json={"name": args.name, "plan_id": plan_id})
        r.raise_for_status()
        auto_id = r.json()["id"]
        ev.row("automation", auto_id)

        ev.section("5. First run")
        print("→ starting run")
        r = await client.post(f"/automations/{auto_id}/run")
        if r.status_code >= 400:
            ev.fail(f"run rejected: {r.text[:300]}")
            _write(ev, args, "Demo validation")
            return 2
        run = await _poll_run(client, r.json()["id"], ev)
        _record_run(ev, run, "First run")
        if args.verify_soql:
            await _verify_soql(client, run["id"], args.verify_soql, ev, "after first run")

        ev.section("6. Next step")
        ev.lines.append(
            f"\nRe-run the SAME automation to check for duplicates:\n\n"
            f"```bash\npython -m scripts.demo_validation --rerun {auto_id}\n```\n")

        _write(ev, args, "Demo validation")
        return 2 if ev.failures else 0


def _write(ev: Evidence, args, title: str) -> None:
    out = Path(args.out or f"demo_evidence_{int(time.time())}.md")
    out.write_text(ev.render(title))
    print(f"\n📄 evidence written to {out}")
    if ev.failures:
        print(f"⚠️  {len(ev.failures)} problem(s) recorded — do not publish this as a clean demo")
        for f in ev.failures:
            print(f"   - {f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", nargs="?", help="path to the screen recording")
    ap.add_argument("--rerun", help="automation id to re-run for a duplicate check")
    ap.add_argument("--backend", default="http://localhost:8001")
    ap.add_argument("--name", default="Demo automation")
    ap.add_argument("--out", default=None)
    ap.add_argument("--verify-soql", default=None,
                    help="SOQL to run after each run as independent verification")
    ap.add_argument("--yes", action="store_true", help="skip the manual review pause")
    args = ap.parse_args()

    if not args.recording and not args.rerun:
        ap.error("give a recording path, or --rerun <automation_id>")
    return asyncio.run(run_demo(args))


if __name__ == "__main__":
    sys.exit(main())
