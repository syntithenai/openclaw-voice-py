from __future__ import annotations

import asyncio

import pytest

from orchestrator.music import native_backend as music_client


class _FakeProc:
    """Stand-in for asyncio.subprocess.Process. wait() returns immediately."""
    returncode: int | None = 0

    async def wait(self) -> int:
        return 0


class _FakeLibrary:
    def get_track(self, file_uri: str) -> dict:
        return {"file": file_uri, "duration": "120"}


def _run_loop_one_advance(backend, timeout: float = 1.0) -> None:
    """Run _auto_advance_loop until it has performed one advance, then cancel."""
    async def _runner():
        task = asyncio.create_task(backend._auto_advance_loop())
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_runner())


def test_loop_advances_to_next_track_when_process_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loop must advance to position 1 when the proc at position 0 exits naturally."""
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state

    played_files: list[str] = []
    fake_proc = _FakeProc()

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        played_files.append(file_uri)
        backend.player._proc = None  # prevent loop from spawning again within timeout
        return True

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/first.mp3", id=101),
            music_client.QueueItem(file="Artist/Album/second.mp3", id=102),
        ]
        backend.current_pos = 0
        backend.state = "play"
        backend.player._proc = fake_proc
        backend.player.output_route = "local"

        monkeypatch.setattr(backend, "library", _FakeLibrary())
        monkeypatch.setattr(backend.player, "play", fake_play)

        _run_loop_one_advance(backend)

        assert played_files == ["Artist/Album/second.mp3"]
        assert backend.current_pos == 1
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.output_route = "local"


def test_loop_wraps_from_last_track_to_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loop must wrap from position 1 (last) back to position 0 and continue playing."""
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state

    played_files: list[str] = []
    fake_proc = _FakeProc()

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        played_files.append(file_uri)
        backend.player._proc = None
        return True

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/first.mp3", id=201),
            music_client.QueueItem(file="Artist/Album/second.mp3", id=202),
        ]
        backend.current_pos = 1
        backend.state = "play"
        backend.player._proc = fake_proc
        backend.player.output_route = "local"

        monkeypatch.setattr(backend, "library", _FakeLibrary())
        monkeypatch.setattr(backend.player, "play", fake_play)

        _run_loop_one_advance(backend)

        assert played_files == ["Artist/Album/first.mp3"]
        assert backend.current_pos == 0
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.output_route = "local"


def test_loop_aborts_when_user_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """If state != 'play' when proc exits, loop must NOT start a new track."""
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state

    played_files: list[str] = []
    fake_proc = _FakeProc()

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        played_files.append(file_uri)
        return True

    async def _runner():
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/first.mp3", id=301),
            music_client.QueueItem(file="Artist/Album/second.mp3", id=302),
        ]
        backend.current_pos = 0
        backend.state = "play"
        backend.player._proc = fake_proc
        backend.player.output_route = "local"

        monkeypatch.setattr(backend, "library", _FakeLibrary())
        monkeypatch.setattr(backend.player, "play", fake_play)

        task = asyncio.create_task(backend._auto_advance_loop())
        # Let one iteration run (proc.wait() returns immediately), then stop state
        # before the advance can fire by setting state to "stop" immediately.
        backend.state = "stop"
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    try:
        asyncio.run(_runner())
        assert played_files == []
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.output_route = "local"


@pytest.mark.asyncio
async def test_connection_serializes_overlapping_load_and_play_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(tmp_path))
    backend = music_client._NativeMusicBackend()
    conn = music_client.NativeMusicConnection("", 0)
    await conn.connect()

    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()
    played_files: list[str] = []

    async def fake_stop() -> None:
        stop_entered.set()
        await release_stop.wait()

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        del seek_s
        played_files.append(file_uri)
        return True

    monkeypatch.setattr(music_client, "_BACKEND", backend)
    monkeypatch.setattr(backend.player, "stop", fake_stop)
    monkeypatch.setattr(backend.player, "play", fake_play)
    monkeypatch.setattr(backend.playlists, "read_playlist", lambda name: ["new-song.mp3"])

    backend.queue = [music_client.QueueItem(file="old-song.mp3", id=1)]
    backend.current_pos = 0
    backend.state = "play"

    load_task = asyncio.create_task(conn.send_command('load "Roadtrip"'))
    await asyncio.wait_for(stop_entered.wait(), timeout=1.0)

    play_task = asyncio.create_task(conn.send_command("play 0"))
    await asyncio.sleep(0)
    assert not play_task.done()

    release_stop.set()
    await asyncio.gather(load_task, play_task)

    assert [item.file for item in backend.queue] == ["new-song.mp3"]
    assert backend.current_pos == 0
    assert played_files == ["new-song.mp3"]


def test_loop_handles_seekcur_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loop must skip advance when proc was replaced by seekcur, then advance for the NEW proc."""
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state

    played_files: list[str] = []
    old_proc = _FakeProc()
    new_proc = _FakeProc()

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        played_files.append(file_uri)
        backend.player._proc = None
        return True

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/first.mp3", id=401),
            music_client.QueueItem(file="Artist/Album/second.mp3", id=402),
        ]
        backend.current_pos = 0
        backend.state = "play"
        # Simulate seekcur already having replaced the proc
        backend.player._proc = new_proc  # seekcur proc
        backend.player.output_route = "local"

        monkeypatch.setattr(backend, "library", _FakeLibrary())
        monkeypatch.setattr(backend.player, "play", fake_play)

        # Run loop starting with new_proc (the seekcur replacement)
        _run_loop_one_advance(backend)

        # Should have advanced using the seekcur proc, not the old one
        assert played_files == ["Artist/Album/second.mp3"]
        assert backend.current_pos == 1
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.output_route = "local"


def test_status_browser_route_advances_when_elapsed_reaches_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Browser output has no process; status must auto-advance when elapsed >= duration."""
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_route = backend.player.output_route
    original_elapsed_anchor_ts = backend.elapsed_anchor_ts
    original_elapsed_anchor_value = backend.elapsed_anchor_value

    played_files: list[str] = []

    class _BrowserLibrary:
        def get_track(self, file_uri: str) -> dict:
            return {"file": file_uri, "duration": "10"}

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        del seek_s
        played_files.append(file_uri)
        backend.player.browser_stream_path = "/tmp/current.m4a"
        return True

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/first.mp3", id=501),
            music_client.QueueItem(file="Artist/Album/second.mp3", id=502),
        ]
        backend.current_pos = 0
        backend.state = "play"
        backend.player.output_route = "browser"
        backend.elapsed_anchor_value = 10.0
        backend.elapsed_anchor_ts = 0.0

        monkeypatch.setattr(backend, "library", _BrowserLibrary())
        monkeypatch.setattr(backend.player, "play", fake_play)

        status = asyncio.run(backend.execute("status"))

        assert played_files == ["Artist/Album/second.mp3"]
        assert backend.current_pos == 1
        assert status["song"] == "1"
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.output_route = original_route
        backend.elapsed_anchor_ts = original_elapsed_anchor_ts
        backend.elapsed_anchor_value = original_elapsed_anchor_value


def test_status_local_fallback_advances_once_for_finished_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    """When local proc has already exited, status fallback should advance once."""
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_route = backend.player.output_route
    original_proc = backend.player._proc
    original_finished_proc_id = backend._last_finished_local_proc_id

    played_files: list[str] = []
    finished_proc = _FakeProc()
    finished_proc.returncode = 0

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        del seek_s
        played_files.append(file_uri)
        # Keep the same finished proc object so a second status poll would
        # double-advance unless the fallback single-fire guard works.
        backend.player._proc = finished_proc
        return True

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/first.mp3", id=601),
            music_client.QueueItem(file="Artist/Album/second.mp3", id=602),
        ]
        backend.current_pos = 0
        backend.state = "play"
        backend.player.output_route = "local"
        backend.player._proc = finished_proc
        backend._last_finished_local_proc_id = None

        monkeypatch.setattr(backend, "library", _FakeLibrary())
        monkeypatch.setattr(backend.player, "play", fake_play)

        status_one = asyncio.run(backend.execute("status"))
        status_two = asyncio.run(backend.execute("status"))

        assert played_files == ["Artist/Album/second.mp3"]
        assert status_one["song"] == "1"
        assert status_two["song"] == "1"
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.output_route = original_route
        backend.player._proc = original_proc
        backend._last_finished_local_proc_id = original_finished_proc_id


def test_failed_play_sets_warning_in_status(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_delay = backend._play_failure_skip_delay_s
    original_warning = backend.last_warning
    original_warning_ts = backend.last_warning_ts

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        del file_uri, seek_s
        backend.player.last_error = "unsupported codec"
        return False

    try:
        backend.queue = [music_client.QueueItem(file="Artist/Album/bad.flac", id=701)]
        backend.current_pos = 0
        backend.state = "stop"
        backend._play_failure_skip_delay_s = 999.0

        monkeypatch.setattr(backend.player, "play", fake_play)

        asyncio.run(backend.execute("play 0"))
        status = asyncio.run(backend.execute("status"))

        assert status["state"] == "stop"
        assert "Playback failed for bad.flac: unsupported codec" == status["warning"]
    finally:
        backend._cancel_play_failure_skip()
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend._play_failure_skip_delay_s = original_delay
        backend.last_warning = original_warning
        backend.last_warning_ts = original_warning_ts


def test_failed_play_skips_to_next_track_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_delay = backend._play_failure_skip_delay_s
    original_warning = backend.last_warning
    original_warning_ts = backend.last_warning_ts

    played_files: list[str] = []
    attempts = {"count": 0}

    async def fake_play(file_uri: str, seek_s: int = 0) -> bool:
        del seek_s
        played_files.append(file_uri)
        attempts["count"] += 1
        if attempts["count"] == 1:
            backend.player.last_error = "decode failure"
            return False
        await asyncio.sleep(0)
        backend.player.last_error = ""
        return True

    async def _runner() -> None:
        await backend.execute("play 0")
        await asyncio.sleep(0.05)

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/bad.flac", id=801),
            music_client.QueueItem(file="Artist/Album/good.mp3", id=802),
        ]
        backend.current_pos = -1
        backend.state = "stop"
        backend._play_failure_skip_delay_s = 0.01

        monkeypatch.setattr(backend.player, "play", fake_play)

        asyncio.run(_runner())

        assert played_files == ["Artist/Album/bad.flac", "Artist/Album/good.mp3"]
        assert backend.current_pos == 1
        assert backend.state == "play"
        assert backend.last_warning == ""
    finally:
        backend._cancel_play_failure_skip()
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend._play_failure_skip_delay_s = original_delay
        backend.last_warning = original_warning
        backend.last_warning_ts = original_warning_ts


def test_load_playlist_clears_runtime_media_state(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_stream_path = backend.player.browser_stream_path
    original_override = backend.browser_file_override
    original_warning = backend.last_warning
    original_warning_ts = backend.last_warning_ts

    stop_calls = {"count": 0}

    async def fake_stop() -> None:
        stop_calls["count"] += 1

    try:
        backend.queue = [music_client.QueueItem(file="Artist/Album/prev.mp3", id=901)]
        backend.current_pos = 0
        backend.state = "play"
        backend.player.browser_stream_path = "/tmp/openclaw-runtime-media/current.m4a"
        backend.browser_file_override = "__runtime_media__/current.m4a"
        backend.last_warning = "Playback failed for prev.mp3"
        backend.last_warning_ts = 123.0

        monkeypatch.setattr(backend.player, "stop", fake_stop)
        monkeypatch.setattr(backend.playlists, "read_playlist", lambda _name: ["Artist/Album/new.mp3"])

        asyncio.run(backend.execute("load test"))

        assert stop_calls["count"] == 1
        assert backend.player.browser_stream_path == ""
        assert backend.browser_file_override == ""
        assert backend.last_warning == ""
        assert backend.state == "stop"
        assert [item.file for item in backend.queue] == ["Artist/Album/new.mp3"]
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.browser_stream_path = original_stream_path
        backend.browser_file_override = original_override
        backend.last_warning = original_warning
        backend.last_warning_ts = original_warning_ts


def test_clear_queue_clears_runtime_media_state(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_stream_path = backend.player.browser_stream_path
    original_override = backend.browser_file_override

    stop_calls = {"count": 0}

    async def fake_stop() -> None:
        stop_calls["count"] += 1

    try:
        backend.queue = [music_client.QueueItem(file="Artist/Album/prev.mp3", id=1001)]
        backend.current_pos = 0
        backend.state = "play"
        backend.player.browser_stream_path = "/tmp/openclaw-runtime-media/current.m4a"
        backend.browser_file_override = "__runtime_media__/current.m4a"

        monkeypatch.setattr(backend.player, "stop", fake_stop)

        asyncio.run(backend.execute("clear"))

        assert stop_calls["count"] == 1
        assert backend.queue == []
        assert backend.current_pos == -1
        assert backend.player.browser_stream_path == ""
        assert backend.browser_file_override == ""
        assert backend.state == "stop"
    finally:
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend.player.browser_stream_path = original_stream_path
        backend.browser_file_override = original_override


def test_sequential_failures_stop_after_exceeding_queue_length(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = music_client._BACKEND
    original_queue = list(backend.queue)
    original_pos = backend.current_pos
    original_state = backend.state
    original_delay = backend._play_failure_skip_delay_s
    original_warning = backend.last_warning
    original_warning_ts = backend.last_warning_ts
    original_fail_count = backend._sequential_failed_plays

    attempts = {"count": 0}

    async def always_fail_play(file_uri: str, seek_s: int = 0) -> bool:
        del file_uri, seek_s
        attempts["count"] += 1
        backend.player.last_error = "decode failure"
        return False

    async def _runner() -> None:
        await backend.execute("play 0")
        await asyncio.sleep(0.08)

    try:
        backend.queue = [
            music_client.QueueItem(file="Artist/Album/a.flac", id=1101),
            music_client.QueueItem(file="Artist/Album/b.flac", id=1102),
        ]
        backend.current_pos = -1
        backend.state = "stop"
        backend._play_failure_skip_delay_s = 0.01
        backend._sequential_failed_plays = 0

        monkeypatch.setattr(backend.player, "play", always_fail_play)

        asyncio.run(_runner())

        # Queue length is 2, so allow the third failure, then stop scheduling.
        assert attempts["count"] == 3
        assert backend.state == "stop"
        assert backend._sequential_failed_plays == 3
    finally:
        backend._cancel_play_failure_skip()
        backend.queue = original_queue
        backend.current_pos = original_pos
        backend.state = original_state
        backend._play_failure_skip_delay_s = original_delay
        backend.last_warning = original_warning
        backend.last_warning_ts = original_warning_ts
        backend._sequential_failed_plays = original_fail_count
