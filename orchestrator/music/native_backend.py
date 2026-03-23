from __future__ import annotations

import sqlite3
import asyncio
import logging
import os
import shlex
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .library_index import LibraryIndex
from .playlist_store import PlaylistStore
from .native_player import NativePlayer

logger = logging.getLogger(__name__)

RUNTIME_MEDIA_PREFIX = "__runtime_media__/"


@dataclass
class QueueItem:
    file: str
    id: int


def _load_env_file_values(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = val.strip().strip('"').strip("'")
    except Exception:
        return {}
    return values


def _get_env_or_file(key: str, workspace: Path, default: str) -> str:
    value = str(os.getenv(key, "") or "").strip()
    if value:
        return value

    candidate_files: list[Path] = [workspace / ".env", workspace.parent / ".env"]
    for env_file in candidate_files:
        if not env_file.exists() or not env_file.is_file():
            continue
        file_values = _load_env_file_values(env_file)
        file_value = str(file_values.get(key, "") or "").strip()
        if file_value:
            return file_value
    return default


def _resolve_default_playlist_root(workspace: Path) -> Path:
    """Pick a sane default playlists directory for this workspace.

    When running from inside the `openclaw-voice` repo (or one of its subdirs),
    prefer the sibling workspace-level `playlists/` directory.
    """
    ws = workspace.resolve()

    # Common local-dev layout:
    #   <workspace>/openclaw-voice
    #   <workspace>/playlists
    for candidate in [ws, *ws.parents]:
        if candidate.name != "openclaw-voice":
            continue
        sibling_playlists = (candidate.parent / "playlists").resolve()
        if sibling_playlists.exists() and sibling_playlists.is_dir():
            return sibling_playlists
        break

    return (ws / "playlists").resolve()


class _NativeMusicBackend:
    def __init__(self) -> None:
        cwd = Path.cwd().resolve()
        # OPENCLAW_WORKSPACE_DIR should only come from explicit process env.
        # Falling back to values in unrelated .env files can redirect indexing.
        workspace = Path(os.getenv("OPENCLAW_WORKSPACE_DIR", str(cwd))).resolve()
        configured_library = Path(_get_env_or_file("MEDIA_LIBRARY_ROOT", workspace, "/music")).expanduser()
        library_root = configured_library.resolve() if configured_library.is_absolute() else (workspace / configured_library).resolve()
        if not library_root.exists():
            try:
                library_root.mkdir(parents=True, exist_ok=True)
            except Exception:
                library_root = (workspace / "music").resolve()
                library_root.mkdir(parents=True, exist_ok=True)
        configured_playlist_root = _get_env_or_file("PLAYLIST_ROOT", workspace, "").strip()
        if configured_playlist_root:
            playlist_root = Path(configured_playlist_root).expanduser().resolve()
        else:
            playlist_root = _resolve_default_playlist_root(workspace)
        db_path = Path(
            _get_env_or_file("MEDIA_INDEX_DB_PATH", workspace, str(workspace / ".media" / "library.sqlite3"))
        ).expanduser().resolve()

        self.library = LibraryIndex(str(db_path), str(library_root))
        self.library_root = library_root
        self.playlists = PlaylistStore(str(playlist_root))
        self.player = NativePlayer(str(library_root))

        self.queue: list[QueueItem] = []
        self.current_pos: int = -1
        self.state: str = "stop"
        self.volume: int = 100
        self.random_enabled: bool = False
        self.repeat_enabled: bool = False
        self.elapsed_anchor_ts: float = 0.0
        self.elapsed_anchor_value: float = 0.0
        self._song_id_seq = 1000
        self.playlist_version = 0
        self.last_db_update = int(time.time())
        self.browser_file_override: str = ""
        self._startup_index_done = False
        self._startup_index_lock = asyncio.Lock()
        self._startup_index_task: asyncio.Task[None] | None = None
        self._indexing_active = False
        # Single persistent background task that watches the current player proc
        # and advances the queue when it exits naturally.
        self._auto_advance_task: asyncio.Task[None] | None = None
        self._play_failure_skip_task: asyncio.Task[None] | None = None
        self._play_failure_skip_delay_s: float = 1.5
        self._sequential_failed_plays: int = 0
        self.last_warning: str = ""
        self.last_warning_ts: float = 0.0
        # Guard to avoid double-advancing the same finished local process in
        # status fallback mode.
        self._last_finished_local_proc_id: int | None = None

    def _on_startup_index_done(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
            logger.warning("✓ Music index startup scan completed in background")
            print("✓ Music index startup scan completed in background", flush=True)
        except Exception as exc:
            logger.warning("✗ Music index startup scan failed: %s", exc)
            print(f"✗ Music index startup scan failed: {exc}", flush=True)
        finally:
            if self._startup_index_task is task:
                self._startup_index_task = None

    def start_startup_index(self) -> None:
        if self._startup_index_done:
            logger.info("Music index startup scan skipped: already initialized")
            print("   Music index: startup scan skipped (already initialized)", flush=True)
            return
        if self._startup_index_task and not self._startup_index_task.done():
            logger.warning("Music index startup scan already running in background")
            return
        task = asyncio.create_task(self.ensure_startup_index())
        self._startup_index_task = task
        task.add_done_callback(self._on_startup_index_done)
        logger.warning("↻ Music index startup scan scheduled in background")
        print("↻ Music index: startup scan scheduled in background", flush=True)

    async def ensure_startup_index(self) -> None:
        if self._startup_index_done:
            logger.info("Music index startup scan skipped: already initialized")
            print("   Music index: startup scan skipped (already initialized)", flush=True)
            return
        async with self._startup_index_lock:
            if self._startup_index_done:
                logger.info("Music index startup scan skipped: already initialized")
                print("   Music index: startup scan skipped (already initialized)", flush=True)
                return

            # Check for incomplete rebuild from previous crash/restart
            try:
                is_incomplete = self.library.detect_incomplete_rebuild()
            except sqlite3.DatabaseError:
                is_incomplete = False

            if is_incomplete:
                logger.warning("🔄 Music index: resuming incomplete rebuild from previous startup")
                print("🔄 Music index: resuming from incomplete rebuild", flush=True)
                try:
                    self.library.cleanup_incomplete_rebuild()
                except sqlite3.DatabaseError:
                    logger.warning(
                        "⚠️ Could not complete rebuild cleanup due to db corruption; recovery will happen during scan"
                    )

            started = time.monotonic()
            logger.warning("🔍 Music index startup scan started in background")
            print("🔍 Music index: startup scan started", flush=True)
            result = await self.execute("update")
            self._startup_index_done = True
            elapsed = time.monotonic() - started
            songs = result.get("songs", "0")
            logger.warning(
                "✓ Music index startup scan complete (background): songs=%s elapsed=%.1fs",
                songs,
                elapsed,
            )
            print(
                f"✓ Music index: startup scan complete (songs={songs}, elapsed={elapsed:.1f}s)",
                flush=True,
            )

    def set_output_route(self, route: str) -> None:
        self.player.set_output_route(route)

    def _next_id(self) -> int:
        self._song_id_seq += 1
        return self._song_id_seq

    def _touch_playlist(self) -> None:
        self.playlist_version += 1

    def _set_warning(self, message: str) -> None:
        self.last_warning = str(message or "").strip()
        self.last_warning_ts = time.time() if self.last_warning else 0.0

    def _clear_warning(self) -> None:
        self.last_warning = ""
        self.last_warning_ts = 0.0

    def _cancel_play_failure_skip(self) -> None:
        task = self._play_failure_skip_task
        self._play_failure_skip_task = None
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not None and not task.done() and task is not current:
            task.cancel()

    def _current_item(self) -> QueueItem | None:
        if 0 <= self.current_pos < len(self.queue):
            return self.queue[self.current_pos]
        return None

    def _current_track(self) -> dict[str, str]:
        item = self._current_item()
        if not item:
            return {}
        track = self.library.get_track(item.file) or {"file": item.file}
        if self.player.output_route == "browser" and self.browser_file_override:
            track["file"] = self.browser_file_override
        track["id"] = str(item.id)
        track["pos"] = str(self.current_pos)
        return track

    def _set_state(self, state: str) -> None:
        if state == "play":
            self.elapsed_anchor_ts = time.monotonic()
        else:
            self.elapsed_anchor_value = self._elapsed_now()
        self.state = state

    def _elapsed_now(self) -> float:
        if self.state != "play":
            return float(self.elapsed_anchor_value)
        return max(0.0, float(self.elapsed_anchor_value) + (time.monotonic() - self.elapsed_anchor_ts))

    async def _skip_failed_track_after_delay(self, failed_pos: int, failed_song_id: int) -> None:
        try:
            await asyncio.sleep(max(0.0, float(self._play_failure_skip_delay_s)))
        except asyncio.CancelledError:
            return

        if self.state != "stop":
            return
        if self.current_pos != failed_pos:
            return
        current = self._current_item()
        if current is None or int(current.id) != int(failed_song_id):
            return
        if len(self.queue) <= 1:
            return

        next_pos = (failed_pos + 1) % len(self.queue)
        logger.warning(
            "⏭ Skipping failed track after %.1fs: pos %d → %d (queue length %d)",
            self._play_failure_skip_delay_s,
            failed_pos,
            next_pos,
            len(self.queue),
        )
        await self._play_pos(next_pos)

    def _schedule_play_failure_skip(self, failed_pos: int, failed_song_id: int) -> None:
        self._cancel_play_failure_skip()
        self._play_failure_skip_task = asyncio.create_task(
            self._skip_failed_track_after_delay(failed_pos, failed_song_id)
        )

    async def _reset_playback_context(self) -> None:
        """Stop playback and clear transient browser/runtime state."""
        self._cancel_play_failure_skip()
        await self.player.stop()
        self.player.browser_stream_path = ""
        self.browser_file_override = ""
        self._sequential_failed_plays = 0
        self._clear_warning()
        self._set_state("stop")

    async def _maybe_advance_browser_route(self) -> None:
        """Advance browser-route playback when elapsed reaches track duration.

        Browser route has no local subprocess to wait on, so we detect track end
        using elapsed/duration from the indexed metadata.
        """
        if self.state != "play":
            return
        if self.player.output_route != "browser":
            return
        item = self._current_item()
        if not item:
            return
        track = self.library.get_track(item.file) or {}
        try:
            duration = float(track.get("duration", "0") or 0.0)
        except Exception:
            duration = 0.0
        if duration <= 0.0:
            return
        if self._elapsed_now() + 0.05 < duration:
            return
        next_pos = (self.current_pos + 1) % len(self.queue)
        logger.debug(
            "⏭ Browser-route auto-advance: pos %d → %d (queue length %d)",
            self.current_pos,
            next_pos,
            len(self.queue),
        )
        await self._play_pos(next_pos)

    async def _maybe_advance_local_route_fallback(self) -> None:
        """Fallback progression for local route when the process is already finished.

        The primary path is _auto_advance_loop. This fallback keeps progression
        reliable even if that task is unavailable or a process switch happened at
        an unlucky time.
        """
        if self.state != "play":
            return
        if self.player.output_route == "browser":
            return
        if not self.queue:
            return

        proc = self.player._proc

        # No local proc currently attached: use elapsed/duration to detect
        # end-of-track and progress.
        if proc is None:
            item = self._current_item()
            if not item:
                return
            track = self.library.get_track(item.file) or {}
            try:
                duration = float(track.get("duration", "0") or 0.0)
            except Exception:
                duration = 0.0
            if duration <= 0.0:
                return
            if self._elapsed_now() + 0.05 < duration:
                return
            next_pos = (self.current_pos + 1) % len(self.queue)
            logger.warning(
                "⏭ Local fallback auto-advance (no proc): pos %d → %d (queue length %d)",
                self.current_pos,
                next_pos,
                len(self.queue),
            )
            await self._play_pos(next_pos)
            return

        # Proc still active: nothing to do.
        if proc.returncode is None:
            return

        proc_id = id(proc)
        if self._last_finished_local_proc_id == proc_id:
            return
        self._last_finished_local_proc_id = proc_id

        next_pos = (self.current_pos + 1) % len(self.queue)
        logger.warning(
            "⏭ Local fallback auto-advance (finished proc): pos %d → %d (queue length %d)",
            self.current_pos,
            next_pos,
            len(self.queue),
        )
        await self._play_pos(next_pos)

    async def _auto_advance_loop(self) -> None:
        """Persistent background loop: watches the current local player process
        and advances the queue when it exits naturally.

        Re-fetches player._proc each iteration so it handles seekcur and any
        other operation that replaces the process without going through _play_pos.
        """
        while True:
            # Not playing locally — park.
            if (
                self.state != "play"
                or self.player.output_route == "browser"
                or self.player._proc is None
            ):
                await asyncio.sleep(0.2)
                continue

            proc = self.player._proc
            pos = self.current_pos

            try:
                await proc.wait()
            except Exception:
                await asyncio.sleep(0.1)
                continue

            # Guards: if anything changed while we waited, loop back and recheck.
            if self.state != "play":
                continue
            if self.current_pos != pos:
                continue
            if self.player._proc is not proc:
                continue
            if not self.queue:
                self.current_pos = -1
                self._set_state("stop")
                continue

            next_pos = (pos + 1) % len(self.queue)
            logger.debug(
                "⏭ Auto-advancing queue: pos %d → %d (queue length %d)",
                pos,
                next_pos,
                len(self.queue),
            )
            try:
                await self._play_pos(next_pos)
            except Exception as exc:
                logger.exception("Local auto-advance loop failed to start next track: %s", exc)
                self._set_state("stop")

    def _on_auto_advance_done(self, task: asyncio.Task[None]) -> None:
        if self._auto_advance_task is task:
            self._auto_advance_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug("Music auto-advance loop cancelled")
        except Exception as exc:
            logger.exception("Music auto-advance loop crashed: %s", exc)
            # Best-effort restart for resilience.
            try:
                self.start_auto_advance_loop()
            except Exception:
                pass

    def start_auto_advance_loop(self) -> None:
        """Start the background auto-advance loop (idempotent; safe to call multiple times)."""
        if self._auto_advance_task is not None and not self._auto_advance_task.done():
            return
        self._auto_advance_task = asyncio.create_task(self._auto_advance_loop())
        self._auto_advance_task.add_done_callback(self._on_auto_advance_done)
        logger.debug("↻ Music auto-advance loop started")

    async def _play_pos(self, pos: int, seek_s: int = 0) -> None:
        self._cancel_play_failure_skip()
        if not self.queue:
            self.current_pos = -1
            self.state = "stop"
            return
        pos = max(0, min(pos, len(self.queue) - 1))
        self.current_pos = pos
        self.elapsed_anchor_value = float(seek_s)
        ok = await self.player.play(self.queue[pos].file, seek_s=seek_s)
        if ok:
            self._sequential_failed_plays = 0
            self._clear_warning()
        self.browser_file_override = ""
        if self.player.output_route == "browser" and self.player.browser_stream_path:
            try:
                rel = Path(self.player.browser_stream_path).resolve().relative_to(self.library_root)
                self.browser_file_override = rel.as_posix()
            except Exception:
                self.browser_file_override = RUNTIME_MEDIA_PREFIX + Path(self.player.browser_stream_path).name
        self._set_state("play" if ok else "stop")
        if not ok:
            self._sequential_failed_plays += 1
            file_name = Path(self.queue[pos].file).name or self.queue[pos].file
            reason = str(getattr(self.player, "last_error", "") or "unknown playback error")
            message = f"Playback failed for {file_name}: {reason}"
            logger.warning(message)
            self._set_warning(message)
            if len(self.queue) > 0 and self._sequential_failed_plays > len(self.queue):
                logger.warning(
                    "⏹ Stopping auto-skip after %d consecutive failures (queue length %d)",
                    self._sequential_failed_plays,
                    len(self.queue),
                )
                return
            self._schedule_play_failure_skip(pos, self.queue[pos].id)

    async def execute(self, command: str, timeout: float | None = None) -> Dict[str, str]:
        del timeout
        cmd = str(command or "").strip()
        if not cmd:
            return {}
        parts = shlex.split(cmd)
        if not parts:
            return {}

        op = parts[0].lower()

        if op == "status":
            await self._maybe_advance_browser_route()
            await self._maybe_advance_local_route_fallback()
            current = self._current_item()
            track = self.library.get_track(current.file) if current else None
            duration = float(track.get("duration", "0") or 0.0) if track else 0.0
            return {
                "state": self.state,
                "song": str(self.current_pos if self.current_pos >= 0 else -1),
                "songid": str(current.id if current else ""),
                "playlistlength": str(len(self.queue)),
                "playlist": str(self.playlist_version),
                "elapsed": f"{self._elapsed_now():.3f}",
                "duration": f"{duration:.3f}",
                "volume": str(self.volume),
                "random": "1" if self.random_enabled else "0",
                "repeat": "1" if self.repeat_enabled else "0",
                "warning": self.last_warning,
            }

        if op == "currentsong":
            return self._current_track()
        if op == "stats":
            return self.library.stats()

        if op == "play":
            pos = self.current_pos if len(parts) < 2 else int(parts[1])
            if pos < 0:
                pos = 0
            await self._play_pos(pos)
            return {}

        if op == "pause":
            on = len(parts) > 1 and parts[1] == "1"
            if on:
                await self.player.pause()
                self._set_state("pause")
            else:
                if self.state == "pause":
                    await self.player.pause()
                    self._set_state("play")
                elif self.state == "stop":
                    await self._play_pos(max(0, self.current_pos))
            return {}

        if op == "stop":
            await self._reset_playback_context()
            return {}

        if op == "next":
            if self.queue:
                nxt = (self.current_pos + 1) % len(self.queue)
                await self._play_pos(nxt)
            return {}

        if op == "previous":
            if self.queue:
                prv = (self.current_pos - 1) % len(self.queue)
                await self._play_pos(prv)
            return {}

        if op == "seekcur":
            target = int(float(parts[1])) if len(parts) > 1 else 0
            ok = await self.player.seek(target)
            if ok:
                self.elapsed_anchor_value = float(target)
                self.elapsed_anchor_ts = time.monotonic()
                if self.state != "pause":
                    self.state = "play"
            return {}

        if op == "setvol":
            level = int(parts[1]) if len(parts) > 1 else self.volume
            self.volume = max(0, min(100, level))
            return {}

        if op == "clear":
            self.queue = []
            self.current_pos = -1
            await self._reset_playback_context()
            self._touch_playlist()
            return {}

        if op == "add":
            file_uri = parts[1]
            self.queue.append(QueueItem(file=file_uri, id=self._next_id()))
            self._touch_playlist()
            return {}

        if op == "addid":
            file_uri = parts[1]
            pos = int(parts[2]) if len(parts) > 2 else len(self.queue)
            item = QueueItem(file=file_uri, id=self._next_id())
            pos = max(0, min(pos, len(self.queue)))
            self.queue.insert(pos, item)
            if self.current_pos >= pos:
                self.current_pos += 1
            self._touch_playlist()
            return {"Id": str(item.id)}

        if op == "delete":
            pos = int(parts[1])
            if 0 <= pos < len(self.queue):
                del self.queue[pos]
                if self.current_pos == pos:
                    self.current_pos = min(pos, len(self.queue) - 1)
                elif self.current_pos > pos:
                    self.current_pos -= 1
                if not self.queue:
                    await self._reset_playback_context()
                self._touch_playlist()
            return {}

        if op == "deleteid":
            sid = int(parts[1])
            idx = next((i for i, it in enumerate(self.queue) if it.id == sid), -1)
            if idx >= 0:
                await self.execute(f"delete {idx}")
            return {}

        if op == "random":
            self.random_enabled = len(parts) > 1 and parts[1] == "1"
            return {}

        if op == "repeat":
            self.repeat_enabled = len(parts) > 1 and parts[1] == "1"
            return {}

        if op == "load":
            name = parts[1]
            await self._reset_playback_context()
            entries = self.playlists.read_playlist(name)
            self.queue = [QueueItem(file=e, id=self._next_id()) for e in entries]
            self.current_pos = -1
            self._touch_playlist()
            return {}

        if op == "save":
            name = parts[1]
            self.playlists.write_playlist(name, [it.file for it in self.queue])
            return {}

        if op == "rm":
            name = parts[1]
            self.playlists.delete_playlist(name)
            return {}

        if op == "rename":
            old_name = parts[1]
            new_name = parts[2]
            self.playlists.rename_playlist(old_name, new_name)
            return {}

        if op == "playlistadd":
            name = parts[1]
            file_uri = parts[2]
            self.playlists.append_to_playlist(name, file_uri)
            return {}

        if op == "playlistcreate":
            name = parts[1]
            self.playlists.write_playlist(name, [])
            return {}

        if op == "update":
            stats = self.library.stats()
            song_count = int(stats.get("songs", "0") or 0)
            self._indexing_active = True
            try:
                if song_count <= 0:
                    logger.info("Music index update mode=full reason=empty-db")
                    await asyncio.to_thread(self.library.rebuild)
                else:
                    logger.info(
                        "Music index update mode=incremental reason=existing-db songs_before=%d",
                        song_count,
                    )
                    scan_result = await asyncio.to_thread(self.library.scan_incremental)
                    logger.info(
                        "Music index incremental scan result indexed=%d changed=%d",
                        int(scan_result.get("indexed", 0)),
                        int(scan_result.get("changed", 0)),
                    )
            finally:
                self._indexing_active = False
            count = int(self.library.stats().get("songs", "0") or 0)
            self.last_db_update = int(time.time())
            return {"updating_db": "1", "songs": str(count)}

        return {}

    async def execute_list(self, command: str, timeout: float | None = None) -> List[Dict[str, str]]:
        del timeout
        cmd = str(command or "").strip()
        if not cmd:
            return []
        parts = shlex.split(cmd)
        if not parts:
            return []
        op = parts[0].lower()

        if op == "outputs":
            route = self.player.output_route
            return [
                {"outputid": "1", "outputname": "browser", "outputenabled": "1" if route == "browser" else "0"},
                {"outputid": "2", "outputname": "local", "outputenabled": "1" if route != "browser" else "0"},
            ]

        if op == "playlistinfo":
            start = 0
            end = len(self.queue)
            if len(parts) > 1 and ":" in parts[1]:
                a, b = parts[1].split(":", 1)
                start = max(0, int(a or 0))
                end = min(len(self.queue), int(b or len(self.queue)))
            elif len(parts) > 1:
                idx = int(parts[1])
                start = max(0, idx)
                end = min(len(self.queue), idx + 1)
            out: list[dict[str, str]] = []
            for pos in range(start, end):
                it = self.queue[pos]
                # During index writes, avoid per-row DB reads so playlist UX stays responsive.
                if self._indexing_active:
                    track = {"file": it.file}
                else:
                    track = self.library.get_track(it.file) or {"file": it.file}
                row = dict(track)
                row["id"] = str(it.id)
                row["pos"] = str(pos)
                row.setdefault("file", it.file)
                out.append(row)
            return out

        if op == "listplaylists":
            return [{"playlist": name} for name in self.playlists.list_playlists()]

        if op == "listall":
            return self.library.list_all()

        if op == "search":
            if len(parts) < 3:
                return []
            field = parts[1].lower()
            query = parts[2]
            limit = None
            offset = 0
            if "window" in parts:
                i = parts.index("window")
                if i + 1 < len(parts) and ":" in parts[i + 1]:
                    a, b = parts[i + 1].split(":", 1)
                    offset = max(0, int(a or 0))
                    end = max(offset, int(b or offset))
                    limit = max(0, end - offset)
            if field == "file" and not str(query).strip():
                rows = self.library.list_all()
                if limit is None:
                    return rows[offset:]
                return rows[offset : offset + limit]
            return self.library.search(field, query, limit=limit, offset=offset)

        return []


_BACKEND = _NativeMusicBackend()


class NativeMusicConnection:
    _id = 0

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        del host, port
        self.timeout = timeout
        self._connected = False
        NativeMusicConnection._id += 1
        self._label = f"native#{NativeMusicConnection._id}"

    @property
    def label(self) -> str:
        return self._label

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def close(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_command(self, command: str, timeout: float | None = None) -> Dict[str, str]:
        return await _BACKEND.execute(command, timeout)

    async def send_command_list(self, send_cmd: str = "", timeout: float | None = None) -> List[Dict[str, str]]:
        return await _BACKEND.execute_list(send_cmd, timeout)

    async def send_command_batch(self, commands: List[str], timeout: float | None = None) -> None:
        for command in commands:
            await _BACKEND.execute(command, timeout)


class NativeMusicClientPool:
    def __init__(self, host: str = "localhost", port: int = 6600, pool_size: int = 3, timeout: float = 5.0):
        del host, port, pool_size
        self.timeout = timeout
        self._conn = NativeMusicConnection("", 0, timeout)

    async def initialize(self) -> bool:
        await self._conn.connect()
        # Build/update index once per backend startup, even if multiple pools initialize.
        try:
            _BACKEND.start_startup_index()
        except Exception:
            pass
            # Start the persistent queue-advance loop (idempotent).
            try:
                _BACKEND.start_auto_advance_loop()
            except Exception:
                pass
        return True

    async def close(self):
        await self._conn.close()

    async def execute(self, command: str, timeout: float | None = None) -> Dict[str, str]:
        return await self._conn.send_command(command, timeout)

    async def execute_list(self, command: str, timeout: float | None = None) -> List[Dict[str, str]]:
        return await self._conn.send_command_list(command, timeout)

    async def execute_batch(self, commands: List[str], timeout: float | None = None) -> None:
        await self._conn.send_command_batch(commands, timeout)

    def list_playlists_direct(self) -> List[str]:
        return _BACKEND.playlists.list_playlists()

    @asynccontextmanager
    async def acquire(self):
        yield self._conn

    def set_output_route(self, route: str) -> None:
        _BACKEND.set_output_route(route)


# Native-first names for the in-process backend.
