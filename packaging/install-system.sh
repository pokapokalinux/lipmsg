#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "エラー: /usr/local へのインストールには管理者権限が必要です。"
  echo "実行例: sudo $0 $*"
  exit 1
fi

INSTALL_SKEL_DESKTOP=0
for arg in "$@"; do
  case "${arg}" in
    --skel-desktop)
      INSTALL_SKEL_DESKTOP=1
      ;;
    *)
      echo "不明なオプション: ${arg}"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_ID="org.example.IpmsgLinuxGtk4"
APP_DIR="/usr/local/share/ipmsg-linux-gtk4"
BIN_PATH="/usr/local/bin/ipmsg-gtk4"
ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"
APPS_DIR="/usr/local/share/applications"
DESKTOP_SRC="${SCRIPT_DIR}/${APP_ID}.desktop"
ICON_SRC="${SCRIPT_DIR}/ipmsg-gtk4.svg"

echo "[1/4] アプリケーションファイルを ${APP_DIR} へ配置中..."
rm -rf "${APP_DIR}"
mkdir -p "${APP_DIR}" "${ICON_DIR}" "${APPS_DIR}"

cp -r "${PROJECT_ROOT}/ipmsg_core" "${APP_DIR}/"
cp -r "${PROJECT_ROOT}/ui_gtk4" "${APP_DIR}/"

find "${APP_DIR}" -type d -exec chmod 0755 {} +
find "${APP_DIR}" -type f -exec chmod 0644 {} +

echo "[2/4] 起動コマンド ${BIN_PATH} を作成中..."
cat > "${BIN_PATH}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${APP_DIR}\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m ui_gtk4.app "\$@"
EOF
chmod 0755 "${BIN_PATH}"

echo "[3/4] アイコンと .desktop エントリを配置中..."
cp "${ICON_SRC}" "${ICON_DIR}/${APP_ID}.svg"
chmod 0644 "${ICON_DIR}/${APP_ID}.svg"

cp "${DESKTOP_SRC}" "${APPS_DIR}/${APP_ID}.desktop"
chmod 0644 "${APPS_DIR}/${APP_ID}.desktop"

if [[ "${INSTALL_SKEL_DESKTOP}" -eq 1 ]]; then
  mkdir -p /etc/skel/Desktop
  cp "${DESKTOP_SRC}" "/etc/skel/Desktop/${APP_ID}.desktop"
  chmod 0644 "/etc/skel/Desktop/${APP_ID}.desktop"
fi

echo "[4/4] デスクトップ・アイコンキャッシュを更新中..."
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/local/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "${APPS_DIR}" || true
fi

echo ""
echo "/usr/local へのシステムインストールが完了しました。"