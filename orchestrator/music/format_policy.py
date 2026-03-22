from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputFormat:
    container: str
    codec: str


BROWSER_CODEC_ALLOW = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le"}
LOCAL_CODEC_ALLOW = {"aac", "mp3", "flac", "opus", "vorbis", "pcm_s16le", "alac", "wmav2"}


def needs_transcode(input_fmt: InputFormat, route: str) -> bool:
    codec = (input_fmt.codec or "").lower()
    route_norm = (route or "local").lower()
    if route_norm == "browser":
        return codec not in BROWSER_CODEC_ALLOW
    return codec not in LOCAL_CODEC_ALLOW
