#!/usr/bin/env python3
"""Render the Pixel Forge icon from a single pixel map.

The map is the source of truth: this script writes icons/pixel-forge.svg
and icons/pixel-forge-{256,128,48}.png from it, so the SVG and the
prebuilt PNGs can never drift apart.

The mark is a pixel flame (ember yellow core, orange middle, red edge)
over a bed of dark coals, on a dark rounded square — pixel squares,
forge ember, nothing else.

Run with the Qt test env (see AGENTS.md):
  LD_LIBRARY_PATH=~/workspace/qt-libs/root/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen \
  ~/workspace/forge-v2-testenv/bin/python tools/render_icon.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS_DIR = os.path.join(ROOT, "icons")

# 12 x 12 pixel map. Y = ember yellow, O = orange, R = red, D = coal.
PIXEL_MAP = [
    ".....YY.....",
    "....YYYY....",
    "....YYOO....",
    "...YYOOO....",
    "...YOOOO....",
    "..YYOOORR...",
    "..YOOORRR...",
    ".YYOORRRR...",
    ".YOOORRRR...",
    ".YOORRRRR...",
    "..ORRRRRR...",
    "..DDDDDDD...",
]

COLORS = {
    "Y": "#FFD23F",
    "O": "#FF8C42",
    "R": "#E63B2E",
    "D": "#5A1F1A",
}

BACKGROUND = "#1C1C22"
BORDER = "#3A3A42"
GRID = 14  # the 12x12 map sits centered in a 14x14 grid


def _cells():
    """(grid_x, grid_y, hex_color) for every filled pixel."""
    margin = (GRID - len(PIXEL_MAP)) // 2
    for row, line in enumerate(PIXEL_MAP):
        for col, char in enumerate(line):
            if char in COLORS:
                yield col + margin, row + margin, COLORS[char]


def write_svg(path):
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 %d %d">' % (GRID, GRID),
        '<rect x="0" y="0" width="%d" height="%d" rx="2.5" '
        'fill="%s" stroke="%s" stroke-width="0.25"/>'
        % (GRID, GRID, BACKGROUND, BORDER),
    ]
    for x, y, color in _cells():
        parts.append('<rect x="%d" y="%d" width="1" height="1" '
                     'fill="%s"/>' % (x, y, color))
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")


def render_pngs():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    os.makedirs(ICONS_DIR, exist_ok=True)
    for size in (256, 128, 48):
        cell = size // GRID
        offset = (size - cell * GRID) // 2
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(BACKGROUND))
        painter.setPen(QColor(BORDER))
        painter.drawRoundedRect(1, 1, size - 2, size - 2,
                                size // 7, size // 7)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, color in _cells():
            painter.setBrush(QColor(color))
            painter.fillRect(offset + x * cell, offset + y * cell,
                             cell, cell, QColor(color))
        painter.end()
        path = os.path.join(ICONS_DIR, "pixel-forge-%d.png" % size)
        if not image.save(path, "PNG"):
            raise SystemExit("could not write %s" % path)
        print("wrote %s" % path)


def main():
    os.makedirs(ICONS_DIR, exist_ok=True)
    write_svg(os.path.join(ICONS_DIR, "pixel-forge.svg"))
    print("wrote %s" % os.path.join(ICONS_DIR, "pixel-forge.svg"))
    render_pngs()


if __name__ == "__main__":
    sys.exit(main())
