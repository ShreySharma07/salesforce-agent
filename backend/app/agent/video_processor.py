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
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(
                f"{tool} not found in PATH. Install with `brew install ffmpeg` "
                "(macOS) or `apt install ffmpeg` (Linux)."
            )


def probe_duration(video_path: Path) -> float:
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
) -> "VideoManifest":
    """Extract keyframes via HYBRID sampling: a steady time-based rate (so no
    interaction is missed) PLUS scene-change frames (so page transitions are
    captured). Uses settings.keyframe_extraction_fps for the time rate."""
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
 
        # Drop adjacent near-identical frames (idle periods) to avoid wasting
        # caption tokens on duplicates — cheap byte-size heuristic.
        produced, timestamps = _dedup_adjacent(produced, timestamps)
 
        # Cap at budget by even downsampling.
        if len(produced) > settings.keyframe_max_count:
            step = len(produced) / settings.keyframe_max_count
            indices = [int(i * step) for i in range(settings.keyframe_max_count)]
            produced = [produced[i] for i in indices]
            if len(timestamps) >= (max(indices) + 1):
                timestamps = [timestamps[i] for i in indices]
 
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
 
 
def _dedup_adjacent(frames: list, timestamps: list, *, min_pct_diff: float = 0.5):
    """Remove a frame if it's within min_pct_diff% file-size of the previous
    kept frame (a cheap 'looks basically identical' proxy that avoids decoding
    pixels). Keeps the first frame always. Conservative — only drops obvious
    idle duplicates, never near-distinct interaction frames."""
    if not frames:
        return frames, timestamps
    kept_f = [frames[0]]
    kept_t = [timestamps[0]] if timestamps else []
    last_size = frames[0].stat().st_size
    for i in range(1, len(frames)):
        size = frames[i].stat().st_size
        diff_pct = abs(size - last_size) / max(last_size, 1) * 100
        if diff_pct >= min_pct_diff:
            kept_f.append(frames[i])
            if i < len(timestamps):
                kept_t.append(timestamps[i])
            last_size = size
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
    return {
        "video_id": m.video_id,
        "source_video_key": m.source_video_key,
        "duration_seconds": m.duration_seconds,
        "frame_count": m.frame_count,
        "keyframes": [asdict(k) for k in m.keyframes],
    }


def load_manifest(video_id: str, *, storage: Storage | None = None) -> VideoManifest:
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