from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.config import load_config
from client.controller import TrayController
from client.settings_ui import validate_settings
from client.vu import border_width_for_state


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_action(self, payload: dict) -> None:
        self.sent.append(payload)


def test_left_click_triggers_mic_toggle(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DESKTOP_GATEWAY_URL=https://localhost\n", encoding="utf-8")
    cfg = load_config(env)
    transport = FakeTransport()
    controller = TrayController(cfg, transport)

    controller.trigger_mic_toggle()

    assert transport.sent[-1] == {"type": "mic_toggle"}


def test_toggle_actions_send_expected_payloads(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DESKTOP_GATEWAY_URL=https://localhost\n", encoding="utf-8")
    cfg = load_config(env)
    transport = FakeTransport()
    controller = TrayController(cfg, transport)

    controller.toggle_tts_mute()
    controller.toggle_continuous_mode()

    assert transport.sent[0]["type"] == "tts_mute_set"
    assert transport.sent[1]["type"] == "continuous_mode_set"
    assert controller.state.tts_muted is True
    assert controller.state.continuous_mode is True


def test_state_snapshot_updates_ui_state(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DESKTOP_GATEWAY_URL=https://localhost\n", encoding="utf-8")
    cfg = load_config(env)
    transport = FakeTransport()
    controller = TrayController(cfg, transport)

    controller.apply_message(
        {
            "type": "state_snapshot",
            "orchestrator": {
                "mic_rms": 0.42,
                "wake_state": "awake",
                "mic_enabled": True,
            },
            "ui_control": {
                "tts_muted": True,
                "continuous_mode": True,
                "browser_audio_enabled": False,
                "mic_enabled": True,
            },
        }
    )

    snap = controller.snapshot()
    assert snap["mic_rms"] == 0.42
    assert snap["wake_state"] == "awake"
    assert snap["mic_enabled"] is True
    assert snap["tts_muted"] is True
    assert snap["continuous_mode"] is True
    assert snap["browser_audio_enabled"] is False


def test_vu_border_mapping_matches_web_ui_formula() -> None:
    assert border_width_for_state(False, True, 0.9) == 4
    assert border_width_for_state(True, False, 0.9) == 4
    assert border_width_for_state(True, True, 0.0) == 2
    assert border_width_for_state(True, True, 0.25) >= 2
    assert border_width_for_state(True, True, 1.0) == 10


def test_settings_validation() -> None:
    ok, msg = validate_settings(
        {
            "DESKTOP_GATEWAY_URL": "https://localhost",
        }
    )
    assert ok is True
    assert msg == ""

    bad_ok, bad_msg = validate_settings(
        {
            "DESKTOP_GATEWAY_URL": "http://localhost",
        }
    )
    assert bad_ok is False
    assert "Gateway URL" in bad_msg


def test_web_url_derives_ws_url(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DESKTOP_GATEWAY_URL=https://192.168.1.20:19999\n", encoding="utf-8")
    cfg = load_config(env)
    assert cfg.ws_url == "wss://192.168.1.20:20000/ws"
