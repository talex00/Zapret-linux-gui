"""Определение сборки zapret в указанной папке.

На Windows роль бинарника играл winws.exe, здесь — nfqws. Проблема в том, что лежать он может
в трёх разных местах, в зависимости от того, собирали его из источников, взяли готовый
или ставили install_easy.sh в /opt/zapret.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

# Каталоги binaries/ в zapret называются по-своему, поэтому сопоставляем вручную.
_ARCH_HINTS = {
    "x86_64": ("x86_64", "amd64"),
    "amd64": ("x86_64", "amd64"),
    "aarch64": ("aarch64", "arm64"),
    "arm64": ("aarch64", "arm64"),
    "armv7l": ("arm",),
    "i686": ("x86", "i686"),
}


@dataclass
class BackendLayout:
    root: Path | None = None
    nfqws: Path | None = None
    config_file: Path | None = None
    hostlist_dir: Path | None = None
    is_valid: bool = False
    missing: list[str] = field(default_factory=list)
    error: str | None = None
    label: str = "Папка не указана"


def _arch_candidates() -> tuple[str, ...]:
    return _ARCH_HINTS.get(platform.machine().lower(), ())


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_nfqws(root: Path) -> Path | None:
    """Ищет nfqws в порядке от самого явного расположения к самому общему."""
    direct = [
        root / "nfq" / "nfqws",       # сборка из источников: make -C nfq
        root / "nfqws" / "nfqws",     # раскладка install_bin.sh
        root / "nfqws",              # голый бинарник рядом
    ]
    for candidate in direct:
        if _is_executable(candidate):
            return candidate

    binaries = root / "binaries"
    if binaries.is_dir():
        found = sorted(p for p in binaries.glob("*/nfqws") if _is_executable(p))

        # Предпочитаем каталог под текущую архитектуру: чужой бинарник запустится
        # и умрёт с Exec format error, а пользователь увидит только «обход не работает».
        for hint in _arch_candidates():
            for path in found:
                if hint in path.parent.name.lower():
                    return path

        if found:
            return found[0]

    return None


def validate(path: str | os.PathLike[str] | None) -> BackendLayout:
    if not path or not str(path).strip():
        return BackendLayout()

    root = Path(path).expanduser()

    if not root.exists():
        return BackendLayout(root=root, error="Папка не найдена.", label="Папка не найдена")
    if not root.is_dir():
        return BackendLayout(root=root, error="Указан файл, а нужна папка.", label="Это не папка")

    nfqws = find_nfqws(root)
    config_file = next((root / name for name in ("config", "config.default") if (root / name).is_file()), None)
    hostlist_dir = root / "ipset" if (root / "ipset").is_dir() else None

    if nfqws is None:
        # Отдельно разбираем случай «файл есть, но не исполняемый»: это частая беда
        # после распаковки архива, и лечится она одной командой chmod.
        for candidate in (root / "nfq" / "nfqws", root / "nfqws" / "nfqws", root / "nfqws"):
            if candidate.is_file():
                return BackendLayout(
                    root=root,
                    config_file=config_file,
                    hostlist_dir=hostlist_dir,
                    error=f"{candidate} не исполняемый: chmod +x {candidate}",
                    label="nfqws не исполняемый",
                )

        return BackendLayout(
            root=root,
            config_file=config_file,
            hostlist_dir=hostlist_dir,
            missing=["nfqws"],
            error="Не найден nfqws. Соберите его (make -C nfq) или запустите ./install_bin.sh",
            label="nfqws не найден",
        )

    flavor = "zapret" if config_file else "zapret (без config)"

    return BackendLayout(
        root=root,
        nfqws=nfqws,
        config_file=config_file,
        hostlist_dir=hostlist_dir,
        is_valid=True,
        label=flavor,
    )
