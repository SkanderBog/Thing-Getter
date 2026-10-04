"""Render our SVG into native platform icon formats."""
import os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image

root = Path(__file__).resolve().parents[1] / 'desktop'
app = QGuiApplication([])
canvas = QImage(1024, 1024, QImage.Format_ARGB32)
canvas.fill(0)
painter = QPainter(canvas)
QSvgRenderer(str(root / 'assets/icon.svg')).render(painter)
painter.end()
canvas.save(str(root / 'assets/icon.png'))
with Image.open(root / 'assets/icon.png') as icon:
    icon.save(root / 'assets/icon.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    icon.save(root / 'assets/icon.icns')
print('Rendered PNG, ICO and ICNS icons')
