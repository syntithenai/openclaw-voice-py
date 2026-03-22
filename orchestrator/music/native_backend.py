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


@dataclass
class QueueItem:
    file: str
    id: int


class _NativeMusicBackend:
    def __init__(self) -> None:
        workspace = Path(os.getenv("OPENCLAW_WORKSPACE_DIR", str(Path.cwd()))).resolve()
        configured_library = Path(os.getenv("MEDIA_LIBRARY_ROOT", "/music")).expanduser()
        library_root = configured_library.resolve() if configured_library.is_absolute() else (workspace / configured_library).resolve()
        if not library_root.exists():
            try:
                library_root.mkdir(parents=True, exist_ok=True)
            except Exception:
                library_root = (workspace / "music").resolve()
                library_root.mkdir(parents=True, exist_ok=True)
        playlist_root = Path(os.getenv("PLAYLIST_ROOT", str(workspace / "playlists"))).expanduser().resolve()
        db_path = Path(os.getenv("MEDIA_INDEX_DB_PATH", str(workspace / ".media" / "library.sqlite3"))).expanduser().resolve()

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

    async def _play_pos(self, pos: int) -> None:
        if not self.queue:
            self.current_pos = -1
            self.state = "stop"
            return
        pos = max(0, min(pos, len(self.queue) - 1))
        self.current_pos = pos
        self.elapsed_anchor_value = 0.0
        ok = await self.player.play(self.queue[pos].file, seek_s=0)
        self.browser_file_override = ""
        if self.player.output_route == "browser" and self.player.browser_stream_path:
            try:
                rel = Path(self.player.browser_stream_path).resolve().relative_to(self.library_root)
                self.browser_file_override = rel.as_posix()
            except Exception:
                self.browser_file_override = self.queue[pos].file
        self._set_state("play" if ok else "stop")

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
            await self.player.stop()
            self.player.browser_stream_path = ""
            self.browser_file_override = ""
            self._set_state("stop")
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
            await self.player.stop()
            self._set_state("stop")
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
                    await self.player.stop()
                    self._set_state("stop")
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


class MPDConnection:
    _id = 0

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        del host, port
        self.timeout = timeout
        self._connected = False
        MPDConnection._id += 1
        self._label = f"native#{MPDConnection._id}"

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


class MPDClientPool:
    def __init__(self, host: str = "localhost", port: int = 6600, pool_size: int = 3, timeout: float = 5.0):
        del host, port, pool_size
        self.timeout = timeout
        self._conn = MPDConnection("", 0, timeout)

    async def initialize(self) -> bool:
        await self._conn.connect()
        # Build/update index once per backend startup, even if multiple pools initialize.
        try:
            _BACKEND.start_startup_index()
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


# Native-first names for the in-process compatibility backend.
NativeMusicConnection = MPDConnection
NativeMusicClientPool = MPDClientPool
