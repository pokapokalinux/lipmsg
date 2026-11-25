from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ipmsg_core import (
    FileAttachmentSpec,
    HistoryStore,
    IncomingMessageEvent,
    IpmsgNode,
    NodeConfig,
    PeerInfo,
)
from ui_gtk4.receive_window import ReceiveWindow
from ui_gtk4.send_window import SendWindow
from ui_gtk4.settings_window import AppSettings, SettingsWindow

APP_ID = "org.example.IpmsgLinuxGtk4"


def _config_dir() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else (Path.home() / ".config")
    return base / "ipmsg-linux-gtk4"


def _data_dir() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else (Path.home() / ".local/share")
    return base / "ipmsg-linux-gtk4"


def _load_settings() -> AppSettings:
    cfg_path = _config_dir() / "config.json"
    if not cfg_path.is_file():
        return AppSettings()
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return AppSettings(
            user=str(data.get("user", "")),
            group=str(data.get("group", "")),
            absence_reply=str(data.get("absence_reply", "ただいま離席中です。")),
            port=int(data.get("port", 2425)),
            broadcast_ip=str(data.get("broadcast_ip", "255.255.255.255")),
        )
    except Exception:
        return AppSettings()


def _save_settings(settings: AppSettings) -> None:
    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class IpmsgGtk4Application(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self._settings = _load_settings()
        self._history = HistoryStore(_data_dir() / "history.db")
        self._node: IpmsgNode | None = None
        self._send_window: SendWindow | None = None
        self._open_receive_windows: list[ReceiveWindow] = []

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self._load_css()
        self._start_node()

    def do_activate(self) -> None:
        if self._send_window is None:
            self._send_window = SendWindow(
                application=self,
                on_send=self._handle_send,
                on_refresh=self._handle_refresh,
                on_toggle_absence=self._handle_toggle_absence,
                on_open_history=self._handle_open_history,
                on_open_settings=self._handle_open_settings,
            )
            self._send_window.connect("close-request", self._on_main_window_close)
            if self._node is not None:
                self._send_window.update_peers(self._node.get_peers())
                self._send_window.set_absence_state(self._node.is_absent)
        self._send_window.present()

    def _on_main_window_close(self, _win: Gtk.Window) -> bool:
        self._shutdown_services()
        return False

    def _shutdown_services(self) -> None:
        if self._node is not None:
            self._node.stop()
            self._node = None
        self._history.close()

    def _load_css(self) -> None:
        css_path = Path(__file__).with_name("style.css")
        if not css_path.is_file():
            return
        provider = Gtk.CssProvider.new()
        provider.load_from_path(str(css_path))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _start_node(self) -> None:
        if self._node is not None:
            self._node.stop()
            self._node = None

        node_cfg = NodeConfig(
            user=self._settings.user,
            group=self._settings.group,
            port=self._settings.port,
            broadcast_ip=self._settings.broadcast_ip,
            absence_reply=self._settings.absence_reply,
            use_utf8=True,
            enable_file_server=True,
        )
        node = IpmsgNode(
            config=node_cfg,
            history=self._history,
            on_peers_changed=self._on_peers_changed_bg,
            on_message_received=self._on_message_received_bg,
            on_absence_changed=self._on_absence_changed_bg,
        )
        self._node = node
        node.start()

    def _on_peers_changed_bg(self, peers: list[PeerInfo]) -> None:
        GLib.idle_add(
            self._apply_peers_ui,
            list(peers),
            priority=GLib.PRIORITY_DEFAULT,
        )

    def _apply_peers_ui(self, peers: list[PeerInfo]) -> int:
        if self._send_window is not None:
            self._send_window.update_peers(peers)
        return GLib.SOURCE_REMOVE

    def _on_absence_changed_bg(self, absent: bool) -> None:
        GLib.idle_add(
            self._apply_absence_ui,
            absent,
            priority=GLib.PRIORITY_DEFAULT,
        )

    def _apply_absence_ui(self, absent: bool) -> int:
        if self._send_window is not None:
            self._send_window.set_absence_state(absent)
            self._send_window.set_status("不在モード中" if absent else "在席中")
        return GLib.SOURCE_REMOVE

    def _on_message_received_bg(self, event: IncomingMessageEvent) -> None:
        GLib.idle_add(
            self._show_receive_window_ui,
            event,
            priority=GLib.PRIORITY_DEFAULT,
        )

    def _show_receive_window_ui(self, event: IncomingMessageEvent) -> int:
        parent = self._send_window
        if parent is None:
            return GLib.SOURCE_REMOVE

        win = ReceiveWindow(
            transient_for=parent,
            event=event,
            on_reply=self._handle_reply_from_receive,
            on_ack_close=self._handle_ack_close_from_receive,
            on_download_attachment=self._handle_download_attachment,
        )
        self._open_receive_windows.append(win)
        win.connect(
            "destroy",
            lambda w: self._open_receive_windows.remove(w)
            if w in self._open_receive_windows
            else None,
        )
        win.present()
        return GLib.SOURCE_REMOVE

    def _handle_send(
        self,
        recipients: list[PeerInfo],
        body: str,
        sealed: bool,
        file_paths: list[Path],
    ) -> None:
        if self._node is None:
            return
        for peer in recipients:
            self._node.send_message(
                target=peer,
                body=body,
                sealed=sealed,
                file_paths=file_paths if file_paths else None,
                wait_ack=True,
                send_read_notice_req=sealed,
            )
        if self._send_window is not None:
            self._send_window.set_status(f"{len(recipients)} 人へ送信しました。")

    def _handle_refresh(self) -> None:
        if self._node is None:
            return
        self._node.announce_presence()
        if self._send_window is not None:
            self._send_window.set_status("プレゼンスを再通知しました。")

    def _handle_toggle_absence(self, absent: bool) -> None:
        if self._node is None:
            return
        self._node.set_absence(absent)

    def _handle_reply_from_receive(self, event: IncomingMessageEvent) -> None:
        if self._send_window is None:
            return
        self._send_window.select_peer_by_ip(event.sender_ip)
        self._send_window.present()
        self._send_window.message_text_view.grab_focus()

    def _handle_ack_close_from_receive(self, event: IncomingMessageEvent) -> None:
        if self._node is None:
            return
        self._node.send_read_notice(
            target_ip=event.sender_ip,
            target_port=event.sender_port,
            packet_no=event.packet_no,
        )

    def _handle_download_attachment(
        self,
        event: IncomingMessageEvent,
        spec: FileAttachmentSpec,
        dest_path: Path,
    ) -> None:
        node = self._node
        if node is None:
            return

        def worker() -> None:
            try:
                saved = node.download_attachment(
                    host_ip=event.sender_ip,
                    packet_no=event.packet_no,
                    file_id=spec.file_id,
                    dest_path=dest_path,
                )
                GLib.idle_add(
                    self._set_status_ui,
                    f"保存完了: {saved.name}",
                    priority=GLib.PRIORITY_DEFAULT,
                )
            except Exception as exc:
                GLib.idle_add(
                    self._set_status_ui,
                    f"添付保存エラー: {exc}",
                    priority=GLib.PRIORITY_DEFAULT,
                )

        threading.Thread(target=worker, daemon=True).start()

    def _set_status_ui(self, text: str) -> int:
        if self._send_window is not None:
            self._send_window.set_status(text)
        return GLib.SOURCE_REMOVE

    def _handle_open_history(self) -> None:
        parent = self._send_window
        if parent is None:
            return

        dlg = Gtk.Window(transient_for=parent, title="送受信履歴")
        dlg.set_default_size(640, 400)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)

        scroller = Gtk.ScrolledWindow(vexpand=True, has_frame=True)
        tv = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        tv.add_css_class("ipmsg-message-view")

        entries = self._history.list_entries(limit=100)
        lines: list[str] = []
        for e in entries:
            dt = datetime.fromtimestamp(e.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            arrow = "送信 ->" if e.direction == "sent" else "受信 <-"
            flags: list[str] = [e.status]
            if e.sealed:
                flags.append("封書")
            if e.has_attachments:
                flags.append("添付あり")
            lines.append(
                f"[{dt}] {arrow} {e.peer_user}@{e.peer_host} ({e.peer_ip}) [{' / '.join(flags)}]\n"
                f"{e.body}\n"
                f"{'-' * 60}"
            )
        tv.get_buffer().set_text("\n".join(lines) if lines else "履歴はありません。")
        scroller.set_child(tv)

        close_btn = Gtk.Button(label="閉じる", halign=Gtk.Align.END)
        close_btn.connect("clicked", lambda _b: dlg.close())

        box.append(scroller)
        box.append(close_btn)
        dlg.set_child(box)
        dlg.present()

    def _handle_open_settings(self) -> None:
        parent = self._send_window
        if parent is None:
            return

        win = SettingsWindow(
            transient_for=parent,
            current=self._settings,
            on_save=self._apply_new_settings,
        )
        win.present()

    def _apply_new_settings(self, new_settings: AppSettings) -> None:
        self._settings = new_settings
        _save_settings(new_settings)
        self._start_node()
        if self._send_window is not None:
            self._send_window.update_peers(self._node.get_peers() if self._node else [])
            self._send_window.set_status("設定を保存して再起動しました。")


def main() -> int:
    app = IpmsgGtk4Application()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())