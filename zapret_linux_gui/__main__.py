from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    try:
        from .app import App
    except (ImportError, ValueError) as exc:
        # Типичная первая ошибка на чистой системе — нет PyGObject или GTK4/libadwaita.
        # Трейсбек тут бесполезен, нужна команда установки.
        print(f"Не удалось загрузить GTK4/libadwaita: {exc}\n", file=sys.stderr)
        print(
            "Установите зависимости:\n"
            "  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita\n"
            "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            "  Arch:          sudo pacman -S python-gobject gtk4 libadwaita",
            file=sys.stderr,
        )
        return 1

    return App().run(argv)


if __name__ == "__main__":
    sys.exit(main())
