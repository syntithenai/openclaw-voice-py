# MPD Replacement Plan (Planning Only)

## Scope and Constraint
- This document is a plan only.
- No implementation, refactor, config migration, dependency change, or runtime behavior change is included.

## Goal
Replace MPD with an orchestrator-native media player while preserving all current music playback capabilities and existing user-facing behavior.

## Required End State
- All references to MPD are removed from code, docs, runtime wiring, and deployment configuration.
- A media player subsystem exists inside the orchestrator.
- The media library folder is indexed with SQLite for fast lookup and metadata/state queries.
- Playlist operations support M3U format (create, read, write).
- Playlists are stored as filesystem-backed M3U files (no playlist database backend).
- FFmpeg is used for format conversion only when needed for playback compatibility.
- Music output is mixed into the main audio stream the same way MPD is currently mixed.
- When browser audio streaming is enabled, music audio is delivered through the web app/browser path.
- When browser audio streaming is disabled, music audio is delivered through host-attached sound hardware.
- Existing music features remain available and behaviorally compatible.

## Non-Goals (for this effort)
- Redesigning music UX beyond compatibility updates needed for backend replacement.
- Introducing a new external media daemon.
- Changing user intent model or command vocabulary unless strictly required for compatibility.

## Architecture Direction
- Implement a first-party orchestrator media engine as a modular service.
- Keep public control surface stable via a compatibility facade so existing voice/UI pathways continue to work.
- Add a dedicated media index service backed by SQLite that tracks scanned library files, normalized paths, metadata, and search keys.
- Add a format capability policy that decides direct playback vs FFmpeg transcode per output route.
- Decouple output routing from playback core through an output abstraction:
  - Browser output adapter (web stream target)
  - Host hardware output adapter (local audio device target)
- Preserve mixer responsibilities in existing audio pipeline with a dedicated player bus/channel.

## Workstreams

### 1) Discovery and MPD Dependency Inventory
- Enumerate all MPD references in:
  - Orchestrator runtime code
  - API handlers
  - Websocket events/actions
  - Skills/voice command handlers
  - UI integration points
  - Docker/compose/env files
  - Tests and test fixtures
  - Docs and runbooks
- Build a dependency map from call-site to behavior.
- Classify each reference as:
  - Required to replicate
  - Can be removed
  - Can be replaced by compatibility wrapper

Deliverable:
- MPD surface inventory with owner files and migration path per item.

### 2) Feature Parity Contract
- Define explicit behavior contract for current music features, including:
  - Play, pause, stop, next, previous
  - Queue management
  - Playlist load/save/list/delete behavior
  - Shuffle/repeat modes
  - Position seek and elapsed/duration reporting
  - Metadata/now-playing updates
  - Startup restore behavior (if present)
  - Event emission semantics and timing
- Freeze this as acceptance criteria before implementation.

Deliverable:
- Versioned feature parity checklist used by tests.

### 3) Orchestrator Media Player Core Design
- Define component boundaries:
  - SQLite media index service (scan, refresh, query)
  - Track resolver/index bridge
  - Playback controller state machine
  - Queue manager
  - Playlist manager (M3U)
  - Output router
  - Event publisher
- Define internal state model:
  - Idle, buffering, playing, paused, stopped, error
- Define recovery policy:
  - File missing
  - Decoder error
  - Output sink unavailable

Deliverable:
- Design spec with interfaces and state transitions.

SQLite index requirements:
- Source of truth for media-library indexing only (not playlist persistence).
- Stores path, stat fingerprints, extracted tags, duration, and search terms.
- Supports incremental rescans and startup warm index checks.
- Defines rebuild/recovery flow for corruption or schema migration.

### 4) File-Based M3U Playlist Subsystem
- Implement playlist manager supporting:
  - Create new M3U playlists
  - Read existing M3U playlists
  - Write/update playlists (ordered entries)
- Use filesystem-backed playlist files under a canonical playlist directory.
- Enforce atomic writes (temp file + rename) to avoid partial file corruption.
- Preserve compatibility expectations:
  - Stable names
  - Case handling behavior aligned with current system needs
  - Path normalization for workspace/music roots
  - Optional support for comments/EXTINF where useful
- Define canonical storage location and migration mapping from previous playlist source.
- Keep playlist persistence decoupled from SQLite index tables.

Deliverable:
- File-based M3U contract + migration plan.

### 5) Audio Mixing and Output Routing
- Keep player audio mixed into orchestrator stream as MPD currently is.
- Introduce output routing policy:
  - Browser streaming enabled -> player output routed to browser/web app path
  - Browser streaming disabled -> player output routed to host hardware sink
- Route format policy:
  - Browser route: direct stream when browser-compatible, otherwise FFmpeg transcode to browser-safe output.
  - Local route: direct playback when host path supports source, otherwise FFmpeg transcode to local-safe output.
- Conversion policy:
  - Never convert preemptively.
  - Convert only when required to allow playback/streaming on the active route.
- Ensure seamless route switching policy:
  - No process restart required
  - Predictable behavior on toggles during playback
- Define volume/mute interaction with global and per-source controls.

Deliverable:
- Output routing matrix and mixer integration contract.

### 6) Compatibility Layer and API Stability
- Maintain existing command and API surface while internals change.
- Replace MPD adapters with orchestrator media service adapters.
- Keep websocket and UI event payload schemas stable where possible.
- Provide deprecation window only if schema changes are unavoidable.

Deliverable:
- Compatibility adapter map and API delta document.

### 7) Configuration and Deployment Migration
- Remove MPD-related environment variables, compose services, startup scripts, and health checks.
- Add player configuration for:
  - Library roots
  - SQLite index database path
  - Index scan/refresh policy
  - Playlist storage root
  - Output routing defaults
  - Audio device/browser sink settings
- Provide migration notes for existing installs.

Deliverable:
- Config migration guide + deployment checklist.

### 8) Testing Strategy
- Unit tests:
  - Playback state machine
  - SQLite media index query and incremental refresh logic
  - Queue operations
  - M3U read/write round-trip
  - File-based playlist atomic write behavior
  - Output routing toggles
- Integration tests:
  - Voice/UI control path to player behavior
  - Library rescan to searchable-index propagation
  - Event propagation to web UI
  - Mixer path verification
- End-to-end tests:
  - Browser streaming enabled playback path
  - Browser streaming disabled hardware playback path
  - Playlist lifecycle scenarios
- Regression tests tied to parity contract.

Deliverable:
- Test matrix with pass/fail gates for cutover.

### 9) Cutover Plan
- Stage 1: Build player behind feature flag.
- Stage 2: Run dual-path validation in non-production profile.
- Stage 3: Default to new player; keep rollback switch.
- Stage 4: Remove MPD code, configs, docs after stability period.

Deliverable:
- Rollout runbook with rollback criteria.

## Detailed Implementation Plan

### Phase 0: Baseline and Guardrails
- Freeze current behavior with explicit snapshots of:
  - Queue payload shape
  - Playback state payload shape
  - Playlist naming behavior
  - Browser-audio toggle behavior
- Add/confirm feature flag:
  - `MEDIA_PLAYER_BACKEND=mpd|native`
  - Default `mpd` until Phase 6.

### Phase 1: Native Service Skeleton
- Create orchestrator-native player modules:
  - `orchestrator/music/native_player.py`
  - `orchestrator/music/native_router.py`
  - `orchestrator/music/native_queue.py`
  - `orchestrator/music/native_events.py`
- Keep existing external API methods in `orchestrator/music/manager.py`.
- Route calls through backend strategy:
  - MPD backend (existing)
  - Native backend (new)

### Phase 2: SQLite Library Index
- Add `orchestrator/music/library_index.py` implementing:
  - DB init/migration
  - Full scan
  - Incremental scan
  - Query API used by manager and search handlers
- Initial SQLite schema (versioned):
  - `tracks(path TEXT PRIMARY KEY, mtime_ns INTEGER, size_bytes INTEGER, duration_s REAL, title TEXT, artist TEXT, album TEXT, genre TEXT, track_no INTEGER, disc_no INTEGER, codec TEXT, bitrate_kbps INTEGER, sample_rate INTEGER, channels INTEGER, added_ts REAL, updated_ts REAL)`
  - `track_terms(path TEXT, term TEXT, weight REAL, PRIMARY KEY(path, term))`
  - `index_meta(key TEXT PRIMARY KEY, value TEXT)`
- Add indexes:
  - `idx_tracks_artist`, `idx_tracks_album`, `idx_tracks_title`, `idx_tracks_genre`
  - `idx_track_terms_term`
- Replace MPD-driven search/list operations with SQLite queries.

### Phase 3: File-Based M3U Playlist Manager
- Add `orchestrator/music/playlist_store.py`:
  - `create_playlist(name)`
  - `read_playlist(name)`
  - `write_playlist(name, entries)`
  - `delete_playlist(name)`
  - `list_playlists()`
- Storage rules:
  - One file per playlist: `<playlist_root>/<name>.m3u`
  - Atomic write via temp file + fsync + rename
  - UTF-8 encoding
  - Strict filename sanitization and reserved-name blocking
- Integrate playlist manager into `orchestrator/music/manager.py` for create/load/save/delete/list.

### Phase 4: Native Playback + Audio Routing
- Implement decode/playback engine in `orchestrator/music/native_player.py` with:
  - Transport control: play/pause/stop/seek/next/previous
  - Queue cursor and elapsed time tracking
  - Metadata emission for now-playing
- Implement output selection in `orchestrator/music/native_router.py`:
  - Browser streaming enabled -> route to web stream path
  - Browser streaming disabled -> route to host hardware device
- Implement on-demand FFmpeg transcoding path:
  - Probe source codec/container and route requirements.
  - Bypass transcode for directly supported formats.
  - Run FFmpeg only when direct playback/streaming would fail.
- Replace MPD FIFO assumptions with native stream source wiring:
  - Deprecate `orchestrator/audio/mpd_fifo_reader.py`
  - Add native stream bridge `orchestrator/audio/music_stream_bridge.py`

### Phase 4A: FFmpeg Capability Layer
- Add `orchestrator/music/ffmpeg_adapter.py`:
  - Build FFmpeg command lines for browser/local routes.
  - Manage process lifecycle and surface conversion errors.
- Add `orchestrator/music/format_policy.py`:
  - Implement `needs_transcode(input_format, route)`.
  - Keep conversion decisions deterministic and testable.
- Optional probing helper for codec/container discovery using ffprobe.
- Route defaults:
  - Browser-safe target format configurable for web playback compatibility.
  - Local-safe target format configurable for mixer/hardware compatibility.

### Phase 5: API/Realtime Compatibility
- Keep realtime message contracts stable in `orchestrator/web/realtime_service.py`.
- Ensure callbacks continue to call manager methods with unchanged payloads.
- Confirm UI receives the same `music_state`, `music_queue`, and `music_playlists` events.

### Phase 6: MPD Removal and Config Migration
- Remove MPD lifecycle management from startup path.
- Remove MPD environment/config references.
- Update Docker and host install scripts to no longer provision MPD.
- Keep optional rollback branch only during staged rollout.

### Phase 7: Validation and Cutover
- Run unit + integration + end-to-end test matrix.
- Validate both output routes:
  - Browser enabled
  - Browser disabled
- Switch default backend to native after parity is green.
- Remove MPD rollback code once stability window closes.

## Complete Planned File Change List

This is the complete planned list of files identified for migration work. Each item is marked with intended action.

Legend:
- `UPDATE`: edit existing file
- `ADD`: create new file
- `DELETE`: remove file after cutover

### Core orchestrator code
- `UPDATE` `orchestrator/music/manager.py`
  - Convert from MPD-command orchestration to backend strategy facade.
  - Wire SQLite index queries and file-based playlist store.
- `UPDATE` `orchestrator/web/realtime_service.py`
  - Keep transport payloads stable while using native backend callbacks.
- `UPDATE` `orchestrator/main.py`
  - Replace MPD manager bootstrap with native player bootstrap.
- `UPDATE` `orchestrator/config.py`
  - Remove MPD settings; add native index/playlist/output config.
- `UPDATE` `orchestrator/music/__init__.py`
  - Export native modules and compatibility interfaces.
- `UPDATE` `orchestrator/gateway/quick_answer.py`
  - Update any direct MPD/music assumptions to manager facade.
- `DELETE` `orchestrator/music/mpd_client.py`
- `DELETE` `orchestrator/services/mpd_manager.py`
- `DELETE` `orchestrator/audio/mpd_fifo_reader.py`
- `DELETE` `orchestrator/services/mpd.conf`
- `UPDATE` `orchestrator/services/__init__.py`
  - Remove MPD service exports.

### New core modules
- `ADD` `orchestrator/music/library_index.py`
- `ADD` `orchestrator/music/playlist_store.py`
- `ADD` `orchestrator/music/native_player.py`
- `ADD` `orchestrator/music/native_router.py`
- `ADD` `orchestrator/music/native_queue.py`
- `ADD` `orchestrator/music/native_events.py`
- `ADD` `orchestrator/music/ffmpeg_adapter.py`
- `ADD` `orchestrator/music/format_policy.py`
- `ADD` `orchestrator/audio/music_stream_bridge.py`

### Runtime and deployment
- `UPDATE` `docker-compose.yml`
  - Remove MPD-specific mounts/env usage; add native playlist/index volume if needed.
- `UPDATE` `Dockerfile`
  - Remove MPD package/runtime dependencies and add FFmpeg runtime dependency.
- `DELETE` `docker/mpd/mpd.conf`
- `UPDATE` `.env.example`
- `UPDATE` `.env.docker.example`
- `UPDATE` `.env.pi.example`
  - Remove `MPD_*`; add `MEDIA_PLAYER_*`, `MEDIA_INDEX_DB_PATH`, `PLAYLIST_ROOT`, and transcode policy options.
- `UPDATE` `run_docker_orchestrator_auto_audio.sh`
  - Remove MPD assumptions.
- `UPDATE` `setup_mpd.sh`
  - Replace with native media setup flow or retire script.
- `UPDATE` `fix_mpd.sh`
  - Replace with native diagnostics flow or retire script.

### Install/bootstrap scripts
- `UPDATE` `install_raspbian.sh`
- `UPDATE` `install_raspbian_remote.sh`
- `UPDATE` `install_ubuntu.sh`
  - Remove MPD install/config steps.

### Tests
- `DELETE` `test_mpd_orchestrator.py`
- `UPDATE` `test_music_system.py`
- `UPDATE` `test_music_ui_playlist_workflow_contract.py`
- `UPDATE` `test_search_playlists_stuck.py`
- `UPDATE` `validate_mpd_integration.py`
  - Replace with native integration validation.
- `UPDATE` `orchestrator/tools/test_tool_router_llm_args.py`
  - Adjust expected music backend/service wiring if referenced.
- `ADD` `orchestrator/test_library_index_sqlite.py`
- `ADD` `orchestrator/test_playlist_store_m3u.py`
- `ADD` `orchestrator/test_native_player_transport.py`
- `ADD` `orchestrator/test_audio_route_browser_vs_host.py`
- `ADD` `orchestrator/test_format_policy_transcode_decisions.py`
- `ADD` `orchestrator/test_ffmpeg_adapter_process_contract.py`

### Docs and planning artifacts
- `UPDATE` `README.md`
- `UPDATE` `ORCHESTRATOR_USAGE.md`
- `UPDATE` `IMPLEMENTATION_CHECKLIST.md`
- `UPDATE` `PROVIDER_CONFIG_PLAN.md`
- `UPDATE` `WEB_UI_EXPANSION_SOURCE_PLAN.md`
- `UPDATE` `STATE_EVENT_MATRIX_AND_CUTIN_NOTES.md`
- `UPDATE` `MUSIC_CONTROL_PLAN.md`
- `UPDATE` `MUSIC_CONTROL_QUICK_START.md`
- `UPDATE` `MUSIC_INTEGRATION_GUIDE.md`
- `UPDATE` `MUSIC_INTEGRATION_SUMMARY.md`
- `UPDATE` `MEDIA_KEYS_GUIDE.md`
- `UPDATE` `MEDIA_KEYS_QUICKSTART.md`
- `UPDATE` `PLAYLIST_LOAD_LATENCY_IMPLEMENTATION_CHECKLIST.md`
- `UPDATE` `PLAYLIST_LOAD_LATENCY_INVESTIGATION_PLAN.md`
- `DELETE` `MPD_FIFO_ARCHITECTURE.md`
- `DELETE` `MPD_INTEGRATION_SUMMARY.md`
- `DELETE` `MPD_ORCHESTRATOR_MANAGEMENT.md`
- `DELETE` `MPD_SETUP_STEPS.md`
- `DELETE` `MPD_TASK_COMPLETE.md`
- `DELETE` `QUICKSTART_MPD.md`

### UI/web app integration points
- `UPDATE` `orchestrator/web/realtime_service.py`
  - Keep event schema and callback contract stable.
- `UPDATE` `orchestrator/web/static/index.html`
- `UPDATE` `orchestrator/web/static/main.js`
  - Only if backend-specific assumptions exist; preserve current UX.

## Detailed Method-Level Change Targets

### `orchestrator/music/manager.py`
- Replace direct calls:
  - `play`, `pause`, `stop`, `next_track`, `previous_track`, `seek_to`
  - `get_status`, `get_current_track`, `get_stats`
  - search methods (`search_artist`, `search_album`, `search_title`, `search_genre`, `search_any`)
  - playlist operations (`list`, `load`, `save`, `create`, `delete`)
  - queue operations (`add`, `remove`, `clear`, random add)
- Preserve method names and return payload shape for callers.
- Remove MPD-specific helpers:
  - `_normalize_pipewire_mpd_stream_volume`
  - MPD outputs-related helpers.

### `orchestrator/web/realtime_service.py`
- Keep callback registration signatures unchanged.
- Keep emitted event payload keys unchanged:
  - `music_transport`
  - `music_queue`
  - `music_playlists`
  - `music_state`

### `orchestrator/main.py`
- Replace MPD manager startup/shutdown wiring with:
  - native player lifecycle
  - library index lifecycle
  - playlist store initialization

### `orchestrator/config.py`
- Remove:
  - `MPD_HOST`, `MPD_PORT`, `MPD_FIFO_*` options
- Add:
  - `MEDIA_PLAYER_BACKEND`
  - `MEDIA_LIBRARY_ROOT`
  - `MEDIA_INDEX_DB_PATH`
  - `PLAYLIST_ROOT`
  - `MEDIA_OUTPUT_MODE` and browser-routing options
  - `MEDIA_TRANSCODE_ONLY_WHEN_NEEDED`
  - `MEDIA_TRANSCODE_BROWSER_TARGET`
  - `MEDIA_TRANSCODE_LOCAL_TARGET`

## FFmpeg Transcode Policy
- Use FFmpeg only as a compatibility fallback for unsupported source formats.
- Do not transcode when direct playback/streaming is supported by the selected route.
- Do not pre-convert or overwrite library files.
- Perform conversion on-demand in the playback pipeline only.
- Prefer stream copy when codec is already compatible and only container adaptation is required.

## Risk Register
- Hidden MPD coupling in edge command paths.
- Behavioral drift in event timing used by UI.
- Audio sink switching glitches during active playback.
- Playlist path mismatches or case sensitivity regressions.
- SQLite index corruption or stale-index drift after library changes.
- Increased orchestrator process load from decoding/mixing.

Mitigations:
- Exhaustive dependency inventory.
- Parity-driven test gates.
- Controlled feature-flag rollout.
- SQLite integrity checks and safe rebuild path.
- Structured observability for playback, routing, and errors.

## Observability Requirements
- Structured logs for command -> state transition -> output sink decision.
- Metrics:
  - Command latency
  - Index scan duration and indexed track count
  - Index query latency and miss rate
  - Track start failure rate
  - Sink switch latency/failures
  - Playlist read/write errors
- Debug endpoint(s) for player state and active route.

## Definition of Done
- No runtime MPD dependency remains.
- All MPD references removed or replaced as intended.
- SQLite media indexing is active and validated for library search/lookup.
- M3U create/read/write workflows pass tests.
- Playlist persistence is filesystem-backed M3U only.
- Browser-enabled and browser-disabled output paths pass end-to-end checks.
- Existing playback feature parity checklist is fully green.
- Documentation and deployment artifacts reflect new architecture.

## Execution Sequence (High-Level)
1. Inventory MPD references and finalize feature parity contract.
2. Build media player core and M3U subsystem.
3. Integrate mixer/output routing with browser/hardware policy.
4. Apply compatibility layer and migrate command/event paths.
5. Run full regression and end-to-end routing tests.
6. Roll out via feature flag and complete MPD removal.

## Notes for Implementation Phase
- Preserve existing external behavior first, then optimize internals.
- Prefer adapter-based migration to minimize churn in UI/voice layers.
- Avoid large-bang replacement without staged rollback capability.
