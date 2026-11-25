#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "エラー: /usr/local へのインストールには管理者権限が必要です。"
  echo "実行例: sudo $0 $*"
  exit 1
fi

INSTALL_SKEL_DESKTOP=0
SKIP_DEPS=0

for arg in "$@"; do
  case "${arg}" in
    --skel-desktop)
      INSTALL_SKEL_DESKTOP=1
      ;;
    --no-deps|--skip-deps)
      SKIP_DEPS=1
      ;;
    -h|--help)
      echo "使い方: sudo $0 [--skel-desktop] [--no-deps]"
      exit 0
      ;;
    *)
      echo "不明なオプション: ${arg}"
      exit 1
      ;;
  esac
done

install_dependencies() {
  if [[ "${SKIP_DEPS}" -eq 1 ]]; then
    echo "[依存関係] --no-deps が指定されたためスキップします。"
    return 0
  fi

  if [[ ! -r /etc/os-release ]]; then
    echo "警告: /etc/os-release が見つかりません。依存関係の自動インストールをスキップします。"
    return 0
  fi

  # shellcheck disable=SC1091
  . /etc/os-release

  local distro_id="${ID:-unknown}"
  local distro_like="${ID_LIKE:-}"
  local match_tokens=" ${distro_id} ${distro_like} "

  echo "[0/4] ディストリビューション (${PRETTY_NAME:-${distro_id}}) の依存関係をインストール中..."

  if [[ "${match_tokens}" == *" ubuntu "* || "${match_tokens}" == *" debian "* || "${match_tokens}" == *" linuxmint "* || "${match_tokens}" == *" pop "* ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y --no-install-recommends \
      python3 \
      python3-gi \
      gir1.2-gtk-4.0 \
      libgtk-4-1 \
      desktop-file-utils \
      libgtk-4-bin

  elif [[ "${match_tokens}" == *" fedora "* || "${match_tokens}" == *" rhel "* || "${match_tokens}" == *" centos "* || "${match_tokens}" == *" rocky "* || "${match_tokens}" == *" almalinux "* ]]; then
    dnf install -y \
      python3 \
      python3-gobject \
      gtk4 \
      desktop-file-utils

  elif [[ "${match_tokens}" == *" opensuse-leap "* || "${match_tokens}" == *" opensuse-tumbleweed "* || "${match_tokens}" == *" suse "* ]]; then
    zypper --non-interactive refresh
    zypper --non-interactive install --no-recommends \
      python3 \
      python3-gobject \
      python3-gobject-Gdk \
      typelib-1_0-Gtk-4_0 \
      libgtk-4-1 \
      desktop-file-utils

  elif [[ "${match_tokens}" == *" arch "* || "${match_tokens}" == *" manjaro "* || "${match_tokens}" == *" endeavour"* ]]; then
    pacman -Sy --noconfirm --needed \
      python \
      python-gobject \
      gtk4 \
      desktop-file-utils

  elif [[ "${match_tokens}" == *" alpine "* ]]; then
    apk update
    apk add --no-cache \
      python3 \
      py3-gobject3 \
      gtk4.0 \
      desktop-file-utils

  else
    echo "警告: 未対応のディストリビューション (${distro_id}) です。GTK4 と PyGObject を手動でインストールしてください。"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_ID="org.example.IpmsgLinuxGtk4"
APP_DIR="/usr/local/share/ipmsg-linux-gtk4"
BIN_PATH="/usr/local/bin/ipmsg-gtk4"
ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"
APPS_DIR="/usr/local/share/applications"
DESKTOP_SRC="${SCRIPT_DIR}/${APP_ID}.desktop"
ICON_SRC="${SCRIPT_DIR}/ipmsg-gtk4.svg"

install_dependencies

echo "[1/4] アプリケーションファイルを ${APP_DIR} へ配置中..."
rm -rf "${APP_DIR}"
mkdir -p "${APP_DIR}" "${ICON_DIR}" "${APPS_DIR}"

cp -r "${PROJECT_ROOT}/ipmsg_core" "${APP_DIR}/"
cp -r "${PROJECT_ROOT}/lipmsg" "${APP_DIR}/"

find "${APP_DIR}" -type d -exec chmod 0755 {} +
find "${APP_DIR}" -type f -exec chmod 0644 {} +

echo "[2/4] 起動コマンド ${BIN_PATH} を作成中..."
cat > "${BIN_PATH}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${APP_DIR}\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m lipmsg.app "\$@"
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