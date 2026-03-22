# Music Control System - Quick Start Guide

This guide helps you get the music control system up and running with the native backend.

## Overview

The music control system provides voice-controlled music playback via the native backend with:
- **Fast-path parsing** for instant responses (<200ms)
- **LLM fallback** for complex queries
- **Automatic library scanning** on first run
- **Persistent state** inside the orchestrator runtime

## Quick Setup (Docker)

### 1. Configure Environment

Add to your `.env` file:

```bash
# Music Control
MUSIC_ENABLED=true
MEDIA_PLAYER_BACKEND=native
MEDIA_LIBRARY_ROOT=music
MEDIA_INDEX_DB_PATH=.media/library.sqlite3
PLAYLIST_ROOT=playlists
MUSIC_LIBRARY_HOST_PATH=/home/stever/Music
```

### 2. Start Services

```bash
docker-compose up -d
```

The system will automatically:
- Start the in-process native music backend
- Scan the library if empty (first run)
- Initialize the music router with fast-path parsing

### 3. Test Voice Commands

Try these commands:
- "play" / "pause" / "stop"
- "next" / "previous"
- "volume 50" / "volume up" / "volume down"
- "what's playing?"
- "play some jazz"
- "play the beatles"
- "update library" (after adding new music)

## Architecture

```
User Voice Input
   ↓
Fast-Path Parser (regex patterns) → Match? → MusicManager → Native Backend
   ↓                                             ↓
    No match                                     Response
   ↓
LLM with Tool Calling → music_play_genre() → MusicRouter → Response
```

### Components

1. **NativeMusicClient** (`orchestrator/music/native_client.py`)
   - In-process command compatibility layer
   - Connection-style pooling interface
   - Native queue/search/playback control

2. **MusicManager** (`orchestrator/music/manager.py`)
   - High-level operations (play, pause, search, etc.)
   - Wraps backend commands in user-friendly methods
   - Handles library management (auto-scan on empty)

3. **MusicFastPathParser** (`orchestrator/music/parser.py`)
   - Regex-based pattern matching
   - Sub-200ms response time for common commands
   - Falls back to LLM for complex queries

4. **MusicRouter** (`orchestrator/music/router.py`)
   - Routes commands through fast-path or LLM
   - Formats responses for TTS
   - Handles tool calls from LLM

## Manual Testing

### Test Backend Connection

```bash
python test_music_system.py --test connection
```

### Test Fast-Path Parser

```bash
python test_music_system.py --test parser
```

### Run All Tests

```bash
python test_music_system.py
```

## Common Operations

### Adding New Music

1. Copy music files to `${MUSIC_LIBRARY_HOST_PATH}`
2. Voice command: "update library" or "scan music"
3. Wait ~2 seconds for scan to complete

Or manually via validator:
```bash
./.venv_orchestrator/bin/python validate_native_music_integration.py
```

### Check Library Status

```bash
docker-compose exec orchestrator-linux-alsa ./.venv_orchestrator/bin/python validate_native_music_integration.py
```

### View Orchestrator Logs

```bash
docker-compose logs orchestrator-linux-alsa
```

### Clear Backend State (Fresh Start)

```bash
docker-compose down
docker-compose up -d
```

## Supported Voice Commands

### Playback Control (100% Fast-Path)
- "play" / "resume" / "continue"
- "pause" / "stop"
- "next" / "skip"
- "previous" / "back"

### Volume Control (100% Fast-Path)
- "volume 50" (set level)
- "volume up" / "louder"
- "volume down" / "quieter"

### Status Queries (90% Fast-Path)
- "what's playing"
- "what's this song"

### Library Management (100% Fast-Path)
- "update library"
- "scan music"
- "refresh library"

### Search & Play (Uses LLM for disambiguation)
- "play some jazz" (genre)
- "play the beatles" (artist)
- "play hey jude" (song title)
- "play revolver" (album)

## Troubleshooting

### No Music in Library

**Symptom:** "No tracks found for genre: jazz"

**Solution:**
1. Check music path: `docker-compose exec orchestrator-linux-alsa ls /music`
2. Voice command: "update library"
3. Verify: `docker-compose exec orchestrator-linux-alsa mpc stats`

### Backend Initialization Failed

**Symptom:** music commands return backend initialization error

**Solution:**
1. Check orchestrator is running: `docker-compose ps orchestrator-linux-alsa`
2. Run validator in container: `docker-compose exec orchestrator-linux-alsa ./.venv_orchestrator/bin/python validate_native_music_integration.py`
3. Verify environment: `echo $MEDIA_PLAYER_BACKEND` (should be `native`)

### Library Not Updating

**Symptom:** Song count stays at 0 after scan

**Solution:**
1. Check mount: `docker-compose exec orchestrator-linux-alsa df -h /music`
2. Check permissions: `docker-compose exec orchestrator-linux-alsa ls -la /music`
3. Check orchestrator logs: `docker-compose logs orchestrator-linux-alsa`

## Performance Targets

| Operation | Target Latency | Fast-Path Hit Rate |
|-----------|----------------|-------------------|
| Play/Pause/Stop | 50-100ms | 100% |
| Volume Control | 50-100ms | 100% |
| Next/Previous | 50-100ms | 100% |
| Current Track | 50-100ms | 100% |
| Play Artist | 150-300ms | 80% (LLM for disambiguation) |
| Play Genre | 150-300ms | 90% |
| Library Update | 50-100ms | 100% |

## File Structure

```
orchestrator/music/
├── __init__.py           # Module exports
├── native_client.py      # Native client surface
├── mpd_client.py         # Compatibility command layer
├── manager.py            # High-level operations
├── parser.py             # Fast-path regex patterns
└── router.py             # Request routing + tool handling

test_music_system.py      # Test suite
validate_native_music_integration.py
```

## Next Steps

1. **Add More Music**: Copy files to `${MUSIC_LIBRARY_HOST_PATH}`
2. **Create Playlists**: "save playlist morning jazz"
3. **Adjust Confidence**: Tune fast-path patterns if needed
4. **Monitor Performance**: Check logs for latency metrics

## Additional Resources

- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
- [MUSIC_CONTROL_PLAN.md](MUSIC_CONTROL_PLAN.md) - Full implementation plan
- [.env.example](.env.example) - All configuration options (baseline)
- [.env.docker.example](.env.docker.example) - Docker profile template
- [.env.pi.example](.env.pi.example) - Raspberry Pi profile template
