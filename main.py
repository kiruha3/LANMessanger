import argparse
import getpass
import sys
import time

from app.core.engine import Engine


def parse_args():
    parser = argparse.ArgumentParser(description="LAN Messenger")
    parser.add_argument("--name", default=None, help="отображаемое имя")
    parser.add_argument("--udp-port", type=int, default=45677)
    parser.add_argument("--tcp-port", type=int, default=45678)
    parser.add_argument("--peer", action="append", default=[],
                        help="direct peer addr:port (если broadcast недоступен)")
    parser.add_argument("--console", action="store_true", help="консольный режим без GUI")
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

    from app.ui.main_window import MainWindow

    engine.start()
    app = QApplication(sys.argv)
    win = MainWindow(engine)
    win.show()
    code = app.exec()
    engine.stop()
    return code


def main():
    args = parse_args()
    name = args.name or Engine.load_saved_name() or getpass.getuser()
    engine = Engine(name, udp_port=args.udp_port, tcp_port=args.tcp_port,
                    targets=make_targets(args))
    if args.console:
        run_console(engine)
    else:
        sys.exit(run_gui(engine))


if __name__ == "__main__":
    main()
