# Incremental Music Index Update Plan

## Goal

Replace the current full-library rebuild on routine updates with a lighter incremental scan that keeps the SQLite index current without recursive filesystem watchers.

## Problem

- Startup currently rebuilds the music index eagerly.
- Full-tree rescans are expensive on large libraries.
- Recursive inotify-style watching is not a good default for large media collections.

## Approach

1. Add a `directories` table to track per-directory `mtime_ns` and `last_scanned_ts`.
2. Keep the existing `tracks` table as the source of truth for file metadata.
3. Introduce an incremental scan path:
   - `stat()` directories first.
   - Skip entire subtrees when directory `mtime_ns` is unchanged.
   - Only `ffprobe` files whose `mtime_ns` or `size_bytes` changed.
   - Delete stale file rows when a scanned directory no longer contains them.
4. Preserve a full `rebuild()` path for recovery and first-run indexing.
5. Change routine `update` behavior to:
   - full rebuild when the DB is empty or missing
   - incremental scan when the DB already exists

## Planned Code Changes

### `orchestrator/music/library_index.py`

- Add `directories` schema and index.
- Add helpers for:
  - normalizing relative directory paths
  - loading cached directory mtimes
  - loading existing file rows for a directory
  - checking whether a file fingerprint changed
  - upserting directory scan state
- Add `scan_incremental()` and a recursive `_scan_dir()` helper.
- Keep `rebuild()` as the slow recovery/full-sync path.

### `orchestrator/music/mpd_client.py`

- Change `update` command handling to:
  - run full rebuild on empty DB
  - otherwise run incremental scan
- Keep startup indexing guarded so multiple pool initializations do not duplicate work.

## Validation

1. Syntax-check modified files.
2. Verify startup still indexes an empty DB.
3. Verify repeated `update` calls do not force a full rebuild on a populated DB.
4. Verify deleted files are removed from the SQLite index when their parent directory is rescanned.

## Non-Goals

- Adding live filesystem watchers.
- Redesigning search behavior.
- Changing playlist persistence.