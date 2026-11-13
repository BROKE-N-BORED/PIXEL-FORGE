"""QImage <-> Image adapters — the GUI's boundary to the core.

This is the ONLY module in the core's orbit that imports PySide6,
and nothing in the core imports it at module level: the GUI
imports it directly (the GUI is Qt anyway), and pixelize.load_image
imports it lazily, only when it takes the Qt decode path. The core
itself — pixelize, palettes, spritesheet, skins, codeinput,
library, video, cli — never touches Qt.

Conversion is exact: ARGB32 QImages store pixels little-endian as
B, G, R, A bytes, while Image stores R, G, B, A — the adapters swap
the red and blue channels and nothing else.
"""

from PySide6.QtGui import QImage

from .image import Image


def to_qimage(image):
    """An Image as a QImage (Format_ARGB32). The QImage owns its
    own copy of the pixels."""
    width, height = image.width(), image.height()
    data = image.data
    bgra = bytearray(width * height * 4)
    bgra[0::4] = data[2::4]
    bgra[1::4] = data[1::4]
    bgra[2::4] = data[0::4]
    bgra[3::4] = data[3::4]
    qimage = QImage(bytes(bgra), width, height, width * 4,
                    QImage.Format.Format_ARGB32)
    return qimage.copy()  # detach from the bytes object


def from_qimage(qimage):
    """A QImage as an Image. Any format is accepted — it is
    converted to ARGB32 first, as the old core did."""
    converted = qimage.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = converted.width(), converted.height()
    stride = converted.bytesPerLine()
    bits = bytes(converted.bits())
    data = bytearray(width * height * 4)
    if stride == width * 4:
        data[0::4] = bits[2::4]
        data[1::4] = bits[1::4]
        data[2::4] = bits[0::4]
        data[3::4] = bits[3::4]
    else:  # padded rows (cannot happen for ARGB32, but be safe)
        for y in range(height):
            row = bits[y * stride:y * stride + width * 4]
            o = y * width * 4
            data[o:o + width * 4:4] = row[2::4]
            data[o + 1:o + width * 4:4] = row[1::4]
            data[o + 2:o + width * 4:4] = row[0::4]
            data[o + 3:o + width * 4:4] = row[3::4]
    return Image(width, height, data)
