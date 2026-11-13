"""Read-only GitHub stable Windows release discovery; never replaces the app."""
import json
import re
import urllib.request
from .https import open_url

API_URL = 'https://api.github.com/repos/kiruha3/LANMessanger/releases/latest'


def release_update(release: dict, current: str):
    tag = release.get('tag_name', '')
    if release.get('prerelease') or release.get('draft') or not re.fullmatch(r'v?\d+\.\d+\.\d+', tag):
        return None
    version = tag.removeprefix('v')
    if tuple(map(int, version.split('.'))) <= tuple(map(int, current.split('.'))):
        return None
    expected = f'https://github.com/kiruha3/LANMessanger/releases/download/{tag}/LANMessenger.exe'
    for asset in release.get('assets', []):
        if asset.get('name') == 'LANMessenger.exe' and asset.get('browser_download_url') == expected:
            return version, expected
    return None


def fetch_update(current: str):
    request = urllib.request.Request(API_URL, headers={'User-Agent':'LANMessenger-update-check', 'Accept':'application/vnd.github+json'})
    with open_url(request, timeout=10) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('Слишком большой ответ сервера обновлений')
    release = json.loads(data)
    if not isinstance(release, dict):
        raise ValueError('Некорректный ответ сервера обновлений')
    return release_update(release, current)
