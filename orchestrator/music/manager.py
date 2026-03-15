"""
Music Manager - High-level operations for MPD control.

Provides user-friendly methods that map to MPD commands:
- Playback control (play, pause, stop, skip)
- Volume control
- Search and browse
- Playlist management
- Library management
"""

import asyncio
import logging
import shutil
import time
from typing import Dict, List, Optional
from .mpd_client import MPDClientPool

logger = logging.getLogger(__name__)


class MusicManager:
    """High-level music control interface wrapping MPD client pool."""
    
    def __init__(
        self,
        pool: MPDClientPool,
        genre_queue_limit: int = 120,
        pipewire_stream_normalize_enabled: bool = True,
        pipewire_stream_target_percent: int = 100,
    ):
        self.pool = pool
        self.genre_queue_limit = max(1, int(genre_queue_limit))
        self.pipewire_stream_normalize_enabled = bool(pipewire_stream_normalize_enabled)
        self.pipewire_stream_target_percent = max(1, min(150, int(pipewire_stream_target_percent)))
        self._last_pipewire_normalize_ts = 0.0

    async def _normalize_pipewire_mpd_stream_volume(self) -> None:
        """Best-effort: set PipeWire per-app stream volume for MPD to target percent.

        MPD's internal volume (setvol/status volume) can diverge from PipeWire's
        sink-input volume for the MPD stream, causing silent playback despite MPD
        reporting normal playback. This method normalizes the MPD sink-input level.
        """
        if not self.pipewire_stream_normalize_enabled:
            return

        # Avoid hammering pactl when play() is called repeatedly.
        now = time.monotonic()
        if (now - self._last_pipewire_normalize_ts) < 2.0:
            return
        self._last_pipewire_normalize_ts = now

        if shutil.which("pactl") is None:
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl",
                "list",
                "sink-inputs",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.debug("Skipping PipeWire MPD stream normalize: pactl list failed: %s", stderr.decode("utf-8", errors="ignore").strip())
                return

            text = stdout.decode("utf-8", errors="ignore")
            blocks = text.split("Sink Input #")
            mpd_ids: List[str] = []

            for block in blocks[1:]:
                lines = block.splitlines()
                if not lines:
                    continue
                sink_input_id = lines[0].strip()
                if not sink_input_id:
                    continue
                if 'application.name = "Music Player Daemon"' in block:
                    mpd_ids.append(sink_input_id)

            if not mpd_ids:
                return

            target = f"{self.pipewire_stream_target_percent}%"
            for sink_input_id in mpd_ids:
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
                        "Failed to normalize MPD PipeWire stream volume (sink-input=%s): %s",
                        sink_input_id,
                        set_err.decode("utf-8", errors="ignore").strip(),
                    )
                    continue

                logger.info(
                    "🎚️ Normalized MPD PipeWire stream volume: sink-input=%s target=%s",
                    sink_input_id,
                    target,
                )
        except Exception as exc:
            logger.debug("PipeWire MPD stream normalize skipped: %s", exc)
    
    # ========== Playback Control ==========
    
    async def play(self, position: Optional[int] = None) -> str:
        """
        Start or resume playback.
        
        Args:
            position: Optional queue position to start from (0-indexed)
        
        Returns:
            Success message
        """
        try:
            if position is not None:
                await self.pool.execute(f"play {position}")
                await self._normalize_pipewire_mpd_stream_volume()
                return f"Playing track {position + 1}"
            else:
                await self.pool.execute("play")
                await self._normalize_pipewire_mpd_stream_volume()
                return "Playback started"
        except Exception as e:
            logger.error(f"Failed to play: {e}")
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
            await self.pool.execute("stop")
            # Verify stop actually took effect; if MPD state still reports play,
            # force a pause and issue stop again as a fallback.
            status = await self.pool.execute("status")
            state = status.get("state", "stop") if status else "stop"
            if state == "play":
                await self.pool.execute("pause 1")
                await self.pool.execute("stop")
                status = await self.pool.execute("status")
                state = status.get("state", "stop") if status else "stop"
                if state == "play":
                    logger.warning("MPD stop fallback executed but state is still 'play'")
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
        """Return configured MPD audio outputs."""
        try:
            return await self.pool.execute_list("outputs")
        except Exception as e:
            logger.error(f"Failed to get outputs: {e}")
            return []

    async def get_enabled_output_names(self) -> List[str]:
        """Return enabled output names from MPD outputs list."""
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
    
    async def clear_queue(self) -> str:
        """Clear the playback queue."""
        try:
            await self.pool.execute("clear")
            return "Queue cleared"
        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")
            return f"Error: {e}"
    
    async def add_to_queue(self, uri: str) -> str:
        """
        Add a track or directory to the queue.
        
        Args:
            uri: MPD URI (e.g., "Artist/Album/track.mp3")
        
        Returns:
            Success message
        """
        try:
            await self.pool.execute(f'add "{uri}"')
            return f"Added to queue"
        except Exception as e:
            logger.error(f"Failed to add to queue: {e}")
            return f"Error: {e}"
    
    async def get_queue(self) -> List[Dict[str, str]]:
        """Get current queue contents."""
        try:
            return await self.pool.execute_list("playlistinfo")
        except Exception as e:
            logger.error(f"Failed to get queue: {e}")
            return []
    
    # ========== Playlist Management ==========
    
    async def list_playlists(self) -> List[str]:
        """List available playlists."""
        try:
            result = await self.pool.execute_list("listplaylists")
            return [item.get("playlist", "") for item in result if "playlist" in item]
        except Exception as e:
            logger.error(f"Failed to list playlists: {e}")
            return []
    
    async def load_playlist(self, name: str) -> str:
        """Load a saved playlist."""
        try:
            await self.pool.execute(f'load "{name}"')
            return f"Loaded playlist: {name}"
        except Exception as e:
            logger.error(f"Failed to load playlist: {e}")
            return f"Error: {e}"
    
    async def save_playlist(self, name: str) -> str:
        """Save current queue as a playlist."""
        try:
            await self.pool.execute(f'save "{name}"')
            return f"Saved playlist: {name}"
        except Exception as e:
            logger.error(f"Failed to save playlist: {e}")
            return f"Error: {e}"
    
    async def delete_playlist(self, name: str) -> str:
        """Delete a saved playlist."""
        try:
            await self.pool.execute(f'rm "{name}"')
            return f"Deleted playlist: {name}"
        except Exception as e:
            logger.error(f"Failed to delete playlist: {e}")
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
        try:
            tracks = await self.search_artist(artist)
            if not tracks:
                return f"No tracks found for artist: {artist}"
            
            # Clear queue and add all tracks
            await self.clear_queue()
            for track in tracks:
                if "file" in track:
                    await self.add_to_queue(track["file"])
            
            if shuffle:
                await self.pool.execute("random 1")
            
            await self.play()
            return f"Playing {len(tracks)} tracks by {artist}"
        except Exception as e:
            logger.error(f"Failed to play artist: {e}")
            return f"Error: {e}"
    
    async def play_album(self, album: str) -> str:
        """Play all tracks from an album."""
        try:
            tracks = await self.search_album(album)
            if not tracks:
                return f"No tracks found for album: {album}"
            
            # Clear queue and add all tracks
            await self.clear_queue()
            for track in tracks:
                if "file" in track:
                    await self.add_to_queue(track["file"])
            
            await self.play()
            return f"Playing album: {album} ({len(tracks)} tracks)"
        except Exception as e:
            logger.error(f"Failed to play album: {e}")
            return f"Error: {e}"
    
    async def play_genre(self, genre: str, shuffle: bool = True) -> str:
        """Play tracks from a genre."""
        try:
            # Use server-side MPD query+enqueue to avoid client-side per-track loops
            # that can stall for very large genres.
            await self.clear_queue()

            safe_genre = genre.replace('"', '\\"')
            await self.pool.execute(f'searchadd genre "{safe_genre}"')

            status_after_add = await self.get_status()
            queue_len = int(status_after_add.get("playlistlength", 0)) if status_after_add else 0

            if queue_len == 0:
                stats = await self.get_stats()
                song_count = int(stats.get("songs", 0))
                if song_count == 0:
                    return "No music in library. Say 'update library' to scan your music folder."
                return f"No tracks found for genre: {genre}"

            # Apply queue cap to keep startup snappy for very broad genres.
            if queue_len > self.genre_queue_limit:
                await self.pool.execute(f'delete {self.genre_queue_limit}:')
                queue_len = self.genre_queue_limit
                logger.info(
                    "Genre '%s' queue capped to %d tracks (originally > %d)",
                    genre,
                    queue_len,
                    self.genre_queue_limit,
                )
            
            if shuffle:
                await self.pool.execute("random 1")
            
            await self.play()

            status_after_play = await self.get_status()
            state_after_play = status_after_play.get("state", "unknown") if status_after_play else "unknown"
            volume_after_play_raw = status_after_play.get("volume") if status_after_play else None
            try:
                volume_after_play = int(volume_after_play_raw) if volume_after_play_raw is not None else None
            except (TypeError, ValueError):
                volume_after_play = None
            enabled_outputs = await self.get_enabled_output_names()

            logger.info(
                "Genre playback diagnostics: genre=%s queue=%d state=%s volume=%s outputs=%s",
                genre,
                queue_len,
                state_after_play,
                volume_after_play if volume_after_play is not None else "unknown",
                ", ".join(enabled_outputs) if enabled_outputs else "none",
            )

            if volume_after_play is not None and volume_after_play <= 0:
                await self.set_volume(35)
                logger.warning(
                    "MPD volume was 0 during genre playback; auto-raised to 35%% to avoid silent playback"
                )
                return f"Playing {queue_len} {genre} tracks (volume was muted, set to 35%)"

            if not enabled_outputs:
                return f"Playing {queue_len} {genre} tracks, but MPD has no enabled audio outputs"

            return f"Playing {queue_len} {genre} tracks"
        except Exception as e:
            logger.error(f"Failed to play genre: {e}")
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
                await self.play()
                
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
                await self.pool.execute("random 1")  # Enable shuffle
                await self.play()
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
            vol_raw = status.get("volume")
            return {
                "state": status.get("state", "stop"),
                "elapsed": float(status.get("elapsed", 0) or 0),
                "duration": float(status.get("duration", status.get("time", "0").split(":")[0] if "time" in status else 0) or 0),
                "queue_length": int(status.get("playlistlength", 0) or 0),
                "position": int(status.get("song", -1) or -1),
                "volume": int(vol_raw) if vol_raw is not None else None,
                "title": track.get("title") or track.get("Title", ""),
                "artist": track.get("artist") or track.get("Artist", ""),
                "album": track.get("album") or track.get("Album", ""),
                "file": track.get("file", ""),
                "random": status.get("random", "0") == "1",
                "repeat": status.get("repeat", "0") == "1",
            }
        except Exception as e:
            logger.error(f"Failed to get UI music state: {e}")
            return {"state": "error", "queue_length": 0}

    async def get_ui_playlist(self, limit: int = 200) -> list:
        """Return a compact queue list for the web UI music page."""
        try:
            queue = await self.get_queue()
            result = []
            for i, item in enumerate(queue[:limit]):
                raw_dur = item.get("duration") or item.get("time") or item.get("Time") or 0
                try:
                    dur = float(raw_dur)
                except (TypeError, ValueError):
                    dur = 0.0
                result.append({
                    "pos": int(item.get("pos", item.get("Pos", i)) or i),
                    "id": item.get("id", item.get("Id", "")),
                    "title": item.get("title") or item.get("Title") or item.get("file", "").split("/")[-1],
                    "artist": item.get("artist") or item.get("Artist", ""),
                    "album": item.get("album") or item.get("Album", ""),
                    "file": item.get("file", ""),
                    "duration": dur,
                })
            return result
        except Exception as e:
            logger.error(f"Failed to get UI playlist: {e}")
            return []
