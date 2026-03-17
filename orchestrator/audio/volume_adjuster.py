"""Dynamic volume adjustment based on cut-in frequency and microphone RMS levels."""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CutInTracker:
    """Tracks repeated cut-in events and manages output volume reduction."""
    
    enabled: bool
    window_ms: int
    count_threshold: int
    reduction_ratio: float
    restoration_timeout_ms: int
    
    # State tracking
    cut_in_timestamps: list[float] = field(default_factory=list)
    volume_reduced: bool = False
    volume_reduction_started_ts: Optional[float] = None
    baseline_output_volume: Optional[float] = None
    
    def on_cut_in(self, now: float) -> tuple[bool, Optional[str]]:
        """
        Process a cut-in event.
        
        Returns:
            (should_reduce_volume, log_message)
            should_reduce_volume: True if output volume should be reduced
            log_message: Log message if volume adjustment occurred
        """
        if not self.enabled:
            return False, None
        
        # Clean up old timestamps outside the window
        window_start = now - (self.window_ms / 1000.0)
        self.cut_in_timestamps = [ts for ts in self.cut_in_timestamps if ts >= window_start]
        
        # Add current cut-in event
        self.cut_in_timestamps.append(now)
        
        # Check if threshold is reached
        should_reduce = len(self.cut_in_timestamps) >= self.count_threshold
        
        if should_reduce and not self.volume_reduced:
            self.volume_reduced = True
            self.volume_reduction_started_ts = now
            msg = (
                f"🔊 AUTO-ADJUST: Cut-in repeated {len(self.cut_in_timestamps)} times in {self.window_ms}ms window. "
                f"Reducing output volume by {int((1 - self.reduction_ratio) * 100)}% "
                f"(reduction_ratio={self.reduction_ratio:.2f})"
            )
            logger.info(msg)
            return True, msg
        elif should_reduce:
            # Volume already reduced, just update the restoration timestamp
            self.volume_reduction_started_ts = now
        
        return False, None
    
    def check_restoration(self, now: float) -> tuple[bool, Optional[str]]:
        """
        Check if volume reduction timeout has expired.
        
        Returns:
            (should_restore_volume, log_message)
        """
        if not self.enabled or not self.volume_reduced or not self.volume_reduction_started_ts:
            return False, None
        
        elapsed_ms = int((now - self.volume_reduction_started_ts) * 1000)
        
        if elapsed_ms >= self.restoration_timeout_ms:
            self.volume_reduced = False
            self.volume_reduction_started_ts = None
            self.cut_in_timestamps.clear()
            msg = (
                f"🔊 AUTO-ADJUST: Output volume restoration timeout ({self.restoration_timeout_ms}ms) reached. "
                f"Restoring to baseline volume."
            )
            logger.info(msg)
            return True, msg
        
        return False, None
    
    def get_output_volume_multiplier(self) -> float:
        """Return the volume multiplier to apply (1.0 = no reduction, <1.0 = reduced)."""
        if self.volume_reduced:
            return self.reduction_ratio
        return 1.0
    
    def reset(self):
        """Reset all tracking state."""
        self.cut_in_timestamps.clear()
        self.volume_reduced = False
        self.volume_reduction_started_ts = None
        self.baseline_output_volume = None


@dataclass
class MicVolumeAdjuster:
    """Automatically adjusts microphone gain based on RMS levels during speech."""
    
    enabled: bool
    target_rms: float
    adjustment_ratio: float
    exclude_devices: list[str]
    gain_min: float
    gain_max: float
    
    # State tracking
    current_gain: float = 1.0
    last_adjustment_ts: Optional[float] = None
    adjustment_count: int = 0
    
    def should_process_device(self, device_name: Optional[str]) -> bool:
        """Check if this device should have auto-adjustment applied."""
        if not self.enabled or not device_name:
            return self.enabled
        
        device_lower = device_name.lower()
        for exclude_pattern in self.exclude_devices:
            if exclude_pattern.lower() in device_lower:
                logger.debug(
                    f"🎤 Auto mic adjust: Skipping device '{device_name}' (matches exclude pattern '{exclude_pattern}')"
                )
                return False
        
        return True
    
    def adjust_gain(self, current_rms: float, now: float) -> tuple[float, Optional[str]]:
        """
        Adjust microphone gain based on current RMS level.
        
        Args:
            current_rms: Current RMS level of speech
            now: Current timestamp
            
        Returns:
            (new_gain, log_message)
        """
        if not self.enabled:
            return self.current_gain, None
        
        if current_rms <= 0:
            return self.current_gain, None
        
        # Calculate RMS ratio and determine adjustment
        rms_ratio = current_rms / self.target_rms
        
        # Apply adjustment proportionally, capped by adjustment_ratio
        if rms_ratio > 1.0:
            # RMS is too loud, reduce gain
            max_reduction = 1.0 - self.adjustment_ratio
            adjustment = max(rms_ratio - 1.0, 0.0) * 0.1  # Scale down the adjustment
            new_gain = self.current_gain * (1.0 - min(adjustment, self.adjustment_ratio))
        elif rms_ratio < 1.0:
            # RMS is too quiet, increase gain
            max_increase = self.adjustment_ratio
            adjustment = (1.0 - rms_ratio) * 0.1  # Scale down the adjustment
            new_gain = self.current_gain * (1.0 + min(adjustment, self.adjustment_ratio))
        else:
            # RMS is at target
            return self.current_gain, None
        
        # Clamp to min/max bounds
        new_gain = max(self.gain_min, min(self.gain_max, new_gain))
        
        # Only log if there's a meaningful change
        if abs(new_gain - self.current_gain) > 0.01:
            old_gain = self.current_gain
            self.current_gain = new_gain
            self.last_adjustment_ts = now
            self.adjustment_count += 1
            
            direction = "increased" if new_gain > old_gain else "decreased"
            msg = (
                f"🎤 AUTO-ADJUST: Mic gain {direction} {old_gain:.3f} → {new_gain:.3f} "
                f"(RMS={current_rms:.4f}, target={self.target_rms:.4f}, ratio={rms_ratio:.2f})"
            )
            logger.info(msg)
            return new_gain, msg
        
        self.current_gain = new_gain
        return new_gain, None
    
    def get_current_gain(self) -> float:
        """Return the current microphone gain multiplier."""
        return self.current_gain
    
    def reset_to_baseline(self):
        """Reset gain to 1.0 (no adjustment)."""
        if self.current_gain != 1.0:
            logger.info(f"🎤 AUTO-ADJUST: Resetting mic gain to baseline (1.0 from {self.current_gain:.3f})")
            self.current_gain = 1.0
