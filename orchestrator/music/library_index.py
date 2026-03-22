from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import logging
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

SUPPORTED_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".alac", ".aiff", ".webm",
}


class LibraryIndex:
    def __init__(self, db_path: str, library_root: str):
        self.db_path = Path(db_path)
        self.library_root = Path(library_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.library_root.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._init_or_recover_database()

    def _init_or_recover_database(self) -> None:
        """Initialize database connection, recovering from corruption if needed."""
        self._close_connection()
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect_database()
            self._verify_database(conn)
            self._conn = conn
            self._init_schema()
        except sqlite3.DatabaseError as e:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            logger.error(
                "🚨 MUSIC DATABASE CORRUPTED: %s (at %s). Removing corrupted database and creating new one.",
                e,
                self.db_path,
            )
            print(f"🚨 MUSIC DATABASE CORRUPTED: {e}", flush=True)
            print(f"   Removing corrupted database at {self.db_path}", flush=True)
            print(f"   Creating fresh database...", flush=True)

            self._remove_database_files()
            self._conn = self._connect_database()
            self._init_schema()
            logger.warning(
                "🆕 Music database recreated and ready for rescan. Run 'update library' to rebuild index."
            )
            print("🆕 Music database recreated. Run 'update library' to rebuild index.", flush=True)

    def _connect_database(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _verify_database(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("PRAGMA quick_check").fetchone()
        if row is None:
            return
        result = str(row[0] or "").strip()
        if result and result.lower() != "ok":
            raise sqlite3.DatabaseError(result)

    def _close_connection(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except sqlite3.Error:
            pass
        finally:
            self._conn = None

    def _remove_database_files(self) -> None:
        for path in [
            self.db_path,
            Path(str(self.db_path) + "-journal"),
            Path(str(self.db_path) + "-wal"),
            Path(str(self.db_path) + "-shm"),
        ]:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as cleanup_err:
                logger.warning("Failed to remove database file %s: %s", path, cleanup_err)

    def _run_with_recovery(self, action: str, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except sqlite3.DatabaseError as e:
            logger.error(
                "🚨 MUSIC DATABASE ERROR while %s: %s. Attempting recovery...",
                action,
                e,
            )
            print(f"🚨 MUSIC DATABASE ERROR while {action}: {e}. Attempting recovery...", flush=True)
            self._init_or_recover_database()
            try:
                return operation()
            except sqlite3.DatabaseError as retry_err:
                logger.error("🚨 Failed to recover database while %s: %s", action, retry_err)
                print(f"🚨 Failed to recover database while {action}: {retry_err}", flush=True)
                raise

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks(
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                duration_s REAL,
                title TEXT,
                artist TEXT,
                album TEXT,
                genre TEXT,
                codec TEXT,
                sample_rate INTEGER,
                channels INTEGER,
                added_ts REAL NOT NULL,
                updated_ts REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS directories(
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                last_scanned_ts REAL NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_directories_last_scanned_ts ON directories(last_scanned_ts)")
        self._conn.commit()

    def _rel_dir(self, abs_path: Path) -> str:
        rel = abs_path.relative_to(self.library_root).as_posix()
        return "" if rel == "." else rel

    def _get_known_dir_mtime(self, rel_dir: str) -> int | None:
        row = self._conn.execute(
            "SELECT mtime_ns FROM directories WHERE path = ?",
            (rel_dir,),
        ).fetchone()
        if not row:
            return None
        return int(row[0])

    def _upsert_directory(self, rel_dir: str, mtime_ns: int) -> None:
        self._conn.execute(
            """
            INSERT INTO directories(path, mtime_ns, last_scanned_ts)
            VALUES(?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              mtime_ns=excluded.mtime_ns,
              last_scanned_ts=excluded.last_scanned_ts
            """,
            (rel_dir, int(mtime_ns), time.time()),
        )

    def _file_fingerprint_changed(self, abs_path: Path, existing_row: sqlite3.Row | None) -> bool:
        if existing_row is None:
            return True
        try:
            st = abs_path.stat()
        except OSError:
            return False
        return (
            int(existing_row["mtime_ns"]) != int(st.st_mtime_ns)
            or int(existing_row["size_bytes"]) != int(st.st_size)
        )

    def _load_existing_rows_for_dir(self, rel_dir: str) -> dict[str, sqlite3.Row]:
        if rel_dir:
            prefix = f"{rel_dir}/%"
            rows = self._conn.execute(
                "SELECT * FROM tracks WHERE path LIKE ? AND instr(substr(path, ?), '/') = 0",
                (prefix, len(rel_dir) + 2),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tracks WHERE instr(path, '/') = 0"
            ).fetchall()
        return {str(row["path"]): row for row in rows}

    def _scan_dir(self, abs_dir: Path) -> tuple[int, int]:
        rel_dir = self._rel_dir(abs_dir)
        try:
            dir_stat = abs_dir.stat()
        except OSError:
            return (0, 0)

        indexed = 0
        changed = 0
        current_mtime_ns = int(dir_stat.st_mtime_ns)
        previous_mtime_ns = self._get_known_dir_mtime(rel_dir)
        dir_changed = previous_mtime_ns != current_mtime_ns
        existing_rows = self._load_existing_rows_for_dir(rel_dir)

        if dir_changed:
            seen_files: set[str] = set()
            try:
                for entry in os.scandir(abs_dir):
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    abs_file = Path(entry.path)
                    if abs_file.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    rel_file = abs_file.relative_to(self.library_root).as_posix()
                    seen_files.add(rel_file)
                    existing_row = existing_rows.get(rel_file)
                    if not self._file_fingerprint_changed(abs_file, existing_row):
                        indexed += 1
                        continue
                    row = self._track_row(abs_file)
                    if not row:
                        continue
                    if existing_row is not None:
                        row["added_ts"] = float(existing_row["added_ts"])
                    self._conn.execute(
                        """
                        INSERT INTO tracks(path,mtime_ns,size_bytes,duration_s,title,artist,album,genre,codec,sample_rate,channels,added_ts,updated_ts)
                        VALUES (:path,:mtime_ns,:size_bytes,:duration_s,:title,:artist,:album,:genre,:codec,:sample_rate,:channels,:added_ts,:updated_ts)
                        ON CONFLICT(path) DO UPDATE SET
                          mtime_ns=excluded.mtime_ns,
                          size_bytes=excluded.size_bytes,
                          duration_s=excluded.duration_s,
                          title=excluded.title,
                          artist=excluded.artist,
                          album=excluded.album,
                          genre=excluded.genre,
                          codec=excluded.codec,
                          sample_rate=excluded.sample_rate,
                          channels=excluded.channels,
                          updated_ts=excluded.updated_ts
                        """,
                        row,
                    )
                    indexed += 1
                    changed += 1
            except FileNotFoundError:
                return (indexed, changed)

            stale_files = set(existing_rows.keys()) - seen_files
            for stale in stale_files:
                self._conn.execute("DELETE FROM tracks WHERE path = ?", (stale,))
                changed += 1
        else:
            for rel_file, existing_row in existing_rows.items():
                abs_file = self.library_root / rel_file
                if not self._file_fingerprint_changed(abs_file, existing_row):
                    indexed += 1
                    continue
                row = self._track_row(abs_file)
                if not row:
                    continue
                row["added_ts"] = float(existing_row["added_ts"])
                self._conn.execute(
                    """
                    INSERT INTO tracks(path,mtime_ns,size_bytes,duration_s,title,artist,album,genre,codec,sample_rate,channels,added_ts,updated_ts)
                    VALUES (:path,:mtime_ns,:size_bytes,:duration_s,:title,:artist,:album,:genre,:codec,:sample_rate,:channels,:added_ts,:updated_ts)
                    ON CONFLICT(path) DO UPDATE SET
                      mtime_ns=excluded.mtime_ns,
                      size_bytes=excluded.size_bytes,
                      duration_s=excluded.duration_s,
                      title=excluded.title,
                      artist=excluded.artist,
                      album=excluded.album,
                      genre=excluded.genre,
                      codec=excluded.codec,
                      sample_rate=excluded.sample_rate,
                      channels=excluded.channels,
                      updated_ts=excluded.updated_ts
                    """,
                    row,
                )
                indexed += 1
                changed += 1

        self._upsert_directory(rel_dir, current_mtime_ns)

        try:
            child_dirs = [
                Path(entry.path)
                for entry in os.scandir(abs_dir)
                if entry.is_dir(follow_symlinks=False)
            ]
        except FileNotFoundError:
            return (indexed, changed)

        for child_dir in child_dirs:
            child_indexed, child_changed = self._scan_dir(child_dir)
            indexed += child_indexed
            changed += child_changed

        return (indexed, changed)

    def _probe_ffprobe(self, abs_path: Path) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(abs_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return {}
            data = json.loads(proc.stdout)
            streams = data.get("streams") or []
            fmt = data.get("format") or {}
            audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
            tags = fmt.get("tags") or {}
            return {
                "duration_s": float(fmt.get("duration") or 0.0) or None,
                "title": str(tags.get("title") or "").strip() or None,
                "artist": str(tags.get("artist") or "").strip() or None,
                "album": str(tags.get("album") or "").strip() or None,
                "genre": str(tags.get("genre") or "").strip() or None,
                "codec": str(audio_stream.get("codec_name") or "").strip() or None,
                "sample_rate": int(audio_stream.get("sample_rate") or 0) or None,
                "channels": int(audio_stream.get("channels") or 0) or None,
            }
        except Exception:
            return {}

    def _track_row(self, abs_path: Path) -> dict[str, Any] | None:
        try:
            st = abs_path.stat()
        except OSError:
            return None
        if abs_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None
        rel = abs_path.relative_to(self.library_root).as_posix()
        meta = self._probe_ffprobe(abs_path)
        now = time.time()
        title = meta.get("title") or abs_path.stem
        return {
            "path": rel,
            "mtime_ns": int(st.st_mtime_ns),
            "size_bytes": int(st.st_size),
            "duration_s": meta.get("duration_s"),
            "title": title,
            "artist": meta.get("artist"),
            "album": meta.get("album"),
            "genre": meta.get("genre"),
            "codec": meta.get("codec"),
            "sample_rate": meta.get("sample_rate"),
            "channels": meta.get("channels"),
            "added_ts": now,
            "updated_ts": now,
        }

    def rebuild(self) -> int:
        def _rebuild() -> int:
            def _collect_supported_files() -> list[Path]:
                files: list[Path] = []
                for root, _dirs, names in os.walk(self.library_root):
                    for name in names:
                        abs_path = Path(root) / name
                        if abs_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                            files.append(abs_path)
                return files

            def _eta_str(elapsed_s: float, done: int, total: int) -> str:
                if done <= 0 or total <= 0 or done >= total:
                    return "00:00"
                rate = done / max(elapsed_s, 1e-6)
                if rate <= 0:
                    return "--:--"
                remaining_s = int((total - done) / rate)
                mins, secs = divmod(max(0, remaining_s), 60)
                hours, mins = divmod(mins, 60)
                if hours > 0:
                    return f"{hours:02d}:{mins:02d}:{secs:02d}"
                return f"{mins:02d}:{secs:02d}"

            found_paths: set[str] = set()
            found_dirs: dict[str, int] = {}
            supported_files = _collect_supported_files()
            total_files = len(supported_files)
            started = time.monotonic()
            log_interval = 25
            commit_interval = 25
            now = time.time()

            logger.warning(
                "Music index full rebuild started: root=%s candidates=%d",
                self.library_root,
                total_files,
            )
            print(
                f"→ Music index: full rebuild started (candidates={total_files})",
                flush=True,
            )

            self._conn.execute(
                "INSERT INTO directories(path, mtime_ns, last_scanned_ts) VALUES(?, ?, ?)",
                ("__rebuild_in_progress__", 0, now),
            )
            self._conn.commit()

            for root, _dirs, _files in os.walk(self.library_root):
                abs_root = Path(root)
                try:
                    found_dirs[self._rel_dir(abs_root)] = int(abs_root.stat().st_mtime_ns)
                except OSError:
                    pass

            for idx, abs_path in enumerate(supported_files, start=1):
                row = self._track_row(abs_path)
                if row:
                    found_paths.add(str(row["path"]))
                    self._conn.execute(
                        """
                        INSERT INTO tracks(path,mtime_ns,size_bytes,duration_s,title,artist,album,genre,codec,sample_rate,channels,added_ts,updated_ts)
                        VALUES (:path,:mtime_ns,:size_bytes,:duration_s,:title,:artist,:album,:genre,:codec,:sample_rate,:channels,:added_ts,:updated_ts)
                        ON CONFLICT(path) DO UPDATE SET
                          mtime_ns=excluded.mtime_ns,
                          size_bytes=excluded.size_bytes,
                          duration_s=excluded.duration_s,
                          title=excluded.title,
                          artist=excluded.artist,
                          album=excluded.album,
                          genre=excluded.genre,
                          codec=excluded.codec,
                          sample_rate=excluded.sample_rate,
                          channels=excluded.channels,
                          updated_ts=excluded.updated_ts
                        """,
                        row,
                    )

                if idx % log_interval == 0 or idx == total_files:
                    elapsed = time.monotonic() - started
                    pct = (idx / max(total_files, 1)) * 100.0
                    rate = idx / max(elapsed, 1e-6)
                    logger.warning(
                        "Music index rebuild progress: %d/%d (%.1f%%) rate=%.1f files/s eta=%s",
                        idx,
                        total_files,
                        pct,
                        rate,
                        _eta_str(elapsed, idx, total_files),
                    )
                    print(
                        f"   Music index progress: {idx}/{total_files} ({pct:.1f}%) "
                        f"rate={rate:.1f} files/s eta={_eta_str(elapsed, idx, total_files)}",
                        flush=True,
                    )
                if idx % commit_interval == 0 or idx == total_files:
                    cur = self._conn.cursor()
                    for dir_path, mtime_ns in found_dirs.items():
                        if dir_path != "__rebuild_in_progress__":
                            cur.execute(
                                "INSERT OR REPLACE INTO directories(path, mtime_ns, last_scanned_ts) VALUES(?, ?, ?)",
                                (dir_path, mtime_ns, time.time()),
                            )
                    self._conn.commit()

            cur = self._conn.cursor()
            existing = {r[0] for r in cur.execute("SELECT path FROM tracks").fetchall()}
            for stale in existing - found_paths:
                cur.execute("DELETE FROM tracks WHERE path = ?", (stale,))

            cur.execute("DELETE FROM directories WHERE path = ?", ("__rebuild_in_progress__",))
            for dir_path, mtime_ns in found_dirs.items():
                cur.execute(
                    "INSERT OR REPLACE INTO directories(path, mtime_ns, last_scanned_ts) VALUES(?, ?, ?)",
                    (dir_path, mtime_ns, now),
                )
            cur.execute("INSERT OR REPLACE INTO tracks(path,mtime_ns,size_bytes,added_ts,updated_ts) VALUES('__meta__',0,0,?,?)", (now, now))
            cur.execute("DELETE FROM tracks WHERE path='__meta__'")
            self._conn.commit()
            elapsed_total = time.monotonic() - started
            logger.warning(
                "Music index full rebuild complete: indexed=%d dirs=%d elapsed=%.1fs",
                len(found_paths),
                len(found_dirs),
                elapsed_total,
            )
            print(
                f"✓ Music index: full rebuild complete (indexed={len(found_paths)}, elapsed={elapsed_total:.1f}s)",
                flush=True,
            )
            return len(found_paths)

        return self._run_with_recovery("running full rebuild", _rebuild)

    def scan_incremental(self) -> dict[str, int]:
        def _scan() -> dict[str, int]:
            if not self.library_root.exists():
                return {"indexed": 0, "changed": 0}

            indexed, changed = self._scan_dir(self.library_root)

            existing_dirs = {
                str(row[0])
                for row in self._conn.execute("SELECT path FROM directories").fetchall()
            }
            actual_dirs = {
                self._rel_dir(Path(root))
                for root, dirs, _files in os.walk(self.library_root)
                if dirs is not None
            }
            stale_dirs = existing_dirs - actual_dirs
            for stale_dir in stale_dirs:
                self._conn.execute("DELETE FROM directories WHERE path = ?", (stale_dir,))
                if stale_dir:
                    self._conn.execute("DELETE FROM tracks WHERE path LIKE ?", (f"{stale_dir}/%",))
                else:
                    self._conn.execute("DELETE FROM tracks WHERE instr(path, '/') = 0")
                changed += 1

            self._conn.commit()
            return {"indexed": indexed, "changed": changed}

        return self._run_with_recovery("running incremental scan", _scan)

    def detect_incomplete_rebuild(self) -> bool:
        """Check if a full rebuild was interrupted (in-progress marker exists in directories table)."""
        def _detect() -> bool:
            row = self._conn.execute(
                "SELECT 1 FROM directories WHERE path = ?",
                ("__rebuild_in_progress__",),
            ).fetchone()
            return row is not None

        return self._run_with_recovery("checking rebuild marker", _detect)
    
    def cleanup_incomplete_rebuild(self) -> None:
        """Remove incomplete rebuild marker to allow fresh scan on next update."""
        def _cleanup() -> None:
            self._conn.execute("DELETE FROM directories WHERE path = ?", ("__rebuild_in_progress__",))
            self._conn.commit()

        self._run_with_recovery("cleaning rebuild marker", _cleanup)

    def stats(self) -> dict[str, str]:
        """Get library statistics, safely handling database errors."""
        def _stats() -> dict[str, str]:
            cur = self._conn.cursor()
            songs = int(cur.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
            latest = float(cur.execute("SELECT COALESCE(MAX(updated_ts), 0) FROM tracks").fetchone()[0])
            return {"songs": str(songs), "db_update": str(int(latest))}

        try:
            return self._run_with_recovery("getting stats", _stats)
        except sqlite3.DatabaseError:
            return {"songs": "0", "db_update": "0"}

    def search(self, field: str, query: str, limit: int | None = None, offset: int = 0) -> list[dict[str, str]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        like = f"%{q}%"
        field_map = {
            "artist": "COALESCE(artist,'')",
            "album": "COALESCE(album,'')",
            "title": "COALESCE(title,'')",
            "genre": "COALESCE(genre,'')",
            "any": "LOWER(COALESCE(title,'') || ' ' || COALESCE(artist,'') || ' ' || COALESCE(album,'') || ' ' || COALESCE(genre,'') || ' ' || path)",
            "file": "LOWER(path)",
        }
        expr = field_map.get(field, field_map["any"])
        sql = f"SELECT path,title,artist,album,genre,duration_s FROM tracks WHERE LOWER({expr}) LIKE ? ORDER BY path"
        params: list[Any] = [like]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        def _search() -> list[dict[str, str]]:
            rows = self._conn.execute(sql, params).fetchall()
            out: list[dict[str, str]] = []
            for row in rows:
                out.append(
                    {
                        "file": str(row["path"] or ""),
                        "title": str(row["title"] or ""),
                        "Title": str(row["title"] or ""),
                        "artist": str(row["artist"] or ""),
                        "Artist": str(row["artist"] or ""),
                        "album": str(row["album"] or ""),
                        "Album": str(row["album"] or ""),
                        "genre": str(row["genre"] or ""),
                        "duration": str(row["duration_s"] or 0),
                    }
                )
            return out

        return self._run_with_recovery("searching library", _search)

    def list_all(self) -> list[dict[str, str]]:
        def _list_all() -> list[dict[str, str]]:
            rows = self._conn.execute("SELECT path FROM tracks ORDER BY path").fetchall()
            return [{"file": str(r[0])} for r in rows]

        return self._run_with_recovery("listing tracks", _list_all)

    def get_track(self, path: str) -> dict[str, str] | None:
        def _get_track() -> dict[str, str] | None:
            row = self._conn.execute(
                "SELECT path,title,artist,album,genre,duration_s FROM tracks WHERE path = ?",
                (path,),
            ).fetchone()
            if not row:
                return None
            return {
                "file": str(row["path"] or ""),
                "title": str(row["title"] or ""),
                "Title": str(row["title"] or ""),
                "artist": str(row["artist"] or ""),
                "Artist": str(row["artist"] or ""),
                "album": str(row["album"] or ""),
                "Album": str(row["album"] or ""),
                "genre": str(row["genre"] or ""),
                "duration": str(row["duration_s"] or 0),
            }

        return self._run_with_recovery("getting track metadata", _get_track)
