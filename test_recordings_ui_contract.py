from pathlib import Path


def test_recordings_nav_entries_present() -> None:
    source = Path("orchestrator/web/static/index.html").read_text(encoding="utf-8")

    assert 'href="#/recordings"' in source
    assert 'data-nav="recordings"' in source


def test_recordings_realtime_protocol_wired() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert 'msg_type == "recordings_list"' in source
    assert 'msg_type == "recording_get"' in source
    assert 'msg_type == "recordings_delete_selected"' in source
    assert '"recordings": list(self._recordings)' in source
    assert '"recordings_rev": self._recordings_rev' in source


def test_recordings_audio_http_route_present() -> None:
    source = Path("orchestrator/web/http_server.py").read_text(encoding="utf-8")

    assert 'path.startswith("/recordings/audio/")' in source


def test_recordings_ui_render_and_ws_handlers_present() -> None:
    events_source = Path("orchestrator/web/static/app-events.js").read_text(encoding="utf-8")
    ws_source = Path("orchestrator/web/static/app-ws.js").read_text(encoding="utf-8")

    assert "renderRecordingsPage" in events_source
    assert "type:'recordings_list'" in events_source
    assert "case 'recordings_state':" in ws_source
    assert "case 'recording_detail':" in ws_source
