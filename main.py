import argparse
import ctypes
import getpass
import os
import subprocess
import sys
import time

from app.core.engine import Engine

_MUTEX_HANDLE = None
MUTEX_NAME = "LANMessenger_SingleInstance"
IMAGE_NAME = "LANMessenger.exe"


def _kill_other_instances():
    """Гасим старые экземпляры по имени процесса (приоритет у нового).
    Исключаем себя и свой процесс-загрузчик PyInstaller."""
    own = {os.getpid(), os.getppid()}
    try:
        res = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {IMAGE_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10)
        lines = res.stdout.decode("cp866", errors="replace").splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return
    for line in lines:
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[0] == IMAGE_NAME:
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid not in own:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True)


def ensure_single_instance() -> bool:
    """Один экземпляр. Если запущен старый — новый закрывает его
    (по имени процесса) и занимает его место."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    h = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        _MUTEX_HANDLE = h
        return True
    kernel32.CloseHandle(h)  # закрыть чужой хэндл, иначе мьютекс не умрёт

    _kill_other_instances()
    for _ in range(12):  # ждём освобождения мьютекса
        time.sleep(0.5)
        h = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() != 183:
            _MUTEX_HANDLE = h
            return True
        kernel32.CloseHandle(h)
    return False


def already_running_notice():
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            0, "LAN Messenger уже запущен — закройте его через трей "
               "(правый клик → Выйти) и запустите снова.",
            "LAN Messenger", 0x40)
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
        already_running_notice()
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
