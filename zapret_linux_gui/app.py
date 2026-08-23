from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import APP_ID, APP_NAME  # noqa: E402
from .log import log  # noqa: E402
from .settings import settings  # noqa: E402

_SCHEMES = {
    "system": Adw.ColorScheme.DEFAULT,
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}


class App(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

    def do_startup(self) -> None:  # noqa: N802 - имя задано GObject
        Adw.Application.do_startup(self)
        self.apply_color_scheme()
        self._load_css()
        log.info(f"{APP_NAME} запущен")

    def do_activate(self) -> None:  # noqa: N802
        from .ui.window import MainWindow

        if self.window is None:
            self.window = MainWindow(application=self)
        self.window.present()

    def apply_color_scheme(self) -> None:
        Adw.StyleManager.get_default().set_color_scheme(
            _SCHEMES.get(settings.color_scheme, Adw.ColorScheme.DEFAULT)
        )

    def _load_css(self) -> None:
        css = Path(__file__).parent / "ui" / "style.css"
        display = Gdk.Display.get_default()
        if not css.is_file() or display is None:
            return

        provider = Gtk.CssProvider()
        provider.load_from_path(str(css))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_quit(self, *_args) -> None:
        if self.window is not None:
            self.window.shutdown()
        self.quit()
