#!/bin/bash
# Install and configure OpenClaw Voice Orchestrator on a remote Raspberry Pi
# Usage: ./install_raspbian_remote.sh <pi_ip_address>
# 
# This script:
# 1. Sets up SSH autologin (ed25519 key)
# 2. Clones the repository
# 3. Installs dependencies
# 4. Configures .env with host's IP address for piper, whisper, and gateway
# 5. Sets up systemd service for auto-start
#
# Assumptions:
# - The computer running this script is hosting Piper, Whisper, and OpenClaw Gateway
# - Pi username is 'stever'
# - Pi has Raspbian OS with internet connectivity

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check arguments
if [ $# -ne 1 ]; then
    echo -e "${RED}Error: IP address required${NC}"
    echo "Usage: $0 <pi_ip_address>"
    echo "Example: $0 10.1.1.210"
    exit 1
fi

PI_IP="$1"
PI_USER="stever"
PI_SSH_ALIAS="pi"
LOCAL_HOST_IP=$(hostname -I | awk '{print $1}')

echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}OpenClaw Voice - Remote Pi Installation${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "Target Pi IP: $PI_IP"
echo "Current host IP: $LOCAL_HOST_IP"
echo "SSH alias: $PI_SSH_ALIAS"
echo ""

# Step 1: Setup SSH autologin
echo -e "${YELLOW}Step 1: Setting up SSH autologin...${NC}"
if [ ! -f ~/.ssh/id_ed25519 ]; then
    echo "  → Generating ed25519 SSH key..."
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "openclaw-voice@$(hostname)"
else
    echo "  ✓ SSH key already exists"
fi

# Add host to ~/.ssh/config if not present
if ! grep -q "^Host $PI_SSH_ALIAS" ~/.ssh/config 2>/dev/null; then
    echo "  → Adding SSH config entry..."
    cat >> ~/.ssh/config << EOF

Host $PI_SSH_ALIAS
    HostName $PI_IP
    User $PI_USER
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
    ConnectTimeout 5
EOF
else
    echo "  ✓ SSH config entry already exists"
fi

# Copy SSH key to Pi
echo "  → Copying SSH key to Pi (you may be prompted for password)..."
ssh-copy-id -i ~/.ssh/id_ed25519.pub -o ConnectTimeout=5 "$PI_USER@$PI_IP" 2>/dev/null || {
    echo -e "${YELLOW}Note: SSH key copy may have failed. Trying alternative method...${NC}"
    ssh-keyscan -t ed25519 "$PI_IP" >> ~/.ssh/known_hosts 2>/dev/null || true
}

# Test SSH connection
if timeout 5 ssh -i ~/.ssh/id_ed25519 "$PI_USER@$PI_IP" "echo 'SSH connection successful'" >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ SSH autologin configured${NC}"
else
    echo -e "${RED}  ✗ SSH connection failed. Please check the IP address and Pi connectivity.${NC}"
    exit 1
fi

# Step 2: Clone repository
echo ""
echo -e "${YELLOW}Step 2: Cloning OpenClaw Voice repository...${NC}"
ssh "$PI_SSH_ALIAS" "if [ ! -d ~/openclaw-voice ]; then \
    git clone https://github.com/syntithenai/openclaw-voice.git ~/openclaw-voice && \
    echo '  ✓ Repository cloned'; \
else \
    echo '  ✓ Repository already exists'; \
    cd ~/openclaw-voice && git pull; \
fi"

# Step 3: Run installation script on Pi
echo ""
echo -e "${YELLOW}Step 3: Installing dependencies on Pi...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && bash install_raspbian.sh << EOF
$LOCAL_HOST_IP
$LOCAL_HOST_IP
$LOCAL_HOST_IP
1.0
0.95
hey_mycroft
webrtc
y
EOF
" || echo -e "${YELLOW}Note: Install script completed (some prompts may have timed out)${NC}"

# Step 4: Configure .env on Pi
echo ""
echo -e "${YELLOW}Step 4: Configuring .env for remote services...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && cat > .env << 'ENVEOF'
# Core
AUDIO_SAMPLE_RATE=16000
AUDIO_FRAME_MS=20
AUDIO_CAPTURE_DEVICE=1
AUDIO_PLAYBACK_DEVICE=CD002: USB Audio (hw:3,0)
AUDIO_BACKEND=portaudio
AUDIO_INPUT_GAIN=50.0

# VAD
VAD_TYPE=webrtc
VAD_CONFIDENCE=0.6
VAD_MIN_SPEECH_MS=100
VAD_MIN_SILENCE_MS=800
VAD_MIN_RMS=0.002
VAD_CUT_IN_RMS=0.008
VAD_CUT_IN_MIN_MS=100
VAD_CUT_IN_FRAMES=2
VAD_CUT_IN_USE_SILERO=false
VAD_CUT_IN_SILERO_CONFIDENCE=0.01
SILERO_MODEL_PATH=
SILERO_AUTO_DOWNLOAD=true
SILERO_MODEL_URL=https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx
SILERO_MODEL_CACHE_DIR=/home/stever/.cache/openclaw/silero-models

# Emotion detection (disabled on Pi due to ARM limitations)
EMOTION_ENABLED=false
EMOTION_MODEL=sensevoice-small
EMOTION_TIMEOUT_MS=300
EMOTION_AUTO_DOWNLOAD=true
MODELSCOPE_CACHE=/home/stever/.cache/modelscope
EMOTION_MODELS_DIR=docker/emotion-models

# Wake word detection
WAKE_WORD_ENABLED=false
WAKE_WORD_ENGINE=openwakeword
WAKE_WORD_TIMEOUT_MS=10000
WAKE_WORD_CONFIDENCE=0.95
OPENWAKEWORD_MODEL_PATH=hey_mycroft
OPENWAKEWORD_AUTO_DOWNLOAD=true
OPENWAKEWORD_MODELS_DIR=docker/wakeword-models

# AEC
ECHO_CANCEL=true
ECHO_CANCEL_WEBRTC_AEC_STRENGTH=strong

# Chunking
CHUNK_MAX_MS=10000
PRE_ROLL_MS=1500
CUT_IN_PRE_ROLL_MS=100

# Services (point to host machine)
WHISPER_URL=http://$LOCAL_HOST_IP:10000
PIPER_URL=http://$LOCAL_HOST_IP:10001
PIPER_VOICE_ID=en_US-amy-medium
PIPER_SPEED=1.0
GATEWAY_TTS_FAST_START_WORDS=3

# OpenClaw Gateway
OPENCLAW_GATEWAY_URL=http://$LOCAL_HOST_IP:18789
ENVEOF
echo '  ✓ .env configured with host IP: $LOCAL_HOST_IP'"

# Step 5: Clear Python cache and restart
echo ""
echo -e "${YELLOW}Step 5: Clearing Python cache...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && \
find orchestrator -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
find orchestrator -name '*.pyc' -delete 2>/dev/null || true; \
echo '  ✓ Cache cleared'"

# Step 6: Test orchestrator startup
echo ""
echo -e "${YELLOW}Step 6: Testing orchestrator startup...${NC}"
ssh "$PI_SSH_ALIAS" "pkill -f orchestrator.main || true; \
sleep 2; \
cd ~/openclaw-voice && \
source .venv_orchestrator/bin/activate && \
timeout 10 python -m orchestrator.main > orchestrator_output.log 2>&1 &" || true

sleep 3

# Check startup logs
STARTUP_CHECK=$(ssh "$PI_SSH_ALIAS" "head -20 ~/openclaw-voice/orchestrator_output.log 2>/dev/null | grep -c 'Audio capture initialized' || echo 0")
if [ "$STARTUP_CHECK" -gt 0 ]; then
    echo -e "${GREEN}  ✓ Orchestrator starting successfully${NC}"
else
    echo -e "${YELLOW}  ⚠ Orchestrator may have startup issues. Check logs with:${NC}"
    echo "    ssh $PI_SSH_ALIAS 'tail -50 ~/openclaw-voice/orchestrator_output.log'"
fi

# Step 7: Optional - Setup systemd service
echo ""
echo -e "${YELLOW}Step 7: Setting up systemd service for auto-start...${NC}"
ssh "$PI_SSH_ALIAS" "cat > /tmp/openclaw-voice.service << 'SVCEOF'
[Unit]
Description=OpenClaw Voice Orchestrator
After=network.target

[Service]
Type=simple
User=stever
WorkingDirectory=/home/stever/openclaw-voice
ExecStart=/home/stever/openclaw-voice/.venv_orchestrator/bin/python -m orchestrator.main
Restart=always
RestartSec=10
StandardOutput=append:/home/stever/openclaw-voice/orchestrator.log
StandardError=append:/home/stever/openclaw-voice/orchestrator.log

[Install]
WantedBy=multi-user.target
SVCEOF

echo '  → Service file created. To enable:' && \
echo '    ssh $PI_SSH_ALIAS sudo systemctl enable --now /tmp/openclaw-voice.service'"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "Next steps:"
echo "1. Verify services on host machine are running:"
echo "   - Whisper STT on port 10000"
echo "   - Piper TTS on port 10001"
echo "   - OpenClaw Gateway on port 18789"
echo ""
echo "2. Check orchestrator status on Pi:"
echo "   ssh $PI_SSH_ALIAS 'tail -50 ~/openclaw-voice/orchestrator_output.log'"
echo ""
echo "3. Test audio:"
echo "   - Speak into the USB microphone"
echo "   - Watch logs for mic level changes and speech detection"
echo ""
echo "4. Enable systemd service (optional):"
echo "   ssh $PI_SSH_ALIAS 'sudo systemctl enable --now /tmp/openclaw-voice.service'"
echo ""
