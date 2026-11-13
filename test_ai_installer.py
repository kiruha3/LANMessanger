"""Private runtime installation: integrity, extraction, cancellation and discovery."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import tarfile
import io
import unittest
import sys
import time
from unittest.mock import patch
import zipfile

from app.core import ai_installer as installer
from app.core.ai import runtime_command, prepare_environment


class InstallerTests(unittest.TestCase):
    def test_download_checks_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'source'); source.write_bytes(b'payload')
            target = Path(folder, 'target')
            installer.download(source.as_uri(), target, hashlib.sha256(b'payload').hexdigest(), lambda *_: None, threading.Event())
            self.assertEqual(target.read_bytes(), b'payload')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                installer.download(source.as_uri(), target, '0'*64, lambda *_: None, threading.Event())

    def test_cancelled_download_does_not_install(self):
        with tempfile.TemporaryDirectory() as folder:
            cancel = threading.Event(); cancel.set()
            with self.assertRaises(installer.InstallCancelled):
                installer.download('https://invalid.example', Path(folder, 'out'), '', lambda *_: None, cancel)

    def test_cancel_stops_running_hidden_process(self):
        with tempfile.TemporaryDirectory() as folder:
            cancel = threading.Event()
            timer = threading.Timer(.5, cancel.set)
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(installer.InstallCancelled):
                    installer.run_hidden([sys.executable, '-c', 'import time; time.sleep(30)'], Path(folder), dict(os.environ), cancel)
                self.assertLess(time.monotonic() - started, 5)
            finally:
                timer.cancel()

    def test_zip_rejects_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder, 'bad.zip')
            for name in ('../escape', '/absolute', 'C:/escape', 'a:stream', 'a\\..\\escape'):
                with zipfile.ZipFile(archive, 'w') as z: z.writestr(name, b'bad')
                with self.assertRaises(ValueError):
                    installer.extract_zip(archive, Path(folder, 'out'), threading.Event())
            with zipfile.ZipFile(archive, 'w') as z:
                info = zipfile.ZipInfo('link'); info.external_attr = 0o120777 << 16
                z.writestr(info, '../escape')
            with self.assertRaises(ValueError):
                installer.extract_zip(archive, Path(folder, 'out'), threading.Event())

    def test_managed_node_and_shell_work_without_system_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'runtime')
            for name in ('node/node.exe', 'git/bin/bash.exe', 'node_modules/@mariozechner/pi-coding-agent/dist/cli.js', 'node_modules/@mariozechner/pi-coding-agent/dist/index.js'):
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.touch()
            data = Path(folder, 'data'); data.mkdir()
            with patch.object(installer, 'runtime_folder', return_value=root), patch('shutil.which', return_value=None):
                node, args, _ = runtime_command()
                self.assertEqual(node, str(root / 'node/node.exe'))
                env = prepare_environment(data, {'provider':'openai'}, '')
                self.assertIn(str(root / 'node'), env['PATH'])
                self.assertEqual(env['NODE_USE_SYSTEM_CA'],'1')
                import json
                self.assertEqual(json.loads((data / 'pi/settings.json').read_text())['shellPath'], str(root / 'git/bin/bash.exe'))

    def test_tar_materializes_hardlinks_without_creating_virtual_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder, 'git.tar.bz2')
            with tarfile.open(archive, 'w:bz2') as tar:
                file = tarfile.TarInfo('usr/bin/bash.exe'); file.size = 4; tar.addfile(file, io.BytesIO(b'bash'))
                link = tarfile.TarInfo('bin/bash.exe'); link.type=tarfile.LNKTYPE; link.linkname='usr/bin/bash.exe'; tar.addfile(link)
                dev = tarfile.TarInfo('dev/fd'); dev.type=tarfile.SYMTYPE; dev.linkname='/proc/self/fd'; tar.addfile(dev)
            out = Path(folder, 'out')
            installer.extract_tar(archive, out, threading.Event())
            self.assertEqual((out/'bin/bash.exe').read_bytes(), b'bash')
            self.assertFalse((out/'dev/fd').exists())
            with tarfile.open(archive, 'w:bz2') as tar:
                bad = tarfile.TarInfo('../escape'); bad.size = 1; tar.addfile(bad, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                installer.extract_tar(archive, out, threading.Event())

    def test_incomplete_ready_marker_does_not_skip_repair_or_damage_old_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder,'runtime'); root.mkdir(); (root/'ready').write_text('0.73.1')
            (root/'keep').write_text('old')
            with patch.object(installer, 'supported', return_value=True), patch.object(installer, 'download', side_effect=OSError('network')) as load:
                with self.assertRaises(OSError):
                    installer.install_runtime(lambda *_:None, threading.Event(), root)
                self.assertTrue(load.called)
            self.assertEqual((root/'keep').read_text(), 'old')

    def test_interrupted_activation_recovers_backup_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder,'runtime'); backup=Path(folder,'runtime-previous')
            for name in ('ready','node/node.exe','node_modules/@mariozechner/pi-coding-agent/dist/cli.js','node_modules/@mariozechner/pi-coding-agent/dist/index.js','git/bin/bash.exe','tools/rg.exe','tools/fd.exe'):
                file=backup/name; file.parent.mkdir(parents=True,exist_ok=True); file.touch()
            with patch.object(installer,'supported',return_value=True), patch.object(installer,'download',side_effect=AssertionError('No network needed')):
                installer.install_runtime(lambda *_:None,threading.Event(),target)
            self.assertTrue(installer.runtime_complete(target))
            self.assertFalse(backup.exists())


if __name__ == '__main__': unittest.main()
