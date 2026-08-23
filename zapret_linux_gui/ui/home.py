"""Главная страница — тот самый сценарий прогрессивного раскрытия.

Нет папки — видна только папка. Есть папка — появляются стратегия и кнопка запуска.
Всё объясняющее — в «Подробностях», чтобы окно оставалось маленьким.
Автоподбор — вторичное действие: если человек знает свою стратегию, тест ему не нужен.
"""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from ..log import log
from ..runner import Status, runner
from ..settings import settings
from ..strategies import catalog


class HomePage(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.window = window
        self._busy = False
        self._strategy_ids: list[str] = []

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # --- папка zapret ---------------------------------------------------
        self.path_group = Adw.PreferencesGroup()
        self.path_row = Adw.ActionRow(title="Папка zapret", subtitle="Не выбрана")
        choose = Gtk.Button(label="Выбрать…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._on_choose_folder)
        self.path_row.add_suffix(choose)
        self.path_group.add(self.path_row)
        self.append(self.path_group)

        # --- состояние -----------------------------------------------------
        self.state_label = Gtk.Label(xalign=0.0, wrap=True)
        self.state_label.add_css_class("heading")
        self.append(self.state_label)

        # --- стратегия ------------------------------------------------------
        self.strategy_model = Gtk.StringList()
        self.strategy_combo = Gtk.DropDown(model=self.strategy_model, hexpand=True)
        self.strategy_combo.connect("notify::selected", self._on_strategy_changed)
        self.append(self.strategy_combo)

        # --- главное действие -----------------------------------------------
        self.action_button = Gtk.Button(label="Запустить обход")
        self.action_button.add_css_class("suggested-action")
        self.action_button.add_css_class("pill")
        self.action_button.connect("clicked", lambda *_: self.toggle_bypass())
        self.append(self.action_button)

        # --- автоподбор ----------------------------------------------------
        self.autopick_button = Gtk.Button(label="Не знаю стратегию — подобрать")
        self.autopick_button.add_css_class("flat")
        self.autopick_button.connect("clicked", lambda *_: self.window.go_to("testing"))
        self.append(self.autopick_button)

        # --- подробности ----------------------------------------------------
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        details_box.set_margin_top(6)

        self.description_label = Gtk.Label(xalign=0.0, wrap=True)
        self.description_label.add_css_class("dim-label")
        details_box.append(self.description_label)

        self.source_label = Gtk.Label(xalign=0.0, wrap=True)
        self.source_label.add_css_class("dim-label")
        self.source_label.add_css_class("caption")
        details_box.append(self.source_label)

        self.args_label = Gtk.Label(xalign=0.0, wrap=True, selectable=True)
        self.args_label.add_css_class("caption")
        self.args_label.add_css_class("log-view")
        details_box.append(self.args_label)

        open_log = Gtk.Button(label="Открыть журнал", halign=Gtk.Align.START)
        open_log.add_css_class("flat")
        open_log.connect("clicked", lambda *_: self.window.go_to("log"))
        details_box.append(open_log)

        self.details = Gtk.Expander(label="Подробности", child=details_box)
        self.append(self.details)

    # ------------------------------------------------------------------ режим

    def set_compact(self, compact: bool) -> None:
        # В расширенном режиме главная не должна растягиваться на всю ширину:
        # кнопка шириной в 900 пикселей выглядит странно.
        self.set_halign(Gtk.Align.FILL if compact else Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START if compact else Gtk.Align.CENTER)
        self.set_size_request(-1 if compact else 460, -1)

    # ------------------------------------------------------------------ действия

    def _on_choose_folder(self, *_args) -> None:
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Выберите папку zapret")
            dialog.select_folder(self.window, None, self._on_folder_chosen)
            return

        # GTK старше 4.10: FileDialog ещё нет.
        chooser = Gtk.FileChooserNative(
            title="Выберите папку zapret",
            transient_for=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Выбрать",
            cancel_label="Отмена",
        )

        def on_response(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                folder = dlg.get_file()
                if folder is not None:
                    self._set_path(folder.get_path())
            dlg.destroy()

        chooser.connect("response", on_response)
        chooser.show()

    def _on_folder_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # отмена — не ошибка
        if folder is not None:
            self._set_path(folder.get_path())

    def _set_path(self, path: str | None) -> None:
        if not path:
            return
        settings.zapret_path = path
        settings.save()
        layout = runner.refresh_layout()
        if layout.is_valid:
            log.success(f"Папка zapret: {path}")
        elif layout.error:
            log.error(layout.error)
        self.window.refresh()

    def _on_strategy_changed(self, *_args) -> None:
        index = self.strategy_combo.get_selected()
        if 0 <= index < len(self._strategy_ids):
            strategy_id = self._strategy_ids[index]
            if strategy_id != settings.selected_strategy_id:
                settings.selected_strategy_id = strategy_id
                settings.save()
                self._update_details()

    def selected_strategy(self):
        index = self.strategy_combo.get_selected()
        if 0 <= index < len(self._strategy_ids):
            return catalog.find_by_id(self._strategy_ids[index])
        return None

    def toggle_bypass(self) -> None:
        if self._busy:
            return

        running = runner.is_running
        strategy = None if running else self.selected_strategy()

        if not running and strategy is None:
            self.window.toast("Сначала укажите папку zapret.")
            return

        self._busy = True
        self.action_button.set_sensitive(False)
        self.action_button.set_label("Останавливаю…" if running else "Запускаю…")

        def work() -> None:
            # pkexec показывает диалог пароля и блокирует вызов на неопределённое время,
            # так что делать это в UI-потоке нельзя — окно замёрзнет.
            ok = runner.stop() if running else runner.start(strategy)
            error = runner.last_error
            GLib.idle_add(self._finish_toggle, ok, error)

        threading.Thread(target=work, daemon=True).start()

    def _finish_toggle(self, ok: bool, error: str | None) -> bool:
        self._busy = False
        self.action_button.set_sensitive(True)
        if not ok and error:
            self.window.toast(error)
        self.window.refresh()
        return False

    # ------------------------------------------------------------------ обновление

    def refresh(self, status: Status) -> None:
        layout = runner.layout
        valid = layout.is_valid

        # Папка нужна на виду только пока она не настроена или сломана.
        self.path_group.set_visible(not valid)
        if not valid:
            self.path_row.set_subtitle(layout.error or "Не выбрана")

        self._reload_strategies(valid)

        has_strategies = bool(self._strategy_ids)
        self.strategy_combo.set_visible(valid and has_strategies and not status.running)
        self.action_button.set_visible(valid and has_strategies)
        self.details.set_visible(valid and has_strategies)

        # Автоподбор предлагаем только тем, кто ещё не нашёл рабочую стратегию.
        self.autopick_button.set_visible(
            valid and has_strategies and not status.running and not settings.last_best_strategy
        )

        if status.running:
            name = runner.current_strategy_name() or status.strategy_name or ""
            self.state_label.set_text(f"Обход работает{f': {name}' if name else ''}")
            self.state_label.remove_css_class("state-stopped")
            self.state_label.add_css_class("state-running")
            self.action_button.set_label("Остановить обход")
            self.action_button.remove_css_class("suggested-action")
            self.action_button.add_css_class("destructive-action")
        else:
            if valid and has_strategies:
                self.state_label.set_text(
                    f"{layout.label} · стратегий: {len(self._strategy_ids)}"
                )
            elif valid:
                self.state_label.set_text("Не найдено ни одной стратегии")
            else:
                self.state_label.set_text("Укажите папку zapret")
            self.state_label.remove_css_class("state-running")
            self.state_label.add_css_class("state-stopped")
            self.action_button.set_label("Запустить обход")
            self.action_button.remove_css_class("destructive-action")
            self.action_button.add_css_class("suggested-action")

        self._update_details()

    def _reload_strategies(self, valid: bool) -> None:
        if not valid:
            if self._strategy_ids:
                self.strategy_model.splice(0, self.strategy_model.get_n_items(), [])
                self._strategy_ids = []
            return

        strategies = catalog.all
        ids = [s.id for s in strategies]
        if ids == self._strategy_ids:
            return

        self._strategy_ids = ids
        self.strategy_model.splice(
            0, self.strategy_model.get_n_items(), [s.name for s in strategies]
        )

        target = settings.selected_strategy_id
        if target in ids:
            self.strategy_combo.set_selected(ids.index(target))
        elif ids:
            self.strategy_combo.set_selected(0)

    def _update_details(self) -> None:
        strategy = catalog.find_by_id(settings.selected_strategy_id) or self.selected_strategy()

        if strategy is None:
            self.description_label.set_text("")
            self.args_label.set_text("")
            self.source_label.set_text("")
            return

        self.description_label.set_text(strategy.description)
        self.args_label.set_text("nfqws " + " ".join(strategy.args))

        source = f"Источник: {catalog.source_description}"
        if settings.last_best_strategy:
            source += f"\nПрошлая проверка выбрала «{settings.last_best_strategy}»"
        self.source_label.set_text(source)
