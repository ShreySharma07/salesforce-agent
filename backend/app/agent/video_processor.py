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
    storage: Storage | None = None,
    scene_threshold: float = SCENE_THRESHOLD,
) -> VideoManifest:
    """Extract keyframes from a video already in storage. Returns the
    manifest; PNG frames are written into storage at predictable keys."""
    ensure_ffmpeg()
    settings = get_settings()
    storage = storage or get_storage()
    video_id = video_id or uuid.uuid4().hex[:12]

    src = storage.local_path(source_video_key)
    if not src.exists():
        raise FileNotFoundError(f"source video not found at {source_video_key}")

    duration = probe_duration(src)

    with tempfile.TemporaryDirectory(prefix="kf_") as tmp:
        tmp_dir = Path(tmp)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-i", str(src),
            "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
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

        # Downsample evenly if we got more frames than budget allows
        if len(produced) > settings.keyframe_max_count:
            step = len(produced) / settings.keyframe_max_count
            indices = [int(i * step) for i in range(settings.keyframe_max_count)]
            produced = [produced[i] for i in indices]
            if len(timestamps) >= max(indices) + 1:
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