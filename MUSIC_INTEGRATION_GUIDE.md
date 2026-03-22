# Music Integration and Media Keys - Complete Guide

## Overview

The orchestrator integrates native music playback control with hardware media keys. The system supports local and browser playback routes, music-aware wake behavior, and automatic resume after voice interruption.

## Key Features

### 1. Music Playback State Management

When music starts playing:
- The orchestrator can sleep during playback (`MUSIC_SLEEP_DURING_PLAYBACK=true`).
- This reduces false transcriptions while music is active.
- Wake word and hardware wake actions still interrupt playback immediately.

When wake word or phone button triggers:
- Music is paused/stopped immediately.
- Wake feedback can play (if configured).
- The system captures voice input.
- Auto-resume timer starts.

Auto-resume behavior:
- If no additional voice activity is detected for `MUSIC_AUTO_RESUME_TIMEOUT_S` seconds,
- Music resumes,
- And orchestrator returns to sleep when enabled.

### 2. Conference Speaker Button Functions

Mute button:
- Toggles microphone mute/unmute.
- Affects microphone capture only, not playback volume.

Play button:
- If queue is empty: add random tracks and start playback.
- If currently playing: pause.
- If paused: resume.

Phone button:
- Triggers wake flow:
  1. Stop/pause active audio.
  2. Unmute mic when needed.
  3. Wake system and begin listening.
  4. Start auto-resume window.

Volume up/down buttons:
- Adjust playback volume in configured step increments.

Next/previous buttons:
- Move through queue.

## Configuration

### Required Settings

```bash
MUSIC_ENABLED=true
MEDIA_PLAYER_BACKEND=native
MEDIA_LIBRARY_ROOT=music
MEDIA_INDEX_DB_PATH=.media/library.sqlite3
PLAYLIST_ROOT=playlists

MEDIA_KEYS_ENABLED=true
# Optional hardware filter
MEDIA_KEYS_DEVICE_FILTER=Burr-Brown
```

### Sleep and Resume Settings

```bash
MUSIC_SLEEP_DURING_PLAYBACK=true
MUSIC_AUTO_RESUME_TIMEOUT_S=5
MUSIC_RANDOM_TRACK_COUNT=50
```

### Example Complete Configuration

```bash
# Music
MUSIC_ENABLED=true
MEDIA_PLAYER_BACKEND=native
MEDIA_LIBRARY_ROOT=music
MEDIA_INDEX_DB_PATH=.media/library.sqlite3
PLAYLIST_ROOT=playlists
MUSIC_SLEEP_DURING_PLAYBACK=true
MUSIC_AUTO_RESUME_TIMEOUT_S=5
MUSIC_RANDOM_TRACK_COUNT=50

# Media keys
MEDIA_KEYS_ENABLED=true
MEDIA_KEYS_DEVICE_FILTER=Burr-Brown
MEDIA_KEYS_CONTROL_MUSIC=true
```

## Workflows

### Workflow 1: Background Music

1. Say "play some rock music".
2. Music starts and orchestrator sleeps.
3. Say wake word.
4. Music pauses/stops and system wakes.
5. Speak command or wait.
6. After timeout, music resumes automatically.

### Workflow 2: Conference Speaker

1. Press play: random queue starts if empty.
2. Press phone: system wakes and listens.
3. Speak command.
4. No further input: music resumes after timeout.
5. Press mute to toggle mic input mute state.

### Workflow 3: Voice Cut-In During TTS

1. TTS starts speaking.
2. User interrupts.
3. TTS stops and music pauses/stops.
4. System captures new command.
5. Auto-resume timer restores music when idle.

## Button Summary

| Button | Action | Details |
|--------|--------|---------|
| Mute | Toggle microphone | Capture mute only |
| Play | Smart play/pause | Adds random tracks if queue empty |
| Phone | Trigger wake sequence | Stops playback and wakes listener |
| Volume Up | Increase volume | Step increase |
| Volume Down | Decrease volume | Step decrease |
| Next | Skip forward | Next queue item |
| Previous | Skip backward | Previous queue item |

## Technical Details

Music state tracking:
- Poll interval is short (around 500ms cadence).
- Tracks playback and wake-interruption flags.
- Auto-resume timer resets on new voice activity.

Wake and cut-in integration:
- On wake or cut-in, active playback is paused/stopped quickly.
- Resume only occurs when no pending speech/listening action remains.

System states:
- ASLEEP + playing: background music mode.
- AWAKE + paused/stopped: command capture mode.
- Idle timeout reached: resume and optionally return to ASLEEP.

## Troubleshooting

Music does not stop on wake:
1. Confirm `MUSIC_ENABLED=true`.
2. Confirm wake detection path is active.
3. Check orchestrator logs for music state transitions.

Music does not auto-resume:
1. Confirm `MUSIC_AUTO_RESUME_TIMEOUT_S` is set.
2. Confirm system is idle/listening and not still processing audio.
3. Check logs for auto-resume decision entries.

Mute button does not work:
1. Confirm `MEDIA_KEYS_ENABLED=true`.
2. Confirm event appears in logs.
3. Confirm capture backend exposes mute toggles.

Play button does not queue random tracks:
1. Confirm library indexing completed.
2. Confirm `MUSIC_RANDOM_TRACK_COUNT` is set.
3. Run native validator if needed.

## Related Docs

- `MUSIC_CONTROL_QUICK_START.md` for quick startup.
- `MUSIC_CONTROL_PLAN.md` for architecture and rollout.
- `MEDIA_KEYS_GUIDE.md` for device setup details.
