"""
End-to-end pipeline CLI:
    video file -> keyframes -> captions -> Plan (PENDING APPROVAL)

The plan is generated and SAVED as pending_approval. It is NOT runnable until
a human reviews it and either approves it or sends a correction. Review/correct/
approve happens in the frontend (the Plans page) or via the API:
    POST /plans/{id}/correct   {"feedback": "do this for all new cases ..."}
    POST /plans/{id}/approve

Usage:
    python -m scripts.process_video path/to/video.mp4
    python -m scripts.process_video path/to/video.mp4 --output plan.json
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from app.agent.audio_transcriber import transcribe_video
from app.agent.keyframe_captioner import caption_keyframes, FRAMES_PER_BATCH
from app.agent.plan_generator import generate_plan, regenerate_plan_from_intent
from app.agent.video_processor import extract_keyframes
from app.config import get_settings
from app.schemas.plan import Plan, PlanStatus, StepKind
from app.services.storage import get_storage
from app.services.run_repo import get_repository


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a recorded video into a Plan")
    parser.add_argument("video", type=Path, help="Path to .mp4 (or other video)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Also write plan.json locally. Default: just save to DB + print.")
    parser.add_argument("--video-id", default=None)
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt.")
    args = parser.parse_args()

    if not args.video.exists():
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 1

    settings = get_settings()
    print(f"LLM provider: {settings.llm_provider}")
    print(f"Video:        {args.video}")

    storage = get_storage()
    video_id = args.video_id or uuid.uuid4().hex[:12]

    src_key = f"videos/{video_id}/source{args.video.suffix}"
    storage.write_bytes(src_key, args.video.read_bytes())
    print(f"\n[1/4] Stored source video at: {src_key}")

    print("[2/4] Transcribing audio narration...")
    narration = transcribe_video(args.video)
    if narration:
        print(f"      {len(narration)} narration segments transcribed")
    else:
        print("      No narration found (silent video or no transcription backend available)")

    print("[3/4] Extracting keyframes...")
    manifest = extract_keyframes(src_key, video_id=video_id, storage=storage)
    print(f"      {manifest.frame_count} keyframes from {manifest.duration_seconds}s video")

    n_caption_calls = (manifest.frame_count + FRAMES_PER_BATCH - 1) // FRAMES_PER_BATCH
    total_calls = n_caption_calls + 1
    # Billing is active (hard cap enforced at the provider). Informational only.
    print(f"\nThis run will make ~{total_calls} LLM calls "
          f"({n_caption_calls} captioning + 1 plan synthesis).")
    if settings.llm_provider != "mock" and not args.yes:
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm in ("n", "no"):
            print("Aborted.")
            return 0

    print("[4a/4] Captioning frames...")
    captions = caption_keyframes(manifest, storage=storage, narration=narration or None)
    print(f"      {len(captions)} captions produced")

    print("[4b/4] Synthesizing plan...")
    plan = generate_plan(captions, source_video_id=video_id)
    print(f"      Plan: {plan.id} with {len(plan.steps)} steps")

    # Persist initial version as PENDING_APPROVAL before entering review loop.
    asyncio.run(get_repository().save_plan(plan, user_id=settings.default_user_id))

    # --- Interactive review loop -----------------------------------
    # The user sees a readable summary of the plan, then either approves it or
    # describes changes in plain language. The LLM regenerates and we loop
    # until the user explicitly approves. Each revision bumps the version and
    # is saved immediately so the state is durable.
    try:
        plan = _review_loop(plan, captions, settings, args.output)
    except EOFError:
        # Non-interactive context (CI, pipe): leave as PENDING_APPROVAL.
        print("\n[non-interactive] Plan saved as PENDING_APPROVAL.")
        if args.output:
            args.output.write_text(plan.model_dump_json(indent=2))
            print(f"Wrote plan to {args.output}")
        print(f"Review/approve via the app or POST /plans/{plan.id}/approve")
        return 0

    if args.output:
        args.output.write_text(plan.model_dump_json(indent=2))
        print(f"\nWrote approved plan to {args.output}")

    return 0


# ---------------------------------------------------------------------------
# Interactive review helpers
# ---------------------------------------------------------------------------

def _print_plan_readable(plan: Plan) -> None:
    """Print a concise, human-readable plan (not raw JSON)."""
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  Plan {plan.id}  (v{plan.version})")
    print(f"  Goal: {plan.goal}")
    if plan.summary:
        print(f"  {plan.summary}")
    print(sep)
    for i, step in enumerate(plan.steps, 1):
        kind = step.kind.value if hasattr(step.kind, "value") else str(step.kind)
        details = ""
        if step.kind == StepKind.NAVIGATE:
            url = step.details.get("url", "")
            details = f"\n       URL: {url}" if url else ""
        elif step.kind == StepKind.UI_ACTION:
            intent = step.details.get("intent", "")
            target = step.details.get("target_description", "")
            value = step.details.get("value", "")
            parts = [x for x in [intent, target, repr(value) if value else ""] if x]
            details = f"  ({', '.join(parts)})" if parts else ""
        elif step.kind == StepKind.EXTRACT:
            var = step.details.get("variable_name", "")
            details = f"  → ${{{var}}}" if var else ""
        elif step.kind == StepKind.WAIT:
            secs = step.details.get("seconds", "?")
            details = f"  ({secs}s)"
        print(f"  {i:2d}. [{kind:10s}] {step.description}{details}")
    if plan.decision_rules:
        print(f"\n  Decision rules:")
        for r in plan.decision_rules:
            print(f"    • {r.rule}")
    print(sep)


def _review_loop(
    plan: Plan,
    captions: list,
    settings,
    output_path,
) -> Plan:
    """Interactive plan-review loop. Raises EOFError if stdin is not a tty.

    Shows the plan readably, asks for approval or plain-language corrections,
    calls the LLM to regenerate on each correction, saves each version, and
    returns the plan once the user types 'approve'.
    """
    repo = get_repository()

    while True:
        _print_plan_readable(plan)

        print(f"\nApprove this plan or describe what to change?")
        print("  Type  approve  to accept and save as APPROVED.")
        print("  Type  show json  to see the raw JSON.")
        print("  Or describe the change you want and press Enter to regenerate.\n")
        try:
            answer = input(f"[v{plan.version}] > ").strip()
        except EOFError:
            raise  # propagate to caller

        if not answer:
            continue

        if answer.lower() in ("approve", "y", "yes"):
            plan.status = PlanStatus.APPROVED
            asyncio.run(repo.save_plan(plan, user_id=settings.default_user_id))
            print(f"\n✓ Plan {plan.id} (v{plan.version}) APPROVED and saved.")
            return plan

        if answer.lower() in ("show json", "json"):
            print(plan.model_dump_json(indent=2))
            continue

        # User described a correction — regenerate using captions for fidelity.
        print(f"\nRegenerating (v{plan.version} → v{plan.version + 1})…")
        plan = regenerate_plan_from_intent(plan, answer, captions=captions)
        asyncio.run(repo.save_plan(plan, user_id=settings.default_user_id))
        print(f"Done. Showing revised plan.")


if __name__ == "__main__":
    sys.exit(main())