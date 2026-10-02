import argparse
import ctypes
import getpass
import os
import subprocess
import sys
import time

from app.core.engine import Engine
from app.core.history import base_dir

_MUTEX_HANDLE = None
MUTEX_NAME = "LANMessenger_SingleInstance"
PID_FILE = os.path.join(base_dir(), "messenger.pid")


def _write_pid():
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def _kill_old_instance():
    """Закрываем старый экземпляр: по pid-файлу, иначе по имени exe."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        pid = None
    if pid and pid != os.getpid():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)
    # ждём освобождения мьютекса
    kernel32 = ctypes.windll.kernel32
    for _ in range(12):
        time.sleep(0.5)
        h = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() != 183:
            kernel32.CloseHandle(h)
            return
        kernel32.CloseHandle(h)


def ensure_single_instance() -> bool:
    """Один экземпляр: при повторном запуске старый закрывается, новый
    стартует (чтобы с зависшей копией на удалённом ПК не было проблем)."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        _write_pid()
        return True
    # мьютекс занят, но мы только что открыли хэндл на него — закрыть,
    # иначе объект мьютекса не умрёт даже после убийства старого процесса
    kernel32.CloseHandle(_MUTEX_HANDLE)
    _MUTEX_HANDLE = None
    _kill_old_instance()
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:
        return False
    _write_pid()
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="LAN Messenger")
    parser.add_argument("--name", default=None, help="отображаемое имя")
    parser.add_argument("--udp-port", type=int, default=45677)
    parser.add_argument("--tcp-port", type=int, default=45678)
    parser.add_argument("--peer", action="append", default=[],
                        help="direct peer addr:port (если broadcast недоступен)")
    parser.add_argument("--console", action="store_true", help="консольный режим без GUI")
    parser.add_argument("--multi", action="store_true",
                        help="разрешить второй экземпляр (для тестов)")
    return parser.parse_args()


def make_targets(args):
    if not args.peer:
        return None
    targets = []
    for p in args.peer:
        addr, _, port = p.partition(":")
        targets.append((addr, int(port)))
    return targets


def run_console(engine):
    def fmt(node):
        status = "online " if node.online else "offline"
        return f"[{status}] {node.name} @ {node.key}"

    engine.discovery.on_node_new = lambda n: print(f"+ {fmt(n)}")
    engine.discovery.on_node_gone = lambda n: print(f"- {fmt(n)}")
    engine.start()
    print(f"Узел '{engine.name}' запущен (udp {engine.udp_port}, tcp {engine.tcp_port}). Ctrl+C — выход.")
    try:
        while True:
            time.sleep(5)
            nodes = engine.nodes()
            if nodes:
                print("--- узлы ---")
                for n in nodes:
                    print(f"  {fmt(n)}")
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        print("bye отправлен, выход.")


def run_gui(engine):
    from PyQt6.QtWidgets import QApplication

    from app.ui import theme
    from app.ui.main_window import MainWindow

    engine.start()
    app = QApplication(sys.argv)
    theme.apply(app, engine.theme)
    win = MainWindow(engine)
    win.show()
    code = app.exec()
    engine.stop()
    return code


def main():
    args = parse_args()
    if not args.console and not args.multi and not ensure_single_instance():
        return
    name = args.name or Engine.load_saved_name() or getpass.getuser()
    engine = Engine(name, udp_port=args.udp_port, tcp_port=args.tcp_port,
                    targets=make_targets(args))
    if args.console:
        run_console(engine)
    else:
        sys.exit(run_gui(engine))


if __name__ == "__main__":
    main()
