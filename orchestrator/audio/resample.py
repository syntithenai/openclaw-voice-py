import numpy as np


def resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample PCM audio efficiently.
    
    Uses simple but fast algorithm suitable for Raspberry Pi 3.
    For upsampling (e.g., 16kHz → 48kHz), uses nearest-neighbor with smoothing.
    """
    if src_rate == dst_rate:
        return pcm
    if src_rate <= 0 or dst_rate <= 0:
        return pcm

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return pcm

    # Calculate new size
    ratio = dst_rate / src_rate
    dst_size = int(samples.size * ratio)
    if dst_size <= 0:
        return pcm

    # Use numpy's linear interpolation - fast and decent quality
    src_indices = np.arange(samples.size)
    dst_indices = np.linspace(0, samples.size - 1, dst_size)
    resampled = np.interp(dst_indices, src_indices, samples)
    
    # Light smoothing for upsampling to reduce aliasing artifacts
    if ratio > 1.5:  # Only for significant upsampling
        # Simple 3-point moving average
        kernel = np.array([0.25, 0.5, 0.25])
        resampled = np.convolve(resampled, kernel, mode='same')
    
    resampled = np.clip(resampled, -32768, 32767)
    return resampled.astype(np.int16).tobytes()
