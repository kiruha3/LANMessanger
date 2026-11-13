"""Generate the exact window/tray artwork as a multi-resolution Windows icon."""
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFontDatabase
from app.ui.app_icon import icon_pixmap

app = QApplication([])
if sys.platform == 'win32':
    for font in ('segoeui.ttf', 'segoeuib.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font))
output = ROOT / 'build/LANMessenger.ico'
output.parent.mkdir(exist_ok=True)
# Pillow writes all Windows icon resolutions from the same artwork.
from PIL import Image
source = ROOT / 'build/LANMessenger.png'
icon_pixmap(256).save(str(source))
Image.open(source).save(output, sizes=[(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)])
print(output)
