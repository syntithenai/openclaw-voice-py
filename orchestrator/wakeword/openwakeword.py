import logging
from typing import Optional

import numpy as np

from orchestrator.metrics import WakeWordResult
from orchestrator.wakeword.base import WakeWordBase

try:
    from openwakeword.model import Model
except ImportError:  # pragma: no cover
    Model = None


logger = logging.getLogger("orchestrator.wakeword.openwakeword")


class OpenWakeWordDetector(WakeWordBase):
    def __init__(self, model_path: str, confidence: float = 0.5) -> None:
        self.model_path = model_path
        self.confidence = confidence
        self._model: Optional[Model] = None
        self._warned = False

        if not Model:
            logger.warning("openwakeword library not found. Install with: pip install openwakeword")
            return

        try:
            # If a specific model path is provided, use it; otherwise load defaults
            if model_path:
                # Check if it's a file path or just a model name
                import os
                if os.path.exists(model_path):
                    # It's a file path to a .tflite model
                    self._model = Model(wakeword_models=[model_path])
                    logger.info("OpenWakeWord loaded model from: %s", model_path)
                else:
                    # Treat it as a model name - load only that specific model
                    self._model = Model(wakeword_models=[model_path])
                    logger.info("OpenWakeWord loaded model: %s", model_path)
            else:
                # Load default pre-trained models (alexa, hey_mycroft, hey_jarvis, timer, weather)
                self._model = Model()
                logger.info("OpenWakeWord loaded all default models: %s", list(self._model.models.keys()))
        except Exception as exc:  # pragma: no cover
            logger.warning("OpenWakeWord initialization failed: %s", exc)
            logger.warning("Make sure openwakeword is installed: pip install openwakeword")
            self._model = None

    def detect(self, pcm_frame: bytes) -> WakeWordResult:
        if not self._model:
            if not self._warned:
                logger.warning("OpenWakeWord model not loaded; wake word disabled.")
                self._warned = True
            return WakeWordResult(detected=False, confidence=0.0)

        audio = np.frombuffer(pcm_frame, dtype=np.int16)
        try:
            scores = self._model.predict(audio)
        except Exception as exc:  # pragma: no cover
            logger.warning("OpenWakeWord inference failed: %s", exc)
            return WakeWordResult(detected=False, confidence=0.0)

        if not scores:
            return WakeWordResult(detected=False, confidence=0.0)

        # If model_path was specified as a name (not file), filter scores to that model
        import os
        if self.model_path and not os.path.exists(self.model_path):
            # Filter to just the specified model name
            if isinstance(scores, dict) and self.model_path in scores:
                max_score = scores[self.model_path]
            else:
                # Model name not found in scores
                max_score = 0.0
        else:
            # Use max score across all models
            max_score = max(scores.values()) if isinstance(scores, dict) else float(scores)
        
        detected = max_score >= self.confidence
        return WakeWordResult(detected=detected, confidence=float(max_score))

    def reset_state(self) -> None:
        """Reset the wake word detector's internal state."""
        if self._model:
            try:
                # Reset the model's internal state by calling reset() if available
                if hasattr(self._model, 'reset'):
                    self._model.reset()
                # Some models use prediction_buffer that needs clearing
                if hasattr(self._model, 'prediction_buffer'):
                    for model_name in self._model.prediction_buffer:
                        self._model.prediction_buffer[model_name] = []
            except Exception as exc:
                logger.debug("Failed to reset wake word model state: %s", exc)
