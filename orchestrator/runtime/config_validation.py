import logging
import struct
from pathlib import Path

from orchestrator.config import VoiceConfig


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

    logger = logging.getLogger("orchestrator.validation")
    errors = []
    warnings_list = []

    arch_bits = struct.calcsize("P") * 8
    machine = platform.machine().lower()

    is_raspberry_pi = False
    for model_path in (
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    ):
        try:
            model_text = Path(model_path).read_text(errors="ignore").strip().lower()
        except Exception:
            continue
        if "raspberry pi" in model_text:
            is_raspberry_pi = True
            break

    is_armv7 = "armv7" in machine or (arch_bits == 32 and "arm" in machine)
    is_arm64 = "aarch64" in machine or "arm64" in machine
    is_i386 = "i686" in machine or (arch_bits == 32 and "i" in machine)
    is_x86_64 = "x86_64" in machine

    logger.info("System architecture detected: %s (%d-bit)", machine, arch_bits)

    if config.wake_word_enabled:
        logger.info("→ Validating wake word configuration...")

        if is_armv7 and is_raspberry_pi:
            logger.info("  System: Raspberry Pi ARMv7/ARMv6 - Requires Precise or Picovoice")
            if not (config.precise_enabled or config.picovoice_enabled):
                errors.append(
                    "Raspberry Pi ARMv7/ARMv6 detected but wake word engine not compatible. "
                    "Set PRECISE_ENABLED=true or PICOVOICE_ENABLED=true. "
                    "(OpenWakeWord is not recommended for ARMv7)"
                )
        elif is_arm64 or is_i386 or is_x86_64 or is_armv7:
            logger.info("  System: %s - Requires OpenWakeWord or Picovoice", machine.upper())
            if not (config.openwakeword_enabled or config.picovoice_enabled):
                errors.append(
                    f"{machine.upper()} system detected but wake word engine not compatible. "
                    "Set OPENWAKEWORD_ENABLED=true or PICOVOICE_ENABLED=true. "
                    "(Precise is not recommended for this architecture)"
                )

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

                params_path = Path(str(model_path) + ".params")
                if not params_path.exists():
                    warnings_list.append(f"Precise model params file not found: {params_path}")
                else:
                    logger.info("    ✓ Params file exists")

        if config.openwakeword_enabled:
            logger.info("  OpenWakeWord engine: validating model...")
            if not config.openwakeword_model_path:
                errors.append("OPENWAKEWORD_ENABLED=true but OPENWAKEWORD_MODEL_PATH is empty")
            else:
                logger.info("    Model: %s", config.openwakeword_model_path)
                try:
                    from openwakeword.model import Model

                    _ = Model
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

        if config.picovoice_enabled:
            logger.info("  Picovoice engine: validating API key...")
            if not config.picovoice_key:
                errors.append("PICOVOICE_ENABLED=true but PICOVOICE_KEY is empty")
            else:
                logger.info("    ✓ API key is configured")

    logger.info("→ Validating audio devices...")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        device_names = [str(d['name']).lower() for d in devices]

        if config.audio_capture_device != "default" and config.audio_capture_device:
            capture_dev_str = str(config.audio_capture_device).lower()
            device_found = any(
                capture_dev_str in name or name in capture_dev_str
                for name in device_names
            )
            if not device_found:
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

    logger.info("→ Validating numeric constants...")

    if config.audio_sample_rate not in [8000, 16000, 44100, 48000]:
        warnings_list.append(
            f"Unusual AUDIO_SAMPLE_RATE={config.audio_sample_rate} (typical: 8000, 16000, 44100, 48000)"
        )
    else:
        logger.info("  ✓ Audio sample rate: %d Hz", config.audio_sample_rate)

    if not (5 <= config.audio_frame_ms <= 100):
        errors.append(
            f"AUDIO_FRAME_MS={config.audio_frame_ms} out of range (must be 5-100 ms)"
        )
    else:
        logger.info("  ✓ Audio frame: %d ms", config.audio_frame_ms)

    if not (100 <= config.vad_min_silence_ms <= 10000):
        errors.append(
            f"VAD_MIN_SILENCE_MS={config.vad_min_silence_ms} out of range (must be 100-10000 ms)"
        )
    else:
        logger.info("  ✓ VAD min silence: %d ms", config.vad_min_silence_ms)

    if config.wake_word_enabled and not (1000 <= config.wake_word_timeout_ms <= 600000):
        errors.append(
            f"WAKE_WORD_TIMEOUT_MS={config.wake_word_timeout_ms} out of range (must be 1000-600000 ms)"
        )
    elif config.wake_word_enabled:
        logger.info("  ✓ Wake word timeout: %d ms", config.wake_word_timeout_ms)

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

    if not (0.1 <= config.tts_relative_gain <= 2.0):
        errors.append(
            f"TTS_RELATIVE_GAIN={config.tts_relative_gain} out of range (must be 0.1-2.0)"
        )
    else:
        logger.info("  ✓ TTS relative gain: %.2fx", config.tts_relative_gain)

    if not (0.5 <= config.piper_speed <= 5.0):
        warnings_list.append(
            f"PIPER_SPEED={config.piper_speed} unusual (typical: 0.5-5.0)"
        )
    else:
        logger.info("  ✓ TTS speed: %.2fx", config.piper_speed)

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
