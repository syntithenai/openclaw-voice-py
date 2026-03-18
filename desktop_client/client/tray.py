from __future__ import annotations

import io
import os
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import derive_ws_url
from .controller import TrayController
from .settings_store import write_desktop_env
from .settings_ui import SettingsDialog
from .state import ClientState
from .vu import border_width_for_state


class DesktopTrayApp:
    def __init__(self, controller: TrayController) -> None:
        self.controller = controller
        self.controller.add_listener(self._on_state_changed)

        self._icon = None
        self._pystray = None
        self._pil_image = None
        self._last_status_line = "OpenClaw Voice Desktop Client"
        self._fallback_opened = False
        self._control_root = None  # tk.Tk reference for deiconify
        self._mic_glyph = None

    def run(self) -> None:
        self._prefer_clickable_linux_backend()

        import pystray
        from PIL import Image, ImageDraw

        self._pystray = pystray
        self._pil_image = (Image, ImageDraw)

        menu = pystray.Menu(
            pystray.MenuItem("Open Web UI", self._open_web_ui),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Mute TTS",
                self._toggle_tts,
                checked=lambda _: self.controller.state.tts_muted,
                radio=False,
            ),
            pystray.MenuItem(
                "Continuous Mode",
                self._toggle_continuous,
                checked=lambda _: self.controller.state.continuous_mode,
                radio=False,
            ),
            pystray.MenuItem("Settings", self._open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

        icon = pystray.Icon(
            name="openclaw-desktop-client",
            icon=self._make_icon(self.controller.state),
            title="OpenClaw Voice Desktop Client",
            menu=menu,
        )
        self._icon = icon

        supports_menu = bool(getattr(icon.__class__, "HAS_MENU", True))
        if not supports_menu:
            self._last_status_line = "Tray backend has no click/menu support. Using control window."
            try:
                icon.run_detached()
            except Exception:
                pass
            self._run_fallback_window(do_auto_open=True)
            return

        # Keep tray loop as the primary event loop for reliable left/right click
        # behavior across Linux backends.
        try:
            icon.run()
        except Exception:
            self._run_fallback_window(do_auto_open=True)

    def _prefer_clickable_linux_backend(self) -> None:
        if os.name != "posix":
            return
        if os.environ.get("PYSTRAY_BACKEND"):
            return

        for backend_name, module_name in (("gtk", "pystray._gtk"), ("appindicator", "pystray._appindicator")):
            try:
                __import__(module_name)
                os.environ["PYSTRAY_BACKEND"] = backend_name
                self._last_status_line = f"Tray backend selected: {backend_name}"
                return
            except Exception:
                continue

    def _open_control_window(self, *_) -> None:
        """Open/raise the control window (left-click default action)."""
        root = self._control_root
        if root is not None:
            try:
                # Schedule on the tk main thread to avoid cross-thread issues.
                root.after(0, lambda: (root.deiconify(), root.lift()))
            except Exception:
                pass
            return

        # tkinter should run on the calling thread to avoid intermittent
        # event-loop issues when opening from tray callbacks.
        self._run_fallback_window(do_auto_open=True)

    def _on_state_changed(self, state: ClientState) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.icon = self._make_icon(state)
            status = "connected" if state.connected else "disconnected"
            self._last_status_line = (
                f"OpenClaw Voice ({status}) | tts_muted={state.tts_muted} | "
                f"continuous={state.continuous_mode} | rms={state.mic_rms:.2f}"
            )
            icon.title = self._last_status_line
            icon.update_menu()
        except Exception:
            # Some Linux tray backends can raise on cross-thread or transient redraw calls.
            # Keep the app alive and continue serving menu actions.
            pass

    def _run_fallback_window(self, do_auto_open: bool = True) -> None:
        try:
            import tkinter as tk
        except Exception:
            self._last_status_line = "Control window unavailable (tkinter not installed)."
            icon = self._icon
            if icon is not None:
                with suppress(Exception):
                    icon.title = self._last_status_line
            return

        if self._fallback_opened:
            root = self._control_root
            if root is not None:
                with suppress(Exception):
                    root.after(0, lambda: (root.deiconify(), root.lift()))
            return

        self._fallback_opened = True
        root = tk.Tk()
        self._control_root = root
        root.title("OpenClaw Voice Controls")
        root.geometry("360x220")
        root.resizable(False, False)

        status_var = tk.StringVar(value=self._last_status_line)

        frame = tk.Frame(root, padx=14, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="System tray controls are unavailable right now.\nUse this control window.",
            justify="left",
            anchor="w",
        ).pack(anchor="w")

        tk.Label(frame, textvariable=status_var, justify="left", wraplength=330, anchor="w").pack(anchor="w", pady=(10, 10))

        row1 = tk.Frame(frame)
        row1.pack(fill=tk.X, pady=4)
        tk.Button(row1, text="Toggle Microphone", command=self._toggle_mic, width=18).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(row1, text="Open Web UI", command=self._open_web_ui, width=14).pack(side=tk.LEFT)

        row2 = tk.Frame(frame)
        row2.pack(fill=tk.X, pady=4)
        tk.Button(row2, text="Mute TTS", command=self._toggle_tts, width=18).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(row2, text="Continuous Mode", command=self._toggle_continuous, width=14).pack(side=tk.LEFT)

        row3 = tk.Frame(frame)
        row3.pack(fill=tk.X, pady=4)
        tk.Button(row3, text="Settings", command=self._open_settings, width=18).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(row3, text="Quit", command=root.destroy, width=14).pack(side=tk.LEFT)

        def _refresh_status() -> None:
            status_var.set(self._last_status_line)
            root.after(500, _refresh_status)

        root.after(500, _refresh_status)
        if not do_auto_open:
            root.withdraw()  # hidden but event loop keeps app alive; deiconify on left-click
        root.mainloop()
        self._fallback_opened = False
        self._control_root = None

    def _make_icon(self, state: ClientState):
        Image, ImageDraw = self._pil_image
        size = 64
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        stroke = border_width_for_state(state.connected, state.mic_enabled, state.mic_rms)
        color = (130, 130, 130, 255)
        if state.connected:
            color = (220, 60, 60, 255)
            if state.wake_state == "awake":
                color = (50, 185, 95, 255)

        # ── Dark background disk ────────────────────────────────────────────────
        d.ellipse((6, 6, 58, 58), fill=(36, 36, 46, 255))

        # ── Coloured VU ring ────────────────────────────────────────────────────
        d.ellipse((6, 6, 58, 58), outline=color, width=max(2, stroke))

        # ── Microphone glyph from bundled SVG ──────────────────────────────────
        glyph = self._load_mic_glyph()
        if glyph is not None:
            im.alpha_composite(glyph, (14, 14))
        else:
            W = (242, 242, 246, 255)
            d.rounded_rectangle((21, 10, 43, 36), radius=11, fill=W)
            d.rectangle((24, 24, 40, 26), fill=(36, 36, 46, 255))
            d.arc((16, 26, 48, 50), start=0, end=180, fill=W, width=4)
            d.rectangle((30, 40, 34, 50), fill=W)
            d.rounded_rectangle((22, 50, 42, 54), radius=2, fill=W)

        # ── Strike-through diagonal when disconnected ───────────────────────────
        if not state.connected:
            d.line((16, 48, 48, 16), fill=(240, 200, 80, 255), width=4)

        return im

    def _load_mic_glyph(self):
        if self._mic_glyph is not None:
            return self._mic_glyph

        Image, _ = self._pil_image
        svg_path = Path(__file__).resolve().parent / "assets" / "microphone.svg"
        if not svg_path.exists():
            return None

        try:
            import cairosvg

            svg_text = svg_path.read_text(encoding="utf-8")
            themed = svg_text.replace("currentColor", "#f2f2f6")
            png_bytes = cairosvg.svg2png(bytestring=themed.encode("utf-8"), output_width=36, output_height=36)
            glyph = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
            self._mic_glyph = glyph
            return glyph
        except Exception:
            return None

    def _toggle_mic(self, *_: Any) -> None:
        self.controller.trigger_mic_toggle()

    def _open_web_ui(self, *_: Any) -> None:
        self.controller.open_web_ui()

    def _toggle_tts(self, *_: Any) -> None:
        self.controller.toggle_tts_mute()

    def _toggle_continuous(self, *_: Any) -> None:
        self.controller.toggle_continuous_mode()

    def _open_settings(self, *_: Any) -> None:
        cfg = self.controller.config
        initial = {
            "DESKTOP_GATEWAY_URL": cfg.web_ui_url,
        }

        def on_save(values: dict[str, str]) -> None:
            cfg.web_ui_url = values.get("DESKTOP_GATEWAY_URL", cfg.web_ui_url)
            cfg.ws_url = derive_ws_url(cfg.web_ui_url)
            write_desktop_env(
                cfg.desktop_env_path,
                {
                    "DESKTOP_GATEWAY_URL": cfg.web_ui_url,
                    "DESKTOP_DEFAULT_TTS_MUTED": str(self.controller.state.tts_muted).lower(),
                    "DESKTOP_DEFAULT_CONTINUOUS_MODE": str(self.controller.state.continuous_mode).lower(),
                    "DESKTOP_RECONNECT_DELAY_S": str(cfg.reconnect_delay_s),
                },
            )

        backend = os.environ.get("PYSTRAY_BACKEND", "")
        if backend in ("gtk", "appindicator"):
            # GTK main loop is on this thread — use native GTK dialog directly.
            from .settings_gtk import show_settings_dialog_gtk
            show_settings_dialog_gtk(initial, on_save)
        else:
            def _open() -> None:
                try:
                    SettingsDialog(initial, on_save).show()
                except Exception:
                    self._last_status_line = "Settings UI unavailable."
                    if self._icon is not None:
                        with suppress(Exception):
                            self._icon.title = self._last_status_line
            threading.Thread(target=_open, daemon=True).start()

    def _quit(self, *_: Any) -> None:
        if self._icon is not None:
            self._icon.stop()
