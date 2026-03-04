import logging
from typing import Optional

import numpy as np

from orchestrator.metrics import WakeWordResult
from orchestrator.wakeword.base import WakeWordBase

try:
    import pvporcupine
except ImportError:  # pragma: no cover
    pvporcupine = None


logger = logging.getLogger("orchestrator.wakeword.picovoice")


class PicovoiceDetector(WakeWordBase):
    """Picovoice Porcupine wake word detector.
    
    Porcupine is a lightweight, on-device wake word detection engine from Picovoice.
    It's highly optimized for embedded devices including Raspberry Pi.
    
    Note: Requires a Picovoice AccessKey (free tier available).
    
    Installation:
        pip install pvporcupine
    
    Get AccessKey:
        1. Sign up at https://console.picovoice.co
        2. Create an AccessKey in the console
        3. Set PICOVOICE_ACCESS_KEY environment variable
    """
    
    def __init__(self, model_path: str, confidence: float = 0.5, access_key: str = "") -> None:
        """
        Initialize Picovoice Porcupine detector.
        
        Args:
            model_path: Keyword name or path (e.g., 'alexa', 'americano')
                       See https://github.com/Picovoice/porcupine/blob/master/resources/keyword_files/README.md
            confidence: Detection sensitivity threshold (0.0-1.0). Higher = more sensitive
            access_key: Picovoice AccessKey (or set via PICOVOICE_ACCESS_KEY env var)
        """
        self.model_path = model_path
        self.confidence = confidence
        self.access_key = access_key or ""
        self._porcupine: Optional[pvporcupine.Porcupine] = None
        self._warned = False
        
        if not pvporcupine:
            logger.warning("pvporcupine library not found. Install with: pip install pvporcupine")
            return
        
        try:
            import os
            # Try to get access key from environment or parameter
            access_key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY", "")
            
            if not access_key:
                logger.warning("Picovoice AccessKey not found. Set PICOVOICE_ACCESS_KEY environment variable.")
                return
            
            # Initialize Porcupine with the specified keyword
            self._porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=[model_path],  # Can be built-in keyword name or path to .ppn file
                sensitivities=[confidence],  # Sensitivity for each keyword
            )
            
            logger.info(
                "Picovoice Porcupine initialized (keyword='%s', sensitivity=%.2f, model_frame_length=%d)",
                model_path,
                confidence,
                self._porcupine.frame_length,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Picovoice Porcupine initialization failed: %s", exc)
            self._porcupine = None
    
    def detect(self, pcm_frame: bytes) -> WakeWordResult:
        """Detect wake word in audio frame."""
        if not self._porcupine:
            if not self._warned:
                logger.warning("Picovoice Porcupine not initialized; wake word disabled.")
                logger.warning("Set PICOVOICE_ACCESS_KEY environment variable to enable.")
                self._warned = True
            return WakeWordResult(detected=False, confidence=0.0)
        
        try:
            audio = np.frombuffer(pcm_frame, dtype=np.int16)
            
            # Porcupine expects exactly frame_length samples
            frame_length = self._porcupine.frame_length
            
            if len(audio) < frame_length:
                # Pad with zeros if frame is too short
                audio = np.pad(audio, (0, frame_length - len(audio)))
            elif len(audio) > frame_length:
                # Take only the first frame_length samples
                audio = audio[:frame_length]
            
            # Run inference
            keyword_index = self._porcupine.process(audio.astype(np.int16))
            
            # keyword_index >= 0 means a keyword was detected (index in keywords list)
            detected = keyword_index >= 0
            
            # Estimate confidence (Porcupine doesn't return raw confidence)
            # Use 0.9 for detection, 0.0 for non-detection
            confidence = 0.9 if detected else 0.0
            
            return WakeWordResult(detected=detected, confidence=confidence)
        except Exception as exc:  # pragma: no cover
            logger.warning("Picovoice Porcupine inference failed: %s", exc)
            return WakeWordResult(detected=False, confidence=0.0)
    
    def reset_state(self) -> None:
        """Reset the wake word detector's internal state."""
        # Porcupine doesn't have stateful detection; nothing to reset
        pass
    
    def __del__(self):
        """Clean up Porcupine resources."""
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception as exc:
                logger.debug("Failed to cleanup Porcupine: %s", exc)
