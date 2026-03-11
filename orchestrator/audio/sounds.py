"""
Audio feedback sounds generation for voice orchestrator.
"""

import io
import math
import wave

import numpy as np


def generate_click_sound(
    sample_rate: int = 16000,
    duration_ms: int = 50,
    frequency: int = 800,
) -> bytes:
    """
    Generate a short click sound for audio feedback.

    Args:
        sample_rate: Sample rate (default 16000)
        duration_ms: Duration in milliseconds (default 50ms)
        frequency: Tone frequency in Hz (used to add a crisp edge)

    Returns:
        WAV audio bytes ready for playback
    """
    samples = int((sample_rate * duration_ms) / 1000)
    if samples <= 0:
        return _encode_wav(b"", sample_rate, 1, 16)

    rng = np.random.default_rng(0)
    noise = rng.standard_normal(samples).astype(np.float32)
    t = np.arange(samples, dtype=np.float32) / float(sample_rate)
    tone = np.sin(2 * math.pi * frequency * t)

    decay = np.exp(-np.linspace(0.0, 6.0, samples, dtype=np.float32))
    signal = (0.65 * noise + 0.35 * tone) * decay
    signal = np.clip(signal * 0.45, -1.0, 1.0)

    pcm = (signal * 32767.0).astype(np.int16)
    return _encode_wav(pcm.tobytes(), sample_rate, 1, 16)


def generate_swoosh_sound(
    sample_rate: int = 16000,
    duration_ms: int = 300,
    start_frequency: int = 1200,
    end_frequency: int = 300,
) -> bytes:
    """
    Generate a swoosh/sigh sound (descending frequency sweep) for timeout feedback.
    
    Args:
        sample_rate: Sample rate (default 16000)
        duration_ms: Duration in milliseconds (default 300ms)
        start_frequency: Starting frequency in Hz (default 1200Hz)
        end_frequency: Ending frequency in Hz (default 300Hz)
    
    Returns:
        WAV audio bytes ready for playback
    """
    samples = int((sample_rate * duration_ms) / 1000)
    if samples <= 0:
        return _encode_wav(b"", sample_rate, 1, 16)

    rng = np.random.default_rng(1)
    noise = rng.standard_normal(samples).astype(np.float32)
    t = np.arange(samples, dtype=np.float32) / float(sample_rate)

    # Shift to a lower, softer sweep to reduce high-pitch content
    low_start = min(start_frequency, 800)
    low_end = min(end_frequency, 180)
    freq = low_start * np.power(low_end / low_start, np.linspace(0.0, 1.0, samples, dtype=np.float32))
    tone = np.sin(2 * math.pi * freq * t)

    # Envelope: slow fade in, long sustain, soft fade out
    attack = int(samples * 0.15)
    release = int(samples * 0.25)
    sustain = max(0, samples - attack - release)
    env = np.concatenate([
        np.linspace(0.0, 1.0, max(1, attack), dtype=np.float32),
        np.ones(max(1, sustain), dtype=np.float32),
        np.linspace(1.0, 0.0, max(1, release), dtype=np.float32),
    ])[:samples]

    # More white noise, less tone for a "whoossshhhh" character
    signal = (0.9 * noise + 0.1 * tone) * env
    signal = np.clip(signal * 0.02, -1.0, 1.0)  # Extremely quiet to avoid wakeword triggers

    pcm = (signal * 32767.0).astype(np.int16)
    return _encode_wav(pcm.tobytes(), sample_rate, 1, 16)


def generate_cluck_sound(
    sample_rate: int = 16000,
    duration_ms: int = 90,
    base_frequency: int = 420,
) -> bytes:
    """
    Generate a short, organic low "cluck" cue with a slight pitch bend.
    """
    samples = int((sample_rate * duration_ms) / 1000)
    if samples <= 0:
        return _encode_wav(b"", sample_rate, 1, 16)

    t = np.arange(samples, dtype=np.float32) / float(sample_rate)
    bend = np.linspace(1.0, 0.75, samples, dtype=np.float32)
    freq = base_frequency * bend
    phase = 2 * math.pi * np.cumsum(freq) / float(sample_rate)
    body = np.sin(phase)

    rng = np.random.default_rng(2)
    breath = rng.standard_normal(samples).astype(np.float32)

    attack = int(samples * 0.18)
    release = int(samples * 0.62)
    sustain = max(0, samples - attack - release)
    env = np.concatenate([
        np.linspace(0.0, 1.0, max(1, attack), dtype=np.float32),
        np.ones(max(1, sustain), dtype=np.float32),
        np.linspace(1.0, 0.0, max(1, release), dtype=np.float32),
    ])[:samples]

    signal = (0.78 * body + 0.22 * breath) * env
    signal = np.clip(signal * 0.42, -1.0, 1.0)
    pcm = (signal * 32767.0).astype(np.int16)
    return _encode_wav(pcm.tobytes(), sample_rate, 1, 16)


def generate_sigh_sound(
    sample_rate: int = 16000,
    duration_ms: int = 520,
    start_frequency: int = 540,
    end_frequency: int = 130,
) -> bytes:
    """
    Generate a gentle, sigh-like descending cue with slow pitch/volume decay.
    """
    samples = int((sample_rate * duration_ms) / 1000)
    if samples <= 0:
        return _encode_wav(b"", sample_rate, 1, 16)

    t = np.arange(samples, dtype=np.float32) / float(sample_rate)
    progress = np.linspace(0.0, 1.0, samples, dtype=np.float32)
    freq = start_frequency * np.power(end_frequency / start_frequency, progress)
    phase = 2 * math.pi * np.cumsum(freq) / float(sample_rate)
    tone = np.sin(phase)

    rng = np.random.default_rng(3)
    air = rng.standard_normal(samples).astype(np.float32)

    attack = int(samples * 0.08)
    hold = int(samples * 0.20)
    release = max(1, samples - attack - hold)
    env = np.concatenate([
        np.linspace(0.0, 1.0, max(1, attack), dtype=np.float32),
        np.ones(max(1, hold), dtype=np.float32),
        np.linspace(1.0, 0.0, release, dtype=np.float32),
    ])[:samples]

    signal = (0.62 * tone + 0.38 * air) * env
    signal = np.clip(signal * 0.12, -1.0, 1.0)
    pcm = (signal * 32767.0).astype(np.int16)
    return _encode_wav(pcm.tobytes(), sample_rate, 1, 16)


def generate_knock_sound(
    sample_rate: int = 16000,
    duration_ms: int = 70,
    base_frequency: int = 240,
) -> bytes:
    """Generate a short woody "knock" cue with a warm resonant body."""
    samples = int((sample_rate * duration_ms) / 1000)
    if samples <= 0:
        return _encode_wav(b"", sample_rate, 1, 16)

    t = np.arange(samples, dtype=np.float32) / float(sample_rate)
    harmonic = (
        0.70 * np.sin(2 * math.pi * base_frequency * t)
        + 0.22 * np.sin(2 * math.pi * (base_frequency * 2.2) * t)
        + 0.08 * np.sin(2 * math.pi * (base_frequency * 3.1) * t)
    )
    envelope = np.exp(-np.linspace(0.0, 9.0, samples, dtype=np.float32))

    rng = np.random.default_rng(4)
    transient = rng.standard_normal(samples).astype(np.float32)
    transient *= np.exp(-np.linspace(0.0, 20.0, samples, dtype=np.float32))

    signal = (0.85 * harmonic + 0.15 * transient) * envelope
    signal = np.clip(signal * 0.42, -1.0, 1.0)
    pcm = (signal * 32767.0).astype(np.int16)
    return _encode_wav(pcm.tobytes(), sample_rate, 1, 16)


def generate_exhale_sound(
    sample_rate: int = 16000,
    duration_ms: int = 620,
    brightness: float = 0.26,
) -> bytes:
    """Generate a breathy exhale cue (soft band-limited noise with gentle fade)."""
    samples = int((sample_rate * duration_ms) / 1000)
    if samples <= 0:
        return _encode_wav(b"", sample_rate, 1, 16)

    rng = np.random.default_rng(5)
    white = rng.standard_normal(samples).astype(np.float32)
    # One-pole low-pass for air-like texture.
    alpha = np.clip(brightness, 0.05, 0.95)
    filt = np.zeros_like(white)
    prev = 0.0
    for index, value in enumerate(white):
        prev = alpha * value + (1.0 - alpha) * prev
        filt[index] = prev

    attack = int(samples * 0.10)
    hold = int(samples * 0.20)
    release = max(1, samples - attack - hold)
    envelope = np.concatenate([
        np.linspace(0.0, 1.0, max(1, attack), dtype=np.float32),
        np.ones(max(1, hold), dtype=np.float32),
        np.linspace(1.0, 0.0, release, dtype=np.float32),
    ])[:samples]

    signal = filt * envelope
    signal = np.clip(signal * 0.085, -1.0, 1.0)
    pcm = (signal * 32767.0).astype(np.int16)
    return _encode_wav(pcm.tobytes(), sample_rate, 1, 16)


def _encode_wav(
    pcm_data: bytes,
    sample_rate: int,
    channels: int,
    sample_width: int,
) -> bytes:
    """Encode PCM data as WAV format."""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width // 8)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return buffer.getvalue()
