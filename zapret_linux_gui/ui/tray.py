"""Сторона основного процесса: запуск трея и обмен сообщениями с ним."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Callable

from gi.repository import GLib

from ..log import log


class Tray:
    def __init__(
        self,
        on_show: Callable[[], None],
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self.on_show = on_show
        self.on_toggle = on_toggle
        self.on_quit = on_quit

        self.available = False
        self._process: subprocess.Popen | None = None
        self._last_state: tuple[bool, str | None] | None = None

        self._spawn()

    # ------------------------------------------------------------------ запуск

    def _spawn(self) -> None:
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "zapret_linux_gui.tray_process"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            log.warn(f"Трей недоступен: {exc}")
            self._process = None
            return

        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for raw in process.stdout:
            line = raw.strip()
            if not line:
                continue
            GLib.idle_add(self._handle, line)

        GLib.idle_add(self._handle, "__exit__")

    # ------------------------------------------------------------------ события

    def _handle(self, line: str) -> bool:
        if line == "ready":
            self.available = True
            log.debug("Иконка в трее активна")
            # Состояние могло обновиться до того, как трей был готов.
            if self._last_state is not None:
                running, strategy = self._last_state
                self._last_state = None
                self.update(running, strategy)
        elif line.startswith("unavailable"):
            self.available = False
            log.warn(
                "Трей недоступен ("
                + line.partition(":")[2].strip()
                + "). Установите gir1.2-ayatanaappindicator3-0.1 или libayatana-appindicator-gtk3; "
                "пока закрытие окна будет завершать приложение."
            )
        elif line == "show":
            self.on_show()
        elif line == "toggle":
            self.on_toggle()
        elif line == "quit":
            self.on_quit()
        elif line == "__exit__":
            self.available = False

        return False

    # ------------------------------------------------------------------ состояние

    def update(self, running: bool, strategy: str | None) -> None:
        state = (running, strategy)
        if state == self._last_state:
            return
        self._last_state = state

        process = self._process
        if process is None or process.stdin is None or not self.available:
            return

        try:
            process.stdin.write(json.dumps({"running": running, "strategy": strategy}) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, ValueError):
            self.available = False

    def shutdown(self) -> None:
        process = self._process
        self._process = None
        self.available = False

        if process is None:
            return

        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
