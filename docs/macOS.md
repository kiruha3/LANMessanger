# Экспериментальная сборка для macOS

Windows остаётся portable-приложением `LANMessenger.exe`. Для Mac подготовлены отдельные
сценарий сборки и `.app`: они не пересобирают и не заменяют Windows exe. Результаты
нативных тестов и сборок для каждой архитектуры видны в GitHub Actions. Автоматическая
проверка запуска не заменяет проверку интерфейса и разрешений на настоящем Mac.

## Скачать готовую версию

В [экспериментальном релизе macOS 0.21.0](https://github.com/kiruha3/LANMessanger/releases/tag/v0.21.0-macos)
доступны два архива:

- [Apple Silicon (M-серия)](https://github.com/kiruha3/LANMessanger/releases/download/v0.21.0-macos/LANMessenger-0.21.0-macos-arm64.zip).
- [Intel](https://github.com/kiruha3/LANMessanger/releases/download/v0.21.0-macos/LANMessenger-0.21.0-macos-x86_64.zip).

Для скачивания из Releases вход в GitHub не требуется. Контрольные суммы архивов —
в файле [SHA256SUMS.txt](https://github.com/kiruha3/LANMessanger/releases/download/v0.21.0-macos/SHA256SUMS.txt).
Распакуйте архив и перенесите `LANMessenger.app` в «Программы»; ограничения первого
запуска описаны ниже. Windows 0.20.0 остаётся стабильным релизом, её exe доступен
по [прежней ссылке](https://github.com/kiruha3/LANMessanger/releases/latest/download/LANMessenger.exe).

## Что доступно

Общий код обеспечивает обнаружение узлов в LAN, личные чаты, комнаты через хаб,
картинки из буфера, историю, календарь, TLS и PSK. Протокол совместим с Windows-клиентами
и существующим хабом. Вставка в macOS — **Cmd+V**.

В первой Mac-версии **RDP, совместный сеанс, автозапуск и сканер сети недоступны**:
соответствующие элементы настроек отключены, сохранённые Windows-флаги не включают их.
Обнаружение других экземпляров мессенджера через UDP работает независимо от сканера.
В Windows эти функции сохраняются.

## Сборка на Mac

Нужен Python **3.11** и macOS. PyInstaller запускается на целевой системе,
на Windows собрать `.app` этой командой нельзя.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
python scripts/build_macos.py
```

Сценарий запускает весь набор тестов в отдельных временных папках, собирает `.app`,
проверяет, что процесс жив и создал таблицы истории и календаря, затем упаковывает
приложение через `ditto`, сохраняя ссылки и права исполняемых файлов.

Архитектура определяется по запущенному Python:

- Apple Silicon: `dist/macos/arm64/LANMessenger.app` и `LANMessenger-<версия>-macos-arm64.zip`.
- Intel: `dist/macos/x86_64/LANMessenger.app` и `LANMessenger-<версия>-macos-x86_64.zip`.

На Apple Silicon запускайте нативный Python arm64. Архив x86_64 собирается отдельно
на Intel, универсальная сборка пока не настроена. Проверка старта использует Qt offscreen;
она не заменяет проверку интерфейса, разрешений локальной сети и уведомлений на настоящем Mac.

## GitHub Actions

Workflow `.github/workflows/build-macos.yml` запускается **только вручную**.

1. Откройте **Actions → Build macOS (experimental) → Run workflow**.
2. Сначала выполняются регрессионные тесты на Windows без пересборки exe.
3. При успехе macOS-задачи выполняют тесты, сборку и проверку старта на Apple Silicon и Intel.
4. Скачайте нужный архив из **Artifacts** (`LANMessenger-macos-arm64` / `LANMessenger-macos-x86_64`).

Артефакты Actions хранятся 14 дней; готовые архивы в Releases доступны отдельно.
Workflow имеет права только на чтение репозитория,
не создаёт релизов, не отправляет коммитов и не меняет сервер-хаб.

## Запуск и данные

Распакуйте архив и перенесите `LANMessenger.app` в «Программы». При запросе macOS
разрешите приложению доступ к локальной сети. Сертификат Apple Developer ID и notarization
пока не настроены: macOS может ограничить первый запуск скачанного приложения.

Данные установленной `.app` находятся в:

```text
~/Library/Application Support/LANMessenger/
  settings.json
  history.db
  images/
  hub.crt
  hub.key
```

Обновление `.app` не затрагивает эту папку. Для резервной копии сохраняйте её целиком,
не передавайте посторонним: `settings.json` содержит пароли комнат и сетевой пароль.
На Windows расположение данных остаётся прежним — рядом с exe. При запуске исходников
на любой платформе данные остаются в текущей рабочей папке.

Для проверки из исходников:

```bash
python3.11 -m pip install -r requirements.txt
python3.11 main.py
```

Совместимость по сети проверяется реальным входящим сообщением от Windows-клиента
или другого участника комнаты, а не только успешным входом в комнату.
