"""Embedded HTTP + WebSocket service for realtime voice UI telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import math
import os
import ssl
import tempfile
import threading
import time
import uuid
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable

import websockets
from orchestrator.web.http_server import start_http_servers

logger = logging.getLogger("orchestrator.web.realtime")


class EmbeddedVoiceWebService:
    """Small embedded HTTP/WebSocket service for realtime UI and audio streaming."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        ui_port: int = 18910,
        ws_port: int = 18911,
        status_hz: int = 12,
        hotword_active_ms: int = 2000,
        mic_starts_disabled: bool = True,
        audio_authority: str = "native",
        chat_history_limit: int = 200,
        ssl_certfile: str = "",
        ssl_keyfile: str = "",
        http_redirect_port: int = 0,
        chat_persist_path: str = "",
        static_root: str = "",
        workspace_files_enabled: bool = False,
        workspace_files_root: str = "",
        workspace_files_allow_listing: bool = False,
        media_files_enabled: bool = False,
        media_files_root: str = "",
        media_files_allow_listing: bool = False,
        openclaw_workspace_root: str = "",
    ):
        self.host = host
        self.ui_port = ui_port
        self.ws_port = ws_port
        self.status_interval_s = 1.0 / max(1, status_hz)
        self.hotword_active_s = max(0.1, hotword_active_ms / 1000.0)
        self.mic_starts_disabled = mic_starts_disabled
        self.audio_authority = audio_authority
        self.chat_history_limit = max(20, chat_history_limit)
        self.ssl_certfile = ssl_certfile
        self.ssl_keyfile = ssl_keyfile
        self.http_redirect_port = http_redirect_port
        self._instance_id = uuid.uuid4().hex

        default_static_root = Path(__file__).resolve().parent / "static"
        self.static_root = str(Path(static_root).expanduser().resolve()) if static_root else str(default_static_root)

        workspace_root = (
            Path(workspace_files_root).expanduser()
            if workspace_files_root
            else Path(openclaw_workspace_root).expanduser() if openclaw_workspace_root else Path.cwd()
        )
        self.workspace_files_enabled = bool(workspace_files_enabled)
        self.workspace_files_root = str(workspace_root.resolve())
        self.workspace_files_allow_listing = bool(workspace_files_allow_listing)

        media_root = Path(media_files_root).expanduser() if media_files_root else Path("/music")
        self.media_files_enabled = bool(media_files_enabled)
        self.media_files_root = str(media_root.resolve())
        self.media_files_allow_listing = bool(media_files_allow_listing)

        self._http_server: HTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._http_redirect_server: HTTPServer | None = None
        self._http_redirect_thread: threading.Thread | None = None
        self._ws_server: Any = None
        self._status_task: asyncio.Task | None = None
        self._ssl_context: ssl.SSLContext | None = None

        self._clients: set[Any] = set()
        self._active_client: Any | None = None
        self._latest_browser_audio: dict[str, float] = {"rms": 0.0, "peak": 0.0}
        self._browser_pcm_frames: deque[bytes] = deque(maxlen=400)
        self._last_browser_pcm_ts: float | None = None
        self._browser_pcm_packet_count: int = 0
        self._browser_pcm_packet_bytes: int = 0
        self._browser_level_packet_count: int = 0
        self._last_audio_packet_log_ts: float = 0.0
        self._last_hotword_ts: float | None = None

        self._orchestrator_status: dict[str, Any] = {
            "voice_state": "idle",
            "wake_state": "asleep",
            "speech_active": False,
            "tts_playing": False,
            "mic_rms": 0.0,
            "queue_depth": 0,
        }
        self._status_rev: int = 0

        self._chat_messages: list[dict[str, Any]] = []
        self._chat_seq: int = 0
        self._chat_threads: list[dict[str, Any]] = []
        self._active_chat_id: str = "active"
        self._active_chat_thread_id: str | None = None
        self._chat_thread_limit = 100
        _default_persist = Path.home() / ".config" / "openclaw" / "chat_state.json"
        self._chat_persist_path = Path(chat_persist_path) if chat_persist_path else _default_persist
        self._load_chat_state()
        self._music_state: dict[str, Any] = {
            "state": "stop", "title": "", "artist": "", "album": "",
            "queue_length": 0, "elapsed": 0.0, "duration": 0.0, "position": -1,
        }
        self._music_queue: list[dict[str, Any]] = []
        self._music_playlists_cache: list[str] = []
        self._music_rev: int = 0
        self._music_state_push_task: asyncio.Task | None = None
        self._timers_state: list[dict[str, Any]] = []
        self._timers_rev: int = 0
        self._ui_control_state: dict[str, Any] = {
            "mic_enabled": not mic_starts_disabled,
            "tts_muted": False,
            "browser_audio_enabled": True,
            "continuous_mode": False,
        }
        self._ui_control_rev: int = 0

        self._on_mic_toggle: Callable[[str], Awaitable[None]] | None = None
        self._on_music_toggle: Callable[[str], Awaitable[None]] | None = None
        self._on_music_stop: Callable[[str], Awaitable[None]] | None = None
        self._on_music_play_track: Callable[[int, str], Awaitable[None]] | None = None
        self._on_music_seek: Callable[[float, str], Awaitable[None]] | None = None
        self._on_music_remove_selected: Callable[[list[int], str, list[str] | None], Awaitable[None]] | None = None
        self._on_music_add_files: Callable[[list[str], str], Awaitable[None]] | None = None
        self._on_music_create_playlist: Callable[[str, list[int], str], Awaitable[None]] | None = None
        self._on_music_load_playlist: Callable[[str, str], Awaitable[None]] | None = None
        self._on_music_save_playlist: Callable[[str, str], Awaitable[None]] | None = None
        self._on_music_delete_playlist: Callable[[str, str], Awaitable[None]] | None = None
        self._on_music_search_library: Callable[[str, str], Awaitable[list[dict[str, Any]]]] | None = None
        self._on_music_list_playlists: Callable[[str], Awaitable[list[str]]] | None = None
        self._on_get_music_state: Callable[[], Awaitable[tuple[dict[str, Any], list[dict[str, Any]]]]] | None = None
        self._on_timer_cancel: Callable[[str, str], Awaitable[None]] | None = None
        self._on_alarm_cancel: Callable[[str, str], Awaitable[None]] | None = None
        self._on_chat_new: Callable[[str], Awaitable[None]] | None = None
        self._on_chat_text: Callable[[str, str], Awaitable[None]] | None = None
        self._on_tts_mute_set: Callable[[bool, str], Awaitable[None]] | None = None
        self._on_browser_audio_set: Callable[[bool, str], Awaitable[None]] | None = None
        self._on_continuous_mode_set: Callable[[bool, str], Awaitable[None]] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _tls_enabled(self) -> bool:
        return bool(self.ssl_certfile and self.ssl_keyfile)

    def _ensure_ssl_context(self) -> ssl.SSLContext | None:
        if not self._tls_enabled():
            return None
        if self._ssl_context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.ssl_certfile, self.ssl_keyfile)
            self._ssl_context = context
        return self._ssl_context

    async def start(self) -> None:
        ssl_context = self._ensure_ssl_context()
        self._start_http_server()
        self._ws_server = await websockets.serve(
            self._ws_handler,
            self.host,
            self.ws_port,
            ssl=ssl_context,
        )
        self._status_task = asyncio.create_task(self._status_loop())
        ui_scheme = "https" if ssl_context else "http"
        ws_scheme = "wss" if ssl_context else "ws"
        logger.info(
            "Embedded web UI started: %s://%s:%d (%s://%s:%d)",
            ui_scheme,
            self.host,
            self.ui_port,
            ws_scheme,
            self.host,
            self.ws_port,
        )
        if ssl_context and self.http_redirect_port:
            logger.info(
                "Embedded web UI HTTP redirector started: http://%s:%d -> https://%s:%d",
                self.host,
                self.http_redirect_port,
                self.host,
                self.ui_port,
            )

    async def stop(self) -> None:
        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
            self._status_task = None

        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None

        if self._http_redirect_server is not None:
            self._http_redirect_server.shutdown()
            self._http_redirect_server.server_close()
            self._http_redirect_server = None

        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=1.0)
        self._http_thread = None
        if self._http_redirect_thread and self._http_redirect_thread.is_alive():
            self._http_redirect_thread.join(timeout=1.0)
        self._http_redirect_thread = None
        self._clients.clear()

    # ------------------------------------------------------------------
    # State update helpers (called from main.py)
    # ------------------------------------------------------------------

    def update_orchestrator_status(self, **status: Any) -> None:
        self._orchestrator_status.update(status)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running() and self._clients:
                asyncio.ensure_future(self.broadcast(self._build_status_payload()))
        except RuntimeError:
            pass

    def note_hotword_detected(self) -> None:
        self._last_hotword_ts = time.monotonic()

    def _load_chat_state(self) -> None:
        """Load persisted chat threads and active messages from disk (best-effort)."""
        try:
            if not self._chat_persist_path.exists():
                return
            raw = self._chat_persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            threads = data.get("threads")
            if isinstance(threads, list):
                self._chat_threads = threads[: self._chat_thread_limit]
            active = data.get("active_messages")
            if isinstance(active, list):
                self._chat_messages = active[-self.chat_history_limit:]
            seq = data.get("chat_seq")
            if isinstance(seq, int):
                self._chat_seq = seq
            active_thread_id = data.get("active_thread_id")
            if isinstance(active_thread_id, str) and active_thread_id.strip():
                self._active_chat_thread_id = active_thread_id.strip()
            if self._active_chat_thread_id and not any(
                str(t.get("id", "")) == self._active_chat_thread_id for t in self._chat_threads
            ):
                self._active_chat_thread_id = None
            logger.info(
                "Loaded %d chat thread(s) and %d active message(s) from %s",
                len(self._chat_threads),
                len(self._chat_messages),
                self._chat_persist_path,
            )
        except Exception:
            logger.debug("Could not load chat state from disk (will start fresh)", exc_info=True)

    def _persist_chat_state(self) -> None:
        """Atomically write chat threads and active messages to disk (best-effort)."""
        try:
            self._chat_persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "threads": self._chat_threads,
                "active_messages": self._chat_messages,
                "active_thread_id": self._active_chat_thread_id,
                "chat_seq": self._chat_seq,
                "saved_ts": time.time(),
            }
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=self._chat_persist_path.parent,
                prefix=".chat_state_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                os.replace(tmp_path, self._chat_persist_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception:
            logger.debug("Could not persist chat state to disk", exc_info=True)

    def update_chat_history(self, messages: list[dict[str, Any]]) -> None:
        self._chat_messages = list(messages[-self.chat_history_limit:])
        self._upsert_active_chat_thread()
        self._persist_chat_state()

    def _derive_chat_title(self, messages: list[dict[str, Any]]) -> str:
        for m in messages:
            if str(m.get("role", "")).lower() == "user":
                raw = str(m.get("text", "")).strip()
                if raw:
                    return raw[:72]
        for m in messages:
            raw = str(m.get("text", "")).strip()
            if raw:
                return raw[:72]
        return f"Chat {len(self._chat_threads) + 1}"

    def _upsert_active_chat_thread(self) -> None:
        if not self._chat_messages:
            return
        now = time.time()
        thread_id = self._active_chat_thread_id or uuid.uuid4().hex[:12]
        self._active_chat_thread_id = thread_id
        existing_index = next(
            (i for i, t in enumerate(self._chat_threads) if str(t.get("id", "")) == thread_id),
            -1,
        )
        created_ts = now
        if existing_index >= 0:
            existing = self._chat_threads.pop(existing_index)
            try:
                created_ts = float(existing.get("created_ts", now))
            except Exception:
                created_ts = now
        thread = {
            "id": thread_id,
            "title": self._derive_chat_title(self._chat_messages),
            "messages": list(self._chat_messages),
            "created_ts": created_ts,
            "updated_ts": now,
        }
        self._chat_threads.insert(0, thread)
        if len(self._chat_threads) > self._chat_thread_limit:
            self._chat_threads = self._chat_threads[: self._chat_thread_limit]

    def _archive_active_chat_if_needed(self) -> None:
        if not self._chat_messages:
            return
        now = time.time()
        thread_id = self._active_chat_thread_id or uuid.uuid4().hex[:12]
        existing_index = next(
            (i for i, t in enumerate(self._chat_threads) if str(t.get("id", "")) == thread_id),
            -1,
        )
        created_ts = now
        if existing_index >= 0:
            existing = self._chat_threads.pop(existing_index)
            try:
                created_ts = float(existing.get("created_ts", now))
            except Exception:
                created_ts = now
        archived = {
            "id": thread_id,
            "title": self._derive_chat_title(self._chat_messages),
            "messages": list(self._chat_messages),
            "created_ts": created_ts,
            "updated_ts": now,
        }
        self._chat_threads.insert(0, archived)
        if len(self._chat_threads) > self._chat_thread_limit:
            self._chat_threads = self._chat_threads[: self._chat_thread_limit]
        self._persist_chat_state()

    def start_new_chat(self) -> None:
        self._archive_active_chat_if_needed()
        self._chat_messages = []
        self._active_chat_id = "active"
        self._active_chat_thread_id = None
        self._persist_chat_state()
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "chat_reset",
                    "active_chat_id": self._active_chat_id,
                    "chat": [],
                    "chat_threads": list(self._chat_threads),
                }
            )
        )

    def delete_chat_thread(self, thread_id: str) -> bool:
        tid = str(thread_id or "").strip()
        if not tid or tid == "active":
            return False
        before = len(self._chat_threads)
        self._chat_threads = [t for t in self._chat_threads if str(t.get("id", "")) != tid]
        if len(self._chat_threads) == before:
            return False
        if self._active_chat_thread_id == tid:
            self._active_chat_thread_id = None
        self._persist_chat_state()
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "chat_threads_update",
                    "active_chat_id": self._active_chat_id,
                    "chat_threads": list(self._chat_threads),
                }
            )
        )
        return True

    def append_chat_message(self, message: dict[str, Any]) -> None:
        self._chat_seq += 1
        msg = dict(message)
        msg.setdefault("id", self._chat_seq)
        msg.setdefault("ts", time.time())
        self._chat_messages.append(msg)
        if len(self._chat_messages) > self.chat_history_limit:
            self._chat_messages = self._chat_messages[-self.chat_history_limit:]
        self._upsert_active_chat_thread()
        self._persist_chat_state()
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "chat_append",
                    "message": msg,
                    "chat_threads": list(self._chat_threads),
                    "active_chat_id": self._active_chat_id,
                }
            )
        )

    def update_or_append_chat_message(self, message: dict[str, Any]) -> None:
        """
        Append a user message, or replace the last user message if it's a prefix extension.
        
        This handles the case where high background noise causes multiple audio captures,
        each extending the previous transcript. Instead of creating multiple user messages
        like "Can you help me?" → "Can you help me? to find out" → "Can you help me? to find out
        What the time is?", this merges them into a single evolving message.
        """
        # Only apply this logic to user messages from voice source
        if message.get("role") != "user" or message.get("source") != "voice":
            self.append_chat_message(message)
            return
        
        new_text = (message.get("text") or "").strip()
        if not new_text:
            self.append_chat_message(message)
            return
        
        # Check if last message is a user message from voice
        if (
            self._chat_messages
            and self._chat_messages[-1].get("role") == "user"
            and self._chat_messages[-1].get("source") == "voice"
        ):
            last_msg = self._chat_messages[-1]
            last_text = (last_msg.get("text") or "").strip()
            
            # Check if new text is a prefix extension of last message (case-insensitive)
            if (
                last_text
                and new_text.lower().startswith(last_text.lower())
                and new_text != last_text
            ):
                # Replace the last message with the extended text
                updated_msg = dict(last_msg)
                updated_msg["text"] = new_text
                updated_msg["ts"] = time.time()  # Update timestamp
                self._chat_messages[-1] = updated_msg
                self._upsert_active_chat_thread()
                self._persist_chat_state()
                asyncio.create_task(
                    self.broadcast(
                        {
                            "type": "chat_update",
                            "message": updated_msg,
                            "chat_threads": list(self._chat_threads),
                            "active_chat_id": self._active_chat_id,
                        }
                    )
                )
                return
        
        # Otherwise, append as a new message
        self.append_chat_message(message)

    def update_music_transport(self, **state: Any) -> None:
        self._music_state.update(state)
        self._music_rev += 1
        payload: dict[str, Any] = {
            "type": "music_transport",
            "music_rev": self._music_rev,
            "music": dict(self._music_state),
        }
        asyncio.create_task(self.broadcast(payload))

    def update_music_queue(
        self,
        queue: list[dict[str, Any]],
        *,
        trace_id: str = "",
        voice_load_complete_ts: float | None = None,
        sync_start_ts: float | None = None,
    ) -> None:
        self._music_queue = list(queue)
        self._music_rev += 1
        payload: dict[str, Any] = {
            "type": "music_queue",
            "music_rev": self._music_rev,
            "queue": list(self._music_queue),
        }
        async def _broadcast_queue_payload() -> None:
            await self.broadcast(payload)
            if trace_id:
                now = time.monotonic()
                since_voice_ms = (
                    (now - float(voice_load_complete_ts)) * 1000
                    if voice_load_complete_ts is not None
                    else None
                )
                since_sync_ms = (
                    (now - float(sync_start_ts)) * 1000
                    if sync_start_ts is not None
                    else None
                )
                logger.info(
                    "🧭 Voice playlist trace %s: first music_queue broadcast sent (queue=%d, since_voice_complete_ms=%s, since_sync_start_ms=%s)",
                    trace_id,
                    len(self._music_queue),
                    f"{since_voice_ms:.1f}" if since_voice_ms is not None else "n/a",
                    f"{since_sync_ms:.1f}" if since_sync_ms is not None else "n/a",
                )

        asyncio.create_task(_broadcast_queue_payload())

    def update_music_playlists(self, playlists: list[str]) -> None:
        playlist_names = [str(name).strip() for name in playlists if str(name).strip()]
        self._music_playlists_cache = playlist_names
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "music_playlists",
                    "playlists": list(self._music_playlists_cache),
                }
            )
        )

    def update_music_state(self, queue: list[dict[str, Any]] | None = None, **state: Any) -> None:
        self._music_state.update(state)
        if queue is not None:
            self._music_queue = list(queue)
        self._music_rev += 1
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "music_state",
                    "music_rev": self._music_rev,
                    "music": dict(self._music_state),
                    "queue": list(self._music_queue),
                }
            )
        )

    async def push_music_state_now(self, queue: list[dict[str, Any]] | None = None, **state: Any) -> None:
        """Like update_music_state but awaits the broadcast to guarantee clients receive state before any subsequent ack."""
        self._music_state.update(state)
        if queue is not None:
            self._music_queue = list(queue)
        self._music_rev += 1
        await self.broadcast(
            {
                "type": "music_state",
                "music_rev": self._music_rev,
                "music": dict(self._music_state),
                "queue": list(self._music_queue),
            }
        )

    def update_timers_state(self, timers: list[dict[str, Any]]) -> None:
        self._timers_state = list(timers)
        self._timers_rev += 1
        asyncio.create_task(self.broadcast({
            "type": "timers_state",
            "timers_rev": self._timers_rev,
            "timers": self._timers_state,
        }))

    def update_ui_control_state(self, **state: Any) -> None:
        self._ui_control_state.update(state)
        self._ui_control_rev += 1
        asyncio.create_task(self.broadcast({
            "type": "ui_control",
            "ui_control_rev": self._ui_control_rev,
            **self._ui_control_state,
        }))

    def navigate_ui_page(self, page: str) -> None:
        page_name = str(page or "").strip().lower()
        if page_name not in ("home", "music"):
            return
        asyncio.create_task(self.broadcast({
            "type": "navigate",
            "page": page_name,
        }))

    def has_active_client(self) -> bool:
        return self._active_client is not None and self._active_client in self._clients

    def has_recent_browser_audio(self, max_age_s: float = 1.0) -> bool:
        if not self.has_active_client():
            return False
        if self._last_browser_pcm_ts is None:
            return False
        return (time.monotonic() - self._last_browser_pcm_ts) <= max(0.05, float(max_age_s))

    async def read_browser_frame(self, timeout: float = 0.0) -> bytes | None:
        if self._browser_pcm_frames:
            return self._browser_pcm_frames.popleft()
        if timeout <= 0:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._browser_pcm_frames:
                return self._browser_pcm_frames.popleft()
            await asyncio.sleep(0.005)
        return None

    def latest_browser_audio(self) -> dict[str, float]:
        return dict(self._latest_browser_audio)

    # ------------------------------------------------------------------
    # Action handler registration
    # ------------------------------------------------------------------

    def set_action_handlers(
        self,
        on_mic_toggle: Callable[[str], Awaitable[None]] | None = None,
        on_music_toggle: Callable[[str], Awaitable[None]] | None = None,
        on_music_stop: Callable[[str], Awaitable[None]] | None = None,
        on_music_play_track: Callable[[int, str], Awaitable[None]] | None = None,
        on_music_seek: Callable[[float, str], Awaitable[None]] | None = None,
        on_music_clear_queue: Callable[[str], Awaitable[None]] | None = None,
        on_music_remove_selected: Callable[[list[int], str, list[str] | None], Awaitable[None]] | None = None,
        on_music_add_files: Callable[[list[str], str], Awaitable[None]] | None = None,
        on_music_create_playlist: Callable[[str, list[int], str], Awaitable[None]] | None = None,
        on_music_load_playlist: Callable[[str, str], Awaitable[None]] | None = None,
        on_music_save_playlist: Callable[[str, str], Awaitable[None]] | None = None,
        on_music_delete_playlist: Callable[[str, str], Awaitable[None]] | None = None,
        on_music_search_library: Callable[[str, str], Awaitable[list[dict[str, Any]]]] | None = None,
        on_music_list_playlists: Callable[[str], Awaitable[list[str]]] | None = None,
        on_get_music_state: Callable[[], Awaitable[tuple[dict[str, Any], list[dict[str, Any]]]]] | None = None,
        on_timer_cancel: Callable[[str, str], Awaitable[None]] | None = None,
        on_alarm_cancel: Callable[[str, str], Awaitable[None]] | None = None,
        on_chat_new: Callable[[str], Awaitable[None]] | None = None,
        on_chat_text: Callable[[str, str], Awaitable[None]] | None = None,
        on_tts_mute_set: Callable[[bool, str], Awaitable[None]] | None = None,
        on_browser_audio_set: Callable[[bool, str], Awaitable[None]] | None = None,
        on_continuous_mode_set: Callable[[bool, str], Awaitable[None]] | None = None,
    ) -> None:
        if on_mic_toggle is not None:
            self._on_mic_toggle = on_mic_toggle
        if on_music_toggle is not None:
            self._on_music_toggle = on_music_toggle
        if on_music_stop is not None:
            self._on_music_stop = on_music_stop
        if on_music_play_track is not None:
            self._on_music_play_track = on_music_play_track
        if on_music_seek is not None:
            self._on_music_seek = on_music_seek
        if on_music_clear_queue is not None:
            self._on_music_clear_queue = on_music_clear_queue
        if on_music_remove_selected is not None:
            self._on_music_remove_selected = on_music_remove_selected
        if on_music_add_files is not None:
            self._on_music_add_files = on_music_add_files
        if on_music_create_playlist is not None:
            self._on_music_create_playlist = on_music_create_playlist
        if on_music_load_playlist is not None:
            self._on_music_load_playlist = on_music_load_playlist
        if on_music_save_playlist is not None:
            self._on_music_save_playlist = on_music_save_playlist
        if on_music_delete_playlist is not None:
            self._on_music_delete_playlist = on_music_delete_playlist
        if on_music_search_library is not None:
            self._on_music_search_library = on_music_search_library
        if on_music_list_playlists is not None:
            self._on_music_list_playlists = on_music_list_playlists
        if on_get_music_state is not None:
            self._on_get_music_state = on_get_music_state
        if on_timer_cancel is not None:
            self._on_timer_cancel = on_timer_cancel
        if on_alarm_cancel is not None:
            self._on_alarm_cancel = on_alarm_cancel
        if on_chat_new is not None:
            self._on_chat_new = on_chat_new
        if on_chat_text is not None:
            self._on_chat_text = on_chat_text
        if on_tts_mute_set is not None:
            self._on_tts_mute_set = on_tts_mute_set
        if on_browser_audio_set is not None:
            self._on_browser_audio_set = on_browser_audio_set
        if on_continuous_mode_set is not None:
            self._on_continuous_mode_set = on_continuous_mode_set

    # ------------------------------------------------------------------
    # Feedback sound helper
    # ------------------------------------------------------------------

    def send_feedback_sound(self, wav_bytes: bytes, gain: float = 1.0) -> None:
        """Broadcast a short feedback sound to all browser clients as base64-encoded WAV."""
        import base64
        asyncio.create_task(self.broadcast({
            "type": "feedback_sound",
            "audio_b64": base64.b64encode(wav_bytes).decode(),
            "gain": float(gain),
        }))

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload)
        stale: list[Any] = []
        for client in list(self._clients):
            try:
                await client.send(message)
            except Exception:
                stale.append(client)
        for c in stale:
            self._clients.discard(c)

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket: Any) -> None:
        client_id = uuid.uuid4().hex[:8]

        self._clients.add(websocket)
        self._active_client = websocket
        logger.info("Web UI client connected (%s); clients=%d", client_id, len(self._clients))
        try:
            await websocket.send(json.dumps({
                "type": "hello",
                "client_id": client_id,
                "ws_port": self.ws_port,
                "ui_port": self.ui_port,
            }))
            # Fetch fresh music state if cache is empty (ensures page load shows current MPD state)
            if self._on_get_music_state and (not self._music_state or not self._music_queue):
                try:
                    transport, queue = await self._on_get_music_state()
                    self._music_state.update(transport)
                    self._music_queue = list(queue)
                    self._music_rev += 1
                except Exception:
                    pass
            await websocket.send(json.dumps(self._build_state_snapshot()))
            if self._on_music_list_playlists is not None:
                try:
                    names = await self._on_music_list_playlists(client_id)
                    playlist_names = [str(n) for n in (names or []) if str(n).strip()]
                    if playlist_names:
                        self._music_playlists_cache = playlist_names
                    elif self._music_playlists_cache:
                        playlist_names = list(self._music_playlists_cache)
                    logger.info("Sent playlists on connect to %s: count=%d", client_id, len(playlist_names))
                    await websocket.send(json.dumps({
                        "type": "music_playlists",
                        "playlists": playlist_names,
                    }))
                except Exception:
                    pass
            async for message in websocket:
                if isinstance(message, str):
                    asyncio.create_task(self._handle_text_action(message, client_id, websocket))
                elif isinstance(message, (bytes, bytearray)):
                    self._handle_pcm_chunk(bytes(message))
        except Exception as exc:
            logger.debug("Web UI client %s disconnected: %s", client_id, exc)
        finally:
            self._clients.discard(websocket)
            if self._active_client is websocket:
                self._active_client = next(iter(self._clients), None)
            self._browser_pcm_frames.clear()
            logger.info("Web UI client disconnected (%s); clients=%d", client_id, len(self._clients))

    # ------------------------------------------------------------------
    # Incoming action dispatch
    # ------------------------------------------------------------------

    async def _handle_text_action(self, message: str, client_id: str, websocket: Any | None = None) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        msg_type = payload.get("type", "")
        if isinstance(msg_type, str) and msg_type.startswith("music_"):
            logger.info("Web UI music action received (%s) from %s", msg_type, client_id)

        if msg_type == "browser_audio_level":
            try:
                self._latest_browser_audio["rms"] = float(payload.get("rms", 0.0))
                self._latest_browser_audio["peak"] = float(payload.get("peak", 0.0))
                self._browser_level_packet_count += 1
                now = time.monotonic()
                if now - self._last_audio_packet_log_ts >= 2.0:
                    logger.info(
                        "📦 Audio packet source summary: browser_audio_level=%d browser_pcm=%d (%d bytes queued=%d)",
                        self._browser_level_packet_count,
                        self._browser_pcm_packet_count,
                        self._browser_pcm_packet_bytes,
                        len(self._browser_pcm_frames),
                    )
                    self._browser_level_packet_count = 0
                    self._browser_pcm_packet_count = 0
                    self._browser_pcm_packet_bytes = 0
                    self._last_audio_packet_log_ts = now
            except Exception:
                pass
            return

        if msg_type == "browser_capture_error":
            try:
                logger.warning(
                    "Browser capture error [%s] from %s: %s | msg=%s | secure=%s | mediaDevices=%s | audioInputs=%s | labels=%s",
                    payload.get("phase", "capture"),
                    client_id,
                    payload.get("name", ""),
                    payload.get("message", ""),
                    payload.get("secure_context", None),
                    payload.get("has_media_devices", None),
                    payload.get("audio_input_count", None),
                    payload.get("audio_input_labels", []),
                )
            except Exception:
                pass
            return

        if msg_type in ("ui_ready", "navigate"):
            return

        if msg_type == "mic_toggle" and self._on_mic_toggle:
            try:
                await self._on_mic_toggle(client_id)
            except Exception as exc:
                logger.warning("mic_toggle handler error: %s", exc)
            return

        async def _send_ws_json(payload_dict: dict[str, Any]) -> bool:
            if websocket is None:
                return False
            try:
                await websocket.send(json.dumps(payload_dict))
                return True
            except Exception as exc:
                logger.debug("Web UI socket send failed (%s): %s", client_id, exc)
                self._clients.discard(websocket)
                if self._active_client is websocket:
                    self._active_client = next(iter(self._clients), None)
                return False

        async def _send_music_action_ack(action: str, action_id: Any) -> None:
            if not action_id:
                return
            await _send_ws_json(
                {
                    "type": "music_action_ack",
                    "action": action,
                    "action_id": str(action_id),
                }
            )

        async def _send_music_playlists_update() -> None:
            if self._on_music_list_playlists is None:
                return
            try:
                names = await self._on_music_list_playlists(client_id)
                await _send_ws_json(
                    {
                        "type": "music_playlists",
                        "playlists": names or [],
                    }
                )
            except Exception:
                pass

        async def _push_music_state_best_effort() -> None:
            if self._on_get_music_state is None:
                return
            for attempt in (1, 2, 3):
                try:
                    transport, queue = await self._on_get_music_state()
                    await self.push_music_state_now(queue=queue, **transport)
                    return
                except Exception as exc:
                    if attempt == 3:
                        logger.warning("music state push failed after retries: %s", exc)
                        return
                    await asyncio.sleep(0.1 * attempt)

        def _schedule_music_state_push(reason: str) -> None:
            current_task = self._music_state_push_task
            if current_task is not None and not current_task.done():
                logger.debug(
                    "Skipping duplicate music state push (%s); refresh already running",
                    reason,
                )
                return

            async def _runner() -> None:
                try:
                    await _push_music_state_best_effort()
                finally:
                    self._music_state_push_task = None

            self._music_state_push_task = asyncio.create_task(_runner())

        if msg_type == "music_get_state" and self._on_get_music_state:
            _schedule_music_state_push("music_get_state")
            return

        if msg_type == "music_toggle" and self._on_music_toggle:
            action_id = payload.get("action_id")
            try:
                await self._on_music_toggle(client_id)
                await _send_music_action_ack("music_toggle", action_id)
                _schedule_music_state_push("music_toggle")
            except Exception as exc:
                logger.warning("music_toggle handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_toggle",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_stop" and self._on_music_stop:
            action_id = payload.get("action_id")
            try:
                await self._on_music_stop(client_id)
                await _send_music_action_ack("music_stop", action_id)
                _schedule_music_state_push("music_stop")
            except Exception as exc:
                logger.warning("music_stop handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_stop",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_play_track" and self._on_music_play_track:
            action_id = payload.get("action_id")
            pos = payload.get("position")
            if pos is not None:
                try:
                    await self._on_music_play_track(int(pos), client_id)
                    await _send_music_action_ack("music_play_track", action_id)
                    _schedule_music_state_push("music_play_track")
                except Exception as exc:
                    logger.warning("music_play_track handler error: %s", exc)
                    if action_id:
                        await _send_ws_json({
                            "type": "music_action_error",
                            "action": "music_play_track",
                            "action_id": str(action_id),
                            "error": str(exc),
                        })
            return

        if msg_type == "music_seek" and self._on_music_seek:
            action_id = payload.get("action_id")
            seconds = payload.get("seconds")
            if seconds is not None:
                try:
                    await self._on_music_seek(float(seconds), client_id)
                    await _send_music_action_ack("music_seek", action_id)
                    _schedule_music_state_push("music_seek")
                except Exception as exc:
                    logger.warning("music_seek handler error: %s", exc)
                    if action_id:
                        await _send_ws_json({
                            "type": "music_action_error",
                            "action": "music_seek",
                            "action_id": str(action_id),
                            "error": str(exc),
                        })
            return

        if msg_type == "music_clear_queue" and self._on_music_clear_queue:
            action_id = payload.get("action_id")
            try:
                await _send_music_action_ack("music_clear_queue", action_id)
                await self._on_music_clear_queue(client_id)
                _schedule_music_state_push("music_clear_queue")
            except Exception as exc:
                logger.warning("music_clear_queue handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_clear_queue",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_remove_selected" and self._on_music_remove_selected:
            action_id = payload.get("action_id")
            positions = payload.get("positions")
            song_ids = payload.get("song_ids")
            try:
                pos_list = [int(p) for p in positions] if isinstance(positions, list) else []
                song_id_list = [str(s).strip() for s in song_ids] if isinstance(song_ids, list) else []
                await _send_music_action_ack("music_remove_selected", action_id)
                await self._on_music_remove_selected(pos_list, client_id, song_id_list or None)
            except Exception as exc:
                logger.warning("music_remove_selected handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_remove_selected",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_add_files" and self._on_music_add_files:
            action_id = payload.get("action_id")
            files = payload.get("files")
            try:
                file_list = [str(f) for f in files] if isinstance(files, list) else []
                await self._on_music_add_files(file_list, client_id)
                await _send_music_action_ack("music_add_files", action_id)
            except Exception as exc:
                logger.warning("music_add_files handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_add_files",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_create_playlist" and self._on_music_create_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            positions = payload.get("positions")
            try:
                pos_list = [int(p) for p in positions] if isinstance(positions, list) else []
                if name:
                    await _send_music_action_ack("music_create_playlist", action_id)
                    await self._on_music_create_playlist(name, pos_list, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_create_playlist handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_create_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_load_playlist" and self._on_music_load_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            try:
                if name:
                    await self._on_music_load_playlist(name, client_id)
                    await _send_music_action_ack("music_load_playlist", action_id)

                    # Push updated state/queue asynchronously to avoid blocking ACK timeout.
                    # Large playlists can take longer to materialize on MPD; retries handled
                    # inside _push_music_state_best_effort.
                    _schedule_music_state_push("music_load_playlist")
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_load_playlist handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_load_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_save_playlist" and self._on_music_save_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            try:
                if name:
                    await _send_music_action_ack("music_save_playlist", action_id)
                    await self._on_music_save_playlist(name, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_save_playlist handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_save_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_delete_playlist" and self._on_music_delete_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            try:
                if name:
                    await _send_music_action_ack("music_delete_playlist", action_id)
                    await self._on_music_delete_playlist(name, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_delete_playlist handler error: %s", exc)
                if action_id:
                    await _send_ws_json({
                        "type": "music_action_error",
                        "action": "music_delete_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    })
            return

        if msg_type == "music_search_library" and self._on_music_search_library:
            query = str(payload.get("query", "")).strip()
            try:
                limit = int(payload.get("limit", 200))
            except Exception:
                limit = 200
            limit = max(1, min(2000, limit))
            try:
                started = time.monotonic()
                # Keep UI responsive: return results or timeout quickly.
                rows = await asyncio.wait_for(
                    self._on_music_search_library(query, limit, client_id),
                    timeout=4.0,
                )
                elapsed_ms = (time.monotonic() - started) * 1000
                logger.info(
                    "music_search_library handled query='%s' limit=%d rows=%d in %.1fms",
                    query,
                    limit,
                    len(rows or []),
                    elapsed_ms,
                )
                if websocket is not None:
                    await websocket.send(json.dumps({
                        "type": "music_library_results",
                        "query": query,
                        "results": rows or [],
                    }))
            except asyncio.TimeoutError:
                logger.warning("music_search_library timed out query='%s' limit=%d", query, limit)
                if websocket is not None:
                    await websocket.send(json.dumps({
                        "type": "music_library_results",
                        "query": query,
                        "results": [],
                        "error": "search timeout",
                    }))
            except Exception as exc:
                logger.warning("music_search_library handler error: %s", exc)
                if websocket is not None:
                    await websocket.send(json.dumps({
                        "type": "music_library_results",
                        "query": query,
                        "results": [],
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_list_playlists" and self._on_music_list_playlists:
            try:
                names = await self._on_music_list_playlists(client_id)
                playlist_names = [str(n) for n in (names or []) if str(n).strip()]
                if playlist_names:
                    self._music_playlists_cache = playlist_names
                elif self._music_playlists_cache:
                    playlist_names = list(self._music_playlists_cache)
                logger.info("Handled music_list_playlists for %s: count=%d", client_id, len(playlist_names))
                if websocket is not None:
                    await websocket.send(json.dumps({
                        "type": "music_playlists",
                        "playlists": playlist_names,
                    }))
            except Exception as exc:
                logger.warning("music_list_playlists handler error: %s", exc)
            return

        if msg_type == "timer_cancel" and self._on_timer_cancel:
            action_id = payload.get("action_id")
            timer_id = payload.get("timer_id", "")
            if timer_id:
                try:
                    await self._on_timer_cancel(str(timer_id), client_id)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_ack",
                            "action": "timer_cancel",
                            "action_id": str(action_id),
                            "id": str(timer_id),
                        }))
                except Exception as exc:
                    logger.warning("timer_cancel handler error: %s", exc)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_error",
                            "action": "timer_cancel",
                            "action_id": str(action_id),
                            "id": str(timer_id),
                            "error": str(exc),
                        }))
            return

        if msg_type == "alarm_cancel" and self._on_alarm_cancel:
            action_id = payload.get("action_id")
            alarm_id = payload.get("alarm_id", "")
            if alarm_id:
                try:
                    await self._on_alarm_cancel(str(alarm_id), client_id)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_ack",
                            "action": "alarm_cancel",
                            "action_id": str(action_id),
                            "id": str(alarm_id),
                        }))
                except Exception as exc:
                    logger.warning("alarm_cancel handler error: %s", exc)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_error",
                            "action": "alarm_cancel",
                            "action_id": str(action_id),
                            "id": str(alarm_id),
                            "error": str(exc),
                        }))
            return

        if msg_type == "tts_mute_set" and self._on_tts_mute_set:
            action_id = payload.get("action_id")
            enabled = bool(payload.get("enabled", False))
            try:
                await self._on_tts_mute_set(enabled, client_id)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_ack",
                        "action": "tts_mute_set",
                        "action_id": str(action_id),
                    }))
            except Exception as exc:
                logger.warning("tts_mute_set handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_error",
                        "action": "tts_mute_set",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "browser_audio_set" and self._on_browser_audio_set:
            action_id = payload.get("action_id")
            enabled = bool(payload.get("enabled", True))
            try:
                await self._on_browser_audio_set(enabled, client_id)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_ack",
                        "action": "browser_audio_set",
                        "action_id": str(action_id),
                    }))
            except Exception as exc:
                logger.warning("browser_audio_set handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_error",
                        "action": "browser_audio_set",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "continuous_mode_set" and self._on_continuous_mode_set:
            action_id = payload.get("action_id")
            enabled = bool(payload.get("enabled", False))
            try:
                await self._on_continuous_mode_set(enabled, client_id)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_ack",
                        "action": "continuous_mode_set",
                        "action_id": str(action_id),
                    }))
            except Exception as exc:
                logger.warning("continuous_mode_set handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_error",
                        "action": "continuous_mode_set",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "chat_new" and self._on_chat_new:
            try:
                await self._on_chat_new(client_id)
            except Exception as exc:
                logger.warning("chat_new handler error: %s", exc)
            return

        if msg_type == "chat_text" and self._on_chat_text:
            text = str(payload.get("text", "")).strip()
            client_msg_id = payload.get("client_msg_id")
            if text:
                try:
                    await self._on_chat_text(text, client_id)
                    if websocket is not None:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "chat_text_ack",
                                    "client_msg_id": client_msg_id,
                                    "ok": True,
                                }
                            )
                        )
                except Exception as exc:
                    logger.warning("chat_text handler error: %s", exc)
                    if websocket is not None:
                        try:
                            await websocket.send(
                                json.dumps(
                                    {
                                        "type": "chat_text_ack",
                                        "client_msg_id": client_msg_id,
                                        "ok": False,
                                        "error": str(exc),
                                    }
                                )
                            )
                        except Exception:
                            pass
            return

        if msg_type == "chat_delete":
            thread_id = str(payload.get("thread_id", "")).strip()
            if thread_id:
                self.delete_chat_thread(thread_id)
            return

        logger.debug("Web UI: unhandled action '%s' from %s", msg_type, client_id)

    def _handle_pcm_chunk(self, pcm_bytes: bytes) -> None:
        if len(pcm_bytes) < 2:
            return
        sample_count = len(pcm_bytes) // 2
        if sample_count <= 0:
            return
        pcm_view = memoryview(pcm_bytes)[:sample_count * 2].cast("h")
        sum_sq = 0.0
        peak = 0
        for sample in pcm_view:
            s = int(sample)
            abs_s = -s if s < 0 else s
            if abs_s > peak:
                peak = abs_s
            sum_sq += float(s * s)
        rms = math.sqrt(sum_sq / float(sample_count)) / 32768.0
        self._last_browser_pcm_ts = time.monotonic()
        self._latest_browser_audio["rms"] = max(0.0, min(1.0, rms))
        self._latest_browser_audio["peak"] = max(0.0, min(1.0, float(peak) / 32768.0))
        self._browser_pcm_frames.append(pcm_bytes)
        self._browser_pcm_packet_count += 1
        self._browser_pcm_packet_bytes += len(pcm_bytes)

        now = time.monotonic()
        if now - self._last_audio_packet_log_ts >= 2.0:
            logger.info(
                "📦 Audio packet source summary: browser_pcm=%d (%d bytes, rms=%.4f peak=%.4f queued=%d) browser_audio_level=%d",
                self._browser_pcm_packet_count,
                self._browser_pcm_packet_bytes,
                self._latest_browser_audio["rms"],
                self._latest_browser_audio["peak"],
                len(self._browser_pcm_frames),
                self._browser_level_packet_count,
            )
            self._browser_level_packet_count = 0
            self._browser_pcm_packet_count = 0
            self._browser_pcm_packet_bytes = 0
            self._last_audio_packet_log_ts = now

    # ------------------------------------------------------------------
    # Status broadcast loop
    # ------------------------------------------------------------------

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(self.status_interval_s)
            if not self._clients:
                continue
            payload = self._build_status_payload()
            message = json.dumps(payload)
            stale: list[Any] = []
            for client in list(self._clients):
                try:
                    await client.send(message)
                except Exception:
                    stale.append(client)
            for c in stale:
                self._clients.discard(c)

    def _build_status_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        hotword_active = (
            self._last_hotword_ts is not None
            and (now - self._last_hotword_ts) <= self.hotword_active_s
        )
        self._status_rev += 1
        orch = dict(self._orchestrator_status)
        orch["hotword_active"] = hotword_active
        orch["mic_enabled"] = self._ui_control_state.get("mic_enabled", False)
        orch["status_rev"] = self._status_rev
        return {
            "type": "orchestrator_status",
            "ts": time.time(),
            **orch,
            "browser_audio": dict(self._latest_browser_audio),
        }

    def _build_state_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        hotword_active = (
            self._last_hotword_ts is not None
            and (now - self._last_hotword_ts) <= self.hotword_active_s
        )
        orch = dict(self._orchestrator_status)
        orch["hotword_active"] = hotword_active
        orch["status_rev"] = self._status_rev
        return {
            "type": "state_snapshot",
            "orchestrator": orch,
            "ui_control": dict(self._ui_control_state),
            "ui_control_rev": self._ui_control_rev,
            "music": dict(self._music_state),
            "music_queue": list(self._music_queue),
            "music_rev": self._music_rev,
            "timers": list(self._timers_state),
            "timers_rev": self._timers_rev,
            "chat": list(self._chat_messages[-50:]),
            "chat_threads": list(self._chat_threads),
            "active_chat_id": self._active_chat_id,
        }

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------

    def _start_http_server(self) -> None:
        ssl_context = self._ensure_ssl_context()
        start_http_servers(self, ssl_context)
