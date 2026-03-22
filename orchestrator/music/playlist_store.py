from __future__ import annotations

import os
import tempfile
from pathlib import Path


class PlaylistStore:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_name(self, name: str) -> str:
        cleaned = "".join(ch for ch in str(name or "").strip() if ch not in "\\/:*?\"<>|")
        if not cleaned:
            raise ValueError("playlist name is required")
        return cleaned

    def _path_for(self, name: str) -> Path:
        base = self._sanitize_name(name)
        if not base.lower().endswith(".m3u"):
            base = f"{base}.m3u"
        path = (self.root_dir / base).resolve()
        if self.root_dir.resolve() not in path.parents:
            raise ValueError("invalid playlist name")
        return path

    def list_playlists(self) -> list[str]:
        out: list[str] = []
        for p in sorted(self.root_dir.glob("*.m3u"), key=lambda x: x.name.lower()):
            out.append(p.stem)
        return out

    def read_playlist(self, name: str) -> list[str]:
        path = self._path_for(name)
        if not path.exists():
            return []
        entries: list[str] = []
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(line)
        return entries

    def write_playlist(self, name: str, entries: list[str]) -> None:
        path = self._path_for(name)
        cleaned = [str(e).strip() for e in entries if str(e).strip()]
        payload = "#EXTM3U\n" + "\n".join(cleaned) + ("\n" if cleaned else "")
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".m3u", dir=str(self.root_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def delete_playlist(self, name: str) -> bool:
        path = self._path_for(name)
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def append_to_playlist(self, name: str, file_uri: str) -> None:
        rows = self.read_playlist(name)
        rows.append(str(file_uri).strip())
        self.write_playlist(name, rows)
