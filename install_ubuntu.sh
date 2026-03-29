#!/bin/bash

################################################################################
#                   OpenClaw Voice Orchestrator - Installer                    #
#                   Compatible with Ubuntu (20.04 LTS and newer)              #
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
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_NAME="x86_64 (64-bit)"
elif [[ "$ARCH" == "aarch64" ]]; then
    ARCH_NAME="ARM64 (64-bit)"
elif [[ "$ARCH" == "armv7l" || "$ARCH" == "armv6l" ]]; then
    ARCH_NAME="ARMv7 (32-bit)"
else
    ARCH_NAME="$ARCH"
fi

# Detect Ubuntu version
UBUNTU_CODENAME=$(grep "^VERSION_CODENAME=" /etc/os-release 2>/dev/null | cut -d'=' -f2)
UBUNTU_VERSION=$(grep "^VERSION_ID=" /etc/os-release 2>/dev/null | cut -d'=' -f2 | tr -d '"')

if [[ -z "$UBUNTU_CODENAME" ]]; then
    UBUNTU_CODENAME="Unknown"
fi

if [[ -z "$UBUNTU_VERSION" ]]; then
    UBUNTU_VERSION="Unknown"
fi

# Script directory and defaults
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUNBUFFERED=1

echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}OpenClaw Voice Orchestrator${NC}"
echo -e "${BLUE}Installation Script (Ubuntu)${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
echo -e "${YELLOW}System Information:${NC}"
echo "  Architecture: $ARCH_NAME"
echo "  Ubuntu Version: $UBUNTU_VERSION ($UBUNTU_CODENAME)"
echo "  Installation Directory: $SCRIPT_DIR"
echo ""

# Verify Ubuntu compatibility
if ! grep -q "Ubuntu" /etc/os-release 2>/dev/null; then
    echo -e "${RED}Warning: This script is designed for Ubuntu systems.${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}Installation cancelled.${NC}"
        exit 1
    fi
fi

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

# Music/audio helpers for native backend
PACKAGES+=(
    "alsa-utils"
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
        echo -e "${YELLOW}Warning: Some optional requirements failed to install${NC}"
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
read -p "Audio capture device (default: 'default'): " -r -e config[AUDIO_CAPTURE_DEVICE]
config[AUDIO_CAPTURE_DEVICE]="${config[AUDIO_CAPTURE_DEVICE]:-default}"

read -p "Audio playback device (default: 'default'): " -r -e config[AUDIO_PLAYBACK_DEVICE]
config[AUDIO_PLAYBACK_DEVICE]="${config[AUDIO_PLAYBACK_DEVICE]:-default}"

# Music backend configuration
echo ""
echo -e "${YELLOW}Music Backend Configuration:${NC}"
read -p "Enable music backend? (y/n, default: y): " -r -e config[ENABLE_MUSIC]
config[ENABLE_MUSIC]="${config[ENABLE_MUSIC]:-y}"

if [[ "${config[ENABLE_MUSIC]}" =~ ^[Yy] ]]; then
    read -p "Music library directory (default: ~/Music): " -r -e config[MEDIA_LIBRARY_ROOT]
    config[MEDIA_LIBRARY_ROOT]="${config[MEDIA_LIBRARY_ROOT]:-~/Music}"
    read -p "Playlist directory (default: playlists): " -r -e config[PLAYLIST_ROOT]
    config[PLAYLIST_ROOT]="${config[PLAYLIST_ROOT]:-playlists}"
else
    config[MEDIA_LIBRARY_ROOT]=""
    config[PLAYLIST_ROOT]="playlists"
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
read -p "Wake word confidence threshold (0.0-1.0, default: '0.95'): " -r -e config[WAKE_WORD_CONFIDENCE]
config[WAKE_WORD_CONFIDENCE]="${config[WAKE_WORD_CONFIDENCE]:-0.95}"

read -p "Wake word model (default: 'hey_mycroft'): " -r -e config[OPENWAKEWORD_MODEL_PATH]
config[OPENWAKEWORD_MODEL_PATH]="${config[OPENWAKEWORD_MODEL_PATH]:-hey_mycroft}"

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
# Generated by Ubuntu installer on $(date)

# Audio Configuration
AUDIO_CAPTURE_DEVICE=${config[AUDIO_CAPTURE_DEVICE]}
AUDIO_PLAYBACK_DEVICE=${config[AUDIO_PLAYBACK_DEVICE]}
AUDIO_SAMPLE_RATE=16000
AUDIO_FRAME_MS=20

# Music Backend Configuration (native)
MUSIC_ENABLED=${config[ENABLE_MUSIC]:-y}
MEDIA_PLAYER_BACKEND=native
MEDIA_LIBRARY_ROOT=${config[MEDIA_LIBRARY_ROOT]:-~/Music}
MEDIA_INDEX_DB_PATH=.media/library.sqlite3
PLAYLIST_ROOT=${config[PLAYLIST_ROOT]:-playlists}
MUSIC_COMMAND_TIMEOUT_S=8.0

# Gateway Configuration
GATEWAY_URL=${config[GATEWAY_URL]}
GATEWAY_TOKEN=${config[GATEWAY_TOKEN]}
GATEWAY_DEBOUNCE_MS=2000
GATEWAY_TTS_STREAMING_ENABLED=false
GATEWAY_TTS_FAST_START_WORDS=3

# STT Configuration (Whisper)
WHISPER_URL=${config[WHISPER_URL]}
WHISPER_LANGUAGE=en

# TTS Configuration (Piper)
PIPER_URL=${config[PIPER_URL]}
PIPER_VOICE=${config[PIPER_VOICE]}
PIPER_SPEED=${config[PIPER_SPEED]}

# Wake Word Configuration (OpenWakeWord)
OPENWAKEWORD_MODEL_PATH=${config[OPENWAKEWORD_MODEL_PATH]}
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=openwakeword
WAKE_WORD_CONFIDENCE=${config[WAKE_WORD_CONFIDENCE]}
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
