from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .format_policy import InputFormat


class FFmpegAdapter:
    def probe(self, source_path: str) -> InputFormat:
        try:
            proc = asyncio.run(
                asyncio.create_subprocess_exec(
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    source_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            )
        except RuntimeError:
            return InputFormat(container="", codec="")
        except Exception:
            return InputFormat(container="", codec="")

        # Fallback to sync parse when called outside a loop is not available.
        return InputFormat(container="", codec="")

    async def probe_async(self, source_path: str) -> InputFormat:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                source_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _err = await proc.communicate()
            if proc.returncode != 0:
                return InputFormat(container="", codec="")
            data = json.loads(out.decode("utf-8", errors="ignore") or "{}")
            streams = data.get("streams") or []
            fmt = data.get("format") or {}
            audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
            return InputFormat(container=str(fmt.get("format_name") or ""), codec=str(audio.get("codec_name") or ""))
        except Exception:
            return InputFormat(container="", codec="")

    async def transcode_to_pcm(self, source_path: str, out_path: str) -> bool:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source_path,
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "wav",
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
        return proc.returncode == 0

    async def transcode_for_browser(self, source_path: str, out_path: str) -> bool:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            source_path,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, _err = await proc.communicate()
        return proc.returncode == 0
