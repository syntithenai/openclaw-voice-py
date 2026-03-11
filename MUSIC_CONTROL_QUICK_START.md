# Music Control System - Quick Start Guide

This guide helps you get the music control system up and running with MPD (Music Player Daemon).

## Overview

The music control system provides voice-controlled music playback via MPD with:
- **Fast-path parsing** for instant responses (<200ms)
- **LLM fallback** for complex queries
- **Automatic library scanning** on first run
- **Persistent state** across container restarts

## Quick Setup (Docker)

### 1. Configure Environment

Add to your `.env` file:

```bash
# Music Control
MUSIC_ENABLED=true
MPD_HOST=mpd
MPD_PORT=6600
MUSIC_LIBRARY_HOST_PATH=/home/stever/Music
```

### 2. Start Services

```bash
docker-compose up -d
```

The system will automatically:
- Start MPD with your music library mounted
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
Fast-Path Parser (regex patterns) →  Match? → MusicManager → MPD
       ↓                                              ↓
    No match                                      Response
       ↓
LLM with Tool Calling → music_play_genre() → MusicRouter → Response
```

### Components

1. **MPDClient** (`orchestrator/music/mpd_client.py`)
   - Low-level MPD protocol communication
   - Connection pooling for instant command execution
   - Automatic reconnection on connection loss

2. **MusicManager** (`orchestrator/music/manager.py`)
   - High-level operations (play, pause, search, etc.)
   - Wraps MPD commands in user-friendly methods
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

### Test MPD Connection

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

Or manually:
```bash
docker-compose exec mpd mpc update
```

### Check Library Status

```bash
docker-compose exec mpd mpc stats
```

### View MPD Logs

```bash
docker-compose logs mpd
```

### Clear MPD State (Fresh Start)

```bash
docker-compose down
docker volume rm openclaw-voice_mpd-state
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
1. Check music path: `docker-compose exec mpd ls /music`
2. Voice command: "update library"
3. Verify: `docker-compose exec mpd mpc stats`

### MPD Connection Failed

**Symptom:** "Failed to connect to MPD at mpd:6600"

**Solution:**
1. Check MPD is running: `docker-compose ps mpd`
2. Check orchestrator can reach MPD: `docker-compose exec orchestrator ping mpd`
3. Verify environment: `echo $MPD_HOST` (should be "mpd" in Docker)

### Library Not Updating

**Symptom:** Song count stays at 0 after scan

**Solution:**
1. Check mount: `docker-compose exec mpd df -h /music`
2. Check permissions: `docker-compose exec mpd ls -la /music`
3. Check MPD logs: `docker-compose logs mpd`

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
├── mpd_client.py         # TCP client + connection pool
├── manager.py            # High-level operations
├── parser.py             # Fast-path regex patterns
└── router.py             # Request routing + tool handling

docker/mpd/
├── Dockerfile            # MPD container image
└── mpd.conf             # MPD configuration

test_music_system.py      # Test suite
```

## Next Steps

1. **Add More Music**: Copy files to `${MUSIC_LIBRARY_HOST_PATH}`
2. **Create Playlists**: "save playlist morning jazz"
3. **Adjust Confidence**: Tune fast-path patterns if needed
4. **Monitor Performance**: Check logs for latency metrics

## Additional Resources

- [MPD Protocol Reference](https://mpd.readthedocs.io/en/latest/protocol.html)
- [MUSIC_CONTROL_PLAN.md](MUSIC_CONTROL_PLAN.md) - Full implementation plan
- [.env.example](.env.example) - All configuration options (baseline)
- [.env.docker.example](.env.docker.example) - Docker profile template
- [.env.pi.example](.env.pi.example) - Raspberry Pi profile template
