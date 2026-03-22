"""Music control module with native playback and compatibility interfaces."""

from .native_client import (
    NativeMusicConnection,
    NativeMusicClientPool,
)
from .mpd_client import MPDConnection, MPDClientPool
from .library_index import LibraryIndex
from .playlist_store import PlaylistStore
from .native_player import NativePlayer
from .manager import MusicManager
from .parser import MusicFastPathParser
from .router import MusicRouter

__all__ = [
    "NativeMusicConnection",
    "NativeMusicClientPool",
    "MPDConnection",
    "MPDClientPool",
    "LibraryIndex",
    "PlaylistStore",
    "NativePlayer",
    "MusicManager",
    "MusicFastPathParser",
    "MusicRouter",
]
