#!/usr/bin/env python3
"""Привилегированная часть: правила NFQUEUE и демон nfqws.

Запускается от root через pkexec и живёт доли секунды. Само приложение остаётся
непривилегированным: GUI от root — плохая идея, а в Windows-версии другого выбора не было.

Файл сознательно самодостаточен и не импортирует ничего из пакета: pkexec запускает
его как отдельный скрипт, и относительные импорты тут не работают.

Протокол: одна строка JSON в stdout. Код возврата 0 = успешно.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

RUN_DIR = Path("/run/zapret-linux-gui")
PID_FILE = RUN_DIR / "nfqws.pid"
STATE_FILE = RUN_DIR / "state.json"
LOG_FILE = RUN_DIR / "nfqws.log"
NFT_TABLE = "zapret_linux_gui"

# Приоритет выше srcnat (100): при NAT пакеты должны попадать в очередь уже с
# внешним адресом, иначе nfqws отправляет пакеты с внутренним IP.
NFT_PRIORITY = 101


def reply(ok: bool, **fields) -> int:
    print(json.dumps({"ok": ok, **fields}, ensure_ascii=False))
    return 0 if ok else 1


def run(cmd: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


# --------------------------------------------------------------------------- rules


def nft_ruleset(queue: int) -> str:
    """Правила в отдельной таблице, чтобы уборка не трогала чужие цепочки.

    ct original packets 1-6 — тот же смысл, что connbytes в iptables: DPI смотрит на начало
    соединения, а гонять весь последующий трафик через userspace бессмысленно дорого.

    bypass обязателен: если nfqws умрёт, трафик пойдёт напрямую, а не встанет совсем.
    """
    return f"""
table inet {NFT_TABLE} {{
    chain postrouting {{
        type filter hook postrouting priority {NFT_PRIORITY}; policy accept;
        meta l4proto tcp tcp dport {{ 80, 443 }} ct original packets 1-6 queue num {queue} bypass
        meta l4proto udp udp dport 443 ct original packets 1-6 queue num {queue} bypass
    }}
}}
"""


def iptables_rules(queue: int) -> list[list[str]]:
    common = [
        "-t", "mangle", "POSTROUTING",
        "-m", "connbytes", "--connbytes-dir=original",
        "--connbytes-mode=packets", "--connbytes", "1:6",
        "-j", "NFQUEUE", "--queue-num", str(queue), "--queue-bypass",
    ]
    return [
        ["-p", "tcp", "-m", "multiport", "--dports", "80,443"] + common,
        ["-p", "udp", "--dport", "443"] + common,
    ]


def apply_rules(queue: int) -> tuple[bool, str, str]:
    """Сначала nftables, и только при его отсутствии — iptables."""
    if shutil.which("nft"):
        drop_rules("nft", queue)
        result = run(["nft", "-f", "-"], input_text=nft_ruleset(queue))
        if result.returncode == 0:
            return True, "nft", ""
        return False, "nft", (result.stderr or result.stdout).strip()

    if shutil.which("iptables"):
        drop_rules("iptables", queue)
        errors = []
        for rule in iptables_rules(queue):
            for binary in ("iptables", "ip6tables"):
                if not shutil.which(binary):
                    continue
                spec = list(rule)
                # -I после имени цепочки: правило должно быть первым.
                spec[spec.index("POSTROUTING")] = "POSTROUTING"
                result = run([binary] + spec[:1] + spec[1:3] + ["-I"] + spec[3:])
                if result.returncode != 0:
                    errors.append((result.stderr or result.stdout).strip())
        if errors:
            return False, "iptables", "; ".join(e for e in errors if e)
        return True, "iptables", ""

    return False, "none", "Не найден ни nft, ни iptables."


def drop_rules(engine: str, queue: int) -> None:
    if engine == "nft" or shutil.which("nft"):
        run(["nft", "delete", "table", "inet", NFT_TABLE])

    if engine == "iptables" or shutil.which("iptables"):
        for rule in iptables_rules(queue):
            for binary in ("iptables", "ip6tables"):
                if not shutil.which(binary):
                    continue
                spec = list(rule)
                run([binary] + spec[:3] + ["-D"] + spec[3:])


# --------------------------------------------------------------------------- daemon


def read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def process_alive(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    # Проверка имени обязательна: PID переиспользуются, и оставшийся pid-файл
    # легко указывает на чужой процесс, который мы бы убили.
    return b"nfqws" in cmdline


def spawn_nfqws(nfqws: Path, workdir: Path, queue: int, args: list[str]) -> tuple[bool, str, int | None]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [str(nfqws), f"--qnum={queue}"] + args

    try:
        log_handle = LOG_FILE.open("w")
    except OSError as exc:
        return False, f"Не удалось открыть {LOG_FILE}: {exc}", None

    try:
        # start_new_session: помощник завершится через мгновение, а демон должен
        # пережить и его, и закрытие GUI.
        process = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.close()
        return False, f"Не удалось запустить {nfqws}: {exc}", None
    finally:
        log_handle.close()

    # nfqws падает сразу, если ему не нравятся аргументы или номер очереди занят.
    # Без этой паузы мы бы ответили «запущено» на уже мертвый процесс.
    time.sleep(0.8)
    if process.poll() is not None:
        tail = ""
        try:
            tail = LOG_FILE.read_text(errors="replace").strip().splitlines()[-5:]
            tail = " | ".join(tail)
        except OSError:
            pass
        return False, tail or f"nfqws завершился с кодом {process.returncode}", None

    PID_FILE.write_text(str(process.pid))
    return True, "", process.pid


def stop_daemon() -> bool:
    pid = read_pid()
    stopped = False

    if pid is not None and process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not process_alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
            stopped = True
        except OSError:
            pass

    PID_FILE.unlink(missing_ok=True)
    STATE_FILE.unlink(missing_ok=True)
    return stopped


def foreign_nfqws() -> list[int]:
    """Другие nfqws в системе (служба zapret, ручной запуск)."""
    ours = read_pid()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == ours:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"nfqws" in cmdline.split(b"\x00")[0]:
            found.append(pid)
    return found


# --------------------------------------------------------------------------- commands


def cmd_start(ns: argparse.Namespace) -> int:
    nfqws = Path(ns.nfqws)
    if not (nfqws.is_file() and os.access(nfqws, os.X_OK)):
        return reply(False, error=f"{nfqws} не найден или не исполняемый")

    workdir = Path(ns.workdir) if ns.workdir else nfqws.parent

    # Повторный start — нормальный сценарий при смене стратегии и на автоподборе.
    stop_daemon()

    ok, engine, error = apply_rules(ns.queue)
    if not ok:
        return reply(False, error=f"Не удалось поставить правила ({engine}): {error}")

    started, error, pid = spawn_nfqws(nfqws, workdir, ns.queue, ns.args)
    if not started:
        # Правила без демона оставлять нельзя: трафик будет ходить в пустую очередь.
        drop_rules(engine, ns.queue)
        return reply(False, error=error)

    STATE_FILE.write_text(
        json.dumps(
            {
                "pid": pid,
                "queue": ns.queue,
                "engine": engine,
                "strategy_id": ns.strategy_id,
                "strategy_name": ns.strategy_name,
                "started_at": time.time(),
            },
            ensure_ascii=False,
        )
    )

    return reply(True, pid=pid, engine=engine, queue=ns.queue)


def cmd_stop(ns: argparse.Namespace) -> int:
    stopped = stop_daemon()
    drop_rules("auto", ns.queue)
    return reply(True, stopped=stopped)


def cmd_status(_: argparse.Namespace) -> int:
    pid = read_pid()
    running = pid is not None and process_alive(pid)

    state = {}
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        pass

    return reply(
        True,
        running=running,
        pid=pid if running else None,
        strategy_id=state.get("strategy_id"),
        strategy_name=state.get("strategy_name"),
        queue=state.get("queue"),
        foreign=foreign_nfqws(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Privileged helper for Zapret Linux GUI")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--nfqws", required=True)
    start.add_argument("--workdir", default=None)
    start.add_argument("--queue", type=int, required=True)
    start.add_argument("--strategy-id", dest="strategy_id", default=None)
    start.add_argument("--strategy-name", dest="strategy_name", default=None)
    start.add_argument("args", nargs=argparse.REMAINDER)
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop")
    stop.add_argument("--queue", type=int, default=0)
    stop.set_defaults(func=cmd_stop)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)

    ns = parser.parse_args(argv)

    if ns.command in ("start", "stop") and os.geteuid() != 0:
        return reply(False, error="Помощник должен запускаться от root")

    # Отбрасываем разделитель «--», который REMAINDER сохраняет как обычный аргумент.
    if getattr(ns, "args", None) and ns.args and ns.args[0] == "--":
        ns.args = ns.args[1:]

    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
