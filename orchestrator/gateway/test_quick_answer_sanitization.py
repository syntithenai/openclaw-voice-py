from orchestrator.gateway.quick_answer import (
    classify_upstream_decision,
    sanitize_quick_answer_text,
    should_force_upstream,
)


def test_sanitize_quick_answer_text_strips_asterisk_markdown() -> None:
    text = "This is **bold** and *italic*"
    assert sanitize_quick_answer_text(text) == "This is bold and italic"


def test_sanitize_quick_answer_text_collapses_whitespace_after_strip() -> None:
    text = "Keep  **two**   spaces *clean*"
    assert sanitize_quick_answer_text(text) == "Keep two spaces clean"


def test_sanitize_quick_answer_text_handles_non_string() -> None:
    assert sanitize_quick_answer_text(None) == ""


def test_sanitize_quick_answer_text_extracts_nested_tool_result_response() -> None:
    payload = {
        "success": True,
        "result": {
            "alarm_id": "123",
            "trigger_time": 1773234000.0,
            "label": "",
            "response": "Alarm set for 12:00 AM",
        },
    }
    assert sanitize_quick_answer_text(payload) == "Alarm set for 12:00 AM"


def test_sanitize_quick_answer_text_falls_back_to_label() -> None:
    payload = {
        "success": True,
        "result": {
            "alarm_id": "123",
            "label": "wake up",
        },
    }
    assert sanitize_quick_answer_text(payload) == "wake up"


def test_should_force_upstream_for_open_browser_tab_command() -> None:
    assert should_force_upstream("open a browser tab to google.com") is True


def test_should_force_upstream_for_direct_visit_command() -> None:
    assert should_force_upstream("visit wikipedia.org") is True


def test_should_not_force_upstream_for_alarm_intent_when_timers_enabled() -> None:
    assert should_force_upstream("add an alarm for 30 seconds", timers_enabled=True) is False


def test_should_force_upstream_for_alarm_intent_when_timers_disabled() -> None:
    assert should_force_upstream("add an alarm for 30 seconds", timers_enabled=False) is True


def test_should_not_force_upstream_for_music_intent_when_music_enabled() -> None:
    assert should_force_upstream("play some music", music_enabled=True) is False


def test_should_force_upstream_for_shopping_list_action_even_when_timers_enabled() -> None:
    assert should_force_upstream("add milk to my shopping list", timers_enabled=True) is True


def test_classify_alarm_intent_reason_when_timers_enabled() -> None:
    decision, reason = classify_upstream_decision("add an alarm for 30 seconds", timers_enabled=True)
    assert decision is False
    assert reason == "timer_alarm_local"


def test_classify_shopping_list_reason() -> None:
    decision, reason = classify_upstream_decision("add milk to my shopping list", timers_enabled=True)
    assert decision is True
    assert reason == "action_intent"
