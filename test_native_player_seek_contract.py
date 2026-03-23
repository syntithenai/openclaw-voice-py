import asyncio
from pathlib import Path

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


def test_play_browser_direct_stream_for_supported_format(tmp_path: Path) -> None:
    library = tmp_path / "music"
    library.mkdir(parents=True, exist_ok=True)
    track = library / "song.mp3"
    track.write_bytes(b"ID3")

    player = NativePlayer(str(library))
    player.set_output_route("browser")

    class _Probe:
        container = "mp3"
        codec = "mp3"

    async def _fake_probe(_source: str):
        return _Probe()

    async def _fail_transcode(*_args, **_kwargs):
        raise AssertionError("transcode should not run for browser-playable mp3")

    player._ffmpeg.probe_async = _fake_probe  # type: ignore[assignment]
    player._ffmpeg.transcode_for_browser = _fail_transcode  # type: ignore[assignment]

    ok = asyncio.run(player.play("song.mp3"))

    assert ok is True
    assert player.browser_stream_path == str(track.resolve())


def test_play_browser_reuses_cached_transcode_on_replay(tmp_path: Path) -> None:
    library = tmp_path / "music"
    library.mkdir(parents=True, exist_ok=True)
    track = library / "song.ape"
    track.write_bytes(b"MAC ")

    player = NativePlayer(str(library))
    player.set_output_route("browser")

    class _Probe:
        container = "ape"
        codec = "ape"

    async def _fake_probe(_source: str):
        return _Probe()

    calls = {"count": 0}

    async def _fake_transcode(_src: str, out: str) -> bool:
        calls["count"] += 1
        Path(out).write_bytes(b"M4A")
        return True

    player._ffmpeg.probe_async = _fake_probe  # type: ignore[assignment]
    player._ffmpeg.transcode_for_browser = _fake_transcode  # type: ignore[assignment]

    first_ok = asyncio.run(player.play("song.ape"))
    assert first_ok is True
    assert calls["count"] == 1

    # stop() should clear active stream state but retain cached runtime media file.
    asyncio.run(player.stop())
    second_ok = asyncio.run(player.play("song.ape"))

    assert second_ok is True
    assert calls["count"] == 1
