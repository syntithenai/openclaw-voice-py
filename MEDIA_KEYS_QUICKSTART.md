# Media Key Detection - Quick Start

## What I've Built

I've added hardware button detection for your Anker conference speaker! Now you can control music playback using the physical buttons on your device.

## Files Created

### Core Module
- **orchestrator/audio/media_keys.py** - Media key detection using evdev

### Test Scripts
- **test_media_keys.py** - Simple test to detect button presses
- **identify_speaker.py** - Lists all input devices with media capabilities
- **find_anker_device.py** - Interactive test that shows which device responds
- **setup_media_keys.sh** - Automated setup wizard

### Documentation
- **MEDIA_KEYS_GUIDE.md** - Complete guide with troubleshooting

### Configuration
- **requirements.txt** - Added `evdev` library
- **orchestrator/config.py** - Added media key settings
- **orchestrator/main.py** - Integrated detector into main loop
- **.env** - Added configuration options

## Quick Test

1. **Add yourself to the input group:**
   ```bash
   sudo usermod -a -G input $USER
   ```
   **(YOU MUST LOG OUT AND BACK IN!)**

2. **Or test with sudo:**
   ```bash
   sudo python3 find_anker_device.py
   ```
   
3. **Press buttons on your Anker speaker** - you should see output like:
   ```
   ✨ [Burr-Brown from TI USB Audio CODEC] Button: KEY_PLAYPAUSE
   ✨ [Burr-Brown from TI USB Audio CODEC] Button: KEY_VOLUMEUP
   ```

## Enable in Orchestrator

Edit `.env` and change:

```bash
MEDIA_KEYS_ENABLED=true
MEDIA_KEYS_DEVICE_FILTER=Burr-Brown  # Optional: filter by device name
```

Then restart the orchestrator:

```bash
./run_voice_demo.sh
```

## Supported Buttons

| Button | Action |
|--------|--------|
| Play/Pause | Toggle wake/sleep behavior |
| Next | Same wake/sleep toggle behavior as play |
| Previous | Same wake/sleep toggle behavior as play |
| Volume Up | Adjust system output volume (OS/desktop) |
| Volume Down | Adjust system output volume (OS/desktop) |
| Mute | Toggle microphone mute state |
| Stop | Optional MPD stop (when MPD media key control is enabled) |
| Phone | Trigger wake behavior |

## Button Mapping

The buttons will:
- **Volume +/-** → Control system volume (not MPD volume)
- **Play/Pause** → Trigger wake/sleep flow
- **Next/Previous** → Same wake/sleep flow for button parity
- **Mute** → Toggle mic mute/unmute

## Troubleshooting

### "No media key devices found"

**Problem:** evdev can't access input devices

**Solution:**
```bash
# Add yourself to input group
sudo usermod -a -G input $USER

# Log out and back in (REQUIRED!)
# Then test:
python3 identify_speaker.py
```

### "Permission denied"

**Problem:** Not in input group

**Quick fix:** Run with sudo:
```bash
sudo python3 find_anker_device.py
```

**Permanent fix:** Add yourself to input group (see above)

### Buttons work in test but not in orchestrator

**Check:**
1. `MEDIA_KEYS_ENABLED=true` in `.env`
2. `MUSIC_ENABLED=true` in `.env`
3. MPD is running: `docker ps | grep mpd`
4. Check logs: `tail -f orchestrator_output.log | grep -i media`

## Device Detection

Your system shows this USB audio device:
```
usb-Burr-Brown_from_TI_USB_Audio_CODEC-event-if03 -> ../event3
```

This is likely your Anker speaker. Use this filter in `.env`:
```bash
MEDIA_KEYS_DEVICE_FILTER=Burr-Brown
```

## Next Steps

1. **Test basic detection:**
   ```bash
   sudo python3 find_anker_device.py
   # Press buttons on your speaker
   ```

2. **Add yourself to input group:**
   ```bash
   sudo usermod -a -G input $USER
   # Log out and back in
   ```

3. **Enable in orchestrator:**
   ```bash
   # Edit .env:
   MEDIA_KEYS_ENABLED=true
   
   # Restart:
   ./run_voice_demo.sh
   ```

4. **Test with music playing:**
   - Start orchestrator
   - Say "play some music"
   - Press buttons on your Anker speaker
   - Volume and playback should respond!

## Architecture

The flow is:
```
Button Press on Anker Speaker
    ↓
/dev/input/event3 (Linux input device)
    ↓
evdev library reads event
    ↓
MediaKeyDetector identifies media key
    ↓
on_media_key_press() callback
    ↓
MusicManager.pause() / .next_track() / etc.
    ↓
MPD command sent
    ↓
Music playback changes
```

## Security Note

⚠️ Users in the `input` group can read ALL keyboard input, including passwords. Only add trusted users to this group. Use `MEDIA_KEYS_DEVICE_FILTER` to limit which devices are monitored.

## Questions?

- See **MEDIA_KEYS_GUIDE.md** for detailed documentation
- Run `sudo python3 identify_speaker.py` to see all your devices
- Check logs: `tail -f orchestrator_output.log | grep -i media`
