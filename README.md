# OpenClaw Voice Orchestrator

**Production-ready voice interface for AI assistants with advanced audio processing, wake word detection, and flexible deployment options.**

---

## ✨ Features

### 🎙️ Advanced Audio Processing
- **Echo Cancellation (WebRTC AEC)** - Eliminates audio feedback during full-duplex conversations
- **Voice Activity Detection (VAD)** - Dual-engine support (WebRTC VAD, Silero VAD) with configurable sensitivity
- **Continuous Capture with Pre-roll** - Never miss the start of speech (configurable 100-2000ms pre-buffer)
- **Cut-in/Interruption Support** - Users can interrupt assistant responses mid-speech with RMS and Silero-based detection
- **Smart Debouncing** - Aggregates rapid follow-up questions (configurable 2000ms window)
- **Duplicate Response Filtering** - Strips already-spoken content from streaming TTS updates

### 🔊 Wake Word Detection
Three hotword engine options with multiple models:
- **OpenWakeWord** - No API keys, multiple built-in models (`hey_mycroft`, `hey_jarvis`, `alexa`)
- **Mycroft Precise** ⭐ *Recommended for Raspberry Pi* - Optimized for ARM, low latency
- **Picovoice Porcupine** - High accuracy, 20+ keywords, free tier available

Configurable confidence thresholds and automatic cooldown to prevent false positives.

### 🗣️ Speech Services
- **Speech-to-Text** - Whisper integration (faster-whisper) via HTTP API
- **Text-to-Speech** - Piper neural TTS with voice selection and speed control
- **Optional Emotion Detection** - SenseVoice integration for emotion tagging

### 🌐 Gateway Support
Universal gateway adapter supporting:
- **OpenClaw** - Primary OpenClaw gateway with WebSocket streaming
- **Generic/HTTP** - Universal HTTP/WebSocket gateway for custom implementations
- **ZeroClaw, TinyClaw, IronClaw, MimiClaw, PicoClaw, NanoBot** - Extended gateway ecosystem

### 🚀 Flexible Deployment
- **Docker Compose** - Profiles for containerized deployment (`stt`, `tts`, `orchestrator`)
- **Native Installation** - Direct host installation for minimal latency
- **Hybrid Mode** - Remote STT/TTS with native orchestrator (ideal for Raspberry Pi)
- **Platform Support** - Linux (ALSA/PulseAudio/PipeWire), Raspberry Pi (3/4/Zero 2W/5), Ubuntu, macOS, Windows

---

## 🚀 Quickstart

### Option 1: Docker Deployment (Recommended for Desktop)

**All-in-one deployment with STT, TTS, and orchestrator in containers:**

```bash
# Clone repository
git clone https://github.com/yourusername/openclaw-voice.git
cd openclaw-voice

# Copy and configure environment
cp .env.example .env
nano .env  # Edit GATEWAY_HTTP_URL, GATEWAY_AUTH_TOKEN, etc.

# Start all services (Linux with ALSA audio)
docker-compose --profile stt --profile tts --profile linux-audio up -d

# Or for PulseAudio/PipeWire (desktop Linux)
docker-compose --profile stt --profile tts --profile linux-pulse up -d

# View logs
docker-compose logs -f orchestrator
```

**Available Docker profiles:**
- `stt` - Whisper speech-to-text service (port 10000)
- `tts` - Piper text-to-speech service (port 10001)
- `orchestrator` - Main orchestrator (cross-platform base)
- `linux-audio` - Linux ALSA hardware passthrough (`/dev/snd`)
- `linux-pulse` - Linux PulseAudio/PipeWire socket passthrough

### Option 2: Native Installation (Raspberry Pi / Low Latency)

**For Raspberry Pi or direct hardware access:**

```bash
# Quick install (Raspbian/Ubuntu)
curl -sSL https://raw.githubusercontent.com/yourusername/openclaw-voice/main/install_raspbian.sh | bash

# Or manual installation
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-optional.txt  # For wake word and emotion detection

# Configure
cp .env.example .env
nano .env

# Run orchestrator (connects to remote or local STT/TTS services)
python -m orchestrator.main
```

**Raspbian quick reference:** See [RASPBIAN_INSTALL.md](RASPBIAN_INSTALL.md) for detailed Raspberry Pi setup.

### Pairing with OpenClaw Gateway

**1. Obtain gateway credentials:**
```bash
# From your OpenClaw gateway instance
openclaw device create --name "voice-assistant" --type voice
# Returns: GATEWAY_HTTP_URL, GATEWAY_AUTH_TOKEN, GATEWAY_AGENT_ID
```

**2. Configure `.env`:**
```bash
# OpenClaw Gateway
GATEWAY_PROVIDER=openclaw
GATEWAY_HTTP_URL=https://your-gateway.example.com
GATEWAY_AUTH_TOKEN=your_device_token_here
GATEWAY_AGENT_ID=assistant

# STT/TTS Services (adjust based on deployment)
WHISPER_URL=http://localhost:10000    # or remote IP for hybrid setup
PIPER_URL=http://localhost:10001      # or remote IP for hybrid setup
```

**3. Start and verify:**
```bash
# Docker
docker-compose --profile stt --profile tts --profile linux-audio up -d
docker-compose logs -f orchestrator

# Native
python -m orchestrator.main
```

**4. Test the connection:**
Speak into microphone or trigger wake word → transcription → gateway response → TTS playback.

Check logs for:
```
✓ Gateway connected (openclaw)
✓ Whisper client ready
✓ Piper client ready  
🎙️ Listening... (say 'Hey Mycroft' to wake)
```

---

## 📋 Deployment Scenarios

### Scenario 1: All-in-Docker (Desktop/Server)

**Best for:** Development, testing, desktop environments with sufficient resources.

```bash
# All services containerized with shared model volumes
docker-compose --profile stt --profile tts --profile linux-pulse up -d
```

**Characteristics:**
- STT (Whisper), TTS (Piper), Orchestrator all in Docker
- Shared model cache via volume mounts
- Audio via PulseAudio/PipeWire socket or ALSA device passthrough
- Easy updates: `docker-compose build && docker-compose up -d`

**Resource requirements:** 2GB+ RAM, 4+ CPU cores recommended for Whisper.

### Scenario 2: Hybrid - Remote STT/TTS, Native Orchestrator (Raspberry Pi)

**Best for:** Raspberry Pi, embedded devices, minimal latency setups.

```bash
# Run Whisper + Piper on a server/desktop
# Server (192.168.1.100):
docker-compose --profile stt --profile tts up -d

# Raspberry Pi (native orchestrator):
source .venv/bin/activate
export WHISPER_URL=http://192.168.1.100:10000
export PIPER_URL=http://192.168.1.100:10001
python -m orchestrator.main
```

**Characteristics:**
- STT/TTS offloaded to powerful remote server
- Orchestrator runs natively for direct audio hardware access
- Ultra-low latency audio capture and playback
- Wake word detection runs locally on Pi

**Resource requirements:** Pi 3/4/Zero 2W (1GB+ RAM), remote server (2GB+ RAM, 4+ cores).

**Tip:** Use `hey_mycroft` wake word model for best Raspberry Pi performance (see [WAKEWORD_ENGINES.md](WAKEWORD_ENGINES.md)).

---

## 📦 Precise Engine Artifacts (ARMv7 + ARM64)

This repository supports building and publishing standalone Mycroft Precise engine artifacts for both 32-bit and 64-bit Raspberry Pi targets:

- `./build_precise_engine_armv7.sh` → `artifacts/precise-engine-armv7/precise-engine.tar.gz`
- `./build_precise_engine_arm64.sh` → `artifacts/precise-engine-arm64/precise-engine.tar.gz`

GitHub Release publication is automated via:

- `.github/workflows/release-precise-engine.yml`

For target compatibility and release steps, see:

- [PRECISE_COMPATIBILITY_AND_RELEASE.md](PRECISE_COMPATIBILITY_AND_RELEASE.md)

---

## ⚙️ Configuration

Configuration via environment variables in `.env` file. All settings have sensible defaults.

### Audio Configuration
```bash
AUDIO_SAMPLE_RATE=16000                  # Sample rate (Hz)
AUDIO_FRAME_MS=20                        # Frame duration (ms)
AUDIO_CAPTURE_DEVICE=default             # Input device (default, hw:0,0, etc.)
AUDIO_PLAYBACK_DEVICE=default            # Output device
AUDIO_BACKEND=portaudio                  # Audio backend (portaudio, alsa)
AUDIO_INPUT_GAIN=1.0                     # Microphone gain multiplier
```

### Voice Activity Detection (VAD)
```bash
VAD_TYPE=webrtc                          # VAD engine: webrtc, silero
VAD_CONFIDENCE=0.5                       # Detection threshold (0.0-1.0)
VAD_MIN_SPEECH_MS=50                     # Min speech duration to start capture
VAD_MIN_SILENCE_MS=800                   # Silence duration to end capture
VAD_MIN_RMS=0.002                        # Minimum RMS level for speech

# Silero VAD (optional, higher accuracy)
SILERO_AUTO_DOWNLOAD=true                # Auto-download models on first run
SILERO_MODEL_CACHE_DIR=docker/silero-models
```

### Cut-in / Interruption Detection
```bash
VAD_CUT_IN_RMS=0.0025                    # RMS threshold for interruption
VAD_CUT_IN_MIN_MS=150                    # Min duration to trigger cut-in
VAD_CUT_IN_FRAMES=3                      # Consecutive frames required
VAD_CUT_IN_USE_SILERO=false              # Use Silero VAD for cut-in detection
VAD_CUT_IN_SILERO_CONFIDENCE=0.3         # Silero confidence for cut-in
```

### Echo Cancellation
```bash
ECHO_CANCEL=true                         # Enable WebRTC AEC
ECHO_CANCEL_STRENGTH=strong              # AEC strength: low, medium, strong
```

### Wake Word Detection
```bash
WAKE_WORD_ENABLED=false                  # Enable hotword detection
WAKE_WORD_ENGINE=openwakeword            # Engine: openwakeword, precise, picovoice
WAKE_WORD_CONFIDENCE=0.95                # Detection threshold (0.0-1.0)
WAKE_WORD_TIMEOUT_MS=120000              # Timeout after wake (ms)
OPENWAKEWORD_MODEL_PATH=hey_mycroft      # Model: hey_mycroft, hey_jarvis, alexa

# OpenWakeWord
OPENWAKEWORD_AUTO_DOWNLOAD=true
OPENWAKEWORD_MODELS_DIR=docker/wakeword-models

# Picovoice (requires API key)
WAKE_WORD_PICOVOICE_KEY=your_key_here
```

See [WAKEWORD_ENGINES.md](WAKEWORD_ENGINES.md) for detailed wake word configuration.

### Speech Services
```bash
# Whisper (STT)
WHISPER_URL=http://localhost:10000       # Whisper API endpoint

# Piper (TTS)
PIPER_URL=http://localhost:10001         # Piper API endpoint
PIPER_VOICE_ID=en_US-amy-medium          # Voice model
PIPER_SPEED=1.0                          # Playback speed multiplier
```

### Gateway Configuration
```bash
# Provider selection
GATEWAY_PROVIDER=openclaw                # openclaw, generic, zeroclaw, tinyclaw, etc.

# OpenClaw Gateway
GATEWAY_HTTP_URL=https://gateway.example.com
GATEWAY_AUTH_TOKEN=your_token_here
GATEWAY_AGENT_ID=assistant
GATEWAY_SESSION_PREFIX=voice             # Session ID prefix

# Generic HTTP/WebSocket Gateway
GATEWAY_HTTP_URL=http://localhost:8000
GATEWAY_HTTP_ENDPOINT=/api/short
GATEWAY_WS_URL=ws://localhost:8000/ws

# Gateway behavior
GATEWAY_TIMEOUT_MS=30000                 # Request timeout
GATEWAY_DEBOUNCE_MS=2000                 # Debounce rapid follow-ups
GATEWAY_TTS_FAST_START_WORDS=5           # Start TTS after N words
```

See `orchestrator/config.py` for all available gateway providers and configuration options.

### Audio Buffering & Chunking
```bash
CHUNK_MAX_MS=10000                       # Max audio chunk duration before force send
PRE_ROLL_MS=2000                         # Pre-roll buffer for speech start
CUT_IN_PRE_ROLL_MS=100                   # Pre-roll buffer for interruptions
```

### Optional Features
```bash
# Emotion Detection (SenseVoice)
EMOTION_ENABLED=false
EMOTION_MODEL=sensevoice-small
EMOTION_TIMEOUT_MS=300
EMOTION_AUTO_DOWNLOAD=true
EMOTION_MODELS_DIR=docker/emotion-models
```

---

## 🏗️ Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    OpenClaw Voice Orchestrator                   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Duplex    │───→│  WebRTC AEC  │───→│  Ring Buffer │      │
│  │  Audio I/O  │    │ (Echo Cancel)│    │  (Pre-roll)  │      │
│  └─────────────┘    └──────────────┘    └──────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │   Wake Word      │      │
│         │                            │   Detection      │      │
│         │                            │ (OpenWakeWord/   │      │
│         │                            │  Precise/        │      │
│         │                            │  Picovoice)      │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │   VAD Engine     │      │
│         │                            │ (WebRTC/Silero)  │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │   Debouncer      │      │
│         │                            │  & Aggregator    │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │   Whisper STT    │      │
│         │                            │   (HTTP Client)  │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │  Gateway Adapter │      │
│         │                            │  (OpenClaw/      │      │
│         │                            │   Generic/etc)   │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │   Response with  │      │
│         │                            │   TTS Streaming  │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        ↓              │
│         │                            ┌──────────────────┐      │
│         │                            │   Piper TTS      │      │
│         │                            │   (HTTP Client)  │      │
│         │                            └──────────────────┘      │
│         │                                        │              │
│         │                                        │              │
│         └────────────────────────────────────────┘              │
│                          (Audio Playback)                       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Audio Processing Pipeline

**1. Capture (Duplex Audio I/O)**
- Continuous 16kHz mono PCM capture via sounddevice
- Configurable software gain for input adjustment
- Full-duplex: simultaneous capture and playback

**2. Echo Cancellation (WebRTC AEC)**
- Removes echo from TTS playback captured by microphone
- Three strength levels: low, medium, strong
- Frame-by-frame processing with playback reference

**3. Pre-roll Buffering (Ring Buffer)**
- Maintains rolling 100-2000ms audio buffer
- Captures speech onset before VAD trigger
- Separate buffers for wake and cut-in events

**4. Wake Word Detection (Optional)**
- Three engine options with model auto-download
- Confidence thresholding and cooldown periods
- State machine: SLEEPING → LISTENING transition

**5. Voice Activity Detection (VAD)**
- Dual-engine support: WebRTC VAD (fast) or Silero VAD (accurate)
- Speech/silence detection with configurable thresholds
- RMS + confidence-based gating

**6. Cut-in Detection**
- Monitors audio during TTS playback
- RMS spike + frame count or Silero confidence trigger
- Interrupts assistant response and starts new capture

**7. Speech-to-Text (Whisper)**
- HTTP API to faster-whisper service
- Sends PCM chunks with pre-roll buffer
- Returns transcribed text with timestamps

**8. Debouncing & Aggregation**
- Collects rapid follow-up utterances (2s window)
- Prevents fragmented queries to gateway
- Combines transcripts before sending

**9. Gateway Integration**
- Pluggable gateway adapter pattern
- Supports multiple backend providers
- Streaming response support for fast TTS start

**10. Text-to-Speech (Piper)**
- Neural TTS with voice and speed selection
- Streams audio chunks for low latency
- Response queueing and mixing

**11. Duplicate Filtering**
- Strips already-spoken content from streaming updates
- Estimates spoken prefix based on elapsed time
- Seamless continuation of updated responses

### State Machine

```
┌─────────────┐
│   SLEEPING  │◄────────────────┐
│ (Wake word  │                 │
│   waiting)  │                 │
└──────┬──────┘                 │
       │ Wake word detected     │
       │                        │
       ↓                        │
┌─────────────┐     Timeout    │
│  LISTENING  │────────────────►│
│ (VAD active)│                 │
└──────┬──────┘                 │
       │ Speech detected        │
       │                        │
       ↓                        │
┌─────────────┐                 │
│  SPEAKING   │                 │
│(Transcribing│                 │
│  & sending) │                 │
└──────┬──────┘                 │
       │ Response received      │
       │                        │
       ↓                        │
┌─────────────┐                 │
│   PLAYING   │◄────┐           │
│ (TTS output)│     │ Update    │
└──────┬──────┘     │ (stream)  │
       │ Cut-in  ───┘           │
       │ OR Complete            │
       └────────────────────────┘
```

---

## 📚 Additional Documentation

- **[WAKEWORD_ENGINES.md](WAKEWORD_ENGINES.md)** - Detailed comparison of wake word engines and model selection
- **[RASPBIAN_INSTALL.md](RASPBIAN_INSTALL.md)** - Comprehensive Raspberry Pi installation guide
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Common commands and troubleshooting
- **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)** - Production deployment checklist
- **[RECENT_CHANGES.md](RECENT_CHANGES.md)** - Recent updates and fixes

---

## 🛠️ Development & Tools

### Testing Tools

**Fake Gateway (No OpenClaw Required)**
```bash
python -m orchestrator.tools.fake_gateway
# Runs on :18901, returns canned responses for testing
```

**Wake Word Test Server**
```bash
python -m orchestrator.tools.wakeword_test_server
# POST WAV files to http://localhost:18950/test/wakeword
```

**AEC Test Server**
```bash
python -m orchestrator.tools.aec_test_server
# POST mic + playback WAV to http://localhost:18951/test/aec
# Returns RMS before/after and reduction ratio
```

**End-to-End Tests**
```bash
python e2e_test.py  # Validates full pipeline
python verify_setup.py  # Checks configuration
```

### Development Workflow

After code changes:
```bash
# Native
./run_orchestrator.sh
tail -f orchestrator_output.log

# Docker (rebuild if container modified)
docker-compose build orchestrator
docker-compose --profile orchestrator up -d
docker-compose logs -f orchestrator
```

---

## 🤝 Contributing

Contributions welcome! Please see `CONTRIBUTING.md` for guidelines.

## 📄 License

MIT License - see `LICENSE` file for details.

---

## 🆘 Support

- **Issues:** [GitHub Issues](https://github.com/yourusername/openclaw-voice/issues)
- **Discussions:** [GitHub Discussions](https://github.com/yourusername/openclaw-voice/discussions)
- **Documentation:** See `docs/` folder for detailed guides
