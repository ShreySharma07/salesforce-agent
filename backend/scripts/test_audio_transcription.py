"""
Standalone test for the Gemini audio transcription backend.

Usage:
    cd backend
    python -m scripts.test_audio_transcription path/to/recording.mp4

What it does:
  1. Extracts audio from the video (ffmpeg, mono 16 kHz WAV)
  2. Sends the WAV to Gemini and asks for timestamped JSON segments
  3. Prints every NarrationSegment (start_s, end_s, text)
  4. Reports whether timestamps are REAL (from Gemini) or APPROXIMATE (even-split fallback)

No side effects — does not write to storage, does not touch the plan pipeline.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Load .env before any app imports so GEMINI_API_KEY (and other env vars) are present.
# python-dotenv is already a backend dependency.
from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_FILE)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test Gemini audio transcription on a single video file."
    )
    parser.add_argument("video", type=Path, help="Path to the video file (.mp4, .webm, etc.)")
    parser.add_argument("--language", default=None, help="BCP-47 language code hint, e.g. 'en'")
    parser.add_argument("--debug", action="store_true", help="Show debug-level log output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    video_path: Path = args.video.resolve()
    if not video_path.exists():
        print(f"ERROR: file not found: {video_path}", file=sys.stderr)
        return 1

    print(f"\nVideo : {video_path}")
    print("Step 1/2 — extracting audio and calling Gemini...\n")

    # Import here so PYTHONPATH / env is set before importing app modules
    from app.agent.audio_transcriber import transcribe_video_gemini_only

    try:
        segments, approximate = transcribe_video_gemini_only(
            video_path, language=args.language
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Step 2/2 — results\n")
    print(f"Timestamp mode : {'APPROXIMATE (even-split fallback)' if approximate else 'REAL (from Gemini)'}")
    print(f"Segments       : {len(segments)}\n")

    if not segments:
        print("  (no speech detected or Gemini returned an empty transcript)")
        return 0

    col_w = max(len(seg.text) for seg in segments)
    col_w = min(col_w, 80)
    header = f"{'start_s':>8}  {'end_s':>7}  text"
    print(header)
    print("-" * (len(header) + col_w))
    for seg in segments:
        text_preview = seg.text if len(seg.text) <= 80 else seg.text[:77] + "..."
        print(f"{seg.start_s:>8.3f}  {seg.end_s:>7.3f}  {text_preview}")

    if approximate:
        print(
            "\nWARNING: Gemini did not return timestamps — the times above are evenly "
            "distributed across the audio duration. Intent text is correct but alignment "
            "to keyframes will be approximate."
        )
    else:
        print("\nOK: timestamps are real Gemini segment boundaries.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
