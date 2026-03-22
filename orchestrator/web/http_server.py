from __future__ import annotations

import html as html_utils
import json
import mimetypes
from pathlib import Path
import ssl
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def start_http_servers(service: Any, ssl_context: ssl.SSLContext | None) -> None:
    """Start embedded UI HTTP server and optional HTTP->HTTPS redirector."""

    class UIHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _parse_request_path(self) -> str:
            return unquote(urlsplit(self.path).path or "/")

        def _parse_query_params(self) -> dict[str, list[str]]:
            return parse_qs(urlsplit(self.path).query or "")

        def _request_is_https(self) -> bool:
            return ssl_context is not None

        def _request_host(self) -> str:
            raw_host = str(self.headers.get("Host", "") or "").strip()
            if raw_host:
                return raw_host
            if service.ui_port in (80, 443):
                return str(service.host)
            return f"{service.host}:{service.ui_port}"

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
            auth_bootstrap = service.auth_bootstrap_from_headers(self.headers)
            template_values = {
                "__WS_PORT__": str(service.ws_port),
                "__MIC_STARTS_DISABLED__": "true" if service.mic_starts_disabled else "false",
                "__AUDIO_AUTHORITY__": str(service.audio_authority),
                "__SERVER_INSTANCE_ID__": str(service._instance_id),
                "__AUTH_MODE__": str(auth_bootstrap.get("mode", "disabled")),
                "__AUTHENTICATED__": "true" if auth_bootstrap.get("authenticated") else "false",
                "__AUTH_USER_JSON__": json.dumps(auth_bootstrap.get("user"), separators=(",", ":")),
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

        def _send(
            self,
            body: bytes,
            status: int = 200,
            content_type: str = "text/html; charset=utf-8",
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            for key, value in (extra_headers or []):
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(
            self,
            payload: dict[str, Any],
            status: int = 200,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            self._send(
                json.dumps(payload).encode("utf-8"),
                status=status,
                content_type="application/json",
                extra_headers=extra_headers,
            )

        def _send_redirect(
            self,
            location: str,
            status: int = 303,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            headers = [("Location", location)] + (extra_headers or [])
            self._send(b"", status=status, content_type="text/plain", extra_headers=headers)

        def _is_authenticated(self) -> bool:
            session = service.session_user_from_headers(self.headers)
            return session is not None

        def _handle_auth_get(self, path: str, query: dict[str, list[str]]) -> bool:
            if path == "/auth/session":
                self._send_json(service.auth_bootstrap_from_headers(self.headers))
                return True

            if path == "/auth/google/login":
                if not service.auth_enabled():
                    self._send_json({"error": "web ui auth is disabled"}, status=404)
                    return True
                if not service.oauth_ready():
                    self._send_json({"error": "google oauth not configured"}, status=503)
                    return True
                next_path = str((query.get("next") or ["/"])[0] or "/")
                auth_url = service.begin_google_login(
                    request_host=self._request_host(),
                    request_is_https=self._request_is_https(),
                    next_path=next_path,
                )
                if not auth_url:
                    self._send_json({"error": "google oauth login unavailable"}, status=503)
                    return True
                self._send_redirect(auth_url, status=302)
                return True

            if path == "/auth/google/callback":
                state = str((query.get("state") or [""])[0] or "")
                code = str((query.get("code") or [""])[0] or "")
                error = str((query.get("error") or [""])[0] or "")
                ok, session_id, next_path, err = service.complete_google_login(
                    request_host=self._request_host(),
                    request_is_https=self._request_is_https(),
                    state=state,
                    code=code,
                    error=error,
                )
                if not ok:
                    target = "/?auth_error=" + quote(err or "authentication failed")
                    self._send_redirect(target, status=303)
                    return True

                cookie_value = service.build_session_set_cookie(
                    session_id=session_id,
                    request_is_https=self._request_is_https(),
                )
                self._send_redirect(
                    next_path,
                    status=303,
                    extra_headers=[("Set-Cookie", cookie_value)],
                )
                return True

            if path == "/auth/logout":
                service.logout_from_headers(self.headers)
                cookie_value = service.build_session_clear_cookie(request_is_https=self._request_is_https())
                next_path = service._sanitize_next_path(str((query.get("next") or ["/"])[0] or "/"))
                self._send_redirect(
                    next_path,
                    status=303,
                    extra_headers=[("Set-Cookie", cookie_value)],
                )
                return True

            return False

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(b"", status=204, content_type="text/plain")

        def do_GET(self) -> None:  # noqa: N802
            path = self._parse_request_path()
            query = self._parse_query_params()

            if self._handle_auth_get(path, query):
                return

            static_root = Path(getattr(service, "static_root", Path(__file__).resolve().parent / "static")).resolve()
            workspace_enabled = bool(getattr(service, "workspace_files_enabled", False))
            workspace_root = (
                Path(str(getattr(service, "workspace_files_root", ""))).expanduser().resolve()
                if workspace_enabled
                else None
            )
            workspace_list = bool(getattr(service, "workspace_files_allow_listing", False))
            media_enabled = bool(getattr(service, "media_files_enabled", False))
            media_root = (
                Path(str(getattr(service, "media_files_root", ""))).expanduser().resolve()
                if media_enabled
                else None
            )
            media_list = bool(getattr(service, "media_files_allow_listing", False))

            if service.should_protect_http_path(path) and not self._is_authenticated():
                self._send(b"Authentication required", status=401, content_type="text/plain")
                return

            if path in ("/", "/index.html"):
                self._send(self._render_ui_index())
            elif path == "/favicon.ico":
                self._send(b"", status=204, content_type="image/x-icon")
            elif path == "/health":
                self._send_json(
                    {
                        "status": "ok",
                        "service": "embedded-voice-ui",
                        "instance_id": self.server._embedded_instance_id,
                    }
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
            elif (
                workspace_enabled
                and workspace_root
                and self._serve_mount(path, "/files/workspace", workspace_root, workspace_list)
            ):
                return
            elif media_enabled and media_root and self._serve_mount(path, "/files/media", media_root, media_list):
                return
            else:
                static_target = self._safe_join(static_root, path)
                if static_target and static_target.is_file():
                    self._send_file(static_target)
                else:
                    self._send(b"Not found", status=404, content_type="text/plain")

        def do_POST(self) -> None:  # noqa: N802
            path = self._parse_request_path()
            if path == "/auth/logout":
                service.logout_from_headers(self.headers)
                cookie_value = service.build_session_clear_cookie(request_is_https=self._request_is_https())
                self._send_json(
                    {"ok": True},
                    status=200,
                    extra_headers=[("Set-Cookie", cookie_value)],
                )
                return
            self._send_json({"error": "Not found"}, status=404)

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
