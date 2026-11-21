import json
import tempfile
import unittest
from pathlib import Path
from app.core.agent_roles import SCHEMA_VERSION, child_capabilities, default_role, migrate_file

class RoleTests(unittest.TestCase):
    def test_caps_are_deny_first_intersection(self):
        parent = {"capabilities": ["read", "delegate", "write"]}
        self.assertEqual(child_capabilities(parent, ["read", "delegate"], ["write", "delegate"]), ["delegate"])
        self.assertEqual(child_capabilities({}, ["read"], ["read"]), [])

    def test_v1_migration_backup_and_reload(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            original = {"config": {}, "chats": [], "profiles": []}
            path.write_text(json.dumps(original), encoding="utf-8")
            data, backup = migrate_file(path)
            self.assertEqual(data["schema_version"], SCHEMA_VERSION)
            self.assertEqual(data["role"]["role_id"], default_role()["role_id"])
            self.assertTrue(backup and backup.exists())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)

    def test_unsupported_schema_does_not_write(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            original = '{"schema_version":99}'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ValueError): migrate_file(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

if __name__ == "__main__": unittest.main()
