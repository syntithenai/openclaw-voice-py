from __future__ import annotations

# Native music client public surface.
# The current implementation lives in native_backend.py as a compatibility backend.
from .native_backend import NativeMusicConnection, NativeMusicClientPool

__all__ = ["NativeMusicConnection", "NativeMusicClientPool"]
