"""Настройки в ~/.config/zapret-linux-gui/settings.json.

Формат читается снисходительно: неизвестные ключи игнорируются, битый файл откатывается
к дефолтам. Настройки — не то место, где приложение должно падать при старте.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TEST_DOMAINS = ["www.youtube.com", "discord.com", "rutracker.org"]

# Запасной номер очереди. 200 использует штатный zapret, поэтому берём соседний:
# два nfqws на одном номере гарантированно ломают обход друг другу.
DEFAULT_QUEUE_NUM = 537


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "zapret-linux-gui"


def config_path() -> Path:
    return config_dir() / "settings.json"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "zapret-linux-gui"


@dataclass
class Settings:
    zapret_path: str | None = None
    selected_strategy_id: str | None = None
    queue_num: int = DEFAULT_QUEUE_NUM
    color_scheme: str = "system"  # system | light | dark
    test_domains: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_DOMAINS))
    probe_timeout: int = 6
    close_to_tray: bool = True
    last_best_strategy: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        path = config_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()

        if not isinstance(raw, dict):
            return cls()

        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in known}

        settings = cls(**clean)

        # Пустой список целей сделал бы тестирование молча бессмысленным.
        if not settings.test_domains:
            settings.test_domains = list(DEFAULT_TEST_DOMAINS)
        if not (1 <= settings.queue_num <= 65535):
            settings.queue_num = DEFAULT_QUEUE_NUM

        return settings

    def save(self) -> bool:
        try:
            config_dir().mkdir(parents=True, exist_ok=True)
            payload = {
                "zapret_path": self.zapret_path,
                "selected_strategy_id": self.selected_strategy_id,
                "queue_num": self.queue_num,
                "color_scheme": self.color_scheme,
                "test_domains": self.test_domains,
                "probe_timeout": self.probe_timeout,
                "close_to_tray": self.close_to_tray,
                "last_best_strategy": self.last_best_strategy,
            }
            tmp = config_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(config_path())
            return True
        except OSError:
            return False


# Единый экземпляр на процесс: страницы и трей должны видеть один и тот же выбор.
settings = Settings.load()
