from pathlib import Path


def test_realtime_service_enforces_ws_auth_when_required() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "def ws_session_user" in source
    assert "if self.auth_required() and session_user is None:" in source
    assert "await websocket.close(code=4401" in source


def test_realtime_service_exposes_google_auth_helpers() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "def begin_google_login" in source
    assert "def complete_google_login" in source
    assert "oauth2.googleapis.com/token" in source
    assert "openidconnect.googleapis.com/v1/userinfo" in source
