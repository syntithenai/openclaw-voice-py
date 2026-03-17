from pathlib import Path


def test_realtime_service_combines_music_state_and_queue_into_single_event() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert '"type": "music_state"' in source
    assert '"music": dict(self._music_state)' in source
    assert '"queue": list(self._music_queue)' in source


def test_web_ui_publisher_uses_atomic_music_state_update() -> None:
    source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "transport_changed = th != last_music_transport_hash" in source
    assert "queue_changed = qh != last_music_queue_hash" in source
    assert "web_service.update_music_state(queue=q, **ms)" in source
