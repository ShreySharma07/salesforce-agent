"""
End-to-end pipeline test. Generates a tiny synthetic video with ffmpeg
(uses lavfi filters — no external assets needed), then runs the full
pipeline against it: keyframe extraction → captioning → plan.

With LLM_PROVIDER=mock (the default during dev), this finishes in a
couple of seconds and burns zero quota.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent.keyframe_captioner import caption_keyframes
from app.agent.plan_generator import generate_plan
from app.agent.video_processor import (
    SCENE_THRESHOLD,
    extract_keyframes,
    load_manifest,
)
from app.schemas.plan import Plan
from app.services.storage import LocalStorage


HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    """Create a 6-second synthetic video with 3 scene changes."""
    out = tmp_path / "synthetic.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "smptebars=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "color=c=blue:duration=2:size=320x240:rate=10",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]",
        "-map", "[outv]",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    assert out.exists() and out.stat().st_size > 0
    return out


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(root=tmp_path / "storage")


def test_extract_keyframes_produces_frames(synthetic_video, storage):
    src_key = "videos/test_001/source.mp4"
    storage.write_bytes(src_key, synthetic_video.read_bytes())

    manifest = extract_keyframes(
        src_key, video_id="test_001", storage=storage,
        scene_threshold=SCENE_THRESHOLD,
    )

    assert manifest.video_id == "test_001"
    assert manifest.frame_count >= 1, "expected at least one keyframe from a 3-scene video"
    assert manifest.duration_seconds > 5.0, "synthetic video should be ~6s"
    for kf in manifest.keyframes:
        assert storage.exists(kf.storage_key), f"missing frame at {kf.storage_key}"
        data = storage.read_bytes(kf.storage_key)
        assert data.startswith(b"\x89PNG"), "should be a valid PNG"


def test_manifest_roundtrip(synthetic_video, storage):
    src_key = "videos/test_002/source.mp4"
    storage.write_bytes(src_key, synthetic_video.read_bytes())

    extract_keyframes(src_key, video_id="test_002", storage=storage)
    loaded = load_manifest("test_002", storage=storage)

    assert loaded.video_id == "test_002"
    assert loaded.frame_count >= 1
    assert all(
        kf.storage_key.startswith("videos/test_002/frames/")
        for kf in loaded.keyframes
    )


def test_full_pipeline_with_mock_llm(synthetic_video, storage):
    """End-to-end: video -> keyframes -> captions -> Plan, with mock LLM."""
    src_key = "videos/test_003/source.mp4"
    storage.write_bytes(src_key, synthetic_video.read_bytes())

    manifest = extract_keyframes(src_key, video_id="test_003", storage=storage)
    captions = caption_keyframes(manifest, storage=storage)
    plan = generate_plan(captions, source_video_id="test_003")

    assert isinstance(plan, Plan)
    assert plan.id.startswith("plan_")
    assert plan.source_video_id == "test_003"
    assert len(plan.steps) >= 1
    encoded = plan.model_dump_json()
    assert "goal" in encoded