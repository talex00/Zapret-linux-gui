# Zapret Linux GUI

Графическая оболочка для [zapret](https://github.com/bol-van/zapret) на Linux. Linux-версия [Zapret-wrapper](https://github.com/talex00/Zapret-wrapper): та же идея прогрессивного раскрытия интерфейса, но на GTK4/libadwaita и с нативным стеком `nfqws` + nftables.

## Что это делает

На Windows zapret работает через `winws.exe` и WinDivert. На Linux тот же проект устроен иначе:

1. трафик направляется в NFQUEUE правилами nftables (или iptables);
2. очередь обрабатывает демон `nfqws`, который и делает DPI-desync.

Поэтому запуск обхода — это всегда две операции, а не одна, и обе требуют root. Приложение берёт это на себя: само остаётся обычным пользовательским процессом, а правила и демона ставит короткий привилегированный помощник через `pkexec`.

## Интерфейс

Как и в Windows-версии, окно раскрывается по мере появления данных:

- нет папки zapret — виден только выбор папки;
- папка найдена — стратегия и одна большая кнопка запуска;
- всё остальное (аргументы, источник стратегий, журнал) — в «Подробностях»;
- служебные страницы (стратегии, тестирование, журнал, настройки) — за переключателем «Расширенный режим».

В простом режиме окно узкое и без серого полотна вокруг содержимого; высоту GTK считает по видимым блокам.

## Трей

Крестик скрывает окно, обход продолжает работать. В меню значка: состояние, «Открыть», «Включить/Выключить обход», «Выйти».

Трей в Linux — штука необязательная: он работает через StatusNotifierItem (`AyatanaAppIndicator3`). Если библиотеки нет или среда не показывает значки (GNOME без расширения AppIndicator), крестик закрывает приложение как обычно — прятать окно в невидимый трей было бы хуже.

## Зависимости

Обязательные: Python 3.11+, GTK 4, libadwaita 1.4+, PyGObject, `nftables` (или `iptables`), `curl`, `polkit` (`pkexec`), собранный `nfqws` из zapret.

Fedora:

```bash
sudo dnf install python3-gobject gtk4 libadwaita nftables curl polkit
```

Debian/Ubuntu:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 nftables curl policykit-1
```

Arch:

```bash
sudo pacman -S python-gobject gtk4 libadwaita nftables curl polkit
```

Необязательно, для трея: `gir1.2-ayatanaappindicator3-0.1` (Debian/Ubuntu), `libayatana-appindicator` (Arch), `libayatana-appindicator-gtk3` (Fedora).

## Откуда взять nfqws

```bash
git clone https://github.com/bol-van/zapret
cd zapret
./install_bin.sh   # готовые бинарники
# или: make -C nfq   # сборка из источников
```

В приложении укажите корень этой папки (или `/opt/zapret`, если ставили через `install_easy.sh`). Стратегии берутся из `config` (`NFQWS_OPT`), `strategies.txt` и встроенного набора.

## Запуск

Без установки:

```bash
python3 -m zapret_linux_gui
```

С установкой в систему:

```bash
sudo ./install.sh
zapret-linux-gui
```

## Конфликт с службой zapret

Если у вас уже работает `zapret.service` или другой `nfqws`, два демона на одном номере очереди гарантированно ломают обход. Приложение предупреждает об этом и предлагает либо остановить службу, либо сменить номер очереди в настройках.

## Лицензия и отношение к zapret

Это только оболочка. `nfqws` и стратегии принадлежат проекту bol-van/zapret и в этот репозиторий не входят.
