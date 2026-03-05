from typing import Callable, Optional
import threading
import logging
import time

import numpy as np
import sounddevice as sd

from orchestrator.audio.resample import resample_pcm
from orchestrator.tts.tts_mixer import apply_gain


logger = logging.getLogger("orchestrator.audio.playback")


class AudioPlayback:
    def __init__(
        self,
        sample_rate: int,
        device: str = "default",
        lead_in_ms: int = 0,
        keepalive_enabled: bool = False,
        keepalive_interval_ms: int = 250,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._stream: Optional[sd.OutputStream] = None
        self._stream_sample_rate: int = sample_rate
        self._on_playback_frame: Optional[Callable[[bytes], None]] = None
        # Some ALSA hw devices clip the first phonemes when playback starts.
        # Add a tiny lead-in silence to let the DAC/driver settle.
        dev_str = str(device).lower() if device is not None else ""
        auto_lead_in_ms = 120 if dev_str.startswith(("hw:", "plughw:")) else 0
        self._lead_in_ms = lead_in_ms if lead_in_ms > 0 else auto_lead_in_ms
        self._stream_warmed = False
        self._keepalive_enabled = keepalive_enabled
        self._keepalive_interval_s = max(0.01, keepalive_interval_ms / 1000.0)
        self._write_lock = threading.Lock()
        self._last_write_ts = time.monotonic()
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: Optional[threading.Thread] = None

    def set_playback_callback(self, callback: Callable[[bytes], None]) -> None:
        self._on_playback_frame = callback

    def _start_keepalive_if_needed(self) -> None:
        if not self._keepalive_enabled or self._keepalive_thread is not None:
            return

        def _loop() -> None:
            while not self._keepalive_stop.is_set():
                time.sleep(max(0.01, self._keepalive_interval_s / 2.0))
                if self._stream is None:
                    continue
                idle_s = time.monotonic() - self._last_write_ts
                if idle_s < self._keepalive_interval_s:
                    continue
                keepalive_frames = max(1, int(self._stream_sample_rate * 0.02))  # 20ms silence
                silence = np.zeros((keepalive_frames, 1), dtype=np.float32)
                try:
                    with self._write_lock:
                        if self._stream is not None:
                            self._stream.write(silence)
                            self._last_write_ts = time.monotonic()
                except Exception:
                    # Stream may be temporarily unavailable; next loop iteration will retry.
                    continue

        self._keepalive_thread = threading.Thread(
            target=_loop,
            name="audio-playback-keepalive",
            daemon=True,
        )
        self._keepalive_thread.start()

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
                self._start_keepalive_if_needed()
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
                self._start_keepalive_if_needed()

        if self._stream_sample_rate != self.sample_rate:
            pcm = resample_pcm(pcm, self.sample_rate, self._stream_sample_rate)

        if self._lead_in_ms > 0:
            # First playback gets extra warm-up silence to avoid clipped sentence starts.
            lead_in_ms = self._lead_in_ms + (280 if not self._stream_warmed else 0)
            lead_in_samples = int(self._stream_sample_rate * (lead_in_ms / 1000.0))
            if lead_in_samples > 0:
                lead_in_pcm = np.zeros(lead_in_samples, dtype=np.int16).tobytes()
                pcm = lead_in_pcm + pcm
            self._stream_warmed = True

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
            with self._write_lock:
                self._stream.write(data[idx:end])
                self._last_write_ts = time.monotonic()
            idx = end
