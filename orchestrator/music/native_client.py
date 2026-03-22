from __future__ import annotations

# Native music client public surface.
# The current implementation lives in mpd_client.py as a compatibility backend.
from .mpd_client import NativeMusicConnection, NativeMusicClientPool

__all__ = ["NativeMusicConnection", "NativeMusicClientPool"]
