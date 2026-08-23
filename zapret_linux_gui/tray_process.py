"""Иконка в трее — отдельный процесс.

Почему не в основном: AppIndicator (Ayatana/libappindicator) собран под GTK3 и требует
Gtk.Menu, которого в GTK4 вообще нет. А загрузить GTK3 и GTK4 в один процесс нельзя:
gi сразу ругается, а если обойти проверку — падает всё приложение.

Поэтому трей живёт в дочернем процессе с GTK3 и общается с основным строками:

  в stdin приходит:  {"running": true, "strategy": "..."}
  в stdout уходит:   ready | unavailable: <причина> | show | toggle | quit
"""

from __future__ import annotations

import json
import sys
import threading


def emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")

        # Ayatana — активно поддерживаемый форк; AppIndicator3 остался в старых дистрибутивах.
        indicator_module = None
        for name, version in (("AyatanaAppIndicator3", "0.1"), ("AppIndicator3", "0.1")):
            try:
                gi.require_version(name, version)
                indicator_module = name
                break
            except ValueError:
                continue

        if indicator_module is None:
            emit("unavailable: не найден AyatanaAppIndicator3")
            return 0

        from gi.repository import GLib, Gtk
        from gi.repository import __getattr__ as _  # noqa: F401 - только для ясности

        indicator_lib = __import__(f"gi.repository.{indicator_module}", fromlist=[indicator_module])
    except (ImportError, ValueError) as exc:
        emit(f"unavailable: {exc}")
        return 0

    AppIndicator = indicator_lib

    indicator = AppIndicator.Indicator.new(
        "io.github.talex00.ZapretLinuxGui",
        "io.github.talex00.ZapretLinuxGui",
        AppIndicator.IndicatorCategory.SYSTEM_SERVICES,
    )
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    # Если своя иконка ещё не установлена в тему, пусть будет системная, а не пустота.
    indicator.set_icon_full("network-vpn-symbolic", "Zapret Linux GUI")

    menu = Gtk.Menu()

    state_item = Gtk.MenuItem(label="Обход остановлен")
    state_item.set_sensitive(False)
    menu.append(state_item)
    menu.append(Gtk.SeparatorMenuItem())

    show_item = Gtk.MenuItem(label="Открыть Zapret Linux GUI")
    show_item.connect("activate", lambda *_: emit("show"))
    menu.append(show_item)

    toggle_item = Gtk.MenuItem(label="Включить обход")
    toggle_item.connect("activate", lambda *_: emit("toggle"))
    menu.append(toggle_item)

    menu.append(Gtk.SeparatorMenuItem())

    quit_item = Gtk.MenuItem(label="Выйти")
    quit_item.connect("activate", lambda *_: emit("quit"))
    menu.append(quit_item)

    menu.show_all()
    indicator.set_menu(menu)
    indicator.set_secondary_activate_target(show_item)

    def apply_state(running: bool, strategy: str | None) -> bool:
        if running:
            state_item.set_label(f"Работает: {strategy}" if strategy else "Обход работает")
            toggle_item.set_label("Выключить обход")
            indicator.set_title("Zapret Linux GUI — обход работает")
        else:
            state_item.set_label("Обход остановлен")
            toggle_item.set_label("Включить обход")
            indicator.set_title("Zapret Linux GUI — остановлен")
        return False

    def reader() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            GLib.idle_add(
                apply_state, bool(payload.get("running")), payload.get("strategy")
            )
        # Основной процесс завершился — иконка больше не нужна.
        GLib.idle_add(Gtk.main_quit)

    threading.Thread(target=reader, daemon=True).start()

    emit("ready")
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
