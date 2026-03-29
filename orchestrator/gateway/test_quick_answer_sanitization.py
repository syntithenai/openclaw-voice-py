from orchestrator.gateway.quick_answer import (
    QuickAnswerClient,
    build_system_prompt,
    build_tool_definitions,
    classify_upstream_decision,
    configured_models_available_from_files,
    sanitize_quick_answer_text,
    resolve_recommended_model_id,
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


def test_should_force_upstream_for_research_and_document_request() -> None:
    query = "research information about the best way to learn old time fiddle bowing rhythms and write it all to a document"
    assert should_force_upstream(query) is True


def test_should_not_force_upstream_for_current_time_question() -> None:
    assert should_force_upstream("what time is it right now") is False


def test_should_not_force_upstream_for_today_date_question() -> None:
    assert should_force_upstream("what is today's date") is False


def test_should_not_force_upstream_for_simple_date_calculation() -> None:
    assert should_force_upstream("what date will it be in 3 weeks") is False


def test_should_not_force_upstream_for_simple_time_calculation() -> None:
    assert should_force_upstream("what time will it be in 2 hours") is False


def test_classify_date_time_reason_for_simple_calculation() -> None:
    decision, reason = classify_upstream_decision("what day will it be tomorrow")
    assert decision is False
    assert reason == "date_time_local"


def test_classify_document_authoring_reason_for_lesson_plan_request() -> None:
    query = "research old time fiddle bowing rhythms and give me 10 concrete lesson plans with goals, write it all to a document"
    decision, reason = classify_upstream_decision(query)
    assert decision is True
    assert reason == "document_authoring"


def test_should_not_force_upstream_for_recorder_intent_when_enabled() -> None:
    assert should_force_upstream("start recording", recorder_enabled=True) is False


def test_should_not_force_upstream_for_recorder_intent_when_disabled() -> None:
    assert should_force_upstream("start recording", recorder_enabled=False) is False


def test_should_not_force_upstream_for_new_session_when_enabled() -> None:
    assert should_force_upstream("start a new session", new_session_enabled=True) is False


def test_should_force_upstream_for_new_session_when_disabled() -> None:
    assert should_force_upstream("start a new session", new_session_enabled=False) is True


def test_classify_new_session_reason_when_enabled() -> None:
    decision, reason = classify_upstream_decision("please start a new session", new_session_enabled=True)
    assert decision is False
    assert reason == "new_session_local"


def test_tool_definitions_include_start_new_session_when_enabled() -> None:
    tool_defs = build_tool_definitions(
        timers_enabled=False,
        music_enabled=False,
        recorder_enabled=False,
        new_session_enabled=True,
    )
    names = [tool.get("function", {}).get("name") for tool in tool_defs]
    assert "start_new_session" in names


# ===== Narrowed Recorder Intent Pattern Tests =====

def test_recorder_narrow_pattern_start_recording() -> None:
    """Narrowed recorder: should match 'start recording'."""
    assert should_force_upstream("start recording", recorder_enabled=True) is False


def test_recorder_narrow_pattern_start_the_recording() -> None:
    """Narrowed recorder: should match 'start the recording'."""
    assert should_force_upstream("start the recording", recorder_enabled=True) is False


def test_recorder_narrow_pattern_stop_recording() -> None:
    """Narrowed recorder: should match 'stop recording'."""
    assert should_force_upstream("stop recording", recorder_enabled=True) is False


def test_recorder_narrow_pattern_stop_the_recording() -> None:
    """Narrowed recorder: should match 'stop the recording'."""
    assert should_force_upstream("stop the recording", recorder_enabled=True) is False


def test_recorder_narrow_pattern_start_record() -> None:
    """Narrowed recorder: should match 'start record' (without -ing)."""
    assert should_force_upstream("start record", recorder_enabled=True) is False


def test_recorder_narrow_pattern_stop_record() -> None:
    """Narrowed recorder: should match 'stop record' (without -ing)."""
    assert should_force_upstream("stop record", recorder_enabled=True) is False


def test_recorder_narrow_excluded_begin_recording() -> None:
    """Narrowed recorder: 'begin recording' does NOT match pattern; passes through to QA."""
    # Since "begin recording" isn't in any escalation pattern, it falls through to QA
    decision, reason = classify_upstream_decision("begin recording", recorder_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_recorder_narrow_excluded_end_recording() -> None:
    """Narrowed recorder: 'end recording' does NOT match pattern; passes through to QA."""
    decision, reason = classify_upstream_decision("end recording", recorder_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_recorder_narrow_excluded_finish_recording() -> None:
    """Narrowed recorder: 'finish recording' does NOT match pattern; passes through to QA."""
    decision, reason = classify_upstream_decision("finish recording", recorder_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_recorder_narrow_excluded_status() -> None:
    """Narrowed recorder: 'recorder status' does NOT match pattern; passes through to QA."""
    decision, reason = classify_upstream_decision("check recorder status", recorder_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_recorder_narrow_excluded_on_off() -> None:
    """Narrowed recorder: 'recorder on' does NOT match pattern; passes through to QA."""
    decision, reason = classify_upstream_decision("turn recorder on", recorder_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_recorder_narrow_disabled_escalates_start_recording() -> None:
    """Narrowed recorder: when disabled, 'start recording' does not trigger local handling."""
    # When recorder_enabled=False, "start recording" pattern match doesn't trigger local handling
    decision, reason = classify_upstream_decision("start recording", recorder_enabled=False)
    assert decision is False  # No escalation pattern matches either
    assert reason == "quick_answer_allowed"


# ===== Narrowed New-Session Intent Pattern Tests =====

def test_new_session_narrow_pattern_start_new_session() -> None:
    """Narrowed new-session: should match 'start new session'."""
    assert should_force_upstream("start new session", new_session_enabled=True) is False


def test_new_session_narrow_pattern_start_a_new_session() -> None:
    """Narrowed new-session: should match 'start a new session'."""
    assert should_force_upstream("start a new session", new_session_enabled=True) is False


def test_new_session_narrow_pattern_start_new_chat() -> None:
    """Narrowed new-session: should match 'start new chat'."""
    assert should_force_upstream("start new chat", new_session_enabled=True) is False


def test_new_session_narrow_pattern_start_a_new_chat() -> None:
    """Narrowed new-session: should match 'start a new chat'."""
    assert should_force_upstream("start a new chat", new_session_enabled=True) is False


def test_new_session_narrow_excluded_create() -> None:
    """Narrowed new-session: 'create new session' does NOT match pattern."""
    # Should escalate since it matches action_intent but new_session check won't catch it
    decision, reason = classify_upstream_decision("create new session", new_session_enabled=True)
    assert decision is True
    assert reason == "action_intent"


def test_new_session_narrow_excluded_open() -> None:
    """Narrowed new-session: 'open new chat' does NOT match pattern."""
    decision, reason = classify_upstream_decision("open new chat", new_session_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_new_session_narrow_excluded_begin() -> None:
    """Narrowed new-session: 'begin new session' does NOT match pattern."""
    decision, reason = classify_upstream_decision("begin new session", new_session_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_new_session_narrow_excluded_fresh() -> None:
    """Narrowed new-session: 'fresh session' does NOT match pattern."""
    decision, reason = classify_upstream_decision("fresh session", new_session_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_new_session_narrow_excluded_reset() -> None:
    """Narrowed new-session: 'reset session' does NOT match pattern."""
    decision, reason = classify_upstream_decision("reset session", new_session_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_new_session_narrow_excluded_clear() -> None:
    """Narrowed new-session: 'clear conversation' does NOT match pattern."""
    decision, reason = classify_upstream_decision("clear conversation", new_session_enabled=True)
    assert decision is False
    assert reason == "quick_answer_allowed"


def test_new_session_disabled_escalates_start_new_chat() -> None:
    """Narrowed new-session: when disabled, 'start new chat' matches new_session_action_disabled."""
    decision, reason = classify_upstream_decision("start new chat", new_session_enabled=False)
    assert decision is True
    assert reason == "new_session_action_disabled"


# ===== Model Tier Resolution Tests =====

class MockConfig:
    """Mock config object for testing resolve_recommended_model_id."""
    def __init__(self, **kwargs):
        self.quick_answer_model_tier_fast_id = kwargs.get("fast", "")
        self.quick_answer_model_tier_basic_id = kwargs.get("basic", "")
        self.quick_answer_model_tier_capable_id = kwargs.get("capable", "")
        self.quick_answer_model_tier_smart_id = kwargs.get("smart", "")
        self.quick_answer_model_tier_genius_id = kwargs.get("genius", "")


def test_resolve_model_id_exact_match() -> None:
    """Tier resolution: should return exact tier match when available."""
    config = MockConfig(capable="llm-capable-v1")
    recommendation = {"type": "model_recommendation", "tier": "capable"}
    assert resolve_recommended_model_id(recommendation, config) == "llm-capable-v1"


def test_resolve_model_id_fallback_chain() -> None:
    """Tier resolution: should fallback to next tier in chain when requested tier is empty."""
    config = MockConfig(capable="", smart="llm-smart-v1", genius="llm-genius-v1")
    recommendation = {"type": "model_recommendation", "tier": "capable"}
    assert resolve_recommended_model_id(recommendation, config) == "llm-smart-v1"


def test_resolve_model_id_no_tier() -> None:
    """Tier resolution: should return None when recommendation has no tier."""
    config = MockConfig(smart="llm-smart-v1")
    recommendation = {"type": "model_recommendation"}
    assert resolve_recommended_model_id(recommendation, config) is None


def test_resolve_model_id_invalid_tier() -> None:
    """Tier resolution: should return None for unrecognized tier name."""
    config = MockConfig(smart="llm-smart-v1")
    recommendation = {"type": "model_recommendation", "tier": "invalid"}
    assert resolve_recommended_model_id(recommendation, config) is None


def test_resolve_model_id_not_dict() -> None:
    """Tier resolution: should return None when recommendation is not a dict."""
    config = MockConfig(smart="llm-smart-v1")
    assert resolve_recommended_model_id("not-a-dict", config) is None


def test_resolve_model_id_all_empty_returns_none() -> None:
    """Tier resolution: should return None when all tier models are empty."""
    config = MockConfig()
    recommendation = {"type": "model_recommendation", "tier": "fast"}
    assert resolve_recommended_model_id(recommendation, config) is None


def test_quick_answer_model_recommendation_pop_clears_state() -> None:
    client = QuickAnswerClient(llm_url="http://localhost:1234/v1/chat/completions")
    client._last_model_recommendation = {
        "type": "model_recommendation",
        "tier": "smart",
        "reason": "needs deeper reasoning",
    }

    first = client.pop_last_model_recommendation()
    second = client.pop_last_model_recommendation()

    assert first == {
        "type": "model_recommendation",
        "tier": "smart",
        "reason": "needs deeper reasoning",
    }
    assert second is None


def test_build_system_prompt_no_models_excludes_model_recommendation() -> None:
        prompt = build_system_prompt(
                "Saturday, March 29, 2026 at 03:00 PM",
                timers_enabled=True,
                music_enabled=False,
                recorder_enabled=False,
                new_session_enabled=False,
                openclaw_models_available=False,
        )
        assert "MODEL RECOMMENDATION" not in prompt
        assert "model_recommendation JSON" not in prompt
        assert "respond with USE_UPSTREAM_AGENT" in prompt


def test_configured_models_available_from_openclaw_json_with_comments(tmp_path) -> None:
        cfg = tmp_path / "openclaw.json"
        cfg.write_text(
                """
                {
                    "models": {
                        "providers": {
                            "local": {
                                "models": [
                                    {"id": "gpt-oss-20b"},
                                ]
                            }
                        }
                    }
                }
                """,
                encoding="utf-8",
        )
        assert configured_models_available_from_files([str(cfg)]) is True


def test_configured_models_available_false_when_files_missing(tmp_path) -> None:
        missing_models = tmp_path / "models.json"
        missing_openclaw = tmp_path / "openclaw.json"
        assert configured_models_available_from_files([str(missing_models), str(missing_openclaw)]) is False


def test_configured_models_available_false_when_models_empty(tmp_path) -> None:
        cfg = tmp_path / "openclaw.json"
        cfg.write_text('{"models":{"providers":{"local":{"models":[]}}}}', encoding="utf-8")
        assert configured_models_available_from_files([str(cfg)]) is False
