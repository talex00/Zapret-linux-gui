"""Каталог стратегий.

Источники, в порядке доверия:

1. `config` самого zapret — переменная NFQWS_OPT. Если человек уже настроил себе zapret,
   это ровно та стратегия, которой он пользуется.
2. `strategies.txt` в корне — куда обычно складывают вывод blockcheck.sh.
3. Встроенный набор — чтобы приложение было полезно сразу, без прогона blockcheck.

Про placeholders: в config встречаются <HOSTLIST> и <HOSTLIST_NOAUTO> — их подставляют init-скрипты
zapret. Мы заменяем их на реальный --hostlist, если файл списка есть, иначе выбрасываем:
оставленный плейсхолдер nfqws не понимает и сразу завершается с ошибкой.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .backend import BackendLayout
from .log import log


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    description: str
    args: tuple[str, ...]
    source: str


# Встроенный набор. Каждая строка — полный набор профилей (TCP 80, TCP 443, QUIC),
# разделённых --new, как это делает штатный config.default.
_BUILTIN: tuple[tuple[str, str, str, str], ...] = (
    (
        "builtin-default",
        "Стандартная (fake + multisplit)",
        "Набор из config.default самого zapret. Разумная первая попытка.",
        "--filter-tcp=80 --dpi-desync=fake,multisplit --dpi-desync-split-pos=method+2 "
        "--dpi-desync-fooling=md5sig --new "
        "--filter-tcp=443 --dpi-desync=fake,multidisorder --dpi-desync-split-pos=1,midsld "
        "--dpi-desync-fooling=badseq,md5sig --new "
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=6",
    ),
    (
        "builtin-multidisorder-midsld",
        "multidisorder midsld",
        "Часто срабатывает там, где DPI смотрит только на первый пакет TLS.",
        "--filter-tcp=80 --dpi-desync=fake,multisplit --dpi-desync-split-pos=method+2 --new "
        "--filter-tcp=443 --dpi-desync=multidisorder --dpi-desync-split-pos=midsld "
        "--dpi-desync-split-seqovl=336 --new "
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=6",
    ),
    (
        "builtin-fakedsplit-ttl",
        "fake + split2 с TTL",
        "Ставка на то, что поддельный пакет умрёт до сервера, но дойдёт до DPI.",
        "--filter-tcp=80,443 --dpi-desync=fake,split2 --dpi-desync-ttl=5 --new "
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-ttl=5 --dpi-desync-repeats=6",
    ),
    (
        "builtin-fake-badseq",
        "fake + badseq",
        "Для DPI, которые не проверяют корректность последовательности TCP.",
        "--filter-tcp=80,443 --dpi-desync=fake --dpi-desync-fooling=badseq --new "
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=6",
    ),
    (
        "builtin-disorder2",
        "disorder2",
        "Перестановка сегментов без поддельных пакетов. Работает там, где fake блокируется.",
        "--filter-tcp=80,443 --dpi-desync=disorder2 --dpi-desync-split-pos=1 --new "
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=6",
    ),
    (
        "builtin-syndata",
        "syndata",
        "Отправка данных вместе с SYN. Некоторые DPI теряют состояние соединения.",
        "--filter-tcp=443 --dpi-desync=syndata --new "
        "--filter-tcp=80 --dpi-desync=fake,multisplit --dpi-desync-split-pos=method+2 --new "
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=6",
    ),
    (
        "builtin-quic-only",
        "Только QUIC",
        "Если блокируется только UDP 443 (типично для YouTube на части провайдеров).",
        "--filter-udp=443 --dpi-desync=fake --dpi-desync-repeats=8",
    ),
)

_PLACEHOLDER_RE = re.compile(r"^<HOSTLIST(_NOAUTO)?>$")


class StrategyCatalog:
    def __init__(self) -> None:
        self._all: list[Strategy] = []
        self._loaded_from: str | None = None
        self.source_description: str = "не загружено"

    @property
    def all(self) -> list[Strategy]:
        return list(self._all)

    def find_by_id(self, strategy_id: str | None) -> Strategy | None:
        if not strategy_id:
            return None
        return next((s for s in self._all if s.id == strategy_id), None)

    def reload(self, layout: BackendLayout, force: bool = False) -> None:
        key = str(layout.root) if layout.root else None
        if not force and self._all and key == self._loaded_from:
            return

        self._loaded_from = key
        found: list[Strategy] = []
        sources: list[str] = []

        if layout.root is not None:
            from_config = self._load_config(layout)
            if from_config is not None:
                found.append(from_config)
                sources.append("config (NFQWS_OPT)")

            from_file = self._load_strategies_txt(layout)
            if from_file:
                found.extend(from_file)
                sources.append(f"strategies.txt: {len(from_file)}")

        builtin = self._builtin()
        found.extend(builtin)
        sources.append(f"встроенные: {len(builtin)}")

        # Дубли по набору аргументов означают бессмысленный повторный прогон на тесте.
        unique: list[Strategy] = []
        seen: set[tuple[str, ...]] = set()
        for strategy in found:
            if strategy.args in seen:
                continue
            seen.add(strategy.args)
            unique.append(strategy)

        self._all = unique
        self.source_description = "; ".join(sources)
        log.info(f"Стратегий загружено: {len(unique)} ({self.source_description})")

    def _resolve_placeholders(self, tokens: list[str], layout: BackendLayout) -> list[str]:
        hostlist = None
        if layout.hostlist_dir is not None:
            for name in ("zapret-hosts-user.txt", "zapret-hosts.txt"):
                candidate = layout.hostlist_dir / name
                if candidate.is_file() and candidate.stat().st_size > 0:
                    hostlist = candidate
                    break

        resolved: list[str] = []
        for token in tokens:
            if not _PLACEHOLDER_RE.match(token):
                resolved.append(token)
                continue
            if hostlist is not None:
                resolved.append(f"--hostlist={hostlist}")
            # Без списка просто убираем фильтр: стратегия применится к всему трафику.
        return resolved

    def _load_config(self, layout: BackendLayout) -> Strategy | None:
        if layout.config_file is None:
            return None

        try:
            text = layout.config_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warn(f"Не удалось прочитать {layout.config_file}: {exc}")
            return None

        match = re.search(r'^\s*NFQWS_OPT\s*=\s*"(.*?)"', text, re.MULTILINE | re.DOTALL)
        if not match:
            return None

        try:
            tokens = shlex.split(match.group(1))
        except ValueError:
            return None

        tokens = self._resolve_placeholders(tokens, layout)
        if not tokens:
            return None

        return Strategy(
            id="config-nfqws-opt",
            name=f"Из {layout.config_file.name} (NFQWS_OPT)",
            description="Стратегия, которая уже настроена в самом zapret.",
            args=tuple(tokens),
            source=str(layout.config_file),
        )

    def _load_strategies_txt(self, layout: BackendLayout) -> list[Strategy]:
        assert layout.root is not None
        path = layout.root / "strategies.txt"
        if not path.is_file():
            return []

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            log.warn(f"Не удалось прочитать {path}: {exc}")
            return []

        result: list[Strategy] = []
        for number, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            name = None
            if "=" in line and line.split("=", 1)[0].strip() and not line.startswith("-"):
                head, tail = line.split("=", 1)
                if "--" not in head:
                    name, line = head.strip(), tail.strip()

            # Вывод blockcheck.sh выглядит как «nfqws --dpi-desync=...» — имя программы лишнее.
            line = re.sub(r"^(nfqws|\./nfqws|dvtws)\s+", "", line)

            try:
                tokens = shlex.split(line)
            except ValueError:
                log.warn(f"{path.name}:{number}: не разобрал строку, пропускаю")
                continue

            tokens = self._resolve_placeholders(tokens, layout)
            if not tokens:
                continue

            result.append(
                Strategy(
                    id=f"file-{number}",
                    name=name or f"strategies.txt #{number}",
                    description="Стратегия из strategies.txt.",
                    args=tuple(tokens),
                    source=str(path),
                )
            )

        return result

    def _builtin(self) -> list[Strategy]:
        return [
            Strategy(
                id=strategy_id,
                name=name,
                description=description,
                args=tuple(shlex.split(args)),
                source="встроенный набор",
            )
            for strategy_id, name, description, args in _BUILTIN
        ]


catalog = StrategyCatalog()
