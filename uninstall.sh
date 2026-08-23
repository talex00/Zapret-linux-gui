#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"

if [ "$(id -u)" != "0" ]; then
  echo "Запустите через sudo: sudo ./uninstall.sh" >&2
  exit 1
fi

# Сначала снимаем правила и демона: иначе удалённое приложение оставит
# после себя работающий nfqws и nft-таблицу, которые нечем будет выключить.
HELPER="$PREFIX/lib/zapret-linux-gui/zapret_linux_gui/privileged_helper.py"
if [ -x "$HELPER" ]; then
  python3 "$HELPER" stop || true
fi

rm -rf "$PREFIX/lib/zapret-linux-gui"
rm -f "$PREFIX/bin/zapret-linux-gui"
rm -f "$PREFIX/share/applications/io.github.talex00.ZapretLinuxGui.desktop"
rm -f "$PREFIX/share/icons/hicolor/scalable/apps/io.github.talex00.ZapretLinuxGui.svg"
rm -f "/usr/share/polkit-1/actions/io.github.talex00.ZapretLinuxGui.policy"

echo "Удалено. Настройки остались в ~/.config/zapret-linux-gui"
