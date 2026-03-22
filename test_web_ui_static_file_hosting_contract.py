from pathlib import Path


def test_static_ui_template_contains_runtime_tokens() -> None:
    source = Path("orchestrator/web/static/index.html").read_text(encoding="utf-8")

    assert "__WS_PORT__" in source
    assert "__MIC_STARTS_DISABLED__" in source
    assert "__AUDIO_AUTHORITY__" in source
    assert "__SERVER_INSTANCE_ID__" in source
    assert "__AUTH_MODE__" in source
    assert "__AUTHENTICATED__" in source
    assert "__AUTH_USER_JSON__" in source


def test_http_server_exposes_workspace_media_and_auth_routes() -> None:
    source = Path("orchestrator/web/http_server.py").read_text(encoding="utf-8")

    assert '"/files/workspace"' in source
    assert '"/files/media"' in source
    assert "workspace_files_allow_listing" in source
    assert "media_files_allow_listing" in source
    assert '"/auth/session"' in source
    assert '"/auth/google/login"' in source
    assert '"/auth/google/callback"' in source
    assert '"/auth/logout"' in source
    assert "service.should_protect_http_path" in source


def test_config_and_main_wire_new_web_ui_file_mount_settings() -> None:
    config_source = Path("orchestrator/config.py").read_text(encoding="utf-8")
    main_source = Path("orchestrator/main.py").read_text(encoding="utf-8")

    assert "web_ui_static_root" in config_source
    assert "web_ui_auth_mode" in config_source
    assert "web_ui_google_client_secret_file" in config_source
    assert "web_ui_google_client_id" in config_source
    assert "web_ui_google_client_secret" in config_source
    assert "web_ui_google_redirect_uri" in config_source
    assert "web_ui_google_allowed_domain" in config_source
    assert "web_ui_auth_session_cookie_name" in config_source
    assert "web_ui_auth_session_ttl_hours" in config_source
    assert "web_ui_auth_cookie_secure" in config_source
    assert "web_ui_workspace_files_enabled" in config_source
    assert "web_ui_workspace_files_root" in config_source
    assert "web_ui_workspace_files_allow_listing" in config_source
    assert "web_ui_media_files_enabled" in config_source
    assert "web_ui_media_files_root" in config_source
    assert "web_ui_media_files_allow_listing" in config_source

    assert "static_root=config.web_ui_static_root" in main_source
    assert "auth_mode=config.web_ui_auth_mode" in main_source
    assert "google_client_secret_file=config.web_ui_google_client_secret_file" in main_source
    assert "google_client_id=config.web_ui_google_client_id" in main_source
    assert "google_client_secret=config.web_ui_google_client_secret" in main_source
    assert "google_redirect_uri=config.web_ui_google_redirect_uri" in main_source
    assert "google_allowed_domain=config.web_ui_google_allowed_domain" in main_source
    assert "auth_session_cookie_name=config.web_ui_auth_session_cookie_name" in main_source
    assert "auth_session_ttl_hours=config.web_ui_auth_session_ttl_hours" in main_source
    assert "auth_cookie_secure=config.web_ui_auth_cookie_secure" in main_source
    assert "workspace_files_enabled=config.web_ui_workspace_files_enabled" in main_source
    assert "workspace_files_root=config.web_ui_workspace_files_root" in main_source
    assert "workspace_files_allow_listing=config.web_ui_workspace_files_allow_listing" in main_source
    assert "media_files_enabled=config.web_ui_media_files_enabled" in main_source
    assert "media_files_root=config.web_ui_media_files_root" in main_source
    assert "media_files_allow_listing=config.web_ui_media_files_allow_listing" in main_source