import logging
from typing import Any, Optional

from orchestrator.metrics import WakeWordResult
from orchestrator.wakeword.base import WakeWordBase

try:
    from precise_runner import PreciseEngine
except ImportError:  # pragma: no cover
    PreciseEngine = None


logger = logging.getLogger("orchestrator.wakeword.precise")


class MycoftPreciseDetector(WakeWordBase):
    """Mycroft Precise wake word detector.
    
    Precise is an open-source, lightweight wake word detector designed for Raspberry Pi.
    It's more ARM-friendly than openwakeword and doesn't require ONNX Runtime.
    
    Installation:
        pip install mycroft-precise-runner
        # Then download a model, e.g.:
        # wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb
    """
    
    def __init__(self, model_path: str, confidence: float = 0.5) -> None:
        """
        Initialize Mycroft Precise detector.
        
        Args:
            model_path: Path to .pb model file or directory containing models
            confidence: Detection confidence threshold (0.0-1.0)
        """
        self.model_path = model_path
        self.confidence = confidence
        self._engine: Optional[Any] = None
        self._warned = False
        self._chunk_size_bytes = 2048
        self._pending = bytearray()

        if not PreciseEngine:
            logger.warning("precise_runner library not found. Install with: pip install precise-runner")
            return

        try:
            if not model_path:
                logger.error("Precise requires a model_path to be specified")
                return

            import shutil
            import os

            # Find the precise-engine binary
            engine_exe = shutil.which('precise-engine')
            if not engine_exe:
                # Try in the venv - orchestrator/wakeword/precise.py -> orchestrator -> project root
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                venv_bin = os.path.join(project_root, '.venv_orchestrator', 'bin', 'precise-engine')
                if os.path.exists(venv_bin):
                    engine_exe = venv_bin
                else:
                    logger.error("precise-engine binary not found in PATH or venv (tried: %s)", venv_bin)
                    return

            self._engine = PreciseEngine(engine_exe, model_path, chunk_size=self._chunk_size_bytes)
            self._engine.start()
            if not self._engine.proc or self._engine.proc.poll() is not None:
                logger.error("precise-engine failed to start (process exited early)")
                self._engine = None
                return

            logger.info("Mycroft Precise initialized with model: %s (confidence=%.2f, chunk=%d bytes)", model_path, confidence, self._chunk_size_bytes)
        except Exception as exc:  # pragma: no cover
            logger.warning("Mycroft Precise initialization failed: %s", exc)
            logger.warning("Make sure model exists at: %s", model_path)
            self._engine = None
    
    def detect(self, pcm_frame: bytes) -> WakeWordResult:
        """Detect wake word in audio frame."""
        if not self._engine:
            if not self._warned:
                logger.warning("Mycroft Precise engine not initialized; wake word disabled.")
                self._warned = True
            return WakeWordResult(detected=False, confidence=0.0)

        if not pcm_frame:
            return WakeWordResult(detected=False, confidence=0.0)

        try:
            if not self._engine.proc or self._engine.proc.poll() is not None:
                logger.warning("Mycroft Precise process is not running; disabling detector")
                self._engine = None
                return WakeWordResult(detected=False, confidence=0.0)

            self._pending.extend(pcm_frame)
            last_confidence = 0.0
            chunk_count = 0

            while len(self._pending) >= self._chunk_size_bytes:
                chunk = bytes(self._pending[:self._chunk_size_bytes])
                del self._pending[:self._chunk_size_bytes]
                chunk_count += 1
                try:
                    last_confidence = float(self._engine.get_prediction(chunk))
                except Exception as chunk_exc:
                    logger.debug("Chunk %d prediction failed: %s", chunk_count, chunk_exc)
                    continue

            detected = last_confidence >= self.confidence
            if last_confidence > 0.0 or detected:
                logger.debug("Precise: confidence=%.4f (threshold=%.2f) detected=%s", last_confidence, self.confidence, detected)
            return WakeWordResult(detected=detected, confidence=last_confidence)
        except Exception as exc:  # pragma: no cover
            logger.warning("Mycroft Precise inference failed: %s", exc)
            return WakeWordResult(detected=False, confidence=0.0)

    def reset_state(self) -> None:
        """Reset the wake word detector's internal state."""
        self._pending.clear()
