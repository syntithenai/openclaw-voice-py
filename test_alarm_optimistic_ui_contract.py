from pathlib import Path


def test_optimistic_alarm_entries_are_marked_pending_server_ack() -> None:
    source = Path("orchestrator/web/static/app-core.js").read_text(encoding="utf-8")

    assert "const pendingServerAck=parsed.kind==='alarm';" in source
    assert "_pendingServerAck:pendingServerAck" in source


def test_pending_server_ack_alarms_do_not_locally_count_down() -> None:
    source = Path("orchestrator/web/static/app-ws.js").read_text(encoding="utf-8")

    assert "const pendingServerAck=!!it.pendingServerAck;" in source
    assert "if((!pendingServerAck && expectedRem<=0) || ageSec>20)" in source
    assert "if(t&&t._pendingServerAck) return;" in source