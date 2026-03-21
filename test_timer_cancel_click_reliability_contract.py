from pathlib import Path


def test_send_action_returns_boolean_status() -> None:
    source = Path("orchestrator/web/static/app-render.js").read_text(encoding="utf-8")

    assert "return true;" in source
    assert "return false;" in source


def test_send_timer_action_only_sets_pending_when_sent() -> None:
    source = Path("orchestrator/web/static/app-render.js").read_text(encoding="utf-8")

    assert "const sent=sendAction(payload);" in source
    assert "if(!sent){" in source
    assert "recordInlineError('timer', pendingKey, 'Not connected - retry');" in source
    assert "S.pendingTimerActions[pendingKey]={type:actionType, action_id:actionId, ts:Date.now()};" in source