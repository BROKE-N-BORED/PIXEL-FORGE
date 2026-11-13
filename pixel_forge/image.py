"""In-memory RGBA image — the core's image type, stdlib-only.

Pixel Forge used to pass QImages through the whole core, which meant
even the command line needed PySide6. The core now passes this plain
buffer instead: width, height, and RGBA bytes, row-major. The GUI
converts to and from QImage at its own boundary (qtimage.py); the
core never imports Qt.

The method names deliberately mirror the QImage surface the core
used (width(), height(), pixel(), setPixel(), copy(), fill(),
save(), scaled(), isNull()) so the core reads the same as before
and every call site stays a one-line change. pixel() returns the
same 0xAARRGGBB int QImage.pixel() returns, so palette and
transparency code is untouched.
"""

from . import pngio


def argb(r, g, b, a=255):
    """One pixel as a 0xAARRGGBB int (what pixel() returns)."""
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | \
        ((g & 0xFF) << 8) | (b & 0xFF)


def _sample_map(src_size, dst_size):
    """Source index for each destination index, nearest-neighbour.

    This reproduces Qt's FastTransformation sampling exactly — it
    was probed against QImage.scaled on 4400+ size pairs (and on
    the boundary cases where a naive formula is off by one). Qt
    walks the source in 16.16 fixed point, starting half a step
    in, and rounds exact-boundary samples DOWN (the -1): a plain
    floor((d + 0.5) * S / D) disagrees on pairs like 100 -> 3
    (Qt samples 16, 49, 83 — the middle one is 49, not 50).
    """
    inc = (src_size << 16) // dst_size
    start = inc // 2 - 1
    return [(start + d * inc) >> 16 for d in range(dst_size)]


class Image:
    """A width x height RGBA image. Pixels live in `data`, a
    bytearray of width * height * 4 bytes, row-major, (r, g, b,
    a) per pixel. New images start fully transparent."""

    __slots__ = ("_width", "_height", "data")

    def __init__(self, width, height, data=None):
        if width < 0 or height < 0:
            raise ValueError("image size cannot be negative")
        self._width = width
        self._height = height
        if data is None:
            self.data = bytearray(width * height * 4)
        else:
            if len(data) != width * height * 4:
                raise ValueError(
                    "data is %d bytes, expected %d for %d x %d"
                    % (len(data), width * height * 4, width, height))
            self.data = bytearray(data)

    def width(self):
        return self._width

    def height(self):
        return self._height

    def isNull(self):
        return self._width == 0 or self._height == 0

    def pixel(self, x, y):
        """The pixel at (x, y) as a 0xAARRGGBB int."""
        offset = (y * self._width + x) * 4
        data = self.data
        return ((data[offset + 3]) << 24) | (data[offset] << 16) | \
            (data[offset + 1] << 8) | data[offset + 2]

    def setPixel(self, x, y, value):
        """Set the pixel at (x, y) from a 0xAARRGGBB int."""
        offset = (y * self._width + x) * 4
        self.data[offset] = (value >> 16) & 0xFF
        self.data[offset + 1] = (value >> 8) & 0xFF
        self.data[offset + 2] = value & 0xFF
        self.data[offset + 3] = (value >> 24) & 0xFF

    def fill(self, value):
        """Every pixel to the 0xAARRGGBB int value."""
        r = (value >> 16) & 0xFF
        g = (value >> 8) & 0xFF
        b = value & 0xFF
        a = (value >> 24) & 0xFF
        pixel = bytes((r, g, b, a))
        self.data[:] = pixel * (self._width * self._height)

    def copy(self, x=None, y=None, w=None, h=None):
        """A copy of the whole image, or of the (x, y, w, h)
        rectangle. The rectangle must lie inside the image."""
        if x is None:
            return Image(self._width, self._height, self.data)
        if (x < 0 or y < 0 or w < 0 or h < 0
                or x + w > self._width or y + h > self._height):
            raise ValueError("copy rectangle outside the image")
        out = Image(w, h)
        src = self.data
        dst = out.data
        src_stride = self._width * 4
        for row in range(h):
            s = ((y + row) * self._width + x) * 4
            d = row * w * 4
            dst[d:d + w * 4] = src[s:s + w * 4]
        return out

    def scaled(self, width, height):
        """Nearest-neighbour resample to exactly width x height
        (Qt FastTransformation sampling — see _sample_map)."""
        if width < 1 or height < 1:
            raise ValueError("scaled size must be at least 1 x 1")
        xmap = _sample_map(self._width, width)
        ymap = _sample_map(self._height, height)
        out = Image(width, height)
        src = self.data
        dst = out.data
        src_stride = self._width * 4
        for dy in range(height):
            srow = ymap[dy] * src_stride
            drow = dy * width * 4
            for dx in range(width):
                s = srow + xmap[dx] * 4
                d = drow + dx * 4
                dst[d:d + 4] = src[s:s + 4]
        return out

    def save(self, path, fmt="PNG"):
        """Write the image to path. Only PNG is supported (the
        core's only output format). Returns True on success,
        False on failure — like QImage.save, so callers keep
        their existing checks."""
        if fmt.upper() != "PNG":
            return False
        try:
            pngio.write_png(self, path)
        except OSError:
            return False
        return True

    def __eq__(self, other):
        return (isinstance(other, Image)
                and self._width == other._width
                and self._height == other._height
                and bytes(self.data) == bytes(other.data))

    def __repr__(self):
        return "Image(%d x %d)" % (self._width, self._height)
