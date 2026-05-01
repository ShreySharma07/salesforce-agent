"""
End-to-end pipeline CLI:
    video file -> keyframes -> captions -> Plan -> printed JSON

Usage:
    python -m scripts.process_video path/to/video.mp4
    python -m scripts.process_video path/to/video.mp4 --output plan.json

Reads LLM_PROVIDER from .env. With LLM_PROVIDER=mock (the default), no
real API calls are made and the pipeline runs end-to-end in a few seconds.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from app.agent.keyframe_captioner import caption_keyframes
from app.agent.plan_generator import generate_plan
from app.agent.video_processor import extract_keyframes
from app.config import get_settings
from app.services.storage import get_storage

from app.agent.keyframe_captioner import FRAMES_PER_BATCH


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a recorded video into a Plan")
    parser.add_argument("video", type=Path, help="Path to .mp4 (or other video)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Where to write plan.json. Default: print to stdout.")
    parser.add_argument("--video-id", default=None,
                        help="Stable ID for this video. Default: random hex.")
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
    print(f"\n[1/3] Stored source video at: {src_key}")

    print("[2/3] Extracting keyframes...")
    manifest = extract_keyframes(src_key, video_id=video_id, storage=storage)
    print(f"      {manifest.frame_count} keyframes from {manifest.duration_seconds}s video")

    # ---- Quota guard ----
    n_caption_calls = (manifest.frame_count + FRAMES_PER_BATCH - 1) // FRAMES_PER_BATCH
    n_plan_calls = 1
    total_calls = n_caption_calls + n_plan_calls
    print(f"\nThis run will make ~{total_calls} LLM calls "
          f"({n_caption_calls} captioning + {n_plan_calls} plan synthesis)")
    if settings.llm_provider != "mock":
        print(f"Free tier daily limit: 20 calls. Use sparingly.")
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm in ("n", "no"):
            print("Aborted.")
            return 0 

    print("[3a/3] Captioning frames (slow with a real LLM, instant with mock)...")
    captions = caption_keyframes(manifest, storage=storage)
    print(f"      {len(captions)} captions produced")

    print("[3b/3] Synthesizing plan...")
    plan = generate_plan(captions, source_video_id=video_id)
    print(f"      Plan: {plan.id} with {len(plan.steps)} steps")

    plan_json = plan.model_dump_json(indent=2)

    if args.output:
        args.output.write_text(plan_json)
        print(f"\nWrote plan to {args.output}")
    else:
        print("\n========== GENERATED PLAN ==========\n")
        print(plan_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())