import warnings

warnings.filterwarnings(
    "ignore",
    message="invalid escape sequence.*",
    category=SyntaxWarning,
)

import os
import sys

# Suppress FunASR/tqdm progress bars BEFORE any imports
os.environ['TQDM_DISABLE'] = '1'
os.environ['TQDM_MININTERVAL'] = '9999999'
os.environ['FUNASR_CACHE_DIR'] = os.environ.get('MODELSCOPE_CACHE', '')

import asyncio
import io
import json
import logging
import math
import platform
import re
import threading
import time
import unicodedata
import wave
from contextlib import redirect_stderr
from pathlib import Path
from urllib.request import urlretrieve

from orchestrator.config import VoiceConfig
from orchestrator.state import VoiceState, WakeState
from orchestrator.audio.capture import AudioCapture
from orchestrator.audio.duplex import DuplexAudioIO
from orchestrator.audio.buffer import RingBuffer
from orchestrator.vad.silero import SileroVAD
from orchestrator.vad.webrtc_vad import WebRTCVAD
from orchestrator.wakeword.openwakeword import OpenWakeWordDetector
from orchestrator.wakeword.precise import MycoftPreciseDetector
from orchestrator.wakeword.picovoice import PicovoiceDetector
from orchestrator.stt.whisper_client import WhisperClient
from orchestrator.emotion.sensevoice import SenseVoice
from orchestrator.gateway import build_gateway
from orchestrator.tts.piper_client import PiperClient
from orchestrator.audio.playback import AudioPlayback
from orchestrator.audio.webrtc_aec import WebRTCAEC
from orchestrator.audio.resample import resample_pcm
from orchestrator.audio.sounds import generate_click_sound, generate_swoosh_sound
from orchestrator.metrics import AECStatus
import numpy as np


# Custom logging formatter with selective color highlighting for transcriptions
class ColoredFormatter(logging.Formatter):
    """Formatter that highlights transcribed speech and TTS responses in green."""
    
    GREEN = '\033[92m'
    RESET = '\033[0m'
    
    def format(self, record):
        msg = super().format(record)
        
        # Highlight transcribed speech: ' text' in STT Complete lines
        if 'STT: Complete in' in msg:
            # Extract text between quotes after "Complete in X ms: "
            import re
            match = re.search(r"Complete in \d+ms: ('.*?')", msg)
            if match:
                quoted_text = match.group(1)
                highlighted = self.GREEN + quoted_text + self.RESET
                msg = msg.replace(quoted_text, highlighted)
        
        # Highlight TTS response text in queue/synth lines
        if 'TTS QUEUE: Enqueuing response:' in msg or 'TTS SYNTH: Generating speech for:' in msg:
            import re
            # Extract text between quotes
            match = re.search(r": ('.*?)$", msg)
            if match:
                quoted_text = match.group(1)
                highlighted = self.GREEN + quoted_text + self.RESET
                msg = msg.replace(quoted_text, highlighted)
        
        return msg


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,  # Force reconfiguration
)
logger = logging.getLogger("orchestrator")

# Apply colored formatter
for handler in logging.root.handlers:
    handler.setFormatter(ColoredFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

# Suppress verbose logging from FunASR and other libraries
logging.getLogger("funasr").setLevel(logging.ERROR)
logging.getLogger("modelscope").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

# Ensure immediate flushing
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()


def wav_bytes_to_pcm(wav_bytes: bytes) -> bytes:
    with io.BytesIO(wav_bytes) as buffer:
        with wave.open(buffer, "rb") as wav_file:
            return wav_file.readframes(wav_file.getnframes())


def wav_bytes_to_pcm_with_rate(wav_bytes: bytes) -> tuple[bytes, int]:
    with io.BytesIO(wav_bytes) as buffer:
        with wave.open(buffer, "rb") as wav_file:
            pcm = wav_file.readframes(wav_file.getnframes())
            return pcm, wav_file.getframerate()


def estimate_spoken_prefix(text: str, elapsed_s: float, total_s: float) -> str:
    if total_s <= 0 or elapsed_s <= 0:
        return ""
    words = text.split()
    if not words:
        return ""
    fraction = min(1.0, elapsed_s / total_s)
    spoken_count = int(len(words) * fraction)
    if spoken_count <= 0:
        return ""
    return " ".join(words[:spoken_count])


def strip_spoken_prefix(new_text: str, previous_text: str, elapsed_s: float, total_s: float) -> str:
    prefix = estimate_spoken_prefix(previous_text, elapsed_s, total_s)
    if not prefix:
        return new_text
    prefix_words = prefix.split()
    new_words = new_text.split()
    if len(new_words) >= len(prefix_words):
        if [w.lower() for w in new_words[:len(prefix_words)]] == [w.lower() for w in prefix_words]:
            return " ".join(new_words[len(prefix_words):]).strip()
    return new_text


def ensure_silero_model(config: VoiceConfig) -> str | None:
    if config.silero_model_path:
        return config.silero_model_path
    if not config.silero_auto_download:
        return None

    min_bytes = 1_000_000

    root_dir = Path(__file__).resolve().parents[2]
    cache_dir = root_dir / config.silero_model_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = cache_dir / "silero_vad.onnx"

    if model_path.exists():
        try:
            if model_path.stat().st_size >= min_bytes:
                return str(model_path)
            logger.warning("Silero model too small (%d bytes); re-downloading", model_path.stat().st_size)
            model_path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        logger.info("Downloading Silero VAD model to %s", model_path)
        urlretrieve(config.silero_model_url, model_path)
        try:
            size = model_path.stat().st_size
        except OSError:
            size = 0
        if size < min_bytes:
            logger.warning("Downloaded Silero model size %d bytes is invalid; deleting", size)
            model_path.unlink(missing_ok=True)
            return None
        return str(model_path)
    except Exception as exc:  # pragma: no cover
        logger.warning("Silero model download failed: %s", exc)
        return None


def extract_text_from_gateway_message(message: str) -> str:
    """Extract text from gateway message. Handles JSON payloads or plain text."""
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        # Preserve leading spaces from streaming deltas (word boundaries), drop trailing noise.
        return message.rstrip()

    # If JSON parsed to a primitive (string, number, bool), return it as string
    if isinstance(payload, (str, int, float, bool)):
        if isinstance(payload, str):
            return payload.rstrip()
        return str(payload).strip()
    
    # Handle dict payloads
    if isinstance(payload, dict):
        if "text" in payload:
            value = payload["text"]
            if isinstance(value, str):
                return value.rstrip()
            return str(value).strip()
        if "content" in payload:
            content = payload["content"]
            if isinstance(content, str):
                return content.rstrip()
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        part = block.get("text", "")
                        if isinstance(part, str):
                            parts.append(part.rstrip())
                        else:
                            parts.append(str(part).strip())
                return "\n".join([p for p in parts if p])
        if "data" in payload and isinstance(payload["data"], dict):
            text = payload["data"].get("text")
            if text:
                if isinstance(text, str):
                    return text.rstrip()
                return str(text).strip()
    return ""


def validate_runtime_config(config: VoiceConfig) -> None:
    """
    Validate orchestrator runtime configuration at startup.
    
    Checks:
    - Wake word configuration if enabled
    - Architecture-specific wake word engine requirements
    - Audio device availability
    - Numeric constant ranges
    - Wake word model files and resources
    """
    import platform
    import struct
    
    logger = logging.getLogger("orchestrator.validation")
    errors = []
    warnings_list = []
    
    # Detect system architecture
    arch_bits = struct.calcsize("P") * 8  # 32 or 64 bits
    machine = platform.machine().lower()  # armv7l, aarch64, i686, x86_64, etc.
    
    # Categorize architecture
    is_armv7 = "armv7" in machine or (arch_bits == 32 and "arm" in machine)
    is_arm64 = "aarch64" in machine or "arm64" in machine
    is_i386 = "i686" in machine or (arch_bits == 32 and "i" in machine)
    is_x86_64 = "x86_64" in machine
    
    logger.info("System architecture detected: %s (%d-bit)", machine, arch_bits)
    
    # =========================================================================
    # 1. WAKE WORD CONFIGURATION VALIDATION
    # =========================================================================
    if config.wake_word_enabled:
        logger.info("→ Validating wake word configuration...")
        
        # Architecture-specific wake word engine requirements
        if is_armv7:
            logger.info("  System: ARMv7 (Raspberry Pi) - Requires Precise or Picovoice")
            if not (config.precise_enabled or config.picovoice_enabled):
                errors.append(
                    "ARMv7 system detected but wake word engine not compatible. "
                    "Set PRECISE_ENABLED=true or PICOVOICE_ENABLED=true. "
                    "(OpenWakeWord is not recommended for ARMv7)"
                )
        elif is_arm64 or is_i386 or is_x86_64:
            logger.info("  System: %s - Requires OpenWakeWord or Picovoice", machine.upper())
            if not (config.openwakeword_enabled or config.picovoice_enabled):
                errors.append(
                    f"{machine.upper()} system detected but wake word engine not compatible. "
                    "Set OPENWAKEWORD_ENABLED=true or PICOVOICE_ENABLED=true. "
                    "(Precise is not recommended for this architecture)"
                )
        
        # Validate Precise engine if enabled
        if config.precise_enabled:
            logger.info("  Precise engine: validating model files...")
            if not config.precise_model_path:
                errors.append("PRECISE_ENABLED=true but PRECISE_MODEL_PATH is empty")
            else:
                model_path = Path(config.precise_model_path)
                if not model_path.exists():
                    errors.append(f"Precise model file not found: {model_path}")
                else:
                    model_size = model_path.stat().st_size
                    if model_size == 0:
                        errors.append(f"Precise model file is empty: {model_path}")
                    elif model_size < 10000:
                        warnings_list.append(f"Precise model file very small ({model_size} bytes): {model_path}")
                    else:
                        logger.info("    ✓ Model file exists (%d bytes)", model_size)
                
                # Check for .params file
                params_path = Path(str(model_path) + ".params")
                if not params_path.exists():
                    warnings_list.append(f"Precise model params file not found: {params_path}")
                else:
                    logger.info("    ✓ Params file exists")
        
        # Validate OpenWakeWord engine if enabled
        if config.openwakeword_enabled:
            logger.info("  OpenWakeWord engine: validating model...")
            if not config.openwakeword_model_path:
                errors.append("OPENWAKEWORD_ENABLED=true but OPENWAKEWORD_MODEL_PATH is empty")
            else:
                logger.info("    Model: %s", config.openwakeword_model_path)
                # Try to validate that model is available (built-in)
                try:
                    from openwakeword.model import Model
                    # Built-in models: hey_mycroft, alexa, americano, downstairs, grapefruit, 
                    # grasshopper, jarvis, ok_google, timer, weather
                    known_builtin = [
                        "hey_mycroft", "alexa", "americano", "downstairs", "grapefruit",
                        "grasshopper", "jarvis", "ok_google", "timer", "weather"
                    ]
                    model_name = config.openwakeword_model_path.lower().split('/')[-1].replace('.tflite', '')
                    if model_name not in known_builtin:
                        warnings_list.append(
                            f"OpenWakeWord model '{model_name}' may not be built-in. "
                            f"Available: {', '.join(known_builtin)}"
                        )
                    else:
                        logger.info("    ✓ Built-in model available")
                except ImportError:
                    warnings_list.append("openwakeword library not available for validation")
        
        # Validate Picovoice engine if enabled
        if config.picovoice_enabled:
            logger.info("  Picovoice engine: validating API key...")
            if not config.picovoice_key:
                errors.append("PICOVOICE_ENABLED=true but PICOVOICE_KEY is empty")
            else:
                logger.info("    ✓ API key is configured")
    
    # =========================================================================
    # 2. AUDIO DEVICE VALIDATION
    # =========================================================================
    logger.info("→ Validating audio devices...")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        device_names = [str(d['name']).lower() for d in devices]
        
        # Check capture device
        if config.audio_capture_device != "default" and config.audio_capture_device:
            capture_dev_str = str(config.audio_capture_device).lower()
            device_found = any(
                capture_dev_str in name or name in capture_dev_str 
                for name in device_names
            )
            if not device_found:
                # Try parsing as numeric device ID
                try:
                    dev_id = int(config.audio_capture_device)
                    if 0 <= dev_id < len(devices):
                        logger.info("  ✓ Capture device %d found: %s", dev_id, devices[dev_id]['name'])
                    else:
                        errors.append(f"Capture device ID out of range: {dev_id} (available: 0-{len(devices)-1})")
                except (ValueError, TypeError):
                    warnings_list.append(f"Capture device not found: {config.audio_capture_device}")
            else:
                logger.info("  ✓ Capture device found: %s", config.audio_capture_device)
        else:
            logger.info("  ✓ Capture device: default")
        
        # Check playback device
        if config.audio_playback_device != "default" and config.audio_playback_device:
            playback_dev_str = str(config.audio_playback_device).lower()
            device_found = any(
                playback_dev_str in name or name in playback_dev_str 
                for name in device_names
            )
            if not device_found:
                try:
                    dev_id = int(config.audio_playback_device)
                    if 0 <= dev_id < len(devices):
                        logger.info("  ✓ Playback device %d found: %s", dev_id, devices[dev_id]['name'])
                    else:
                        errors.append(f"Playback device ID out of range: {dev_id} (available: 0-{len(devices)-1})")
                except (ValueError, TypeError):
                    warnings_list.append(f"Playback device not found: {config.audio_playback_device}")
            else:
                logger.info("  ✓ Playback device found: %s", config.audio_playback_device)
        else:
            logger.info("  ✓ Playback device: default")
    except ImportError:
        warnings_list.append("sounddevice library not available for device validation")
    except Exception as e:
        warnings_list.append(f"Could not validate audio devices: {e}")
    
    # =========================================================================
    # 3. NUMERIC CONSTANT RANGES
    # =========================================================================
    logger.info("→ Validating numeric constants...")
    
    # Audio sample rate
    if config.audio_sample_rate not in [8000, 16000, 44100, 48000]:
        warnings_list.append(
            f"Unusual AUDIO_SAMPLE_RATE={config.audio_sample_rate} (typical: 8000, 16000, 44100, 48000)"
        )
    else:
        logger.info("  ✓ Audio sample rate: %d Hz", config.audio_sample_rate)
    
    # Audio frame size
    if not (5 <= config.audio_frame_ms <= 100):
        errors.append(
            f"AUDIO_FRAME_MS={config.audio_frame_ms} out of range (must be 5-100 ms)"
        )
    else:
        logger.info("  ✓ Audio frame: %d ms", config.audio_frame_ms)
    
    # VAD timeout
    if not (100 <= config.vad_min_silence_ms <= 10000):
        errors.append(
            f"VAD_MIN_SILENCE_MS={config.vad_min_silence_ms} out of range (must be 100-10000 ms)"
        )
    else:
        logger.info("  ✓ VAD min silence: %d ms", config.vad_min_silence_ms)
    
    # Wake word timeout
    if config.wake_word_enabled and not (1000 <= config.wake_word_timeout_ms <= 600000):
        errors.append(
            f"WAKE_WORD_TIMEOUT_MS={config.wake_word_timeout_ms} out of range (must be 1000-600000 ms)"
        )
    elif config.wake_word_enabled:
        logger.info("  ✓ Wake word timeout: %d ms", config.wake_word_timeout_ms)
    
    # Audio gains
    if not (0.1 <= config.audio_input_gain <= 10.0):
        errors.append(
            f"AUDIO_INPUT_GAIN={config.audio_input_gain} out of range (must be 0.1-10.0)"
        )
    else:
        logger.info("  ✓ Audio input gain: %.2fx", config.audio_input_gain)

    if not (0.1 <= config.audio_output_gain <= 5.0):
        errors.append(
            f"AUDIO_OUTPUT_GAIN={config.audio_output_gain} out of range (must be 0.1-5.0)"
        )
    else:
        logger.info("  ✓ Audio output gain: %.2fx", config.audio_output_gain)
    
    # TTS speed
    if not (0.5 <= config.piper_speed <= 5.0):
        warnings_list.append(
            f"PIPER_SPEED={config.piper_speed} unusual (typical: 0.5-5.0)"
        )
    else:
        logger.info("  ✓ TTS speed: %.2fx", config.piper_speed)
    
    # =========================================================================
    # REPORT RESULTS
    # =========================================================================
    if warnings_list:
        logger.warning("=" * 70)
        logger.warning("RUNTIME CONFIGURATION WARNINGS")
        logger.warning("=" * 70)
        for warning in warnings_list:
            logger.warning("  ⚠ %s", warning)
        logger.warning("=" * 70)
    
    if errors:
        logger.error("=" * 70)
        logger.error("RUNTIME CONFIGURATION ERRORS - Cannot start orchestrator")
        logger.error("=" * 70)
        for error in errors:
            logger.error("  ❌ %s", error)
        logger.error("=" * 70)
        raise RuntimeError(f"Runtime configuration validation failed with {len(errors)} error(s). See logs above.")
    
    logger.info("✓ All runtime configuration checks passed!")


def is_raspberry_pi() -> bool:
    """Best-effort Raspberry Pi detection for Pi-specific audio workarounds."""
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "raspberry pi" in model:
                return True
    except Exception:
        pass
    machine = platform.machine().lower()
    return machine.startswith("arm") or machine.startswith("aarch64")


def _resolve_device_index(device: int | str | None, want_input: bool) -> int | None:
    """Resolve configured device value to a PortAudio device index.

    Returns None for "default" or if not resolvable.
    """
    if device is None:
        return None

    dev_str = str(device).strip()
    if not dev_str or dev_str.lower() == "default":
        return None

    try:
        import sounddevice as sd

        devices = sd.query_devices()
        channel_key = "max_input_channels" if want_input else "max_output_channels"

        # Numeric index
        if dev_str.isdigit():
            idx = int(dev_str)
            if 0 <= idx < len(devices) and int(devices[idx].get(channel_key, 0) or 0) > 0:
                return idx
            return None

        # ALSA style hw:X,Y / plughw:X,Y
        if dev_str.startswith(("hw:", "plughw:")):
            hw = dev_str.split(":", 1)[1]
            card = hw.split(",", 1)[0]
            match = next(
                (
                    i
                    for i, d in enumerate(devices)
                    if (
                        f"(hw:{hw})" in d.get("name", "")
                        or f"(hw:{card}," in d.get("name", "")
                    )
                    and int(d.get(channel_key, 0) or 0) > 0
                ),
                None,
            )
            return match

        # Fuzzy name match
        needle = dev_str.lower()
        match = next(
            (
                i
                for i, d in enumerate(devices)
                if needle in str(d.get("name", "")).lower()
                and int(d.get(channel_key, 0) or 0) > 0
            ),
            None,
        )
        return match
    except Exception:
        return None


def _rank_device_priority(name: str, hostapi_name: str) -> int:
    txt = f"{name} {hostapi_name}".lower()
    if "pipewire" in txt:
        return 0
    if "pulseaudio" in txt or "pulse" in txt:
        return 1
    if "usb" in txt:
        return 2
    return 3


def _auto_select_audio_device(want_input: bool) -> int | None:
    """Select best available device using configure_audio_devices-style priorities.

    Priority: PipeWire -> PulseAudio -> USB -> first available.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        channel_key = "max_input_channels" if want_input else "max_output_channels"

        candidates: list[tuple[int, int]] = []
        for i, dev in enumerate(devices):
            if int(dev.get(channel_key, 0) or 0) <= 0:
                continue
            hostapi_idx = int(dev.get("hostapi", -1) or -1)
            hostapi_name = ""
            if 0 <= hostapi_idx < len(hostapis):
                hostapi_name = str(hostapis[hostapi_idx].get("name", ""))
            rank = _rank_device_priority(str(dev.get("name", "")), hostapi_name)
            candidates.append((rank, i))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][1]
    except Exception:
        return None


def _pick_working_playback_rate(device_idx: int | None, desired_rate: int) -> int:
    """Pick a working playback sample rate for device.

    Tries desired first, then common rates (highest preferred), then device default.
    """
    try:
        import sounddevice as sd

        rates_desc = [192000, 176400, 96000, 88200, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000]
        ordered = [desired_rate] + [r for r in rates_desc if r != desired_rate]
        for rate in ordered:
            try:
                sd.check_output_settings(device=device_idx, samplerate=rate, channels=1)
                return int(rate)
            except Exception:
                continue

        info = sd.query_devices(device_idx, "output")
        fallback = int(round(float(info.get("default_samplerate", desired_rate) or desired_rate)))
        return fallback
    except Exception:
        return int(desired_rate)


def _describe_device(device_idx: int | None) -> str:
    if device_idx is None:
        return "default"
    try:
        import sounddevice as sd

        dev = sd.query_devices(device_idx)
        return f"#{device_idx} {dev.get('name', 'unknown')}"
    except Exception:
        return str(device_idx)


async def run_orchestrator() -> None:
    config = VoiceConfig()
    
    # Smart defaults: enable ring buffer clearing on ARM systems to prevent ghost transcripts
    if is_raspberry_pi() and config.wake_clear_ring_buffer is False:
        config.wake_clear_ring_buffer = True
        logger.info("Auto-enabling wake_clear_ring_buffer on ARM architecture")
    
    # Print immediately so user sees something
    print("\n" + "="*51, flush=True)
    print("  OpenClaw Voice Orchestrator - Initializing", flush=True)
    print("="*51 + "\n", flush=True)
    
    logger.info("Starting Python voice orchestrator (scaffold)")
    
    # Validate runtime configuration before proceeding with initialization
    print("→ Validating runtime configuration...", flush=True)
    validate_runtime_config(config)

    frame_samples = int(config.audio_sample_rate * (config.audio_frame_ms / 1000))
    logger.info("═══════════════════════════════════════════════════")
    logger.info("  OpenClaw Voice Orchestrator - Initializing")
    logger.info("═══════════════════════════════════════════════════")
    logger.info("Audio config: device=%s, sample_rate=%d Hz, frame_ms=%d", 
                config.audio_capture_device, config.audio_sample_rate, config.audio_frame_ms)
    logger.info("VAD config: type=%s, confidence=%.2f, min_silence=%d ms", 
                config.vad_type, config.vad_confidence, config.vad_min_silence_ms)
    
    ring_buffer = RingBuffer(max_frames=int(config.pre_roll_ms / config.audio_frame_ms))

    if config.audio_backend == "portaudio-duplex":
        duplex = DuplexAudioIO(
            sample_rate=config.audio_sample_rate,
            frame_samples=frame_samples,
            input_device=config.audio_capture_device,
            output_device=config.audio_playback_device,
            input_gain=config.audio_input_gain,
        )
        capture = duplex
        playback = duplex
        logger.info("Audio duplex initialized (input=%s, output=%s, gain=%.1fx)", config.audio_capture_device, config.audio_playback_device, config.audio_input_gain)
    else:
        # Runtime audio selection policy:
        # 1) Try configured .env devices first
        # 2) On fatal init/start errors, auto-select based on available hardware
        # 3) Do NOT modify .env (next restart retries configured values first)
        selected_capture_device: int | str = config.audio_capture_device
        selected_playback_device: int | str = config.audio_playback_device
        selected_playback_rate = config.audio_playback_sample_rate if config.audio_playback_sample_rate > 0 else config.audio_sample_rate

        try:
            cap_idx = _resolve_device_index(config.audio_capture_device, want_input=True)
            pb_idx = _resolve_device_index(config.audio_playback_device, want_input=False)

            # Validate configured capture device by opening check settings.
            import sounddevice as sd

            cap_ok = False
            cap_err = None
            for ch in (1, 2):
                try:
                    sd.check_input_settings(device=cap_idx, samplerate=config.audio_sample_rate, channels=ch)
                    cap_ok = True
                    break
                except Exception as exc:
                    cap_err = exc

            if not cap_ok:
                raise RuntimeError(f"Configured capture device failed validation ({config.audio_capture_device}): {cap_err}")

            # Validate configured playback device/rate.
            selected_playback_rate = _pick_working_playback_rate(pb_idx, selected_playback_rate)
            sd.check_output_settings(device=pb_idx, samplerate=selected_playback_rate, channels=1)

            # Keep configured values for this run.
            selected_capture_device = config.audio_capture_device
            selected_playback_device = config.audio_playback_device
            logger.info(
                "Audio device validation passed using configured devices (capture=%s, playback=%s, rate=%s)",
                _describe_device(cap_idx),
                _describe_device(pb_idx),
                selected_playback_rate,
            )
        except Exception as audio_exc:
            logger.error("Configured audio initialization failed: %s", audio_exc)
            logger.warning("Attempting automatic audio device selection (without changing .env)")

            auto_cap_idx = _auto_select_audio_device(want_input=True)
            auto_pb_idx = _auto_select_audio_device(want_input=False)

            if auto_cap_idx is None or auto_pb_idx is None:
                raise RuntimeError(
                    "No compatible audio devices found for automatic fallback; please reconnect audio hardware"
                ) from audio_exc

            selected_capture_device = auto_cap_idx
            selected_playback_device = auto_pb_idx
            selected_playback_rate = _pick_working_playback_rate(auto_pb_idx, selected_playback_rate)

            logger.warning(
                "Using auto-selected audio devices for this run: capture=%s playback=%s rate=%s Hz",
                _describe_device(auto_cap_idx),
                _describe_device(auto_pb_idx),
                selected_playback_rate,
            )
            logger.warning(".env remains unchanged; next restart will retry configured devices first")

        capture = AudioCapture(
            sample_rate=config.audio_sample_rate,
            frame_samples=frame_samples,
            device=selected_capture_device,
            input_gain=config.audio_input_gain,
        )
        logger.info("Audio capture initialized on device: %s (gain=%.1fx)", selected_capture_device, config.audio_input_gain)

    # VAD initialization
    print("→ Loading VAD model...", flush=True)
    logger.info("→ Loading VAD model (%s)...", config.vad_type)
    vad_start = time.monotonic()
    if config.vad_type.lower() == "webrtc":
        vad = WebRTCVAD(sample_rate=config.audio_sample_rate, frame_ms=config.audio_frame_ms)
    else:
        silero_path = ensure_silero_model(config)
        vad = SileroVAD(
            sample_rate=config.audio_sample_rate,
            frame_samples=frame_samples,
            model_path=silero_path or None,
        )
    vad_elapsed = int((time.monotonic() - vad_start) * 1000)
    logger.info("✓ VAD loaded in %dms", vad_elapsed)
    print(f"✓ VAD loaded in {vad_elapsed}ms", flush=True)

    cut_in_silero: SileroVAD | None = None
    if config.vad_cut_in_use_silero:
        if isinstance(vad, SileroVAD):
            cut_in_silero = vad
            logger.info("✓ Cut-in Silero VAD: using primary Silero model")
        else:
            logger.info("→ Loading Silero VAD for cut-in gate...")
            # Use ensure_silero_model to get path with v5 model URL from config
            silero_path = ensure_silero_model(config)
            cut_in_silero = SileroVAD(
                sample_rate=config.audio_sample_rate,
                frame_samples=frame_samples,
                model_path=silero_path or None,
            )
            if cut_in_silero.loaded:
                logger.info("✓ Cut-in Silero VAD loaded")
            else:
                logger.warning("Cut-in Silero VAD failed to load; disabling Silero gate")
                cut_in_silero = None
                config.vad_cut_in_use_silero = False

    state = VoiceState.IDLE
    wake_state = WakeState.AWAKE if not config.wake_word_enabled else WakeState.ASLEEP
    last_activity_ts = time.monotonic()
    last_speech_ts: float | None = None
    chunk_start_ts: float | None = None
    chunk_frames: list[bytes] = []
    cut_in_triggered_ts: float | None = None
    active_transcriptions = 0
    tts_playing = False
    tts_base_gain = config.audio_output_gain
    tts_gain = tts_base_gain
    last_playback_frame: bytes | None = None
    last_tts_text = ""
    last_tts_ts = 0.0
    tts_dedupe_window_ms = 800
    current_request_id = 0  # Incremented on each user message
    current_tts_request_id = 0  # Tracks which request is currently playing
    warned_wake_resample = False
    warned_aec_stub = False
    wake_sleep_ts: float | None = None
    wake_sleep_cooldown_ms = max(0, config.wake_sleep_cooldown_ms)
    last_wake_detected_ts: float | None = None
    last_wake_conf_log_ts = 0.0

    wake_detector = None
    active_wake_engine = None
    if config.wake_word_enabled:
        wake_start = time.monotonic()
        
        # Select wake word engine based on enabled flags
        if config.openwakeword_enabled:
            active_wake_engine = "openwakeword"
            logger.info("→ Loading Wake Word detector (OpenWakeWord: %s)...", config.openwakeword_wake_word)
            wake_detector = OpenWakeWordDetector(
                model_path=config.openwakeword_model_path,
                confidence=config.openwakeword_confidence,
            )
        elif config.precise_enabled:
            active_wake_engine = "precise"
            logger.info("→ Loading Wake Word detector (Precise: %s)...", config.precise_wake_word)
            wake_detector = MycoftPreciseDetector(
                model_path=config.precise_model_path,
                confidence=config.precise_confidence,
            )
        elif config.picovoice_enabled:
            active_wake_engine = "picovoice"
            logger.info("→ Loading Wake Word detector (Picovoice: %s)...", config.picovoice_wake_word)
            wake_detector = PicovoiceDetector(
                model_path=config.picovoice_wake_word,
                access_key=config.picovoice_key,
                confidence=config.picovoice_confidence,
            )
        
        wake_elapsed = int((time.monotonic() - wake_start) * 1000)
        if wake_detector:
            logger.info("✓ Wake Word detector loaded in %dms", wake_elapsed)
            # Warm up Precise detector to trigger TensorFlow loading (so it doesn't delay first real detection)
            # OpenWakeWord uses TFLite and doesn't need warm-up
            if active_wake_engine == "precise":
                logger.info("→ Warming up wake detector (loading TensorFlow models)...")
                warmup_start = time.monotonic()
                dummy_audio = np.zeros(2048, dtype=np.int16).tobytes()
                try:
                    wake_detector.detect(dummy_audio)
                    # Reset detector state after warm-up to clear the dummy audio from internal buffer
                    if hasattr(wake_detector, 'reset_state'):
                        wake_detector.reset_state()
                    warmup_elapsed = int((time.monotonic() - warmup_start) * 1000)
                    logger.info("✓ Wake detector ready (TensorFlow loaded in %dms)", warmup_elapsed)
                except Exception as e:
                    logger.warning("Wake detector warm-up failed: %s", e)
        else:
            logger.error("⚠ Wake word detector failed to initialize; staying ASLEEP to avoid always-on transcription")
            if config.openwakeword_enabled:
                logger.error("   Check OPENWAKEWORD_MODEL_PATH=%s", config.openwakeword_model_path)
            elif config.precise_enabled:
                logger.error("   Check PRECISE_MODEL_PATH=%s", config.precise_model_path)
            elif config.picovoice_enabled:
                logger.error("   Check PICOVOICE_KEY configuration")
            wake_state = WakeState.ASLEEP
            last_wake_detected_ts = None

    # STT client
    print("→ Initializing Whisper STT client...", flush=True)
    logger.info("→ Initializing Whisper STT client (%s)...", config.whisper_url)
    whisper_start = time.monotonic()
    whisper_client = WhisperClient(config.whisper_url)
    whisper_elapsed = int((time.monotonic() - whisper_start) * 1000)
    logger.info("✓ Whisper client ready in %dms", whisper_elapsed)
    print(f"✓ Whisper client ready in {whisper_elapsed}ms", flush=True)
    
    # Emotion model
    emotion_model_ref = config.sensevoice_model_path or config.emotion_model or None
    if config.emotion_enabled and emotion_model_ref:
        logger.info("→ Loading SenseVoice model (%s)... (this may take 30-60 seconds)", emotion_model_ref)
        print(f"\n→ Loading SenseVoice model ({emotion_model_ref})...", flush=True)
        print("  (Suppressing FunASR verbose output - please wait...)", flush=True)
        emotion_start = time.monotonic()
        # Suppress FunASR verbose output to both stdout and stderr
        with open(os.devnull, 'w') as devnull:
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            try:
                sys.stdout = devnull
                sys.stderr = devnull
                emotion = SenseVoice(model_path=emotion_model_ref)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
        emotion_elapsed = int((time.monotonic() - emotion_start) * 1000)
        logger.info("✓ SenseVoice loaded in %dms", emotion_elapsed)
        print(f"✓ SenseVoice loaded in {emotion_elapsed}ms\n", flush=True)
    else:
        emotion = None
    
    # Gateway
    print("→ Initializing gateway...", flush=True)
    logger.info("→ Initializing gateway (%s)...", config.gateway_provider)
    gateway_start = time.monotonic()
    gateway = build_gateway(config)
    gateway_elapsed = int((time.monotonic() - gateway_start) * 1000)
    logger.info("✓ Gateway ready in %dms", gateway_elapsed)
    print(f"✓ Gateway ready in {gateway_elapsed}ms", flush=True)
    
    session_id = f"{config.gateway_session_prefix}-{int(time.time())}"
    agent_id = config.gateway_agent_id or "assistant"
    
    # TTS client
    print("→ Initializing Piper TTS client...", flush=True)
    logger.info("→ Initializing Piper TTS client (%s)...", config.piper_url)
    piper_start = time.monotonic()
    piper = PiperClient(config.piper_url)
    piper_elapsed = int((time.monotonic() - piper_start) * 1000)
    logger.info("✓ Piper client ready in %dms", piper_elapsed)
    print(f"✓ Piper client ready in {piper_elapsed}ms", flush=True)
    if config.audio_backend != "portaudio-duplex":
        playback_rate = selected_playback_rate
        pi_keepalive = bool(config.audio_playback_keepalive_enabled and is_raspberry_pi())
        if config.audio_playback_keepalive_enabled and not pi_keepalive:
            logger.info("Playback keepalive requested but disabled (non-Pi system)")
        elif pi_keepalive:
            logger.info(
                "Playback keepalive enabled for Pi (interval=%dms)",
                config.audio_playback_keepalive_interval_ms,
            )
        playback = AudioPlayback(
            sample_rate=playback_rate,
            device=selected_playback_device,
            lead_in_ms=config.audio_playback_lead_in_ms,
            keepalive_enabled=pi_keepalive,
            keepalive_interval_ms=config.audio_playback_keepalive_interval_ms,
        )
    
    # Generate audio feedback sounds
    logger.info("→ Generating audio feedback sounds...")
    wake_click_sound = generate_click_sound(sample_rate=config.audio_sample_rate, duration_ms=12, frequency=2000)
    timeout_swoosh_sound = None
    aec = WebRTCAEC(
        sample_rate=config.audio_sample_rate,
        frame_ms=config.audio_frame_ms,
        strength=config.echo_cancel_strength,
    ) if config.echo_cancel else None

    aec_status = AECStatus(
        enabled=bool(config.echo_cancel),
        backend="webrtc_audio_processing",
        available=aec is not None,
    )
    logger.info("✓ AEC: enabled=%s backend=%s available=%s", aec_status.enabled, aec_status.backend, aec_status.available)

    tts_queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
    tts_stop_event = threading.Event()
    tts_playback_start_ts: float | None = None
    current_tts_text = ""
    current_tts_duration_s = 0.0
    
    print("\n" + "="*51, flush=True)
    print("  ✓ System Ready - All models loaded", flush=True)
    print(f"  Session: {session_id} | Agent: {agent_id}", flush=True)
    print("="*51 + "\n", flush=True)
    
    logger.info("═══════════════════════════════════════════════════")
    logger.info("  ✓ System Ready - All models loaded")
    logger.info("  Session: %s | Agent: %s", session_id, agent_id)
    logger.info("═══════════════════════════════════════════════════")

    def playback_callback(pcm: bytes) -> None:
        nonlocal last_playback_frame
        last_playback_frame = pcm

    playback.set_playback_callback(playback_callback)

    # Debounce state for transcript aggregation
    pending_transcripts: list[tuple[str, str]] = []  # (transcript, emotion_tag)
    debounce_task: asyncio.Task | None = None

    def clean_text_for_tts(text: str) -> str:
        """Remove punctuation and icon symbols that should not be spoken by TTS.
        
        Removes: colons, semicolons, quotes, brackets, parentheses
        Removes: emoji/icon symbol characters
        Keeps: periods, commas, dashes (natural for pacing/reading)
        """
        # Remove punctuation that would be read aloud
        text = text.replace(":", "")  # colon
        text = text.replace(";", "")  # semicolon
        text = text.replace('"', "")  # quote
        text = text.replace("'", "")  # apostrophe (but keeps contractions)
        text = text.replace("(", "")  # open paren
        text = text.replace(")", "")  # close paren
        text = text.replace("[", "")  # open bracket
        text = text.replace("]", "")  # close bracket
        text = text.replace("{", "")  # open brace
        text = text.replace("}", "")  # close brace
        text = text.replace("/", " ")  # slash -> space

        # Remove emoji/icon symbols and emoji formatting code points.
        # So = Symbol, Other (emoji/pictographs), plus variation selector/joiner helpers.
        text = "".join(
            ch
            for ch in text
            if unicodedata.category(ch) != "So" and ch not in {"\ufe0f", "\u200d"}
        )

        # Clean up multiple spaces
        text = re.sub(r'\s+', ' ', text).strip()

        # If nothing speakable remains, skip TTS for this chunk.
        if not any(ch.isalnum() for ch in text):
            return ""

        return text

    async def submit_tts(text: str, request_id: int = 0) -> None:
        nonlocal last_tts_text, last_tts_ts
        nonlocal current_tts_text, current_tts_duration_s, tts_playback_start_ts
        nonlocal tts_playing, current_tts_request_id
        
        # Filter out NO_REPLY markers (final safeguard)
        if "NO_REPLY" in text or "NO_RE" in text or text.strip() in ["NO", "_RE", "NO _RE"]:
            logger.info("🚫 Filtered NO_REPLY from TTS: '%s'", text)
            return
        
        now = time.monotonic()
        if text == last_tts_text and (now - last_tts_ts) * 1000 < tts_dedupe_window_ms:
            return

        # Only interrupt if this is a new request (different from currently playing)
        if request_id and request_id != current_tts_request_id and tts_playing and current_tts_text and tts_playback_start_ts is not None:
            logger.info("🔄 TTS [req#%d→%d]: New user message arrived; stopping current playback", current_tts_request_id, request_id)
            tts_stop_event.set()
        elif tts_playing and current_tts_text and tts_playback_start_ts is not None and (not request_id or request_id == current_tts_request_id):
            # Same request, so strip prefix to avoid re-speaking already played content
            elapsed = now - tts_playback_start_ts
            trimmed = strip_spoken_prefix(text, current_tts_text, elapsed, current_tts_duration_s)
            if trimmed != text:
                logger.info("✂️ TTS [req#%d]: Stripped spoken prefix (%d→%d chars)", request_id, len(text), len(trimmed))
            text = trimmed

        if not text:
            return

        # Clean punctuation that would be spoken as words
        text = clean_text_for_tts(text)
        if not text:
            logger.info("🚫 Text became empty after punctuation/icon cleanup")
            return

        last_tts_text = text
        last_tts_ts = now
        effective_request_id = request_id if request_id else current_request_id
        await tts_queue.put((text, effective_request_id))

    async def send_debounced_transcripts() -> None:
        """Send accumulated transcripts after debounce period."""
        nonlocal pending_transcripts
        logger.info("⏱️ Debounce timer started (will fire in %dms)", config.gateway_debounce_ms)
        await asyncio.sleep(config.gateway_debounce_ms / 1000)
        
        logger.info("⏱️ Debounce timer fired with %d pending transcripts", len(pending_transcripts))
        if not pending_transcripts:
            logger.info("⏱️ No transcripts to send (pending_transcripts is empty)")
            return
        
        # Combine all pending transcripts
        combined_transcript = " ".join(t[0] for t in pending_transcripts)
        # Use emotion from first transcript (or combine if needed)
        emotion_tag = pending_transcripts[0][1] if pending_transcripts else ""
        transcript_count = len(pending_transcripts)
        
        pending_transcripts.clear()
        
        # Increment request ID for new user message
        nonlocal current_request_id
        current_request_id += 1
        logger.info("📍 New user message [req#%d]", current_request_id)
        
        # Gateway submission
        final_text = f"[{emotion_tag}] {combined_transcript}" if emotion_tag else combined_transcript
        print(f"\033[93m→ USER: {combined_transcript}\033[0m", flush=True)
        logger.info("→ GATEWAY: Sending debounced transcript (%d parts) to %s [req#%d]", transcript_count, gateway.provider, current_request_id)
        gw_start = time.monotonic()
        try:
            response_text = await gateway.send_message(
                final_text,
                session_id=session_id,
                agent_id=agent_id,
                metadata={"emotion": emotion_tag} if emotion_tag else {},
            )
            gw_elapsed = int((time.monotonic() - gw_start) * 1000)
            logger.info("← GATEWAY: Response received in %dms", gw_elapsed)
            if response_text:
                print(f"\033[94m← ASSISTANT: {response_text}\033[0m", flush=True)
                logger.info("→ TTS QUEUE [req#%d]: Enqueuing response: '%s'", current_request_id, response_text[:80])
                # Update activity timestamp to keep system awake during TTS synthesis
                last_activity_ts = time.monotonic()
                await submit_tts(response_text, request_id=current_request_id)
        except Exception as exc:
            logger.warning("Gateway send failed (%s); continuing", exc)

    def count_syllables(word: str) -> int:
        """Estimate syllable count based on vowel groups."""
        word = word.lower()
        if not word:
            return 0
        vowels = "aeiouy"
        syllable_count = 0
        previous_was_vowel = False
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not previous_was_vowel:
                syllable_count += 1
            previous_was_vowel = is_vowel
        return max(1, syllable_count)

    async def process_chunk(
        pcm: bytes,
        cut_in_ts: float | None = None,
        chunk_started_ts: float | None = None,
    ) -> None:
        nonlocal active_transcriptions, state, pending_transcripts, debounce_task
        active_transcriptions += 1
        state = VoiceState.SENDING
        try:
            wav_bytes = pcm_to_wav_bytes(pcm, config.audio_sample_rate)
            
            # STT phase
            logger.info("→ STT: Sending %d bytes to Whisper", len(wav_bytes))
            stt_start = time.monotonic()
            try:
                transcript = await asyncio.to_thread(whisper_client.transcribe, wav_bytes)
            except Exception as exc:
                logger.error("Whisper transcription failed: %s", exc)
                transcript = "[inaudible]"
            stt_elapsed = int((time.monotonic() - stt_start) * 1000)
            logger.info("← STT: Complete in %dms: '%s'", stt_elapsed, transcript[:80])
                
            transcript = transcript.strip()
            
            # Filter out transcripts containing [inaudible]
            if "[inaudible]" in transcript.lower():
                logger.warning("⊘ Transcript filtered out: contains [inaudible]")
                return
            
            # Filter out transcripts that are only punctuation/silence markers
            # Keep only if there are actual words (letters/numbers)
            import re
            has_words = bool(re.search(r'[a-zA-Z0-9]', transcript))
            if not transcript or not has_words:
                logger.warning(
                    "⊘ Transcript filtered out: empty=%s, has_words=%s, raw_text='%s'",
                    not transcript,
                    has_words,
                    transcript if transcript else "[EMPTY]"
                )
                return
            
            # Filter single-syllable words during cut-in
            if cut_in_ts is not None:
                reference_ts = chunk_started_ts if chunk_started_ts is not None else time.monotonic()
                elapsed_ms = int((reference_ts - cut_in_ts) * 1000)
                words = transcript.split()
                # Only apply filter if: single word AND within 500ms of cut-in
                if len(words) == 1 and elapsed_ms <= 500:
                    if count_syllables(words[0]) == 1:
                        logger.warning(
                            "⊘ Cut-in: Filtered out single-syllable word '%s' (elapsed=%dms)",
                            words[0],
                            elapsed_ms
                        )
                        return

            # Emotion detection phase
            emotion_tag = ""
            if config.emotion_enabled:
                logger.info("→ EMOTION: Detecting emotional state")
                emotion_start = time.monotonic()
                try:
                    emotion_tag = await asyncio.wait_for(
                        asyncio.to_thread(emotion.detect_emotion, wav_bytes),
                        timeout=config.emotion_timeout_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    emotion_tag = ""
                emotion_elapsed = int((time.monotonic() - emotion_start) * 1000)
                if emotion_tag:
                    logger.info("← EMOTION: Detected '%s' in %dms", emotion_tag, emotion_elapsed)
                else:
                    logger.info("← EMOTION: No emotion detected (%dms)", emotion_elapsed)

            # Add to pending transcripts and restart debounce timer
            pending_transcripts.append((transcript, emotion_tag))
            logger.info("⏱️ Transcript queued for debounce (%d pending)", len(pending_transcripts))
            
            # Cancel existing debounce task and start new one
            if debounce_task and not debounce_task.done():
                debounce_task.cancel()
            debounce_task = asyncio.create_task(send_debounced_transcripts())
        finally:
            active_transcriptions = max(0, active_transcriptions - 1)
            if active_transcriptions == 0:
                state = VoiceState.IDLE

    async def tts_loop() -> None:
        nonlocal tts_playing, tts_gain, last_playback_frame, tts_playback_start_ts
        nonlocal current_tts_text, current_tts_duration_s
        nonlocal current_tts_request_id, last_activity_ts
        while True:
            text, request_id = await tts_queue.get()
            if not text:
                continue
            tts_playing = True
            current_tts_text = text
            current_tts_request_id = request_id  # Track which request is now playing
            current_tts_duration_s = 0.0
            logger.info("▶️ TTS PLAY: Starting playback for [req#%d] (gain=%.1f)", request_id, tts_gain)
            # Reset wake timeout when TTS starts to keep conversation alive
            last_activity_ts = time.monotonic()
            try:
                try:
                    # TTS synthesis phase
                    logger.info("→ TTS SYNTH [req#%d]: Generating speech for: '%s'", request_id, text[:80])
                    synth_start = time.monotonic()
                    wav_bytes = await asyncio.to_thread(piper.synthesize, text, config.piper_voice_id, config.piper_speed)
                    synth_elapsed = int((time.monotonic() - synth_start) * 1000)
                    logger.info("← TTS SYNTH: Generated %d bytes in %dms", len(wav_bytes), synth_elapsed)

                    # Playback phase
                    logger.info("→ TTS PLAY: Starting playback (gain=%.1f)", tts_gain)
                    # Reset Silero RNN state to prevent carryover from previous speech
                    if cut_in_silero is not None:
                        cut_in_silero.reset_state()
                    tts_playback_start_ts = time.monotonic()
                    play_start = time.monotonic()
                    pcm, wav_rate = wav_bytes_to_pcm_with_rate(wav_bytes)
                    # Resample to playback rate (which may differ from capture rate for USB devices)
                    target_rate = playback.sample_rate
                    if wav_rate != target_rate:
                        pcm = resample_pcm(pcm, wav_rate, target_rate)
                    sample_count = len(pcm) / 2.0
                    current_tts_duration_s = sample_count / float(target_rate) if sample_count > 0 else 0.0
                    tts_stop_event.clear()
                    await asyncio.to_thread(playback.play_pcm, pcm, tts_gain, tts_stop_event)
                    play_elapsed = int((time.monotonic() - play_start) * 1000)
                    interrupted = tts_stop_event.is_set()
                    if interrupted:
                        logger.info("⏹️ TTS PLAY: Interrupted by mic speech (%dms)", play_elapsed)
                    else:
                        logger.info("← TTS PLAY: Playback complete in %dms", play_elapsed)
                        # Reset wake timeout after TTS completes to keep conversation alive
                        last_activity_ts = time.monotonic()
                    last_playback_frame = None
                    tts_playback_start_ts = None
                    if not interrupted:
                        logger.info("↻ Restarting audio capture after TTS playback")
                        try:
                            capture.restart()
                        except Exception as exc:  # pragma: no cover
                            logger.warning("Audio capture restart failed: %s", exc)
                    else:
                        logger.info("↻ Skipping capture restart after cut-in interruption")
                except Exception as exc:
                    logger.error("Piper TTS failed: %s", exc)
            finally:
                tts_playing = False
                tts_gain = tts_base_gain
                current_tts_text = ""
                current_tts_duration_s = 0.0

    async def gateway_listener() -> None:
        nonlocal current_request_id
        buffer = ""
        flush_task: asyncio.Task | None = None
        first_chunk_word_threshold = max(0, config.gateway_tts_fast_start_words)
        active_buffer_request_id = 0
        kickoff_sent_request_id = 0
        reconnect_delay_s = 1.0
        reconnect_delay_max_s = 8.0

        def should_emit_fast_start_chunk(text: str) -> bool:
            """Avoid early TTS kickoff at awkward clause boundaries (e.g., ending with 'so')."""
            s = text.strip()
            if not s:
                return False
            if s[-1] in ".!?":
                return True
            # Avoid kickoff on connector words that usually imply continuation.
            tokens = re.findall(r"[A-Za-z']+", s.lower())
            if not tokens:
                return False
            trailing_connectors = {
                "and", "or", "but", "so", "because", "if", "then", "than", "though", "although",
                "however", "therefore", "thus", "while", "when", "where", "which", "that", "who",
                "whom", "whose", "to", "of", "in", "on", "at", "for", "with", "from", "by",
                "a", "an", "the",
            }
            return tokens[-1] not in trailing_connectors

        def split_first_n_words(text: str, n: int) -> tuple[str, str]:
            """Split text into first n tokens and remainder, preserving punctuation in tokens."""
            if n <= 0:
                return "", text
            tokens = list(re.finditer(r"\S+", text))
            if len(tokens) < n:
                return "", text
            cutoff = tokens[n - 1].end()
            return text[:cutoff].strip(), text[cutoff:].strip()

        async def flush_buffer() -> None:
            nonlocal buffer
            if not buffer.strip():
                buffer = ""
                return
            
            # Filter out NO_REPLY markers
            text_to_send = buffer.strip()
            if "NO_REPLY" in text_to_send or "NO_RE" in text_to_send or text_to_send in ["NO", "_RE", "NO _RE"]:
                logger.info("🚫 Filtered NO_REPLY from flush: '%s'", text_to_send)
                buffer = ""
                return
                
            await submit_tts(text_to_send, request_id=current_request_id)
            buffer = ""

        while True:
            try:
                async for message in gateway.listen():
                    # If we receive any frame, connection is healthy again.
                    reconnect_delay_s = 1.0

                    if current_request_id != active_buffer_request_id:
                        # New user request boundary: reset sentence buffer state.
                        buffer = ""
                        active_buffer_request_id = current_request_id
                        if flush_task and not flush_task.done():
                            flush_task.cancel()

                    text = extract_text_from_gateway_message(message)
                    if not text:
                        continue

                    logger.info("🔤 Received: '%s'", text)

                    # Smart concatenation: determine if space is needed
                    needs_space = False
                    if buffer:
                        # Respect explicit leading whitespace from streamed deltas.
                        if text[0].isspace():
                            needs_space = False
                        else:
                            last_char = buffer[-1]
                            first_char = text[0]
                        
                            # No space before punctuation or closing brackets
                            if first_char in ",.!?;:)]}":
                                needs_space = False
                            # No space for apostrophe-led contraction chunks (e.g., '’t', ''s')
                            elif first_char in "'’":
                                needs_space = False
                            # No space for ordinal suffix after digit (1st, 2nd, etc.)
                            elif last_char.isdigit() and len(text) >= 2 and text[:2] in ["st", "nd", "rd", "th"]:
                                needs_space = False
                            # No space between consecutive digits (for numbers like 2026)
                            elif last_char.isdigit() and first_char.isdigit():
                                needs_space = False
                            # No space after opening brackets
                            elif last_char in "([{":
                                needs_space = False
                            # No space before/after colons in times (1:28)
                            elif last_char == ":" or first_char == ":":
                                needs_space = False
                            # No space for same-word token continuation (e.g., 'Austr' + 'ia').
                            elif last_char.isalpha() and first_char.isalpha() and not text[0].isspace():
                                needs_space = False
                            else:
                                needs_space = True
                    
                    buffer += (" " if needs_space else "") + text
                    logger.info("📝 Buffer: '%s'", buffer[:100])

                    # Fast-start policy: emit first chunk once threshold words are available for this request.
                    if first_chunk_word_threshold > 0 and kickoff_sent_request_id != current_request_id:
                        kickoff_text, remainder = split_first_n_words(buffer, first_chunk_word_threshold)
                        if kickoff_text and should_emit_fast_start_chunk(kickoff_text):
                            buffer = remainder
                            kickoff_sent_request_id = current_request_id
                            logger.info("🚀 Fast-start chunk [req#%d]: '%s'", current_request_id, kickoff_text)
                            await submit_tts(kickoff_text, request_id=current_request_id)
                            if flush_task and not flush_task.done():
                                flush_task.cancel()
                            continue

                    match = re.search(r"(.+?[.!?])\s*$", buffer)
                    if match:
                        sentence = match.group(1).strip()
                        buffer = buffer[len(sentence):].strip()
                        
                        # Filter out NO_REPLY markers and other special tokens
                        if "NO_REPLY" in sentence or "NO_RE" in sentence or sentence.strip() in ["NO", "_RE", "NO _RE"]:
                            logger.info("🚫 Filtered NO_REPLY marker: '%s'", sentence)
                            if flush_task and not flush_task.done():
                                flush_task.cancel()
                            continue
                        
                        logger.info("✅ Complete sentence: '%s'", sentence)
                        await submit_tts(sentence, request_id=current_request_id)
                        if flush_task and not flush_task.done():
                            flush_task.cancel()
                        continue

                    if flush_task and not flush_task.done():
                        flush_task.cancel()
                    flush_task = asyncio.create_task(asyncio.sleep(5))
                    flush_task.add_done_callback(lambda task: asyncio.create_task(flush_buffer()) if not task.cancelled() else None)

                # Stream ended cleanly (e.g., websocket dropped) — reconnect.
                logger.warning("Gateway listen stream ended; reconnecting in %.1fs", reconnect_delay_s)
            except (ConnectionRefusedError, OSError) as exc:
                logger.warning("Gateway unavailable (%s); retrying listener in %.1fs", exc, reconnect_delay_s)
            except Exception as exc:
                logger.error("Gateway listener error: %s (retrying in %.1fs)", exc, reconnect_delay_s)

            if flush_task and not flush_task.done():
                flush_task.cancel()
            await asyncio.sleep(reconnect_delay_s)
            reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)

    print("🎤 Audio capture starting. Press Ctrl+C to stop.", flush=True)
    logger.info("🎤 Audio capture starting. Press Ctrl+C to stop.")
    try:
        capture.start()
    except Exception as exc:
        logger.error("Audio capture failed to start with selected device (%s): %s", getattr(capture, "device", "unknown"), exc)
        if config.audio_backend != "portaudio-duplex":
            auto_cap_idx = _auto_select_audio_device(want_input=True)
            if auto_cap_idx is None:
                raise
            logger.warning(
                "Retrying capture start with auto-selected input device %s (without changing .env)",
                _describe_device(auto_cap_idx),
            )
            capture = AudioCapture(
                sample_rate=config.audio_sample_rate,
                frame_samples=frame_samples,
                device=auto_cap_idx,
                input_gain=config.audio_input_gain,
            )
            capture.start()
            logger.warning("Capture recovery succeeded using auto-selected input device for this run")
        else:
            raise
    print("🎧 Listening for audio input...\n", flush=True)
    logger.info("🎧 Listening for audio input...")
    
    asyncio.create_task(tts_loop())
    if getattr(gateway, "supports_listen", False):
        asyncio.create_task(gateway_listener())

    frame_count = 0
    last_heartbeat_ts = time.monotonic()
    heartbeat_interval = 10.0  # Log heartbeat every 10 seconds
    last_meter_ts = time.monotonic()
    meter_interval = 1.0
    mic_level_count = 0
    swoosh_played = False
    last_nonzero_mic_ts = time.monotonic()
    mic_silence_restart_s = 0.0
    mic_level_threshold = 0.001
    last_tts_speech_log_ts = 0.0
    tts_speech_log_interval = 1.0
    last_tts_meter_ts = 0.0
    tts_meter_interval = 0.5
    tts_rms_baseline = 0.0
    tts_rms_alpha = 0.05
    cut_in_hits = 0
    silero_zero_hits = 0
    speech_frame_count = 0
    min_speech_frames = max(1, int(config.vad_min_speech_ms / config.audio_frame_ms))
    
    try:
        while True:
            frame = capture.read_frame(timeout=1.0)
            if frame is None:
                await asyncio.sleep(0.01)
                continue

            now = time.monotonic()
            frame_count += 1

            processed_frame = frame
            
            # Periodic heartbeat to show system is alive
            if now - last_heartbeat_ts >= heartbeat_interval:
                logger.info("💓 Heartbeat: %d frames processed, state=%s", frame_count, state.name)
                last_heartbeat_ts = now

            # Live mic level meter (RMS + dBFS)
            if now - last_meter_ts >= meter_interval:
                try:
                    samples = np.frombuffer(processed_frame, dtype=np.int16).astype(np.float32)
                    if samples.size:
                        rms = float(np.sqrt(np.mean(samples ** 2)) / 32768.0)
                        dbfs = 20.0 * math.log10(max(rms, 1e-6))
                        logger.info("🎚️ Mic level: %.4f (%.1f dBFS)", rms, dbfs)
                        mic_level_count += 1
                        
                        # Swoosh sound disabled
                        # (Was: Play swoosh after second mic level log, but disabled per user request)
                        if False:  # DISABLED
                            try:
                                swoosh_sound = generate_swoosh_sound(sample_rate=config.audio_sample_rate)
                                playback.play_pcm(swoosh_sound, gain=2.5, stop_event=threading.Event())
                                logger.info("✓ Readiness chime (swoosh) played")
                                swoosh_played = True
                            except Exception as e:
                                logger.debug("Failed to play readiness chime: %s", e)
                                swoosh_played = True
                        else:
                            swoosh_played = True  # Mark as played so we don't keep trying
                        
                        if rms > mic_level_threshold:
                            last_nonzero_mic_ts = now
                except Exception as exc:  # pragma: no cover
                    logger.warning("Mic level meter error: %s", exc)
                last_meter_ts = now

            if mic_silence_restart_s > 0 and not tts_playing and (now - last_nonzero_mic_ts) >= mic_silence_restart_s:
                logger.warning("Mic silent for %.1fs → restarting capture", mic_silence_restart_s)
                try:
                    capture.restart()
                except Exception as exc:  # pragma: no cover
                    logger.warning("Audio capture restart failed: %s", exc)
                last_nonzero_mic_ts = now

            # Calculate RMS from RAW frame (before AEC) for diagnostics
            rms_raw = 0.0
            try:
                raw_samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
                if raw_samples.size:
                    rms_raw = float(np.sqrt(np.mean(raw_samples ** 2)) / 32768.0)
            except Exception:  # pragma: no cover
                rms_raw = 0.0

            if aec and tts_playing and last_playback_frame:
                try:
                    processed_frame = aec.process(frame, last_playback_frame)
                except NotImplementedError:
                    processed_frame = frame
                    if not warned_aec_stub:
                        logger.warning("WebRTC AEC bindings not configured; passing mic audio through.")
                        warned_aec_stub = True

            # Calculate RMS from processed frame (after AEC) for cut-in detection
            rms_cutin = 0.0
            try:
                cutin_samples = np.frombuffer(processed_frame, dtype=np.int16).astype(np.float32)
                if cutin_samples.size:
                    rms_cutin = float(np.sqrt(np.mean(cutin_samples ** 2)) / 32768.0)
            except Exception:  # pragma: no cover
                rms_cutin = 0.0

            # Track baseline RMS during TTS playback to detect mic speech over speaker bleed
            if tts_playing:
                if tts_rms_baseline == 0.0:
                    tts_rms_baseline = rms_raw
                else:
                    tts_rms_baseline = (1.0 - tts_rms_alpha) * tts_rms_baseline + tts_rms_alpha * rms_raw
            else:
                tts_rms_baseline = 0.0
                cut_in_hits = 0
                silero_zero_hits = 0

            # Store raw frame in ring buffer (not AEC-processed)
            # AEC is too aggressive during TTS and removes user speech along with echo
            # We use RMS baseline tracking for echo rejection instead
            ring_buffer.add_frame(frame)

            if config.wake_word_enabled and wake_state == WakeState.ASLEEP:
                # Keep awake while TTS is playing or queued so cut-in can work
                if tts_playing or not tts_queue.empty():
                    wake_state = WakeState.AWAKE
                    last_activity_ts = now
                else:
                    if wake_sleep_ts is not None and (now - wake_sleep_ts) * 1000 < wake_sleep_cooldown_ms:
                        await asyncio.sleep(0)
                        continue
                    if wake_detector:
                        wake_frame = processed_frame
                        # DEBUG: Check frame properties
                        frame_len = len(wake_frame) if wake_frame else 0
                        frame_rms = 0.0
                        if frame_len > 0:
                            samples = np.frombuffer(wake_frame, dtype=np.int16)
                            # Avoid RuntimeWarning by ensuring positive values
                            mean_sq = np.mean(samples.astype(np.float64) ** 2)
                            frame_rms = float(np.sqrt(max(mean_sq, 0.0)) / 32768.0)
                        
                        if config.audio_sample_rate != 16000:
                            if not warned_wake_resample:
                                logger.warning("Wake word expects 16kHz audio; resampling from %s Hz.", config.audio_sample_rate)
                                warned_wake_resample = True
                            wake_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
                        
                        try:
                            wake_result = wake_detector.detect(wake_frame)
                        except Exception as e:
                            logger.error("Wake detector error: %s", e)
                            wake_result = WakeWordResult(detected=False, confidence=0.0)

                        # Get active confidence threshold for logging
                        if active_wake_engine == "openwakeword":
                            active_confidence = config.openwakeword_confidence
                        elif active_wake_engine == "precise":
                            active_confidence = config.precise_confidence
                        elif active_wake_engine == "picovoice":
                            active_confidence = config.picovoice_confidence
                        else:
                            active_confidence = 0.5
                        
                        # Log only meaningful confidence spikes and rate-limit to avoid log spam.
                        wake_conf_log_threshold = max(0.15, active_confidence * 0.5)
                        if wake_result.confidence >= wake_conf_log_threshold and (now - last_wake_conf_log_ts) >= 0.75:
                            logger.info("Wake confidence spike: %.4f (frame_rms=%.6f, frame_len=%d)", wake_result.confidence, frame_rms, frame_len)
                            last_wake_conf_log_ts = now
                        elif frame_rms > 0.05 and (now - last_wake_conf_log_ts) >= 1.0:  # Also log when there's significant audio
                            logger.info("Audio detected but no spike: conf=%.4f, frame_rms=%.6f", wake_result.confidence, frame_rms)

                        if wake_result.detected:
                            # Guard against false detections on silence right after timeout/sleep.
                            if frame_rms < config.wake_min_detect_rms:
                                logger.info(
                                    "Ignoring wake detection on low RMS frame (conf=%.4f, rms=%.6f < %.6f)",
                                    wake_result.confidence,
                                    frame_rms,
                                    config.wake_min_detect_rms,
                                )
                                if wake_detector and hasattr(wake_detector, 'reset_state'):
                                    wake_detector.reset_state()
                                await asyncio.sleep(0)
                                continue
                            wake_state = WakeState.AWAKE
                            wake_sleep_ts = None
                            last_wake_detected_ts = now
                            last_activity_ts = now
                            state = VoiceState.LISTENING
                            chunk_start_ts = now
                            # Clear ring buffer if configured to avoid stale pre-wake audio (prevents ghost transcripts)
                            # Recommended for ARM systems where ring buffer latency is high
                            if config.wake_clear_ring_buffer:
                                ring_buffer.clear()
                                chunk_frames = [frame]
                            else:
                                # Reduced prebuffer from 200ms to 80ms to avoid capturing the hotword itself being spoken
                                wake_pre_roll_ms = min(80, config.pre_roll_ms)
                                wake_pre_roll_frames = max(0, int(wake_pre_roll_ms / config.audio_frame_ms))
                                prebuffer = ring_buffer.get_frames()
                                if wake_pre_roll_frames > 0 and len(prebuffer) > wake_pre_roll_frames:
                                    prebuffer = prebuffer[-wake_pre_roll_frames:]
                                chunk_frames = prebuffer
                                chunk_frames.append(frame)
                            last_speech_ts = now
                            logger.info("Wake word detected → awake")
                            # Play wake word click sound - DISABLED to prevent feedback loop
                            # try:
                            #     pcm_click = wav_bytes_to_pcm(wake_click_sound)
                            #     asyncio.create_task(asyncio.to_thread(playback.play_pcm, pcm_click, 1.0, threading.Event()))
                            # except Exception as exc:
                            #     logger.debug("Failed to play wake click sound: %s", exc)
                    await asyncio.sleep(0)
                    continue

            vad_frame = processed_frame
            if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                vad_frame = resample_pcm(processed_frame, config.audio_sample_rate, 16000)
            vad_result = vad.is_speech(vad_frame)
            vad_result_cutin = vad_result
            if tts_playing:
                # Use raw frame for cut-in VAD (AEC removes user speech along with echo)
                # RMS baseline tracking handles echo rejection instead
                vad_cutin_frame = frame
                if isinstance(vad, SileroVAD) and config.audio_sample_rate != 16000:
                    vad_cutin_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                vad_result_cutin = vad.is_speech(vad_cutin_frame)
            silero_gate = True
            silero_conf = None
            if tts_playing and config.vad_cut_in_use_silero:
                silero_gate = False
                if cut_in_silero is not None:
                    # Use raw frame for Silero (it should distinguish speech from echo better than WebRTC AEC)
                    silero_frame = frame
                    if config.audio_sample_rate != 16000:
                        silero_frame = resample_pcm(frame, config.audio_sample_rate, 16000)
                    silero_result = cut_in_silero.is_speech(silero_frame)
                    silero_conf = silero_result.confidence
                    silero_gate = silero_conf >= config.vad_cut_in_silero_confidence
            rms = 0.0
            try:
                samples = np.frombuffer(processed_frame, dtype=np.int16).astype(np.float32)
                if samples.size:
                    rms = float(np.sqrt(np.mean(samples ** 2)) / 32768.0)
            except Exception:  # pragma: no cover
                rms = 0.0

            speech_hit = bool(vad_result.speech_detected) and rms >= config.vad_min_rms
            if speech_hit:
                speech_frame_count += 1
            else:
                speech_frame_count = 0

            if speech_frame_count >= min_speech_frames:
                last_activity_ts = now
                last_speech_ts = now
                if not chunk_frames:
                    chunk_start_ts = now
                    chunk_frames = ring_buffer.get_frames()
                chunk_frames.append(processed_frame)
                if state == VoiceState.IDLE:
                    state = VoiceState.LISTENING
                    print("🎤 Speech detected → listening", flush=True)
                    logger.info("Speech detected → listening")
            elif chunk_frames:
                chunk_frames.append(processed_frame)

            if tts_playing:
                if now - last_tts_meter_ts >= tts_meter_interval:
                    logger.info(
                        "🎚️ Cut-in RMS (raw=%.4f, aec=%.4f, baseline=%.4f, excess=%.4f) | VAD: %s | silero: %s (conf=%.2f) | threshold=%.4f",
                        rms_raw,
                        rms_cutin,
                        tts_rms_baseline,
                        max(0.0, rms_raw - tts_rms_baseline),
                        vad_result_cutin.speech_detected,
                        silero_gate,
                        silero_conf if silero_conf is not None else -1.0,
                        config.vad_cut_in_rms,
                    )
                    logger.info(
                        "🎚️ Cut-in gate (ready=%s, silero_gate=%s, rms_excess=%.4f, rms_cutin=%.4f, hits=%d/%d, min_ms=%d)",
                        (tts_playback_start_ts is not None and int((now - tts_playback_start_ts) * 1000) >= config.vad_cut_in_min_ms),
                        silero_gate,
                        max(0.0, rms_raw - tts_rms_baseline),
                        rms_cutin,
                        cut_in_hits,
                        config.vad_cut_in_frames,
                        config.vad_cut_in_min_ms,
                    )
                    last_tts_meter_ts = now
                playback_ms = 0
                if tts_playback_start_ts is not None:
                    playback_ms = int((now - tts_playback_start_ts) * 1000)
                cut_in_ready = playback_ms >= config.vad_cut_in_min_ms
                rms_excess = max(0.0, rms_raw - tts_rms_baseline)
                if tts_playing and config.vad_cut_in_use_silero and silero_conf is not None:
                    if silero_conf <= 0.01 and vad_result_cutin.speech_detected and rms_excess >= config.vad_cut_in_rms:
                        silero_zero_hits += 1
                    else:
                        silero_zero_hits = 0
                    if silero_zero_hits >= 50:
                        logger.warning("Silero gate stuck at low confidence; disabling Silero cut-in gate")
                        config.vad_cut_in_use_silero = False
                        silero_gate = True
                cut_in_candidate = cut_in_ready and silero_gate and (
                    (vad_result_cutin.speech_detected and rms_excess >= config.vad_cut_in_rms)
                    or rms_cutin >= config.vad_cut_in_rms
                )
                if cut_in_candidate:
                    cut_in_hits += 1
                else:
                    cut_in_hits = 0
                cut_in = cut_in_hits >= config.vad_cut_in_frames
                if cut_in and now - last_tts_speech_log_ts >= tts_speech_log_interval:
                    logger.info(
                        "✋ Cut-in triggered! (rms_raw=%.4f, rms_aec=%.4f, rms_excess=%.4f, vad=%s, silero=%s, silero_conf=%.2f, playback_ms=%d, cut_in_ready=%s)",
                        rms_raw,
                        rms_cutin,
                        rms_excess,
                        vad_result_cutin.speech_detected,
                        silero_gate,
                        silero_conf if silero_conf is not None else -1.0,
                        playback_ms,
                        cut_in_ready,
                    )
                    print("✋ Cut-in triggered → stopping TTS", flush=True)
                    last_tts_speech_log_ts = now
                if cut_in:
                    if not chunk_frames:
                        cut_in_triggered_ts = now
                        chunk_start_ts = now
                        # Use minimal prebuffer for cut-in to keep interruptions tight
                        cut_in_pre_roll_frames = max(0, int(config.cut_in_pre_roll_ms / config.audio_frame_ms))
                        if cut_in_pre_roll_frames > 0:
                            prebuffer = ring_buffer.get_frames()
                            if len(prebuffer) > cut_in_pre_roll_frames:
                                prebuffer = prebuffer[-cut_in_pre_roll_frames:]
                            # Apply AEC to prebuffer frames to remove TTS while preserving early user speech
                            if aec and last_playback_frame:
                                aec_prebuffer = []
                                for pb_frame in prebuffer:
                                    try:
                                        aec_pb_frame = aec.process(pb_frame, last_playback_frame)
                                        aec_prebuffer.append(aec_pb_frame)
                                    except NotImplementedError:
                                        aec_prebuffer.append(pb_frame)
                                chunk_frames = aec_prebuffer
                            else:
                                chunk_frames = prebuffer
                            logger.info("📥 Cut-in prebuffer: %d frames (~%dms), AEC applied=%s", len(chunk_frames), len(chunk_frames) * config.audio_frame_ms, aec is not None and last_playback_frame is not None)
                        else:
                            chunk_frames = []
                            logger.info("📥 Cut-in prebuffer disabled (CUT_IN_PRE_ROLL_MS=0)")
                    chunk_frames.append(processed_frame)
                    last_speech_ts = now
                    last_activity_ts = now  # Reset wake timeout to keep listening after cut-in
                    wake_state = WakeState.AWAKE
                    last_wake_detected_ts = now
                    if tts_gain != 0.5:
                        tts_gain = 0.5
                    logger.info(
                        "⏹️ Setting tts_stop_event (rms_raw=%.4f, rms_aec=%.4f, rms_excess=%.4f, vad=%s, silero=%s, silero_conf=%.2f)",
                        rms_raw,
                        rms_cutin,
                        rms_excess,
                        vad_result_cutin.speech_detected,
                        silero_gate,
                        silero_conf if silero_conf is not None else -1.0,
                    )
                    tts_stop_event.set()
            if tts_playing and last_speech_ts:
                silence_ms = int(((now - last_speech_ts) * 1000))
                if silence_ms >= config.vad_min_silence_ms and tts_gain != tts_base_gain:
                    tts_gain = tts_base_gain
                    logger.info("Mic speech ended → restoring TTS volume")

            if chunk_frames and chunk_start_ts is not None:
                chunk_duration_ms = int((now - chunk_start_ts) * 1000)
                silence_ms = int(((now - last_speech_ts) * 1000)) if last_speech_ts else 0

                if silence_ms >= config.vad_min_silence_ms or chunk_duration_ms >= config.chunk_max_ms:
                    pcm = b"".join(chunk_frames)
                    latency_ms = int((now - cut_in_triggered_ts) * 1000) if cut_in_triggered_ts else -1
                    print(f"📦 Audio chunk ready: {chunk_duration_ms}ms, {len(pcm)} bytes, silence={silence_ms}ms, latency={latency_ms}ms", flush=True)
                    logger.info(
                        "═══ AUDIO CHUNK: duration=%d ms, size=%d bytes, silence=%d ms, frames=%d, latency=%d ms (cut-in→send) ═══",
                        chunk_duration_ms,
                        len(pcm),
                        silence_ms,
                        len(chunk_frames),
                        latency_ms,
                    )
                    asyncio.create_task(process_chunk(pcm, cut_in_triggered_ts, chunk_start_ts))
                    ring_buffer.clear()
                    chunk_frames = []
                    chunk_start_ts = None
                    last_speech_ts = None
                    cut_in_triggered_ts = None

            if config.wake_word_enabled and wake_state == WakeState.AWAKE:
                if last_wake_detected_ts is None:
                    wake_state = WakeState.ASLEEP
                    wake_sleep_ts = now
                    await asyncio.sleep(0)
                    continue
                inactive_ms = int((now - last_activity_ts) * 1000)
                if config.wake_word_timeout_ms > 0 and inactive_ms >= config.wake_word_timeout_ms:
                    # Don't timeout if TTS is playing, queued, or we're actively processing
                    debounce_pending = debounce_task is not None and not debounce_task.done()
                    has_pending_transcripts = bool(pending_transcripts)
                    if (
                        state in (VoiceState.IDLE, VoiceState.LISTENING)
                        and not tts_playing
                        and tts_queue.empty()
                        and not debounce_pending
                        and not has_pending_transcripts
                        and active_transcriptions == 0
                    ):
                        wake_state = WakeState.ASLEEP
                        wake_sleep_ts = now
                        last_wake_detected_ts = None
                        # Reset wake detector state to prevent immediate re-detection
                        if wake_detector and hasattr(wake_detector, 'reset_state'):
                            wake_detector.reset_state()
                        logger.info("Wake timeout reached → asleep")
                        # Timeout swoosh sound disabled
                        # (Was: play_pcm on timeout, but disabled per user request)
                        if False and timeout_swoosh_sound:  # DISABLED
                            try:
                                pcm_swoosh = wav_bytes_to_pcm(timeout_swoosh_sound)
                                asyncio.create_task(asyncio.to_thread(playback.play_pcm, pcm_swoosh, 1.0, threading.Event()))
                            except Exception as exc:
                                logger.debug("Failed to play timeout swoosh sound: %s", exc)

            await asyncio.sleep(0)
    finally:
        capture.stop()


def main() -> None:
    asyncio.run(run_orchestrator())


if __name__ == "__main__":
    main()
