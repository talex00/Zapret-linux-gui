#!/usr/bin/env bash
# Установка в /usr/local. Путь важен: тот же путь прописан в polkit-политике,
# а polkit сверяет его буквально.
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
LIBDIR="$PREFIX/lib/zapret-linux-gui"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" != "0" ]; then
  echo "Запустите через sudo: sudo ./install.sh" >&2
  exit 1
fi

if [ "$PREFIX" != "/usr/local" ]; then
  echo "Внимание: PREFIX не /usr/local — поправьте путь в .policy вручную." >&2
fi

echo "==> Копирую пакет в $LIBDIR"
rm -rf "$LIBDIR"
install -d "$LIBDIR"
cp -r "$SRC_DIR/zapret_linux_gui" "$LIBDIR/"
find "$LIBDIR" -name '__pycache__' -type d -exec rm -rf {} +

# Помощник запускается через pkexec напрямую, значит нужен бит исполнения
# и владелец root: pkexec откажется запускать файл, запись в который разрешена другим.
chown -R root:root "$LIBDIR"
chmod 755 "$LIBDIR/zapret_linux_gui/privileged_helper.py"

echo "==> Ставлю запускающий скрипт"
cat >"$PREFIX/bin/zapret-linux-gui" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="$LIBDIR\${PYTHONPATH:+:\$PYTHONPATH}" python3 -m zapret_linux_gui "\$@"
EOF
chmod 755 "$PREFIX/bin/zapret-linux-gui"

echo "==> Ставлю .desktop, иконку и polkit-политику"
install -Dm644 "$SRC_DIR/data/io.github.talex00.ZapretLinuxGui.desktop" \
  "$PREFIX/share/applications/io.github.talex00.ZapretLinuxGui.desktop"
install -Dm644 "$SRC_DIR/data/icons/io.github.talex00.ZapretLinuxGui.svg" \
  "$PREFIX/share/icons/hicolor/scalable/apps/io.github.talex00.ZapretLinuxGui.svg"
install -Dm644 "$SRC_DIR/data/io.github.talex00.ZapretLinuxGui.policy" \
  "/usr/share/polkit-1/actions/io.github.talex00.ZapretLinuxGui.policy"

command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" || true
command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database -q "$PREFIX/share/applications" || true

echo "Готово. Запуск: zapret-linux-gui"
