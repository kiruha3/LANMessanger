"""Run the complete suite with each script's working data in a temporary folder."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "demo_two_nodes.py",
    "test_persistent.py",
    "test_rooms.py",
    "test_crypto.py",
    "test_tls.py",
    "test_https.py",
    "test_ui_smoke.py",
    "test_legacy_ack.py",
    "test_platform_support.py",
    "test_ai.py",
    "test_ai_installer.py",
    "test_updates.py",
    "test_tray_icon.py",
    "test_ai_catalog.py",
    "test_ai_timeline.py",
    "test_ai_ui.py",
    "test_ai_pi.py",
)


def main():
    failed = []
    env = dict(os.environ, PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen", LANMESSENGER_NO_UPDATE_CHECK="1")
    for name in TESTS:
        print(f"\nRunning {name}", flush=True)
        with tempfile.TemporaryDirectory(prefix="lanmessenger-test-") as work:
            try:
                result = subprocess.run(
                    [sys.executable, str(ROOT / name)], cwd=work, env=env,
                    timeout=120,
                )
                if result.returncode:
                    failed.append(name)
            except subprocess.TimeoutExpired:
                print(f"TIMEOUT: {name}", flush=True)
                failed.append(name)
    if failed:
        print(f"\nFAILED: {', '.join(failed)}", flush=True)
        return 1
    print(f"\nAll {len(TESTS)} test scripts passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
