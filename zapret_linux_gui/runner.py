"""Запуск и остановка обхода.

Аналог ZapretRunner из Windows-версии, но с двумя отличиями:

* запуск и остановка идут через pkexec и привилегированный помощник;
* состояние читается из /run без root. Это важно: если бы опрос состояния требовал
  прав, приложение спрашивало бы пароль на каждом тике таймера.

Следствие последнего — приложение корректно подхватывает уже работающий обход после своего
перезапуска, а не стартует второй экземпляр nfqws на том же номере очереди.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import backend
from .backend import BackendLayout
from .log import log
from .settings import settings
from .strategies import Strategy, catalog

HELPER = Path(__file__).with_name("privileged_helper.py")
RUN_DIR = Path("/run/zapret-linux-gui")
PID_FILE = RUN_DIR / "nfqws.pid"
STATE_FILE = RUN_DIR / "state.json"


@dataclass
class Status:
    running: bool = False
    pid: int | None = None
    strategy_id: str | None = None
    strategy_name: str | None = None
    queue: int | None = None


class Runner:
    def __init__(self) -> None:
        self.layout: BackendLayout = BackendLayout()
        self.last_error: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = threading.RLock()
        self.refresh_layout()

    # ------------------------------------------------------------------ события

    def subscribe(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _emit(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ состояние

    def refresh_layout(self) -> BackendLayout:
        self.layout = backend.validate(settings.zapret_path)
        if self.layout.is_valid:
            catalog.reload(self.layout)
        return self.layout

    @property
    def is_valid(self) -> bool:
        return self.layout.is_valid

    def status(self) -> Status:
        """Читает состояние без повышения прав."""
        try:
            pid = int(PID_FILE.read_text().strip())
        except (OSError, ValueError):
            return Status()

        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return Status()

        if b"nfqws" not in cmdline:
            # pid-файл от умершего процесса, PID уже занят кем-то ещё.
            return Status()

        state = {}
        try:
            state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            pass

        return Status(
            running=True,
            pid=pid,
            strategy_id=state.get("strategy_id"),
            strategy_name=state.get("strategy_name"),
            queue=state.get("queue"),
        )

    @property
    def is_running(self) -> bool:
        return self.status().running

    def current_strategy(self) -> Strategy | None:
        status = self.status()
        if not status.running:
            return None
        return catalog.find_by_id(status.strategy_id)

    def current_strategy_name(self) -> str | None:
        status = self.status()
        if not status.running:
            return None
        strategy = catalog.find_by_id(status.strategy_id)
        return strategy.name if strategy else status.strategy_name

    def foreign_nfqws(self) -> list[int]:
        """Сторонние nfqws: служба zapret или ручной запуск.

        Два демона на одном номере очереди дают ровно те симптомы, по которым обычно
        грешат на стратегию, поэтому об этом лучше сказать вслух.
        """
        ours = self.status().pid
        found: list[int] = []
        try:
            entries = list(Path("/proc").iterdir())
        except OSError:
            return found

        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == ours:
                continue
            try:
                argv0 = (entry / "cmdline").read_bytes().split(b"\x00")[0]
            except OSError:
                continue
            if b"nfqws" in argv0:
                found.append(pid)
        return found

    # ------------------------------------------------------------------ помощник

    def _helper_command(self, args: list[str]) -> list[str] | None:
        if not HELPER.is_file():
            self.last_error = f"Не найден помощник: {HELPER}"
            return None

        if os.geteuid() == 0:
            return [sys.executable, str(HELPER)] + args

        pkexec = shutil.which("pkexec")
        if pkexec is None:
            self.last_error = "Не найден pkexec. Установите polkit."
            return None

        # Если файл исполняемый, запускаем его напрямую: только тогда путь совпадает с
        # exec.path в polkit-политике и пользователь видит внятный запрос, а не
        # «требуется аутентификация для запуска python3».
        if os.access(HELPER, os.X_OK):
            return [pkexec, str(HELPER)] + args
        return [pkexec, sys.executable, str(HELPER)] + args

    def _call_helper(self, args: list[str], timeout: int = 120) -> dict | None:
        command = self._helper_command(args)
        if command is None:
            log.error(self.last_error or "Помощник недоступен")
            return None

        log.debug("Вызов: " + " ".join(command))

        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            self.last_error = "Помощник не ответил вовремя."
            return None
        except OSError as exc:
            self.last_error = f"Не удалось вызвать помощника: {exc}"
            return None

        if result.returncode == 126:
            # Стандартный код pkexec, когда пользователь закрыл диалог пароля.
            self.last_error = "Авторизация отменена."
            return None
        if result.returncode == 127:
            self.last_error = "pkexec не смог запустить помощника."
            return None

        payload = None
        for line in reversed((result.stdout or "").strip().splitlines()):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue

        if payload is None:
            self.last_error = (result.stderr or result.stdout or "Помощник не ответил").strip()
            return None

        if not payload.get("ok"):
            self.last_error = payload.get("error") or "Неизвестная ошибка."
            return None

        self.last_error = None
        return payload

    # ------------------------------------------------------------------ действия

    def start(self, strategy: Strategy) -> bool:
        with self._lock:
            self.refresh_layout()

            if not self.layout.is_valid or self.layout.nfqws is None:
                self.last_error = self.layout.error or "Папка zapret не настроена."
                self._emit()
                return False

            foreign = self.foreign_nfqws()
            if foreign:
                log.warn(
                    "В системе уже работает nfqws (PID "
                    + ", ".join(str(p) for p in foreign)
                    + "). Если обход ведёт себя странно — остановите службу zapret или "
                    "смените номер очереди в настройках."
                )

            log.info(f"Запуск: {strategy.name}")
            log.debug("nfqws " + " ".join(strategy.args))

            payload = self._call_helper(
                [
                    "start",
                    "--nfqws", str(self.layout.nfqws),
                    "--workdir", str(self.layout.root),
                    "--queue", str(settings.queue_num),
                    "--strategy-id", strategy.id,
                    "--strategy-name", strategy.name,
                    "--",
                ]
                + list(strategy.args)
            )

            if payload is None:
                log.error(f"Не удалось запустить обход: {self.last_error}")
                self._emit()
                return False

            log.success(
                f"Обход работает: {strategy.name} "
                f"(PID {payload.get('pid')}, очередь {payload.get('queue')}, {payload.get('engine')})"
            )

            settings.selected_strategy_id = strategy.id
            settings.save()

            self._emit()
            return True

    def stop(self) -> bool:
        with self._lock:
            payload = self._call_helper(["stop", "--queue", str(settings.queue_num)])
            if payload is None:
                log.error(f"Не удалось остановить обход: {self.last_error}")
                self._emit()
                return False

            log.info("Обход остановлен")
            self._emit()
            return True

    def toggle(self, strategy: Strategy | None = None) -> bool:
        if self.is_running:
            return self.stop()

        target = strategy or catalog.find_by_id(settings.selected_strategy_id)
        if target is None:
            available = catalog.all
            target = available[0] if available else None

        if target is None:
            self.last_error = "Нет ни одной стратегии."
            return False

        return self.start(target)


runner = Runner()
