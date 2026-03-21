"""Regression tests for TTS queue gating policy."""

from __future__ import annotations

import time
import unittest

from orchestrator.state import VoiceState
from orchestrator.tts_policy import tts_start_gate_block_reason


class TtsPolicyTests(unittest.TestCase):
    def test_reply_is_blocked_while_listening(self) -> None:
        reason = tts_start_gate_block_reason(
            item_kind="reply",
            now_ts=time.monotonic(),
            cut_in_tts_hold_active=False,
            tts_playing=False,
            state=VoiceState.LISTENING,
            listening_state=VoiceState.LISTENING,
            last_speech_ts=None,
            vad_min_silence_ms=600,
        )

        self.assertEqual(reason, "listening_state")

    def test_notification_bypasses_listening_gate(self) -> None:
        reason = tts_start_gate_block_reason(
            item_kind="notification",
            now_ts=time.monotonic(),
            cut_in_tts_hold_active=False,
            tts_playing=False,
            state=VoiceState.LISTENING,
            listening_state=VoiceState.LISTENING,
            last_speech_ts=time.monotonic(),
            vad_min_silence_ms=600,
        )

        self.assertIsNone(reason)

    def test_notification_still_waits_for_active_tts(self) -> None:
        reason = tts_start_gate_block_reason(
            item_kind="notification",
            now_ts=time.monotonic(),
            cut_in_tts_hold_active=False,
            tts_playing=True,
            state=VoiceState.IDLE,
            listening_state=VoiceState.LISTENING,
            last_speech_ts=None,
            vad_min_silence_ms=600,
        )

        self.assertEqual(reason, "tts_playing")


if __name__ == "__main__":
    unittest.main()