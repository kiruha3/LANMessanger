"""Test, build, smoke-check and zip the native macOS app without touching Windows dist."""

from pathlib import Path
import os
import platform
import runpy
import sqlite3
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def smoke_bundle(bundle):
    """Check the frozen process and its writable database in an isolated user home."""
    with tempfile.TemporaryDirectory(prefix="lanmessenger-bundle-") as work:
        env = dict(os.environ, HOME=work, QT_QPA_PLATFORM="offscreen")
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen(
                [str(bundle / "Contents" / "MacOS" / "LANMessenger"),
                 "--multi", "--udp-port", "0", "--tcp-port", "0"],
                cwd=work, env=env, stdout=log, stderr=log,
            )
            try:
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    database = (Path(work) / "Library" / "Application Support"
                                / "LANMessenger" / "history.db")
                    if not database.is_file():
                        raise RuntimeError("The bundle did not create its history database")
                    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
                        tables = {row[0] for row in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"messages", "events"} <= tables:
                        raise RuntimeError("The bundle did not initialize history/calendar")
                else:
                    raise RuntimeError(f"The bundle exited during startup (code {code})")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                log.seek(0)
                output = log.read().decode("utf-8", errors="replace")
                if output:
                    print(output, flush=True)
            if "Traceback (most recent call last):" in output:
                raise RuntimeError("The bundle logged an unhandled exception")
    print("macOS bundle startup: OK", flush=True)


def main():
    if sys.platform != "darwin":
        print("macOS is required. Windows files have not been changed.", file=sys.stderr)
        return 1
    if sys.version_info[:2] != (3, 11):
        print("Use Python 3.11 for this build.", file=sys.stderr)
        return 1
    arch = platform.machine()
    if arch not in {"arm64", "x86_64"}:
        print(f"Unsupported architecture: {arch}", file=sys.stderr)
        return 1
    subprocess.run([sys.executable, str(ROOT / "scripts" / "run_tests.py")],
                   cwd=ROOT, check=True)
    output = ROOT / "dist" / "macos" / arch
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", str(output),
        "--workpath", str(ROOT / "build" / "macos" / arch),
        str(ROOT / "packaging" / "macos" / "LANMessenger.spec"),
    ], cwd=ROOT, check=True)
    bundle = output / "LANMessenger.app"
    smoke_bundle(bundle)
    version = runpy.run_path(str(ROOT / "app" / "__init__.py"))["__version__"]
    archive = output / f"LANMessenger-{version}-macos-{arch}.zip"
    # ditto preserves bundle symlinks and Unix executable permissions.
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                    str(bundle), str(archive)], check=True)
    print(f"Built: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
