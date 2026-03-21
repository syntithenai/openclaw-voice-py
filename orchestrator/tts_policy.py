"""TTS queue start-gating policy helpers."""

from __future__ import annotations

from typing import Any


def tts_start_gate_block_reason(
    *,
    item_kind: str,
    now_ts: float,
    cut_in_tts_hold_active: bool,
    tts_playing: bool,
    item_request_id: int = 0,
    tts_last_played_request_id: int = 0,
    state: Any = None,
    listening_state: Any = None,
    last_speech_ts: float | None = None,
    vad_min_silence_ms: int = 0,
) -> str | None:
    """Return the reason a queued TTS item must wait before starting.

    Notification items should still respect active TTS/cut-in holds so playback
    never overlaps, but they must not be blocked by the conversational reply
    gate that waits for the system to leave LISTENING or for recent speech to
    settle.
    """
    if cut_in_tts_hold_active:
        return "cut_in_hold"
    if tts_playing:
        return "tts_playing"

    if item_kind == "notification":
        return None

    # Continuation sentences for the same request should not be delayed by the
    # initial reply gate once playback has already started for that request.
    is_continuation = item_request_id > 0 and item_request_id == tts_last_played_request_id
    if is_continuation:
        return None

    if listening_state is not None and state == listening_state:
        return "listening_state"

    if last_speech_ts is not None:
        silence_ms = int((now_ts - last_speech_ts) * 1000)
        required_silence = max(350, vad_min_silence_ms // 2)
        if silence_ms < required_silence:
            return f"speech_recent:{silence_ms}ms"

    return None