import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.audio.pcm_utils import pcm_to_wav_bytes
from orchestrator.stt.whisper_client import WhisperTranscriptSegment


logger = logging.getLogger("orchestrator.tools.recorder")


def compute_hotword_stop_trim_seconds(
    *,
    armed_ts: float | None,
    stop_ts: float | None,
    extra_trim_ms: int = 900,
    max_trim_ms: int = 8000,
) -> float:
    """Compute how much audio to trim from the tail after a hotword-armed recorder stop.

    The trim window removes everything from the hotword arm moment to the end of the
    recording, plus a small extra amount before the arm moment to catch the spoken hotword.
    """
    if armed_ts is None or stop_ts is None:
        return 0.0
    try:
        armed = float(armed_ts)
        stop = float(stop_ts)
    except Exception:
        return 0.0

    if stop <= armed:
        return max(0.0, float(extra_trim_ms) / 1000.0)

    trim_seconds = (stop - armed) + (max(0, int(extra_trim_ms)) / 1000.0)
    if max_trim_ms > 0:
        trim_seconds = min(trim_seconds, max_trim_ms / 1000.0)
    return max(0.0, trim_seconds)


def _canonicalize(text: str) -> str:
    value = (text or "").strip().lower()
    if not value:
        return ""
    value = value.replace("’", "'")
    value = re.sub(r"[^a-z0-9\s']+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _format_seconds(total_seconds: float) -> str:
    total_seconds = max(0.0, float(total_seconds))
    minutes = int(total_seconds // 60)
    seconds = total_seconds - (minutes * 60)
    return f"{minutes:02d}:{seconds:05.2f}"


def _format_spoken_duration(total_seconds: float) -> str:
    total_rounded = max(0, int(round(float(total_seconds))))
    minutes = total_rounded // 60
    seconds = total_rounded % 60
    parts: list[str] = []
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if seconds or not parts:
        parts.append(f"{seconds} second" + ("s" if seconds != 1 else ""))
    return " ".join(parts)


def _pipeline_from_pretrained_with_token(model_ref: str, token: str):
    from pyannote.audio import Pipeline

    if token:
        try:
            return Pipeline.from_pretrained(model_ref, token=token)
        except TypeError:
            return Pipeline.from_pretrained(model_ref, use_auth_token=token)
    return Pipeline.from_pretrained(model_ref)


def _should_surface_diarization_note(note: str) -> bool:
    value = (note or "").strip().lower()
    if not value:
        return False

    suppressed_prefixes = (
        "pyannote diarization disabled",
        "pyannote.audio is not installed",
        "pyannote enabled but pyannote_auth_token is missing",
    )
    return not any(value.startswith(prefix) for prefix in suppressed_prefixes)


@dataclass
class RecorderStopResult:
    response: str
    audio_path: str
    transcript_path: str
    diarization_path: str = ""
    duration_seconds: float = 0.0


class RecorderTool:
    def __init__(
        self,
        *,
        workspace_root: Path,
        output_dir: str,
        sample_rate: int,
        whisper_client: Any,
        pyannote_enabled: bool,
        pyannote_auth_token: str,
        pyannote_model: str,
        pyannote_client: Any = None,
        on_recording_started: Any = None,
        on_recording_stopped: Any = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.output_root = self.workspace_root / output_dir
        self.sample_rate = int(sample_rate)
        self.whisper_client = whisper_client
        self.pyannote_client = pyannote_client
        self.pyannote_enabled = bool(pyannote_enabled)
        self.pyannote_auth_token = (pyannote_auth_token or "").strip()
        self.pyannote_model = (pyannote_model or "pyannote/speaker-diarization-3.1").strip()
        self.on_recording_started = on_recording_started
        self.on_recording_stopped = on_recording_stopped

        self._lock = asyncio.Lock()
        self._append_lock = threading.Lock()
        self._recording_active = False
        self._recording_started_ts = 0.0
        self._frames: list[bytes] = []
        self._postprocess_tasks: set[asyncio.Task[Any]] = set()

    async def _invoke_hook(self, hook: Any) -> None:
        if hook is None:
            return
        try:
            result = hook()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning("Recorder hook failed: %s", exc)

    def is_recording(self) -> bool:
        return self._recording_active

    def append_frame(self, frame: bytes) -> None:
        if not self._recording_active:
            return
        if not frame:
            return
        with self._append_lock:
            self._frames.append(frame)

    def should_stop_from_transcript(self, transcript: str) -> bool:
        text = _canonicalize(transcript)
        if not text:
            return False
        patterns = (
            r"\b(stop|end|finish)\s+(the\s+)?record(ing)?\b",
            r"\b(stop|end|finish)\s+recorder\b",
            r"\brecorder\s+off\b",
            r"\bthat'?s\s+enough\s+recording\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    def should_start_from_transcript(self, transcript: str) -> bool:
        text = _canonicalize(transcript)
        if not text:
            return False
        patterns = (
            r"\b(start|begin)\s+(the\s+)?record(ing)?\b",
            r"\b(recorder\s+on)\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    def should_report_status_from_transcript(self, transcript: str) -> bool:
        text = _canonicalize(transcript)
        if not text:
            return False
        return bool(re.search(r"\b(recorder|recording)\s+status\b", text))

    async def try_handle_fast_path(self, transcript: str) -> dict[str, Any] | None:
        if self.should_start_from_transcript(transcript):
            return await self.start_recording()
        if self.should_stop_from_transcript(transcript):
            stop_result = await self.stop_recording(reason="voice command")
            return {
                "success": True,
                "response": stop_result.response,
                "audio_path": stop_result.audio_path,
                "transcript_path": stop_result.transcript_path,
                "diarization_path": stop_result.diarization_path,
                "duration_seconds": stop_result.duration_seconds,
            }
        if self.should_report_status_from_transcript(transcript):
            return await self.execute_tool(action="status")
        return None

    async def execute_tool(self, action: str = "status") -> dict[str, Any]:
        normalized = str(action or "status").strip().lower()
        if normalized == "start":
            return await self.start_recording()
        if normalized == "stop":
            result = await self.stop_recording(reason="manual")
            return {
                "success": True,
                "response": result.response,
                "audio_path": result.audio_path,
                "transcript_path": result.transcript_path,
                "diarization_path": result.diarization_path,
                "duration_seconds": result.duration_seconds,
            }
        if normalized == "status":
            if self._recording_active:
                elapsed = max(0.0, time.time() - self._recording_started_ts)
                return {
                    "success": True,
                    "recording": True,
                    "response": f"Recorder is active ({elapsed:.1f}s).",
                }
            return {
                "success": True,
                "recording": False,
                "response": "Recorder is idle.",
            }
        return {
            "success": False,
            "response": "Recorder action must be start, stop, or status.",
        }

    async def start_recording(self) -> dict[str, Any]:
        async with self._lock:
            if self._recording_active:
                elapsed = max(0.0, time.time() - self._recording_started_ts)
                return {
                    "success": True,
                    "response": f"Recording is already in progress ({elapsed:.1f}s).",
                }

            self.output_root.mkdir(parents=True, exist_ok=True)
            with self._append_lock:
                self._frames = []
            self._recording_started_ts = time.time()
            self._recording_active = True

        logger.info("Recorder started (output_dir=%s)", self.output_root)
        await self._invoke_hook(self.on_recording_started)
        return {
            "success": True,
            "response": "Starting recording",
        }

    async def stop_recording(self, *, reason: str = "manual", trim_tail_seconds: float = 0.0) -> RecorderStopResult:
        async with self._lock:
            if not self._recording_active:
                return RecorderStopResult(
                    response="Recorder is not currently active.",
                    audio_path="",
                    transcript_path="",
                    diarization_path="",
                    duration_seconds=0.0,
                )

            self._recording_active = False
            started_ts = self._recording_started_ts
            self._recording_started_ts = 0.0
            with self._append_lock:
                frames = self._frames
                self._frames = []

        # Transition recorder/UI state immediately. File writing and post-processing
        # continue after this, but recording mode itself is already over.
        await self._invoke_hook(self.on_recording_stopped)

        pcm = b"".join(frames)
        if trim_tail_seconds > 0 and pcm:
            bytes_per_second = max(1, int(self.sample_rate) * 2)
            trim_bytes = int(max(0.0, float(trim_tail_seconds)) * bytes_per_second)
            if trim_bytes > 0:
                if trim_bytes >= len(pcm):
                    pcm = b""
                else:
                    pcm = pcm[:-trim_bytes]
        if not pcm:
            return RecorderStopResult(
                response="Stopped recording",
                audio_path="",
                transcript_path="",
                diarization_path="",
                duration_seconds=0.0,
            )

        wav_bytes = pcm_to_wav_bytes(pcm, self.sample_rate)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        audio_path = self.output_root / f"recording-{stamp}.wav"
        transcript_path = self.output_root / f"recording-{stamp}.transcript.txt"
        diarization_path = self.output_root / f"recording-{stamp}.diarization.txt"

        audio_path.write_bytes(wav_bytes)

        bytes_per_second = max(1, int(self.sample_rate) * 2)
        duration_s = float(len(pcm)) / float(bytes_per_second)
        transcript_path.write_text("", encoding="utf-8")

        self._schedule_postprocess(
            wav_bytes=wav_bytes,
            audio_path=audio_path,
            transcript_path=transcript_path,
            diarization_path=diarization_path,
        )

        response = (
            f"Finished recording {_format_spoken_duration(duration_s)} of audio. "
            f"Created files: {audio_path.name}, {transcript_path.name}."
        )
        logger.info(
            "Recorder stopped: audio=%s transcript=%s diarization_target=%s duration=%s (post-processing queued)",
            audio_path,
            transcript_path,
            diarization_path,
            _format_seconds(duration_s),
        )
        return RecorderStopResult(
            response=response,
            audio_path=str(audio_path),
            transcript_path=str(transcript_path),
            diarization_path=str(diarization_path),
            duration_seconds=duration_s,
        )

    def _schedule_postprocess(
        self,
        *,
        wav_bytes: bytes,
        audio_path: Path,
        transcript_path: Path,
        diarization_path: Path,
    ) -> None:
        task = asyncio.create_task(
            self._finalize_recording_files(
                wav_bytes=wav_bytes,
                audio_path=audio_path,
                transcript_path=transcript_path,
                diarization_path=diarization_path,
            )
        )
        self._postprocess_tasks.add(task)
        task.add_done_callback(self._postprocess_tasks.discard)

    async def _finalize_recording_files(
        self,
        *,
        wav_bytes: bytes,
        audio_path: Path,
        transcript_path: Path,
        diarization_path: Path,
    ) -> None:
        transcript_text = ""
        transcript_segments: list[WhisperTranscriptSegment] = []
        diarization_rows: list[dict[str, Any]] = []
        diarization_error = ""

        try:
            transcript_result = await asyncio.to_thread(self.whisper_client.transcribe_detailed, wav_bytes)
            transcript_text = (transcript_result.text or "").strip()
            transcript_segments = list(transcript_result.segments or [])
        except Exception as exc:
            logger.warning("Recorder whisper transcription failed: %s", exc)

        try:
            diarization_rows, diarization_error = await asyncio.to_thread(self._run_pyannote, audio_path)
        except Exception as exc:
            diarization_rows = []
            logger.warning("Recorder diarization post-process failed: %s", exc)
            diarization_error = str(exc)

        transcript_path.write_text((transcript_text or "").strip(), encoding="utf-8")

        diarization_text = self._format_diarization_text(
            diarization_rows=diarization_rows,
            transcript_segments=transcript_segments,
        )
        if diarization_text.strip():
            diarization_path.write_text(diarization_text, encoding="utf-8")
        else:
            try:
                if diarization_path.exists():
                    diarization_path.unlink()
            except Exception:
                pass

        logger.info(
            "Recorder post-processing complete: transcript=%s diarization=%s diarization_available=%s diarization_error=%s",
            transcript_path,
            diarization_path,
            bool(diarization_text.strip()),
            bool(diarization_error),
        )

    def _run_pyannote(self, audio_path: Path) -> tuple[list[dict[str, Any]], str]:
        if not self.pyannote_enabled:
            return [], "pyannote diarization disabled"

        if self.pyannote_client is not None:
            try:
                rows = self.pyannote_client.diarize(audio_path.read_bytes())
                rows.sort(key=lambda item: (item["start"], item["end"]))
                return rows, ""
            except Exception as exc:
                msg = str(exc)
                if "PYANNOTE_AUTH_TOKEN is missing" in msg:
                    logger.info(
                        "Recorder remote pyannote unavailable: PYANNOTE_AUTH_TOKEN is missing on backend; continuing without diarization"
                    )
                    return [], ""
                else:
                    logger.warning("Recorder remote pyannote diarization failed: %s", exc)
                    return [], f"remote pyannote diarization failed: {exc}"

        # Remote client not configured — attempt local pyannote install
        remote_error = ""
        model_path = Path(self.pyannote_model)
        use_local_model = model_path.exists()

        token = self.pyannote_auth_token or os.environ.get("PYANNOTE_AUTH_TOKEN", "").strip()
        if not token and not use_local_model:
            if remote_error:
                return [], f"{remote_error}; pyannote enabled but PYANNOTE_AUTH_TOKEN is missing"
            return [], "pyannote enabled but PYANNOTE_AUTH_TOKEN is missing"

        try:
            from pyannote.audio import Pipeline
        except Exception:
            return [], "pyannote.audio is not installed"

        try:
            if use_local_model:
                pipeline = _pipeline_from_pretrained_with_token(str(model_path), token)
            else:
                pipeline = _pipeline_from_pretrained_with_token(self.pyannote_model, token)
            diarization = pipeline(str(audio_path))
            rows: list[dict[str, Any]] = []
            for segment, _, speaker in diarization.itertracks(yield_label=True):
                rows.append(
                    {
                        "start": float(segment.start),
                        "end": float(segment.end),
                        "speaker": str(speaker),
                    }
                )
            rows.sort(key=lambda item: (item["start"], item["end"]))
            return rows, ""
        except Exception as exc:
            logger.warning("Recorder diarization failed: %s", exc)
            if remote_error:
                return [], f"{remote_error}; pyannote diarization failed: {exc}"
            return [], f"pyannote diarization failed: {exc}"

    def _format_diarization_text(
        self,
        *,
        diarization_rows: list[dict[str, Any]],
        transcript_segments: list[WhisperTranscriptSegment],
    ) -> str:
        if not diarization_rows:
            return ""

        assigned_texts = ["" for _ in diarization_rows]
        if transcript_segments:
            for segment in transcript_segments:
                segment_text = (segment.text or "").strip()
                if not segment_text:
                    continue
                best_idx = -1
                best_overlap = -1.0
                seg_start = float(segment.start)
                seg_end = max(seg_start, float(segment.end))
                seg_mid = (seg_start + seg_end) / 2.0
                for idx, row in enumerate(diarization_rows):
                    row_start = float(row["start"])
                    row_end = max(row_start, float(row["end"]))
                    overlap = max(0.0, min(seg_end, row_end) - max(seg_start, row_start))
                    contains_midpoint = row_start <= seg_mid <= row_end
                    if contains_midpoint and overlap == 0.0:
                        overlap = 1e-6
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_idx = idx
                if best_idx >= 0:
                    assigned_texts[best_idx] = (assigned_texts[best_idx] + " " + segment_text).strip()

        lines: list[str] = []
        for idx, row in enumerate(diarization_rows):
            chunk_text = assigned_texts[idx].strip()
            line = f"[{_format_seconds(row['start'])} - {_format_seconds(row['end'])}] {row['speaker']}"
            if chunk_text:
                line += f" {chunk_text}"
            lines.append(line)
        return "\n".join(lines)
