"""Журнал в памяти плюс файл.

Записи приходят из рабочих потоков (тестер, чтение вывода помощника), поэтому
список прикрыт замком. Перевод в UI-поток — забота подписчика (GLib.idle_add).
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from .settings import state_dir

MAX_ENTRIES = 500


@dataclass(frozen=True)
class Entry:
    level: str
    message: str
    time: datetime

    @property
    def text(self) -> str:
        return f"[{self.time:%H:%M:%S}] {self.message}"


class _Log:
    def __init__(self) -> None:
        self._entries: deque[Entry] = deque(maxlen=MAX_ENTRIES)
        self._listeners: list[Callable[[], None]] = []
        self._lock = threading.RLock()
        self._file_broken = False

    @property
    def path(self):
        return state_dir() / "app.log"

    def subscribe(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def entries(self) -> list[Entry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        self._notify()

    def info(self, message: str) -> None:
        self._add("info", message)

    def warn(self, message: str) -> None:
        self._add("warn", message)

    def error(self, message: str) -> None:
        self._add("error", message)

    def success(self, message: str) -> None:
        self._add("success", message)

    def debug(self, message: str) -> None:
        self._add("debug", message)

    def lines(self, lines: Iterable[str], level: str = "debug") -> None:
        for line in lines:
            line = line.rstrip()
            if line:
                self._add(level, line)

    def _add(self, level: str, message: str) -> None:
        entry = Entry(level=level, message=message, time=datetime.now())
        with self._lock:
            self._entries.append(entry)
        self._write(entry)
        self._notify()

    def _write(self, entry: Entry) -> None:
        if self._file_broken:
            return
        try:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{entry.time:%Y-%m-%d %H:%M:%S} {entry.level:>7} {entry.message}\n")
        except OSError:
            # Недоступный для записи журнал не повод ломать работу; больше не пробуем.
            self._file_broken = True

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback()
            except Exception:  # noqa: BLE001 - подписчик не должен рвать журнал
                pass


log = _Log()
