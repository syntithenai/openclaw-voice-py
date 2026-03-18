from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def _is_http_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_settings(values: dict[str, str]) -> tuple[bool, str]:
    web_ui = str(values.get("DESKTOP_GATEWAY_URL", "")).strip()

    if not web_ui:
        return False, "Gateway URL is required."
    if not _is_http_url(web_ui):
        return False, "Gateway URL must be a valid https URL."
    if not web_ui.lower().startswith("https://"):
        return False, "Gateway URL must use https://"
    return True, ""


class SettingsDialog:
    def __init__(self, initial_values: dict[str, str], on_save) -> None:
        self._initial_values = dict(initial_values)
        self._on_save = on_save
        self._root: Any | None = None
        self._entries: dict[str, Any] = {}
        self._messagebox: Any | None = None

    def show(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox
        except Exception as exc:
            raise RuntimeError("Tkinter is not available in this Python environment") from exc

        root = tk.Tk()
        root.title("OpenClaw Desktop Client Settings")
        root.geometry("640x160")
        root.resizable(False, False)
        self._root = root
        self._messagebox = messagebox

        fields = [
            ("DESKTOP_GATEWAY_URL", "Gateway URL (HTTPS)"),
        ]

        frame = tk.Frame(root, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        for row, (key, label) in enumerate(fields):
            tk.Label(frame, text=label, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            entry = tk.Entry(frame, width=68)
            entry.insert(0, str(self._initial_values.get(key, "")))
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self._entries[key] = entry

        frame.grid_columnconfigure(1, weight=1)

        button_row = tk.Frame(frame)
        button_row.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(14, 0))

        tk.Button(button_row, text="Cancel", command=root.destroy).pack(side=tk.RIGHT, padx=6)
        tk.Button(button_row, text="Save", command=self._save).pack(side=tk.RIGHT)

        root.mainloop()

    def _save(self) -> None:
        assert self._root is not None
        values = {k: e.get().strip() for k, e in self._entries.items()}
        ok, message = validate_settings(values)
        if not ok:
            if self._messagebox is not None:
                self._messagebox.showerror("Invalid settings", message)
            return
        self._on_save(values)
        self._root.destroy()
