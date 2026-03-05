import queue
from typing import Optional
import logging
import time

import numpy as np
import sounddevice as sd

logger = logging.getLogger("orchestrator.audio.capture")


class AudioCapture:
    def __init__(self, sample_rate: int, frame_samples: int, device: str = "default", input_gain: float = 1.0) -> None:
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.device = device
        self.input_gain = input_gain
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stream: Optional[sd.InputStream] = None
        self._warned_status = False

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        if status:
            if not self._warned_status:
                logger.warning("Audio capture status: %s", status)
                self._warned_status = True
        # Apply software gain and clip to prevent overflow
        pcm = np.clip(indata[:, 0] * self.input_gain, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16).tobytes()
        try:
            self._queue.put_nowait(pcm)
        except queue.Full:
            pass

    def start(self) -> None:
        # Handle device name conversion for sounddevice
        device_param = None
        if self.device != "default":
            # Try numeric index first (e.g. "8" from .env)
            device_param = self.device
            if isinstance(self.device, str) and self.device.isdigit():
                device_param = int(self.device)
            # Try to parse ALSA device names like "hw:2,0" or "card:device"
            elif isinstance(self.device, str) and self.device.startswith(("hw:", "plughw:")):
                try:
                    hw = self.device.split(":", 1)[1]
                    devices = sd.query_devices()
                    match = next(
                        (
                            i
                            for i, d in enumerate(devices)
                            if f"(hw:{hw})" in d.get("name", "") and d.get("max_input_channels", 0) > 0
                        ),
                        None,
                    )
                    if match is not None:
                        device_param = int(match)
                        logger.debug("Resolved ALSA input device %s to numeric index %d", self.device, device_param)
                    else:
                        device_param = self.device
                except Exception:
                    logger.warning("Could not resolve ALSA device format: %s", self.device)
                    device_param = self.device

        # Try to open stream with channels: mono (1), stereo (2), then the device's default
        channels_to_try = [1, 2]  # Try mono first, then stereo
        last_error = None
        
        for num_channels in channels_to_try:
            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=num_channels,
                    dtype="float32",
                    blocksize=self.frame_samples,
                    device=device_param,
                    callback=self._callback,
                )
                self._stream.start()
                if num_channels != 1:
                    logger.info("Device does not support mono input; using %d channels", num_channels)
                return
            except Exception as e:
                last_error = e
                logger.debug("Failed to open stream with %d channels: %s", num_channels, str(e))
        
        # If all channel counts failed, raise the last error
        if last_error:
            raise last_error

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def restart(self) -> None:
        try:
            self.stop()
            time.sleep(0.05)
            self.start()
            logger.info("Audio capture restarted")
        except Exception as exc:  # pragma: no cover
            logger.warning("Audio capture restart failed: %s", exc)

    def read_frame(self, timeout: float = 0.0) -> Optional[bytes]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
