"""
Video endpoints — the front door of the whole platform.

  POST /videos              upload a screen recording; returns a job id
  GET  /videos/{video_id}   poll the pipeline; carries plan_id once ready
  GET  /videos              list this user's recordings

Until now the recording → plan pipeline existed only as a CLI script, so the
dashboard had no way to turn a recording into a Plan. This route runs the same
four stages in the background:

    upload → [video_processor]     keyframes (ffmpeg)
           → [audio_transcriber]   narration timeline (optional)
           → [keyframe_captioner]  per-frame descriptions (vision LLM)
           → [plan_generator]      structured Plan  → saved PENDING_APPROVAL

The captions are persisted to `captions/<video_id>.json`, which is what
POST /plans/{id}/correct reads to anchor a regeneration to the original
recording rather than to the previous plan alone.

Stage work is blocking (ffmpeg, vision calls), so it runs in a worker thread;
status lives in `videos/<video_id>/status.json` so a poll survives a restart.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_current_user, get_scoped_repo_dep
from app.config import get_settings
from app.schemas.auth import User
from app.services.scoping import ScopedRepo
from app.services.storage import get_storage

router = APIRouter(prefix="/videos", tags=["videos"])
log = logging.getLogger(__name__)

# Recordings only. Rejecting by extension keeps ffmpeg from being handed
# arbitrary uploaded bytes.
ALLOWED_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB


class VideoStatus(BaseModel):
    """What the dashboard polls while the pipeline runs.

    The narration fields are deliberately prominent. A recording whose audio
    failed to transcribe still produces a plan, but that plan is missing every
    spoken rule, and the user has to be told rather than left to discover it
    when the automation does the wrong thing.
    """
    video_id: str
    user_id: str
    status: str            # uploaded | processing | completed | failed
    stage: str = ""        # human-readable current stage
    filename: str = ""
    plan_id: str | None = None
    frame_count: int = 0
    narration_segments: int = 0
    # ok | approximate | no_speech | no_audio | unavailable | failed
    narration_status: str = ""
    narration_detail: str = ""
    # True when timestamps were estimated, so narration may sit on the wrong
    # frame and any spoken rule should be double-checked in the plan.
    narration_approximate: bool = False
    # Set when narration is missing or unreliable, for the UI to show plainly.
    narration_warning: str | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


def _status_key(video_id: str) -> str:
    return f"videos/{video_id}/status.json"


def _write_status(status: VideoStatus) -> None:
    """Persist pipeline progress so a poll survives a backend restart."""
    status.updated_at = datetime.utcnow().isoformat()
    get_storage().write_text(_status_key(status.video_id), status.model_dump_json(indent=2))


def _read_status(video_id: str) -> VideoStatus | None:
    """Load a recording's pipeline status, or None if there is no such job."""
    storage = get_storage()
    key = _status_key(video_id)
    if not storage.exists(key):
        return None
    try:
        return VideoStatus.model_validate_json(storage.read_text(key))
    except Exception:
        log.warning("unreadable status file for video %s", video_id, exc_info=True)
        return None


@router.post("", response_model=VideoStatus, status_code=202)
async def upload_video(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Accept a screen recording and start the recording → plan pipeline.

    Returns 202 with a video_id immediately; the caller polls
    GET /videos/{video_id} until `status` is completed and `plan_id` is set.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            f"unsupported file type {suffix!r}; expected one of {sorted(ALLOWED_SUFFIXES)}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, "uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"file is {len(data) // 1_048_576} MB; limit is {MAX_UPLOAD_BYTES // 1_048_576} MB",
        )

    video_id = uuid.uuid4().hex[:12]
    source_key = f"videos/{video_id}/source{suffix}"
    get_storage().write_bytes(source_key, data)

    status = VideoStatus(
        video_id=video_id, user_id=user.id, status="uploaded",
        stage="queued", filename=file.filename or "",
        created_at=datetime.utcnow().isoformat(),
    )
    _write_status(status)

    background.add_task(_process_video_job, video_id, source_key, user.id)
    return status


@router.get("", response_model=list[VideoStatus])
async def list_videos(user: User = Depends(get_current_user)):
    """Every recording this user has uploaded, newest first."""
    root = Path(get_settings().local_storage_root) / "videos"
    if not root.exists():
        return []
    out: list[VideoStatus] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        status = _read_status(child.name)
        if status is not None and status.user_id == user.id:
            out.append(status)
    return sorted(out, key=lambda s: s.created_at, reverse=True)


@router.get("/{video_id}", response_model=VideoStatus)
async def get_video(video_id: str, user: User = Depends(get_current_user)):
    """Poll one recording's pipeline status; 404 unless the caller owns it."""
    status = _read_status(video_id)
    if status is None or status.user_id != user.id:
        raise HTTPException(404, f"video {video_id} not found")
    return status


@router.get("/{video_id}/plan")
async def get_video_plan(video_id: str, user: User = Depends(get_current_user),
                         repo: ScopedRepo = Depends(get_scoped_repo_dep)):
    """The Plan generated from this recording, once the pipeline finishes."""
    status = _read_status(video_id)
    if status is None or status.user_id != user.id:
        raise HTTPException(404, f"video {video_id} not found")
    if not status.plan_id:
        raise HTTPException(409, f"video {video_id} has no plan yet (status: {status.status})")
    plan = await repo.get_plan(status.plan_id)
    if plan is None:
        raise HTTPException(404, f"plan {status.plan_id} not found")
    return plan


@router.get("/{video_id}/transcript")
async def get_video_transcript(video_id: str, user: User = Depends(get_current_user)):
    """The full spoken transcript for a recording, with per-segment timings.

    Kept separate from the plan so the user can check what the agent actually
    heard, which is the fastest way to explain a plan that missed a rule.
    """
    status = _read_status(video_id)
    if status is None or status.user_id != user.id:
        raise HTTPException(404, f"video {video_id} not found")
    storage = get_storage()
    key = f"transcripts/{video_id}.json"
    if not storage.exists(key):
        raise HTTPException(404, f"no transcript stored for video {video_id}")
    return json.loads(storage.read_text(key))


# ---------------------------------------------------------------------------
# The pipeline itself
# ---------------------------------------------------------------------------

async def _process_video_job(video_id: str, source_key: str, user_id: str) -> None:
    """Background task: run the four pipeline stages and save the Plan.

    Each stage updates the status file so the dashboard can show progress.
    Never raises — a failure is recorded as status="failed" with the reason.
    """
    from app.services.run_repo import get_repository

    status = _read_status(video_id)
    if status is None:
        log.error("video %s: status file vanished before processing", video_id)
        return

    try:
        status.status, status.stage = "processing", "transcribing narration"
        _write_status(status)
        result = await anyio.to_thread.run_sync(_run_pipeline, video_id, source_key, status)

        status.frame_count = result["frame_count"]
        status.narration_segments = result["narration_segments"]
        plan = result["plan"]

        await get_repository().save_plan(plan, user_id=user_id)
        status.plan_id = plan.id
        status.status, status.stage = "completed", "plan ready for review"
        status.error = None
        _write_status(status)
        log.info("video %s → plan %s (%d steps, pending approval)",
                 video_id, plan.id, len(plan.steps))

    except Exception as e:
        log.exception("video %s: pipeline failed", video_id)
        status.status = "failed"
        status.error = f"{type(e).__name__}: {e}"
        _write_status(status)


def _run_pipeline(video_id: str, source_key: str, status: VideoStatus) -> dict[str, Any]:
    """Blocking stages: narration → keyframes → captions → Plan.

    Runs in a worker thread (ffmpeg and the vision calls are synchronous).
    Captions are written to `captions/<video_id>.json` so a later plan
    correction can be anchored to the original recording, and the full
    transcript is written to `transcripts/<video_id>.json` so the spoken
    record survives independently of the plan.
    """
    from app.agent.audio_transcriber import transcribe_video_result
    from app.agent.keyframe_captioner import caption_keyframes
    from app.agent.plan_generator import generate_plan
    from app.agent.video_processor import extract_keyframes

    storage = get_storage()
    local_video = storage.local_path(source_key)

    # 1. Narration. A silent recording is fine, but a FAILED transcription is
    # not the same thing and must be reported rather than silently dropped.
    transcription = transcribe_video_result(local_video)
    narration = transcription.segments
    status.narration_status = transcription.status
    status.narration_detail = transcription.detail
    status.narration_approximate = transcription.timestamps_approximate
    status.narration_segments = len(narration)
    status.narration_warning = _narration_warning(transcription)
    if status.narration_warning:
        log.warning("video %s narration: %s", video_id, status.narration_warning)
    _write_status(status)

    # Preserve the transcript verbatim, separate from the plan it informs.
    storage.write_text(
        f"transcripts/{video_id}.json",
        json.dumps({
            "video_id": video_id,
            "status": transcription.status,
            "detail": transcription.detail,
            "backend": transcription.backend,
            "timestamps_approximate": transcription.timestamps_approximate,
            "full_text": transcription.full_text,
            "segments": [asdict(seg) for seg in narration],
        }, indent=2),
    )

    # 2. Keyframes. Narration spans are passed in so frames where the user was
    # TALKING survive dedup and downsampling even if the screen was static.
    status.stage = "extracting keyframes"
    _write_status(status)
    manifest = extract_keyframes(
        source_key, video_id=video_id, storage=storage,
        narration_windows=[(seg.start_s, seg.end_s) for seg in narration],
    )

    # 3. Captions (vision LLM), enriched with narration.
    status.stage = f"captioning {manifest.frame_count} keyframes"
    status.frame_count = manifest.frame_count
    _write_status(status)
    captions = caption_keyframes(manifest, storage=storage, narration=narration or None)

    # Persist captions — POST /plans/{id}/correct reads these to regenerate
    # against the recording instead of against the previous plan alone.
    storage.write_text(
        f"captions/{video_id}.json",
        json.dumps([asdict(c) for c in captions], indent=2),
    )

    # 4. Plan synthesis. Saved as PENDING_APPROVAL — a human reviews it
    # before it can ever run.
    status.stage = "synthesizing plan"
    _write_status(status)
    plan = generate_plan(captions, source_video_id=video_id)

    return {
        "plan": plan,
        "frame_count": manifest.frame_count,
        "narration_segments": len(narration or []),
    }


def _narration_warning(transcription) -> str | None:
    """A plain-language warning when narration is missing or unreliable.

    Returns None when the transcript is trustworthy, so the UI can show this
    field whenever it is set without deciding severity itself.
    """
    messages = {
        "approximate": (
            "Narration was transcribed but its timestamps are ESTIMATED, not "
            "measured. Spoken instructions may be attached to the wrong moment "
            "in the recording — check the plan reflects what you said."
        ),
        "no_speech": (
            "No speech was detected in this recording. The plan was built from "
            "the visuals alone, so any rule you intended to say out loud is not "
            "in it."
        ),
        "no_audio": (
            "This recording has no usable audio track. The plan was built from "
            "the visuals alone — spoken rules could not be captured."
        ),
        "unavailable": (
            "Narration could NOT be transcribed: no transcription backend was "
            "available. The plan was built from the visuals alone and is "
            "missing anything you explained out loud."
        ),
        "failed": (
            "Narration transcription FAILED. The plan was built from the "
            "visuals alone and is missing anything you explained out loud."
        ),
    }
    return messages.get(transcription.status)
