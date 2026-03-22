from pathlib import Path


def test_tts_mute_guard_and_timer_name_exception_contract() -> None:
    source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "web_service._ui_control_state.get(\"tts_muted\", False)" in source
    assert "not item.allow_when_ui_tts_muted" in source
    assert "TTS muted by UI; skipping" in source
    assert "allow_when_ui_tts_muted=True" in source