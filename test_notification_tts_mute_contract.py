from pathlib import Path


def test_reply_only_tts_mute_guard_in_tts_loop() -> None:
    source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "web_service._ui_control_state.get(\"tts_muted\", False)" in source
    assert "and item.kind == \"reply\"" in source
    assert "Reply TTS muted by UI" in source