import asyncio

from orchestrator.music.manager import MusicManager
from orchestrator.music.parser import MusicFastPathParser
from orchestrator.music.router import MusicRouter


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


def test_list_playlists_returns_backend_names() -> None:
    class PlaylistPool(FakePool):
        async def execute_list(self, command: str, timeout=None):
            assert command == "listplaylists"
            return [{"playlist": "Roadtrip"}, {"playlist": "Ambient"}]

    pool = PlaylistPool()
    manager = MusicManager(pool, pipewire_stream_normalize_enabled=False)

    playlists = asyncio.run(manager.list_playlists())

    assert playlists == ["Roadtrip", "Ambient"]


def test_parser_routes_clear_queue_phrases_to_clear_queue_command() -> None:
    parser = MusicFastPathParser()

    assert parser.parse("clear the queue") == ("clear_queue", {})
    assert parser.parse("empty queue") == ("clear_queue", {})


def test_music_router_music_clear_queue_tool_calls_manager_clear_queue() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.clear_calls = 0

        async def clear_queue(self) -> str:
            self.clear_calls += 1
            return "Queue cleared"

    manager = FakeManager()
    router = MusicRouter(manager)

    result = asyncio.run(router.handle_tool_call("music_clear_queue", {}))

    assert result == "Queue cleared."
    assert manager.clear_calls == 1
