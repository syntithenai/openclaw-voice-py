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

# Load exactly one env profile file, and never override explicit process env.
#
# Precedence should be:
#   1) Explicit process env (e.g., docker compose service environment)
#   2) Selected env profile file (.env.docker / .env.pi / .env)
#
# Loading base .env in addition to a selected profile can leak host-specific
# values (e.g., local device indices) into container/PI runtimes.
_env_file_to_load = _SELECTED_ENV_FILE if _SELECTED_ENV_FILE.exists() else _BASE_ENV_FILE
if _env_file_to_load.exists():
    load_dotenv(str(_env_file_to_load), override=False)


class VoiceConfig(BaseSettings):
    model_config = ConfigDict(case_sensitive=False, extra="ignore")

    openclaw_workspace_dir: str = Field(
        str(_ROOT_DIR),
        validation_alias=AliasChoices("OPENCLAW_WORKSPACE_DIR", "OPENCLAW_WORKSPACE"),
    )

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
    tts_relative_gain: float = Field(0.75)  # Additional trim for TTS only (relative to music/background)

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
    wake_word_timeout_ms: int = Field(4000)
    wake_sleep_cooldown_ms: int = Field(2500)  # Ignore wake detections briefly after going to sleep
    wake_min_detect_rms: float = Field(0.0015)  # Reject wake detections on near-silence frames
    wake_clear_ring_buffer: bool = Field(False)  # Clear ring buffer on wake to avoid ghost transcripts (ARM/Pi only)
    wake_detect_prebuffer_ms: int = Field(280)  # One-shot prebuffer fed into wake detector after music cut-in duck starts

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

    # STT ghost transcript suppression gate
    ghost_filter_enabled: bool = Field(True)
    ghost_filter_single_word_enabled: bool = Field(True)
    ghost_filter_require_question_for_acks: bool = Field(True)
    ghost_filter_playback_tail_ms: int = Field(1200)
    ghost_filter_cutin_early_ms: int = Field(500)
    ghost_filter_recent_assistant_ms: int = Field(12000)
    ghost_filter_upstream_context_ms: int = Field(20000)
    ghost_filter_self_echo_similarity_threshold: float = Field(0.75)
    ghost_filter_debug_logging: bool = Field(True)
    ghost_filter_kill_switch: bool = Field(False)

    # Recorder Tool
    recorder_enabled: bool = Field(False)  # Enable quick-answer recorder tool
    recorder_output_dir: str = Field("recordings")  # Relative to OPENCLAW_WORKSPACE_DIR
    recorder_pyannote_enabled: bool = Field(False)  # Enable pyannote diarization during recorder stop
    recorder_pyannote_auth_token: str = Field("")  # Hugging Face token for pyannote pipeline
    recorder_pyannote_model: str = Field("pyannote/speaker-diarization-3.1")
    recorder_pyannote_url: str = Field("http://localhost:10002")  # Remote pyannote diarization service URL

    # Embedded realtime web UI / websocket bridge
    web_ui_enabled: bool = Field(False)  # Serve local UI + websocket bridge for continuous browser audio
    web_ui_host: str = Field("0.0.0.0")  # Bind address for embedded web service
    web_ui_port: int = Field(18910)  # HTTP UI port
    web_ui_ws_port: int = Field(18911)  # WebSocket bridge port
    web_ui_status_hz: int = Field(12)  # Status broadcast frequency to connected clients
    web_ui_hotword_active_ms: int = Field(2000)  # How long to keep hotword indicator active in UI after detection
    web_ui_chat_history_limit: int = Field(200)  # Max chat messages retained in web UI memory
    web_ui_chat_persist_path: str = Field("")  # Path to JSON file for durable chat thread storage; empty = ~/.openclaw/chat_state.json
    web_ui_music_poll_ms: int = Field(1000)  # How often to poll MPD for music state (ms)
    web_ui_timer_poll_ms: int = Field(500)  # How often to push timer state to UI (ms)
    web_ui_mic_starts_disabled: bool = Field(True)  # Mic button starts in disabled (red) state
    web_ui_audio_authority: str = Field("native")  # native=OS mic only; browser=browser audio while connected (fallback local when disconnected); hybrid=same handoff semantics with explicit shared wake-state intent
    web_ui_ssl_certfile: str = Field("")  # Path to TLS certificate file (PEM); empty = plain HTTP
    web_ui_ssl_keyfile: str = Field("")   # Path to TLS private key file (PEM)
    web_ui_http_redirect_port: int = Field(0)  # Port for HTTP→HTTPS redirector (0 = disabled)

    # Tool System
    tools_enabled: bool = Field(True)  # Enable timer/alarm tool system
    timers_enabled: bool = Field(True)  # Enable timer/alarm exposure and background monitoring
    tools_persist_dir: str = Field("timers")  # Directory for timer/alarm persistence (relative to workspace root)
    tools_debounce_ms: int = Field(75)  # Write debouncing window for alarm state updates
    tools_monitor_interval_ms: int = Field(100)  # How often to check for timer/alarm expiration
    tools_clear_on_startup: bool = Field(True)  # Clear persisted timers/alarms at process start

    # Music Control (MPD)
    music_enabled: bool = Field(False)  # Enable music control via MPD
    mpd_host: str = Field("localhost")  # MPD server host
    mpd_port: int = Field(6600)  # MPD server port
    mpd_timeout: float = Field(5.0)  # Connection timeout in seconds
    mpd_pool_size: int = Field(3)  # Number of connections in pool
    mpd_playlist_dir: str = Field("playlists")  # Relative to OPENCLAW_WORKSPACE_DIR
    mpd_state_dir: str = Field(".mpd")  # Relative to OPENCLAW_WORKSPACE_DIR
    music_fast_path_enabled: bool = Field(True)  # Enable fast-path parsing for music commands
    music_sleep_during_playback: bool = Field(True)  # Put orchestrator to sleep while music is playing
    music_auto_resume_timeout_s: int = Field(5)  # Seconds of silence before auto-resuming music after wake
    music_random_track_count: int = Field(50)  # Number of random tracks to add when queue is empty
    music_genre_queue_limit: int = Field(120)  # Max genre tracks enqueued per command to avoid long startup stalls
    music_tts_duck_enabled: bool = Field(True)  # Reduce music volume while TTS is speaking
    music_tts_duck_ratio: float = Field(0.45)  # Keep this fraction of current music volume during TTS
    music_cut_in_duck_ratio: float = Field(0.50)  # Keep this fraction of current music volume during voice cut-in
    music_cut_in_duck_timeout_ms: int = Field(2000)  # Restore cut-in ducking after this timeout if not paused
    music_pipewire_stream_normalize_enabled: bool = Field(True)  # Normalize PipeWire per-app stream volume for MPD on play/resume
    music_pipewire_stream_target_percent: int = Field(100)  # Target PipeWire sink-input volume for MPD stream (percent)

    # Media Keys (Hardware button detection)
    media_keys_enabled: bool = Field(False)  # Enable hardware media key detection
    media_keys_device_filter: str = Field("")  # Optional device name filter (e.g., "Anker", "USB", "Conference")
    media_keys_exclusive_grab: bool = Field(False)  # Grab input device exclusively (usually leave false so OS volume/LED behavior works)
    media_keys_passthrough_keys: str = Field("volume_up,volume_down,mute")  # Comma-separated logical keys to re-inject to OS when exclusive grab is enabled
    media_keys_control_music: bool = Field(False)  # Allow media keys to control MPD playback
    media_keys_suppress_system_play: bool = Field(True)  # Pause desktop media players on wake/play-button events
    media_keys_play_scan_codes: str = Field("0xc00b6,0xc00cd")  # Comma-separated MSC_SCAN values that should be treated as play button
    media_keys_volume_up_scan_codes: str = Field("0xc00e9")  # Optional MSC_SCAN values to map to volume-up button
    media_keys_volume_down_scan_codes: str = Field("0xc00ea")  # Optional MSC_SCAN values to map to volume-down button
    media_keys_mute_scan_codes: str = Field("")  # Optional MSC_SCAN values to map to mute button
    media_keys_phone_scan_codes: str = Field("")  # Optional MSC_SCAN values to map to phone button
    media_keys_command_debounce_ms: int = Field(400)  # Ignore duplicate logical button commands within this window
    media_keys_sync_alsa_mic_switch: bool = Field(True)  # Also toggle ALSA capture switch on mute/unmute (may sync device LED)
    media_keys_alsa_mic_control: str = Field("Mic")  # ALSA control name to toggle via amixer (e.g. Mic/Capture)
    media_keys_alsa_card: str = Field("")  # Optional explicit ALSA card index/name for amixer; auto-detected from device name when empty

    # Wake/sleep feedback sounds
    wake_feedback_variant: str = Field("click")  # click|double|bright|soft|cluck|doublecluck|knock|knocklow|doubleknock
    sleep_feedback_variant: str = Field("swoosh")  # swoosh|short|deep|sigh|sighshort|exhale|exhaleshort|exhalelong|none
    wake_feedback_gain: float = Field(1.6)  # Playback gain multiplier for wake cue
    sleep_feedback_gain: float = Field(1.3)  # Playback gain multiplier for sleep cue
    volume_feedback_gain: float = Field(0.4)  # Playback gain for volume-step click sounds

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
                is_pi = False
                for model_path in (
                    "/proc/device-tree/model",
                    "/sys/firmware/devicetree/base/model",
                ):
                    try:
                        model_text = Path(model_path).read_text(errors="ignore").strip().lower()
                    except Exception:
                        continue
                    if "raspberry pi" in model_text:
                        is_pi = True
                        break

                if is_pi and (arch.startswith("armv7") or arch.startswith("armv6")):
                    self.precise_enabled = True
                    if not self.precise_model_path:
                        self.precise_model_path = "docker/wakeword-models/hey-mycroft.pb"
                    if not self.precise_wake_word:
                        self.precise_wake_word = "hey-mycroft"
                    logger.info(
                        "Auto-selected wake-word engine: Precise (Raspberry Pi ARMv7/ARMv6 detected; no explicit engine configured)"
                    )
                else:
                    self.openwakeword_enabled = True
                    if not self.openwakeword_model_path:
                        self.openwakeword_model_path = "hey_mycroft"
                    if not self.openwakeword_wake_word:
                        self.openwakeword_wake_word = "hey_mycroft"
                    logger.info(
                        "Auto-selected wake-word engine: OpenWakeWord (default; non-Raspberry-Pi-armv7/armv6 or non-ARM detected)"
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
        if self.tts_relative_gain < 0.1 or self.tts_relative_gain > 2.0:
            errors.append(f"TTS_RELATIVE_GAIN={self.tts_relative_gain} is unusual (typical range: 0.1-2.0)")

        if self.media_keys_command_debounce_ms < 0:
            errors.append(
                f"MEDIA_KEYS_COMMAND_DEBOUNCE_MS={self.media_keys_command_debounce_ms} must be >= 0"
            )

        if not (0.05 <= self.music_tts_duck_ratio <= 1.0):
            errors.append(f"MUSIC_TTS_DUCK_RATIO={self.music_tts_duck_ratio} must be between 0.05 and 1.0")

        if not (0.05 <= self.music_cut_in_duck_ratio <= 1.0):
            errors.append(f"MUSIC_CUT_IN_DUCK_RATIO={self.music_cut_in_duck_ratio} must be between 0.05 and 1.0")

        if self.music_cut_in_duck_timeout_ms < 0:
            errors.append(
                f"MUSIC_CUT_IN_DUCK_TIMEOUT_MS={self.music_cut_in_duck_timeout_ms} must be >= 0"
            )

        if not (1 <= self.music_pipewire_stream_target_percent <= 150):
            errors.append(
                f"MUSIC_PIPEWIRE_STREAM_TARGET_PERCENT={self.music_pipewire_stream_target_percent} must be between 1 and 150"
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

        # Validate ghost transcript filter settings
        if self.ghost_filter_playback_tail_ms < 0:
            errors.append(
                f"GHOST_FILTER_PLAYBACK_TAIL_MS={self.ghost_filter_playback_tail_ms} must be >= 0"
            )
        if self.ghost_filter_cutin_early_ms < 0:
            errors.append(
                f"GHOST_FILTER_CUTIN_EARLY_MS={self.ghost_filter_cutin_early_ms} must be >= 0"
            )
        if self.ghost_filter_recent_assistant_ms < 0:
            errors.append(
                f"GHOST_FILTER_RECENT_ASSISTANT_MS={self.ghost_filter_recent_assistant_ms} must be >= 0"
            )
        if self.ghost_filter_upstream_context_ms < 0:
            errors.append(
                f"GHOST_FILTER_UPSTREAM_CONTEXT_MS={self.ghost_filter_upstream_context_ms} must be >= 0"
            )
        if not (0.0 <= self.ghost_filter_self_echo_similarity_threshold <= 1.0):
            errors.append(
                "GHOST_FILTER_SELF_ECHO_SIMILARITY_THRESHOLD must be between 0.0 and 1.0"
            )

        # Validate embedded web UI configuration
        if self.web_ui_enabled:
            if not self.web_ui_host:
                errors.append("WEB_UI_ENABLED=true but WEB_UI_HOST is empty")
            if self.web_ui_port <= 0 or self.web_ui_port > 65535:
                errors.append(f"WEB_UI_PORT={self.web_ui_port} must be between 1 and 65535")
            if self.web_ui_ws_port <= 0 or self.web_ui_ws_port > 65535:
                errors.append(f"WEB_UI_WS_PORT={self.web_ui_ws_port} must be between 1 and 65535")
            if self.web_ui_ws_port == self.web_ui_port:
                errors.append("WEB_UI_WS_PORT must differ from WEB_UI_PORT")
            if self.web_ui_status_hz <= 0 or self.web_ui_status_hz > 120:
                errors.append(f"WEB_UI_STATUS_HZ={self.web_ui_status_hz} must be between 1 and 120")
            if self.web_ui_hotword_active_ms < 100 or self.web_ui_hotword_active_ms > 60000:
                errors.append(
                    f"WEB_UI_HOTWORD_ACTIVE_MS={self.web_ui_hotword_active_ms} must be between 100 and 60000"
                )
            if self.web_ui_audio_authority not in ("native", "browser", "hybrid"):
                errors.append(f"WEB_UI_AUDIO_AUTHORITY must be 'native', 'browser', or 'hybrid'; got '{self.web_ui_audio_authority}'")
            if bool(self.web_ui_ssl_certfile) != bool(self.web_ui_ssl_keyfile):
                errors.append("WEB_UI_SSL_CERTFILE and WEB_UI_SSL_KEYFILE must both be set or both be empty")
            if self.web_ui_http_redirect_port != 0:
                if not (1 <= self.web_ui_http_redirect_port <= 65535):
                    errors.append(f"WEB_UI_HTTP_REDIRECT_PORT={self.web_ui_http_redirect_port} must be 1–65535 or 0 to disable")
                elif self.web_ui_http_redirect_port in (self.web_ui_port, self.web_ui_ws_port):
                    errors.append("WEB_UI_HTTP_REDIRECT_PORT must differ from WEB_UI_PORT and WEB_UI_WS_PORT")
                elif not self.web_ui_ssl_certfile:
                    errors.append("WEB_UI_HTTP_REDIRECT_PORT requires WEB_UI_SSL_CERTFILE to be configured")
            if self.web_ui_music_poll_ms < 100:
                errors.append(f"WEB_UI_MUSIC_POLL_MS={self.web_ui_music_poll_ms} must be >= 100 ms")
            if self.web_ui_timer_poll_ms < 100:
                errors.append(f"WEB_UI_TIMER_POLL_MS={self.web_ui_timer_poll_ms} must be >= 100 ms")

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
