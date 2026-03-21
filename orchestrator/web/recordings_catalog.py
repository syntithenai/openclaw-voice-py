from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
import wave


logger = logging.getLogger("orchestrator.web.recordings")


class RecordingsCatalog:
    def __init__(
        self,
        recordings_root: Path,
        on_change: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> None:
        self.recordings_root = Path(recordings_root).expanduser().resolve()
        self._on_change = on_change
        self._watch_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._recordings: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self.recordings_root.mkdir(parents=True, exist_ok=True)
        await self.refresh()
        try:
            from inotify_simple import INotify, flags
        except Exception:
            logger.info("inotify_simple unavailable; recordings catalog falling back to polling")
            self._poll_task = asyncio.create_task(self._poll_loop())
            return
        self._watch_task = asyncio.create_task(self._inotify_loop(INotify, flags))

    async def stop(self) -> None:
        for task in (self._watch_task, self._poll_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._watch_task = None
        self._poll_task = None

    def list_recordings(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._recordings]

    async def refresh(self) -> list[dict[str, Any]]:
        async with self._lock:
            updated = self._scan_recordings()
            changed = updated != self._recordings
            self._recordings = updated
        if changed:
            await self._emit_change(updated)
        return [dict(item) for item in self._recordings]

    async def delete_recordings(self, recording_ids: list[str]) -> int:
        deleted_files = 0
        for recording_id in recording_ids or []:
            base = self._sanitize_recording_id(recording_id)
            if not base:
                continue
            for suffix in (".wav", ".transcript.txt", ".diarization.txt"):
                target = (self.recordings_root / f"{base}{suffix}").resolve()
                if not self._is_safe_path(target):
                    continue
                if not target.exists() or not target.is_file():
                    continue
                try:
                    target.unlink()
                    deleted_files += 1
                except Exception as exc:
                    logger.warning("Failed to delete recording file %s: %s", target, exc)
        await self.refresh()
        return deleted_files

    def get_recording_detail(self, recording_id: str) -> dict[str, Any] | None:
        base = self._sanitize_recording_id(recording_id)
        if not base:
            return None

        wav_path = (self.recordings_root / f"{base}.wav").resolve()
        transcript_path = (self.recordings_root / f"{base}.transcript.txt").resolve()
        diarization_path = (self.recordings_root / f"{base}.diarization.txt").resolve()

        if not all(
            p.exists() and p.is_file() and self._is_safe_path(p)
            for p in (wav_path, transcript_path)
        ):
            return None

        summary = next((item for item in self._recordings if item.get("id") == base), None)
        transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        diarization_text = diarization_path.read_text(encoding="utf-8", errors="replace") if diarization_path.exists() else ""

        return {
            "id": base,
            "date": (summary or {}).get("date") or "",
            "time": (summary or {}).get("time") or "",
            "created_ts": (summary or {}).get("created_ts") or 0,
            "duration_seconds": float((summary or {}).get("duration_seconds") or self._read_wav_duration(wav_path)),
            "excerpt": (summary or {}).get("excerpt") or self._excerpt_from_text(transcript_text),
            "transcript": transcript_text,
            "diarization": diarization_text,
            "audio_url": f"/recordings/audio/{quote(wav_path.name)}",
        }

    def resolve_audio_path(self, audio_filename: str) -> Path | None:
        candidate_name = Path(str(audio_filename or "")).name
        if not candidate_name.endswith(".wav"):
            return None
        target = (self.recordings_root / candidate_name).resolve()
        if not self._is_safe_path(target):
            return None
        if not target.exists() or not target.is_file():
            return None
        return target

    async def _emit_change(self, recordings: list[dict[str, Any]]) -> None:
        if self._on_change is None:
            return
        try:
            result = self._on_change([dict(item) for item in recordings])
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning("recordings on_change callback failed: %s", exc)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            await self.refresh()

    async def _inotify_loop(self, inotify_cls: Any, flags: Any) -> None:
        watch = inotify_cls()
        mask = (
            flags.CREATE
            | flags.DELETE
            | flags.CLOSE_WRITE
            | flags.MOVED_FROM
            | flags.MOVED_TO
            | flags.ATTRIB
        )
        watch.add_watch(str(self.recordings_root), mask)

        while True:
            events = await asyncio.to_thread(watch.read, 1200)
            if not events:
                continue
            await self.refresh()

    def _scan_recordings(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for wav_path in sorted(self.recordings_root.glob("recording-*.wav")):
            if not wav_path.is_file():
                continue
            base = wav_path.stem
            transcript_path = self.recordings_root / f"{base}.transcript.txt"
            if not transcript_path.exists():
                continue

            created_dt = self._datetime_from_recording_id(base)
            created_ts = created_dt.timestamp() if created_dt else wav_path.stat().st_mtime
            date_label = created_dt.strftime("%Y-%m-%d") if created_dt else datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
            time_label = created_dt.strftime("%H:%M:%S") if created_dt else datetime.fromtimestamp(created_ts).strftime("%H:%M:%S")
            transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")

            items.append(
                {
                    "id": base,
                    "date": date_label,
                    "time": time_label,
                    "created_ts": created_ts,
                    "duration_seconds": self._read_wav_duration(wav_path),
                    "excerpt": self._excerpt_from_text(transcript_text),
                }
            )

        items.sort(key=lambda item: float(item.get("created_ts") or 0.0), reverse=True)
        return items

    def _is_safe_path(self, path: Path) -> bool:
        return path == self.recordings_root or self.recordings_root in path.parents

    def _sanitize_recording_id(self, recording_id: str) -> str:
        candidate = str(recording_id or "").strip()
        if not candidate:
            return ""
        if "/" in candidate or "\\" in candidate:
            return ""
        if not candidate.startswith("recording-"):
            return ""
        return candidate

    def _datetime_from_recording_id(self, recording_id: str) -> datetime | None:
        stamp = recording_id.removeprefix("recording-")
        try:
            return datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        except Exception:
            return None

    def _read_wav_duration(self, wav_path: Path) -> float:
        try:
            with wave.open(str(wav_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate <= 0:
                    return 0.0
                return float(frames) / float(rate)
        except Exception:
            return 0.0

    def _excerpt_from_text(self, value: str, max_chars: int = 140) -> str:
        compact = " ".join(str(value or "").split())
        if not compact:
            return ""
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 1].rstrip() + "…"
