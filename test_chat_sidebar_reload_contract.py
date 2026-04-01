from pathlib import Path


def test_clear_chat_threads_persists_and_broadcasts_sidebar_reset() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "def clear_chat_threads(self) -> bool:" in source
    assert "self._persist_chat_state()" in source
    assert '"type": "chat_threads_update"' in source
    assert "return True" in source


def test_web_ui_reloads_gateway_sessions_when_client_connects() -> None:
    realtime_source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")
    main_source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "self._on_client_connect: Callable[[str], Awaitable[None]] | None = None" in realtime_source
    assert "on_client_connect: Callable[[str], Awaitable[None]] | None = None," in realtime_source
    assert "await self._on_client_connect(client_id)" in realtime_source
    assert "async def _ui_client_connect(client_id: str) -> None:" in main_source
    assert 'await _refresh_web_ui_chat_threads_from_gateway(f"client_connect:{client_id}")' in main_source
    assert "on_client_connect=_ui_client_connect," in main_source