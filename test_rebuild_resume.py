from __future__ import annotations

from pathlib import Path
import pytest

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


def test_incomplete_rebuild_detection_and_resume(tmp_path: Path) -> None:
    """Verify that incomplete rebuild can be detected and resumed."""
    index = _make_index(tmp_path)
    library_root = Path(index.library_root)
    
    # Create some test files
    song1 = library_root / "Artist1" / "Album1" / "song1.mp3"
    song2 = library_root / "Artist1" / "Album1" / "song2.mp3"
    song3 = library_root / "Artist2" / "Album2" / "song3.mp3"
    _write_file(song1, b"a")
    _write_file(song2, b"b")
    _write_file(song3, b"c")
    
    # Simulate incomplete rebuild: start rebuild, commit some directories,
    # then mark as in-progress (simulating a crash)
    result = index.rebuild()
    assert result == 3  # All files indexed
    
    # Now verify there's no incomplete marker (build completed)
    assert not index.detect_incomplete_rebuild()
    
    # Simulate second rebuild starting but crashing after first batch
    # by manually inserting the in-progress marker
    index._conn.execute(
        "INSERT INTO directories(path, mtime_ns, last_scanned_ts) VALUES(?, ?, ?)",
        ("__rebuild_in_progress__", 0, 0),
    )
    index._conn.commit()
    
    # Verify incomplete rebuild is detected
    assert index.detect_incomplete_rebuild()
    
    # Clean up and resume with incremental scan
    index.cleanup_incomplete_rebuild()
    assert not index.detect_incomplete_rebuild()
    
    # Incremental scan should complete without error (no changes since last complete rebuild)
    scan_result = index.scan_incremental()
    assert scan_result["changed"] == 0  # No files changed
    assert int(index.stats()["songs"]) == 3  # All 3 still in index


def test_incomplete_rebuild_resume_picks_up_missed_files(tmp_path: Path) -> None:
    """Verify that resuming a build picks up files that were missed in first pass."""
    index = _make_index(tmp_path)
    library_root = Path(index.library_root)
    
    # Create initial file
    song1 = library_root / "Album1" / "song1.mp3"
    _write_file(song1, b"a")
    
    # Do a full rebuild
    result = index.rebuild()
    assert result == 1
    
    # Now create a second file and simulate incomplete rebuild
    song2 = library_root / "Album2" / "song2.mp3"
    _write_file(song2, b"b")
    
    # Manually mark rebuild as in-progress (simulating crash)
    index._conn.execute(
        "INSERT INTO directories(path, mtime_ns, last_scanned_ts) VALUES(?, ?, ?)",
        ("__rebuild_in_progress__", 0, 0),
    )
    index._conn.commit()
    
    # Clean up and resume with incremental scan
    index.cleanup_incomplete_rebuild()
    scan_result = index.scan_incremental()
    
    # The new file should be picked up
    assert scan_result["changed"] >= 1
    assert int(index.stats()["songs"]) == 2


def test_rebuild_checkpoint_during_scan(tmp_path: Path) -> None:
    """Verify that directories are checkpointed incrementally during rebuild, not just at the end."""
    index = _make_index(tmp_path)
    library_root = Path(index.library_root)
    
    # Create files in different directories
    song1 = library_root / "Artist1" / "Album1" / "song1.mp3"
    song2 = library_root / "Artist2" / "Album2" / "song2.mp3"
    _write_file(song1, b"a")
    _write_file(song2, b"b")
    
    # Do rebuild
    result = index.rebuild()
    assert result == 2
    
    # Verify directories are in the checkpoint table
    dirs = {
        str(row[0])
        for row in index._conn.execute("SELECT path FROM directories").fetchall()
    }
    assert "" in dirs  # Root library directory
    assert "Artist1" in dirs
    assert "Artist1/Album1" in dirs
    assert "Artist2" in dirs
    assert "Artist2/Album2" in dirs
    assert "__rebuild_in_progress__" not in dirs  # Should be cleaned up after build completes
