from pathlib import Path


def test_chat_select_routes_to_server_activation() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "sendAction({{type:'chat_select', thread_id: tid}});" in source
    assert "def activate_chat_thread(self, thread_id: str) -> None:" in source
    assert "if msg_type == \"chat_select\":" in source
    assert "self.activate_chat_thread(str(payload.get(\"thread_id\", \"active\")))" in source


def test_chat_reset_uses_server_chat_payload() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "applyServerChatState(msg.chat, msg.chat_threads, msg.active_chat_id, true);" in source
    assert "S.chat=[];" not in source


def test_historic_thread_is_duplicated_not_removed() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "self._active_chat_source_thread_id = target_id" in source
    assert "self._chat_threads.pop(selected_index)" not in source


def test_active_edits_are_mirrored_back_to_source_thread() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "if self._active_chat_source_thread_id:" in source
    assert "thread_messages.append(dict(msg))" in source
    assert "\"type\": \"chat_threads_update\"" in source


def test_loaded_historic_session_stays_selected_while_editing() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "S.selectedChatId=String(msg.active_chat_id || S.selectedChatId || 'active');" in source
    assert "const selected = S.selectedChatId || S.activeChatId || 'active';" in source
    assert "if (S.selectedChatId !== 'active')" not in source


def test_backend_marks_loaded_thread_as_active_chat() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "self._active_chat_id = target_id" in source


def test_new_session_promotes_to_history_on_first_user_message() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "def _promote_active_chat_to_thread_if_needed(self, now_ts: float | None = None) -> bool:" in source
    assert '"title": self._derive_chat_title(self._chat_messages),' in source
    assert "self._active_chat_id = str(promoted[\"id\"])" in source
    assert "promoted = self._promote_active_chat_to_thread_if_needed(now_ts)" in source


def test_frontend_selects_promoted_active_thread() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "if(String(S.selectedChatId||'active')==='active' && String(S.activeChatId||'active')!=='active')" in source
    assert "S.selectedChatId = String(S.activeChatId||'active');" in source


def test_chat_threads_are_sorted_recent_first() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "def _sorted_chat_threads(self, threads: list[dict[str, Any]]) -> list[dict[str, Any]]:" in source
    assert "reverse=True" in source


def test_chat_persistence_is_debounced_and_user_message_gated() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "def _schedule_chat_state_persist(self) -> None:" in source
    assert "if not self._chat_state_persistable():" in source
    assert "self._chat_persist_timer = loop.call_later(" in source


def test_session_list_does_not_render_current_chat_row() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert 'data-thread-id="active"' not in source
    assert '>Current chat<' not in source


def test_follow_latest_button_is_not_rendered() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert 'id="chatFollowToggle"' not in source
    assert 'data-action="chat-follow-toggle"' not in source
    assert "function updateChatFollowToggleState()" not in source


def test_chat_delete_requires_confirmation_and_exact_title_match() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "sendAction({type:'chat_delete', thread_id: tid, expected_title: title, confirmed: true});" in source
    assert 'def delete_chat_thread(self, thread_id: str, expected_title: str = "", confirmed: bool = False) -> None:' in source
    assert 'if not confirmed:' in source
    assert 'and (not expected_title or str(thread.get("title", "")).strip() == expected_title)' in source
