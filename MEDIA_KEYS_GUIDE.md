# Media Key Detection Guide

This guide explains how to use hardware media buttons (from USB/Bluetooth conference speakers, headsets, keyboards) for wake/sleep controls while leaving volume on system audio controls.

## Overview

The media key detection system uses the Linux `evdev` library to capture button presses from hardware devices and translates them into orchestrator actions.

### Supported Buttons

- **Play/Pause** - Toggle wake/sleep
- **Stop** - Optional MPD stop (when MPD media-key control is enabled)
- **Next** - Same wake/sleep toggle behavior as play
- **Previous** - Same wake/sleep toggle behavior as play
- **Volume Up** - Increase system output volume
- **Volume Down** - Decrease system output volume
- **Mute** - Toggle microphone mute
- **Phone** - Trigger wake

## Setup

### 1. Install Dependencies

The `evdev` library is already added to `requirements.txt`:

```bash
pip install evdev
```

### 2. Add Permissions

Linux requires special permissions to read input devices. You have two options:

#### Option A: Add User to Input Group (Recommended)

```bash
sudo usermod -a -G input $USER
```

**Important:** You must log out and back in for this to take effect!

#### Option B: Run with Sudo (Testing Only)

```bash
sudo python3 test_media_keys.py
```

### 3. Find Your Device

Run the identification script to find your Anker speaker:

```bash
sudo python3 identify_speaker.py
```

This will list all input devices and show which ones have media key capabilities.

### 4. Test Button Detection

Run the interactive test script:

```bash
sudo python3 find_anker_device.py
```

Press buttons on your Anker speaker and watch the output to confirm detection is working.

### 5. Configure Orchestrator

Add these settings to your `.env` file:

```bash
# Media Key Detection
MEDIA_KEYS_ENABLED=true
MEDIA_KEYS_DEVICE_FILTER=Burr-Brown  # Optional: substring to filter device names
MEDIA_KEYS_CONTROL_MUSIC=false       # Keep media keys from controlling MPD volume/playback
MEDIA_KEYS_EXCLUSIVE_GRAB=false      # Let OS keep handling volume keys/LED state
MEDIA_KEYS_PASSTHROUGH_KEYS=volume_up,volume_down,mute
```

The `MEDIA_KEYS_DEVICE_FILTER` is optional but helpful if you have multiple devices. Use a substring from your device name (e.g., "Anker", "USB", "Conference", "Burr-Brown").

### 6. Enable Music System

Media keys require the music system to be enabled:

```bash
# Music Control (MPD)
MUSIC_ENABLED=true
MPD_HOST=localhost
MPD_PORT=6600
```

## Testing

### Step 1: Test Media Key Detection

```bash
# Add yourself to input group (one time only)
sudo usermod -a -G input $USER

# Log out and back in, then test without sudo:
python3 test_media_keys.py

# Press buttons on your Anker speaker
```

### Step 2: Test with Orchestrator

1. Start the orchestrator:
   ```bash
   ./run_voice_demo.sh
   ```

2. Check the logs for media key detector initialization:
   ```bash
   tail -f orchestrator_output.log | grep -i "media"
   ```

3. Press buttons on your Anker speaker - they should control MPD playback

## Troubleshooting

### No Devices Found

**Symptoms:**
- "No media key devices found"
- Empty device list in `identify_speaker.py`

**Solutions:**
1. Check device is connected: `ls -la /dev/input/by-id/`
2. Check permissions: `ls -la /dev/input/event*`
3. Add yourself to input group: `sudo usermod -a -G input $USER`
4. Log out and back in
5. Try with sudo as a test: `sudo python3 find_anker_device.py`

### Device Found But No Button Events

**Symptoms:**
- Device appears in `identify_speaker.py`
- No events when pressing buttons

**Solutions:**
1. Check if another process is grabbing the device
2. Try different USB port
3. Check if Bluetooth is properly paired
4. Some buttons may use different protocols (not HID)

### Permission Denied Errors

**Symptoms:**
- `PermissionError: [Errno 13] Permission denied: '/dev/input/event3'`

**Solutions:**
1. Add yourself to input group: `sudo usermod -a -G input $USER`
2. Log out and back in (group membership only updates on login)
3. Verify: `groups | grep input`
4. Temporary workaround: Run with sudo

### Buttons Detected But Expected Action Doesn't Happen

**Symptoms:**
- Button presses logged
- No expected wake/sleep or volume behavior happens

**Solutions:**
1. Check `MEDIA_KEYS_ENABLED=true` in `.env`
2. Check `MEDIA_KEYS_EXCLUSIVE_GRAB` and `MEDIA_KEYS_PASSTHROUGH_KEYS` values
3. If using exclusive grab, ensure `wpctl`, `pactl`, or `amixer` exists for system volume changes
4. Check orchestrator logs for media-key detector initialization errors

## Architecture

### Components

1. **MediaKeyDetector** (`orchestrator/audio/media_keys.py`)
   - Scans for input devices with media key capabilities
   - Monitors devices for button press events
   - Calls callback when buttons are pressed

2. **Integration** (`orchestrator/main.py`)
   - Initializes detector when `MEDIA_KEYS_ENABLED=true`
    - Connects play/phone events to wake/sleep flow
    - Routes volume events to OS volume behavior (not MPD volume)
   - Manages lifecycle (start/stop)

3. **Configuration** (`orchestrator/config.py`)
   - `media_keys_enabled` - Enable/disable feature
   - `media_keys_device_filter` - Optional device name filter
    - `media_keys_exclusive_grab` - Whether to exclusively grab input device
    - `media_keys_passthrough_keys` - Keys re-injected to OS when grabbed

### Event Flow

```
Hardware Button Press
    ↓
Linux Input Device (/dev/input/eventX)
    ↓
evdev Library
    ↓
MediaKeyDetector.detect()
    ↓
Callback: on_media_key_press()
    ↓
Wake/Sleep Handler or System Volume Command
    ↓
Audio Output Changes
```

## Customization

### Add Custom Button Actions

Edit the `on_media_key_press()` callback in `orchestrator/main.py`:

```python
def on_media_key_press(event: MediaKeyEvent):
    logger.info("Media key pressed: %s", event.key)
    
    # Your custom logic here
    if event.key == "phone":
        # Do something when phone button is pressed
        logger.info("Phone button pressed!")
    
    # Call music manager
    if event.key == "play_pause":
        asyncio.create_task(music_manager.pause())
```

### Filter Specific Devices

Use `MEDIA_KEYS_DEVICE_FILTER` to only monitor specific devices:

```bash
# Only monitor devices with "Anker" in the name
MEDIA_KEYS_DEVICE_FILTER=Anker

# Only monitor USB devices
MEDIA_KEYS_DEVICE_FILTER=USB

# Monitor all devices (empty = no filter)
MEDIA_KEYS_DEVICE_FILTER=
```

### Adjust Volume Steps

Volume-step behavior is handled through system mixers (`wpctl`/`pactl`/`amixer`) in `orchestrator/main.py` and currently uses 5% steps.

## Security Notes

- **Input Group Access**: Members of the `input` group can read ALL keyboard input, including passwords. Only add trusted users.
  
- **Exclusive Grab**: The detector can optionally grab exclusive access to the device (preventing OS from also handling events). This is currently commented out in the code.

- **Device Filtering**: Use `MEDIA_KEYS_DEVICE_FILTER` to limit which devices are monitored, reducing security surface.

## Known Limitations

1. **Linux Only**: `evdev` is Linux-specific. Will not work on Windows/macOS.

2. **Permission Required**: Must be in `input` group or run as root.

3. **USB/Bluetooth Only**: Analog audio connections don't support button detection.

4. **Device-Specific**: Some devices use proprietary protocols that may not be detected by evdev.

5. **No Analog Buttons**: Buttons that generate audio signals (like inline mic controls on 3.5mm cables) cannot be detected this way.

## References

- [evdev Documentation](https://python-evdev.readthedocs.io/)
- [Linux Input Subsystem](https://www.kernel.org/doc/html/latest/input/input.html)
- [HID Usage Tables](https://usb.org/sites/default/files/hut1_21_0.pdf)
