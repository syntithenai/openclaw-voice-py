from __future__ import annotations

from pathlib import Path


ENV_KEYS = [
    "DESKTOP_GATEWAY_URL",
    "DESKTOP_DEFAULT_TTS_MUTED",
    "DESKTOP_DEFAULT_CONTINUOUS_MODE",
    "DESKTOP_RECONNECT_DELAY_S",
]


def write_desktop_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# OpenClaw Voice desktop client configuration",
        "# Generated/updated by desktop tray client settings",
        "",
    ]
    for key in ENV_KEYS:
        val = values.get(key, "")
        safe = str(val).replace("\n", " ")
        lines.append(f"{key}={safe}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
