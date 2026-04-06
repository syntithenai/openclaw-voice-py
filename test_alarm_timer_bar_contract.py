from pathlib import Path


def test_timer_bar_keeps_zero_second_timers_until_backend_removes_them() -> None:
    events_source = Path("orchestrator/web/static/app-events.js").read_text(encoding="utf-8")
    ws_source = Path("orchestrator/web/static/app-ws.js").read_text(encoding="utf-8")

    assert "if(kind==='timer' && rem<=0) return false;" not in events_source
    assert "if(kind==='timer' && rem<=0) return false;" not in ws_source
    assert "if(!Number.isFinite(rem)) return false;" in events_source
    assert "if(!Number.isFinite(rem)) return false;" in ws_source


def test_web_ui_alarm_cancel_uses_ringing_stop_path() -> None:
    source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "alarm = alarm_manager.alarms.get(alarm_id)" in source
    assert "if alarm and alarm.ringing:" in source
    assert "await alarm_manager.stop_alarm(alarm_id)" in source
    assert "alarm_playback_stop_event.set()" in source
    assert "await alarm_manager.cancel_alarm(alarm_id)" in source