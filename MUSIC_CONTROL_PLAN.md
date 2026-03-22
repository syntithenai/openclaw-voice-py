# Music Control Fast-Path Integration Plan (Native Backend)

## Overview

This plan documents fast-path music control in the voice orchestrator using the native in-process backend. The goal is deterministic, low-latency handling for common music commands, with LLM tool-calling fallback for complex requests.

## Goals

1. Immediate playback control: play, pause, skip, stop, volume.
2. Natural language playback requests: genre, artist, album, track.
3. File-based playlist management using M3U files.
4. Deterministic fast-path regex handling for common commands.
5. LLM tool-calling fallback for complex or ambiguous requests.
6. Connection-style pooling interface preserved for compatibility.

## Architecture

```
Voice Input
    ↓
STT (Whisper)
    ↓
Quick Answer Client
    ↓
Fast-Path Parser (regex)
    ↓
MusicRouter
    ↓
NativeMusicClient (compatibility facade)
    ↓
Native backend services
  - SQLite media index
  - File playlist store (M3U)
  - Local and browser playback routing
```

## Core Components

- `orchestrator/music/native_client.py`: native-facing client interface.
- `orchestrator/music/mpd_client.py`: compatibility command layer backed by native logic.
- `orchestrator/music/manager.py`: high-level music operations and user-facing responses.
- `orchestrator/music/router.py`: maps parsed/LLM intents to backend methods.
- `orchestrator/music/parser.py`: deterministic regex command parser.
- `orchestrator/music/library_index.py`: SQLite indexing and search.
- `orchestrator/music/playlist_store.py`: playlist CRUD on filesystem M3U files.
- `orchestrator/music/native_player.py`: queue/playback handling for local and browser routes.
- `orchestrator/music/ffmpeg_adapter.py`: ffprobe and conditional transcode helpers.
- `orchestrator/music/format_policy.py`: route-aware capability checks and transcode policy.

## Configuration

Required environment values:

- `MUSIC_ENABLED=true`
- `MEDIA_PLAYER_BACKEND=native`
- `MEDIA_LIBRARY_ROOT=music`
- `MEDIA_INDEX_DB_PATH=.media/library.sqlite3`
- `PLAYLIST_ROOT=playlists`
- `MUSIC_COMMAND_TIMEOUT_S=2.0`
- `MUSIC_FAST_PATH_ENABLED=true`

Optional route/runtime values:

- `LOCAL_AUDIO_CMD` for local playback command.
- `MEDIA_FILES_ENABLED=true` for browser playback source serving.
- `MEDIA_FILES_DIR=music` for media static path.

## Fast-Path Strategy

- Handle simple, unambiguous phrases directly:
  - play/resume/pause/stop/next/previous
  - absolute/relative volume
  - quick genre/artist/playlist commands
  - now-playing/status checks
- Send complex requests to LLM tool-calling:
  - disambiguation-heavy artist/album/track requests
  - compositional queries (era + genre + mood)
  - playlist creation from arbitrary criteria

## Validation

1. Run unit/integration tests for music manager/router/parser.
2. Run native integration validator:
   - `./.venv_orchestrator/bin/python validate_native_music_integration.py`
3. Verify UI state sync for queue/current track updates.
4. Verify both playback routes:
   - local output route
   - browser audio route

## Risks and Mitigations

- Route mismatch between local and browser playback:
  - Mitigation: central route selection and explicit state push.
- Large library indexing time:
  - Mitigation: persistent SQLite index and incremental rescans.
- Unsupported media formats:
  - Mitigation: FFmpeg only when required by route capability policy.

## Rollout

1. Enable native backend in environment defaults.
2. Validate command and UI parity with prior behavior.
3. Remove stale MPD references from scripts and docs.
4. Keep compatibility aliases to avoid breaking existing imports.
