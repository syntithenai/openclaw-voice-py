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
echo -e "${YELLOW}Prerequisites Check:${NC}"
echo "  • Raspberry Pi with Raspbian/Raspberry Pi OS"
echo "  • Network connectivity to Pi"
echo "  • USB audio devices connected to Pi"
echo "  • Services running on this host ($LOCAL_HOST_IP):"
echo "    - Whisper STT on port 10000"
echo "    - Piper TTS on port 10001"
echo "    - OpenClaw Gateway on port 18789"
echo ""
echo -e "${GREEN}Proceeding with deployment...${NC}"
echo ""

# Step 1: Setup SSH autologin
echo -e "${YELLOW}Step 1: Setting up SSH autologin...${NC}"
if [ ! -f ~/.ssh/id_ed25519 ]; then
    echo "  → Generating ed25519 SSH key..."
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "openclaw-voice@$(hostname)"
else
    echo "  ✓ SSH key already exists"
fi

# Add/update host in ~/.ssh/config
if ! grep -q "^Host $PI_SSH_ALIAS$" ~/.ssh/config 2>/dev/null; then
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
    CURRENT_ALIAS_IP=$(ssh -G "$PI_SSH_ALIAS" 2>/dev/null | awk '/^hostname / {print $2; exit}')
    if [ "$CURRENT_ALIAS_IP" != "$PI_IP" ]; then
        echo "  → Updating SSH alias '$PI_SSH_ALIAS' HostName: $CURRENT_ALIAS_IP → $PI_IP"
        if [ -f ~/.ssh/config ]; then
            awk -v host="$PI_SSH_ALIAS" -v ip="$PI_IP" '
                BEGIN { in_host=0 }
                {
                    if ($1 == "Host") {
                        in_host = 0
                        for (i = 2; i <= NF; i++) {
                            if ($i == host) {
                                in_host = 1
                            }
                        }
                    }

                    if (in_host && $1 == "HostName") {
                        print "    HostName " ip
                        next
                    }

                    print
                }
            ' ~/.ssh/config > ~/.ssh/config.tmp && mv ~/.ssh/config.tmp ~/.ssh/config
            chmod 600 ~/.ssh/config 2>/dev/null || true
        fi
    else
        echo "  ✓ SSH config entry already exists"
    fi
fi

# Copy SSH key to Pi (only if key auth is not already working)
if timeout 5 ssh -o BatchMode=yes "$PI_SSH_ALIAS" "echo ok" >/dev/null 2>&1; then
    echo "  ✓ SSH key auth already working"
else
    echo "  → Copying SSH key to Pi (password may be required once)..."
    ssh-keyscan -t ed25519 "$PI_IP" >> ~/.ssh/known_hosts 2>/dev/null || true
    ssh-copy-id -i ~/.ssh/id_ed25519.pub -o ConnectTimeout=5 "$PI_USER@$PI_IP" || {
        echo -e "${RED}  ✗ SSH key installation failed.${NC}"
        echo -e "${YELLOW}  → If prompted, enter the Pi user's password once to enable autologin.${NC}"
        echo -e "${YELLOW}  → You can also run manually: ssh-copy-id -i ~/.ssh/id_ed25519.pub $PI_USER@$PI_IP${NC}"
    }
fi

# Test SSH connection (retry for transient network/auth delays)
SSH_OK=false
for attempt in 1 2 3 4 5; do
    if timeout 10 ssh "$PI_SSH_ALIAS" "echo 'SSH connection successful'" >/dev/null 2>&1; then
        SSH_OK=true
        break
    fi
    sleep 1
done

if [ "$SSH_OK" = true ]; then
    echo -e "${GREEN}  ✓ SSH autologin configured${NC}"
else
    echo -e "${RED}  ✗ SSH connection failed after retries. Please check credentials and connectivity.${NC}"
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

# Step 2b: Sync artifacts (models and binaries)
echo ""
echo -e "${YELLOW}Step 2b: Syncing artifacts to Pi...${NC}"
if [ -f "./sync_artifacts_to_pi.sh" ]; then
    bash ./sync_artifacts_to_pi.sh "$PI_SSH_ALIAS" auto || {
        echo -e "${RED}  ✗ Artifact sync failed${NC}"
        echo -e "${YELLOW}  → You may need to build artifacts first:${NC}"
        echo -e "${YELLOW}     ./build_precise_engine_armv7.sh (for ARMv7)${NC}"
        exit 1
    }
else
    echo -e "${YELLOW}  ⚠ sync_artifacts_to_pi.sh not found, skipping artifact sync${NC}"
    echo -e "${YELLOW}  → You may need to manually sync artifacts later${NC}"
fi

# Step 3: Run installation script on Pi
echo ""
echo -e "${YELLOW}Step 3: Installing dependencies on Pi...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && bash install_raspbian.sh << EOF
y
default
default
ws://$LOCAL_HOST_IP:18789

http://$LOCAL_HOST_IP:10000
http://$LOCAL_HOST_IP:10001
en_US-amy-medium
1.2
0.15
docker/wakeword-models/hey-mycroft.pb
webrtc
1
INFO
n
EOF
" || echo -e "${YELLOW}Note: Install script completed with warnings${NC}"

# Step 4: Detect and configure audio devices
echo ""
echo -e "${YELLOW}Step 4: Detecting audio devices...${NC}"
PI_ARCH=$(ssh "$PI_SSH_ALIAS" "uname -m" 2>/dev/null || echo "unknown")
USB_MIC=$(ssh "$PI_SSH_ALIAS" "arecord -l | grep -E 'USB Camera|USB Audio' | grep -m1 'card' | sed -E 's/.*card ([0-9]+).*device ([0-9]+).*/hw:\1,\2/'" || echo "default")
USB_SPEAKER=$(ssh "$PI_SSH_ALIAS" "aplay -l | grep -E 'CD002|USB Audio' | grep -m1 'card' | sed -E 's/.*card ([0-9]+).*device ([0-9]+).*/hw:\1,\2/'" || echo "default")
USB_MIC_NAME=$(ssh "$PI_SSH_ALIAS" "arecord -l | grep -E 'USB Camera|USB Audio' | grep -m1 'card' | sed -E 's/.*\[(.*)\].*/\1/'" || echo "default")
USB_SPEAKER_NAME=$(ssh "$PI_SSH_ALIAS" "aplay -l | grep -E 'CD002|USB Audio' | grep -m1 'card' | sed -E 's/.*\[(.*)\].*/\1/'" || echo "default")

if [[ "$PI_ARCH" == "armv7l" || "$PI_ARCH" == "armv6l" ]]; then
    # ARMv7: Use Precise engine (Mycroft)
    PRECISE_ENABLED="true"
    PRECISE_MODEL_PATH="docker/wakeword-models/hey-mycroft.pb"
    PRECISE_WAKE_WORD="hey-mycroft"
    PRECISE_CONFIDENCE="0.15"
    OPENWAKEWORD_MODEL_PATH=""
    echo -e "${GREEN}  ✓ ARMv7 detected: Using Precise engine${NC}"
    echo -e "${GREEN}    - Model: hey-mycroft.pb${NC}"
    echo -e "${GREEN}    - Confidence: 0.15 (0.1-0.2 range, lower=more sensitive)${NC}"
else
    # ARMv8/ARM64: Use OpenWakeWord (TFLite)
    PRECISE_ENABLED="false"
    PRECISE_MODEL_PATH=""
    PRECISE_WAKE_WORD=""
    PRECISE_CONFIDENCE=""
    OPENWAKEWORD_MODEL_PATH="hey_mycroft"
    echo -e "${GREEN}  ✓ $PI_ARCH detected: Using OpenWakeWord${NC}"
    echo -e "${GREEN}    - Model: hey_mycroft (auto-download)${NC}"
    echo -e "${GREEN}    - Confidence: 0.50-0.95 (higher=more sensitive)${NC}"
fi

if [ "$USB_MIC" != "default" ]; then
    echo -e "${GREEN}  ✓ USB Microphone detected: $USB_MIC_NAME ($USB_MIC)${NC}"
else
    echo -e "${YELLOW}  ⚠ USB Microphone not detected, using default${NC}"
fi

if [ "$USB_SPEAKER" != "default" ]; then
    echo -e "${GREEN}  ✓ USB Speaker detected: $USB_SPEAKER_NAME ($USB_SPEAKER)${NC}"
    # Set speaker volume to 50% to prevent acoustic feedback
    SPEAKER_CARD=$(echo "$USB_SPEAKER" | cut -d: -f2 | cut -d, -f1)
    ssh "$PI_SSH_ALIAS" "amixer -c $SPEAKER_CARD sset PCM 50% 2>/dev/null || true"
    echo -e "${GREEN}  ✓ Speaker volume set to 50%${NC}"
else
    echo -e "${YELLOW}  ⚠ USB Speaker not detected, using default${NC}"
fi

# Step 5: Configure .env on Pi
echo ""
echo -e "${YELLOW}Step 5: Configuring .env for remote services...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && cat > .env << 'ENVEOF'
# ==============================================================================
# AUDIO CONFIGURATION
# ==============================================================================
AUDIO_SAMPLE_RATE=16000
AUDIO_PLAYBACK_SAMPLE_RATE=48000
AUDIO_FRAME_MS=20
AUDIO_OUTPUT_GAIN=2.0
# ==============================================================================
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

# ==============================================================================
# WAKE WORD DETECTION
# ==============================================================================
WAKE_WORD_ENABLED=true
WAKE_WORD_TIMEOUT_MS=6000

# ARMv7: Precise engine (configured above based on arch detection)
PRECISE_ENABLED=$PRECISE_ENABLED
PRECISE_MODEL_PATH=$PRECISE_MODEL_PATH
PRECISE_WAKE_WORD=$PRECISE_WAKE_WORD
PRECISE_CONFIDENCE=$PRECISE_CONFIDENCE

# ARMv8/ARM64: OpenWakeWord (configured above based on arch detection)
OPENWAKEWORD_MODEL_PATH=$OPENWAKEWORD_MODEL_PATH
OPENWAKEWORD_AUTO_DOWNLOAD=true
OPENWAKEWORD_MODELS_DIR=docker/wakeword-models

# Wake word advanced settings
WAKE_SLEEP_COOLDOWN_MS=3000
WAKE_MIN_DETECT_RMS=0.0020
WAKE_CLEAR_RING_BUFFER=false

# ==============================================================================
# ACOUSTIC ECHO CANCELLATION (AEC)
# ==============================================================================
ECHO_CANCEL=true
ECHO_CANCEL_WEBRTC_AEC_STRENGTH=strong

# ==============================================================================
# SPEECH CHUNKING
# ==============================================================================
CHUNK_MAX_MS=10000
PRE_ROLL_MS=1500
CUT_IN_PRE_ROLL_MS=100

# ==============================================================================
# TEXT-TO-SPEECH (PIPER)
# ==============================================================================
PIPER_URL=http://$LOCAL_HOST_IP:10001
PIPER_VOICE_ID=en_US-amy-medium
PIPER_SPEED=1.2
GATEWAY_TTS_FAST_START_WORDS=0
AUDIO_PLAYBACK_LEAD_IN_MS=700
AUDIO_PLAYBACK_KEEPALIVE_ENABLED=true
AUDIO_PLAYBACK_KEEPALIVE_INTERVAL_MS=250

# ==============================================================================
# SPEECH-TO-TEXT (WHISPER)
# ==============================================================================
WHISPER_URL=http://$LOCAL_HOST_IP:10000

# ==============================================================================
# OPENCLAW GATEWAY
# ==============================================================================
OPENCLAW_GATEWAY_URL=http://$LOCAL_HOST_IP:18789
GATEWAY_DEBOUNCE_MS=2000
VOICE_CLAW_PROVIDER=openclaw
GATEWAY_AUTH_TOKEN=153c732bd3b98e9525600393b0a6554557027ba4aac11085fbe1fd3dea001aa5
GATEWAY_AGENT_ID=voice
VOICE_SESSION_PREFIX=voiceorch

# ==============================================================================
# EMOTION DETECTION (OPTIONAL)
# ==============================================================================
EMOTION_ENABLED=false
EMOTION_MODEL=sensevoice-small
EMOTION_TIMEOUT_MS=300
EMOTION_AUTO_DOWNLOAD=true
MODELSCOPE_CACHE=/home/stever/.cache/modelscope
EMOTION_MODELS_DIR=docker/emotion-models
SENSEVOICE_MODEL_PATH=iic/SenseVoiceSmall
ENVEOF
echo '  ✓ .env configured with host IP: $LOCAL_HOST_IP'"

# Ensure wakeword resources are available for all engines
echo ""
echo -e "${YELLOW}Step 5b: Ensuring wakeword resources...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && bash ./ensure_wakeword_resources.sh all || true"

# Step 6: Clear Python cache and restart
echo ""
echo -e "${YELLOW}Step 6: Clearing Python cache...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && \
find orchestrator -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
find orchestrator -name '*.pyc' -delete 2>/dev/null || true; \
echo '  ✓ Cache cleared'"

# Step 7: Test orchestrator startup
echo ""
echo -e "${YELLOW}Step 7: Testing orchestrator startup...${NC}"
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

# Step 8: Optional - Setup systemd service
echo ""
echo -e "${YELLOW}Step 8: Setting up systemd service for auto-start...${NC}"
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
