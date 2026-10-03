# AGENTS.md — LAN Messenger

P2P-мессенджер для LAN и прямых подключений через интернет. Python 3.11 + PyQt6, portable exe через PyInstaller.

## Правила работы

- **Пуш — только по явной команде пользователя.** Коммитить можно, `git push` — нет.
- **При каждой сборке exe поднимать версию** в `app/__init__.py` (`__version__`) — версия видна в заголовке окна.
- **Перед сборкой прогонять тесты**: `demo_two_nodes.py`, `test_persistent.py`, `test_ui_smoke.py` (все должны быть зелёными).
- Сборка: `python -m PyInstaller --noconfirm --onefile --windowed --name LANMessenger main.py` → `dist\LANMessenger.exe`. Перед сборкой убивать запущенный exe (`taskkill //F //IM LANMessenger.exe`), иначе файл занят.
- После сборки запускать exe и проверять, что процесс жив.
- Убирать за тестами: `history.db`, `settings.json`, `messenger.pid` в корне — это рабочие данные, в git не идут (есть в `.gitignore`).
- Коммит: `git -c user.name="kirill" -c user.email="kirill@localhost" commit`.

## Архитектура (кратко)

- Один exe = один процесс = сеть + UI. Порты: UDP 45677 (discovery), TCP 45678 (сообщения/канал/туннели), 8087 (ntfy push, выкл по умолчанию).
- `app/net/` — протокол (JSON-кадры `LANMSG/1`, фрейминг 4 байта длины + payload), discovery, постоянные соединения, сканер.
- `app/core/` — engine (ядро), history (SQLite), tunnel (RDP-проброс), push (ntfy SSE).
- `app/ui/` — главное окно (вкладки Чаты/Календарь), пузыри чата (QPainter), темы, свитчи, настройки.
- Входящие TCP — только приватные IP + явно разрешённые; есть режим accept_all.

## Особенности, о которые уже споткнулись

- Windows: ICMP port unreachable прилетает как WSAECONNRESET на UDP recvfrom — игнорировать.
- Qt6: нет конструктора `QDateTime(QDate)` — использовать `QDate.startOfDay()`.
- При одновременном дозвоне двух сторон — tie-break по node_id (меньший держит исходящее).
- Тёмная тема Windows ломает цвет текста в QTextDocument — цвета задавать явно.
- При быстрых правках UI проверять импорты виджетов (0.14.0 вылетал диалог настроек из-за QLabel).
