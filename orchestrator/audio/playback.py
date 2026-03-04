from typing import Callable, Optional
import threading
import logging

import numpy as np
import sounddevice as sd

from orchestrator.audio.resample import resample_pcm
from orchestrator.tts.tts_mixer import apply_gain


logger = logging.getLogger("orchestrator.audio.playback")


class AudioPlayback:
    def __init__(self, sample_rate: int, device: str = "default") -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._stream: Optional[sd.OutputStream] = None
        self._stream_sample_rate: int = sample_rate
        self._on_playback_frame: Optional[Callable[[bytes], None]] = None

    def set_playback_callback(self, callback: Callable[[bytes], None]) -> None:
        self._on_playback_frame = callback

    def play_pcm(self, pcm: bytes, gain: float = 1.0, stop_event: Optional[threading.Event] = None) -> None:
        if gain != 1.0:
            pcm = apply_gain(pcm, gain)
        if self._stream is None:
            device_param = None
            if self.device != "default":
                # Accept numeric index passed as string from env (e.g. "1")
                if isinstance(self.device, str) and self.device.isdigit():
                    device_param = int(self.device)
                # Accept ALSA card syntax and map to PortAudio device index heuristically
                elif isinstance(self.device, str) and self.device.startswith(("hw:", "plughw:")):
                    try:
                        hw = self.device.split(":", 1)[1]
                        card = hw.split(",", 1)[0]
                        devices = sd.query_devices()
                        match = next(
                            (
                                i
                                for i, d in enumerate(devices)
                                if (
                                    f"(hw:{hw})" in d.get("name", "")
                                    or f"(hw:{card}," in d.get("name", "")
                                )
                                and d.get("max_output_channels", 0) > 0
                            ),
                            None,
                        )
                        device_param = match if match is not None else self.device
                    except Exception:
                        device_param = self.device
                else:
                    device_param = self.device
            try:
                self._stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    device=device_param,
                )
                self._stream_sample_rate = self.sample_rate
                self._stream.start()
            except Exception as exc:
                # Some USB speakers reject 16kHz; retry with the device default rate.
                if "Invalid sample rate" not in str(exc):
                    raise
                info = sd.query_devices(device_param, "output")
                fallback_rate = int(info.get("default_samplerate", 48000))
                logger.warning(
                    "Playback sample rate %s Hz not supported on %s; falling back to %s Hz",
                    self.sample_rate,
                    self.device,
                    fallback_rate,
                )
                self._stream = sd.OutputStream(
                    samplerate=fallback_rate,
                    channels=1,
                    dtype="float32",
                    device=device_param,
                )
                self._stream_sample_rate = fallback_rate
                self._stream.start()

        if self._stream_sample_rate != self.sample_rate:
            pcm = resample_pcm(pcm, self.sample_rate, self._stream_sample_rate)

        data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
        data = data.reshape(-1, 1)
        if self._on_playback_frame:
            self._on_playback_frame(pcm)
        # Larger chunk size for smoother playback, especially at 48kHz
        chunk_size = 4096
        total = data.shape[0]
        idx = 0
        while idx < total:
            if stop_event is not None and stop_event.is_set():
                break
            end = min(idx + chunk_size, total)
            self._stream.write(data[idx:end])
            idx = end
