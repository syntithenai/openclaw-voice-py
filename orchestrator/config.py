from pydantic import Field, ConfigDict, AliasChoices
from pydantic_settings import BaseSettings
from pathlib import Path
from dotenv import load_dotenv

# Load .env file, overriding any existing environment variables
load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"), override=True)


class VoiceConfig(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Audio
    audio_sample_rate: int = Field(16000)
    audio_frame_ms: int = Field(20)
    audio_capture_device: str = Field("default")
    audio_playback_device: str = Field("default")
    audio_backend: str = Field("portaudio")
    audio_input_gain: float = Field(1.0)  # Software gain multiplier for input audio

    # VAD
    vad_type: str = Field("webrtc")
    vad_confidence: float = Field(0.5)
    vad_min_speech_ms: int = Field(50)
    vad_min_silence_ms: int = Field(800)
    vad_min_rms: float = Field(0.002)
    vad_cut_in_rms: float = Field(0.0025)
    vad_cut_in_min_ms: int = Field(150)
    vad_cut_in_frames: int = Field(3)
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

    # Wake word
    wake_word_enabled: bool = Field(False)
    wake_word_engine: str = Field("openwakeword")
    wake_word_timeout_ms: int = Field(120000)
    wake_word_confidence: float = Field(0.5)
    openwakeword_model_path: str = Field("")

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
