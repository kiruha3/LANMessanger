# ALT Linux: от клона до запуска UI

Полная инструкция запуска оконного LAN Messenger на ALT Linux (p9/p10).
Сборка не требуется — приложение работает из исходников.

## 1. Зависимости

```bash
apt-get update
apt-get install -y git python3 python3-module-PyQt6
```

Если `python3-module-PyQt6` нет в репозитории (старое ALT):
`pip3 install PyQt6 --user`

## 2. Код

```bash
git clone https://github.com/kiruha3/LANMessanger.git
cd LANMessanger
pip3 install cryptography --user   # шифрование (комнаты, PSK, TLS)
```

## 3. Запуск UI

```bash
python3 main.py
```

Откроется главное окно: чаты, дерево узлов, комнаты, календарь, помощь.
Остановка: трей → правый клик → Выйти.

Полезные флаги: `--name ИМЯ`, `--udp-port N`, `--tcp-port N`,
`--peer IP:PORT` (пир вручную), `--console` (режим без окон, для хаба).

## Что работает на Linux

Полностью: сообщения, комнаты через хаб, шифрование (PSK, TLS),
календарь, картинки по Ctrl+V, просмотрщик изображений, сканер сети,
история (history.db), настройки и темы, RDP-кнопка для подключения
К Windows-машинам (Linux → Windows).

Ограничения:
- автозапуск с Windows (реестр) — нет; автозапуск делать через
  systemd или ~/.config/autostart/lanmessenger.desktop;
- single-instance не работает (можно запустить несколько копий);
- нативных тостов Windows нет — вместо них Qt-уведомление в трее;
- принимать RDP на Linux нельзя (нет RDP-сервера) — только исходящие;
- ntfy-пуш на телефон работает (порт 8087), приложение ntfy на Android.

## Автозапуск при входе

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/lanmessenger.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=LAN Messenger
Exec=python3 /home/USER/LANMessanger/main.py
X-GNOME-Autostart-enabled=true
EOF
```
(путь в Exec поправьте под себя; окно откроется при логине).

## Проблемы

| Симптом | Причина | Решение |
|---|---|---|
| `No module named 'PyQt6'` | нет пакета | apt-get install python3-module-PyQt6 или pip3 install PyQt6 --user |
| `No module named 'cryptography'` | нет библиотеки | pip3 install cryptography --user |
| Окно не открывается по SSH | нет DISPLAY | запускать локально или ssh -X |
| Не видит узлы в LAN | broadcast закрыт | запуск с `--peer IP:45677` |
| «Не удалось расшифровать» | неверный пароль комнаты/сети | сверить пароль с участниками |

## Обновление

```bash
cd LANMessanger && git pull
```
Данные (settings.json, history.db, images/) переживают обновление.
