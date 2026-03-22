from __future__ import annotations

import pytest

from orchestrator.music import mpd_client


@pytest.mark.asyncio
async def test_update_uses_full_rebuild_for_empty_db(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    backend = mpd_client._BACKEND

    calls: list[str] = []

    class FakeLibrary:
        def __init__(self) -> None:
            self._songs = 0

        def stats(self) -> dict[str, str]:
            return {"songs": str(self._songs)}

        def rebuild(self) -> int:
            calls.append("rebuild")
            self._songs = 12
            return self._songs

        def scan_incremental(self) -> dict[str, int]:
            calls.append("incremental")
            return {"indexed": 0, "changed": 0}

    monkeypatch.setattr(backend, "library", FakeLibrary())

    with caplog.at_level("INFO"):
        result = await backend.execute("update")

    assert calls == ["rebuild"]
    assert result["songs"] == "12"
    assert "Music index update mode=full" in caplog.text


@pytest.mark.asyncio
async def test_update_uses_incremental_for_populated_db(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    backend = mpd_client._BACKEND

    calls: list[str] = []

    class FakeLibrary:
        def __init__(self) -> None:
            self._songs = 10

        def stats(self) -> dict[str, str]:
            return {"songs": str(self._songs)}

        def rebuild(self) -> int:
            calls.append("rebuild")
            return self._songs

        def scan_incremental(self) -> dict[str, int]:
            calls.append("incremental")
            self._songs = 11
            return {"indexed": 11, "changed": 2}

    monkeypatch.setattr(backend, "library", FakeLibrary())

    with caplog.at_level("INFO"):
        result = await backend.execute("update")

    assert calls == ["incremental"]
    assert result["songs"] == "11"
    assert "Music index update mode=incremental" in caplog.text
    assert "Music index incremental scan result indexed=11 changed=2" in caplog.text


@pytest.mark.asyncio
async def test_startup_index_guard_runs_once_and_then_skips(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    backend = mpd_client._BACKEND
    backend._startup_index_done = False

    calls: list[str] = []

    async def fake_execute(command: str, timeout=None):
        del timeout
        calls.append(command)
        return {"updating_db": "1", "songs": "7"}

    monkeypatch.setattr(backend, "execute", fake_execute)

    with caplog.at_level("INFO"):
        await backend.ensure_startup_index()
        await backend.ensure_startup_index()

    assert calls == ["update"]
    assert "Music index startup scan complete (background): songs=7" in caplog.text
    assert "Music index startup scan skipped: already initialized" in caplog.text


@pytest.mark.asyncio
async def test_pool_initialize_schedules_startup_index_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = mpd_client.MPDClientPool()
    calls: list[str] = []

    def fake_start_startup_index() -> None:
        calls.append("scheduled")

    monkeypatch.setattr(mpd_client._BACKEND, "start_startup_index", fake_start_startup_index)

    initialized = await pool.initialize()

    assert initialized is True
    assert calls == ["scheduled"]


@pytest.mark.asyncio
async def test_playlistinfo_stays_available_while_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = mpd_client._BACKEND
    original_queue = backend.queue
    original_indexing_active = backend._indexing_active

    class FakeLibrary:
        def get_track(self, path: str):
            raise AssertionError(f"get_track should not run during indexing for {path}")

    try:
        backend.queue = [mpd_client.QueueItem(file="Artist/Album/song.mp3", id=1234)]
        backend._indexing_active = True
        monkeypatch.setattr(backend, "library", FakeLibrary())

        rows = await backend.execute_list("playlistinfo")

        assert len(rows) == 1
        assert rows[0]["file"] == "Artist/Album/song.mp3"
        assert rows[0]["id"] == "1234"
        assert rows[0]["pos"] == "0"
    finally:
        backend.queue = original_queue
        backend._indexing_active = original_indexing_active
