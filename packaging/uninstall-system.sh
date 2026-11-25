#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "エラー: /usr/local からのアンインストールには管理者権限が必要です。"
  echo "実行例: sudo $0"
  exit 1
fi

APP_ID="org.example.IpmsgLinuxGtk4"
APP_DIR="/usr/local/share/ipmsg-linux-gtk4"
BIN_PATH="/usr/local/bin/ipmsg-gtk4"
ICON_PATH="/usr/local/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
APPS_DESKTOP_PATH="/usr/local/share/applications/${APP_ID}.desktop"
SKEL_DESKTOP_PATH="/etc/skel/Desktop/${APP_ID}.desktop"

echo "[1/3] 実行コマンド・アプリ本体・アイコン・エントリを削除中..."
rm -f "${BIN_PATH}" "${ICON_PATH}" "${APPS_DESKTOP_PATH}" "${SKEL_DESKTOP_PATH}"
rm -rf "${APP_DIR}"

echo "[2/3] デスクトップ・アイコンキャッシュを更新中..."
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/local/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/local/share/applications || true
fi

echo "[3/3] 完了案内"
echo "/usr/local からの削除が完了しました。"
echo "※ 各ユーザーのホームディレクトリ配下にある設定（~/.config/ipmsg-linux-gtk4）および"
echo "   履歴（~/.local/share/ipmsg-linux-gtk4/history.db）は保持されています。"