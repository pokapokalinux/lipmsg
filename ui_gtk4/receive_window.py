from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, Gtk

from ipmsg_core import FileAttachmentSpec, IncomingMessageEvent


@Gtk.Template(filename=str(Path(__file__).with_name("receive_window.ui")))
class ReceiveWindow(Gtk.Window):
    __gtype_name__ = "IpmsgReceiveWindow"

    sender_label: Gtk.Label = Gtk.Template.Child()
    meta_label: Gtk.Label = Gtk.Template.Child()
    seal_box: Gtk.Box = Gtk.Template.Child()
    open_seal_button: Gtk.Button = Gtk.Template.Child()
    body_text_view: Gtk.TextView = Gtk.Template.Child()
    attachments_box: Gtk.Box = Gtk.Template.Child()
    reply_button: Gtk.Button = Gtk.Template.Child()
    close_ack_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        *,
        transient_for: Gtk.Window,
        event: IncomingMessageEvent,
        on_reply: Callable[[IncomingMessageEvent], None],
        on_ack_close: Callable[[IncomingMessageEvent], None],
        on_download_attachment: Callable[[IncomingMessageEvent, FileAttachmentSpec, Path], None],
    ) -> None:
        super().__init__(transient_for=transient_for)
        self._event = event
        self._on_reply = on_reply
        self._on_ack_close = on_ack_close
        self._on_download_attachment = on_download_attachment

        self.set_title(f"受信 - {event.user} ({event.host})")

        grp = f" [{event.group}]" if event.group else ""
        self.sender_label.set_text(f"送信元: {event.user}{grp}@{event.host} ({event.sender_ip})")

        tags: list[str] = [f"Packet #{event.packet_no}"]
        if event.sealed:
            tags.append("封書")
        if event.auto_reply:
            tags.append("自動応答")
        if event.attachments:
            tags.append(f"添付 {len(event.attachments)} 件")
        self.meta_label.set_text(" / ".join(tags))

        if event.sealed:
            self.seal_box.set_visible(True)
            self._set_body("")
        else:
            self._set_body(event.body)

        if event.attachments:
            self._build_attachment_rows(event.attachments)

        self.open_seal_button.connect("clicked", self._on_open_seal_clicked)
        self.reply_button.connect("clicked", self._on_reply_clicked)
        self.close_ack_button.connect("clicked", self._on_close_ack_clicked)

    def _set_body(self, text: str) -> None:
        self.body_text_view.get_buffer().set_text(text)

    def _on_open_seal_clicked(self, _btn: Gtk.Button) -> None:
        self.seal_box.set_visible(False)
        self._set_body(self._event.body)

    def _build_attachment_rows(self, specs: list[FileAttachmentSpec]) -> None:
        self.attachments_box.set_visible(True)
        title = Gtk.Label(label="添付ファイル", xalign=0.0)
        title.add_css_class("ipmsg-section-label")
        self.attachments_box.append(title)

        for spec in specs:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(
                label=f"{spec.name} ({spec.size:,} bytes)",
                xalign=0.0,
                hexpand=True,
                ellipsize=3,
            )
            save_btn = Gtk.Button(label="保存")
            save_btn.connect("clicked", lambda _b, s=spec: self._prompt_save(s))
            row.append(lbl)
            row.append(save_btn)
            self.attachments_box.append(row)

    def _prompt_save(self, spec: FileAttachmentSpec) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title("添付ファイルの保存先")
        dialog.set_initial_name(spec.name)
        dialog.save(self, None, lambda d, res: self._on_save_chosen(d, res, spec))

    def _on_save_chosen(
        self,
        dialog: Gtk.FileDialog,
        result: Gio.AsyncResult,
        spec: FileAttachmentSpec,
    ) -> None:
        try:
            gfile = dialog.save_finish(result)
        except Exception:
            return
        if gfile is None:
            return
        dest = gfile.get_path()
        if dest:
            self._on_download_attachment(self._event, spec, Path(dest))

    def _on_reply_clicked(self, _btn: Gtk.Button) -> None:
        self._on_reply(self._event)

    def _on_close_ack_clicked(self, _btn: Gtk.Button) -> None:
        self._on_ack_close(self._event)
        self.close()