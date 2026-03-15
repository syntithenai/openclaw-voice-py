#!/bin/bash

################################################################################
#                   OpenClaw Voice Orchestrator - Installer                    #
#                   Compatible with Raspbian 32-bit & 64-bit                  #
################################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Detect system architecture
ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64" ]]; then
    ARCH_NAME="ARM64 (64-bit)"
elif [[ "$ARCH" == "armv7l" || "$ARCH" == "armv6l" ]]; then
    ARCH_NAME="ARMv7 (32-bit)"
else
    ARCH_NAME="$ARCH"
fi

# Detect Raspbian version
if grep -q "bullseye" /etc/os-release 2>/dev/null; then
    RASPBIAN_VERSION="Bullseye"
elif grep -q "bookworm" /etc/os-release 2>/dev/null; then
    RASPBIAN_VERSION="Bookworm"
elif grep -q "buster" /etc/os-release 2>/dev/null; then
    RASPBIAN_VERSION="Buster"
else
    RASPBIAN_VERSION="Unknown"
fi

# Script directory and defaults
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUNBUFFERED=1

echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}OpenClaw Voice Orchestrator${NC}"
echo -e "${BLUE}Installation Script${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
echo -e "${YELLOW}System Information:${NC}"
echo "  Architecture: $ARCH_NAME"
echo "  Raspbian Version: $RASPBIAN_VERSION"
echo "  Installation Directory: $SCRIPT_DIR"
echo ""

################################################################################
# STEP 1: Confirmation
################################################################################

read -p "Proceed with installation? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}Installation cancelled.${NC}"
    exit 1
fi

################################################################################
# STEP 2: Check for required commands
################################################################################

echo -e "${BLUE}Checking prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is not installed.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo -e "${GREEN}✓ Python 3 found: $PYTHON_VERSION${NC}"

if ! command -v pip3 &> /dev/null; then
    echo -e "${RED}Error: pip3 is not installed.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ pip3 available${NC}"

################################################################################
# STEP 3: Update system packages
################################################################################

echo -e "${BLUE}Updating system packages...${NC}"
sudo apt-get update
sudo apt-get upgrade -y

################################################################################
# STEP 4: Install system dependencies
################################################################################

echo -e "${BLUE}Installing system dependencies...${NC}"

# Core build tools
PACKAGES=(
    "build-essential"
    "python3-dev"
    "python3-venv"
    "swig"
    "curl"
    "ca-certificates"
)

# Audio libraries
PACKAGES+=(
    "libasound2"
    "libasound2-dev"
    "libportaudio2"
    "portaudio19-dev"
    "libsndfile1"
    "libsndfile1-dev"
    "pulseaudio"
    "pulseaudio-utils"
)

# Music player (MPD for voice-controlled playlist creation)
PACKAGES+=(
    "mpd"
    "mpc"
    "alsa-utils"
    "snapserver"
    "snapclient"
)

# Additional optional dependencies
PACKAGES+=(
    "git"
    "libcap2-bin"
    "libopenblas-dev"
    "libblas-dev"
    "liblapack-dev"
    "libffi-dev"
    "libssl-dev"
    "libopenjp2-7"
    "libtiff5"
)

for package in "${PACKAGES[@]}"; do
    if ! dpkg -l | grep -q "^ii  $package"; then
        echo "  Installing $package..."
        sudo apt-get install -y "$package" || echo "Warning: Failed to install $package"
    else
        echo "  ✓ $package already installed"
    fi
done

echo -e "${GREEN}✓ System dependencies installed${NC}"

echo -e "${BLUE}Configuring PulseAudio and Snapcast defaults...${NC}"

# Ensure PulseAudio is available for the current user session.
if command -v pulseaudio >/dev/null 2>&1; then
    pulseaudio --check >/dev/null 2>&1 || pulseaudio --start >/dev/null 2>&1 || true
    systemctl --user enable --now pulseaudio.service pulseaudio.socket >/dev/null 2>&1 || true
fi

# Force snapserver to publish from PulseAudio by default.
if [ -f /etc/default/snapserver ]; then
    SNAPSERVER_OPTS_VALUE="--stream pulse://?name=OpenClaw%20Main&sampleformat=48000:16:2"
    if grep -q '^SNAPSERVER_OPTS=' /etc/default/snapserver; then
        sudo sed -i "s|^SNAPSERVER_OPTS=.*|SNAPSERVER_OPTS=\"${SNAPSERVER_OPTS_VALUE}\"|" /etc/default/snapserver
    else
        echo "SNAPSERVER_OPTS=\"${SNAPSERVER_OPTS_VALUE}\"" | sudo tee -a /etc/default/snapserver >/dev/null
    fi
fi

# Default snapclient to PulseAudio output and localhost server.
if [ -f /etc/default/snapclient ]; then
    SNAPCLIENT_OPTS_VALUE="--host localhost --player pulse"
    if grep -q '^SNAPCLIENT_OPTS=' /etc/default/snapclient; then
        sudo sed -i "s|^SNAPCLIENT_OPTS=.*|SNAPCLIENT_OPTS=\"${SNAPCLIENT_OPTS_VALUE}\"|" /etc/default/snapclient
    else
        echo "SNAPCLIENT_OPTS=\"${SNAPCLIENT_OPTS_VALUE}\"" | sudo tee -a /etc/default/snapclient >/dev/null
    fi
fi

sudo systemctl enable snapserver snapclient >/dev/null 2>&1 || true
sudo systemctl restart snapserver snapclient >/dev/null 2>&1 || true
echo -e "${GREEN}✓ PulseAudio/Snapcast defaults configured${NC}"

################################################################################
# STEP 5: Create Python virtual environment
################################################################################

echo -e "${BLUE}Setting up Python virtual environment...${NC}"

VENV_DIR="$SCRIPT_DIR/.venv_orchestrator"

if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

echo "  Upgrading pip..."
pip install --upgrade pip setuptools wheel

################################################################################
# STEP 5.1: Enable low-port bind capability (optional, idempotent)
################################################################################

echo -e "${BLUE}Configuring Python low-port bind permission (for 80/443)...${NC}"
if [ -x "$SCRIPT_DIR/scripts/grant-bind-permission.sh" ]; then
    "$SCRIPT_DIR/scripts/grant-bind-permission.sh" "$VENV_DIR/bin/python" || true
else
    echo -e "${YELLOW}Warning: bind-permission helper not found at $SCRIPT_DIR/scripts/grant-bind-permission.sh${NC}"
fi

################################################################################
# STEP 6: Install Python dependencies
################################################################################

echo -e "${BLUE}Installing Python dependencies...${NC}"

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    echo "  Installing core requirements..."
    pip install -r "$SCRIPT_DIR/requirements.txt" || {
        echo -e "${RED}Warning: Some core requirements failed to install${NC}"
    }
    echo -e "${GREEN}✓ Core requirements installed${NC}"
else
    echo -e "${RED}Warning: requirements.txt not found at $SCRIPT_DIR${NC}"
fi

if [ -f "$SCRIPT_DIR/requirements-optional.txt" ]; then
    echo "  Installing optional requirements (PyTorch, Silero VAD, etc)..."
    echo "  This may take several minutes..."
    pip install -r "$SCRIPT_DIR/requirements-optional.txt" || {
        echo -e "${YELLOW}Warning: Some optional requirements failed to install (expected on RAM-constrained systems)${NC}"
    }
    echo -e "${GREEN}✓ Optional requirements completed${NC}"
else
    echo -e "${YELLOW}Warning: requirements-optional.txt not found${NC}"
fi

################################################################################
# STEP 7: Configuration
################################################################################

echo ""
echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}Configuration${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
echo "Enter configuration values. Press Enter to skip (will be prompted later)."
echo ""

# Initialize variables
declare -A config

# Audio device configuration
read -p "Audio capture device (default: 'pulse'): " -r -e config[AUDIO_CAPTURE_DEVICE]
config[AUDIO_CAPTURE_DEVICE]="${config[AUDIO_CAPTURE_DEVICE]:-pulse}"

read -p "Audio playback device (default: 'pulse'): " -r -e config[AUDIO_PLAYBACK_DEVICE]
config[AUDIO_PLAYBACK_DEVICE]="${config[AUDIO_PLAYBACK_DEVICE]:-pulse}"

# Music Player (MPD) configuration
echo ""
echo -e "${YELLOW}Music Player (MPD) Configuration:${NC}"
read -p "Enable MPD music player? (y/n, default: y): " -r -e config[ENABLE_MPD]
config[ENABLE_MPD]="${config[ENABLE_MPD]:-y}"

if [[ "${config[ENABLE_MPD]}" =~ ^[Yy] ]]; then
    read -p "Music library directory (default: ~/Music): " -r -e config[MPD_MUSIC_DIRECTORY]
    config[MPD_MUSIC_DIRECTORY]="${config[MPD_MUSIC_DIRECTORY]:-~/Music}"
    read -p "MPD port (default: 6600): " -r -e config[MPD_PORT]
    config[MPD_PORT]="${config[MPD_PORT]:-6600}"
    
    # Update MPD library after configuration
    echo -e "${YELLOW}Updating MPD library... (this may take a minute)${NC}"
    mpc update 2>/dev/null || echo "Note: Run 'mpc update' manually after starting MPD"
else
    config[MPD_MUSIC_DIRECTORY]=""
    config[MPD_PORT]="6600"
fi

# Gateway configuration
echo ""
echo -e "${YELLOW}Gateway Configuration:${NC}"
read -p "Gateway URL (e.g., 'ws://openclaw.local:8000'): " -r -e config[GATEWAY_URL]
config[GATEWAY_URL]="${config[GATEWAY_URL]:-ws://localhost:8000}"

read -p "Gateway authentication token: " -r -s config[GATEWAY_TOKEN]
echo
config[GATEWAY_TOKEN]="${config[GATEWAY_TOKEN]:-}"

# STT/Whisper configuration
echo ""
echo -e "${YELLOW}Speech-to-Text (Whisper) Configuration:${NC}"
read -p "Whisper service URL (e.g., 'http://localhost:10000'): " -r -e config[WHISPER_URL]
config[WHISPER_URL]="${config[WHISPER_URL]:-http://localhost:10000}"

# TTS/Piper configuration
echo ""
echo -e "${YELLOW}Text-to-Speech (Piper) Configuration:${NC}"
read -p "Piper service URL (e.g., 'http://localhost:10001'): " -r -e config[PIPER_URL]
config[PIPER_URL]="${config[PIPER_URL]:-http://localhost:10001}"

read -p "Piper voice (default: 'en_US-amy-medium'): " -r -e config[PIPER_VOICE]
config[PIPER_VOICE]="${config[PIPER_VOICE]:-en_US-amy-medium}"

read -p "Piper speech speed (default: '1.0'): " -r -e config[PIPER_SPEED]
config[PIPER_SPEED]="${config[PIPER_SPEED]:-1.0}"

# Wake word configuration
echo ""
echo -e "${YELLOW}Wake Word Configuration:${NC}"
if [[ "$ARCH" == "armv7l" || "$ARCH" == "armv6l" ]]; then
    DEFAULT_WAKE_ENGINE="precise"
    DEFAULT_WAKE_CONF="0.15"
    DEFAULT_WAKE_MODEL="docker/wakeword-models/hey-mycroft.pb"
else
    DEFAULT_WAKE_ENGINE="openwakeword"
    DEFAULT_WAKE_CONF="0.5"
    DEFAULT_WAKE_MODEL="hey_mycroft"
fi

echo "  Policy default: OpenWakeWord on all installs, except Raspberry Pi ARMv7/ARMv6 uses Precise."
echo "  Suggested default for $ARCH_NAME: engine=$DEFAULT_WAKE_ENGINE, model=$DEFAULT_WAKE_MODEL"
read -p "Wake word engine to enforce (openwakeword|precise, default: '$DEFAULT_WAKE_ENGINE'): " -r -e config[WAKE_WORD_ENGINE]
config[WAKE_WORD_ENGINE]="${config[WAKE_WORD_ENGINE]:-$DEFAULT_WAKE_ENGINE}"
config[WAKE_WORD_ENGINE]="$(echo "${config[WAKE_WORD_ENGINE]}" | tr '[:upper:]' '[:lower:]')"
if [[ "${config[WAKE_WORD_ENGINE]}" != "openwakeword" && "${config[WAKE_WORD_ENGINE]}" != "precise" ]]; then
    echo -e "${YELLOW}Invalid wake word engine '${config[WAKE_WORD_ENGINE]}'; defaulting to '$DEFAULT_WAKE_ENGINE'.${NC}"
    config[WAKE_WORD_ENGINE]="$DEFAULT_WAKE_ENGINE"
fi

if [[ "${config[WAKE_WORD_ENGINE]}" == "precise" ]]; then
    ENGINE_DEFAULT_CONF="0.15"
    ENGINE_DEFAULT_MODEL="docker/wakeword-models/hey-mycroft.pb"
else
    ENGINE_DEFAULT_CONF="0.5"
    ENGINE_DEFAULT_MODEL="hey_mycroft"
fi

read -p "Wake word confidence threshold (0.0-1.0, default: '$ENGINE_DEFAULT_CONF'): " -r -e config[WAKE_WORD_CONFIDENCE]
config[WAKE_WORD_CONFIDENCE]="${config[WAKE_WORD_CONFIDENCE]:-$ENGINE_DEFAULT_CONF}"

read -p "Wake word model (default: '$ENGINE_DEFAULT_MODEL'): " -r -e config[WAKE_WORD_MODEL]
config[WAKE_WORD_MODEL]="${config[WAKE_WORD_MODEL]:-$ENGINE_DEFAULT_MODEL}"

if [[ "${config[WAKE_WORD_ENGINE]}" == "precise" ]]; then
    PRECISE_ENABLED="true"
    PRECISE_MODEL_PATH="${config[WAKE_WORD_MODEL]}"
    PRECISE_WAKE_WORD="hey-mycroft"
    PRECISE_CONFIDENCE="${config[WAKE_WORD_CONFIDENCE]}"
    OPENWAKEWORD_ENABLED="false"
    OPENWAKEWORD_MODEL_PATH="hey_mycroft"
    OPENWAKEWORD_WAKE_WORD=""
    OPENWAKEWORD_CONFIDENCE="0.5"
else
    PRECISE_ENABLED="false"
    PRECISE_MODEL_PATH="docker/wakeword-models/hey-mycroft.pb"
    PRECISE_WAKE_WORD=""
    PRECISE_CONFIDENCE="0.15"
    OPENWAKEWORD_ENABLED="true"
    OPENWAKEWORD_MODEL_PATH="${config[WAKE_WORD_MODEL]}"
    OPENWAKEWORD_WAKE_WORD="${config[WAKE_WORD_MODEL]}"
    OPENWAKEWORD_CONFIDENCE="${config[WAKE_WORD_CONFIDENCE]}"
fi

# VAD configuration
echo ""
echo -e "${YELLOW}Voice Activity Detection Configuration:${NC}"
read -p "VAD backend (webrtc|silero|none, default: 'webrtc'): " -r -e config[VAD_BACKEND]
config[VAD_BACKEND]="${config[VAD_BACKEND]:-webrtc}"

read -p "VAD aggressiveness (0-3, default: '1'): " -r -e config[VAD_AGGRESSIVENESS]
config[VAD_AGGRESSIVENESS]="${config[VAD_AGGRESSIVENESS]:-1}"

# Logging configuration
echo ""
echo -e "${YELLOW}Logging Configuration:${NC}"
read -p "Log level (DEBUG|INFO|WARNING|ERROR, default: 'INFO'): " -r -e config[LOG_LEVEL]
config[LOG_LEVEL]="${config[LOG_LEVEL]:-INFO}"

################################################################################
# STEP 8: Create .env file
################################################################################

echo ""
echo -e "${BLUE}Creating .env configuration file...${NC}"

ENV_FILE="$SCRIPT_DIR/.env"
MUSIC_ENABLED_VALUE="false"
if [[ "${config[ENABLE_MPD]:-n}" =~ ^[Yy]$ ]]; then
    MUSIC_ENABLED_VALUE="true"
fi
WRITE_ENV="y"
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}Existing .env detected at $ENV_FILE${NC}"
    read -p "Overwrite existing .env? (y/n, default: n): " -r -e OVERWRITE_ENV
    OVERWRITE_ENV="${OVERWRITE_ENV:-n}"
    if [[ ! "$OVERWRITE_ENV" =~ ^[Yy]$ ]]; then
        WRITE_ENV="n"
        echo -e "${GREEN}✓ Keeping existing .env (rerun-safe mode)${NC}"
    else
        cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
        echo -e "${YELLOW}Backed up existing .env before overwrite${NC}"
    fi
fi

if [[ "$WRITE_ENV" =~ ^[Yy]$ ]]; then
cat > "$ENV_FILE" << EOF
# OpenClaw Voice Orchestrator Configuration
# Generated by installer on $(date)

# Audio Configuration
AUDIO_CAPTURE_DEVICE=${config[AUDIO_CAPTURE_DEVICE]}
AUDIO_PLAYBACK_DEVICE=${config[AUDIO_PLAYBACK_DEVICE]}
AUDIO_SAMPLE_RATE=16000
AUDIO_FRAME_MS=20

# Music Player Configuration (MPD)
MPD_ENABLED=${config[ENABLE_MPD]:-y}
MUSIC_ENABLED=$MUSIC_ENABLED_VALUE
MPD_MUSIC_DIRECTORY=${config[MPD_MUSIC_DIRECTORY]:-~/Music}
MPD_PORT=${config[MPD_PORT]:-6600}
MPD_HOST=localhost
MPD_FIFO_HOST_PATH=/tmp/openclaw-mpd-fifo
MPD_FIFO_ENABLED=false
MPD_FIFO_PATH=/tmp/openclaw-mpd-fifo/music.pcm
MPD_FIFO_SAMPLE_RATE=44100
MPD_FIFO_CHANNELS=2
MPD_FIFO_BITS_PER_SAMPLE=16
MPD_FIFO_CHUNK_BYTES=16384
MPD_MIX_GAIN=1.0
MPD_MIX_DUCK_TTS_GAIN=0.30
MPD_MIX_DUCK_ALARM_GAIN=0.12
MPD_MIX_DUCK_LISTENING_GAIN=0.25
SNAPCAST_ENABLED=true
SNAPCAST_HOST=localhost
SNAPCAST_PORT=1705

# Gateway Configuration
GATEWAY_URL=${config[GATEWAY_URL]}
GATEWAY_TOKEN=${config[GATEWAY_TOKEN]}
GATEWAY_DEBOUNCE_MS=2000
GATEWAY_TTS_FAST_START_WORDS=3

# STT Configuration (Whisper)
WHISPER_URL=${config[WHISPER_URL]}
WHISPER_LANGUAGE=en

# TTS Configuration (Piper)
PIPER_URL=${config[PIPER_URL]}
PIPER_VOICE=${config[PIPER_VOICE]}
PIPER_SPEED=${config[PIPER_SPEED]}

# Wake Word Configuration (OpenWakeWord)
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=${config[WAKE_WORD_ENGINE]}
PRECISE_ENABLED=$PRECISE_ENABLED
PRECISE_MODEL_PATH=$PRECISE_MODEL_PATH
PRECISE_WAKE_WORD=$PRECISE_WAKE_WORD
PRECISE_CONFIDENCE=$PRECISE_CONFIDENCE
OPENWAKEWORD_ENABLED=$OPENWAKEWORD_ENABLED
OPENWAKEWORD_MODEL_PATH=$OPENWAKEWORD_MODEL_PATH
OPENWAKEWORD_WAKE_WORD=$OPENWAKEWORD_WAKE_WORD
OPENWAKEWORD_CONFIDENCE=$OPENWAKEWORD_CONFIDENCE
PICOVOICE_ENABLED=false
WAKE_WORD_PREBUFFER_MS=80
OPENWAKEWORD_AUTO_DOWNLOAD=true
OPENWAKEWORD_MODELS_DIR=docker/wakeword-models

# VAD Configuration
VAD_BACKEND=${config[VAD_BACKEND]}
VAD_AGGRESSIVENESS=${config[VAD_AGGRESSIVENESS]}

# Emotion Detection (SenseVoice)
EMOTION_DETECTION_ENABLED=false

# Logging
LOG_LEVEL=${config[LOG_LEVEL]}
LOG_FILE=orchestrator.log

# Device Identity (OpenClaw Authorization)
DEVICE_IDENTITY_PATH=~/.openclaw

# Model Cache Paths
FUNASR_CACHE=~/.cache/funasr
OPENWAKEWORD_PRELOAD_MODELS=true
EOF

echo -e "${GREEN}✓ Configuration saved to: $ENV_FILE${NC}"
else
    echo -e "${GREEN}✓ Existing configuration preserved: $ENV_FILE${NC}"
fi

upsert_env_var() {
    local file_path="$1"
    local key="$2"
    local value="$3"
    local escaped_value
    escaped_value=$(printf '%s' "$value" | sed 's/[&/]/\\&/g')
    if grep -qE "^${key}=" "$file_path" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$file_path"
    else
        echo "${key}=${value}" >> "$file_path"
    fi
}

apply_wakeword_engine_config() {
    local file_path="$1"
    [ -f "$file_path" ] || touch "$file_path"

    upsert_env_var "$file_path" "WAKE_WORD_ENABLED" "true"
    upsert_env_var "$file_path" "WAKE_WORD_ENGINE" "${config[WAKE_WORD_ENGINE]}"
    upsert_env_var "$file_path" "PRECISE_ENABLED" "$PRECISE_ENABLED"
    upsert_env_var "$file_path" "PRECISE_MODEL_PATH" "$PRECISE_MODEL_PATH"
    upsert_env_var "$file_path" "PRECISE_WAKE_WORD" "$PRECISE_WAKE_WORD"
    upsert_env_var "$file_path" "PRECISE_CONFIDENCE" "$PRECISE_CONFIDENCE"
    upsert_env_var "$file_path" "OPENWAKEWORD_ENABLED" "$OPENWAKEWORD_ENABLED"
    upsert_env_var "$file_path" "OPENWAKEWORD_MODEL_PATH" "$OPENWAKEWORD_MODEL_PATH"
    upsert_env_var "$file_path" "OPENWAKEWORD_WAKE_WORD" "$OPENWAKEWORD_WAKE_WORD"
    upsert_env_var "$file_path" "OPENWAKEWORD_CONFIDENCE" "$OPENWAKEWORD_CONFIDENCE"
    upsert_env_var "$file_path" "OPENWAKEWORD_AUTO_DOWNLOAD" "true"
    upsert_env_var "$file_path" "OPENWAKEWORD_MODELS_DIR" "docker/wakeword-models"
    upsert_env_var "$file_path" "PICOVOICE_ENABLED" "false"
}

apply_wakeword_engine_config "$ENV_FILE"
echo -e "${GREEN}✓ Enforced wake word engine: ${config[WAKE_WORD_ENGINE]}${NC}"

mkdir -p /tmp/openclaw-mpd-fifo || true
chmod 0777 /tmp/openclaw-mpd-fifo || true
if [ ! -p /tmp/openclaw-mpd-fifo/music.pcm ]; then
    rm -f /tmp/openclaw-mpd-fifo/music.pcm || true
    mkfifo -m 0666 /tmp/openclaw-mpd-fifo/music.pcm || true
fi
chmod 0666 /tmp/openclaw-mpd-fifo/music.pcm || true

echo ""
echo -e "${BLUE}Ensuring wakeword resources...${NC}"
bash "$SCRIPT_DIR/ensure_wakeword_resources.sh" all || true
echo ""
echo "Configuration values:"
for key in "${!config[@]}"; do
    if [[ "$key" == "GATEWAY_TOKEN" ]]; then
        echo "  $key: ***hidden***"
    else
        echo "  $key: ${config[$key]}"
    fi
done

################################################################################
# STEP 9: Create activation script
################################################################################

echo ""
echo -e "${BLUE}Creating activation script...${NC}"

ACTIVATE_SCRIPT="$SCRIPT_DIR/activate.sh"
cat > "$ACTIVATE_SCRIPT" << 'EOF'
#!/bin/bash
# Quick activation script for the OpenClaw Voice Orchestrator virtual environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv_orchestrator/bin/activate"
echo "OpenClaw Voice virtual environment activated"
EOF

chmod +x "$ACTIVATE_SCRIPT"
echo -e "${GREEN}✓ Activation script created: $ACTIVATE_SCRIPT${NC}"

################################################################################
# STEP 10: Create run script
################################################################################

echo -e "${BLUE}Creating run script...${NC}"

RUN_SCRIPT="$SCRIPT_DIR/run.sh"
cat > "$RUN_SCRIPT" << 'EOF'
#!/bin/bash
# Run the OpenClaw Voice Orchestrator
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv_orchestrator/bin/activate"
cd "$SCRIPT_DIR"
python -m orchestrator.main
EOF

chmod +x "$RUN_SCRIPT"
echo -e "${GREEN}✓ Run script created: $RUN_SCRIPT${NC}"

################################################################################
# SUMMARY
################################################################################

echo ""
echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}Installation Complete!${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
echo -e "${GREEN}✓ System packages installed${NC}"
echo -e "${GREEN}✓ Virtual environment created at: $VENV_DIR${NC}"
echo -e "${GREEN}✓ Python dependencies installed${NC}"
if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}✓ Configuration available at: $ENV_FILE${NC}"
fi
echo -e "${GREEN}✓ Installer is safe to rerun (idempotent package/venv steps, optional .env overwrite)${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo "1. Verify your .env configuration:"
echo "   nano $ENV_FILE"
echo ""
echo "2. Activate the virtual environment:"
echo "   source $ACTIVATE_SCRIPT"
echo "   # OR:"
echo "   source $VENV_DIR/bin/activate"
echo ""
echo "3. Start the orchestrator:"
echo "   $RUN_SCRIPT"
echo "   # OR (after activating venv):"
echo "   python -m orchestrator.main"
echo ""
echo "4. Monitor logs:"
echo "   tail -f $SCRIPT_DIR/orchestrator.log"
echo ""
echo -e "${YELLOW}Troubleshooting:${NC}"
echo ""
echo "• Audio issues: Check AUDIO_CAPTURE_DEVICE and AUDIO_PLAYBACK_DEVICE in .env"
echo "  List available devices with: python3 -m sounddevice"
echo ""
echo "• Gateway connection: Verify GATEWAY_URL and GATEWAY_TOKEN are correct"
echo ""
echo "• Whisper/Piper services: Ensure container services are running"
echo ""
echo "• Logs: Check orchestrator.log for detailed error messages"
echo ""

# Optionally activate the venv for the current session
read -p "Activate virtual environment now for this session? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    source "$VENV_DIR/bin/activate"
    echo -e "${GREEN}✓ Virtual environment activated${NC}"
fi
