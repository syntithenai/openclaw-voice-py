from pathlib import Path


def test_openclaw_gateway_exposes_session_delete() -> None:
    source = Path("orchestrator/gateway/provider_backends/core.py").read_text(encoding="utf-8")

    assert "async def delete_session(self, *, session_key: str, delete_transcript: bool = True) -> bool:" in source
    assert 'await self._send_request("sessions.delete", params, timeout_s=self.timeout_s)' in source
    assert '"deleteTranscript": bool(delete_transcript)' in source


def test_realtime_service_routes_chat_clear_all_to_server_handler() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "self._on_chat_delete: Callable[[str, str], Awaitable[None]] | None = None" in source
    assert "self._on_chat_clear_all: Callable[[list[str], str], Awaitable[None]] | None = None" in source
    assert "def get_clearable_chat_thread_ids(self) -> list[str]:" in source
    assert "on_chat_delete: Callable[[str, str], Awaitable[None]] | None = None," in source
    assert "on_chat_clear_all: Callable[[list[str], str], Awaitable[None]] | None = None," in source
    assert "await self._on_chat_delete(thread_id, client_id)" in source
    assert "await self._on_chat_clear_all(self.get_clearable_chat_thread_ids(), client_id)" in source


def test_main_clear_all_deletes_upstream_sessions_and_refreshes_sidebar() -> None:
    source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "async def _ui_chat_delete(thread_id: str, client_id: str) -> None:" in source
    assert "async def _ui_chat_clear_all(thread_ids: list[str], client_id: str) -> None:" in source
    assert "await gateway.delete_session(" in source
    assert 'await _refresh_web_ui_chat_threads_from_gateway(f"chat_delete:{client_id}")' in source
    assert 'await _refresh_web_ui_chat_threads_from_gateway(f"chat_clear_all:{client_id}")' in source
    assert "on_chat_delete=_ui_chat_delete," in source
    assert "on_chat_clear_all=_ui_chat_clear_all," in source