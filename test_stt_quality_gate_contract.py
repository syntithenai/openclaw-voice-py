from orchestrator.main import decide_ghost_transcript


def _ctx(**overrides):
    base = {
        "transcript_text": "yes",
        "canonical_transcript": "yes",
        "token_count": 1,
        "char_count": 3,
        "is_single_word": True,
        "is_short_transcript": True,
        "tts_playing": False,
        "ms_since_tts_end": 5000.0,
        "last_assistant_was_question": False,
        "last_assistant_expects_short_reply": False,
        "last_user_went_upstream": False,
        "last_upstream_response_was_question": False,
        "last_upstream_response_requested_confirmation": False,
        "upstream_context_is_fresh": False,
        "cut_in_active": False,
        "ms_from_cut_in_start": 99999.0,
        "self_echo_similarity": 0.0,
        "self_echo_similarity_threshold": 0.75,
        "single_word_enabled": True,
        "require_question_for_acks": True,
        "playback_tail_ms": 1200.0,
        "cutin_early_ms": 500.0,
        "has_fresh_prompt_context": False,
        "has_inflight_user_request": False,
    }
    base.update(overrides)
    return base


def test_ack_after_non_question_is_suppressed():
    decision = decide_ghost_transcript(_ctx())
    assert decision.accepted is False
    assert "ack_without_question_context" in decision.reason_codes


def test_single_word_after_assistant_question_is_accepted():
    decision = decide_ghost_transcript(
        _ctx(last_assistant_was_question=True, has_fresh_prompt_context=True)
    )
    assert decision.accepted is True


def test_single_word_after_upstream_question_is_accepted():
    decision = decide_ghost_transcript(
        _ctx(
            transcript_text="browser",
            canonical_transcript="browser",
            token_count=1,
            last_user_went_upstream=True,
            last_upstream_response_was_question=True,
            upstream_context_is_fresh=True,
        )
    )
    assert decision.accepted is True


def test_upstream_declarative_ack_is_suppressed():
    decision = decide_ghost_transcript(
        _ctx(
            transcript_text="okay",
            canonical_transcript="okay",
            last_user_went_upstream=True,
            last_upstream_response_was_question=False,
            upstream_context_is_fresh=True,
        )
    )
    assert decision.accepted is False


def test_high_self_echo_overlap_is_suppressed():
    decision = decide_ghost_transcript(
        _ctx(
            transcript_text="you are welcome",
            canonical_transcript="you are welcome",
            token_count=3,
            is_single_word=False,
            self_echo_similarity=0.9,
        )
    )
    assert decision.accepted is False
    assert decision.matched_priority_rule == "hard_reject_self_echo"


def test_empty_or_punctuation_is_suppressed():
    decision_empty = decide_ghost_transcript(_ctx(transcript_text="", canonical_transcript="", token_count=0))
    decision_punct = decide_ghost_transcript(_ctx(transcript_text="...", canonical_transcript="", token_count=0))
    assert decision_empty.accepted is False
    assert decision_punct.accepted is False


def test_normal_multi_word_transcript_is_accepted_by_default():
    decision = decide_ghost_transcript(
        _ctx(
            transcript_text="set a timer for ten minutes",
            canonical_transcript="set a timer for ten minutes",
            token_count=6,
            is_single_word=False,
            is_short_transcript=False,
            require_question_for_acks=False,
        )
    )
    assert decision.accepted is True


def test_short_non_ack_fragment_is_accepted_when_request_inflight():
    decision = decide_ghost_transcript(
        _ctx(
            transcript_text="time.",
            canonical_transcript="time",
            token_count=1,
            is_single_word=True,
            is_short_transcript=True,
            has_inflight_user_request=True,
        )
    )
    assert decision.accepted is True
    assert decision.matched_priority_rule == "continuation_allow_inflight"
