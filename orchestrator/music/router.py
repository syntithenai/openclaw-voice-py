"""
Music Router - Integration between fast-path parser and music manager.

Routes user requests through fast-path or LLM fallback, executes commands,
and formats responses for voice output.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Callable
from .parser import MusicFastPathParser
from .manager import MusicManager

logger = logging.getLogger(__name__)


class MusicRouter:
    """Route music commands through fast-path or LLM, execute, and format responses."""
    COMMAND_TIMEOUT_S = 12.0
    
    def __init__(self, manager: MusicManager):
        self.manager = manager
        self.parser = MusicFastPathParser()
        
        # Map command names to manager methods
        self.command_handlers: Dict[str, Callable] = {
            # Playback control
            "play": self._handle_play,
            "pause": self._handle_pause,
            "stop": self._handle_stop,
            "next_track": self._handle_next_track,
            "previous_track": self._handle_previous_track,
            "clear_queue": self._handle_clear_queue,
            
            # Volume control
            "set_volume": self._handle_set_volume,
            "volume_up": self._handle_volume_up,
            "volume_down": self._handle_volume_down,
            
            # Status queries
            "get_current_track": self._handle_get_current_track,
            "get_status": self._handle_get_status,
            
            # Search and play
            "play_artist": self._handle_play_artist,
            "play_album": self._handle_play_album,
            "play_genre": self._handle_play_genre,
            "play_song": self._handle_play_song,
            
            # Playlist management
            "load_playlist": self._handle_load_playlist,
            "save_playlist": self._handle_save_playlist,
            "list_playlists": self._handle_list_playlists,
            "add_songs": self._handle_add_songs,
            
            # Library management
            "update_library": self._handle_update_library,
        }

    @staticmethod
    def _is_error(result: str) -> bool:
        return str(result).lower().startswith("error")

    @staticmethod
    def _extract_numeric_volume(result: str) -> Optional[int]:
        import re
        match = re.search(r"(\d{1,3})%", result or "")
        if not match:
            return None
        try:
            return max(0, min(100, int(match.group(1))))
        except Exception:
            return None
    
    async def handle_request(self, text: str, use_fast_path: bool = True) -> Optional[str]:
        """
        Handle a music-related user request.
        
        Args:
            text: User input (transcript)
            use_fast_path: Whether to attempt fast-path parsing
        
        Returns:
            Response text for TTS, or None if not a music command
        """
        # Try fast-path first if enabled
        if use_fast_path:
            result = self.parser.parse(text)
            if result:
                command, params = result
                logger.info(f"Fast-path match: {command} {params}")
                
                # Execute command
                handler = self.command_handlers.get(command)
                if handler:
                    try:
                        response = await asyncio.wait_for(
                            handler(**params),
                            timeout=self.COMMAND_TIMEOUT_S,
                        )
                        return response
                    except asyncio.TimeoutError:
                        logger.error("Timed out executing music command %s", command)
                        return "Music command timed out. Please try again."
                    except Exception as e:
                        logger.error(f"Error executing {command}: {e}")
                        return f"Sorry, I couldn't {command.replace('_', ' ')}"
                else:
                    logger.warning(f"No handler for command: {command}")
                    return None
        
        # No fast-path match - return None to trigger LLM fallback
        return None
    
    def is_music_related(self, text: str) -> bool:
        """Check if text appears to be music-related."""
        return self.parser.is_music_related(text)
    
    # ========== Command Handlers ==========
    
    async def _handle_play(self) -> str:
        """Handle play/resume command (silent on success)."""
        result = await self.manager.play()
        return result if self._is_error(result) else ""
    
    async def _handle_pause(self) -> str:
        """Handle pause command as stop (silent on success)."""
        result = await self.manager.stop()
        return result if self._is_error(result) else ""
    
    async def _handle_stop(self) -> str:
        """Handle stop command (silent on success)."""
        result = await self.manager.stop()
        return result if self._is_error(result) else ""
    
    async def _handle_next_track(self) -> str:
        """Handle next track command."""
        result = await self.manager.next_track()
        return result if self._is_error(result) else "Next."
    
    async def _handle_previous_track(self) -> str:
        """Handle previous track command."""
        result = await self.manager.previous_track()
        return result if self._is_error(result) else "Previous."

    async def _handle_clear_queue(self) -> str:
        """Handle clear queue command."""
        result = await self.manager.clear_queue()
        return result if self._is_error(result) else "Queue cleared."
    
    async def _handle_set_volume(self, level: int) -> str:
        """Handle set volume command."""
        result = await self.manager.set_volume(level)
        if self._is_error(result):
            return result
        vol = self._extract_numeric_volume(result)
        return f"Volume {vol}%" if vol is not None else "Volume updated."
    
    async def _handle_volume_up(self, amount: int = 10) -> str:
        """Handle volume up command."""
        result = await self.manager.volume_up(amount)
        if self._is_error(result):
            return result
        vol = self._extract_numeric_volume(result)
        return f"Volume {vol}%" if vol is not None else "Volume up."
    
    async def _handle_volume_down(self, amount: int = 10) -> str:
        """Handle volume down command."""
        result = await self.manager.volume_down(amount)
        if self._is_error(result):
            return result
        vol = self._extract_numeric_volume(result)
        return f"Volume {vol}%" if vol is not None else "Volume down."
    
    async def _handle_get_current_track(self) -> str:
        """Handle current track query."""
        track = await self.manager.get_current_track()
        
        if not track:
            return "Nothing is playing right now"
        
        title = track.get("Title", "Unknown")
        artist = track.get("Artist", "Unknown artist")
        album = track.get("Album", "")
        
        if album:
            return f"Now playing: {title} by {artist}, from {album}"
        else:
            return f"Now playing: {title} by {artist}"
    
    async def _handle_get_status(self) -> str:
        """Handle status query."""
        status = await self.manager.get_status()
        state = status.get("state", "stopped")
        
        if state == "play":
            track = await self.manager.get_current_track()
            title = track.get("Title", "Unknown")
            artist = track.get("Artist", "Unknown artist")
            return f"Playing: {title} by {artist}"
        elif state == "pause":
            return "Music is paused"
        else:
            return "Music is stopped"
    
    async def _handle_play_artist(self, artist: str, shuffle: bool = True) -> str:
        """Handle play artist command."""
        result = await self.manager.play_artist(artist, shuffle)
        return result if self._is_error(result) else f"Playing artist: {artist}."
    
    async def _handle_play_album(self, album: str) -> str:
        """Handle play album command."""
        result = await self.manager.play_album(album)
        return result if self._is_error(result) else f"Playing album: {album}."
    
    async def _handle_play_genre(self, genre: str, shuffle: bool = True) -> str:
        """Handle play genre command."""
        result = await self.manager.play_genre(genre, shuffle)
        return result if self._is_error(result) else f"Playing genre: {genre}."
    
    async def _handle_play_song(self, title: str) -> str:
        """Handle play song command."""
        result = await self.manager.play_song(title)
        return result if self._is_error(result) else f"Playing: {title}."
    
    async def _handle_play_playlist(self, name: str) -> str:
        """Play a saved playlist (load and start playback)."""
        result = await self.manager.load_playlist(name)
        if self._is_error(result):
            return result
        # Auto-play after loading
        play_result = await self.manager.play(0)
        return play_result if self._is_error(play_result) else f"Now playing: {name}"

    async def _handle_add_songs(self, query: str, count: int = 5) -> str:
        """Add songs to existing queue without clearing it."""
        result = await self.manager.add_songs_to_queue(query, count)
        return result

    async def _handle_load_playlist(self, name: str) -> str:
        """Handle load playlist command."""
        return await self.manager.load_playlist(name)
    
    async def _handle_save_playlist(self, name: str) -> str:
        """Handle save playlist command."""
        return await self.manager.save_playlist(name)
    
    async def _handle_list_playlists(self) -> str:
        """Handle list playlists command."""
        playlists = await self.manager.list_playlists()
        
        if not playlists:
            return "No saved playlists"
        
        if len(playlists) == 1:
            return f"You have 1 playlist: {playlists[0]}"
        elif len(playlists) <= 5:
            names = ", ".join(playlists)
            return f"You have {len(playlists)} playlists: {names}"
        else:
            names = ", ".join(playlists[:5])
            return f"You have {len(playlists)} playlists including: {names}"
    
    async def _handle_update_library(self) -> str:
        """Handle library update command."""
        return await self.manager.update_library()
    
    # ========== LLM Tool Call Handler ==========
    
    async def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Handle a tool call from the LLM.
        
        Args:
            tool_name: Name of the music tool to execute
            arguments: Dictionary of arguments for the tool
        
        Returns:
            Result string from executing the tool
        """
        logger.info(f"LLM tool call: {tool_name}({arguments})")

        # Map tool names to handlers
        tool_map = {
            "music_play": lambda: self._handle_play(),
            "music_pause": lambda: self._handle_pause(),
            "music_stop": lambda: self._handle_stop(),
            "music_next": lambda: self._handle_next_track(),
            "music_previous": lambda: self._handle_previous_track(),
            "music_clear_queue": lambda: self._handle_clear_queue(),
            "music_set_volume": lambda: self._handle_set_volume(arguments.get("level", 50)),
            "music_get_current": lambda: self._handle_get_current_track(),
            "music_get_status": lambda: self._handle_get_status(),
            "music_play_artist": lambda: self._handle_play_artist(
                arguments.get("artist", ""),
                arguments.get("shuffle", True)
            ),
            "music_play_album": lambda: self._handle_play_album(arguments.get("album", "")),
            "music_play_genre": lambda: self._handle_play_genre(
                arguments.get("genre", ""),
                arguments.get("shuffle", True)
            ),
            "music_play_song": lambda: self._handle_play_song(arguments.get("title", "")),
            "music_play_playlist": lambda: self._handle_play_playlist(arguments.get("name", "")),
            "music_search": lambda: self._handle_search(arguments.get("query", "")),
            "music_load_playlist": lambda: self.manager.load_playlist(arguments.get("name", "")),
            "music_add_songs": lambda: self._handle_add_songs(
                arguments.get("query", ""),
                int(arguments.get("count", 5)),
            ),
            "music_update_library": lambda: self.manager.update_library(),
        }
        
        handler = tool_map.get(tool_name)
        if not handler:
            return f"Unknown music tool: {tool_name}"
        
        try:
            result = await asyncio.wait_for(handler(), timeout=self.COMMAND_TIMEOUT_S)
            return result
        except asyncio.TimeoutError:
            logger.error("Timed out executing tool %s", tool_name)
            return "Music command timed out. Please try again."
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            return f"Error: {e}"
    
    async def _handle_search(self, query: str) -> str:
        """Handle general search query."""
        tracks = await self.manager.search_any(query)
        
        if not tracks:
            return f"No results found for: {query}"
        
        if len(tracks) == 1:
            return f"Found 1 match for: {query}."
        return f"Found {len(tracks)} matches for: {query}."
