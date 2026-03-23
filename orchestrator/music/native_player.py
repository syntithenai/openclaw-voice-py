from __future__ import annotations

import asyncio
import logging
import os
import signal
import shutil
import tempfile
from pathlib import Path

from .ffmpeg_adapter import FFmpegAdapter
from .format_policy import needs_transcode

logger = logging.getLogger(__name__)


class NativePlayer:
    def __init__(self, library_root: str):
        self.library_root = Path(library_root)
        self.runtime_media_root = Path(tempfile.gettempdir()) / "openclaw-runtime-media"
        self.runtime_media_root.mkdir(parents=True, exist_ok=True)
        self.output_route = "local"
        self._proc: asyncio.subprocess.Process | None = None
        self._ffmpeg = FFmpegAdapter()
        self._paused = False
        self._last_source_path = ""
        self._last_seek_s = 0
        self.browser_stream_path: str = ""
        self.last_error: str = ""

    def set_output_route(self, route: str) -> None:
        self.output_route = "browser" if str(route).lower() == "browser" else "local"

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=1.5)
            except Exception:
                self._proc.kill()
        self._proc = None
        self._paused = False
        self.browser_stream_path = ""
        self._cleanup_runtime_browser_files()

    def _cleanup_runtime_browser_files(self) -> None:
        try:
            if self.runtime_media_root.exists():
                for name in ("current.m4a", "current.mp3", "current.wav"):
                    p = self.runtime_media_root / name
                    if p.exists() and p.is_file():
                        p.unlink(missing_ok=True)
        except Exception:
            pass

    def _set_error(self, message: str) -> None:
        self.last_error = str(message or "Playback failed").strip()

    async def play(self, rel_path: str, seek_s: int = 0) -> bool:
        src = (self.library_root / rel_path).resolve()
        if not src.exists() or not src.is_file():
            self._set_error(f"file not found: {rel_path}")
            return False

        await self.stop()
        self._last_source_path = rel_path
        self._last_seek_s = max(0, int(seek_s))

        probe = await self._ffmpeg.probe_async(str(src))
        transcode = needs_transcode(probe, self.output_route)

        # Browser route creates a transcoded asset that can be fetched by UI media mount.
        if self.output_route == "browser":
            self._cleanup_runtime_browser_files()
            m4a_out = self.runtime_media_root / "current.m4a"
            mp3_out = self.runtime_media_root / "current.mp3"
            wav_out = self.runtime_media_root / "current.wav"

            # Always transcode for browser route so unsupported source formats
            # still become web-playable and never touch the music library tree.
            ok = await self._ffmpeg.transcode_for_browser(str(src), str(m4a_out))
            if ok:
                self.browser_stream_path = str(m4a_out)
                self.last_error = ""
                return True

            first_err = str(self._ffmpeg.last_error or "")
            ok = await self._ffmpeg.transcode_for_browser_mp3(str(src), str(mp3_out))
            if ok:
                self.browser_stream_path = str(mp3_out)
                self.last_error = ""
                return True

            second_err = str(self._ffmpeg.last_error or "")
            ok = await self._ffmpeg.transcode_for_browser_wav(str(src), str(wav_out))
            if ok:
                self.browser_stream_path = str(wav_out)
                self.last_error = ""
                return True

            final_err = str(self._ffmpeg.last_error or "")
            err_text = final_err or second_err or first_err or "browser transcode failed"
            self._set_error(f"browser transcode failed: {rel_path} ({err_text})")
            return False

        play_src = str(src)
        if transcode:
            out = Path(tempfile.gettempdir()) / "openclaw-local-stream.wav"
            ok = await self._ffmpeg.transcode_to_pcm(str(src), str(out))
            if not ok:
                self._set_error(f"local transcode failed: {rel_path}")
                return False
            play_src = str(out)

        args = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]
        if self._last_seek_s > 0:
            args += ["-ss", str(self._last_seek_s)]
        args.append(play_src)

        # Prefer Pulse/PipeWire so desktop system volume controls affect music playback.
        ffplay_env = os.environ.copy()
        pulse_capable = shutil.which("pactl") is not None
        if pulse_capable:
            ffplay_env.setdefault("SDL_AUDIODRIVER", "pulseaudio")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=ffplay_env,
            )
            self._paused = False
            self.last_error = ""
            return True
        except Exception as exc:
            if pulse_capable:
                logger.warning(
                    "Failed starting ffplay with SDL_AUDIODRIVER=%s; retrying default backend: %s",
                    ffplay_env.get("SDL_AUDIODRIVER", "<unset>"),
                    exc,
                )
                try:
                    self._proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    self._paused = False
                    self.last_error = ""
                    return True
                except Exception as fallback_exc:
                    logger.error("Failed starting ffplay fallback backend: %s", fallback_exc)
                    self._set_error(f"failed starting local player: {fallback_exc}")
                    self._proc = None
                    return False

            logger.error("Failed starting ffplay: %s", exc)
            self._set_error(f"failed starting local player: {exc}")
            self._proc = None
            return False

    async def pause(self) -> bool:
        if not self._proc or self._proc.returncode is not None:
            return False
        try:
            if self._paused:
                os.kill(self._proc.pid, signal.SIGCONT)
                self._paused = False
            else:
                os.kill(self._proc.pid, signal.SIGSTOP)
                self._paused = True
            return True
        except Exception:
            return False

    async def seek(self, seconds: int) -> bool:
        if not self._last_source_path:
            return False
        if self.output_route == "browser":
            # Browser route playback is driven by the web audio element.
            # Restarting/transcoding on seek rewrites the runtime file and can
            # desync UI progress from playback. Keep seek as a logical state move.
            self._last_seek_s = max(0, int(seconds))
            return True
        return await self.play(self._last_source_path, seek_s=seconds)

    def is_active(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def is_paused(self) -> bool:
        return self._paused
