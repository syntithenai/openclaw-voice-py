from __future__ import annotations

import html as html_utils
import json
import mimetypes
from pathlib import Path
import ssl
import threading
from urllib.parse import quote, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def start_http_servers(service: Any, ssl_context: ssl.SSLContext | None) -> None:
    """Start embedded UI HTTP server and optional HTTP->HTTPS redirector."""

    class UIHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _parse_request_path(self) -> str:
            return unquote(urlsplit(self.path).path or "/")

        def _safe_join(self, root: Path, relative: str) -> Path | None:
            rel = relative.lstrip("/")
            candidate = (root / rel).resolve()
            if candidate == root or root in candidate.parents:
                return candidate
            return None

        def _list_directory(self, requested_path: str, fs_path: Path) -> bytes:
            title = f"Directory listing for {requested_path}"
            entries: list[str] = []
            if requested_path not in ("", "/"):
                parent = requested_path.rsplit("/", 1)[0] or "/"
                if not parent.startswith("/"):
                    parent = "/" + parent
                entries.append(f'<li><a href="{quote(parent, safe="/:@+-._~")}">..</a></li>')

            try:
                children = sorted(fs_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except Exception:
                children = []

            for child in children:
                name = child.name + ("/" if child.is_dir() else "")
                child_path = (requested_path.rstrip("/") + "/" + child.name).replace("//", "/")
                if child.is_dir():
                    child_path += "/"
                href = quote(child_path, safe="/:@+-._~")
                entries.append(f'<li><a href="{href}">{html_utils.escape(name)}</a></li>')

            doc = (
                "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
                + html_utils.escape(title)
                + "</title></head><body><h1>"
                + html_utils.escape(title)
                + "</h1><ul>"
                + "".join(entries)
                + "</ul></body></html>"
            )
            return doc.encode("utf-8")

        def _send_file(self, file_path: Path) -> None:
            data = file_path.read_bytes()
            content_type, _ = mimetypes.guess_type(str(file_path))
            self._send(data, content_type=content_type or "application/octet-stream")

        def _render_ui_index(self) -> bytes:
            template_values = {
                "__WS_PORT__": str(service.ws_port),
                "__MIC_STARTS_DISABLED__": "true" if service.mic_starts_disabled else "false",
                "__AUDIO_AUTHORITY__": str(service.audio_authority),
                "__SERVER_INSTANCE_ID__": str(service._instance_id),
            }
            static_root = Path(getattr(service, "static_root", Path(__file__).resolve().parent / "static")).resolve()
            index_path = static_root / "index.html"
            if not index_path.exists():
                return b"Embedded UI static file not found"
            rendered = index_path.read_text(encoding="utf-8")
            for token, value in template_values.items():
                rendered = rendered.replace(token, value)
            return rendered.encode("utf-8")

        def _serve_mount(self, request_path: str, mount_prefix: str, root: Path, allow_listing: bool) -> bool:
            if request_path != mount_prefix and not request_path.startswith(mount_prefix + "/"):
                return False

            relative = request_path[len(mount_prefix) :]
            target = self._safe_join(root, relative)
            if target is None or not target.exists():
                self._send(b"Not found", status=404, content_type="text/plain")
                return True

            if target.is_dir():
                if not allow_listing:
                    self._send(b"Directory listing disabled", status=403, content_type="text/plain")
                    return True
                listing = self._list_directory(request_path, target)
                self._send(listing, content_type="text/html; charset=utf-8")
                return True

            if target.is_file():
                self._send_file(target)
                return True

            self._send(b"Not found", status=404, content_type="text/plain")
            return True

        def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(b"", status=204, content_type="text/plain")

        def do_GET(self) -> None:  # noqa: N802
            path = self._parse_request_path()

            static_root = Path(getattr(service, "static_root", Path(__file__).resolve().parent / "static")).resolve()
            workspace_enabled = bool(getattr(service, "workspace_files_enabled", False))
            workspace_root = Path(str(getattr(service, "workspace_files_root", ""))).expanduser().resolve() if workspace_enabled else None
            workspace_list = bool(getattr(service, "workspace_files_allow_listing", False))
            media_enabled = bool(getattr(service, "media_files_enabled", False))
            media_root = Path(str(getattr(service, "media_files_root", ""))).expanduser().resolve() if media_enabled else None
            media_list = bool(getattr(service, "media_files_allow_listing", False))

            if path in ("/", "/index.html"):
                self._send(self._render_ui_index())
            elif path == "/favicon.ico":
                self._send(b"", status=204, content_type="image/x-icon")
            elif path == "/health":
                self._send(
                    json.dumps(
                        {
                            "status": "ok",
                            "service": "embedded-voice-ui",
                            "instance_id": self.server._embedded_instance_id,
                        }
                    ).encode(),
                    content_type="application/json",
                )
            elif path.startswith("/recordings/audio/"):
                audio_name = path.removeprefix("/recordings/audio/")
                resolver = getattr(service, "resolve_recording_audio_path", None)
                if callable(resolver):
                    audio_path = resolver(audio_name)
                    if isinstance(audio_path, Path) and audio_path.exists() and audio_path.is_file():
                        self._send_file(audio_path)
                        return
                self._send(b"Not found", status=404, content_type="text/plain")
            elif workspace_enabled and workspace_root and self._serve_mount(path, "/files/workspace", workspace_root, workspace_list):
                return
            elif media_enabled and media_root and self._serve_mount(path, "/files/media", media_root, media_list):
                return
            else:
                static_target = self._safe_join(static_root, path)
                if static_target and static_target.is_file():
                    self._send_file(static_target)
                else:
                    self._send(b"Not found", status=404, content_type="text/plain")

    class RedirectHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _redirect_target(self) -> str:
            raw_host = self.headers.get("Host", "")
            host = raw_host.split(":", 1)[0].strip() or service.host or "localhost"
            port_suffix = "" if service.ui_port == 443 else f":{service.ui_port}"
            return f"https://{host}{port_suffix}{self.path}"

        def _redirect(self) -> None:
            target = self._redirect_target()
            self.send_response(307)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._redirect()

        def do_HEAD(self) -> None:  # noqa: N802
            self._redirect()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._redirect()

    service._http_server = HTTPServer((service.host, service.ui_port), UIHandler)
    if ssl_context is not None:
        service._http_server.socket = ssl_context.wrap_socket(service._http_server.socket, server_side=True)
    service._http_server._embedded_instance_id = service._instance_id  # type: ignore[attr-defined]
    service._http_thread = threading.Thread(target=service._http_server.serve_forever, daemon=True)
    service._http_thread.start()

    if ssl_context is not None and service.http_redirect_port:
        service._http_redirect_server = HTTPServer((service.host, service.http_redirect_port), RedirectHandler)
        service._http_redirect_thread = threading.Thread(
            target=service._http_redirect_server.serve_forever,
            daemon=True,
        )
        service._http_redirect_thread.start()
