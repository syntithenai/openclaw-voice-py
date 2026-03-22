from __future__ import annotations

import shutil
from pathlib import Path

from orchestrator.music.library_index import LibraryIndex


def _make_index(tmp_path: Path) -> LibraryIndex:
    library_root = tmp_path / "music"
    library_root.mkdir()
    index = LibraryIndex(str(tmp_path / "library.sqlite3"), str(library_root))
    index._probe_ffprobe = lambda abs_path: {
        "duration_s": 123.0,
        "title": Path(abs_path).stem,
        "artist": "Test Artist",
        "album": "Test Album",
        "genre": "Test Genre",
        "codec": "mp3",
        "sample_rate": 44100,
        "channels": 2,
    }
    return index


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_incremental_scan_skips_unchanged_dirs_and_updates_changed_file(tmp_path: Path) -> None:
    index = _make_index(tmp_path)
    library_root = Path(index.library_root)
    song = library_root / "AlbumA" / "song1.mp3"
    _write_file(song, b"first")

    assert index.rebuild() == 1

    first_scan = index.scan_incremental()
    assert first_scan["changed"] == 0
    assert int(index.stats()["songs"]) == 1

    _write_file(song, b"first but changed")
    second_scan = index.scan_incremental()
    assert second_scan["changed"] >= 1

    row = index._conn.execute(
        "SELECT size_bytes, title FROM tracks WHERE path = ?",
        ("AlbumA/song1.mp3",),
    ).fetchone()
    assert row is not None
    assert int(row["size_bytes"]) == len(b"first but changed")
    assert str(row["title"]) == "song1"


def test_incremental_scan_removes_deleted_file_and_removed_directory(tmp_path: Path) -> None:
    index = _make_index(tmp_path)
    library_root = Path(index.library_root)
    song1 = library_root / "AlbumA" / "song1.mp3"
    song2 = library_root / "AlbumB" / "song2.mp3"
    _write_file(song1, b"a")
    _write_file(song2, b"b")

    assert index.rebuild() == 2
    assert int(index.stats()["songs"]) == 2

    song1.unlink()
    removed_file_scan = index.scan_incremental()
    assert removed_file_scan["changed"] >= 1
    assert index.get_track("AlbumA/song1.mp3") is None
    assert int(index.stats()["songs"]) == 1

    shutil.rmtree(song2.parent)
    removed_dir_scan = index.scan_incremental()
    assert removed_dir_scan["changed"] >= 1
    assert index.get_track("AlbumB/song2.mp3") is None
    assert int(index.stats()["songs"]) == 0


def test_corrupted_database_is_recreated_and_rebuild_succeeds(tmp_path: Path) -> None:
    library_root = tmp_path / "music"
    library_root.mkdir()
    db_path = tmp_path / "library.sqlite3"
    db_path.write_bytes(b"not a sqlite database")

    index = LibraryIndex(str(db_path), str(library_root))
    index._probe_ffprobe = lambda abs_path: {
        "duration_s": 123.0,
        "title": Path(abs_path).stem,
        "artist": "Test Artist",
        "album": "Test Album",
        "genre": "Test Genre",
        "codec": "mp3",
        "sample_rate": 44100,
        "channels": 2,
    }

    song = library_root / "Recovered" / "song1.mp3"
    _write_file(song, b"recovered")

    assert index.rebuild() == 1
    assert int(index.stats()["songs"]) == 1
    assert index.get_track("Recovered/song1.mp3") is not None