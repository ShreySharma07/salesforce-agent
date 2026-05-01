"""
Keyframe captioner. For each keyframe in a manifest, asks the LLM
"what's happening in this frame, in the context of what came before?"

Returns a list of FrameCaption objects that the plan generator consumes.

Cost optimization: we batch frames in groups of N and pass them with a
shared "context so far" so the model can reason about progression rather
than treating each frame in isolation. Massively reduces token usage.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agent.video_processor import Keyframe, VideoManifest
from app.core.llm.client import LLMClient
from app.core.llm.factory import get_llm_client
from app.services.storage import Storage, get_storage


# How many frames per LLM call. Larger = cheaper but slower per call.
FRAMES_PER_BATCH = 6


@dataclass
class FrameCaption:
    keyframe_index: int
    timestamp_seconds: float
    description: str


CAPTION_SYSTEM_PROMPT = """You are watching a screen recording of someone performing a task on their computer. You will receive frames in chronological batches. For EACH frame, output ONE concise sentence describing what is visible AND what the user appears to be doing. Focus on:
  - The application/website visible
  - What the user is interacting with (input field, button, menu)
  - What appears to have just happened (e.g. "form submitted, success message visible")

DO NOT speculate beyond what is visible. DO NOT add filler.
Output as a JSON array of strings, one per frame, in order. No prose, no fences."""


def caption_keyframes(
    manifest: VideoManifest,
    *,
    storage: Storage | None = None,
    llm: LLMClient | None = None,
) -> list[FrameCaption]:
    storage = storage or get_storage()
    llm = llm or get_llm_client()

    captions: list[FrameCaption] = []
    frames = manifest.keyframes
    rolling_context = ""  # accumulates a short summary across batches

    for batch_start in range(0, len(frames), FRAMES_PER_BATCH):
        batch = frames[batch_start : batch_start + FRAMES_PER_BATCH]
        images = [storage.read_bytes(kf.storage_key) for kf in batch]

        prompt_lines = [
            f"Total frames in this video: {len(frames)}.",
            f"You are receiving frames {batch_start} through {batch_start + len(batch) - 1}.",
        ]
        if rolling_context:
            prompt_lines.append(f"Summary of what happened earlier: {rolling_context}")
        prompt_lines.append(
            f"Describe each of the {len(batch)} frames in this batch. "
            "Output a JSON array with exactly that many strings."
        )
        prompt = "\n".join(prompt_lines)

        response = llm.generate(
            prompt=prompt,
            purpose="keyframe_understanding",
            system=CAPTION_SYSTEM_PROMPT,
            images=images,
            json_mode=True,
            max_tokens=2000,
        )

        descriptions = _parse_caption_array(response.text, expected=len(batch))
        for kf, desc in zip(batch, descriptions, strict=False):
            captions.append(
                FrameCaption(
                    keyframe_index=kf.index,
                    timestamp_seconds=kf.timestamp_seconds,
                    description=desc,
                )
            )

        # Update rolling context with a brief summary of what we just saw.
        rolling_context = _summarize_for_context(captions[-len(batch):], rolling_context)

    return captions


def _parse_caption_array(text: str, *, expected: int) -> list[str]:
    """Parse the JSON array of strings, tolerating fences/extra text."""
    import json
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            out = [str(x) for x in parsed]
        else:
            out = [str(parsed)]
    except json.JSONDecodeError:
        # Mock client returns a single descriptive string; fall back
        # to repeating it across frames so the rest of the pipeline runs.
        out = [t] * expected
    # Pad/truncate to expected count so the caller can zip safely
    if len(out) < expected:
        out += ["(no caption returned)"] * (expected - len(out))
    return out[:expected]


def _summarize_for_context(recent: list[FrameCaption], previous: str) -> str:
    """Compact context for the next batch. Heuristic: keep last 3
    descriptions, joined. Cheap, no LLM call."""
    bits = previous.split(" | ") if previous else []
    bits.extend(c.description for c in recent[-3:])
    return " | ".join(bits[-6:])  # cap at 6 most recent