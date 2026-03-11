from pydantic import Field, ConfigDict, AliasChoices, field_validator, model_validator
from pydantic_settings import BaseSettings
from pathlib import Path
from dotenv import load_dotenv
import logging
import os


def _detect_env_file() -> Path:
    """Select the most appropriate env file for this runtime.

    Priority:
      1) OPENCLAW_ENV_FILE (explicit override)
      2) .env.docker when running in container
      3) .env.pi on ARM boards
      4) .env (default)
    """
    root = Path(__file__).resolve().parent.parent

    explicit = os.environ.get("OPENCLAW_ENV_FILE", "").strip()
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if not explicit_path.is_absolute():
            explicit_path = (root / explicit_path).resolve()
        return explicit_path

    in_docker = Path("/.dockerenv").exists() or os.environ.get("OPENCLAW_IN_DOCKER", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if in_docker and (root / ".env.docker").exists():
        return root / ".env.docker"

    arch = (os.uname().machine or "").lower()
    if arch.startswith("arm") and (root / ".env.pi").exists():
        return root / ".env.pi"

    return root / ".env"


_ROOT_DIR = Path(__file__).resolve().parent.parent
_BASE_ENV_FILE = _ROOT_DIR / ".env"
_SELECTED_ENV_FILE = _detect_env_file()

# Load base .env first (if present), then selected env as override.
if _BASE_ENV_FILE.exists():
    load_dotenv(str(_BASE_ENV_FILE), override=False)
if _SELECTED_ENV_FILE.exists():
    load_dotenv(str(_SELECTED_ENV_FILE), override=True)


class VoiceConfig(BaseSettings):
    model_config = ConfigDict(case_sensitive=False, extra="ignore")

    @field_validator("audio_capture_device", "audio_playback_device", mode="before")
    @classmethod
    def _normalize_audio_device(cls, value):
        if value is None:
            return "default"
        s = str(value).strip()
        if not s:
            return "default"
        if s.lower() == "default":
            return "default"
        if s.isdigit():
            return int(s)
        return s

    # Audio
    audio_sample_rate: int = Field(16000)
    audio_playback_sample_rate: int = Field(0)  # 0 = use audio_sample_rate; set to 48000 for USB devices
    audio_playback_lead_in_ms: int = Field(0)  # 0 = auto; set higher (e.g. 400-800) for devices clipping sentence starts
    audio_playback_keepalive_enabled: bool = Field(False)  # Keep output stream primed during idle (Pi-only)
    audio_playback_keepalive_interval_ms: int = Field(250)  # Idle gap before sending a silence keepalive frame
    audio_frame_ms: int = Field(20)
    audio_capture_device: int | str = Field("default")
    audio_playback_device: int | str = Field("default")
    audio_backend: str = Field("portaudio")
    audio_input_gain: float = Field(1.0)  # Software gain multiplier for input audio
    audio_output_gain: float = Field(1.0)  # Software gain multiplier for TTS/output audio

    # VAD
    vad_type: str = Field("webrtc")
    vad_confidence: float = Field(0.5)
    vad_min_speech_ms: int = Field(50)
    vad_min_silence_ms: int = Field(800)
    vad_min_rms: float = Field(0.002)
    vad_cut_in_rms: float = Field(0.0025)
    vad_cut_in_min_ms: int = Field(150)
    vad_cut_in_frames: int = Field(3)
    vad_cut_in_tts_hold_timeout_ms: int = Field(4500)  # Suppress further TTS after cut-in until transcript or timeout
    vad_cut_in_use_silero: bool = Field(False)
    vad_cut_in_silero_confidence: float = Field(0.3)
    silero_model_path: str = Field("")
    silero_auto_download: bool = Field(True)
    silero_model_url: str = Field("https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx")
    silero_model_cache_dir: str = Field("docker/silero-models")

    # Wakeword models
    openwakeword_models_dir: str = Field("docker/wakeword-models")
    openwakeword_auto_download: bool = Field(True)

    # Emotion models
    emotion_models_dir: str = Field("docker/emotion-models")
    emotion_auto_download: bool = Field(True)

    # AEC
    echo_cancel: bool = Field(True)
    echo_cancel_strength: str = Field("strong")

    # Wake word - Global settings
    wake_word_enabled: bool = Field(False)
    wake_word_timeout_ms: int = Field(120000)
    wake_sleep_cooldown_ms: int = Field(2500)  # Ignore wake detections briefly after going to sleep
    wake_min_detect_rms: float = Field(0.0015)  # Reject wake detections on near-silence frames
    wake_clear_ring_buffer: bool = Field(False)  # Clear ring buffer on wake to avoid ghost transcripts (ARM/Pi only)

    # Wake word - Precise Engine (Mycroft Precise v0.3.0)
    precise_enabled: bool = Field(False)
    precise_wake_word: str = Field("")  # Descriptive name of what wake word is in the model
    precise_model_path: str = Field("")  # Path to .pb file
    precise_confidence: float = Field(0.15)  # Detection threshold

    # Wake word - OpenWakeWord Engine (TFLite-based)
    openwakeword_enabled: bool = Field(False)
    openwakeword_wake_word: str = Field("")  # Model name (e.g., "hey_mycroft")
    openwakeword_model_path: str = Field("")  # Model name or path to .tflite file
    openwakeword_confidence: float = Field(0.5)  # Detection threshold
    openwakeword_models_dir: str = Field("docker/wakeword-models")
    openwakeword_auto_download: bool = Field(True)

    # Wake word - Picovoice Engine (Proprietary)
    picovoice_enabled: bool = Field(False)
    picovoice_wake_word: str = Field("")  # Model name
    picovoice_key: str = Field("")  # API key
    picovoice_confidence: float = Field(0.5)  # Detection threshold

    # Chunking
    chunk_max_ms: int = Field(10000)
    pre_roll_ms: int = Field(2000)
    cut_in_pre_roll_ms: int = Field(100)

    # Services
    whisper_url: str = Field("http://10.1.1.249:10000")
    piper_url: str = Field("http://10.1.1.249:10001")
    piper_voice_id: str = Field("en_US-amy-medium")
    piper_speed: float = Field(1.0)
    gateway_ws_url: str = Field("", validation_alias=AliasChoices("GATEWAY_WS_URL"))
    gateway_http_url: str = Field("", validation_alias=AliasChoices("GATEWAY_HTTP_URL", "OPENCLAW_GATEWAY_URL"))
    gateway_http_endpoint: str = Field("/api/short", validation_alias=AliasChoices("GATEWAY_HTTP_ENDPOINT"))
    gateway_provider: str = Field("openclaw", validation_alias=AliasChoices("VOICE_CLAW_PROVIDER", "GATEWAY_PROVIDER"))
    gateway_agent_id: str = Field("", validation_alias=AliasChoices("GATEWAY_AGENT_ID", "OPENCLAW_AGENT_ID"))
    gateway_auth_token: str = Field("", validation_alias=AliasChoices("GATEWAY_AUTH_TOKEN", "OPENCLAW_GATEWAY_TOKEN"))
    openclaw_gateway_url: str = Field("", validation_alias=AliasChoices("OPENCLAW_GATEWAY_URL"))
    gateway_timeout_ms: int = Field(30000, validation_alias=AliasChoices("VOICE_GATEWAY_TIMEOUT", "GATEWAY_TIMEOUT_MS"))
    gateway_session_prefix: str = Field("voice", validation_alias=AliasChoices("VOICE_SESSION_PREFIX"))
    gateway_debounce_ms: int = Field(2000, validation_alias=AliasChoices("GATEWAY_DEBOUNCE_MS"))
    gateway_tts_fast_start_words: int = Field(5, validation_alias=AliasChoices("GATEWAY_TTS_FAST_START_WORDS"))

    # ZeroClaw
    zeroclaw_gateway_url: str = Field("http://localhost:3000", validation_alias=AliasChoices("ZEROCLAW_GATEWAY_URL"))
    zeroclaw_webhook_token: str = Field("", validation_alias=AliasChoices("ZEROCLAW_WEBHOOK_TOKEN"))
    zeroclaw_channel: str = Field("voice", validation_alias=AliasChoices("ZEROCLAW_CHANNEL"))

    # TinyClaw
    tinyclaw_home: str = Field("", validation_alias=AliasChoices("TINYCLAW_HOME"))
    tinyclaw_agent_id: str = Field("", validation_alias=AliasChoices("TINYCLAW_AGENT_ID"))

    # IronClaw
    ironclaw_gateway_url: str = Field("http://localhost:8888", validation_alias=AliasChoices("IRONCLAW_GATEWAY_URL"))
    ironclaw_gateway_token: str = Field("", validation_alias=AliasChoices("IRONCLAW_GATEWAY_TOKEN"))
    ironclaw_use_websocket: bool = Field(True, validation_alias=AliasChoices("IRONCLAW_USE_WEBSOCKET"))
    ironclaw_agent_id: str = Field("", validation_alias=AliasChoices("IRONCLAW_AGENT_ID"))

    # MimiClaw
    mimiclaw_device_host: str = Field("localhost", validation_alias=AliasChoices("MIMICLAW_DEVICE_HOST"))
    mimiclaw_device_port: int = Field(18789, validation_alias=AliasChoices("MIMICLAW_DEVICE_PORT"))
    mimiclaw_use_websocket: bool = Field(True, validation_alias=AliasChoices("MIMICLAW_USE_WEBSOCKET"))
    mimiclaw_telegram_bot_token: str = Field("", validation_alias=AliasChoices("MIMICLAW_TELEGRAM_BOT_TOKEN"))
    mimiclaw_telegram_chat_id: str = Field("", validation_alias=AliasChoices("MIMICLAW_TELEGRAM_CHAT_ID"))

    # PicoClaw
    picoclaw_home: str = Field("", validation_alias=AliasChoices("PICOCLAW_HOME"))
    picoclaw_gateway_url: str = Field("", validation_alias=AliasChoices("PICOCLAW_GATEWAY_URL"))
    picoclaw_agent_id: str = Field("", validation_alias=AliasChoices("PICOCLAW_AGENT_ID"))

    # NanoBot
    nanobot_home: str = Field("", validation_alias=AliasChoices("NANOBOT_HOME"))
    nanobot_gateway_url: str = Field("http://localhost:18790", validation_alias=AliasChoices("NANOBOT_GATEWAY_URL"))
    nanobot_agent_id: str = Field("", validation_alias=AliasChoices("NANOBOT_AGENT_ID"))

    # Emotion
    emotion_enabled: bool = Field(False)
    emotion_model: str = Field("sensevoice-small")
    emotion_timeout_ms: int = Field(300)
    sensevoice_model_path: str = Field("")

    # Quick Answer LLM
    quick_answer_enabled: bool = Field(False)
    quick_answer_llm_url: str = Field("")  # OpenAI-compatible endpoint (e.g., http://localhost:8080/v1/chat/completions)
    quick_answer_model: str = Field("")  # Model name to use (e.g., "gpt-3.5-turbo" or specific loaded model in LM Studio)
    quick_answer_api_key: str = Field("")  # Optional API key for authentication
    quick_answer_timeout_ms: int = Field(5000)  # Timeout for quick answer requests
    quick_answer_mirror_enabled: bool = Field(False)  # Mirror QA turns to the openclaw session so they appear in web chat
    quick_answer_bypass_window_ms: int = Field(8000)  # After a transcript is sent to gateway, bypass quick answer for this many ms (0=disabled)

    # Tool System
    tools_enabled: bool = Field(True)  # Enable timer/alarm tool system
    timers_enabled: bool = Field(True)  # Enable timer/alarm exposure and background monitoring
    tools_persist_dir: str = Field("timers")  # Directory for timer/alarm persistence (relative to workspace root)
    tools_debounce_ms: int = Field(75)  # Write debouncing window for alarm state updates
    tools_monitor_interval_ms: int = Field(100)  # How often to check for timer/alarm expiration

    # Music Control (MPD)
    music_enabled: bool = Field(False)  # Enable music control via MPD
    mpd_host: str = Field("localhost")  # MPD server host
    mpd_port: int = Field(6600)  # MPD server port
    mpd_timeout: float = Field(5.0)  # Connection timeout in seconds
    mpd_pool_size: int = Field(3)  # Number of connections in pool
    music_fast_path_enabled: bool = Field(True)  # Enable fast-path parsing for music commands
    music_sleep_during_playback: bool = Field(True)  # Put orchestrator to sleep while music is playing
    music_auto_resume_timeout_s: int = Field(5)  # Seconds of silence before auto-resuming music after wake
    music_random_track_count: int = Field(50)  # Number of random tracks to add when queue is empty

    # Media Keys (Hardware button detection)
    media_keys_enabled: bool = Field(False)  # Enable hardware media key detection
    media_keys_device_filter: str = Field("")  # Optional device name filter (e.g., "Anker", "USB", "Conference")
    media_keys_exclusive_grab: bool = Field(False)  # Grab input device exclusively (blocks OS media handling)
    media_keys_control_music: bool = Field(True)  # Allow media keys to control MPD playback
    media_keys_suppress_system_play: bool = Field(True)  # Pause desktop media players on wake/play-button events
    media_keys_play_scan_codes: str = Field("0xc00b6,0xc00cd")  # Comma-separated MSC_SCAN values that should be treated as play button
    media_keys_command_debounce_ms: int = Field(400)  # Ignore duplicate logical button commands within this window

    # Wake/sleep feedback sounds
    wake_feedback_variant: str = Field("click")  # click|double|bright|soft|cluck|doublecluck|knock|knocklow|doubleknock
    sleep_feedback_variant: str = Field("swoosh")  # swoosh|short|deep|sigh|sighshort|exhale|exhaleshort|exhalelong|none
    wake_feedback_gain: float = Field(1.6)  # Playback gain multiplier for wake cue
    sleep_feedback_gain: float = Field(1.3)  # Playback gain multiplier for sleep cue

    @model_validator(mode='after')
    def validate_critical_config(self):
        """Validate that configuration is sensible and log errors for bad settings."""
        logger = logging.getLogger("orchestrator.config")
        errors = []

        # Auto-select a wake-word engine if wake word is enabled and none were explicitly enabled.
        if self.wake_word_enabled:
            enabled_engines = sum([
                self.precise_enabled,
                self.openwakeword_enabled,
                self.picovoice_enabled,
            ])
            if enabled_engines == 0:
                arch = (os.uname().machine or "").lower()
                if arch.startswith("arm"):
                    self.precise_enabled = True
                    if not self.precise_model_path:
                        self.precise_model_path = "docker/wakeword-models/hey-mycroft.pb"
                    if not self.precise_wake_word:
                        self.precise_wake_word = "hey-mycroft"
                    logger.info(
                        "Auto-selected wake-word engine: Precise (ARM detected; no explicit engine configured)"
                    )
                else:
                    self.openwakeword_enabled = True
                    if not self.openwakeword_model_path:
                        self.openwakeword_model_path = "hey_mycroft"
                    if not self.openwakeword_wake_word:
                        self.openwakeword_wake_word = "hey_mycroft"
                    logger.info(
                        "Auto-selected wake-word engine: OpenWakeWord (non-ARM detected; no explicit engine configured)"
                    )

        # Validate wake word configuration
        if self.wake_word_enabled:
            # Check that exactly one engine is enabled
            enabled_engines = sum([
                self.precise_enabled,
                self.openwakeword_enabled,
                self.picovoice_enabled
            ])
            
            if enabled_engines == 0:
                errors.append("WAKE_WORD_ENABLED=true but no engine enabled (set one of: PRECISE_ENABLED, OPENWAKEWORD_ENABLED, PICOVOICE_ENABLED)")
            elif enabled_engines > 1:
                errors.append("Multiple wake word engines enabled - set only one of: PRECISE_ENABLED, OPENWAKEWORD_ENABLED, PICOVOICE_ENABLED")
            
            # Validate Precise engine
            if self.precise_enabled:
                if not self.precise_model_path:
                    errors.append("PRECISE_ENABLED=true but PRECISE_MODEL_PATH is empty")
                if not (0.0 <= self.precise_confidence <= 1.0):
                    errors.append(f"PRECISE_CONFIDENCE={self.precise_confidence} must be between 0.0 and 1.0")
            
            # Validate OpenWakeWord engine
            if self.openwakeword_enabled:
                if not self.openwakeword_model_path:
                    errors.append("OPENWAKEWORD_ENABLED=true but OPENWAKEWORD_MODEL_PATH is empty")
                if not (0.0 <= self.openwakeword_confidence <= 1.0):
                    errors.append(f"OPENWAKEWORD_CONFIDENCE={self.openwakeword_confidence} must be between 0.0 and 1.0")
            
            # Validate Picovoice engine
            if self.picovoice_enabled:
                if not self.picovoice_key:
                    errors.append("PICOVOICE_ENABLED=true but PICOVOICE_KEY is empty")
                if not (0.0 <= self.picovoice_confidence <= 1.0):
                    errors.append(f"PICOVOICE_CONFIDENCE={self.picovoice_confidence} must be between 0.0 and 1.0")


        # Validate audio settings
        if self.audio_sample_rate <= 0:
            errors.append(f"Invalid AUDIO_SAMPLE_RATE={self.audio_sample_rate} (must be > 0)")
        if self.audio_playback_keepalive_interval_ms <= 0:
            errors.append(
                f"AUDIO_PLAYBACK_KEEPALIVE_INTERVAL_MS={self.audio_playback_keepalive_interval_ms} must be > 0"
            )
        if self.audio_frame_ms <= 0:
            errors.append(f"Invalid AUDIO_FRAME_MS={self.audio_frame_ms} (must be > 0)")
        if self.audio_input_gain < 0.1 or self.audio_input_gain > 10.0:
            errors.append(f"AUDIO_INPUT_GAIN={self.audio_input_gain} is unusual (typical range: 0.1-10.0)")
        if self.audio_output_gain < 0.1 or self.audio_output_gain > 5.0:
            errors.append(f"AUDIO_OUTPUT_GAIN={self.audio_output_gain} is unusual (typical range: 0.1-5.0)")

        if self.media_keys_command_debounce_ms < 0:
            errors.append(
                f"MEDIA_KEYS_COMMAND_DEBOUNCE_MS={self.media_keys_command_debounce_ms} must be >= 0"
            )

        # Validate VAD settings
        if not (0.0 <= self.vad_confidence <= 1.0):
            errors.append(f"VAD_CONFIDENCE={self.vad_confidence} must be between 0.0 and 1.0")
        if self.vad_min_speech_ms < 0:
            errors.append(f"VAD_MIN_SPEECH_MS={self.vad_min_speech_ms} must be >= 0")
        if self.vad_min_silence_ms < 0:
            errors.append(f"VAD_MIN_SILENCE_MS={self.vad_min_silence_ms} must be >= 0")

        # Validate wake sleep/detection guardrails
        if self.wake_sleep_cooldown_ms < 0:
            errors.append(f"WAKE_SLEEP_COOLDOWN_MS={self.wake_sleep_cooldown_ms} must be >= 0")
        if not (0.0 <= self.wake_min_detect_rms <= 1.0):
            errors.append(f"WAKE_MIN_DETECT_RMS={self.wake_min_detect_rms} must be between 0.0 and 1.0")

        # Service URL validation
        # Validate service URLs (if they contain text, they should look like URLs)

        if self.whisper_url and not (self.whisper_url.startswith("http://") or self.whisper_url.startswith("https://")) and ":" not in self.whisper_url:
            errors.append(f"Invalid WHISPER_URL format: {self.whisper_url}")
        if self.piper_url and not (self.piper_url.startswith("http://") or self.piper_url.startswith("https://")) and ":" not in self.piper_url:
            errors.append(f"Invalid PIPER_URL format: {self.piper_url}")

        # Validate TTS speed
        if self.piper_speed < 0.5 or self.piper_speed > 5.0:
            errors.append(f"PIPER_SPEED={self.piper_speed} is unusual (typical range: 0.5-5.0)")

        # Validate quick answer configuration
        if self.quick_answer_enabled:
            if not self.quick_answer_llm_url:
                errors.append("QUICK_ANSWER_ENABLED=true but QUICK_ANSWER_LLM_URL is empty")
            elif not (self.quick_answer_llm_url.startswith("http://") or self.quick_answer_llm_url.startswith("https://")):
                errors.append(f"Invalid QUICK_ANSWER_LLM_URL format: {self.quick_answer_llm_url}")
            if not self.quick_answer_model:
                logger.warning("QUICK_ANSWER_MODEL not set - will default to 'gpt-3.5-turbo' (may not match LM Studio loaded model)")
            if self.quick_answer_timeout_ms <= 0:
                errors.append(f"QUICK_ANSWER_TIMEOUT_MS={self.quick_answer_timeout_ms} must be > 0")

        # Log and exit if critical errors found
        if errors:
            logger.error("=" * 70)
            logger.error("CONFIGURATION VALIDATION FAILED - Cannot start orchestrator")
            logger.error("=" * 70)
            for error in errors:
                logger.error("  ❌ %s", error)
            logger.error("=" * 70)
            logger.error("Please fix the errors in your .env file and try again.")
            raise ValueError(f"Configuration validation failed with {len(errors)} error(s). See logs above.")

        return self
