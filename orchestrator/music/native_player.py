from __future__ import annotations

import asyncio
import logging
import os
import signal
import tempfile
from pathlib import Path

from .ffmpeg_adapter import FFmpegAdapter
from .format_policy import needs_transcode

logger = logging.getLogger(__name__)


class NativePlayer:
    def __init__(self, library_root: str):
        self.library_root = Path(library_root)
        self.output_route = "local"
        self._proc: asyncio.subprocess.Process | None = None
        self._ffmpeg = FFmpegAdapter()
        self._paused = False
        self._last_source_path = ""
        self._last_seek_s = 0
        self.browser_stream_path: str = ""

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

    async def play(self, rel_path: str, seek_s: int = 0) -> bool:
        src = (self.library_root / rel_path).resolve()
        if not src.exists() or not src.is_file():
            return False

        await self.stop()
        self._last_source_path = rel_path
        self._last_seek_s = max(0, int(seek_s))

        probe = await self._ffmpeg.probe_async(str(src))
        transcode = needs_transcode(probe, self.output_route)

        # Browser route creates a transcoded asset that can be fetched by UI media mount.
        if self.output_route == "browser":
            if transcode:
                out_dir = self.library_root / ".openclaw-transcoded"
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / "current.m4a"
                ok = await self._ffmpeg.transcode_for_browser(str(src), str(out))
                if not ok:
                    return False
                self.browser_stream_path = str(out)
            else:
                self.browser_stream_path = str(src)
            return True

        play_src = str(src)
        if transcode:
            out = Path(tempfile.gettempdir()) / "openclaw-local-stream.wav"
            ok = await self._ffmpeg.transcode_to_pcm(str(src), str(out))
            if not ok:
                return False
            play_src = str(out)

        args = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]
        if self._last_seek_s > 0:
            args += ["-ss", str(self._last_seek_s)]
        args.append(play_src)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._paused = False
            return True
        except Exception as exc:
            logger.error("Failed starting ffplay: %s", exc)
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
        return await self.play(self._last_source_path, seek_s=seconds)

    def is_active(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def is_paused(self) -> bool:
        return self._paused
