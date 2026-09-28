"""Render assets/logo.svg into the PNG and multi-size .ico files the app and README use.

    python tools/build_assets.py
"""
import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def render(svg: Path, w: int, h: int | None = None) -> QImage:
    h = h or w
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(str(svg))  # keep a reference: a temporary can be freed mid-paint
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    """ICO with one PNG-compressed entry per size (Windows picks the right one)."""
    blobs = [png_bytes(i) for i in images]
    offset = 6 + 16 * len(blobs)
    out = struct.pack("<HHH", 0, 1, len(blobs))
    for img, blob in zip(images, blobs):
        w = img.width() if img.width() < 256 else 0
        out += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(out + b"".join(blobs))


def main():
    app = QGuiApplication(sys.argv)  # must stay alive: text rendering needs it for fonts  # noqa: F841
    logo = ASSETS / "logo.svg"
    render(logo, 512).save(str(ASSETS / "logo-512.png"))
    render(logo, 64).save(str(ASSETS / "icon-64.png"))
    write_ico(ASSETS / "cue.ico", [render(logo, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if (ASSETS / "banner.svg").exists():
        render(ASSETS / "banner.svg", 1280, 400).save(str(ASSETS / "banner.png"))
    if (ASSETS / "splash.svg").exists():  # 2x for sharp text on high-DPI screens
        render(ASSETS / "splash.svg", 1120, 440).save(str(ASSETS / "splash.png"))
    # installer wizard art (Inno Setup wants 24-bit BMPs): tall side panel + small corner logo
    side = QImage(328, 628, QImage.Format.Format_RGB32)
    p = QPainter(side)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(side.rect(), Qt.GlobalColor.black)
    QSvgRenderer(str(ASSETS / "installer_side.svg")).render(p)
    p.end()
    side.save(str(ASSETS / "installer_side.bmp"))
    small = QImage(110, 110, QImage.Format.Format_RGB32)
    small.fill(Qt.GlobalColor.white)
    p = QPainter(small)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(logo)).render(p)
    p.end()
    small.save(str(ASSETS / "installer_small.bmp"))
    print("assets written to", ASSETS)


if __name__ == "__main__":
    main()
