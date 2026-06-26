"""
Audio transcription for the video → plan pipeline.

Extracts the audio track from a recorded video and returns timestamped
narration segments. Used downstream to enrich keyframe captions with the
user's spoken intent.

Backend priority (first available wins):
  1. Gemini audio API  — primary (google-genai already a dep, billed key present)
  2. faster-whisper    — local GPU fallback
  3. openai-whisper    — local CPU fallback
  4. Graceful empty list + warning — pipeline continues without narration

All backends return the same shape:
  list[NarrationSegment]  — ordered by start_s, no gaps required
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NarrationSegment:
    start_s: float
    end_s: float
    text: str


def transcribe_video(
    video_path: Path,
    *,
    language: str | None = None,
) -> list[NarrationSegment]:
    """
    Extract and transcribe the audio track from *video_path*.

    Returns a (possibly empty) list of NarrationSegments ordered by start
    time.  Never raises — logs a warning and returns [] if all backends fail.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        logger.warning("audio_transcriber: video not found at %s, skipping narration", video_path)
        return []

    with tempfile.TemporaryDirectory(prefix="atr_") as tmp:
        audio_path = Path(tmp) / "audio.wav"
        if not _extract_audio(video_path, audio_path):
            return []

        # Check if the audio file has actual content (silent/no-audio tracks → skip)
        if audio_path.stat().st_size < 4096:
            logger.info("audio_transcriber: audio track appears empty, skipping narration")
            return []

        for backend_fn in (_try_gemini, _try_faster_whisper, _try_openai_whisper):
            try:
                segments = backend_fn(audio_path, language=language)
                if segments is not None:
                    logger.info(
                        "audio_transcriber: got %d narration segments via %s",
                        len(segments), backend_fn.__name__,
                    )
                    return segments
            except Exception as exc:
                logger.debug("audio_transcriber: %s failed: %s", backend_fn.__name__, exc)

    logger.warning(
        "audio_transcriber: all transcription backends failed. "
        "Ensure GEMINI_API_KEY is set, or install faster-whisper for local transcription."
    )
    return []


# ---------------------------------------------------------------------------
# Public test helper — Gemini only, returns (segments, timestamps_approximate)
# ---------------------------------------------------------------------------

def transcribe_video_gemini_only(
    video_path: Path,
    *,
    language: str | None = None,
) -> tuple[list[NarrationSegment], bool]:
    """
    Run only the Gemini backend end-to-end and return
    (segments, timestamps_are_approximate).

    Use this to verify Gemini output before committing to the full pipeline.
    Raises RuntimeError if audio extraction or the Gemini call fails.
    """
    video_path = Path(video_path)
    with tempfile.TemporaryDirectory(prefix="atr_test_") as tmp:
        audio_path = Path(tmp) / "audio.wav"
        if not _extract_audio(video_path, audio_path):
            raise RuntimeError(f"ffmpeg could not extract audio from {video_path}")
        if audio_path.stat().st_size < 4096:
            return [], False
        segs, approx = _gemini_transcribe(audio_path, language=language)
        if segs is None:
            raise RuntimeError(
                "Gemini transcription failed — check GEMINI_API_KEY is set and run "
                "with --debug to see the full error."
            )
        return segs, approx


# ---------------------------------------------------------------------------
# Merge helper used by caption_keyframes
# ---------------------------------------------------------------------------

def narration_at(
    frame_ts: float,
    narration: list[NarrationSegment],
    *,
    window_s: float = 2.0,
) -> str:
    """
    Return the concatenated narration text that overlaps with the frame at
    *frame_ts* (within ±window_s).  Returns "" if nothing overlaps.
    """
    parts = [
        seg.text.strip()
        for seg in narration
        if seg.start_s <= frame_ts + window_s and seg.end_s >= frame_ts - window_s
        and seg.text.strip()
    ]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Private: audio extraction
# ---------------------------------------------------------------------------

def _extract_audio(video_path: Path, out_wav: Path) -> bool:
    """Use ffmpeg to extract a mono 16 kHz WAV. Returns True on success."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn",           # no video
        "-ac", "1",      # mono
        "-ar", "16000",  # 16 kHz — standard ASR rate
        "-f", "wav",
        str(out_wav),
        "-y",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("audio_transcriber: ffmpeg audio extract failed: %s", result.stderr[-500:])
        return False
    return True


def _probe_audio_duration(audio_path: Path) -> float | None:
    """Return audio duration in seconds via ffprobe, or None on failure."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Private: transcription backends
# ---------------------------------------------------------------------------

def _try_gemini(audio_path: Path, *, language: str | None) -> list[NarrationSegment] | None:
    """Thin wrapper around _gemini_transcribe for the backend-loop interface."""
    segs, _ = _gemini_transcribe(audio_path, language=language)
    return segs if segs is not None else None


def _gemini_transcribe(
    audio_path: Path,
    *,
    language: str | None = None,
) -> tuple[list[NarrationSegment] | None, bool]:
    """
    Call Gemini with the WAV file and ask for timestamped JSON segments.

    Returns (segments, timestamps_are_approximate).
    Returns (None, False) if Gemini is unavailable or the call fails.
    """
    try:
        from google import genai  # type: ignore[import]
        from google.genai import types
    except ImportError:
        return None, False

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        # Try loading from the backend .env as a fallback (e.g. when running scripts directly)
        try:
            from dotenv import load_dotenv as _load_dotenv
            _env = Path(__file__).resolve().parent.parent.parent / ".env"
            _load_dotenv(_env)
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        except ImportError:
            pass
    if not api_key:
        logger.warning("audio_transcriber: no GEMINI_API_KEY set, skipping Gemini backend")
        return None, False

    client = genai.Client(api_key=api_key)
    audio_bytes = audio_path.read_bytes()

    lang_hint = f" The speaker's language is {language}." if language else ""
    prompt = (
        "Transcribe all speech in this audio file into sentence-level segments."
        f"{lang_hint}"
        " Return ONLY a JSON array — no prose, no markdown fences — where each element is:\n"
        '  {"start_s": <float>, "end_s": <float>, "text": "<utterance>"}\n'
        "start_s and end_s are the segment's start and end times in seconds from the"
        " beginning of the audio. Omit silent sections. If the audio contains no speech,"
        " return []."
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                types.Part(text=prompt),
            ],
        )
    except Exception as exc:
        logger.warning("audio_transcriber: Gemini API call failed: %s", exc)
        return None, False

    raw = (response.text or "").strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # --- Attempt 1: parse as JSON array with timestamps -----------------------
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            segs = _parse_timestamped_json(parsed)
            if segs is not None:
                return segs, False   # real timestamps
    except (json.JSONDecodeError, ValueError):
        pass

    # --- Attempt 2: Gemini returned plain text — even-split fallback ----------
    plain_text = _extract_plain_text(raw)
    if not plain_text:
        return [], False

    duration = _probe_audio_duration(audio_path)
    if not duration or duration <= 0:
        duration = max(len(plain_text) / 15.0, 1.0)  # rough estimate: 15 chars/sec

    logger.warning(
        "audio_transcriber: Gemini did not return timestamped JSON; "
        "splitting text evenly across %.1fs audio — timestamps are APPROXIMATE", duration,
    )
    segs = _even_split(plain_text, duration)
    return segs, True   # approximate timestamps


def _parse_timestamped_json(parsed: list) -> list[NarrationSegment] | None:
    """
    Convert a list of dicts into NarrationSegments.
    Returns None if any element is missing start_s or end_s
    (signals that we should fall back to even-split).
    """
    result = []
    for item in parsed:
        if not isinstance(item, dict):
            return None
        if "start_s" not in item or "end_s" not in item:
            return None
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        result.append(NarrationSegment(
            start_s=round(float(item["start_s"]), 3),
            end_s=round(float(item["end_s"]), 3),
            text=text,
        ))
    return result


def _extract_plain_text(raw: str) -> str:
    """Best-effort: pull readable text from a non-JSON Gemini response."""
    # Remove JSON-looking noise and collapse whitespace
    text = re.sub(r"[\[\]{}\"]", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _even_split(text: str, duration_s: float) -> list[NarrationSegment]:
    """
    Split *text* into sentences and distribute them evenly across *duration_s*.
    Used as a last-resort approximation when Gemini returns no timestamps.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return [NarrationSegment(start_s=0.0, end_s=round(duration_s, 3), text=text)]

    slot = duration_s / len(sentences)
    result = []
    for i, sent in enumerate(sentences):
        result.append(NarrationSegment(
            start_s=round(i * slot, 3),
            end_s=round((i + 1) * slot, 3),
            text=sent,
        ))
    return result


def _try_faster_whisper(audio_path: Path, *, language: str | None) -> list[NarrationSegment] | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import]
    except ImportError:
        return None

    model_size = os.environ.get("WHISPER_MODEL", "base")
    device = "cuda" if _cuda_available() else "cpu"
    model = WhisperModel(model_size, device=device, compute_type="int8")
    segments_iter, _ = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        word_timestamps=False,
    )
    return [
        NarrationSegment(start_s=round(seg.start, 3), end_s=round(seg.end, 3), text=seg.text.strip())
        for seg in segments_iter
        if seg.text.strip()
    ]


def _try_openai_whisper(audio_path: Path, *, language: str | None) -> list[NarrationSegment] | None:
    try:
        import whisper  # type: ignore[import]
    except ImportError:
        return None

    model_name = os.environ.get("WHISPER_MODEL", "base")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = whisper.load_model(model_name)

    opts: dict = {"verbose": False}
    if language:
        opts["language"] = language
    result = model.transcribe(str(audio_path), **opts)

    return [
        NarrationSegment(
            start_s=round(seg["start"], 3),
            end_s=round(seg["end"], 3),
            text=seg["text"].strip(),
        )
        for seg in result.get("segments", [])
        if seg.get("text", "").strip()
    ]


def _cuda_available() -> bool:
    try:
        import torch  # type: ignore[import]
        return torch.cuda.is_available()
    except ImportError:
        pass
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False
