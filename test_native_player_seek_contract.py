import asyncio

from orchestrator.music.native_player import NativePlayer


def test_seek_browser_route_does_not_restart_playback() -> None:
    player = NativePlayer(".")
    player.set_output_route("browser")
    player._last_source_path = "music/test.mp3"

    called = False

    async def _fake_play(rel_path: str, seek_s: int = 0) -> bool:
        nonlocal called
        called = True
        return True

    player.play = _fake_play  # type: ignore[assignment]

    ok = asyncio.run(player.seek(42))

    assert ok is True
    assert called is False
    assert player._last_seek_s == 42


def test_seek_local_route_restarts_with_offset() -> None:
    player = NativePlayer(".")
    player.set_output_route("local")
    player._last_source_path = "music/test.mp3"

    calls: list[tuple[str, int]] = []

    async def _fake_play(rel_path: str, seek_s: int = 0) -> bool:
        calls.append((rel_path, seek_s))
        return True

    player.play = _fake_play  # type: ignore[assignment]

    ok = asyncio.run(player.seek(17))

    assert ok is True
    assert calls == [("music/test.mp3", 17)]
