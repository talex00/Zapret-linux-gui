"""Автоподбор стратегии.

Логика та же, что в StrategyTester на Windows: последовательно включаем каждую стратегию,
дёргаем несколько заведомо блокируемых доменов, считаем успешные ответы и задержку.

Перед прогоном есть базовая проба без обхода: если всё и так открывается, тест
бессмыслен — любая стратегия покажет 100% и выбор будет случайным.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .log import log
from .runner import runner
from .settings import settings
from .strategies import Strategy


@dataclass
class ProbeResult:
    domain: str
    ok: bool
    latency_ms: int | None
    detail: str


@dataclass
class Outcome:
    strategy: Strategy | None
    results: list[ProbeResult] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def successes(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def success_rate(self) -> float:
        return self.successes / self.total if self.total else 0.0

    @property
    def average_latency(self) -> int | None:
        values = [r.latency_ms for r in self.results if r.ok and r.latency_ms is not None]
        return round(sum(values) / len(values)) if values else None

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        latency = f", {self.average_latency} мс" if self.average_latency is not None else ""
        return f"{self.successes}/{self.total}{latency}"

    @property
    def failed_domains(self) -> list[str]:
        return [r.domain for r in self.results if not r.ok]


def probe(domain: str, timeout: int) -> ProbeResult:
    """Одна проба через curl.

    Нам важен не контент, а факт, что TLS-рукопожатие дошло до конца: именно его
    рвёт DPI. Поэтому любой HTTP-код считается успехом, а ошибка curl — провалом.
    """
    if shutil.which("curl") is None:
        return ProbeResult(domain, False, None, "не найден curl")

    command = [
        "curl", "--silent", "--show-error", "--output", "/dev/null",
        "--max-time", str(timeout),
        "--write-out", "%{http_code} %{time_total}",
        f"https://{domain}/",
    ]

    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 3, check=False)
    except subprocess.TimeoutExpired:
        return ProbeResult(domain, False, None, "таймаут")
    except OSError as exc:
        return ProbeResult(domain, False, None, str(exc))

    elapsed = int((time.monotonic() - started) * 1000)

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return ProbeResult(domain, False, None, detail[-1] if detail else f"curl {result.returncode}")

    parts = (result.stdout or "").split()
    code = parts[0] if parts else "?"
    try:
        latency = int(float(parts[1]) * 1000)
    except (IndexError, ValueError):
        latency = elapsed

    return ProbeResult(domain, True, latency, f"HTTP {code}")


class Tester:
    """Запускается из рабочего потока; все колбеки тоже приходят из него."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.last_best: Strategy | None = None

    def baseline(self) -> Outcome:
        """Проба без обхода."""
        runner.stop() if runner.is_running else None
        results = [probe(d, settings.probe_timeout) for d in settings.test_domains]
        return Outcome(strategy=None, results=results)

    def run(
        self,
        strategies: Sequence[Strategy],
        on_progress: Callable[[int, int, Strategy], None] | None = None,
        on_result: Callable[[Outcome], None] | None = None,
    ) -> list[Outcome]:
        self.cancel.clear()
        outcomes: list[Outcome] = []
        total = len(strategies)

        for index, strategy in enumerate(strategies, start=1):
            if self.cancel.is_set():
                log.warn("Тестирование отменено")
                break

            if on_progress is not None:
                on_progress(index, total, strategy)

            if not runner.start(strategy):
                outcome = Outcome(strategy=strategy, error=runner.last_error or "не запустилась")
                outcomes.append(outcome)
                if on_result is not None:
                    on_result(outcome)
                continue

            # nfqws нужно мгновение на привязку к очереди, иначе первый запрос
            # уйдёт мимо обхода и стратегия получит незаслуженный минус.
            time.sleep(0.6)

            results = []
            for domain in settings.test_domains:
                if self.cancel.is_set():
                    break
                results.append(probe(domain, settings.probe_timeout))

            outcome = Outcome(strategy=strategy, results=results)
            outcomes.append(outcome)
            log.info(f"{strategy.name}: {outcome.summary}")

            if on_result is not None:
                on_result(outcome)

        runner.stop()

        best = self.best_of(outcomes)
        if best is not None and best.strategy is not None:
            self.last_best = best.strategy
            settings.last_best_strategy = best.strategy.name
            settings.selected_strategy_id = best.strategy.id
            settings.save()
            log.success(f"Лучшая стратегия: {best.strategy.name} ({best.summary})")
        else:
            log.warn("Ни одна стратегия не сработала")

        return outcomes

    @staticmethod
    def best_of(outcomes: Sequence[Outcome]) -> Outcome | None:
        usable = [o for o in outcomes if o.error is None and o.successes > 0]
        if not usable:
            return None
        # Сначала доля успеха, при равенстве — меньшая задержка.
        return max(usable, key=lambda o: (o.success_rate, -(o.average_latency or 10_000)))


tester = Tester()
