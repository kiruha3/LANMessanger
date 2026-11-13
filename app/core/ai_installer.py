"""Opt-in, per-user Windows runtime. Never changes system PATH or installs globally."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile

from PyQt6.QtCore import QLockFile, QThread, pyqtSignal
from .https import open_url, network_error_message

PI_VERSION = '0.73.1'
NODE_VERSION = '22.23.5'
ASSETS = {
    'Node.js': ('https://nodejs.org/dist/v22.23.5/node-v22.23.5-win-x64.zip',
                '4a191082e9706866830379d577d2cc8a50c776fcd9436a8d061b063ed0d9a0e3'),
    'Git Bash': ('https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/Git-2.56.1-64-bit.tar.bz2',
                 'd7d89e69855f00e73d957b513c49538a420c465c84b9d212cf1add400eecdf54'),
    'ripgrep': ('https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/ripgrep-15.2.0-x86_64-pc-windows-msvc.zip',
                '71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5'),
    'fd': ('https://github.com/sharkdp/fd/releases/download/v10.5.2/fd-v10.5.2-x86_64-pc-windows-msvc.zip',
           '7edac75ef96b7e9a01ebb251a21ee86ea365a09adcfb865f1ec3346a906153b6'),
}


def runtime_folder() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'LANMessenger/ai-runtime/pi-0.73.1-node22-v1'


def managed_shell(root: Path | None = None) -> Path:
    root = root or runtime_folder()
    for relative in ('git/bin/bash.exe', 'git/usr/bin/bash.exe'):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return root / 'git/bin/bash.exe'


def runtime_complete(root: Path) -> bool:
    files = ('ready', 'node/node.exe', 'node_modules/@mariozechner/pi-coding-agent/dist/cli.js',
             'node_modules/@mariozechner/pi-coding-agent/dist/index.js', 'tools/rg.exe', 'tools/fd.exe')
    return all((root / name).is_file() for name in files) and managed_shell(root).is_file()


def supported() -> bool:
    return sys.platform == 'win32' and platform.machine().lower() in ('amd64', 'x86_64')


class InstallCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise InstallCancelled('Установка отменена. Можно попробовать снова.')


def download(url: str, target: Path, sha256: str, progress, cancel):
    check_cancel(cancel)
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={'User-Agent': 'LANMessenger-runtime-installer'})
    with open_url(request, timeout=10) as response, target.open('wb') as output:
        total = int(response.headers.get('Content-Length', 0))
        size = 0
        while True:
            check_cancel(cancel)
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
            if size > 512 * 1024 * 1024:
                raise ValueError('Слишком большой файл компонентов ИИ')
            progress(size, total)
    check_cancel(cancel)
    if digest.hexdigest() != sha256:
        target.unlink(missing_ok=True)
        raise ValueError('SHA256 не совпадает: загруженный файл не будет запущен. Повторите установку.')


def extract_zip(archive: Path, destination: Path, cancel):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as z:
        total = 0
        for entry in z.infolist():
            check_cancel(cancel)
            name = entry.filename.replace('\\', '/')
            path = PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or ':' in name
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or not (root / name).resolve().is_relative_to(root)):
                raise ValueError('Небезопасный путь в архиве компонентов ИИ')
            total += entry.file_size
            if total > 2 * 1024**3:
                raise ValueError('Слишком большой распакованный архив')
            z.extract(entry, root)


def extract_tar(archive: Path, destination: Path, cancel):
    """Materialize files/hardlinks; MSYS virtual /dev links are not Windows files."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    total = 0
    with tarfile.open(archive) as tar:
        for entry in tar:
            check_cancel(cancel)
            name = entry.name.replace('\\', '/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or ':' in name or not (root / name).resolve().is_relative_to(root):
                raise ValueError('Небезопасный путь в архиве Git Bash')
            if entry.issym():
                continue
            if entry.isdir():
                (root / name).mkdir(parents=True, exist_ok=True)
            elif entry.isfile() or entry.islnk():
                if entry.islnk() and (PurePosixPath(entry.linkname).is_absolute() or '..' in PurePosixPath(entry.linkname).parts or ':' in entry.linkname):
                    raise ValueError('Небезопасная ссылка в архиве Git Bash')
                output = root / name
                output.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(entry) as source, output.open('wb') as dest:
                    while chunk := source.read(256 * 1024):
                        check_cancel(cancel)
                        total += len(chunk)
                        if total > 2 * 1024**3:
                            raise ValueError('Слишком большой распакованный архив')
                        dest.write(chunk)
            else:
                raise ValueError('Недопустимый файл в архиве Git Bash')


def run_hidden(args, cwd: Path, env: dict, cancel, timeout=600):
    """Bounded process with cancellation; no shell or visible console windows."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   creationflags=flags)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ValueError('Время установки истекло. Проверьте интернет и повторите.')
                cancel.wait(.1)
            if process.returncode:
                raise ValueError(f'Не удалось установить компоненты ИИ (код {process.returncode}). Проверьте интернет и свободное место; повторите установку.')
        finally:
            if process.poll() is None:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=flags, timeout=10)
                else:
                    process.kill()
                process.wait(timeout=10)


def runtime_environment() -> dict:
    root = runtime_folder()
    if not (root / 'node/node.exe').is_file():
        return {}
    paths = [root / 'node', root / 'tools', root / 'git/bin', root / 'git/usr/bin', root / 'git/cmd']
    return {'PATH': os.pathsep.join([*(str(p) for p in paths), os.environ.get('PATH', '')]),
            'NODE_USE_SYSTEM_CA': '1'}


def install_runtime(progress, cancel, target: Path | None = None):
    if not supported():
        raise ValueError('Автоматическая установка доступна в Windows x64. На этой системе настройте Pi вручную.')
    target = target or runtime_folder()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(target.parent / 'install.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise ValueError('Компоненты ИИ уже устанавливаются в другом окне мессенджера.')
    try:
        check_cancel(cancel)
        backup = target.with_name(target.name + '-previous')
        # Recover an activation interrupted between the two directory renames.
        if backup.exists() and not target.exists():
            backup.rename(target)
        elif backup.exists() and not runtime_complete(target) and runtime_complete(backup):
            damaged = target.with_name(target.name + '-damaged-' + uuid.uuid4().hex)
            target.rename(damaged)
            try:
                backup.rename(target)
            except OSError:
                damaged.rename(target)
                raise
            shutil.rmtree(damaged, ignore_errors=True)
        if runtime_complete(target):
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            progress('Компоненты ИИ уже установлены.', 100)
            return
        if backup.exists():
            shutil.rmtree(backup)
        with tempfile.TemporaryDirectory(prefix='install-', dir=target.parent) as temp:
            work = Path(temp)
            stage = work / 'runtime'; stage.mkdir()
            for number, (label, (url, digest)) in enumerate(ASSETS.items()):
                archive = work / url.rsplit('/', 1)[-1]
                def report(size, total, label=label, number=number):
                    fraction = size / total if total else 0
                    progress(f'Загрузка {label}: {size // 1048576} МБ' + (f' из {total // 1048576} МБ' if total else ''), int(number * 18 + fraction * 17))
                download(url, archive, digest, report, cancel)
                progress(f'Распаковка {label}…', number * 18 + 17)
                if label == 'Git Bash':
                    extract_tar(archive, stage / 'git', cancel)
                else:
                    extracted = work / label
                    extract_zip(archive, extracted, cancel)
                    if label == 'Node.js':
                        shutil.move(str(extracted / f'node-v{NODE_VERSION}-win-x64'), str(stage / 'node'))
                    else:
                        # Keep the license/readme beside each tool.
                        tools = stage / 'tools'; tools.mkdir(exist_ok=True)
                        for item in next(extracted.iterdir()).iterdir():
                            if item.is_file():
                                shutil.copy2(item, tools / (item.name if item.suffix == '.exe' else label + '-' + item.name))
            check_cancel(cancel)
            env = dict(os.environ)
            env['NODE_USE_SYSTEM_CA'] = '1'
            env['PATH'] = os.pathsep.join([str(stage / p) for p in ('node', 'tools', 'git/bin', 'git/usr/bin', 'git/cmd')] + [env.get('PATH', '')])
            # Do not inherit developer global npm configuration or install scripts.
            env['npm_config_cache'] = str(work / 'npm-cache')
            env['npm_config_userconfig'] = str(work / 'empty.npmrc')
            (work / 'empty.npmrc').touch()
            progress('Установка локального агента Pi…', 76)
            run_hidden([str(stage / 'node/node.exe'), str(stage / 'node/node_modules/npm/bin/npm-cli.js'),
                        'install', '--prefix', str(stage), '--registry=https://registry.npmjs.org',
                        '--ignore-scripts', '--omit=optional', '--no-audit', '--no-fund',
                        f'@mariozechner/pi-coding-agent@{PI_VERSION}'], work, env, cancel)
            progress('Проверка компонентов…', 94)
            cli = stage / 'node_modules/@mariozechner/pi-coding-agent/dist/cli.js'
            for file in (cli, cli.with_name('index.js'), managed_shell(stage), stage / 'tools/rg.exe', stage / 'tools/fd.exe'):
                if not file.is_file():
                    raise ValueError(f'Установка неполная: отсутствует {file.relative_to(stage)}. Повторите установку компонентов ИИ.')
            env['PI_CODING_AGENT_DIR'] = str(work / 'check-data')
            env['PI_OFFLINE'] = '1'
            run_hidden([str(stage / 'node/node.exe'), str(cli), '--version'], work, env, cancel, 45)
            run_hidden([str(managed_shell(stage)), '-c', 'printf runtime-ok'], work, env, cancel, 30)
            check_cancel(cancel)
            (stage / 'ready').write_text(PI_VERSION, encoding='utf-8')
            if target.exists():
                target.rename(backup)
            try:
                stage.rename(target)
            except OSError:
                if backup.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        progress('Компоненты ИИ установлены. Настройте подключение и отправьте сообщение.', 100)
    finally:
        lock.unlock()


class RuntimeInstaller(QThread):
    progress = pyqtSignal(str, int)
    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            install_runtime(self.progress.emit, self.cancel_event)
        except Exception as exc:
            self.failed.emit(network_error_message(exc))
        else:
            self.succeeded.emit()
