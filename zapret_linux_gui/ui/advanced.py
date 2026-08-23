"""Страницы расширенного режима: стратегии, тестирование, журнал, настройки.

Сознательно используются только Adw.ActionRow и обычные GTK-виджеты вместо свежих
удобств вроде Adw.SpinRow/Adw.SwitchRow: они есть только в libadwaita 1.4+, а в Debian
и Mint встречаются версии постарше.
"""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from ..log import log
from ..runner import Status, runner
from ..settings import DEFAULT_TEST_DOMAINS, settings
from ..strategies import catalog
from ..tester import tester

_THEMES = ["system", "light", "dark"]


class StrategiesPage(Gtk.ScrolledWindow):
    def __init__(self, window) -> None:
        super().__init__(vexpand=True)
        self.window = window

        self.group = Adw.PreferencesGroup(
            title="Стратегии",
            description="Выберите стратегию и запустите её. Выбор запоминается.",
        )

        page = Adw.PreferencesPage()
        page.add(self.group)
        self.set_child(page)

        self._rows: list[Gtk.Widget] = []
        self._shown_ids: list[str] = []

    def refresh(self, status: Status) -> None:
        ids = [s.id for s in catalog.all]
        if ids == self._shown_ids:
            return

        for row in self._rows:
            self.group.remove(row)
        self._rows.clear()
        self._shown_ids = ids

        for strategy in catalog.all:
            row = Adw.ActionRow(title=strategy.name, subtitle=strategy.description)

            run = Gtk.Button(label="Запустить", valign=Gtk.Align.CENTER)
            run.add_css_class("flat")
            run.connect("clicked", self._on_run, strategy)
            row.add_suffix(run)

            self.group.add(row)
            self._rows.append(row)

    def _on_run(self, _button, strategy) -> None:
        settings.selected_strategy_id = strategy.id
        settings.save()

        def work() -> None:
            ok = runner.start(strategy)
            error = runner.last_error
            GLib.idle_add(self._done, ok, error)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, ok: bool, error: str | None) -> bool:
        if not ok and error:
            self.window.toast(error)
        self.window.refresh()
        return False


class TestingPage(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self._running = False

        self.set_margin_top(18)
        self.set_margin_bottom(18)
        self.set_margin_start(18)
        self.set_margin_end(18)

        header = Gtk.Label(
            xalign=0.0,
            wrap=True,
            label=(
                "Проверка по очереди включает каждую стратегию и пробует открыть тестовые "
                "домены. Во время прогона связь будет прерываться, и каждый запуск может "
                "запросить пароль — политика polkit помнит ответ несколько минут."
            ),
        )
        header.add_css_class("dim-label")
        self.append(header)

        self.start_button = Gtk.Button(label="Найти рабочую стратегию", halign=Gtk.Align.START)
        self.start_button.add_css_class("suggested-action")
        self.start_button.add_css_class("pill")
        self.start_button.connect("clicked", self._on_start)
        self.append(self.start_button)

        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        self.append(self.progress)

        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(Gtk.ScrolledWindow(vexpand=True, child=self.results_box))

    def _on_start(self, *_args) -> None:
        if self._running:
            tester.cancel.set()
            self.start_button.set_label("Отменяю…")
            return

        strategies = catalog.all
        if not strategies:
            self.window.toast("Нет стратегий для проверки.")
            return

        while (child := self.results_box.get_first_child()) is not None:
            self.results_box.remove(child)

        self._running = True
        self.start_button.set_label("Остановить проверку")
        self.progress.set_visible(True)
        self.progress.set_fraction(0.0)

        def work() -> None:
            tester.run(
                strategies,
                on_progress=lambda i, total, s: GLib.idle_add(self._on_progress, i, total, s),
                on_result=lambda outcome: GLib.idle_add(self._on_result, outcome),
            )
            GLib.idle_add(self._on_finished)

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, index: int, total: int, strategy) -> bool:
        self.progress.set_fraction((index - 1) / total if total else 0.0)
        self.progress.set_text(f"{index}/{total}: {strategy.name}")
        return False

    def _on_result(self, outcome) -> bool:
        name = outcome.strategy.name if outcome.strategy else "без обхода"
        row = Gtk.Label(xalign=0.0, wrap=True, label=f"{name} — {outcome.summary}")
        if outcome.error or outcome.successes == 0:
            row.add_css_class("state-error")
        elif outcome.success_rate == 1.0:
            row.add_css_class("state-running")
        self.results_box.append(row)
        return False

    def _on_finished(self) -> bool:
        self._running = False
        self.start_button.set_label("Найти рабочую стратегию")
        self.progress.set_visible(False)

        if tester.last_best is not None:
            self.window.toast(f"Лучшая стратегия: {tester.last_best.name}")
        else:
            self.window.toast("Ни одна стратегия не сработала.")

        self.window.refresh()
        return False


class LogPage(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window = window

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        clear = Gtk.Button(label="Очистить")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: log.clear())
        toolbar.append(clear)

        path_label = Gtk.Label(xalign=0.0, selectable=True, label=str(log.path))
        path_label.add_css_class("dim-label")
        path_label.add_css_class("caption")
        toolbar.append(path_label)

        self.append(toolbar)

        self.view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.view.add_css_class("log-view")
        self.append(Gtk.ScrolledWindow(vexpand=True, child=self.view))

        self.refresh()

    def refresh(self) -> bool:
        self.view.get_buffer().set_text("\n".join(entry.text for entry in log.entries()))
        return False


class SettingsPage(Gtk.ScrolledWindow):
    def __init__(self, window) -> None:
        super().__init__(vexpand=True)
        self.window = window
        self._loading = False

        page = Adw.PreferencesPage()

        # --- zapret ----------------------------------------------------------
        backend_group = Adw.PreferencesGroup(title="zapret")

        self.path_row = Adw.ActionRow(title="Папка zapret")
        choose = Gtk.Button(label="Изменить…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self.window.home.choose_folder())
        self.path_row.add_suffix(choose)
        backend_group.add(self.path_row)

        queue_row = Adw.ActionRow(
            title="Номер очереди NFQUEUE",
            subtitle="Смените, если параллельно работает штатная служба zapret",
        )
        self.queue_spin = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.queue_spin.set_valign(Gtk.Align.CENTER)
        self.queue_spin.connect("value-changed", self._on_queue_changed)
        queue_row.add_suffix(self.queue_spin)
        backend_group.add(queue_row)

        page.add(backend_group)

        # --- проверка ---------------------------------------------------------
        test_group = Adw.PreferencesGroup(title="Проверка")

        domains_row = Adw.ActionRow(
            title="Тестовые домены", subtitle="Через запятую"
        )
        self.domains_entry = Gtk.Entry(valign=Gtk.Align.CENTER, hexpand=True, width_chars=28)
        self.domains_entry.connect("activate", self._on_domains_changed)
        # Потеря фокуса тоже должна сохранять: иначе правка без Enter просто исчезнет.
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self._on_domains_changed())
        self.domains_entry.add_controller(focus)
        domains_row.add_suffix(self.domains_entry)

        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER)
        reset.set_tooltip_text("Вернуть стандартные")
        reset.add_css_class("flat")
        reset.connect("clicked", self._on_domains_reset)
        domains_row.add_suffix(reset)
        test_group.add(domains_row)

        timeout_row = Adw.ActionRow(title="Таймаут пробы", subtitle="Секунды")
        self.timeout_spin = Gtk.SpinButton.new_with_range(2, 30, 1)
        self.timeout_spin.set_valign(Gtk.Align.CENTER)
        self.timeout_spin.connect("value-changed", self._on_timeout_changed)
        timeout_row.add_suffix(self.timeout_spin)
        test_group.add(timeout_row)

        page.add(test_group)

        # --- интерфейс --------------------------------------------------------
        ui_group = Adw.PreferencesGroup(title="Интерфейс")

        self.theme_row = Adw.ComboRow(title="Тема")
        self.theme_row.set_model(Gtk.StringList.new(["Как в системе", "Светлая", "Тёмная"]))
        self.theme_row.connect("notify::selected", self._on_theme_changed)
        ui_group.add(self.theme_row)

        tray_row = Adw.ActionRow(
            title="Сворачивать в трей",
            subtitle="Крестик скрывает окно, а не закрывает приложение",
        )
        self.tray_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.tray_switch.connect("notify::active", self._on_tray_changed)
        tray_row.add_suffix(self.tray_switch)
        ui_group.add(tray_row)

        page.add(ui_group)
        self.set_child(page)

        self.refresh()

    # ------------------------------------------------------------------ чтение

    def refresh(self) -> None:
        self._loading = True
        try:
            layout = runner.layout
            self.path_row.set_subtitle(
                f"{settings.zapret_path} · {layout.label}"
                if settings.zapret_path
                else "Не выбрана"
            )
            self.queue_spin.set_value(settings.queue_num)
            self.timeout_spin.set_value(settings.probe_timeout)
            self.tray_switch.set_active(settings.close_to_tray)

            if not self.domains_entry.has_focus():
                self.domains_entry.set_text(", ".join(settings.test_domains))

            scheme = settings.color_scheme if settings.color_scheme in _THEMES else "system"
            self.theme_row.set_selected(_THEMES.index(scheme))
        finally:
            self._loading = False

    # ------------------------------------------------------------------ запись

    def _on_queue_changed(self, spin: Gtk.SpinButton) -> None:
        if self._loading:
            return
        value = int(spin.get_value())
        if value == settings.queue_num:
            return
        settings.queue_num = value
        settings.save()
        if runner.is_running:
            self.window.toast("Новый номер очереди применится после перезапуска обхода.")

    def _on_timeout_changed(self, spin: Gtk.SpinButton) -> None:
        if self._loading:
            return
        settings.probe_timeout = int(spin.get_value())
        settings.save()

    def _on_domains_changed(self, *_args) -> None:
        if self._loading:
            return
        domains = [d.strip() for d in self.domains_entry.get_text().split(",") if d.strip()]
        if not domains:
            domains = list(DEFAULT_TEST_DOMAINS)
            self.domains_entry.set_text(", ".join(domains))
        if domains == settings.test_domains:
            return
        settings.test_domains = domains
        settings.save()

    def _on_domains_reset(self, *_args) -> None:
        settings.test_domains = list(DEFAULT_TEST_DOMAINS)
        settings.save()
        self.domains_entry.set_text(", ".join(settings.test_domains))

    def _on_theme_changed(self, *_args) -> None:
        if self._loading:
            return
        index = self.theme_row.get_selected()
        if not 0 <= index < len(_THEMES):
            return
        settings.color_scheme = _THEMES[index]
        settings.save()

        app = self.window.get_application()
        if app is not None:
            app.apply_color_scheme()

    def _on_tray_changed(self, *_args) -> None:
        if self._loading:
            return
        settings.close_to_tray = self.tray_switch.get_active()
        settings.save()

        if settings.close_to_tray and not self.window.tray.available:
            self.window.toast("Иконка в трее недоступна — подробности в журнале.")
