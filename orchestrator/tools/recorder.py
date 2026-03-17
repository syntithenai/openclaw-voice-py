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


logger = logging.getLogger("orchestrator.tools.recorder")


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


@dataclass
class RecorderStopResult:
    response: str
    audio_path: str
    transcript_path: str


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
                )

            self._recording_active = False
            started_ts = self._recording_started_ts
            self._recording_started_ts = 0.0
            with self._append_lock:
                frames = self._frames
                self._frames = []

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
            )

        wav_bytes = pcm_to_wav_bytes(pcm, self.sample_rate)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        audio_path = self.output_root / f"recording-{stamp}.wav"
        transcript_path = self.output_root / f"recording-{stamp}.txt"

        audio_path.write_bytes(wav_bytes)

        transcript_text = ""
        diarization_rows: list[dict[str, Any]] = []
        diarization_note = ""

        try:
            transcript_text = await asyncio.to_thread(self.whisper_client.transcribe, wav_bytes)
        except Exception as exc:
            transcript_text = ""
            diarization_note = f"Whisper transcription failed: {exc}"
            logger.warning("Recorder whisper transcription failed: %s", exc)

        diarization_rows, diarization_err = await asyncio.to_thread(self._run_pyannote, audio_path)
        if diarization_err:
            if diarization_note:
                diarization_note = f"{diarization_note}; {diarization_err}"
            else:
                diarization_note = diarization_err

        duration_s = max(0.0, time.time() - started_ts)
        transcript_path.write_text(
            self._format_output_text(
                audio_path=audio_path,
                duration_s=duration_s,
                reason=reason,
                transcript_text=(transcript_text or "").strip(),
                diarization_rows=diarization_rows,
                diarization_note=diarization_note,
            ),
            encoding="utf-8",
        )

        response = "Stopped recording"
        logger.info("Recorder stopped: audio=%s transcript=%s", audio_path, transcript_path)
        await self._invoke_hook(self.on_recording_stopped)
        return RecorderStopResult(
            response=response,
            audio_path=str(audio_path),
            transcript_path=str(transcript_path),
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
                logger.warning("Recorder remote pyannote diarization failed: %s", exc)
                remote_error = f"remote pyannote diarization failed: {exc}"
            else:
                remote_error = ""
        else:
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
                pipeline = Pipeline.from_pretrained(str(model_path))
            else:
                pipeline = Pipeline.from_pretrained(self.pyannote_model, use_auth_token=token)
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

    def _format_output_text(
        self,
        *,
        audio_path: Path,
        duration_s: float,
        reason: str,
        transcript_text: str,
        diarization_rows: list[dict[str, Any]],
        diarization_note: str,
    ) -> str:
        lines: list[str] = []
        lines.append(f"audio_file: {audio_path}")
        lines.append(f"duration: {_format_seconds(duration_s)}")
        lines.append(f"stopped_by: {reason}")
        lines.append("")
        lines.append("whisper_transcript:")
        lines.append(transcript_text if transcript_text else "(empty)")
        lines.append("")
        lines.append("pyannote_diarization:")
        if diarization_rows:
            for row in diarization_rows:
                lines.append(
                    f"[{_format_seconds(row['start'])} - {_format_seconds(row['end'])}] {row['speaker']}"
                )
        else:
            lines.append("(none)")

        if diarization_note:
            lines.append("")
            lines.append(f"note: {diarization_note}")

        lines.append("")
        lines.append("combined_output:")
        if transcript_text:
            lines.append(transcript_text)
        else:
            lines.append("(no whisper transcript)")
        if diarization_rows:
            lines.append("")
            lines.append("speaker_timeline:")
            for row in diarization_rows:
                lines.append(
                    f"- [{_format_seconds(row['start'])} - {_format_seconds(row['end'])}] {row['speaker']}"
                )

        return "\n".join(lines)
