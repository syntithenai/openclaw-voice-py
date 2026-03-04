import logging
import queue
import threading
import time
from typing import Callable, Optional, Tuple, Union

import numpy as np
import sounddevice as sd

from orchestrator.tts.tts_mixer import apply_gain

logger = logging.getLogger("orchestrator.audio.duplex")


class DuplexAudioIO:
    def __init__(
        self,
        sample_rate: int,
        frame_samples: int,
        input_device: str = "default",
        output_device: str = "default",
        input_gain: float = 1.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.input_device = input_device
        self.output_device = output_device
        self.input_gain = input_gain

        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stream: Optional[sd.Stream] = None
        self._on_playback_frame: Optional[Callable[[bytes], None]] = None
        self._warned_status = False

        self._out_queue: queue.Queue[Tuple[np.ndarray, Optional[threading.Event], int]] = queue.Queue()
        self._play_lock = threading.Lock()
        self._call_counter = 0
        self._call_pending: dict[int, int] = {}
        self._call_events: dict[int, threading.Event] = {}
        self._call_stop_events: dict[int, Optional[threading.Event]] = {}
        self._cancelled_calls: set[int] = set()
        self._current: Optional[Tuple[np.ndarray, int, Optional[threading.Event], int]] = None

    def set_playback_callback(self, callback: Callable[[bytes], None]) -> None:
        self._on_playback_frame = callback

    def _device_tuple(self) -> Tuple[Optional[Union[int, str]], Optional[Union[int, str]]]:
        # Convert numeric or ALSA device names to concrete indices for sounddevice
        in_dev = None
        if self.input_device != "default":
            if isinstance(self.input_device, str) and self.input_device.isdigit():
                in_dev = int(self.input_device)
            elif isinstance(self.input_device, str) and self.input_device.startswith(("hw:", "plughw:")):
                try:
                    hw = self.input_device.split(":", 1)[1]
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
                        in_dev = int(match)
                        logger.debug("Resolved input ALSA device %s to index %d", self.input_device, in_dev)
                    else:
                        in_dev = self.input_device
                except Exception:
                    logger.warning("Could not resolve ALSA input device format: %s", self.input_device)
                    in_dev = self.input_device
            else:
                in_dev = self.input_device

        if self.output_device == "default":
            out_dev: Optional[Union[int, str]] = None
        elif isinstance(self.output_device, str) and self.output_device.isdigit():
            out_dev = int(self.output_device)
        elif isinstance(self.output_device, str) and self.output_device.startswith(("hw:", "plughw:")):
            try:
                hw = self.output_device.split(":", 1)[1]
                devices = sd.query_devices()
                match = next(
                    (
                        i
                        for i, d in enumerate(devices)
                        if f"(hw:{hw})" in d.get("name", "") and d.get("max_output_channels", 0) > 0
                    ),
                    None,
                )
                out_dev = int(match) if match is not None else self.output_device
            except Exception:
                out_dev = self.output_device
        else:
            out_dev = self.output_device
        return (in_dev, out_dev)

    def _callback(self, indata: np.ndarray, outdata: np.ndarray, frames: int, time_info, status) -> None:
        if status and not self._warned_status:
            logger.warning("Audio duplex status: %s", status)
            self._warned_status = True

        # Input capture with gain
        pcm = np.clip(indata[:, 0] * self.input_gain, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16).tobytes()
        try:
            self._queue.put_nowait(pcm)
        except queue.Full:
            pass

        # Output playback
        outdata.fill(0)
        while True:
            if self._current is None:
                try:
                    data, stop_event, call_id = self._out_queue.get_nowait()
                except queue.Empty:
                    break
                if call_id in self._cancelled_calls:
                    self._mark_chunk_complete(call_id)
                    continue
                if stop_event is not None and stop_event.is_set():
                    logger.debug("Stop event detected when dequeuing chunk (call_id=%d)", call_id)
                    self._cancel_call(call_id)
                    continue
                self._current = (data, 0, stop_event, call_id)

            if self._current is None:
                break

            data, idx, stop_event, call_id = self._current
            if stop_event is not None and stop_event.is_set():
                logger.debug("Stop event detected in current chunk (call_id=%d)", call_id)
                self._cancel_call(call_id)
                self._current = None
                continue

            remaining = data.shape[0] - idx
            if remaining <= 0:
                self._mark_chunk_complete(call_id)
                self._current = None
                continue

            count = min(frames, remaining)
            
            # Check stop_event BEFORE writing audio data for faster response
            if stop_event is not None and stop_event.is_set():
                logger.debug("Stop event detected before writing audio (call_id=%d)", call_id)
                self._cancel_call(call_id)
                self._current = None
                continue
            
            outdata[:count] = data[idx:idx + count]
            if count < frames:
                outdata[count:] = 0

            if self._on_playback_frame:
                out_pcm = (outdata[:count].flatten() * 32767).astype(np.int16).tobytes()
                self._on_playback_frame(out_pcm)

            idx += count
            if idx >= data.shape[0]:
                self._mark_chunk_complete(call_id)
                self._current = None
            else:
                self._current = (data, idx, stop_event, call_id)
            break

    def _mark_chunk_complete(self, call_id: int) -> None:
        with self._play_lock:
            remaining = self._call_pending.get(call_id, 0)
            if remaining <= 1:
                self._call_pending.pop(call_id, None)
                event = self._call_events.pop(call_id, None)
                self._call_stop_events.pop(call_id, None)
                if event:
                    event.set()
            else:
                self._call_pending[call_id] = remaining - 1

    def _cancel_call(self, call_id: int) -> None:
        self._cancelled_calls.add(call_id)
        with self._play_lock:
            self._call_pending.pop(call_id, None)
            event = self._call_events.pop(call_id, None)
            self._call_stop_events.pop(call_id, None)
            if event:
                event.set()

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.Stream(
            samplerate=self.sample_rate,
            channels=(1, 1),
            dtype="float32",
            blocksize=self.frame_samples,
            device=self._device_tuple(),
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Duplex audio stream started")

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def restart(self) -> None:
        self.stop()
        time.sleep(0.05)
        self.start()

    def read_frame(self, timeout: float = 0.0) -> Optional[bytes]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def play_pcm(self, pcm: bytes, gain: float = 1.0, stop_event: Optional[threading.Event] = None) -> None:
        if gain != 1.0:
            pcm = apply_gain(pcm, gain)
        data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32767.0
        data = data.reshape(-1, 1)

        chunks = [data[i:i + self.frame_samples] for i in range(0, data.shape[0], self.frame_samples)]
        done_event = threading.Event()

        with self._play_lock:
            self._call_counter += 1
            call_id = self._call_counter
            self._call_pending[call_id] = len(chunks)
            self._call_events[call_id] = done_event
            self._call_stop_events[call_id] = stop_event

        for chunk in chunks:
            self._out_queue.put((chunk, stop_event, call_id))

        while not done_event.wait(0.05):
            if stop_event is not None and stop_event.is_set():
                self._cancel_call(call_id)
                break
