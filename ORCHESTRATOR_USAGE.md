# Orchestrator Usage Guide

## Default Configuration (ONE orchestrator runs at a time)

By default, **only `orchestrator-linux-alsa` runs** when you start services:
```bash
docker compose up -d
```

This starts:
- ✅ whisper (auto backend: CPU by default, GPU if exposed)
- ✅ piper (auto backend: CPU by default, GPU if exposed)
- ✅ **orchestrator-linux-alsa** (ALSA direct hardware)

## Why ALSA is Default

The ALSA orchestrator is the **only working option** for Linux desktop audio:

### Working: ✅ orchestrator-linux-alsa
- Uses direct `/dev/snd` hardware access
- PortAudio with ALSA backend can enumerate devices
- **This is the recommended orchestrator for Linux**

### Not Working: ❌ orchestrator-linux-pulse
- Requires PulseAudio support in PortAudio
- Docker container's PortAudio lacks PulseAudio backend
- Cannot enumerate audio devices even with socket mounted
- **Don't use this unless you rebuild PortAudio with PulseAudio**

### Not Recommended: ⚠️ orchestrator (base)
- Generic cross-platform container
- No special audio device access
- Profile: `manual` (doesn't auto-start)

## Manual Control

### Start specific orchestrator:
```bash
# Start ALSA (default - already running)
docker compose up -d orchestrator-linux-alsa

# Start base orchestrator (manual profile)
docker compose --profile manual up -d orchestrator

# Start pulse orchestrator (manual profile - will fail)
docker compose --profile manual up -d orchestrator-linux-pulse
```

### Stop all orchestrators:
```bash
docker compose stop orchestrator orchestrator-linux-alsa orchestrator-linux-pulse
```

### Check which orchestrator is running:
```bash
docker compose ps | grep orchestrator
```

## Important Rules

⚠️ **Only run ONE orchestrator at a time**
- Multiple orchestrators will conflict over audio devices
- If switching, stop the current one first:
  ```bash
  docker compose stop orchestrator-linux-alsa
  docker compose up -d <other-orchestrator>
  ```

## Audio Device Configuration

All orchestrators now use:
```yaml
environment:
  - AUDIO_CAPTURE_DEVICE=0
  - AUDIO_PLAYBACK_DEVICE=0
```

This uses the first ALSA device (device index 0) for both input and output.

## Troubleshooting

### "No compatible audio devices found"
- You're using orchestrator-linux-pulse (doesn't work)
- Switch to orchestrator-linux-alsa

### Multiple orchestrators running
```bash
docker compose ps | grep orchestrator
docker compose stop <orchestrator-name>
```

### Check device enumeration
```bash
# ALSA orchestrator (should see 7+ devices):
docker compose run --rm orchestrator-linux-alsa python3 -c \
  "import sounddevice as sd; print(sd.query_devices())"

# Pulse orchestrator (will see 0 devices):
docker compose run --rm orchestrator-linux-pulse python3 -c \
  "import sounddevice as sd; print(sd.query_devices())"
```
