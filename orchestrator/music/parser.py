"""
Fast-path parser for music commands.

Provides sub-200ms pattern matching for common music control commands.
Falls back to LLM for complex queries and disambiguation.
"""

import re
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


class MusicFastPathParser:
    """Fast regex-based pattern matching for music commands."""
    
    # Playback control patterns
    PLAY_PATTERNS = [
        r"^(?:play|resume|continue|unpause)(?:\s+(?:music|song|track|it))?$",
        r"^(?:start|begin)\s+(?:playing|music|the music)$",
    ]
    
    PAUSE_PATTERNS = [
        r"^(?:pause|hold)\s+(?:music|song|track|it|playback)?$",
        r"^(?:pause|hold)$",
    ]
    
    STOP_PATTERNS = [
        r"^(?:stop)$",
        r"^(?:stop)\s+(?:music|song|track|playing|playback|the music)$",
    ]
    
    NEXT_PATTERNS = [
        r"^(?:next|skip)(?:\s+(?:song|track))?$",
        r"^(?:skip|next)\s+(?:song|track|this)$",
    ]
    
    PREVIOUS_PATTERNS = [
        r"^(?:previous|back)(?:\s+(?:song|track))?$",
        r"^(?:go\s+)?back(?:\s+(?:a|one)\s+(?:song|track))?$",
        r"^(?:play\s+)?(?:the\s+)?(?:last|previous)\s+(?:song|track)$",
    ]
    
    # Volume control patterns
    VOLUME_SET_PATTERN = r"^(?:set\s+)?(?:volume\s+)?(?:to\s+)?(\d+)(?:\s*%)?$"
    VOLUME_UP_PATTERNS = [
        r"^(?:volume\s+)?(?:turn\s+)?up(?:\s+(?:volume|the volume))?$",
        r"^(?:increase|raise)\s+(?:volume|the volume)$",
        r"^(?:louder|make it louder)$",
    ]
    VOLUME_DOWN_PATTERNS = [
        r"^(?:volume\s+)?(?:turn\s+)?down(?:\s+(?:volume|the volume))?$",
        r"^(?:decrease|lower)\s+(?:volume|the volume)$",
        r"^(?:quieter|make it quieter|softer)$",
    ]
    
    # Status query patterns
    STATUS_PATTERNS = [
        r"^(?:what'?s|what is)\s+(?:playing|this|this song|the song)(?:\?)?$",
        r"^(?:current|now playing)(?:\s+(?:song|track))?(?:\?)?$",
        r"^(?:song|track)\s+(?:info|information|name|title)(?:\?)?$",
    ]
    
    # Search patterns (with capture groups)
    PLAY_ARTIST_PATTERN = r"^play\s+(?:some\s+)?(?:music\s+by\s+)?(.+)$"
    PLAY_GENRE_PATTERN = r"^play\s+(?:some\s+)?(?:music\s+)?(?:genre\s+)?(?:of\s+)?(\w+)$"
    PLAY_SONG_PATTERN = r"^play\s+(?:the\s+)?(?:song\s+)?['\"]?(.+?)['\"]?$"
    PLAY_ALBUM_PATTERN = r"^play\s+(?:the\s+)?album\s+['\"]?(.+?)['\"]?$"
    
    # Playlist patterns
    LOAD_PLAYLIST_PATTERN = r"^(?:play|load)\s+(?:playlist\s+)?['\"]?(.+?)['\"]?$"
    SAVE_PLAYLIST_PATTERN = r"^save\s+(?:playlist\s+)?(?:as\s+)?['\"]?(.+?)['\"]?$"
    
    # Library management patterns
    LIBRARY_PATTERNS = [
        r"^(?:update|scan|refresh|index)\s+(?:music\s+)?library$",
        r"^(?:update|scan|refresh)\s+music$",
        r"^(?:scan|update)\s+(?:the\s+)?(?:music|library)$",
    ]

    COMMAND_START_HINT = (
        r"(?:play|resume|continue|unpause|pause|hold|stop|next|skip|previous|back|"
        r"volume|turn|increase|raise|decrease|lower|louder|quieter|what|current|"
        r"now|update|scan|refresh|index|load|save)"
    )
    
    def __init__(self):
        # Compile patterns for efficiency
        self.play_regexes = [re.compile(p, re.IGNORECASE) for p in self.PLAY_PATTERNS]
        self.pause_regexes = [re.compile(p, re.IGNORECASE) for p in self.PAUSE_PATTERNS]
        self.stop_regexes = [re.compile(p, re.IGNORECASE) for p in self.STOP_PATTERNS]
        self.next_regexes = [re.compile(p, re.IGNORECASE) for p in self.NEXT_PATTERNS]
        self.previous_regexes = [re.compile(p, re.IGNORECASE) for p in self.PREVIOUS_PATTERNS]
        
        self.volume_up_regexes = [re.compile(p, re.IGNORECASE) for p in self.VOLUME_UP_PATTERNS]
        self.volume_down_regexes = [re.compile(p, re.IGNORECASE) for p in self.VOLUME_DOWN_PATTERNS]
        self.volume_set_regex = re.compile(self.VOLUME_SET_PATTERN, re.IGNORECASE)
        
        self.status_regexes = [re.compile(p, re.IGNORECASE) for p in self.STATUS_PATTERNS]
        
        self.play_artist_regex = re.compile(self.PLAY_ARTIST_PATTERN, re.IGNORECASE)
        self.play_genre_regex = re.compile(self.PLAY_GENRE_PATTERN, re.IGNORECASE)
        self.play_song_regex = re.compile(self.PLAY_SONG_PATTERN, re.IGNORECASE)
        self.play_album_regex = re.compile(self.PLAY_ALBUM_PATTERN, re.IGNORECASE)
        
        self.load_playlist_regex = re.compile(self.LOAD_PLAYLIST_PATTERN, re.IGNORECASE)
        self.save_playlist_regex = re.compile(self.SAVE_PLAYLIST_PATTERN, re.IGNORECASE)
        
        self.library_regexes = [re.compile(p, re.IGNORECASE) for p in self.LIBRARY_PATTERNS]

    def _normalize_for_matching(self, text: str) -> str:
        """Normalize transcript for deterministic command matching."""
        normalized = (text or "").strip().lower()
        if not normalized:
            return ""

        # Drop obvious wake-style address prefixes that can leak into STT.
        # Examples: "hey minecraft, stop playing", "ok openclaw stop".
        normalized = re.sub(
            rf"^(?:hey|hi|hello|ok(?:ay)?)\s+[a-z0-9][a-z0-9'\- ]{{0,40}},\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            rf"^(?:hey|hi|hello|ok(?:ay)?)\s+[a-z0-9][a-z0-9'\- ]{{0,40}}\s+(?={self.COMMAND_START_HINT}\b)",
            "",
            normalized,
            flags=re.IGNORECASE,
        )

        # Trim polite/filler words and end punctuation to improve regex hit rate.
        normalized = re.sub(r"^(?:please\s+)", "", normalized)
        normalized = re.sub(r"[\s\.,!?;:]+$", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized
    
    def parse(self, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Parse user input for music commands.
        
        Args:
            text: User input text (transcript)
        
        Returns:
            Tuple of (command, params) if matched, None otherwise
            
        Examples:
            "play" -> ("play", {})
            "volume 50" -> ("set_volume", {"level": 50})
            "play beatles" -> ("play_artist", {"artist": "beatles"})
            "what's playing" -> ("get_current_track", {})
        """
        text = self._normalize_for_matching(text)
        
        if not text:
            return None
        
        # === Playback Control ===
        
        if any(regex.match(text) for regex in self.play_regexes):
            return ("play", {})
        
        # User requested behavior: pause requests should act as stop.
        if any(regex.match(text) for regex in self.pause_regexes):
            return ("stop", {})
        
        if any(regex.match(text) for regex in self.stop_regexes):
            return ("stop", {})
        
        if any(regex.match(text) for regex in self.next_regexes):
            return ("next_track", {})
        
        if any(regex.match(text) for regex in self.previous_regexes):
            return ("previous_track", {})
        
        # === Volume Control ===
        
        # Volume set (e.g., "volume 50", "50%")
        match = self.volume_set_regex.match(text)
        if match:
            level = int(match.group(1))
            return ("set_volume", {"level": level})
        
        if any(regex.match(text) for regex in self.volume_up_regexes):
            return ("volume_up", {"amount": 10})
        
        if any(regex.match(text) for regex in self.volume_down_regexes):
            return ("volume_down", {"amount": 10})
        
        # === Status Queries ===
        
        if any(regex.match(text) for regex in self.status_regexes):
            return ("get_current_track", {})
        
        # === Library Management ===
        
        if any(regex.match(text) for regex in self.library_regexes):
            return ("update_library", {})
        
        # === Search and Play (Heuristic Patterns) ===
        
        # These are more ambiguous and should prioritize specific matches
        
        # Play album (has "album" keyword)
        if "album" in text:
            match = self.play_album_regex.match(text)
            if match:
                album = match.group(1).strip()
                return ("play_album", {"album": album})
        
        # Play artist (has "by" or common artist indicators)
        if " by " in text or "music by" in text:
            match = self.play_artist_regex.match(text)
            if match:
                artist = match.group(1).strip()
                return ("play_artist", {"artist": artist, "shuffle": True})
        
        # Play genre (single word after "play some")
        if text.startswith("play some ") or text.startswith("play "):
            words = text.split()
            genre = None
            if len(words) == 2:  # "play jazz", "play rock"
                genre = words[1]
            elif len(words) == 3 and words[1] == "some":  # "play some jazz"
                genre = words[2]

            if genre:
                # Common genres (helps disambiguate from artist names)
                common_genres = [
                    "rock", "pop", "jazz", "blues", "classical", "country",
                    "hip-hop", "hiphop", "rap", "metal", "punk", "folk",
                    "electronic", "dance", "reggae", "soul", "funk", "indie"
                ]
                if genre in common_genres:
                    return ("play_genre", {"genre": genre, "shuffle": True})
        
        # === Playlist Management ===
        
        # Load playlist
        if text.startswith("play playlist") or text.startswith("load playlist"):
            match = self.load_playlist_regex.match(text)
            if match:
                playlist = match.group(1).strip()
                return ("load_playlist", {"name": playlist})
        
        # Save playlist
        if text.startswith("save playlist") or text.startswith("save as"):
            match = self.save_playlist_regex.match(text)
            if match:
                playlist = match.group(1).strip()
                return ("save_playlist", {"name": playlist})
        
        # No fast-path match - return None to trigger LLM fallback
        return None
    
    def is_music_related(self, text: str) -> bool:
        """
        Check if text appears to be music-related (for routing decisions).
        
        Args:
            text: User input text
        
        Returns:
            True if text contains music-related keywords
        """
        text = text.lower()
        
        music_keywords = [
            "play", "pause", "stop", "skip", "next", "previous", "back",
            "volume", "music", "song", "track", "album", "artist", "playlist",
            "playlists", "queue", "queued", "genre", "playing", "library", "scan", "update", "resume",
        ]
        
        return any(keyword in text for keyword in music_keywords)
