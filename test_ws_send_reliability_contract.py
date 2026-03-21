from pathlib import Path


def test_music_action_only_sets_pending_after_successful_send() -> None:
    source = Path("orchestrator/web/static/app-render.js").read_text(encoding="utf-8")

    assert "const sent=sendAction(Object.assign({type:actionType, action_id:actionId}, extraPayload||{}));" in source
    assert "recordInlineError('music','', 'Not connected - retry');" in source
    assert "S.pendingMusicActions[actionId]={type:actionType, ts:Date.now()};" in source


def test_setting_action_only_sets_pending_after_successful_send() -> None:
    source = Path("orchestrator/web/static/app-render.js").read_text(encoding="utf-8")

    assert "const sent=sendAction({type:key, action_id:actionId, enabled:!!enabled});" in source
    assert "recordInlineError('setting', key, 'Not connected - retry');" in source
    assert "S.pendingSettingActions[key]={action_id:actionId, enabled:!!enabled, ts:Date.now()};" in source