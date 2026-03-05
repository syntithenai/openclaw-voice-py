# Quick Deployment Guide for New Raspberry Pi

## Prerequisites Checklist

### On Build Machine (Current Host)
- [x] Git repository with latest code
- [x] `install_raspbian_remote.sh` script ready
- [x] `sync_artifacts_to_pi.sh` script ready
- [x] `.env.comprehensive` configuration template
- [x] ARMv7 artifacts ready:
  - `artifacts/precise-engine-armv7/precise-engine.tar.gz` (162M)
  - `docker/wakeword-models/hey-mycroft.pb` (26K)
  - `docker/wakeword-models/hey-mycroft.pb.params` (132 bytes)

### Services Running on 10.1.1.249
- [ ] Whisper STT service on port 10000
- [ ] Piper TTS service on port 10001
- [ ] OpenClaw Gateway on port 18789

### New Raspberry Pi
- [ ] Raspbian/Raspberry Pi OS installed
- [ ] Network connectivity (connected to same network)
- [ ] SSH enabled
- [ ] Username: stever (or adjust scripts accordingly)
- [ ] USB audio devices connected (microphone and speaker)

## Deployment Steps

### Step 1: Test Connectivity

```bash
# Identify new Pi IP address (e.g., 10.1.1.211)
NEW_PI_IP=10.1.1.211

# Test SSH connection (will require password on first connection)
ssh stever@$NEW_PI_IP "echo 'Connection successful'"
```

### Step 2: Run Automated Installation

```bash
# Run the install script with new Pi IP
./install_raspbian_remote.sh $NEW_PI_IP
```

**This script will automatically:**
1. Setup SSH autologin (ed25519 key)
2. Clone the repository to Pi
3. **Sync artifacts** (Precise engine + models) based on detected architecture
4. Install Python dependencies
5. Detect and configure audio devices
6. Generate `.env` with correct settings for architecture:
   - **ARMv7**: Precise engine, hey-mycroft.pb, confidence 0.15
   - **ARM64**: OpenWakeWord, hey_mycroft model, confidence 0.50-0.95
7. Configure service URLs (Whisper, Piper, Gateway)
8. Test orchestrator startup

### Step 3: Manual Artifact Sync (If Needed)

If the automatic sync fails or you need to re-sync:

```bash
# Sync artifacts to new Pi
./sync_artifacts_to_pi.sh stever@$NEW_PI_IP auto
```

### Step 4: Pull Latest Code on New Pi

```bash
# SSH to the new Pi
ssh stever@$NEW_PI_IP

# Navigate to project directory
cd ~/openclaw-voice

# Pull latest changes
git pull origin master

# If you haven't committed your local changes yet:
# git fetch origin
# git reset --hard origin/master
```

### Step 5: Verify Installation

```bash
# Check orchestrator logs
ssh stever@$NEW_PI_IP "tail -50 ~/openclaw-voice/orchestrator_output.log"

# Look for:
# - "Audio capture initialized"
# - "Wake Word detector loaded"
# - No errors or tracebacks
```

### Step 6: Test Audio

```bash
# Monitor logs for wake word detection
ssh stever@$NEW_PI_IP "tail -f ~/openclaw-voice/orchestrator_output.log | grep --line-buffered -E 'Wake|confidence|SLEEP|AWAKE|TTS'"

# Speak "Hey Mycroft" into the microphone
# You should see:
# - Wake word detection messages
# - Speech transcription
# - TTS playback
```

### Step 7: Enable Autostart (Optional)

```bash
# Enable systemd service for auto-start on boot
ssh stever@$NEW_PI_IP "sudo cp /tmp/openclaw-voice.service /etc/systemd/system/ && \
  sudo systemctl enable openclaw-voice.service && \
  sudo systemctl start openclaw-voice.service"

# Check service status
ssh stever@$NEW_PI_IP "sudo systemctl status openclaw-voice.service"
```

## Configuration Notes

### Working Configuration (from Pi 10.1.1.210)

**Hardware:**
- USB Microphone: hw:2,0 (USB Camera-B4.09.24.1)
- USB Speaker: hw:2,0 (same device)
- Architecture: ARMv7 (Raspberry Pi 3/Zero 2)

**Wake Word Settings (ARMv7):**
```env
PRECISE_ENABLED=true
PRECISE_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb
PRECISE_WAKE_WORD=hey-mycroft
PRECISE_CONFIDENCE=0.15
WAKE_WORD_TIMEOUT_MS=6000
```

**Audio Settings:**
```env
AUDIO_SAMPLE_RATE=16000
AUDIO_PLAYBACK_SAMPLE_RATE=48000
ECHO_CANCEL=true
ECHO_CANCEL_WEBRTC_AEC_STRENGTH=strong
```

**Service URLs:**
```env
WHISPER_URL=http://10.1.1.249:10000
PIPER_URL=http://10.1.1.249:10001
OPENCLAW_GATEWAY_URL=http://10.1.1.249:18789
```

## Architecture Differences

### ARMv7 (Pi 3, Zero 2)
- **Wake Engine**: Precise (Mycroft)
- **Model**: File-based (`hey-mycroft.pb`)
- **Confidence**: 0.10-0.20 (lower = more sensitive)
- **Requires**: Pre-built `precise-engine` binary (162M tarball)
- **Auto-configured by**: `install_raspbian_remote.sh`

### ARM64 (Pi 4, Pi 5)
- **Wake Engine**: OpenWakeWord (TFLite)
- **Model**: String reference (`hey_mycroft`)
- **Confidence**: 0.50-0.95 (higher = more sensitive)
- **Requires**: Python packages only (no binary needed)
- **Auto-configured by**: `install_raspbian_remote.sh`

## Troubleshooting

### Artifact Sync Fails
```bash
# Manually check if artifacts exist locally
ls -lh artifacts/precise-engine-armv7/precise-engine.tar.gz
ls -lh docker/wakeword-models/hey-mycroft.pb

# If missing, build them:
./build_precise_engine_armv7.sh

# Then re-sync:
./sync_artifacts_to_pi.sh stever@$NEW_PI_IP armv7
```

### Wake Word Not Detecting
```bash
# Check if Precise engine is properly installed
ssh stever@$NEW_PI_IP "ls -lh ~/openclaw-voice/precise-engine/precise-engine"

# Check if it's linked to venv
ssh stever@$NEW_PI_IP "ls -lh ~/openclaw-voice/.venv_orchestrator/bin/precise-engine"

# If missing, re-extract:
ssh stever@$NEW_PI_IP "cd ~/openclaw-voice && \
  tar -xzf precise-engine.tar.gz && \
  ln -sf \$PWD/precise-engine/precise-engine .venv_orchestrator/bin/precise-engine"
```

### Audio Device Issues
```bash
# List audio devices on Pi
ssh stever@$NEW_PI_IP "arecord -l"  # Microphones
ssh stever@$NEW_PI_IP "aplay -l"    # Speakers

# Update .env with correct hw:X,Y values
ssh stever@$NEW_PI_IP "nano ~/openclaw-voice/.env"
```

### Service URLs Unreachable
```bash
# Test connectivity from Pi to services
ssh stever@$NEW_PI_IP "curl -v http://10.1.1.249:10000/health"  # Whisper
ssh stever@$NEW_PI_IP "curl -v http://10.1.1.249:10001/health"  # Piper
ssh stever@$NEW_PI_IP "curl -v http://10.1.1.249:18789/health"  # Gateway

# If unreachable, check firewall and network configuration
```

## Files Not in Git (Must Sync Separately)

These large binary files are essential but excluded from git:

1. **Precise Engine for ARMv7** (162M)
   - Location: `artifacts/precise-engine-armv7/precise-engine.tar.gz`
   - Contains: Binary + all dependencies (TensorFlow, SciPy, NumPy, etc.)
   - Synced by: `sync_artifacts_to_pi.sh` or `install_raspbian_remote.sh`

2. **Wake Word Models** (~26K)
   - Location: `docker/wakeword-models/hey-mycroft.pb`
   - Location: `docker/wakeword-models/hey-mycroft.pb.params`
   - Synced by: `sync_artifacts_to_pi.sh` or `install_raspbian_remote.sh`

## Summary

**You are now ready to deploy!**

The complete deployment process has been automated and tested. Key improvements:

✅ Architecture detection (ARMv7 vs ARM64)  
✅ Automatic artifact syncing  
✅ Working configuration from Pi 10.1.1.210  
✅ Proper wake word settings per architecture  
✅ Audio device auto-detection  
✅ Service URL configuration  
✅ Git + rsync strategy for code + artifacts  

**To deploy to a new Pi with the same hardware:**

```bash
./install_raspbian_remote.sh <new_pi_ip>
```

That's it! The script handles everything automatically.
