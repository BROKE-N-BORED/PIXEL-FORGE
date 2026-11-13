"""PNG codec tests (task 64) — pixel_forge/pngio.py, the stdlib
codec the decoupled core reads and writes with.

The test PNGs are built here by an independent minimal encoder
(struct + zlib, straight from the PNG spec) — never by pngio
itself, so a codec bug cannot hide behind its own writer.
Expected pixels are hand-computed per case.

Qt-free, except PngQtCrossCheckTests, which decodes the same
files with QImage and compares — skipped without PySide6, so
this module stays unguarded.
"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.image import Image, argb  # noqa: E402
from pixel_forge.pngio import (PngError, decode_png, encode_png,  # noqa: E402
                               read_png, write_png)

try:
    from PySide6.QtGui import QImage  # noqa: F401
    HAS_QT = True
except ImportError:
    HAS_QT = False

HAS_FFMPEG = subprocess.run(
    ["ffmpeg", "-version"], capture_output=True).returncode == 0

ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
         (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _chunk(typ, data):
    out = struct.pack(">I", len(data)) + typ + data
    return out + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)


def _paeth(left, up, upleft):
    p = left + up - upleft
    pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return upleft


def _filter_row(ftype, row, prev, bpp):
    """Forward-filter one row (the inverse of pngio._unfilter)."""
    out = bytearray(len(row))
    for i, value in enumerate(row):
        left = row[i - bpp] if i >= bpp else 0
        up = prev[i] if prev is not None else 0
        upleft = prev[i - bpp] if prev is not None and i >= bpp else 0
        if ftype == 0:
            pred = 0
        elif ftype == 1:
            pred = left
        elif ftype == 2:
            pred = up
        elif ftype == 3:
            pred = (left + up) // 2
        else:
            pred = _paeth(left, up, upleft)
        out[i] = (value - pred) & 0xFF
    return bytes(out)


def _pack_samples(samples, bit_depth):
    """Sample values -> MSB-first packed bytes."""
    out = bytearray()
    acc = 0
    nbits = 0
    for sample in samples:
        acc = (acc << bit_depth) | sample
        nbits += bit_depth
        if nbits == 8:
            out.append(acc)
            acc = 0
            nbits = 0
    if nbits:
        out.append(acc << (8 - nbits))
    return bytes(out)


def _rgba(*pixels):
    """Expected RGBA bytes from (r, g, b, a) tuples."""
    out = bytearray()
    for pixel in pixels:
        out += bytes(pixel)
    return bytes(out)


def _encode(width, height, color_type, bit_depth, rows,
            palette=None, trns=None, interlace=0, filters=None,
            extra_chunks=(), ihdr_override=None, idat_parts=1,
            reset_at=()):
    """Build a PNG from unfiltered packed scanline rows.

    rows: one packed row per scanline (for interlaced images, the
    pass rows concatenated in Adam7 order — see _adam7_rows).
    filters: one filter type per row (default 0). palette: a list
    of (r, g, b). trns: palette -> list of alphas; gray -> int;
    RGB -> (r, g, b) of 8- or 16-bit values. reset_at: row
    indices where filtering restarts with no row above (the
    first row of each Adam7 pass — the spec resets there).
    """
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp = max(1, channels * bit_depth // 8)
    filters = filters or [0] * len(rows)
    resets = set(reset_at)
    raw = bytearray()
    prev = None
    for index, (row, ftype) in enumerate(zip(rows, filters)):
        if index in resets:
            prev = None
        raw.append(ftype)
        raw += _filter_row(ftype, row, prev, bpp)
        prev = row
    ihdr = ihdr_override if ihdr_override is not None else \
        struct.pack(">IIBBBBB", width, height, bit_depth,
                    color_type, 0, 0, interlace)
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png += _chunk(b"IHDR", ihdr)
    if palette is not None:
        png += _chunk(b"PLTE", b"".join(bytes(c) for c in palette))
    if trns is not None:
        if color_type == 3:
            payload = bytes(trns)
        elif color_type == 0:
            payload = struct.pack(">H", trns)
        else:
            payload = struct.pack(">HHH", *trns)
        png += _chunk(b"tRNS", payload)
    for typ, payload in extra_chunks:
        png += _chunk(typ, payload)
    compressed = zlib.compress(bytes(raw))
    # Split the IDAT payload across idat_parts chunks — a real
    # encoder may do this, and the decoder must concatenate them.
    part = (len(compressed) + idat_parts - 1) // idat_parts
    for i in range(0, len(compressed), part):
        png += _chunk(b"IDAT", compressed[i:i + part])
    png += _chunk(b"IEND", b"")
    return bytes(png)


def _adam7_rows(width, height, rows, sample_bytes):
    """Full packed rows -> (Adam7 pass rows, pass-start indices)
    (8-bit samples only: sample_bytes is 3 for RGB, 1 for
    palette/gray). The pass starts are the rows where filtering
    restarts — pass them to _encode as reset_at."""
    out = []
    starts = []
    for x0, y0, dx, dy in ADAM7:
        pw = len(range(x0, width, dx))
        ph = len(range(y0, height, dy))
        if pw == 0 or ph == 0:
            continue
        starts.append(len(out))
        for py in range(ph):
            y = y0 + py * dy
            row = bytearray()
            for px in range(pw):
                x = x0 + px * dx
                start = x * sample_bytes
                row += rows[y][start:start + sample_bytes]
            out.append(bytes(row))
    return out, starts


class PngDecodeTests(unittest.TestCase):
    def test_rgb8_all_filters(self):
        # 5 rows, one filter type each — every filter must undo.
        rows = [bytes((10, 20, 30, 40, 50, 60)),
                bytes((70, 80, 90, 100, 110, 120)),
                bytes((200, 210, 220, 230, 240, 250)),
                bytes((1, 2, 3, 4, 5, 6)),
                bytes((255, 254, 253, 252, 251, 250))]
        data = _encode(2, 5, 2, 8, rows, filters=[0, 1, 2, 3, 4])
        image = decode_png(data)
        self.assertEqual((image.width(), image.height()), (2, 5))
        self.assertEqual(bytes(image.data), _rgba(
            (10, 20, 30, 255), (40, 50, 60, 255),
            (70, 80, 90, 255), (100, 110, 120, 255),
            (200, 210, 220, 255), (230, 240, 250, 255),
            (1, 2, 3, 255), (4, 5, 6, 255),
            (255, 254, 253, 255), (252, 251, 250, 255)))

    def test_rgba8(self):
        rows = [bytes((255, 0, 0, 255, 0, 255, 0, 128)),
                bytes((0, 0, 255, 0, 10, 20, 30, 1))]
        image = decode_png(_encode(2, 2, 6, 8, rows))
        self.assertEqual(bytes(image.data), _rgba(
            (255, 0, 0, 255), (0, 255, 0, 128),
            (0, 0, 255, 0), (10, 20, 30, 1)))

    def test_rgb8_trns(self):
        rows = [bytes((255, 0, 0, 0, 255, 0))]
        image = decode_png(_encode(2, 1, 2, 8, rows,
                                   trns=(255, 0, 0)))
        self.assertEqual(bytes(image.data), _rgba(
            (255, 0, 0, 0), (0, 255, 0, 255)))

    def test_gray8_and_trns(self):
        rows = [bytes((0, 128, 255))]
        image = decode_png(_encode(3, 1, 0, 8, rows))
        self.assertEqual(bytes(image.data), _rgba(
            (0, 0, 0, 255), (128, 128, 128, 255),
            (255, 255, 255, 255)))
        image = decode_png(_encode(3, 1, 0, 8, rows, trns=128))
        self.assertEqual(bytes(image.data), _rgba(
            (0, 0, 0, 255), (128, 128, 128, 0),
            (255, 255, 255, 255)))

    def test_gray_alpha8(self):
        rows = [bytes((10, 255, 20, 0, 30, 128))]
        image = decode_png(_encode(3, 1, 4, 8, rows))
        self.assertEqual(bytes(image.data), _rgba(
            (10, 10, 10, 255), (20, 20, 20, 0),
            (30, 30, 30, 128)))

    def test_gray_subbyte_depths(self):
        # 1-bit: 0 -> 0, 1 -> 255. 2-bit: v * 85. 4-bit: v * 17.
        rows = [_pack_samples([0, 1, 1, 0, 1, 0, 0, 1], 1)]
        image = decode_png(_encode(8, 1, 0, 1, rows))
        self.assertEqual(bytes(image.data), _rgba(
            (0, 0, 0, 255), (255, 255, 255, 255),
            (255, 255, 255, 255), (0, 0, 0, 255),
            (255, 255, 255, 255), (0, 0, 0, 255),
            (0, 0, 0, 255), (255, 255, 255, 255)))
        rows = [_pack_samples([0, 1, 2, 3], 2)]
        image = decode_png(_encode(4, 1, 0, 2, rows))
        self.assertEqual(bytes(image.data), _rgba(
            (0, 0, 0, 255), (85, 85, 85, 255),
            (170, 170, 170, 255), (255, 255, 255, 255)))
        rows = [_pack_samples([0, 5, 10, 15], 4)]
        image = decode_png(_encode(4, 1, 0, 4, rows))
        self.assertEqual(bytes(image.data), _rgba(
            (0, 0, 0, 255), (85, 85, 85, 255),
            (170, 170, 170, 255), (255, 255, 255, 255)))

    def test_16bit_scales_like_libpng(self):
        # libpng's scale (what Qt uses): round to nearest, NOT
        # the high byte — 0x00FF scales to 1 and 0xFF00 to 254.
        # (The high byte agrees on 0xFF80 -> 255 and 0x1234 -> 18,
        # so those cases alone would not catch a truncation bug.)
        row = struct.pack(">HHH", 0xFF80, 0x1234, 0x00FF)
        image = decode_png(_encode(1, 1, 2, 16, [row]))
        self.assertEqual(bytes(image.data),
                         _rgba((255, 0x12, 1, 255)))
        row = struct.pack(">HHH", 0xFF00, 0x7FFF, 0x8080)
        image = decode_png(_encode(1, 1, 2, 16, [row]))
        self.assertEqual(bytes(image.data),
                         _rgba((254, 127, 128, 255)))
        # Gray 16-bit + gray+alpha 16-bit.
        row = struct.pack(">H", 0xABCD)
        image = decode_png(_encode(1, 1, 0, 16, [row]))
        self.assertEqual(bytes(image.data),
                         _rgba((0xAB, 0xAB, 0xAB, 255)))
        row = struct.pack(">HH", 0x8000, 0x4000)
        image = decode_png(_encode(1, 1, 4, 16, [row]))
        self.assertEqual(bytes(image.data),
                         _rgba((0x80, 0x80, 0x80, 0x40)))
        row = struct.pack(">HHHH", 0xFF00, 0x8000, 0x1000, 0x0000)
        image = decode_png(_encode(1, 1, 6, 16, [row]))
        self.assertEqual(bytes(image.data),
                         _rgba((254, 0x80, 0x10, 0)))

    def test_palette8_and_trns(self):
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        rows = [bytes((0, 1, 2, 1))]
        image = decode_png(_encode(4, 1, 3, 8, rows,
                                   palette=palette))
        self.assertEqual(bytes(image.data), _rgba(
            (255, 0, 0, 255), (0, 255, 0, 255),
            (0, 0, 255, 255), (0, 255, 0, 255)))
        # tRNS shorter than the palette: missing entries are opaque.
        image = decode_png(_encode(4, 1, 3, 8, rows,
                                   palette=palette, trns=[0, 128]))
        self.assertEqual(bytes(image.data), _rgba(
            (255, 0, 0, 0), (0, 255, 0, 128),
            (0, 0, 255, 255), (0, 255, 0, 128)))

    def test_palette_subbyte_depths(self):
        palette = [(10, 20, 30), (40, 50, 60), (70, 80, 90),
                   (100, 110, 120)]
        rows = [_pack_samples([0, 1, 2, 3, 3, 2, 1, 0], 2)]
        image = decode_png(_encode(8, 1, 3, 2, rows,
                                   palette=palette))
        self.assertEqual(bytes(image.data), _rgba(
            (10, 20, 30, 255), (40, 50, 60, 255),
            (70, 80, 90, 255), (100, 110, 120, 255),
            (100, 110, 120, 255), (70, 80, 90, 255),
            (40, 50, 60, 255), (10, 20, 30, 255)))
        rows = [_pack_samples([1, 0], 1)]
        image = decode_png(_encode(2, 1, 3, 1, rows,
                                   palette=palette))
        self.assertEqual(bytes(image.data), _rgba(
            (40, 50, 60, 255), (10, 20, 30, 255)))

    def test_split_idat(self):
        rows = [bytes((9, 8, 7, 6, 5, 4))]
        image = decode_png(_encode(2, 1, 2, 8, rows, idat_parts=3))
        self.assertEqual(bytes(image.data), _rgba(
            (9, 8, 7, 255), (6, 5, 4, 255)))

    def test_ancillary_chunks_ignored(self):
        # A gAMA chunk (ignored) and a tEXt chunk with a
        # deliberately bad CRC (libpng ignores ancillary CRCs).
        bad_crc_text = (struct.pack(">I", 4) + b"tEXt" + b"bad!" +
                        struct.pack(">I", 0xDEADBEEF))
        rows = [bytes((1, 2, 3))]
        data = _encode(1, 1, 2, 8, rows,
                       extra_chunks=[(b"gAMA",
                                      struct.pack(">I", 45455))])
        # Splice the bad-CRC chunk in after the IHDR.
        ihdr_end = 8 + 12 + 13
        data = data[:ihdr_end] + bad_crc_text + data[ihdr_end:]
        image = decode_png(data)
        self.assertEqual(bytes(image.data), _rgba((1, 2, 3, 255)))

    def test_interlaced_rgb8(self):
        # 9 x 7: every Adam7 pass is non-empty.
        pixels = [[((x * 29 + y * 7) % 256, (x * 3 + y * 41) % 256,
                    (x * 17 + y * 13) % 256)
                   for x in range(9)] for y in range(7)]
        rows = [b"".join(bytes(p) for p in row) for row in pixels]
        pass_rows, resets = _adam7_rows(9, 7, rows, 3)
        # 14 pass rows for a 9 x 7 image; mix the filter types.
        self.assertEqual(len(pass_rows), 14)
        data = _encode(9, 7, 2, 8, pass_rows, interlace=1,
                       filters=[1, 2, 0, 3, 4, 1, 2, 0, 3, 4,
                                1, 2, 0, 3], reset_at=resets)
        image = decode_png(data)
        self.assertEqual((image.width(), image.height()), (9, 7))
        expected = _rgba(*[p + (255,) for row in pixels
                           for p in row])
        self.assertEqual(bytes(image.data), expected)

    def test_interlaced_palette8(self):
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                   (255, 255, 0)]
        indices = [[(x + y) % 4 for x in range(5)]
                   for y in range(5)]
        rows = [bytes(row) for row in indices]
        pass_rows, resets = _adam7_rows(5, 5, rows, 1)
        data = _encode(5, 5, 3, 8, pass_rows, palette=palette,
                       interlace=1, reset_at=resets)
        image = decode_png(data)
        expected = _rgba(*[palette[i] + (255,)
                           for row in indices for i in row])
        self.assertEqual(bytes(image.data), expected)


class PngEncodeTests(unittest.TestCase):
    def test_roundtrip_preserves_pixels(self):
        image = Image(5, 3)
        for y in range(3):
            for x in range(5):
                image.setPixel(x, y, argb(x * 50, y * 80, 200 - x,
                                          (x + y * 5) * 10))
        back = decode_png(encode_png(image))
        self.assertEqual((back.width(), back.height()), (5, 3))
        self.assertEqual(bytes(back.data), bytes(image.data))

    def test_roundtrip_of_decoded_file(self):
        rows = [bytes((255, 0, 0, 255, 0, 255, 0, 128)),
                bytes((0, 0, 255, 0, 10, 20, 30, 1))]
        first = decode_png(_encode(2, 2, 6, 8, rows))
        second = decode_png(encode_png(first))
        self.assertEqual(bytes(second.data), bytes(first.data))

    def test_writer_output_is_well_formed(self):
        image = Image(2, 2)
        image.setPixel(0, 0, argb(1, 2, 3, 255))
        data = encode_png(image)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        # Walk the chunks: each CRC must verify, and the file
        # must be exactly IHDR, one IDAT, IEND.
        pos = 8
        seen = []
        while pos < len(data):
            length, ctype = struct.unpack_from(">I4s", data, pos)
            payload = data[pos + 8:pos + 8 + length]
            crc, = struct.unpack_from(">I", data, pos + 8 + length)
            self.assertEqual(
                crc, zlib.crc32(ctype + payload) & 0xFFFFFFFF)
            seen.append(ctype)
            pos += 12 + length
        self.assertEqual(seen, [b"IHDR", b"IDAT", b"IEND"])

    def test_read_write_file_roundtrip(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "roundtrip.png")
        image = Image(4, 4)
        image.fill(argb(9, 8, 7, 6))
        write_png(image, path)
        back = read_png(path)
        self.assertEqual(bytes(back.data), bytes(image.data))


class PngErrorTests(unittest.TestCase):
    def _valid(self):
        return _encode(2, 1, 2, 8, [bytes((1, 2, 3, 4, 5, 6))])

    def test_bad_signature(self):
        with self.assertRaises(PngError):
            decode_png(b"not a png at all, just text........")
        with self.assertRaises(PngError):
            decode_png(b"")

    def test_truncated_file(self):
        data = self._valid()
        with self.assertRaises(PngError):
            decode_png(data[:len(data) - 6])

    def test_bad_ihdr_crc(self):
        data = bytearray(self._valid())
        # Corrupt one byte of the IHDR CRC (offset 8+8+13 = 29).
        data[29] ^= 0xFF
        with self.assertRaises(PngError):
            decode_png(bytes(data))

    def test_no_ihdr(self):
        data = bytearray(self._valid())
        # Turn the IHDR chunk type into a skipped ancillary chunk.
        data[12:16] = b"abCD"
        with self.assertRaises(PngError):
            decode_png(bytes(data))

    def test_unknown_color_type(self):
        ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 1, 0, 0, 0)
        data = _encode(2, 1, 2, 8, [bytes((1, 2, 3, 4, 5, 6))],
                       ihdr_override=ihdr)
        with self.assertRaises(PngError):
            decode_png(data)

    def test_bad_bit_depth_for_color_type(self):
        ihdr = struct.pack(">IIBBBBB", 2, 1, 1, 2, 0, 0, 0)
        data = _encode(2, 1, 2, 8, [bytes((1, 2, 3, 4, 5, 6))],
                       ihdr_override=ihdr)
        with self.assertRaises(PngError):
            decode_png(data)

    def test_unknown_interlace_method(self):
        ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 2)
        data = _encode(2, 1, 2, 8, [bytes((1, 2, 3, 4, 5, 6))],
                       ihdr_override=ihdr)
        with self.assertRaises(PngError):
            decode_png(data)

    def test_palette_without_plte(self):
        data = _encode(2, 1, 3, 8, [bytes((0, 1))])
        with self.assertRaises(PngError):
            decode_png(data)

    def test_palette_index_out_of_range(self):
        palette = [(255, 0, 0)]
        data = _encode(2, 1, 3, 8, [bytes((0, 1))],
                       palette=palette)
        with self.assertRaises(PngError):
            decode_png(data)

    def test_bad_compressed_data(self):
        data = bytearray(self._valid())
        # Corrupt a byte inside the IDAT payload.
        idat = data.index(b"IDAT")
        data[idat + 8] ^= 0xFF
        with self.assertRaises(PngError):
            decode_png(bytes(data))

    def test_unknown_filter_type(self):
        rows = [bytes((1, 2, 3, 4, 5, 6))]
        data = _encode(2, 1, 2, 8, rows, filters=[9])
        with self.assertRaises(PngError):
            decode_png(data)

    def test_zero_size(self):
        ihdr = struct.pack(">IIBBBBB", 0, 1, 8, 2, 0, 0, 0)
        data = _encode(2, 1, 2, 8, [bytes((1, 2, 3, 4, 5, 6))],
                       ihdr_override=ihdr)
        with self.assertRaises(PngError):
            decode_png(data)


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class PngQtCrossCheckTests(unittest.TestCase):
    """pngio and Qt's libpng must decode the same file to the
    same pixels — this pins the two decoders together, so the
    Qt-preferred decode in load_image cannot drift from the
    stdlib path (see pixelize.load_image)."""

    @classmethod
    def setUpClass(cls):
        from helpers import ensure_app
        ensure_app()

    @staticmethod
    def _qimage_rgba(data):
        qimg = QImage.fromData(data, "PNG")
        assert not qimg.isNull(), "Qt could not read the test PNG"
        qimg = qimg.convertToFormat(
            QImage.Format.Format_ARGB32)
        out = bytearray()
        for y in range(qimg.height()):
            for x in range(qimg.width()):
                px = qimg.pixel(x, y)
                out += bytes(((px >> 16) & 0xFF, (px >> 8) & 0xFF,
                              px & 0xFF, (px >> 24) & 0xFF))
        return qimg.width(), qimg.height(), bytes(out)

    def _check(self, data):
        mine = decode_png(data)
        w, h, rgba = self._qimage_rgba(data)
        self.assertEqual((mine.width(), mine.height()), (w, h))
        self.assertEqual(bytes(mine.data), rgba)

    def test_crosscheck_all_color_types(self):
        cases = [
            _encode(3, 2, 2, 8, [bytes((10, 20, 30, 40, 50, 60,
                                        70, 80, 90)),
                                 bytes((1, 2, 3, 4, 5, 6, 7, 8, 9))],
                    filters=[4, 3]),
            _encode(2, 2, 6, 8, [bytes((255, 0, 0, 255, 0, 255,
                                        0, 128)),
                                 bytes((0, 0, 255, 0, 10, 20, 30,
                                        1))]),
            _encode(3, 1, 0, 8, [bytes((0, 128, 255))]),
            _encode(3, 1, 4, 8, [bytes((10, 255, 20, 0, 30, 128))]),
            _encode(4, 1, 3, 8, [bytes((0, 1, 2, 1))],
                    palette=[(255, 0, 0), (0, 255, 0),
                             (0, 0, 255)], trns=[0, 128]),
            _encode(1, 1, 2, 16, [struct.pack(">HHH", 0xFF80,
                                              0x1234, 0x00FF)]),
            _encode(8, 1, 0, 1, [_pack_samples(
                [0, 1, 1, 0, 1, 0, 0, 1], 1)]),
            _encode(4, 1, 0, 2, [_pack_samples([0, 1, 2, 3], 2)]),
            _encode(4, 1, 0, 4, [_pack_samples([0, 5, 10, 15], 4)]),
        ]
        for data in cases:
            with self.subTest(data=data[:40]):
                self._check(data)

    def test_crosscheck_interlaced(self):
        pixels = [[((x * 29 + y * 7) % 256, (x * 3 + y * 41) % 256,
                    (x * 17 + y * 13) % 256)
                   for x in range(9)] for y in range(7)]
        rows = [b"".join(bytes(p) for p in row) for row in pixels]
        pass_rows, resets = _adam7_rows(9, 7, rows, 3)
        data = _encode(9, 7, 2, 8, pass_rows,
                       interlace=1, reset_at=resets)
        self._check(data)

    def test_crosscheck_writer_output(self):
        # The other direction: Qt reads a PNG pngio wrote.
        image = Image(6, 4)
        for y in range(4):
            for x in range(6):
                image.setPixel(x, y, argb(x * 40, y * 60, 100,
                                          255 - x * 10))
        w, h, rgba = self._qimage_rgba(encode_png(image))
        self.assertEqual((w, h), (6, 4))
        self.assertEqual(rgba, bytes(image.data))

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
    def test_crosscheck_ffmpeg_png(self):
        # A PNG written by a real tool (ffmpeg, Paeth-heavy).
        tmp = tempfile.mkdtemp()
        frame = os.path.join(tmp, "frame.png")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=duration=0.1:size=64x48:rate=8",
             "-frames:v", "1", frame],
            check=True, timeout=60)
        with open(frame, "rb") as handle:
            data = handle.read()
        self._check(data)
