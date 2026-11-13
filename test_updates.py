"""Release checks do not depend on another LAN peer; reject non-Windows releases."""
import unittest
from app.core.updates import release_update


class UpdateTests(unittest.TestCase):
    def test_stable_windows_release_detected(self):
        release = {'tag_name':'v0.22.10', 'assets':[{'name':'LANMessenger.exe', 'browser_download_url':'https://github.com/kiruha3/LANMessanger/releases/download/v0.22.10/LANMessenger.exe'}]}
        self.assertEqual(release_update(release, '0.22.9')[0], '0.22.10')
        self.assertIsNone(release_update(release, '0.22.10'))
        self.assertIsNone(release_update(release, '0.23.0'))
        for key in ('prerelease', 'draft'):
            self.assertIsNone(release_update(dict(release, **{key:True}), '0.22.9'))

    def test_mac_and_foreign_links_are_ignored(self):
        self.assertIsNone(release_update({'tag_name':'v0.23.0-macos', 'assets':[]}, '0.22.9'))
        self.assertIsNone(release_update({'tag_name':'v0.23.0', 'assets':[{'name':'LANMessenger.exe', 'browser_download_url':'https://evil.example/LANMessenger.exe'}]}, '0.22.9'))


if __name__ == '__main__': unittest.main()
