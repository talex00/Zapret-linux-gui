"""Главное окно.

Два режима, как в Windows-версии:

* простой — только главная, узкое окно без фиксированной высоты;
* расширенный — переключатель страниц и нормальный размер.

Про размер: в WPF пришлось включать SizeToContent и вручную центрировать окно.
GTK4 сам берёт натуральный размер, если не задавать высоту, поэтому здесь достаточно
set_default_size(width, -1). Серого фона вокруг карточки тоже нет: в простом режиме
содержимое и есть окно.
"""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import APP_NAME
from ..log import log
from ..runner import runner
from ..settings import settings
from .advanced import LogPage, SettingsPage, StrategiesPage, TestingPage
from .home import HomePage
from .tray import Tray

SIMPLE_WIDTH = 400
ADVANCED_SIZE = (900, 640)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application) -> None:
        super().__init__(application=application, title=APP_NAME)

        self.advanced = False
        self._tray_hint_shown = False
        self._quitting = False

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.stack = Adw.ViewStack()
        self.home = HomePage(self)
        self.strategies_page = StrategiesPage(self)
        self.testing_page = TestingPage(self)
        self.log_page = LogPage(self)
        self.settings_page = SettingsPage(self)

        self.stack.add_titled_with_icon(self.home, "home", "Главная", "go-home-symbolic")
        self.stack.add_titled_with_icon(
            self.strategies_page, "strategies", "Стратегии", "view-list-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.testing_page, "testing", "Тестирование", "speedometer-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.log_page, "log", "Журнал", "utilities-terminal-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.settings_page, "settings", "Настройки", "emblem-system-symbolic"
        )

        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.window_title = Adw.WindowTitle(title=APP_NAME)

        self.mode_button = Gtk.ToggleButton(
            icon_name="view-more-symbolic", tooltip_text="Расширенный режим"
        )
        self.mode_button.connect("toggled", self._on_mode_toggled)

        self.header = Adw.HeaderBar(title_widget=self.window_title)
        self.header.pack_end(self.mode_button)

        toolbar = Adw.ToolbarView(content=self.stack)
        toolbar.add_top_bar(self.header)
        self.toasts.set_child(toolbar)

        self.tray = Tray(
            on_show=self._tray_show,
            on_toggle=self._tray_toggle,
            on_quit=self._tray_quit,
        )

        self.connect("close-request", self._on_close_request)

        runner.subscribe(lambda: GLib.idle_add(self.refresh))
        log.subscribe(lambda: GLib.idle_add(self.log_page.refresh))

        self._apply_mode()
        self.refresh()

        # Обход может выключиться извне (служба, другой экземпляр, краш nfqws),
        # поэтому состояние нужно опрашивать, а не только доверять своим событиям.
        GLib.timeout_add_seconds(3, self._poll)

    # ------------------------------------------------------------------ режимы

    def _on_mode_toggled(self, button: Gtk.ToggleButton) -> None:
        self.advanced = button.get_active()
        self._apply_mode()

    def _apply_mode(self) -> None:
        if self.advanced:
            self.header.set_title_widget(self.switcher)
            self.set_size_request(560, 420)
            self.set_default_size(*ADVANCED_SIZE)
            self.mode_button.set_tooltip_text("Простой режим")
        else:
            self.stack.set_visible_child(self.home)
            self.header.set_title_widget(self.window_title)
            # Сначала снимаем минимум от расширенного режима, иначе он не даст
            # окну сжаться до содержимого.
            self.set_size_request(-1, -1)
            self.set_default_size(SIMPLE_WIDTH, -1)
            GLib.idle_add(self._shrink_to_content)
            self.mode_button.set_tooltip_text("Расширенный режим")

        self.home.set_compact(not self.advanced)
        self.refresh()

    def _shrink_to_content(self) -> bool:
        # После возврата из расширенного режима окно остаётся большим: явно просим
        # GTK пересчитать размер по натуральному.
        if not self.advanced and not self.is_maximized() and not self.is_fullscreen():
            _, natural = self.get_preferred_size()
            self.set_default_size(SIMPLE_WIDTH, natural.height)
        return False

    # ------------------------------------------------------------------ обновление

    def _poll(self) -> bool:
        self.refresh()
        return True

    def refresh(self) -> bool:
        status = runner.status()

        self.home.refresh(status)
        self.strategies_page.refresh(status)
        self.settings_page.refresh()

        name = runner.current_strategy_name()
        self.tray.update(status.running, name)

        if status.running:
            suffix = f" — {name}" if name else ""
            self.window_title.set_subtitle(f"Обход работает{suffix}")
        else:
            self.window_title.set_subtitle("Обход остановлен")

        if not self.advanced:
            GLib.idle_add(self._shrink_to_content)

        return False

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def go_to(self, name: str) -> None:
        if not self.advanced:
            self.mode_button.set_active(True)
        self.stack.set_visible_child_name(name)

    # ------------------------------------------------------------------ трей и закрытие

    def _on_close_request(self, *_args) -> bool:
        if self._quitting or not settings.close_to_tray or not self.tray.available:
            self.shutdown()
            return False

        self.set_visible(False)

        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            log.info(
                "Окно скрыто в трей. Управление обходом и выход — по правой кнопке мыши."
            )

        return True

    def _tray_show(self) -> None:
        self.set_visible(True)
        self.present()

    def _tray_toggle(self) -> None:
        self.home.toggle_bypass()

    def _tray_quit(self) -> None:
        self._quitting = True
        self.shutdown()
        app = self.get_application()
        if app is not None:
            app.quit()

    def shutdown(self) -> None:
        """Обход сознательно не выключается: демон живёт отдельно от GUI, и гасить его
        при выходе значило бы терять связь каждый раз и снова спрашивать пароль.
        Остановка обхода — явное действие пользователя."""
        self.tray.shutdown()
