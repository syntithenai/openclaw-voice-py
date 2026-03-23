"""Music manager with high-level operations for native backend control."""

import asyncio
import difflib
import logging
import os
import sqlite3
import shutil
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional
from .native_client import NativeMusicClientPool
from orchestrator.observability.latency_trace import emit as emit_latency_trace

logger = logging.getLogger(__name__)


def _is_internal_playlist_name(name: str) -> bool:
    playlist_name = str(name or "").strip()
    return playlist_name.startswith("__openclaw_")


def _fuzzy_match_playlists(requested: str, available: List[str], threshold: float = 0.6) -> Optional[str]:
    """
    Fuzzy match a playlist name against available playlists.
    
    Priority:
    1. Exact match
    2. Case-insensitive match
    3. Fuzzy match (using SequenceMatcher for similarity)
    
    Returns the matched playlist name or None if no good match found.
    """
    if not requested or not available:
        return None
    
    requested_lower = requested.lower()
    
    # First try exact match
    for playlist in available:
        if playlist == requested:
            return playlist
    
    # Then try case-insensitive match
    for playlist in available:
        if playlist.lower() == requested_lower:
            return playlist
    
    # Finally try fuzzy match using difflib
    best_match = None
    best_ratio = threshold
    
    for playlist in available:
        # Compare against lowercase versions for better fuzzy matching
        ratio = difflib.SequenceMatcher(None, requested_lower, playlist.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = playlist
    
    return best_match


class MusicManager:
    """High-level music control interface wrapping the native music client pool."""
    
    def __init__(
        self,
        pool: NativeMusicClientPool,
        control_pool: NativeMusicClientPool | None = None,
        genre_queue_limit: int = 120,
        pipewire_stream_normalize_enabled: bool = True,
        pipewire_stream_target_percent: int = 100,
    ):
        self.pool = pool
        self.control_pool = control_pool if control_pool is not None else pool
        self.genre_queue_limit = max(1, int(genre_queue_limit))
        self.pipewire_stream_normalize_enabled = bool(pipewire_stream_normalize_enabled)
        self.pipewire_stream_target_percent = max(1, min(150, int(pipewire_stream_target_percent)))
        self._last_pipewire_normalize_ts = 0.0
        self._loaded_playlist_name: str = ""
        self._loading_playlist_event: asyncio.Event = asyncio.Event()
        self._loading_playlist_event.set()  # Initially not loading
        self._ui_search_cache: Dict[tuple[str, int], tuple[float, List[Dict[str, str]]]] = {}
        self._ui_search_cache_ttl_s = 20.0
        self._ui_search_cache_max_entries = 64
        self._ui_prefix_cache: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
        self._search_metrics: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._search_metrics_counts: dict[str, int] = defaultdict(int)

        self._fts_conn: sqlite3.Connection | None = None
        self._fts_ready = False
        self._fts_building = False
        self._fts_last_revision = ""
        self._fts_last_revision_check_ts = 0.0
        self._fts_revision_check_interval_s = 20.0
        self._fts_batch_size = 1000
        self._fts_last_indexed_count = 0
        self._fts_rebuild_task: asyncio.Task | None = None
        self._fts_indexed_so_far = 0
        self._fts_total_estimate = 0
        self._fts_build_started_ts = 0.0
        self._playlist_names_cache: list[str] = []
        self._playlist_names_cache_ts: float = 0.0
        self._playlist_names_cache_ttl_s: float = 30.0
        self._resume_song_id: str | None = None
        self._resume_elapsed_s: int | None = None

    def _clear_resume_marker(self) -> None:
        self._resume_song_id = None
        self._resume_elapsed_s = None

    @staticmethod
    def _parse_elapsed_seconds(status: Dict[str, str]) -> int | None:
        raw = status.get("elapsed")
        if raw is None:
            return None
        try:
            # Backend reports elapsed as a decimal string (e.g. "123.456").
            return max(0, int(float(raw)))
        except Exception:
            return None

    async def _control_execute(self, command: str, timeout: float | None = None) -> Dict[str, str]:
        return await self.control_pool.execute(command, timeout=timeout)

    async def _control_execute_list(self, command: str, timeout: float | None = None) -> List[Dict[str, str]]:
        return await self.control_pool.execute_list(command, timeout=timeout)

    async def _list_pipewire_music_stream_ids(self) -> List[str]:
        if shutil.which("pactl") is None:
            return []

        proc = await asyncio.create_subprocess_exec(
            "pactl",
            "list",
            "sink-inputs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.debug(
                "Skipping PipeWire music stream sync: pactl list failed: %s",
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            return []

        text = stdout.decode("utf-8", errors="ignore")
        blocks = text.split("Sink Input #")
        stream_ids: List[str] = []

        for block in blocks[1:]:
            lines = block.splitlines()
            if not lines:
                continue
            sink_input_id = lines[0].strip()
            if not sink_input_id:
                continue
            if (
                'application.name = "Music Player Daemon"' in block
                or 'application.name = "ffplay"' in block
                or 'application.process.binary = "ffplay"' in block
            ):
                stream_ids.append(sink_input_id)

        return stream_ids

    async def _set_pipewire_music_stream_volume(self, target_percent: int) -> bool:
        if not self.pipewire_stream_normalize_enabled:
            return False

        stream_ids = await self._list_pipewire_music_stream_ids()
        if not stream_ids:
            return False

        target = f"{max(0, min(150, int(target_percent)))}%"
        updated = False
        for sink_input_id in stream_ids:
            set_proc = await asyncio.create_subprocess_exec(
                "pactl",
                "set-sink-input-volume",
                sink_input_id,
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, set_err = await set_proc.communicate()
            if set_proc.returncode != 0:
                logger.debug(
                    "Failed to sync music PipeWire stream volume (sink-input=%s): %s",
                    sink_input_id,
                    set_err.decode("utf-8", errors="ignore").strip(),
                )
                continue

            updated = True
            logger.info(
                "🎚️ Synced music PipeWire stream volume: sink-input=%s target=%s",
                sink_input_id,
                target,
            )

        return updated

    async def _normalize_pipewire_music_stream_volume(self) -> None:
        """Best-effort: align PipeWire per-app stream volume with current music volume."""
        if not self.pipewire_stream_normalize_enabled:
            return

        # Avoid hammering pactl when play() is called repeatedly.
        now = time.monotonic()
        if (now - self._last_pipewire_normalize_ts) < 2.0:
            return
        self._last_pipewire_normalize_ts = now

        try:
            current = await self.get_volume()
            target = self.pipewire_stream_target_percent if current is None else min(current, self.pipewire_stream_target_percent)
            await self._set_pipewire_music_stream_volume(target)
        except Exception as exc:
            logger.debug("PipeWire music stream normalize skipped: %s", exc)
    
    # ========== Playback Control ==========
    
    async def play(self, position: Optional[int] = None) -> str:
        """
        Start or resume playback.
        
        Waits for any pending playlist load to complete before starting playback,
        to ensure the correct playlist is in the queue.
        
        Args:
            position: Optional queue position to start from (0-indexed)
        
        Returns:
            Success message
        """
        # Wait for any pending playlist load to complete
        if not self._loading_playlist_event.is_set():
            logger.info("⏸ Play command waiting for pending playlist load to complete...")
            await self._loading_playlist_event.wait()
            logger.info("✓ Playlist load completed, resuming play command")
        
        try:
            if position is not None:
                self._clear_resume_marker()
                await self.pool.execute(f"play {position}")
                await self._normalize_pipewire_music_stream_volume()
                return f"Playing track {position + 1}"
            else:
                if self._resume_song_id is not None and self._resume_elapsed_s is not None:
                    resume_id = self._resume_song_id
                    resume_elapsed = self._resume_elapsed_s
                    self._clear_resume_marker()
                    await self.pool.execute("play")
                    # With the native player, seekcur restarts ffplay at the target offset.
                    # There is no async race: play() is fully synchronous, seekcur runs
                    # immediately after in the same event loop turn.
                    if resume_elapsed > 0:
                        try:
                            status = await self.pool.execute("status")
                            if status.get("songid") == resume_id:
                                await self.pool.execute(f"seekcur {resume_elapsed}")
                        except Exception as seek_exc:
                            logger.warning("Resume seek failed (will play from start): %s", seek_exc)
                else:
                    await self.pool.execute("play")
                await self._normalize_pipewire_music_stream_volume()
                return "Playback started"
        except Exception as e:
            logger.error(f"Failed to play: {e}")
            return f"Error: {e}"

    async def seek_to(self, seconds: float) -> str:
        """Seek to absolute position (seconds) in current track."""
        try:
            target = max(0, int(float(seconds)))
            await self.pool.execute(f"seekcur {target}")
            return f"Seeked to {target}s"
        except Exception as e:
            logger.error(f"Failed to seek: {e}")
            return f"Error: {e}"
    
    async def pause(self) -> str:
        """Pause playback."""
        try:
            status = await self.pool.execute("status")
            state = status.get("state", "stop")
            
            if state == "play":
                await self.pool.execute("pause 1")
                return "Paused"
            elif state == "pause":
                await self.pool.execute("pause 0")
                return "Resumed"
            else:
                return "Not playing"
        except Exception as e:
            logger.error(f"Failed to pause: {e}")
            return f"Error: {e}"

    async def pause_if_playing(self) -> bool:
        """Pause playback only when state is 'play'. Returns True if paused."""
        try:
            status = await self.pool.execute("status")
            if status.get("state", "stop") == "play":
                await self.pool.execute("pause 1")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to pause_if_playing: {e}")
            return False
    
    async def stop(self) -> str:
        """Stop playback."""
        try:
            status_before_stop = await self.pool.execute("status")
            state_before_stop = status_before_stop.get("state", "stop") if status_before_stop else "stop"
            elapsed_before_stop = self._parse_elapsed_seconds(status_before_stop or {})
            # Use songid (unique, stable) rather than song (queue position) so that
            # seekid after play targets the exact queue entry.
            songid_before_stop = status_before_stop.get("songid") if status_before_stop else None

            if state_before_stop in {"play", "pause"} and songid_before_stop and elapsed_before_stop is not None:
                self._resume_song_id = str(songid_before_stop)
                self._resume_elapsed_s = elapsed_before_stop
            else:
                self._clear_resume_marker()

            await self.pool.execute("stop")
            # Verify stop actually took effect; if state still reports play,
            # force a pause and issue stop again as a fallback.
            status = await self.pool.execute("status")
            state = status.get("state", "stop") if status else "stop"
            if state == "play":
                await self.pool.execute("pause 1")
                await self.pool.execute("stop")
                status = await self.pool.execute("status")
                state = status.get("state", "stop") if status else "stop"
                if state == "play":
                    logger.warning("Music stop fallback executed but state is still 'play'")
                    return "Error: failed to stop playback"
            return "Stopped"
        except Exception as e:
            logger.error(f"Failed to stop: {e}")
            return f"Error: {e}"
    
    async def next_track(self) -> str:
        """Skip to next track."""
        try:
            await self.pool.execute("next")
            return "Skipped to next track"
        except Exception as e:
            logger.error(f"Failed to skip: {e}")
            return f"Error: {e}"
    
    async def previous_track(self) -> str:
        """Go to previous track."""
        try:
            await self.pool.execute("previous")
            return "Playing previous track"
        except Exception as e:
            logger.error(f"Failed to go to previous: {e}")
            return f"Error: {e}"
    
    # ========== Volume Control ==========
    
    async def set_volume(self, level: int) -> str:
        """
        Set volume level.
        
        Args:
            level: Volume level (0-100)
        
        Returns:
            Success message
        """
        try:
            level = max(0, min(100, level))
            await self.pool.execute(f"setvol {level}")
            try:
                await self._set_pipewire_music_stream_volume(level)
            except Exception as exc:
                logger.debug("Failed to sync live music stream volume to %s%%: %s", level, exc)
            return f"Volume set to {level}%"
        except Exception as e:
            logger.error(f"Failed to set volume: {e}")
            return f"Error: {e}"
    
    async def get_volume(self) -> Optional[int]:
        """Get current volume level (0-100)."""
        try:
            status = await self.pool.execute("status")
            vol_str = status.get("volume", "50")
            return int(vol_str)
        except Exception as e:
            logger.error(f"Failed to get volume: {e}")
            return None
    
    async def volume_up(self, amount: int = 10) -> str:
        """Increase volume."""
        current = await self.get_volume()
        if current is None:
            return "Failed to get current volume"
        new_vol = min(100, current + amount)
        return await self.set_volume(new_vol)
    
    async def volume_down(self, amount: int = 10) -> str:
        """Decrease volume."""
        current = await self.get_volume()
        if current is None:
            return "Failed to get current volume"
        new_vol = max(0, current - amount)
        return await self.set_volume(new_vol)
    
    # ========== Status and Info ==========
    
    async def get_status(self) -> Dict[str, str]:
        """Get current playback status."""
        try:
            return await self.pool.execute("status")
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return {}
    
    async def get_current_track(self) -> Dict[str, str]:
        """Get information about currently playing track."""
        try:
            return await self.pool.execute("currentsong")
        except Exception as e:
            logger.error(f"Failed to get current track: {e}")
            return {}
    
    async def get_stats(self) -> Dict[str, str]:
        """Get library statistics."""
        try:
            return await self.pool.execute("stats")
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

    async def get_outputs(self) -> List[Dict[str, str]]:
        """Return configured music outputs."""
        try:
            return await self.pool.execute_list("outputs")
        except Exception as e:
            logger.error(f"Failed to get outputs: {e}")
            return []

    async def get_enabled_output_names(self) -> List[str]:
        """Return enabled output names from output list."""
        outputs = await self.get_outputs()
        enabled: List[str] = []
        for output in outputs:
            name = output.get("outputname", "unknown")
            if str(output.get("outputenabled", "0")).strip() == "1":
                enabled.append(name)
        return enabled
    
    # ========== Search and Browse ==========
    
    async def search_artist(self, artist: str) -> List[Dict[str, str]]:
        """Search for tracks by artist."""
        try:
            return await self.pool.execute_list(f'search artist "{artist}"')
        except Exception as e:
            logger.error(f"Failed to search artist: {e}")
            return []
    
    async def search_album(self, album: str) -> List[Dict[str, str]]:
        """Search for tracks by album."""
        try:
            return await self.pool.execute_list(f'search album "{album}"')
        except Exception as e:
            logger.error(f"Failed to search album: {e}")
            return []
    
    async def search_title(self, title: str) -> List[Dict[str, str]]:
        """Search for tracks by title."""
        try:
            return await self.pool.execute_list(f'search title "{title}"')
        except Exception as e:
            logger.error(f"Failed to search title: {e}")
            return []
    
    async def search_genre(self, genre: str) -> List[Dict[str, str]]:
        """Search for tracks by genre."""
        try:
            return await self.pool.execute_list(f'search genre "{genre}"')
        except Exception as e:
            logger.error(f"Failed to search genre: {e}")
            return []
    
    async def search_any(self, query: str) -> List[Dict[str, str]]:
        """Search for tracks matching any field."""
        try:
            return await self.pool.execute_list(f'search any "{query}"')
        except Exception as e:
            logger.error(f"Failed to search: {e}")
            return []
    
    # ========== Queue Management ==========

    def get_loaded_playlist_name(self) -> str:
        """Return currently loaded playlist name, if any."""
        return str(self._loaded_playlist_name or "").strip()

    def _detach_loaded_playlist(self) -> None:
        """Mark the current queue as no longer backed by a saved playlist."""
        if self._loaded_playlist_name:
            logger.info(
                "Detaching active queue from saved playlist '%s'",
                self._loaded_playlist_name,
            )
        self._loaded_playlist_name = ""

    @staticmethod
    def _quote(value: str) -> str:
        return str(value).replace('\\', '\\\\').replace('"', '\\"')

    async def _list_all_playlists(self) -> List[str]:
        """List all playlists, including internal runtime snapshots."""
        now = time.monotonic()
        if self._playlist_names_cache and (now - self._playlist_names_cache_ts) <= self._playlist_names_cache_ttl_s:
            return list(self._playlist_names_cache)

        for attempt in (1, 2):
            try:
                emit_latency_trace("music_load.list_playlists_start", attempt=attempt)
                result = await self._control_execute_list("listplaylists", timeout=8.0)
                emit_latency_trace("music_load.list_playlists_done", attempt=attempt, count=len(result))
                playlists = [item.get("playlist", "") for item in result if "playlist" in item]
                self._playlist_names_cache = [p for p in playlists if str(p).strip()]
                self._playlist_names_cache_ts = time.monotonic()
                return list(self._playlist_names_cache)
            except Exception as e:
                emit_latency_trace("music_load.list_playlists_error", attempt=attempt, error=str(e))
                if attempt == 1:
                    logger.warning(f"list_playlists attempt 1 failed, retrying: {e}")
                    await asyncio.sleep(0.05)
                    continue
                logger.error(f"Failed to list playlists: {e}")
                return []
        return []

    @staticmethod
    def _artist_bucket_key(track: Dict[str, str], fallback_index: int) -> str:
        artist = str(track.get("Artist") or track.get("artist") or "").strip().lower()
        if artist:
            return artist

        album_artist = str(track.get("AlbumArtist") or track.get("albumartist") or "").strip().lower()
        if album_artist:
            return album_artist

        file_uri = str(track.get("file") or "").strip().lower()
        if file_uri:
            return file_uri

        return f"unknown-{fallback_index}"

    def _spread_tracks_across_artists(self, tracks: List[Dict[str, str]]) -> List[Dict[str, str]]:
        import random

        if len(tracks) <= 1:
            return list(tracks)

        artist_buckets: dict[str, deque[Dict[str, str]]] = {}
        for index, track in enumerate(tracks):
            bucket_key = self._artist_bucket_key(track, index)
            artist_buckets.setdefault(bucket_key, deque()).append(track)

        artist_keys = list(artist_buckets.keys())
        random.shuffle(artist_keys)
        for artist_key in artist_keys:
            bucket_items = list(artist_buckets[artist_key])
            random.shuffle(bucket_items)
            artist_buckets[artist_key] = deque(bucket_items)

        ordered: List[Dict[str, str]] = []
        while artist_keys:
            next_round: List[str] = []
            for artist_key in artist_keys:
                bucket = artist_buckets[artist_key]
                if not bucket:
                    continue
                ordered.append(bucket.popleft())
                if bucket:
                    next_round.append(artist_key)
            random.shuffle(next_round)
            artist_keys = next_round

        return ordered

    async def add_songs_to_queue(self, query: str, count: int = 5) -> str:
        """Append matching songs to the END of the current queue without clearing it.

        Tries a genre-specific search first; falls back to any-field search.
        Picks up to *count* random tracks from the results.
        """
        import random
        try:
            count = max(1, min(50, int(count)))
            safe_q = str(query or "").strip().replace('"', '\\"')
            if not safe_q:
                return "Search query is required"

            limit = count * 10  # fetch a larger pool for random selection
            tracks: List[Dict[str, str]] = []

            async def _search_backend(field: str) -> List[Dict[str, str]]:
                try:
                    rows = await self.pool.execute_list(f'search {field} "{safe_q}" window 0:{limit - 1}')
                    if rows:
                        return rows
                except Exception:
                    pass
                try:
                    return await self.pool.execute_list(f'search {field} "{safe_q}"')
                except Exception:
                    return []

            # Try genre-specific search first (most accurate for genre requests)
            tracks = await _search_backend("genre")

            # Fallback: any-field search
            if not tracks:
                tracks = await _search_backend("any")

            # During index writes, search can transiently return no rows.
            # Retry once briefly before declaring a miss.
            if not tracks:
                await asyncio.sleep(0.15)
                tracks = await _search_backend("genre")
                if not tracks:
                    tracks = await _search_backend("any")

            # Align with the Add Songs page: if backend search still misses,
            # use the UI library search path (indexed path/title/artist/album matching).
            if not tracks and len(safe_q) >= 3:
                try:
                    tracks = await self.search_library_for_ui(safe_q, limit=max(limit, 50))
                except Exception:
                    tracks = []

            if not tracks:
                return f"No songs found matching: {query}"

            selected = random.sample(tracks, min(count, len(tracks)))
            files = [t.get("file", "") for t in selected if t.get("file", "")]
            if not files:
                return f"No playable songs found matching: {query}"

            result = await self.add_many_to_queue(files, batch_size=40)
            if str(result).lower().startswith("error"):
                return result

            added = len(files)
            logger.info("\u2795 add_songs_to_queue '%s' count=%d: added %d", query, count, added)
            return f"Added {added} {'song' if added == 1 else 'songs'} to queue"
        except Exception as e:
            logger.error("Failed to add songs to queue '%s': %s", query, e)
            return f"Error: {e}"
    
    async def add_songs_to_playlist(self, query: str, count: int = 5) -> str:
        """Add matching songs to the loaded/active playlist.
        
        Searches for songs matching the query and adds them to the current loaded playlist.
        Requires a loaded playlist (e.g., from create_playlist or load_playlist).
        Supports artist names (e.g., "joni mitchell"), genres (e.g., "rock"), or any-field queries.
        
        Songs are randomly selected from the ENTIRE collection matching the query,
        not just the first N results.
        """
        import random
        try:
            if not self._loaded_playlist_name:
                return "No active playlist. Create or load a playlist first."
            
            count = max(1, min(100, int(count)))
            safe_q = str(query or "").strip().replace('"', '\\"')
            if not safe_q:
                return "Search query is required"

            tracks: List[Dict[str, str]] = []
            
            # Detect if query is likely a genre (single word, common genres)
            common_genres = [
                "rock", "pop", "jazz", "blues", "classical", "country",
                "hip-hop", "hiphop", "rap", "metal", "punk", "folk",
                "electronic", "dance", "reggae", "soul", "funk", "indie",
                "alternative", "ambient", "ballad", "bebop", "bluegrass",
                "bossa nova", "breakbeat", "britpop", "cajun", "calypso",
                "chamber", "chillout", "cool jazz", "darkwave", "death metal",
                "deep house", "delta blues", "dub", "dubstep", "easy listening",
                "folk rock", "funk rock", "fusion", "garage", "garage rock",
                "glam", "glitch", "gospel", "goth", "grunge", "hardcore",
                "hard rock", "heavy metal", "house", "indie pop", "indie rock",
                "industrial", "instrumental", "jazz funk", "jump blues", "k-pop",
                "latin", "lo-fi", "lounge", "metal", "metalcore", "minimal",
                "modern jazz", "new wave", "noise", "nu metal", "nu soul",
            ]
            
            is_likely_genre = safe_q.lower() in common_genres

            # Fetch all matching songs (use large window to get entire collection)
            # Try genre-specific search first if it looks like a genre
            if is_likely_genre:
                try:
                    # Fetch all genre matches with a large window
                    rows = await self.pool.execute_list(f'search genre "{safe_q}" window 0:999999')
                    if rows:
                        tracks = rows
                except Exception:
                    try:
                        tracks = await self.pool.execute_list(f'search genre "{safe_q}"')
                    except Exception:
                        pass
            
            # Try artist-specific search if no genre match
            if not tracks:
                try:
                    # Fetch all artist matches with a large window
                    rows = await self.pool.execute_list(f'search artist "{safe_q}" window 0:999999')
                    if rows:
                        tracks = rows
                except Exception:
                    try:
                        tracks = await self.pool.execute_list(f'search artist "{safe_q}"')
                    except Exception:
                        pass

            # Fallback: any-field search
            if not tracks:
                try:
                    # Fetch all matches with a large window
                    rows = await self.pool.execute_list(f'search any "{safe_q}" window 0:999999')
                    if rows:
                        tracks = rows
                except Exception:
                    try:
                        tracks = await self.pool.execute_list(f'search any "{safe_q}"')
                    except Exception:
                        pass

            if not tracks:
                return f"No songs found matching: {query}"

            # Randomly sample from ALL matching songs (entire collection)
            selected = random.sample(tracks, min(count, len(tracks)))
            files = [t.get("file", "") for t in selected if t.get("file", "")]
            if not files:
                return f"No playable songs found matching: {query}"

            # Add each file to the playlist
            for file_uri in files:
                try:
                    await self._control_execute(
                        f'playlistadd "{self._quote(self._loaded_playlist_name)}" "{self._quote(file_uri)}"'
                    )
                except Exception as e:
                    logger.warning(f"Failed to add song to playlist: {e}")

            # Also append to the live queue so the UI reflects the change immediately.
            try:
                await self.add_many_to_queue(files, batch_size=40)
            except Exception as add_exc:
                logger.warning("add_songs_to_playlist: failed to append to live queue: %s", add_exc)

            added = len(files)
            total_available = len(tracks)
            logger.info(f"⭐ add_songs_to_playlist '{self._loaded_playlist_name}': added {added}/{total_available} songs matching '{query}'")
            return f"Added {added} {'song' if added == 1 else 'songs'} (from {total_available} available) to {self._loaded_playlist_name}"
        except Exception as e:
            logger.error("Failed to add songs to playlist '%s': %s", query, e)
            return f"Error: {e}"
    
    async def clear_queue(self) -> str:
        """Clear the playback queue."""
        try:
            await self.pool.execute("clear")
            self._detach_loaded_playlist()
            return "Queue cleared"
        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")
            return f"Error: {e}"
    
    async def add_to_queue(self, uri: str) -> str:
        """
        Add a track or directory to the queue.
        
        Args:
            uri: Track URI (e.g., "Artist/Album/track.mp3")
        
        Returns:
            Success message
        """
        try:
            await self.pool.execute(f'add "{uri}"')
            return f"Added to queue"
        except Exception as e:
            logger.error(f"Failed to add to queue: {e}")
            return f"Error: {e}"

    async def add_many_to_queue(self, uris: List[str], batch_size: int = 40) -> str:
        """Add multiple tracks to the queue efficiently using backend command batches."""
        cleaned = [str(uri).strip() for uri in uris if str(uri).strip()]
        if not cleaned:
            return "No tracks to add"

        try:
            chunk = max(1, int(batch_size))
            for start in range(0, len(cleaned), chunk):
                commands = [f'add "{self._quote(uri)}"' for uri in cleaned[start:start + chunk]]
                await self.pool.execute_batch(commands, timeout=15.0)
            return f"Added {len(cleaned)} tracks to queue"
        except Exception as e:
            logger.error(f"Failed to add multiple tracks to queue: {e}")
            return f"Error: {e}"
    
    async def get_queue(self, limit: int = 500, timeout: float | None = None) -> List[Dict[str, str]]:
        """Get current queue contents (limited to avoid overwhelming huge playlists).
        
        Args:
            limit: Maximum number of items to fetch (default 500). Use None for unlimited.
            timeout: Optional query timeout override in seconds.
        
        Returns:
            List of queue items
        """
        try:
            # For huge queues, full playlistinfo can be expensive.
            # So we always limit the response. Clients can paginate if needed.
            t0 = time.monotonic()
            if limit is None:
                # Large queries need extra time; 60s for potentially slow connections
                query_timeout = float(timeout) if timeout is not None else 60.0
                result = await self.pool.execute_list("playlistinfo", timeout=query_timeout)
            else:
                cmd = f"playlistinfo 0:{limit-1}"
                # Scale timeout based on limit; 200 items = 45s, 500 items = 60s
                query_timeout = float(timeout) if timeout is not None else max(45.0, min(60.0, 30.0 + (limit / 10)))
                result = await self.pool.execute_list(cmd, timeout=query_timeout)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if len(result) > 50:
                logger.info(f"⏱️ playlistinfo 0:{limit-1} returned {len(result)} items in {elapsed_ms:.1f}ms")
            return result
        except Exception as e:
            logger.error(f"Failed to get queue: {e}")
            return []

    async def remove_from_queue_positions(
        self,
        positions: List[int],
        song_ids: Optional[List[str]] = None,
    ) -> str:
        """Remove selected queue items.

        Prefers stable song IDs (`deleteid`) when available to avoid position drift.
        If the currently playing item is removed, playback advances to the next item.
        """
        try:
            status_before = await self.pool.execute("status")
            state_before = str(status_before.get("state", "stop")) if status_before else "stop"
            try:
                current_song_id = str(status_before.get("songid", "")).strip() if status_before else ""
            except Exception:
                current_song_id = ""
            try:
                current_pos = int(status_before.get("song", -1)) if status_before else -1
            except Exception:
                current_pos = -1

            selected_song_ids = [str(s).strip() for s in (song_ids or []) if str(s).strip()]
            removed_count = 0
            removed_current = False

            if selected_song_ids:
                uniq_ids = sorted(set(selected_song_ids))
                if not uniq_ids:
                    return "No queue items selected"
                removed_current = bool(current_song_id and current_song_id in uniq_ids)
                for sid in uniq_ids:
                    await self.pool.execute(f"deleteid {sid}")
                    removed_count += 1
            else:
                uniq_positions = sorted({int(p) for p in positions if int(p) >= 0}, reverse=True)
                if not uniq_positions:
                    return "No queue items selected"
                removed_current = current_pos in uniq_positions
                for pos in uniq_positions:
                    await self.pool.execute(f"delete {pos}")
                    removed_count += 1

            if removed_current and state_before == "play":
                status_after = await self.pool.execute("status")
                try:
                    queue_len_after = int(status_after.get("playlistlength", 0)) if status_after else 0
                except Exception:
                    queue_len_after = 0
                if queue_len_after > 0:
                    next_pos = current_pos
                    if next_pos < 0:
                        next_pos = 0
                    if next_pos >= queue_len_after:
                        next_pos = queue_len_after - 1
                    await self.pool.execute(f"play {next_pos}")

            return f"Removed {removed_count} queue item(s)"
        except Exception as e:
            logger.error(f"Failed to remove queue items: {e}")
            return f"Error: {e}"

    async def add_files_to_queue(self, files: List[str]) -> str:
        """Add multiple file URIs to the top of the queue and focus the new head."""
        try:
            cleaned = [str(f).strip() for f in files if str(f).strip()]
            if not cleaned:
                return "No tracks selected"

            status_before = await self.pool.execute("status")
            state_before = str(status_before.get("state", "stop") or "stop")
            added = 0
            failed = 0

            # Insert at queue position 0 in reverse order so the first requested
            # track ends up at the top of the playlist.
            for file_uri in reversed(cleaned):
                try:
                    await self.pool.execute(f'addid "{self._quote(file_uri)}" 0')
                    added += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("Failed to add file to queue '%s': %s", file_uri, exc)

            if added == 0:
                return "Error: Failed to add selected tracks to queue"

            # The backend has no direct "select queue item without playback side effects"
            # command, so move focus to the new head and then restore the prior
            # non-playing state as closely as possible.
            await self.pool.execute("play 0")
            if state_before == "pause":
                await self.pool.execute("pause 1")
            elif state_before == "stop":
                await self.pool.execute("stop")
                status_after_stop = await self.pool.execute("status")
                try:
                    current_pos_after_stop = int(status_after_stop.get("song", -1) or -1)
                except Exception:
                    current_pos_after_stop = -1
                if current_pos_after_stop != 0:
                    await self.pool.execute("play 0")
                    await self.pool.execute("pause 1")

            if failed > 0:
                return f"Added {added} track(s) to queue ({failed} failed)"
            return f"Added {added} track(s) to queue"
        except Exception as e:
            logger.error(f"Failed to add files to queue: {e}")
            return f"Error: {e}"

    async def create_playlist_from_queue_positions(self, name: str, positions: List[int]) -> str:
        """Create/replace playlist from selected queue positions."""
        try:
            playlist_name = str(name or "").strip()
            if not playlist_name:
                return "Playlist name is required"

            selected = {int(p) for p in positions if int(p) >= 0}
            if not selected:
                return "No queue items selected"

            queue = await self.get_queue()
            files: List[str] = []
            for item in queue:
                try:
                    pos = int(item.get("pos", item.get("Pos", -1)))
                except Exception:
                    pos = -1
                if pos in selected:
                    file_uri = str(item.get("file", "")).strip()
                    if file_uri:
                        files.append(file_uri)

            if not files:
                return "No valid files found for selected queue items"

            try:
                await self.pool.execute(f'rm "{self._quote(playlist_name)}"')
            except Exception:
                pass

            for file_uri in files:
                await self.pool.execute(
                    f'playlistadd "{self._quote(playlist_name)}" "{self._quote(file_uri)}"'
                )

            self._playlist_names_cache = [p for p in self._playlist_names_cache if p.lower() != playlist_name.lower()]
            self._playlist_names_cache.append(playlist_name)
            self._playlist_names_cache_ts = time.monotonic()

            return f"Created playlist '{playlist_name}' with {len(files)} track(s)"
        except Exception as e:
            logger.error(f"Failed to create playlist from queue positions: {e}")
            return f"Error: {e}"

    def _ensure_fts_conn(self) -> sqlite3.Connection:
        if self._fts_conn is None:
            workspace_dir = os.getenv("OPENCLAW_WORKSPACE_DIR", os.path.join(os.getcwd(), ".openclaw"))
            media_dir = os.path.join(workspace_dir, ".media")
            os.makedirs(media_dir, exist_ok=True)
            db_path = os.path.join(media_dir, "music_search_idx.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS music_search_idx USING fts5(file, title, artist, album, searchable)"
            )
            try:
                existing_rows = int(conn.execute("SELECT COUNT(*) FROM music_search_idx").fetchone()[0])
            except Exception:
                existing_rows = 0
            if existing_rows > 0:
                self._fts_ready = True
                self._fts_last_indexed_count = existing_rows
                logger.info("🔎 Using existing music FTS index: %d rows", existing_rows)
            self._fts_conn = conn
        return self._fts_conn

    async def _current_library_revision(self) -> str:
        stats = await self.get_stats()
        songs = str(stats.get("songs", "0"))
        db_update = str(stats.get("db_update", "0"))
        return f"{songs}:{db_update}"

    def _fts_progress_text(self) -> str:
        indexed = int(self._fts_indexed_so_far or 0)
        total = int(self._fts_total_estimate or 0)
        elapsed = max(0.0, time.monotonic() - float(self._fts_build_started_ts or 0.0))
        if total > 0:
            pct = min(100.0, (indexed / total) * 100.0)
            return (
                f"Library index is building ({indexed}/{total}, {pct:.1f}%). "
                f"Please retry in a few seconds."
            )
        return f"Library index is building ({indexed} indexed in {elapsed:.1f}s). Please retry in a few seconds."

    def _fts_query_expr(self, query: str) -> str:
        terms = [t.strip().replace('"', '""') for t in query.split() if t.strip()]
        if not terms:
            return "*"
        return " ".join(f'"{term}"*' for term in terms)

    async def _rebuild_fts_index(self) -> None:
        if self._fts_building:
            return
        self._fts_building = True
        try:
            conn = self._ensure_fts_conn()
            conn.execute("DELETE FROM music_search_idx")
            conn.commit()
            self._fts_ready = False

            inserted = 0
            offset = 0
            batch = max(100, int(self._fts_batch_size))
            pending: List[tuple] = []
            commit_every = batch
            self._fts_build_started_ts = time.monotonic()
            self._fts_indexed_so_far = 0
            try:
                stats = await self.get_stats()
                self._fts_total_estimate = int(stats.get("songs", 0) or 0)
            except Exception:
                self._fts_total_estimate = 0
            while True:
                cmd = f'search file "" window {offset}:{offset + batch}'
                rows = await self.pool.execute_list(cmd)
                if not rows:
                    break

                for item in rows:
                    file_uri = str(item.get("file", "")).strip()
                    if not file_uri:
                        continue
                    title = str(item.get("title") or item.get("Title") or file_uri.split("/")[-1])
                    artist = str(item.get("artist") or item.get("Artist") or "")
                    album = str(item.get("album") or item.get("Album") or "")
                    searchable = f"{title} {artist} {album} {file_uri}".strip().lower()
                    pending.append((file_uri, title, artist, album, searchable))

                if len(pending) >= commit_every:
                    conn.executemany(
                        "INSERT INTO music_search_idx(file,title,artist,album,searchable) VALUES (?,?,?,?,?)",
                        pending,
                    )
                    inserted += len(pending)
                    pending = []
                    conn.commit()
                    self._fts_ready = inserted > 0
                    self._fts_indexed_so_far = inserted
                    if self._fts_total_estimate > 0:
                        pct = min(100.0, (inserted / self._fts_total_estimate) * 100.0)
                        logger.info(
                            "🔎 FTS indexing progress: %d/%d (%.1f%%)",
                            inserted,
                            self._fts_total_estimate,
                            pct,
                        )
                    else:
                        logger.info("🔎 FTS indexing progress: %d rows", inserted)

                if len(rows) < batch:
                    break
                offset += batch

            if pending:
                conn.executemany(
                    "INSERT INTO music_search_idx(file,title,artist,album,searchable) VALUES (?,?,?,?,?)",
                    pending,
                )
                inserted += len(pending)
                self._fts_indexed_so_far = inserted

            conn.commit()
            self._fts_last_indexed_count = inserted
            self._fts_last_revision = await self._current_library_revision()
            self._fts_ready = inserted > 0
            self._ui_prefix_cache.clear()
            logger.info("🔎 Built music FTS index: %d rows (revision=%s)", inserted, self._fts_last_revision)
        except Exception as exc:
            logger.warning("Music FTS index build failed; falling back to direct search: %s", exc)
            self._fts_ready = False
        finally:
            self._fts_building = False

    async def _maybe_refresh_fts_index(self) -> None:
        now = time.monotonic()
        if (now - self._fts_last_revision_check_ts) < self._fts_revision_check_interval_s:
            return
        self._fts_last_revision_check_ts = now

        current_revision = await self._current_library_revision()
        if not self._fts_ready or current_revision != self._fts_last_revision:
            if self._fts_rebuild_task is None or self._fts_rebuild_task.done():
                self._fts_rebuild_task = asyncio.create_task(self._rebuild_fts_index())

    def _record_search_metric(self, source: str, query: str, elapsed_ms: float) -> None:
        q_len = len(query.strip())
        if q_len <= 4:
            bucket = "len_3_4"
        elif q_len <= 8:
            bucket = "len_5_8"
        else:
            bucket = "len_9_plus"

        key = f"{source}:{bucket}"
        store = self._search_metrics[key]
        store.append(float(elapsed_ms))
        self._search_metrics_counts[key] += 1

        if self._search_metrics_counts[key] % 20 == 0 and len(store) >= 5:
            ordered = sorted(store)
            p50 = ordered[int(0.50 * (len(ordered) - 1))]
            p95 = ordered[int(0.95 * (len(ordered) - 1))]
            logger.info(
                "📊 Music search latency %s count=%d p50=%.1fms p95=%.1fms",
                key,
                self._search_metrics_counts[key],
                p50,
                p95,
            )

    async def search_library_for_ui(self, query: str, limit: int = 300) -> List[Dict[str, str]]:
        """Search library and return normalized results for Web UI selection list."""
        q = str(query or "").strip()
        if len(q) < 3:
            return []
        
        start_ms = time.monotonic() * 1000
        try:
            safe_limit = max(1, int(limit))
            q_norm = " ".join(q.lower().split())
            cache_key = (q_norm, safe_limit)
            cached = self._ui_search_cache.get(cache_key)
            if cached:
                cached_ts, cached_rows = cached
                if (time.monotonic() - cached_ts) <= self._ui_search_cache_ttl_s:
                    elapsed = time.monotonic() * 1000 - start_ms
                    logger.info(f"🔍 Music search (cache hit): '{q}' → {len(cached_rows)} results in {elapsed:.1f}ms")
                    self._record_search_metric("exact_cache", q, elapsed)
                    return [dict(row) for row in cached_rows]

            prefix_rows: List[Dict[str, str]] | None = None
            for prefix_len in range(len(q_norm) - 1, 2, -1):
                prefix = q_norm[:prefix_len]
                pref_cached = self._ui_prefix_cache.get(prefix)
                if not pref_cached:
                    continue
                pref_ts, pref_rows = pref_cached
                if (time.monotonic() - pref_ts) > self._ui_search_cache_ttl_s:
                    continue

                filtered: List[Dict[str, str]] = []
                for row in pref_rows:
                    hay = (
                        f"{row.get('title', '')} {row.get('artist', '')} "
                        f"{row.get('album', '')} {row.get('file', '')}"
                    ).lower()
                    if q_norm in hay:
                        filtered.append(dict(row))
                        if len(filtered) >= safe_limit:
                            break
                prefix_rows = filtered
                break

            if prefix_rows is not None:
                elapsed = time.monotonic() * 1000 - start_ms
                self._ui_search_cache[cache_key] = (time.monotonic(), [dict(row) for row in prefix_rows])
                self._ui_prefix_cache[q_norm] = (time.monotonic(), [dict(row) for row in prefix_rows])
                logger.info(f"🔍 Music search (prefix cache): '{q}' → {len(prefix_rows)} results in {elapsed:.1f}ms")
                self._record_search_metric("prefix_cache", q, elapsed)
                return prefix_rows

            # Ensure SQLite/FTS connection is opened so we can use existing local index
            # immediately on first request after startup.
            try:
                self._ensure_fts_conn()
            except Exception:
                pass

            # Only refresh FTS index if no FTS rebuild is in progress (avoid starving UI with pool connections)
            if not self._fts_building:
                await self._maybe_refresh_fts_index()

            # First-search bootstrap: wait briefly for an in-flight index build so
            # users don't immediately see empty results right after startup.
            if not self._fts_ready and self._fts_rebuild_task is not None and not self._fts_rebuild_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self._fts_rebuild_task), timeout=2.5)
                except Exception:
                    pass

            if self._fts_ready and self._fts_conn is not None:
                try:
                    fts_start = time.monotonic() * 1000
                    expr = self._fts_query_expr(q_norm)
                    cursor = self._fts_conn.execute(
                        "SELECT file,title,artist,album FROM music_search_idx WHERE music_search_idx MATCH ? LIMIT ?",
                        (expr, safe_limit),
                    )
                    out = [
                        {
                            "file": str(file_uri or ""),
                            "title": str(title or ""),
                            "artist": str(artist or ""),
                            "album": str(album or ""),
                        }
                        for (file_uri, title, artist, album) in cursor.fetchall()
                        if str(file_uri or "").strip()
                    ]
                    if out:
                        fts_elapsed = time.monotonic() * 1000 - fts_start
                        total_elapsed = time.monotonic() * 1000 - start_ms
                        self._ui_search_cache[cache_key] = (time.monotonic(), [dict(row) for row in out])
                        self._ui_prefix_cache[q_norm] = (time.monotonic(), [dict(row) for row in out])
                        logger.info(
                            f"🔍 Music search (fts): '{q}' → {len(out)} results in {total_elapsed:.1f}ms "
                            f"(FTS:{fts_elapsed:.1f}ms)"
                        )
                        self._record_search_metric("fts", q, total_elapsed)
                        return out

                    # FTS may return zero rows for some tokenization edge-cases;
                    # use local SQLite LIKE fallback before direct backend search.
                    like = f"%{q_norm}%"
                    cursor = self._fts_conn.execute(
                        "SELECT file,title,artist,album FROM music_search_idx WHERE searchable LIKE ? LIMIT ?",
                        (like, safe_limit),
                    )
                    out_like = [
                        {
                            "file": str(file_uri or ""),
                            "title": str(title or ""),
                            "artist": str(artist or ""),
                            "album": str(album or ""),
                        }
                        for (file_uri, title, artist, album) in cursor.fetchall()
                        if str(file_uri or "").strip()
                    ]
                    if out_like:
                        fts_elapsed = time.monotonic() * 1000 - fts_start
                        total_elapsed = time.monotonic() * 1000 - start_ms
                        self._ui_search_cache[cache_key] = (time.monotonic(), [dict(row) for row in out_like])
                        self._ui_prefix_cache[q_norm] = (time.monotonic(), [dict(row) for row in out_like])
                        logger.info(
                            f"🔍 Music search (sqlite-like): '{q}' → {len(out_like)} results in {total_elapsed:.1f}ms "
                            f"(sqlite:{fts_elapsed:.1f}ms)"
                        )
                        self._record_search_metric("sqlite_like", q, total_elapsed)
                        return out_like
                except Exception as exc:
                    logger.warning("FTS query failed for '%s': %s", q, exc)

            # Bounded backend fallback: used only when local index returns no results.
            if self._fts_building or not self._fts_ready:
                raise RuntimeError(self._fts_progress_text())

            total_elapsed = time.monotonic() * 1000 - start_ms
            logger.info(
                f"🔍 Music search (miss): '{q}' → 0 results in {total_elapsed:.1f}ms "
                f"(fts_ready={self._fts_ready}, fts_building={self._fts_building})"
            )
            self._record_search_metric("local_miss", q, total_elapsed)
            return []
        except Exception as e:
            logger.error(f"Failed UI library search: {e}")
            return []
    
    # ========== Playlist Management ==========
    
    async def list_playlists(self) -> List[str]:
        """List user-visible playlists, excluding internal runtime snapshots."""
        playlists = await self._list_all_playlists()
        return [playlist for playlist in playlists if not _is_internal_playlist_name(playlist)]

    async def resolve_playlist_name(self, requested_name: str, refresh_if_miss: bool = True) -> str:
        """Resolve a playlist name using fuzzy matching with intelligent fallback."""
        requested = str(requested_name or "").strip()
        if not requested:
            return ""

        now = time.monotonic()
        cached = self._playlist_names_cache if (now - self._playlist_names_cache_ts) <= self._playlist_names_cache_ttl_s else []
        
        # Try fuzzy match against cached playlists
        if cached:
            matched = _fuzzy_match_playlists(requested, cached)
            if matched:
                return matched

        if not refresh_if_miss:
            return ""

        available_playlists = await self.list_playlists()
        # Try fuzzy match against all playlists
        matched = _fuzzy_match_playlists(requested, available_playlists)
        return matched if matched else ""
    
    async def create_playlist(self, name: str) -> str:
        """Create an empty playlist with the given name.
        
        Returns an error if a playlist with this name already exists.
        Sets the created playlist as the loaded/active playlist for subsequent operations.
        """
        try:
            playlist_name = str(name or "").strip()
            if not playlist_name:
                return "Playlist name is required"
            
            # Check if playlist already exists
            existing = await self.resolve_playlist_name(playlist_name, refresh_if_miss=True)
            if existing:
                return f"Error: Playlist '{playlist_name}' already exists"
            
            # Create the empty playlist
            await self._control_execute(f'playlistcreate "{self._quote(playlist_name)}"')
            
            # Update cache
            self._playlist_names_cache = [p for p in self._playlist_names_cache if p.lower() != playlist_name.lower()]
            self._playlist_names_cache.append(playlist_name)
            self._playlist_names_cache_ts = time.monotonic()
            
            # Set as loaded playlist so subsequent adds go to this playlist
            self._loaded_playlist_name = playlist_name
            
            logger.info(f"📂 Created empty playlist '{playlist_name}'")
            return f"Created playlist: {playlist_name}"
        except Exception as e:
            logger.error(f"Failed to create playlist: {e}")
            return f"Error: {e}"
    
    async def load_playlist(self, name: str) -> str:
        """Load a saved playlist (case-insensitive matching)."""
        # Signal that a playlist load is in progress
        self._loading_playlist_event.clear()
        emit_latency_trace("music_load.manager_enter", playlist=name)
        
        start_ms = time.monotonic() * 1000
        try:
            playlist_name = str(name or "").strip()
            if not playlist_name:
                emit_latency_trace("music_load.manager_return", playlist=name, ok=False, reason="empty_playlist_name")
                self._loading_playlist_event.set()  # Signal load complete (even though it failed)
                return "Playlist name is required"

            # Fast path: resolve from cache first to avoid list calls on every load.
            cached = self._playlist_names_cache if (time.monotonic() - self._playlist_names_cache_ts) <= self._playlist_names_cache_ttl_s else []
            actual_playlist_name = await self.resolve_playlist_name(playlist_name, refresh_if_miss=True)
            emit_latency_trace(
                "music_load.cache_lookup",
                playlist=playlist_name,
                cache_size=len(cached),
                hit=bool(actual_playlist_name and any(str(p).lower() == playlist_name.lower() for p in cached)),
            )

            if not actual_playlist_name:
                self._loading_playlist_event.set()  # Signal load complete
                logger.warning(
                    f"Playlist '{playlist_name}' not found. Available: {await self.list_playlists()}"
                )
                return f"Error: Playlist '{playlist_name}' not found"

            clear_start = time.monotonic() * 1000
            emit_latency_trace("music_load.clear_start", playlist=actual_playlist_name)
            await self._control_execute("clear", timeout=8.0)
            clear_ms = time.monotonic() * 1000 - clear_start
            emit_latency_trace("music_load.clear_done", playlist=actual_playlist_name, elapsed_ms=clear_ms)
            
            load_start = time.monotonic() * 1000
            load_ok = False
            last_exc: Exception | None = None
            for attempt in (1, 2):
                try:
                    emit_latency_trace("music_load.load_start", playlist=actual_playlist_name, attempt=attempt)
                    await self._control_execute(f'load "{self._quote(actual_playlist_name)}"', timeout=25.0)
                    emit_latency_trace("music_load.load_done", playlist=actual_playlist_name, attempt=attempt, elapsed_ms=(time.monotonic() * 1000 - load_start))
                    load_ok = True
                    break
                except Exception as exc:
                    last_exc = exc
                    emit_latency_trace("music_load.load_error", playlist=actual_playlist_name, attempt=attempt, error=str(exc))
                    if attempt == 1:
                        logger.warning("load_playlist first attempt failed for '%s', retrying: %s", actual_playlist_name, exc)
                        await asyncio.sleep(0.05)
            if not load_ok:
                if last_exc is not None:
                    self._loading_playlist_event.set()  # Signal load complete
                    raise last_exc
                self._loading_playlist_event.set()  # Signal load complete
                raise RuntimeError("Playlist load failed")
            load_ms = time.monotonic() * 1000 - load_start
            
            self._loaded_playlist_name = actual_playlist_name
            total_ms = time.monotonic() * 1000 - start_ms
            
            # Log the case mapping if different from original
            case_info = ""
            if actual_playlist_name != playlist_name:
                case_info = f" (normalized from '{playlist_name}')"
            
            logger.info(
                f"📂 Load playlist '{actual_playlist_name}'{case_info}: {total_ms:.1f}ms total "
                f"(clear:{clear_ms:.1f}ms, load:{load_ms:.1f}ms)"
            )
            emit_latency_trace(
                "music_load.manager_return",
                playlist=actual_playlist_name,
                ok=True,
                elapsed_ms=total_ms,
                clear_ms=clear_ms,
                load_ms=load_ms,
            )
            return f"Loaded playlist: {actual_playlist_name}"
        except Exception as e:
            elapsed = time.monotonic() * 1000 - start_ms
            logger.error(f"Failed to load playlist '{name}' after {elapsed:.1f}ms: {e}")
            emit_latency_trace("music_load.manager_return", playlist=name, ok=False, elapsed_ms=elapsed, error=str(e))
            return f"Error: {e}"
        finally:
            # Always signal that the load operation is complete
            self._loading_playlist_event.set()
    
    async def save_playlist(self, name: str) -> str:
        """Save current queue as a playlist."""
        try:
            playlist_name = str(name or "").strip()
            if not playlist_name:
                return "Playlist name is required"
            try:
                await self._control_execute(f'rm "{self._quote(playlist_name)}"')
            except Exception:
                pass
            await self._control_execute(f'save "{self._quote(playlist_name)}"')
            self._loaded_playlist_name = playlist_name
            self._playlist_names_cache = [p for p in self._playlist_names_cache if p.lower() != playlist_name.lower()]
            self._playlist_names_cache.append(playlist_name)
            self._playlist_names_cache_ts = time.monotonic()
            return f"Saved playlist: {playlist_name}"
        except Exception as e:
            logger.error(f"Failed to save playlist: {e}")
            return f"Error: {e}"

    async def rename_playlist(self, old_name: str, new_name: str) -> str:
        """Rename a saved playlist (case-insensitive source lookup)."""
        try:
            src_name = str(old_name or "").strip()
            dst_name = str(new_name or "").strip()
            if not src_name:
                return "Source playlist name is required"
            if not dst_name:
                return "Destination playlist name is required"

            actual_src = await self.resolve_playlist_name(src_name, refresh_if_miss=True)
            if not actual_src:
                return f"Error: Playlist '{src_name}' not found"

            actual_dst = await self.resolve_playlist_name(dst_name, refresh_if_miss=True)
            if actual_dst and actual_dst.lower() != actual_src.lower():
                return f"Error: Playlist '{dst_name}' already exists"

            await self._control_execute(
                f'rename "{self._quote(actual_src)}" "{self._quote(dst_name)}"'
            )

            self._playlist_names_cache = [
                p for p in self._playlist_names_cache if p.lower() != actual_src.lower()
            ]
            if not any(p.lower() == dst_name.lower() for p in self._playlist_names_cache):
                self._playlist_names_cache.append(dst_name)
            self._playlist_names_cache_ts = time.monotonic()

            if str(self._loaded_playlist_name or "").lower() == actual_src.lower():
                self._loaded_playlist_name = dst_name

            logger.info("📂 Renamed playlist '%s' -> '%s'", actual_src, dst_name)
            return f"Renamed playlist: {actual_src} -> {dst_name}"
        except Exception as e:
            logger.error(f"Failed to rename playlist '{old_name}' -> '{new_name}': {e}")
            return f"Error: {e}"
    
    async def delete_playlist(self, name: str, ignore_missing: bool = False) -> str:
        """Delete a saved playlist (case-insensitive matching)."""
        try:
            playlist_name = str(name or "").strip()
            if not playlist_name:
                return "Playlist name is required"
            
            # Find the actual playlist name (case-insensitive matching)
            # This ensures deletion works with "delete fred" even if playlist is "Fred.m3u"
            available_playlists = await self._list_all_playlists()
            actual_playlist_name = None
            
            for available in available_playlists:
                if available.lower() == playlist_name.lower():
                    actual_playlist_name = available
                    break
            
            if not actual_playlist_name:
                if ignore_missing:
                    logger.debug(
                        "Playlist '%s' missing during best-effort delete; available=%s",
                        playlist_name,
                        available_playlists,
                    )
                    return f"Playlist '{playlist_name}' not found"
                logger.warning(
                    f"Playlist '{playlist_name}' not found for deletion. Available: {available_playlists}"
                )
                return f"Error: Playlist '{playlist_name}' not found"

            deleting_loaded_playlist = (
                str(self._loaded_playlist_name or "").strip().lower()
                == actual_playlist_name.lower()
            )
            
            await self._control_execute(f'rm "{self._quote(actual_playlist_name)}"')
            self._playlist_names_cache = [p for p in self._playlist_names_cache if p.lower() != actual_playlist_name.lower()]
            self._playlist_names_cache_ts = time.monotonic()

            if deleting_loaded_playlist:
                clear_result = await self.clear_queue()
                if str(clear_result).strip().lower().startswith("error:"):
                    logger.warning(
                        "Deleted active playlist '%s', but failed to clear queue: %s",
                        actual_playlist_name,
                        clear_result,
                    )
                    self._detach_loaded_playlist()
                    return f"Deleted playlist: {playlist_name} (warning: {clear_result})"
                logger.info(
                    "Deleted active playlist '%s'; queue cleared and detached",
                    actual_playlist_name,
                )
            
            case_info = ""
            if actual_playlist_name != playlist_name:
                case_info = f" (matched '{actual_playlist_name}')"
            
            logger.info(f"📂 Deleted playlist '{playlist_name}'{case_info}")
            return f"Deleted playlist: {playlist_name}"
        except Exception as e:
            logger.error(f"Failed to delete playlist '{name}': {e}")
            return f"Error: {e}"
    
    # ========== High-Level Operations ==========
    
    async def play_artist(self, artist: str, shuffle: bool = True) -> str:
        """
        Play all tracks by an artist.
        
        Args:
            artist: Artist name
            shuffle: Whether to shuffle the tracks
        
        Returns:
            Success or error message
        """
        start_ms = time.monotonic() * 1000
        try:
            search_start = time.monotonic() * 1000
            tracks = await self.search_artist(artist)
            search_ms = time.monotonic() * 1000 - search_start
            
            if not tracks:
                logger.info(f"🎤 Play artist '{artist}': no matches found (search:{search_ms:.1f}ms)")
                return f"No tracks found for artist: {artist}"
            
            # Clear queue and add all tracks
            queue_start = time.monotonic() * 1000
            await self.clear_queue()
            for track in tracks:
                if "file" in track:
                    await self.add_to_queue(track["file"])
            queue_ms = time.monotonic() * 1000 - queue_start
            
            await self.play(0)
            if shuffle:
                await self.pool.execute("random 1")
            total_ms = time.monotonic() * 1000 - start_ms
            logger.info(
                f"🎤 Play artist '{artist}': {len(tracks)} tracks in {total_ms:.1f}ms "
                f"(search:{search_ms:.1f}ms, queue:{queue_ms:.1f}ms)"
            )
            return f"Playing {len(tracks)} tracks by {artist}"
        except Exception as e:
            elapsed = time.monotonic() * 1000 - start_ms
            logger.error(f"Failed to play artist '{artist}' after {elapsed:.1f}ms: {e}")
            return f"Error: {e}"
    
    async def play_album(self, album: str) -> str:
        """Play all tracks from an album."""
        start_ms = time.monotonic() * 1000
        try:
            search_start = time.monotonic() * 1000
            tracks = await self.search_album(album)
            search_ms = time.monotonic() * 1000 - search_start
            
            if not tracks:
                logger.info(f"💿 Play album '{album}': no matches found (search:{search_ms:.1f}ms)")
                return f"No tracks found for album: {album}"
            
            # Clear queue and add all tracks
            queue_start = time.monotonic() * 1000
            await self.clear_queue()
            for track in tracks:
                if "file" in track:
                    await self.add_to_queue(track["file"])
            queue_ms = time.monotonic() * 1000 - queue_start
            
            await self.play(0)
            total_ms = time.monotonic() * 1000 - start_ms
            logger.info(
                f"💿 Play album '{album}': {len(tracks)} tracks in {total_ms:.1f}ms "
                f"(search:{search_ms:.1f}ms, queue:{queue_ms:.1f}ms)"
            )
            return f"Playing album: {album} ({len(tracks)} tracks)"
        except Exception as e:
            elapsed = time.monotonic() * 1000 - start_ms
            logger.error(f"Failed to play album '{album}' after {elapsed:.1f}ms: {e}")
            return f"Error: {e}"
    
    async def play_genre(self, genre: str, shuffle: bool = True) -> str:
        """Play tracks from a genre."""
        start_ms = time.monotonic() * 1000
        try:
            clear_start = time.monotonic() * 1000
            await self.clear_queue()
            clear_ms = time.monotonic() * 1000 - clear_start

            safe_genre = genre.replace('"', '\\"')
            search_start = time.monotonic() * 1000
            limit = max(1, int(self.genre_queue_limit))
            try:
                genre_tracks = await self.pool.execute_list(f'search genre "{safe_genre}" window 0:999999')
            except Exception as window_exc:
                logger.debug(
                    "Genre full-window query failed, falling back to full search for '%s': %s",
                    genre,
                    window_exc,
                )
                genre_tracks = await self.pool.execute_list(f'search genre "{safe_genre}"')

            search_ms = time.monotonic() * 1000 - search_start

            total_matches = len(genre_tracks)

            if total_matches == 0:
                stats = await self.get_stats()
                song_count = int(stats.get("songs", 0))
                elapsed = time.monotonic() * 1000 - start_ms
                if song_count == 0:
                    logger.info(f"🎵 Play genre '{genre}': no library (search:{search_ms:.1f}ms, total:{elapsed:.1f}ms)")
                    return "No music in library. Say 'update library' to scan your music folder."
                logger.info(f"🎵 Play genre '{genre}': no matches found (search:{search_ms:.1f}ms, total:{elapsed:.1f}ms)")
                return f"No tracks found for genre: {genre}"

            if shuffle:
                import random

                selected_tracks = list(genre_tracks)
                if len(selected_tracks) > limit:
                    selected_tracks = random.sample(selected_tracks, limit)
                selected_tracks = self._spread_tracks_across_artists(selected_tracks)
            else:
                selected_tracks = genre_tracks[:limit]

            queue_len = len(selected_tracks)

            if total_matches > limit:
                logger.info(
                    "Genre '%s' sampled %d tracks from %d matches",
                    genre,
                    queue_len,
                    total_matches,
                )
            
            add_start = time.monotonic() * 1000
            queue_files = [track.get("file", "") for track in selected_tracks if track.get("file", "")]
            add_result = await self.add_many_to_queue(queue_files, batch_size=40)
            if str(add_result).lower().startswith("error"):
                return add_result
            add_ms = time.monotonic() * 1000 - add_start
            
            play_start = time.monotonic() * 1000
            await self.play(0)
            if shuffle:
                await self.pool.execute("random 1")
            play_ms = time.monotonic() * 1000 - play_start

            status_after_play = await self.get_status()
            state_after_play = status_after_play.get("state", "unknown") if status_after_play else "unknown"
            volume_after_play_raw = status_after_play.get("volume") if status_after_play else None
            try:
                volume_after_play = int(volume_after_play_raw) if volume_after_play_raw is not None else None
            except (TypeError, ValueError):
                volume_after_play = None
            
            total_ms = time.monotonic() * 1000 - start_ms
            logger.info(
                f"🎵 Play genre '{genre}': {queue_len} tracks in {total_ms:.1f}ms (clear:{clear_ms:.1f}ms, search:{search_ms:.1f}ms, add:{add_ms:.1f}ms, play:{play_ms:.1f}ms) | "
                f"state={state_after_play} volume={volume_after_play if volume_after_play is not None else 'unknown'}"
            )

            if volume_after_play is not None and volume_after_play <= 0:
                await self.set_volume(100)
                logger.warning(
                    "Music volume was 0 during genre playback; auto-raised to 100%% to avoid silent playback"
                )
                return f"Playing {queue_len} {genre} tracks (volume was muted, set to 100%)"
            
            enabled_outputs = await self.get_enabled_output_names()
            if not enabled_outputs:
                return f"Playing {queue_len} {genre} tracks, but no audio outputs are enabled"

            return f"Playing {queue_len} {genre} tracks"
        except Exception as e:
            elapsed = time.monotonic() * 1000 - start_ms
            logger.error(f"Failed to play genre '{genre}' after {elapsed:.1f}ms: {e}")
            return f"Error: {e}"
    
    async def play_song(self, title: str) -> str:
        """Play a specific song by title."""
        try:
            tracks = await self.search_title(title)
            if not tracks:
                return f"Song not found: {title}"
            
            # Play first match
            track = tracks[0]
            if "file" in track:
                await self.clear_queue()
                await self.add_to_queue(track["file"])
                await self.play(0)
                
                artist = track.get("Artist", "Unknown")
                title = track.get("Title", title)
                return f"Playing: {title} by {artist}"
            else:
                return f"Error: Track has no file path"
        except Exception as e:
            logger.error(f"Failed to play song: {e}")
            return f"Error: {e}"
    
    # ========== Library Management ==========
    
    async def update_library(self) -> str:
        """Scan music directory and update database."""
        try:
            await self.pool.execute("update")
            return "Scanning music library. This may take a few moments..."
        except Exception as e:
            logger.error(f"Failed to update library: {e}")
            return f"Error: {e}"
    
    # ========== Additional Helper Methods ==========
    
    async def is_playing(self) -> bool:
        """Check if music is currently playing."""
        try:
            status = await self.pool.execute("status")
            return status.get("state", "stop") == "play"
        except Exception as e:
            logger.error(f"Failed to check playing status: {e}")
            return False
    
    async def is_paused(self) -> bool:
        """Check if music is currently paused."""
        try:
            status = await self.pool.execute("status")
            return status.get("state", "stop") == "pause"
        except Exception as e:
            logger.error(f"Failed to check pause status: {e}")
            return False
    
    async def get_playback_state(self) -> str:
        """Get current playback state: 'play', 'pause', or 'stop'."""
        try:
            status = await self.pool.execute("status")
            return status.get("state", "stop")
        except Exception as e:
            logger.error(f"Failed to get playback state: {e}")
            return "stop"
    
    async def toggle_playback(self) -> str:
        """Toggle between play and pause."""
        try:
            state = await self.get_playback_state()
            if state == "play":
                return await self.pause()
            elif state == "pause":
                return await self.pause()  # This will resume
            else:
                return await self.play()
        except Exception as e:
            logger.error(f"Failed to toggle playback: {e}")
            return f"Error: {e}"
    
    async def get_queue_length(self) -> int:
        """Get number of items in queue."""
        try:
            status = await self.pool.execute("status")
            return int(status.get("playlistlength", 0))
        except Exception as e:
            logger.error(f"Failed to get queue length: {e}")
            return 0
    
    async def add_random_tracks(self, count: int = 50) -> str:
        """
        Add random tracks to the queue.
        
        Args:
            count: Number of random tracks to add
        
        Returns:
            Success message
        """
        try:
            # Get all tracks in library
            all_tracks = await self.pool.execute_list("listall")
            
            # Filter to only files (not directories)
            files = [item.get("file") for item in all_tracks if "file" in item]
            
            if not files:
                return "No music files found in library"
            
            # Randomly select tracks
            import random
            selected = random.sample(files, min(count, len(files)))
            
            # Add to queue
            for file in selected:
                await self.add_to_queue(file)
            
            logger.info(f"Added {len(selected)} random tracks to queue")
            return f"Added {len(selected)} random tracks"
        except Exception as e:
            logger.error(f"Failed to add random tracks: {e}")
            return f"Error: {e}"
    
    async def smart_play(self, random_count: int = 50) -> str:
        """
        Smart play: If queue is empty, add random tracks and play. Otherwise toggle play/pause.
        
        Args:
            random_count: Number of random tracks to add if queue is empty
        
        Returns:
            Success message
        """
        try:
            queue_length = await self.get_queue_length()
            state = await self.get_playback_state()
            
            if queue_length == 0:
                # Queue is empty - add random tracks
                logger.info("Queue empty - adding random tracks")
                await self.add_random_tracks(random_count)
                await self.play(0)
                await self.pool.execute("random 1")  # Enable shuffle after starting first queued track
                return f"Playing {random_count} random tracks"
            elif state == "play":
                # Already playing - pause
                await self.pause()
                return "Paused"
            else:
                # Not playing - resume/start
                await self.play()
                return "Playing"
        except Exception as e:
            logger.error(f"Failed smart play: {e}")
            return f"Error: {e}"
    
    async def increase_volume(self, amount: int = 5) -> str:
        """Increase volume by specified amount."""
        return await self.volume_up(amount)
    
    async def decrease_volume(self, amount: int = 5) -> str:
        """Decrease volume by specified amount."""
        return await self.volume_down(amount)

    # ========== Web UI Helpers ==========

    async def get_ui_music_state(self) -> dict:
        """Return compact music state dict for web UI snapshot/delta events."""
        try:
            status = await self.get_status()
            track = await self.get_current_track()
            try:
                position = int(status.get("song", -1) or -1)
            except (TypeError, ValueError):
                position = -1
            track_pos_raw = track.get("pos", track.get("Pos", -1)) if track else -1
            try:
                track_pos = int(track_pos_raw if track_pos_raw is not None else -1)
            except (TypeError, ValueError):
                track_pos = -1
            if position >= 0 and (not track or track_pos != position):
                current_items = await self.pool.execute_list(f"playlistinfo {position}")
                if current_items:
                    track = current_items[0]
            vol_raw = status.get("volume")
            return {
                "state": status.get("state", "stop"),
                "elapsed": float(status.get("elapsed", 0) or 0),
                "duration": float(status.get("duration", status.get("time", "0").split(":")[0] if "time" in status else 0) or 0),
                "queue_length": int(status.get("playlistlength", 0) or 0),
                "playlist_version": str(status.get("playlist", "") or ""),
                "position": position,
                "songid": str(status.get("songid", "") or ""),
                "warning": str(status.get("warning", "") or ""),
                "volume": int(vol_raw) if vol_raw is not None else None,
                "title": track.get("title") or track.get("Title", ""),
                "artist": track.get("artist") or track.get("Artist", ""),
                "album": track.get("album") or track.get("Album", ""),
                "file": track.get("file", ""),
                "loaded_playlist": self._loaded_playlist_name,
                "random": status.get("random", "0") == "1",
                "repeat": status.get("repeat", "0") == "1",
            }
        except Exception as e:
            logger.error(f"Failed to get UI music state: {e}")
            return {"state": "error", "queue_length": 0}

    async def get_ui_playlist(self, limit: int = 200, timeout: float = 6.0) -> list:
        """Return a compact queue list for the web UI music page."""
        t0 = time.monotonic()
        query_timeout = max(0.5, float(timeout))

        def _to_ui_rows(queue_items: list[dict], out_limit: int) -> list[dict]:
            rows: list[dict] = []
            for i, item in enumerate((queue_items or [])[:out_limit]):
                raw_dur = item.get("duration") or item.get("time") or item.get("Time") or 0
                try:
                    dur = float(raw_dur)
                except (TypeError, ValueError):
                    dur = 0.0
                rows.append(
                    {
                        "pos": int(item.get("pos", item.get("Pos", i)) or i),
                        "id": item.get("id", item.get("Id", "")),
                        "title": item.get("title") or item.get("Title") or item.get("file", "").split("/")[-1],
                        "artist": item.get("artist") or item.get("Artist", ""),
                        "album": item.get("album") or item.get("Album", ""),
                        "file": item.get("file", ""),
                        "duration": dur,
                    }
                )
            return rows

        try:
            # Fetch only as many items as we'll display to avoid connection timeouts on huge queues.
            queue = await self.get_queue(limit=limit, timeout=query_timeout)
            t_queue = time.monotonic()
            result = _to_ui_rows(queue, limit)
            t_loop = time.monotonic()
            elapsed_ms = (t_loop - t0) * 1000
            queue_ms = (t_queue - t0) * 1000
            loop_ms = (t_loop - t_queue) * 1000
            if len(result) > 50:
                logger.info(f"⏱️ get_ui_playlist({limit}): {elapsed_ms:.1f}ms total (queue:{queue_ms:.1f}ms, loop:{loop_ms:.1f}ms)")
            return result
        except Exception as e:
            preview_limit = max(1, min(40, int(limit)))
            preview_timeout = min(query_timeout, 2.0)
            logger.warning(
                "get_ui_playlist full fetch failed (limit=%d timeout=%.1fs): %s; retrying preview limit=%d timeout=%.1fs",
                limit,
                query_timeout,
                e,
                preview_limit,
                preview_timeout,
            )
            try:
                preview = await self.get_queue(limit=preview_limit, timeout=preview_timeout)
                result = _to_ui_rows(preview, preview_limit)
                logger.info(
                    "⏱️ get_ui_playlist preview fallback returned %d rows (limit=%d timeout=%.1fs)",
                    len(result),
                    preview_limit,
                    preview_timeout,
                )
                return result
            except Exception as preview_exc:
                logger.error(f"Failed to get UI playlist: {preview_exc}")
                return []
