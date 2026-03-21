import asyncio

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
