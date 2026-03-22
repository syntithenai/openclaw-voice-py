import asyncio

import pytest

from orchestrator.music.manager import MusicManager


class FakePool:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def execute(self, command: str):
        self.commands.append(command)
        return {}


def test_clear_queue_detaches_loaded_saved_playlist() -> None:
    pool = FakePool()
    manager = MusicManager(pool, pipewire_stream_normalize_enabled=False)
    manager._loaded_playlist_name = "Roadtrip Mix"

    result = asyncio.run(manager.clear_queue())

    assert result == "Queue cleared"
    assert pool.commands == ["clear"]
    assert manager.get_loaded_playlist_name() == ""


def test_clear_queue_without_loaded_playlist_still_succeeds() -> None:
    pool = FakePool()
    manager = MusicManager(pool, pipewire_stream_normalize_enabled=False)

    result = asyncio.run(manager.clear_queue())

    assert result == "Queue cleared"
    assert pool.commands == ["clear"]
    assert manager.get_loaded_playlist_name() == ""


@pytest.mark.asyncio
async def test_list_playlists_uses_direct_store_when_available() -> None:
    class PlaylistPool(FakePool):
        def list_playlists_direct(self):
            return ["Roadtrip", "Ambient"]

        async def execute_list(self, command: str, timeout=None):
            raise AssertionError(f"execute_list should not run for {command}")

    pool = PlaylistPool()
    manager = MusicManager(pool, pipewire_stream_normalize_enabled=False)

    playlists = await manager.list_playlists()

    assert playlists == ["Roadtrip", "Ambient"]
