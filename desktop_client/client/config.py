from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


@dataclass(slots=True)
class DesktopClientConfig:
    desktop_env_path: Path
    web_ui_url: str
    ws_url: str
    default_tts_muted: bool
    default_continuous_mode: bool
    reconnect_delay_s: float


def _to_bool(raw: str | bool | None, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def derive_ws_url(web_ui_url: str, ws_port_override: str = "") -> str:
    parsed = urlparse(str(web_ui_url or "").strip())
    host = parsed.hostname or "127.0.0.1"
    scheme = "wss" if parsed.scheme == "https" else "ws"

    if ws_port_override.strip():
        try:
            ws_port = int(ws_port_override)
        except Exception:
            ws_port = 18911
    else:
        if parsed.port is not None:
            ws_port = int(parsed.port) + 1
        else:
            ws_port = 443 if parsed.scheme == "https" else 18911

    return f"{scheme}://{host}:{ws_port}/ws"


def load_config(desktop_env_path: Path | None = None) -> DesktopClientConfig:
    base_dir = Path(__file__).resolve().parents[1]  # openclaw-voice/desktop_client
    project_root = base_dir.parent
    env_path = desktop_env_path or (base_dir / ".env")

    root_env = dotenv_values(project_root / ".env")
    local_env = dotenv_values(env_path) if env_path.exists() else {}

    def get(key: str, fallback: str = "") -> str:
        if key in os.environ:
            return str(os.environ[key])
        if key in local_env and local_env[key] is not None:
            return str(local_env[key])
        if key in root_env and root_env[key] is not None:
            return str(root_env[key])
        return fallback

    ws_port = get("WEB_UI_WS_PORT", "18911")
    gateway_url = get("DESKTOP_GATEWAY_URL", "")
    web_ui_url = gateway_url or get("DESKTOP_WEB_UI_URL", "https://localhost")
    desktop_ws_url = get("DESKTOP_WS_URL", "")
    has_explicit_desktop_web_ui = (
        "DESKTOP_GATEWAY_URL" in os.environ
        or ("DESKTOP_GATEWAY_URL" in local_env and local_env["DESKTOP_GATEWAY_URL"] is not None)
        or ("DESKTOP_GATEWAY_URL" in root_env and root_env["DESKTOP_GATEWAY_URL"] is not None)
        or
        "DESKTOP_WEB_UI_URL" in os.environ
        or ("DESKTOP_WEB_UI_URL" in local_env and local_env["DESKTOP_WEB_UI_URL"] is not None)
        or ("DESKTOP_WEB_UI_URL" in root_env and root_env["DESKTOP_WEB_UI_URL"] is not None)
    )
    if desktop_ws_url:
        ws_url = desktop_ws_url
    elif has_explicit_desktop_web_ui:
        ws_url = derive_ws_url(web_ui_url)
    else:
        ws_url = derive_ws_url(web_ui_url, ws_port)

    return DesktopClientConfig(
        desktop_env_path=env_path,
        web_ui_url=web_ui_url,
        ws_url=ws_url,
        default_tts_muted=_to_bool(get("DESKTOP_DEFAULT_TTS_MUTED", "false"), False),
        default_continuous_mode=_to_bool(get("DESKTOP_DEFAULT_CONTINUOUS_MODE", "false"), False),
        reconnect_delay_s=float(get("DESKTOP_RECONNECT_DELAY_S", "1.5")),
    )
