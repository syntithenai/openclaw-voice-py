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

# Determine target architecture and wakeword policy before running installer.
PI_ARCH=$(ssh "$PI_SSH_ALIAS" "uname -m" 2>/dev/null || echo "unknown")
if [[ "$PI_ARCH" == "armv7l" || "$PI_ARCH" == "armv6l" ]]; then
    WAKEWORD_ENGINE_CHOICE="precise"
    WAKEWORD_CONFIDENCE="0.15"
    WAKEWORD_MODEL="docker/wakeword-models/hey-mycroft.pb"
else
    WAKEWORD_ENGINE_CHOICE="openwakeword"
    WAKEWORD_CONFIDENCE="0.5"
    WAKEWORD_MODEL="hey_mycroft"
fi

# Step 3: Run installation script on Pi
echo ""
echo -e "${YELLOW}Step 3: Installing dependencies on Pi...${NC}"
ssh "$PI_SSH_ALIAS" "cd ~/openclaw-voice && bash install_raspbian.sh << EOF
y
pulse
pulse
ws://$LOCAL_HOST_IP:18789

http://$LOCAL_HOST_IP:10000
http://$LOCAL_HOST_IP:10001
en_US-amy-medium
1.2
$WAKEWORD_ENGINE_CHOICE
$WAKEWORD_CONFIDENCE
$WAKEWORD_MODEL
webrtc
1
INFO
n
EOF
" || echo -e "${YELLOW}Note: Install script completed with warnings${NC}"

# Step 4: Detect and configure audio devices
echo ""
echo -e "${YELLOW}Step 4: Detecting audio devices...${NC}"
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
    OPENWAKEWORD_ENABLED="false"
    OPENWAKEWORD_MODEL_PATH="hey_mycroft"
    OPENWAKEWORD_WAKE_WORD=""
    OPENWAKEWORD_CONFIDENCE="0.5"
    WAKE_WORD_ENGINE="precise"
    echo -e "${GREEN}  ✓ ARMv7 detected: Using Precise engine${NC}"
    echo -e "${GREEN}    - Model: hey-mycroft.pb${NC}"
    echo -e "${GREEN}    - Confidence: 0.15 (0.1-0.2 range, lower=more sensitive)${NC}"
else
    # ARMv8/ARM64: Use OpenWakeWord (TFLite)
    PRECISE_ENABLED="false"
    PRECISE_MODEL_PATH="docker/wakeword-models/hey-mycroft.pb"
    PRECISE_WAKE_WORD=""
    PRECISE_CONFIDENCE="0.15"
    OPENWAKEWORD_ENABLED="true"
    OPENWAKEWORD_MODEL_PATH="hey_mycroft"
    OPENWAKEWORD_WAKE_WORD="hey_mycroft"
    OPENWAKEWORD_CONFIDENCE="0.5"
    WAKE_WORD_ENGINE="openwakeword"
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
echo -e "${YELLOW}Step 5: Updating .env for remote services (idempotent, non-destructive)...${NC}"
ssh "$PI_SSH_ALIAS" \
  "LOCAL_HOST_IP='$LOCAL_HOST_IP' PRECISE_ENABLED='$PRECISE_ENABLED' PRECISE_MODEL_PATH='$PRECISE_MODEL_PATH' PRECISE_WAKE_WORD='$PRECISE_WAKE_WORD' PRECISE_CONFIDENCE='$PRECISE_CONFIDENCE' OPENWAKEWORD_ENABLED='$OPENWAKEWORD_ENABLED' OPENWAKEWORD_MODEL_PATH='$OPENWAKEWORD_MODEL_PATH' OPENWAKEWORD_WAKE_WORD='$OPENWAKEWORD_WAKE_WORD' OPENWAKEWORD_CONFIDENCE='$OPENWAKEWORD_CONFIDENCE' WAKE_WORD_ENGINE='$WAKE_WORD_ENGINE' bash -s" << 'ENVUPDATE'
set -e
cd ~/openclaw-voice

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    if [ -f ".env.pi" ]; then
        cp .env.pi "$ENV_FILE"
    elif [ -f ".env.pi.example" ]; then
        cp .env.pi.example "$ENV_FILE"
    elif [ -f ".env.example" ]; then
        cp .env.example "$ENV_FILE"
    else
        touch "$ENV_FILE"
    fi
fi

upsert_env_var() {
    local key="$1"
    local value="$2"
    local escaped
    escaped=$(printf '%s' "$value" | sed 's/[&/]/\\&/g')
    if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

# Remote service endpoints (safe to re-apply)
upsert_env_var "AUDIO_CAPTURE_DEVICE" "pulse"
upsert_env_var "AUDIO_PLAYBACK_DEVICE" "pulse"
upsert_env_var "WHISPER_URL" "http://${LOCAL_HOST_IP}:10000"
upsert_env_var "PIPER_URL" "http://${LOCAL_HOST_IP}:10001"
upsert_env_var "PIPER_VOICE_ID" "en_US-amy-medium"
upsert_env_var "PIPER_SPEED" "1.2"
upsert_env_var "OPENCLAW_GATEWAY_URL" "http://${LOCAL_HOST_IP}:18789"
upsert_env_var "GATEWAY_AUTH_TOKEN" "153c732bd3b98e9525600393b0a6554557027ba4aac11085fbe1fd3dea001aa5"
upsert_env_var "GATEWAY_AGENT_ID" "voice"
upsert_env_var "VOICE_SESSION_PREFIX" "voiceorch"

# Force wakeword engine explicitly (single-engine state)
upsert_env_var "WAKE_WORD_ENABLED" "true"
upsert_env_var "WAKE_WORD_ENGINE" "$WAKE_WORD_ENGINE"
upsert_env_var "PRECISE_ENABLED" "$PRECISE_ENABLED"
upsert_env_var "PRECISE_MODEL_PATH" "$PRECISE_MODEL_PATH"
upsert_env_var "PRECISE_WAKE_WORD" "$PRECISE_WAKE_WORD"
upsert_env_var "PRECISE_CONFIDENCE" "$PRECISE_CONFIDENCE"
upsert_env_var "OPENWAKEWORD_ENABLED" "$OPENWAKEWORD_ENABLED"
upsert_env_var "OPENWAKEWORD_MODEL_PATH" "$OPENWAKEWORD_MODEL_PATH"
upsert_env_var "OPENWAKEWORD_WAKE_WORD" "$OPENWAKEWORD_WAKE_WORD"
upsert_env_var "OPENWAKEWORD_CONFIDENCE" "$OPENWAKEWORD_CONFIDENCE"
upsert_env_var "OPENWAKEWORD_AUTO_DOWNLOAD" "true"
upsert_env_var "OPENWAKEWORD_MODELS_DIR" "docker/wakeword-models"
upsert_env_var "PICOVOICE_ENABLED" "false"

echo "  ✓ .env updated in-place with host IP and forced wakeword engine: ${WAKE_WORD_ENGINE}"
ENVUPDATE

ssh "$PI_SSH_ALIAS" "mkdir -p /tmp/openclaw-mpd-fifo && chmod 0777 /tmp/openclaw-mpd-fifo && \
    ( [ -p /tmp/openclaw-mpd-fifo/music.pcm ] || (rm -f /tmp/openclaw-mpd-fifo/music.pcm && mkfifo -m 0666 /tmp/openclaw-mpd-fifo/music.pcm) ) && \
    chmod 0666 /tmp/openclaw-mpd-fifo/music.pcm || true"

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
