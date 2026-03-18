from __future__ import annotations

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from .settings_ui import validate_settings


def show_settings_dialog_gtk(initial: dict[str, str], on_save) -> None:
    """Open a native GTK settings dialog on the calling (GTK main) thread."""
    dialog = Gtk.Dialog(title="OpenClaw Desktop Settings")
    dialog.set_default_size(500, 0)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    ok_btn = dialog.add_button("Save", Gtk.ResponseType.OK)
    ok_btn.get_style_context().add_class("suggested-action")

    content = dialog.get_content_area()
    content.set_spacing(8)
    content.set_border_width(16)

    grid = Gtk.Grid(column_spacing=12, row_spacing=8)
    content.add(grid)

    label = Gtk.Label(label="Gateway URL (HTTPS):", xalign=1.0)
    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.set_width_chars(46)
    entry.set_text(initial.get("DESKTOP_GATEWAY_URL", "https://localhost"))
    entry.set_placeholder_text("https://localhost")

    grid.attach(label, 0, 0, 1, 1)
    grid.attach(entry, 1, 0, 1, 1)

    error_label = Gtk.Label(label="", xalign=0.0)
    error_label.set_no_show_all(True)
    grid.attach(error_label, 1, 1, 1, 1)

    dialog.show_all()

    while True:
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            values = {"DESKTOP_GATEWAY_URL": entry.get_text().strip()}
            ok, msg = validate_settings(values)
            if not ok:
                error_label.set_markup(f'<span foreground="red">{GLib.markup_escape_text(msg)}</span>')
                error_label.show()
                continue
            dialog.destroy()
            on_save(values)
        else:
            dialog.destroy()
        break
