#!/bin/bash
# Remote Installation Quickstart

## Usage

```bash
./install_raspbian_remote.sh 10.1.1.210
```

This will:
1. Set up SSH autologin (ed25519 key)
2. Clone OpenClaw Voice repository from GitHub
3. Install all dependencies
4. Configure `.env` with your host machine's IP for Piper, Whisper, and Gateway
5. Set up 50x software input gain for the USB microphone
6. Create a systemd service (optional)

## Prerequisites

- Raspberry Pi 3+ with Raspbian OS
- Pi has connected USB microphone and speakers
- Pi has internet connectivity
- Your host machine is running:
  - **Whisper STT** on port 10000
  - **Piper TTS** on port 10001  
  - **OpenClaw Gateway** on port 18789

## Wake Word Detection Issue

⚠️ **Wake word detection is NOT available on Raspberry Pi 3 (ARMv7 32-bit)**

### Why?
- Wake word detection uses `openwakeword` which depends on `onnxruntime`
- `onnxruntime` wheels are not available for Python 3.11 on ARMv7 (32-bit ARM)
- The package is therefore moved to optional dependencies and disabled by default

### Current Configuration
On the Pi, `WAKE_WORD_ENABLED=false` because the library cannot be installed.

### Workarounds

**Option 1: Disable Wake Word (Default)**
- Orchestrator always listens for speech
- No wake word needed to trigger
- Voice Activity Detection (WebRTC VAD) triggers processing whenever speech is detected
- This is the current configuration and works well with 50x input gain

**Option 2: Use 64-bit OS**
If you can install 64-bit Raspbian (Raspberry Pi OS Lite 64-bit):
- Set `WAKE_WORD_ENABLED=true` in `.env`
- Install openwakeword and onnxruntime from PyPI
- This will enable "hey mycroft" wake word detection

**Option 3: Process Wake Word on Host**
- Keep orchestrator on Pi listening without wake word
- Run wake word detection on host machine instead
- Send transcriptions to host for full processing

## Network Configuration

The script configures the Pi to use your host machine's IP for all remote services:

```env
WHISPER_URL=http://<your_host_ip>:10000
PIPER_URL=http://<your_host_ip>:10001
OPENCLAW_GATEWAY_URL=http://<your_host_ip>:18789
```

This assumes:
- Pi and host are on ==same network== (can ping each other)
- Services are accessible from the Pi

## Testing

After installation, verify it's working:

```bash
# SSH to Pi
ssh pi

# Check logs
tail -f ~/openclaw-voice/orchestrator_output.log

# Look for:
# - "Audio capture initialized" with 50x gain
# - "Mic level" entries showing dBFS values
# - "OpenClaw WebSocket connected"

# Try speaking - you should see:
# - Mic level spikes when you talk (e.g., -80.0 dBFS instead of -120.0)
# - VAD speech detection logs
# - STT transcription if gateway is connected
# - TTS audio playback through speakers
```

## Architecture

```
┌─────────────────────┐
│  Raspberry Pi 3     │
└─────────────────────┘
  │
  ├─ USB Mic (hw:2,0) ──→ [Audio Capture + 50x Gain]
  │
  ├─ [VAD - WebRTC] ──→ Detects speech (no wake word needed)
  │
  ├─ [Orchestrator Main Loop]
  │      ↓ STT request
  ├─→ HTTP to Whisper (host:10000)
  │      ↓ Transcription
  ├─→ WebSocket to Gateway (host:18789)
  │      ↓ Response
  ├─→ HTTP to Piper (host:10001)
  │      ↓ Audio bytes
  ├─ USB Speakers (hw:3,0) ←── [Audio Playback]
```

## Troubleshooting

### Mic not capturing
- Check device is listed: `arecord -l`
- Verify dBFS values change when you speak
- Current config: 50x software gain should be sufficient

### Orchestrator crashes on startup
- Check cache is cleared: `find orchestrator -type d -name __pycache__ -exec rm -rf {} +`
- Verify Python venv is set up: `source ~/.venv_orchestrator/bin/activate`
- Check audio device ID is correct (usually 1): `python -c "import sounddevice; print([(i,d['name']) for i,d in enumerate(sounddevice.query_devices()) if d['max_input_channels'] > 0])"`

### Services unreachable
- Verify host services are running on correct ports
- Test from Pi: `curl http://<host_ip>:10000/health`
- Check firewall allows connections from Pi

### No response from gateway
- Ensure gateway is available at `http://<host_ip>:18789`
- Check if orchestrator needs pairing approval on gateway
- Logs should show WebSocket connection status

## Files Created/Modified

```
orchestrator/
  config.py          - Added audio_input_gain parameter
  main.py            - Pass input_gain to audio capture
  audio/
    capture.py       - Implement software gain boost
    duplex.py        - Implement software gain boost (if using duplex mode)

.env (on Pi)         - Set gain=50.0, device=1, service URLs
```

## Next Steps

1. Run: `./install_raspbian_remote.sh <pi_ip>`
2. Verify orchestrator is running and capturing audio
3. Start using voice commands (no wake word needed - always listening)
4. If 64-bit OS is available: Enable wake word detection
