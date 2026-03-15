import webrtcvad

from orchestrator.metrics import VADResult
from orchestrator.vad.base import VADBase


class WebRTCVAD(VADBase):
    def __init__(self, sample_rate: int, frame_ms: int, mode: int = 2) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self._vad = webrtcvad.Vad(mode)
        self._bytes_per_sample = 2

    def _valid_frame_sizes(self) -> list[int]:
        # WebRTC VAD supports only 10/20/30ms frames at 8k/16k/32k/48k.
        return [int(self.sample_rate * ms / 1000) * self._bytes_per_sample for ms in (30, 20, 10)]

    def _iter_usable_frames(self, pcm_frame: bytes):
        if not pcm_frame:
            return
        frame_len = len(pcm_frame)
        if frame_len <= 0:
            return

        valid_sizes = self._valid_frame_sizes()
        # Prefer configured frame duration first when valid.
        preferred = int(self.sample_rate * self.frame_ms / 1000) * self._bytes_per_sample
        if preferred in valid_sizes:
            valid_sizes = [preferred] + [v for v in valid_sizes if v != preferred]

        for size in valid_sizes:
            if frame_len < size:
                continue
            # Chunk large buffers into valid-size slices; ignore tail remainder.
            for offset in range(0, frame_len - size + 1, size):
                yield pcm_frame[offset: offset + size]
            return

    def is_speech(self, pcm_frame: bytes) -> VADResult:
        try:
            for frame in self._iter_usable_frames(pcm_frame):
                if self._vad.is_speech(frame, self.sample_rate):
                    return VADResult(speech_detected=True, confidence=1.0)
            return VADResult(speech_detected=False, confidence=0.0)
        except webrtcvad.Error:
            # Invalid/unsupported frame; fail safe instead of crashing audio loop.
            return VADResult(speech_detected=False, confidence=0.0)
