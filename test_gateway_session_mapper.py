from orchestrator.gateway.session_mapper import map_gateway_messages_to_voice_format


def test_history_mapper_preserves_thinking_and_tool_timeline_blocks() -> None:
    mapped = map_gateway_messages_to_voice_format(
        [
            {
                "role": "user",
                "timestamp": 1,
                "content": [{"type": "text", "text": "Find the latest logs"}],
            },
            {
                "role": "assistant",
                "timestamp": 2,
                "content": [
                    {"type": "thinking", "thinking": "Checking recent files"},
                    {"type": "toolCall", "id": "call_1", "name": "read", "arguments": {"path": "/tmp/log"}},
                    {"type": "text", "text": "I found the latest logs."},
                ],
            },
            {
                "role": "toolResult",
                "timestamp": 3,
                "toolCallId": "call_1",
                "content": [{"type": "tool_result", "text": "Read 120 lines", "name": "read"}],
            },
        ]
    )

    assert [msg["role"] for msg in mapped] == ["user", "interim", "step", "assistant", "step"]
    assert mapped[1]["text"] == "reasoning"
    assert 'Checking recent files' in str(mapped[1]["details"])
    assert mapped[2]["name"] == "read"
    assert mapped[2]["tool_call_id"] == "call_1"
    assert mapped[3]["text"] == "I found the latest logs."
    assert mapped[4]["phase"] == "result"
    assert mapped[4]["tool_call_id"] == "call_1"
    assert 'Read 120 lines' in str(mapped[4]["details"])


def test_history_mapper_ignores_hidden_tool_only_turn_without_result_text() -> None:
    mapped = map_gateway_messages_to_voice_format(
        [
            {
                "role": "assistant",
                "timestamp": 2,
                "content": [
                    {"type": "toolCall", "id": "call_1", "name": "read", "arguments": {"path": "/tmp/log"}},
                ],
            }
        ]
    )

    assert len(mapped) == 1
    assert mapped[0]["role"] == "step"
    assert mapped[0]["name"] == "read"