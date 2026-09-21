"""
Video processor. Extracts keyframes from a recorded screen-capture video.

Strategy:
  1. Run ffmpeg with `select='gt(scene,T)'` to keep only frames where the
     scene-change score exceeds a threshold. Drops the visually-static
     frames that would waste vision tokens.
  2. Cap the result at settings.keyframe_max_count to bound LLM cost.
  3. Persist frames as PNG into storage under "videos/<vid>/frames/".

Saves a manifest.json with frame metadata (timestamp, storage key) so
downstream code (captioner) can iterate without re-decoding the video.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import get_settings
from app.services.storage import Storage, get_storage


# Scene-change threshold. 0.0-1.0 where higher = stricter (fewer frames).
# 0.10 has worked well in practice for screen recordings.
SCENE_THRESHOLD = 0.10


@dataclass
class Keyframe:
    index: int                # 0-based order in the video
    timestamp_seconds: float  # offset from video start
    storage_key: str          # where the PNG lives, "videos/<vid>/frames/00003.png"


@dataclass
class VideoManifest:
    video_id: str
    source_video_key: str
    duration_seconds: float
    frame_count: int
    keyframes: list[Keyframe]


def ensure_ffmpeg() -> None:
    """Fail fast with an install hint if ffmpeg/ffprobe are not on PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(
                f"{tool} not found in PATH. Install with `brew install ffmpeg` "
                "(macOS) or `apt install ffmpeg` (Linux)."
            )


def probe_duration(video_path: Path) -> float:
    """Return the video duration in seconds via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError as e:
        raise RuntimeError(f"ffprobe returned unparseable duration: {out.stdout!r}") from e


def extract_keyframes(
    source_video_key: str,
    video_id: str | None = None,
    *,
    storage: "Storage | None" = None,
    scene_threshold: float | None = None,
    narration_windows: "list | None" = None,
) -> "VideoManifest":
    """Extract keyframes via HYBRID sampling: a steady time-based rate (so no
    interaction is missed) PLUS scene-change frames (so page transitions are
    captured). Uses settings.keyframe_extraction_fps for the time rate.

    `narration_windows` is a list of (start_s, end_s) spans where the user was
    SPEAKING. Frames inside those spans are protected from both the
    near-identical dedup and the frame-budget downsample. Without this, the
    most valuable moments in a recording get thrown away: someone explaining a
    business rule ("only escalate the ones for Acme") usually stops clicking
    while they talk, so the screen is static, so the frame looks like a
    duplicate, so the rule never reaches the planner.
    """
    ensure_ffmpeg()
    settings = get_settings()
    storage = storage or get_storage()
    video_id = video_id or uuid.uuid4().hex[:12]
 
    fps = getattr(settings, "keyframe_extraction_fps", 1.0) or 1.0
    scene_thr = scene_threshold if scene_threshold is not None else 0.04
 
    src = storage.local_path(source_video_key)
    if not src.exists():
        raise FileNotFoundError(f"source video not found at {source_video_key}")
 
    duration = probe_duration(src)
 
    with tempfile.TemporaryDirectory(prefix="kf_") as tmp:
        tmp_dir = Path(tmp)
        # The filter keeps a frame if EITHER:
        #   - it's on the time grid (every 1/fps seconds), OR
        #   - it's a significant scene change.
        # isnan(prev_selected_t) handles the very first frame.
        vf = (
            f"select='"
            f"gt(scene,{scene_thr})"
            f"+isnan(prev_selected_t)"
            f"+gte(t-prev_selected_t,{1.0/fps:.4f})"
            f"',showinfo"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-i", str(src),
            "-vf", vf,
            "-vsync", "vfr",
            "-frame_pts", "1",
            str(tmp_dir / "frame_%05d.png"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (rc={proc.returncode}). stderr:\n{proc.stderr[-2000:]}"
            )
 
        produced = sorted(tmp_dir.glob("frame_*.png"))
        timestamps = _parse_showinfo_timestamps(proc.stderr)

        windows = _normalize_windows(narration_windows)

        # Drop adjacent near-identical frames (idle periods) to avoid wasting
        # caption tokens on duplicates — cheap byte-size heuristic. Narrated
        # frames are exempt: a static screen with speech over it is the
        # opposite of redundant.
        produced, timestamps = _dedup_adjacent(produced, timestamps, windows=windows)

        # Cap at budget, keeping every narrated frame we can.
        if len(produced) > settings.keyframe_max_count:
            produced, timestamps = _downsample_to_budget(
                produced, timestamps, settings.keyframe_max_count, windows,
            )
 
        keyframes: list[Keyframe] = []
        for idx, frame_path in enumerate(produced):
            ts = (
                timestamps[idx]
                if idx < len(timestamps)
                else (idx * (duration / max(1, len(produced))))
            )
            key = f"videos/{video_id}/frames/{idx:05d}.png"
            storage.write_bytes(key, frame_path.read_bytes())
            keyframes.append(
                Keyframe(index=idx, timestamp_seconds=round(ts, 3), storage_key=key)
            )
 
    manifest = VideoManifest(
        video_id=video_id,
        source_video_key=source_video_key,
        duration_seconds=round(duration, 3),
        frame_count=len(keyframes),
        keyframes=keyframes,
    )
    storage.write_text(
        f"videos/{video_id}/manifest.json",
        json.dumps(_manifest_to_dict(manifest), indent=2),
    )
    return manifest
 
 
# How far outside a spoken segment a frame still counts as "narrated".
# Speech about an on-screen thing routinely starts just before or trails just
# after the action it describes.
NARRATION_PAD_SECONDS = 1.5


def _normalize_windows(narration_windows) -> list:
    """Coerce narration spans into a plain list of (start, end) float pairs.

    Accepts NarrationSegment objects or raw tuples so callers can pass either
    without importing the transcriber.
    """
    out: list = []
    for w in narration_windows or []:
        try:
            if isinstance(w, (tuple, list)) and len(w) >= 2:
                out.append((float(w[0]), float(w[1])))
            else:
                out.append((float(w.start_s), float(w.end_s)))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _is_narrated(ts: float, windows: list, pad: float = NARRATION_PAD_SECONDS) -> bool:
    """True if the user was speaking at (or right around) this timestamp."""
    return any(start - pad <= ts <= end + pad for start, end in windows)


def _dedup_adjacent(frames: list, timestamps: list, *, min_pct_diff: float = 0.5,
                    windows: list | None = None):
    """Remove a frame if it's within min_pct_diff% file-size of the previous
    kept frame (a cheap 'looks basically identical' proxy that avoids decoding
    pixels). Keeps the first frame always. Conservative — only drops obvious
    idle duplicates, never near-distinct interaction frames.

    A frame whose timestamp falls inside a narration window is always kept,
    however similar it looks: the screen being static is exactly what happens
    while someone explains a rule out loud.
    """
    if not frames:
        return frames, timestamps
    windows = windows or []
    kept_f = [frames[0]]
    kept_t = [timestamps[0]] if timestamps else []
    last_size = frames[0].stat().st_size
    for i in range(1, len(frames)):
        size = frames[i].stat().st_size
        diff_pct = abs(size - last_size) / max(last_size, 1) * 100
        ts = timestamps[i] if i < len(timestamps) else None
        narrated = ts is not None and _is_narrated(ts, windows)
        if diff_pct >= min_pct_diff or narrated:
            kept_f.append(frames[i])
            if i < len(timestamps):
                kept_t.append(timestamps[i])
            last_size = size
    return kept_f, kept_t


def _downsample_to_budget(frames: list, timestamps: list, budget: int, windows: list):
    """Reduce to `budget` frames, keeping narrated ones in preference.

    Narrated frames are taken first, then the remaining budget is filled with
    evenly spaced frames from the rest, so coverage stays uniform while the
    spoken parts survive. Falls back to plain even sampling when nothing is
    narrated.
    """
    n = len(frames)
    if n <= budget:
        return frames, timestamps

    narrated_idx = [
        i for i in range(n)
        if i < len(timestamps) and _is_narrated(timestamps[i], windows)
    ]
    if not narrated_idx:
        step = n / budget
        chosen = sorted({int(i * step) for i in range(budget)})
    else:
        # Narration gets priority but not the whole budget. In a heavily
        # narrated recording almost every frame qualifies, and spending the
        # entire budget there would abandon coverage of the rest of the task.
        # Half is the cap; within that, sample the narrated frames evenly so
        # the whole spoken span is represented rather than just its start.
        narr_budget = min(len(narrated_idx), max(1, budget // 2))
        n_step = len(narrated_idx) / narr_budget
        keep = {narrated_idx[int(i * n_step)] for i in range(narr_budget)}

        remaining = budget - len(keep)
        if remaining > 0:
            others = [i for i in range(n) if i not in keep]
            if others:
                o_step = len(others) / remaining
                keep.update(others[int(i * o_step)] for i in range(remaining))
        chosen = sorted(keep)

    kept_f = [frames[i] for i in chosen]
    kept_t = [timestamps[i] for i in chosen if i < len(timestamps)]
    return kept_f, kept_t
 


def _parse_showinfo_timestamps(stderr: str) -> list[float]:
    """Pull pts_time values from ffmpeg's showinfo output, in order."""
    out: list[float] = []
    for line in stderr.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            try:
                token = line.split("pts_time:", 1)[1].split()[0]
                out.append(float(token))
            except (IndexError, ValueError):
                continue
    return out


def _manifest_to_dict(m: VideoManifest) -> dict:
    """Serialize a VideoManifest (and its Keyframes) for manifest.json."""
    return {
        "video_id": m.video_id,
        "source_video_key": m.source_video_key,
        "duration_seconds": m.duration_seconds,
        "frame_count": m.frame_count,
        "keyframes": [asdict(k) for k in m.keyframes],
    }


def load_manifest(video_id: str, *, storage: Storage | None = None) -> VideoManifest:
    """Reload a previously written manifest.json for a video id."""
    storage = storage or get_storage()
    raw = storage.read_text(f"videos/{video_id}/manifest.json")
    data = json.loads(raw)
    keyframes = [Keyframe(**k) for k in data["keyframes"]]
    return VideoManifest(
        video_id=data["video_id"],
        source_video_key=data["source_video_key"],
        duration_seconds=data["duration_seconds"],
        frame_count=data["frame_count"],
        keyframes=keyframes,
    )