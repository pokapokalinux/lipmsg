from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ipmsg_core import parse_broadcast_targets


@dataclass
class AppSettings:
    user: str = ""
    group: str = ""
    absence_reply: str = "ただいま離席中です。"
    port: int = 2425
    broadcast_ip: str = "255.255.255.255"


@Gtk.Template(filename=str(Path(__file__).with_name("settings_window.ui")))
class SettingsWindow(Gtk.Window):
    __gtype_name__ = "IpmsgSettingsWindow"

    user_entry: Gtk.Entry = Gtk.Template.Child()
    group_entry: Gtk.Entry = Gtk.Template.Child()
    absence_reply_entry: Gtk.Entry = Gtk.Template.Child()
    port_spin: Gtk.SpinButton = Gtk.Template.Child()
    broadcast_entry: Gtk.Entry = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    save_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        *,
        transient_for: Gtk.Window,
        current: AppSettings,
        on_save: Callable[[AppSettings], None],
    ) -> None:
        super().__init__(transient_for=transient_for)
        self._on_save = on_save

        self.broadcast_entry.set_placeholder_text(
            "例: 255.255.255.255, 192.168.2.255, 192.168.3.10"
        )
        self.user_entry.set_text(current.user)
        self.group_entry.set_text(current.group)
        self.absence_reply_entry.set_text(current.absence_reply)
        self.port_spin.set_value(float(current.port))
        self.broadcast_entry.set_text(current.broadcast_ip)

        self.cancel_button.connect("clicked", lambda _b: self.close())
        self.save_button.connect("clicked", self._on_save_clicked)

    def _on_save_clicked(self, _btn: Gtk.Button) -> None:
        targets = parse_broadcast_targets(self.broadcast_entry.get_text())
        updated = AppSettings(
            user=self.user_entry.get_text().strip(),
            group=self.group_entry.get_text().strip(),
            absence_reply=self.absence_reply_entry.get_text().strip() or "ただいま離席中です。",
            port=int(self.port_spin.get_value_as_int()),
            broadcast_ip=", ".join(targets),
        )
        self._on_save(updated)
        self.close()