from pathlib import Path


def test_music_actions_ack_before_handler_execution() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "async def _send_music_action_ack(action: str, action_id: Any) -> None:" in source
    assert "await _send_music_action_ack(\"music_toggle\", action_id)" in source
    assert "await _send_music_action_ack(\"music_stop\", action_id)" in source
    assert "await _send_music_action_ack(\"music_play_track\", action_id)" in source


def test_music_action_ack_does_not_optimistically_toggle_client_state() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "case 'music_action_ack':" in source
    assert "delete S.pendingMusicActions[String(msg.action_id)]" in source
    assert "S.music.state = normalizeMusicState(S.music.state) === 'play' ? 'stop' : 'play';" not in source
    assert "S.music.state = 'stop';" not in source
    assert "S.music.state = 'play';" not in source
