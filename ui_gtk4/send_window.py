from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GObject, Gtk

from ipmsg_core import PeerInfo


class PeerRowObject(GObject.Object):
    __gtype_name__ = "IpmsgPeerRowObject"

    def __init__(self, peer: PeerInfo) -> None:
        super().__init__()
        self.peer = peer


@Gtk.Template(filename=str(Path(__file__).with_name("send_window.ui")))
class SendWindow(Gtk.ApplicationWindow):
    __gtype_name__ = "IpmsgSendWindow"

    peer_count_label: Gtk.Label = Gtk.Template.Child()
    absence_toggle: Gtk.ToggleButton = Gtk.Template.Child()
    refresh_button: Gtk.Button = Gtk.Template.Child()
    history_button: Gtk.Button = Gtk.Template.Child()
    settings_button: Gtk.Button = Gtk.Template.Child()
    peer_column_view: Gtk.ColumnView = Gtk.Template.Child()
    attach_button: Gtk.Button = Gtk.Template.Child()
    clear_attach_button: Gtk.Button = Gtk.Template.Child()
    attach_summary_label: Gtk.Label = Gtk.Template.Child()
    message_text_view: Gtk.TextView = Gtk.Template.Child()
    secret_check: Gtk.CheckButton = Gtk.Template.Child()
    status_label: Gtk.Label = Gtk.Template.Child()
    send_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        *,
        application: Gtk.Application,
        on_send: Callable[[list[PeerInfo], str, bool, list[Path]], None],
        on_refresh: Callable[[], None],
        on_toggle_absence: Callable[[bool], None],
        on_open_history: Callable[[], None],
        on_open_settings: Callable[[], None],
    ) -> None:
        super().__init__(application=application)
        self._on_send = on_send
        self._on_refresh = on_refresh
        self._on_toggle_absence = on_toggle_absence
        self._on_open_history = on_open_history
        self._on_open_settings = on_open_settings

        self._peers: list[PeerInfo] = []
        self._attached_paths: list[Path] = []
        self._store = Gio.ListStore.new(PeerRowObject)
        self._selection = Gtk.MultiSelection.new(self._store)
        self.peer_column_view.set_model(self._selection)

        self._setup_columns()
        self._bind_signals()

    def _setup_columns(self) -> None:
        self._add_text_column("ユーザ", lambda p: p.user, expand=True)
        self._add_text_column("状態", lambda p: "不在" if p.absent else "在席")
        self._add_text_column("グループ", lambda p: p.group or "-")
        self._add_text_column("ホスト", lambda p: p.host)
        self._add_text_column("IP", lambda p: p.ip)

    def _add_text_column(
        self,
        title: str,
        getter: Callable[[PeerInfo], str],
        *,
        expand: bool = False,
    ) -> None:
        factory = Gtk.SignalListItemFactory()

        def on_setup(_f: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            lbl = Gtk.Label(xalign=0.0)
            lbl.set_margin_start(6)
            lbl.set_margin_end(6)
            list_item.set_child(lbl)

        def on_bind(_f: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            row = list_item.get_item()
            lbl = list_item.get_child()
            if isinstance(row, PeerRowObject) and isinstance(lbl, Gtk.Label):
                lbl.set_text(getter(row.peer))

        factory.connect("setup", on_setup)
        factory.connect("bind", on_bind)

        col = Gtk.ColumnViewColumn.new(title, factory)
        col.set_expand(expand)
        self.peer_column_view.append_column(col)

    def _bind_signals(self) -> None:
        self.send_button.connect("clicked", self._on_send_clicked)
        self.refresh_button.connect("clicked", lambda _b: self._on_refresh())
        self.history_button.connect("clicked", lambda _b: self._on_open_history())
        self.settings_button.connect("clicked", lambda _b: self._on_open_settings())
        self.absence_toggle.connect("toggled", self._on_absence_toggled)
        self.attach_button.connect("clicked", self._on_attach_clicked)
        self.clear_attach_button.connect("clicked", self._on_clear_attach_clicked)

        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.message_text_view.add_controller(key_ctrl)

    def _on_key_pressed(
        self,
        _ctrl: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if (state & Gdk.ModifierType.CONTROL_MASK) and keyval in (
            Gdk.KEY_Return,
            Gdk.KEY_KP_Enter,
        ):
            self._on_send_clicked(self.send_button)
            return True
        return False

    def _on_absence_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._on_toggle_absence(btn.get_active())

    def set_absence_state(self, absent: bool) -> None:
        if self.absence_toggle.get_active() != absent:
            self.absence_toggle.set_active(absent)

    def set_status(self, text: str) -> None:
        self.status_label.set_text(text)

    def update_peers(self, peers: list[PeerInfo]) -> None:
        selected_ips = {p.ip for p in self.get_selected_peers()}
        self._peers = list(peers)
        self._store.remove_all()

        to_select: list[int] = []
        for idx, peer in enumerate(self._peers):
            self._store.append(PeerRowObject(peer))
            if peer.ip in selected_ips:
                to_select.append(idx)

        self.peer_count_label.set_text(f"ユーザ数: {len(self._peers)}")
        for idx in to_select:
            self._selection.select_item(idx, False)

    def select_peer_by_ip(self, ip: str) -> None:
        for idx, peer in enumerate(self._peers):
            if peer.ip == ip:
                self._selection.unselect_all()
                self._selection.select_item(idx, True)
                self.peer_column_view.scroll_to(idx, None, Gtk.ListScrollFlags.NONE, None)
                break

    def get_selected_peers(self) -> list[PeerInfo]:
        result: list[PeerInfo] = []
        n = self._store.get_n_items()
        for i in range(n):
            if self._selection.is_selected(i):
                item = self._store.get_item(i)
                if isinstance(item, PeerRowObject):
                    result.append(item.peer)
        return result

    def _on_attach_clicked(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title("添付ファイルを選択")
        dialog.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except Exception:
            return
        if files is None:
            return

        added: list[Path] = []
        for i in range(files.get_n_items()):
            gfile = files.get_item(i)
            if isinstance(gfile, Gio.File):
                p = gfile.get_path()
                if p:
                    path_obj = Path(p)
                    if path_obj.is_file() and path_obj not in self._attached_paths:
                        self._attached_paths.append(path_obj)
                        added.append(path_obj)

        if added:
            self._refresh_attachment_label()

    def _on_clear_attach_clicked(self, _btn: Gtk.Button) -> None:
        self._attached_paths.clear()
        self._refresh_attachment_label()

    def _refresh_attachment_label(self) -> None:
        count = len(self._attached_paths)
        self.clear_attach_button.set_sensitive(count > 0)
        if count == 0:
            self.attach_summary_label.set_text("添付なし")
        elif count == 1:
            self.attach_summary_label.set_text(f"添付: {self._attached_paths[0].name}")
        else:
            self.attach_summary_label.set_text(
                f"添付 {count} 件: {self._attached_paths[0].name} ほか"
            )

    def _on_send_clicked(self, _btn: Gtk.Button) -> None:
        recipients = self.get_selected_peers()
        buf = self.message_text_view.get_buffer()
        start, end = buf.get_bounds()
        body = buf.get_text(start, end, True)

        if not recipients and not self._attached_paths:
            self.set_status("送信先を選択してください。")
            return
        if not recipients:
            self.set_status("送信先を1人以上選択してください。")
            return
        if not body.strip() and not self._attached_paths:
            self.set_status("メッセージまたは添付ファイルを指定してください。")
            return

        sealed = self.secret_check.get_active()
        attachments = list(self._attached_paths)
        self._on_send(recipients, body, sealed, attachments)

        buf.set_text("")
        self.secret_check.set_active(False)
        self._attached_paths.clear()
        self._refresh_attachment_label()