"""Minimal PNG reader + writer, stdlib-only (zlib).

This is what lets the Pixel Forge core load and save PNGs with no
PySide6 at all. It is a complete decoder for the PNGs the program
meets: all five color types (grayscale, RGB, palette, grayscale +
alpha, RGBA), bit depths 1/2/4/8/16, tRNS transparency, all five
row filters, and Adam7 interlacing. The writer emits plain 8-bit
RGBA, one IDAT, no ancillary chunks.

16-bit samples are reduced to 8-bit by keeping the high byte — the
same reduction Qt applies when it converts to ARGB32, so a 16-bit
PNG decodes to the same pixels here as it did through QImage.
Gamma (gAMA) and color-profile chunks are ignored, as QImage
ignores them: pixel values pass through unchanged.
"""

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Color type -> channels per pixel.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

# Adam7 passes: (x offset, y offset, x step, y step).
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
          (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))

_CRITICAL = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}


# 16-bit sample -> 8-bit, libpng's scale (see _unpack_row).
# (v * 255 + 32895) >> 16 == (v + 128) // 257 for every v in
# 0..65535 (checked exhaustively); the table is built once.
_SCALE16 = bytes((v * 255 + 32895) >> 16 for v in range(65536))


class PngError(ValueError):
    """A PNG file that is malformed or uses a feature this
    decoder does not support. Callers wrap it in PixelForgeError."""


def _chunk(data, pos):
    """One chunk at pos: (type, payload, next_pos). Verifies the
    CRC of critical chunks (libpng fails those too); a bad CRC in
    an ancillary chunk is ignored, as libpng ignores it."""
    if pos + 8 > len(data):
        raise PngError("truncated chunk header")
    length, ctype = struct.unpack_from(">I4s", data, pos)
    end = pos + 12 + length
    if end > len(data):
        raise PngError("truncated %s chunk" % ctype.decode("ascii", "replace"))
    payload = data[pos + 8:pos + 8 + length]
    crc, = struct.unpack_from(">I", data, pos + 8 + length)
    if ctype in _CRITICAL:
        actual = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        if actual != crc:
            raise PngError("bad CRC in %s chunk" % ctype.decode("ascii"))
    return ctype, payload, end


def _unfilter(raw, width, height, bpp, row_bytes):
    """Undo the per-row filters. raw is the decompressed IDAT
    data (one filter byte + row_bytes per row). Returns a list
    of unfiltered rows (bytes).

    Speed matters here — a 1080p video frame is 6 MB of filtered
    bytes, and the video path decodes one frame per kept frame.
    Up runs as one big-int add (C speed); Sub runs as a prefix
    sum per byte-lane (itertools.accumulate, C speed); Average
    and Paeth are sequential by nature, so they run as tight
    inlined loops (no per-byte function call).
    """
    from itertools import accumulate
    from operator import add

    rows = []
    prev = bytes(row_bytes)
    pos = 0
    # Big-int masks for the Up filter, built once per call.
    up_mask = int.from_bytes(b"\x7f" * row_bytes, "big")
    up_top = int.from_bytes(b"\x80" * row_bytes, "big")
    for _ in range(height):
        ftype = raw[pos]
        line = raw[pos + 1:pos + 1 + row_bytes]
        pos += 1 + row_bytes
        if ftype == 0:
            out = bytes(line)
        elif ftype == 1:  # Sub: each byte adds the one bpp back
            # Per lane this is a running sum mod 256 — accumulate
            # does the adds in C, and the mod is one map call.
            out = bytearray(row_bytes)
            for lane in range(bpp):
                sums = accumulate(line[lane::bpp], add)
                out[lane::bpp] = bytes(map((255).__and__, sums))
            out = bytes(out)
        elif ftype == 2:  # Up: add the pixel above (SWAR add)
            ai = int.from_bytes(line, "big")
            bi = int.from_bytes(prev, "big")
            total = ((ai & up_mask) + (bi & up_mask)) ^ \
                ((ai ^ bi) & up_top)
            out = total.to_bytes(row_bytes, "big")
        elif ftype == 3:  # Average: add the mean of left + up
            # Per byte-lane the chain is sequential, but within a
            # lane "left" is just the previous output — a local,
            # not an index bpp back.
            out = bytearray(row_bytes)
            for lane in range(bpp):
                raw_lane = line[lane::bpp]
                up_lane = prev[lane::bpp]
                left = 0
                for k in range(len(raw_lane)):
                    value = (raw_lane[k] + (left + up_lane[k]) // 2) \
                        & 0xFF
                    out[lane + k * bpp] = value
                    left = value
            out = bytes(out)
        elif ftype == 4:  # Paeth
            out = bytearray(row_bytes)
            for lane in range(bpp):
                raw_lane = line[lane::bpp]
                up_lane = prev[lane::bpp]
                left = 0
                upleft = 0
                for k in range(len(raw_lane)):
                    up = up_lane[k]
                    p = left + up - upleft
                    pa = p - left if p >= left else left - p
                    pb = p - up if p >= up else up - p
                    pc = p - upleft if p >= upleft else upleft - p
                    if pa <= pb and pa <= pc:
                        pred = left
                    elif pb <= pc:
                        pred = up
                    else:
                        pred = upleft
                    value = (raw_lane[k] + pred) & 0xFF
                    out[lane + k * bpp] = value
                    left = value
                    upleft = up
            out = bytes(out)
        else:
            raise PngError("unknown filter type %d" % ftype)
        rows.append(out)
        prev = out
    return rows


def _unpack_row(row, width, color_type, bit_depth, palette, trns):
    """One unfiltered row -> RGBA bytes (width * 4)."""
    if bit_depth == 8:
        if color_type == 6:  # RGBA: already the target layout
            return row[:width * 4]
        if color_type == 2:  # RGB -> RGBA, alpha 255 or tRNS
            out = bytearray(width * 4)
            out[0::4] = row[0::3]
            out[1::4] = row[1::3]
            out[2::4] = row[2::3]
            if trns is None:
                out[3::4] = b"\xff" * width
            else:
                tr, tg, tb = trns
                for x in range(width):
                    i = x * 3
                    out[x * 4 + 3] = 0 if (row[i], row[i + 1],
                                           row[i + 2]) == (tr, tg, tb) \
                        else 255
            return bytes(out)
        if color_type == 4:  # gray + alpha
            out = bytearray(width * 4)
            gray = row[0::2]
            out[0::4] = gray
            out[1::4] = gray
            out[2::4] = gray
            out[3::4] = row[1::2]
            return bytes(out)
        if color_type == 0:  # gray
            out = bytearray(width * 4)
            out[0::4] = row[:width]
            out[1::4] = row[:width]
            out[2::4] = row[:width]
            if trns is None:
                out[3::4] = b"\xff" * width
            else:
                for x in range(width):
                    out[x * 4 + 3] = 0 if row[x] == trns else 255
            return bytes(out)
        # color type 3, 8-bit palette indices
        out = bytearray(width * 4)
        for x in range(width):
            index = row[x]
            if index >= len(palette):
                raise PngError("palette index %d out of range" % index)
            r, g, b = palette[index]
            a = trns[index] if trns and index < len(trns) else 255
            o = x * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a
        return bytes(out)
    if bit_depth == 16:
        # Scale each 16-bit sample to 8-bit the way libpng does
        # (png_set_scale_16_to_8, which is what Qt uses): round
        # to nearest, (v * 255 + 32895) >> 16 — NOT the high byte.
        # Truncation disagrees on a quarter of all values (0x00FF
        # scales to 1, 0xFF00 to 254); the Qt cross-check in
        # test_pngio caught the truncation. A 64 KiB lookup table
        # keeps the per-sample cost to one index — still a Python
        # loop, so a huge 16-bit image is slow on this path, but
        # 16-bit input is rare and the Qt path (preferred when
        # PySide6 is present) decodes it at C speed.
        table = _SCALE16
        channels = _CHANNELS[color_type]
        out = bytearray(width * 4)
        if color_type == 6:
            for x in range(width):
                i = x * 8
                o = x * 4
                out[o] = table[(row[i] << 8) | row[i + 1]]
                out[o + 1] = table[(row[i + 2] << 8) | row[i + 3]]
                out[o + 2] = table[(row[i + 4] << 8) | row[i + 5]]
                out[o + 3] = table[(row[i + 6] << 8) | row[i + 7]]
        elif color_type == 2:
            for x in range(width):
                i = x * 6
                o = x * 4
                r = (row[i] << 8) | row[i + 1]
                g = (row[i + 2] << 8) | row[i + 3]
                b = (row[i + 4] << 8) | row[i + 5]
                out[o] = table[r]
                out[o + 1] = table[g]
                out[o + 2] = table[b]
                if trns is None:
                    out[o + 3] = 255
                else:
                    out[o + 3] = 0 if (r, g, b) == trns else 255
        elif color_type == 4:
            for x in range(width):
                i = x * 4
                o = x * 4
                gray = table[(row[i] << 8) | row[i + 1]]
                out[o] = gray
                out[o + 1] = gray
                out[o + 2] = gray
                out[o + 3] = table[(row[i + 2] << 8) | row[i + 3]]
        elif color_type == 0:
            for x in range(width):
                i = x * 2
                o = x * 4
                sample = (row[i] << 8) | row[i + 1]
                gray = table[sample]
                out[o] = gray
                out[o + 1] = gray
                out[o + 2] = gray
                if trns is None:
                    out[o + 3] = 255
                else:
                    out[o + 3] = 0 if sample == trns else 255
        else:
            raise PngError("16-bit palette PNGs do not exist")
        return bytes(out)
    # Sub-byte depths (1/2/4): gray or palette, samples MSB-first.
    if color_type not in (0, 3):
        raise PngError("bit depth %d is only valid for grayscale "
                       "and palette images" % bit_depth)
    max_sample = (1 << bit_depth) - 1
    out = bytearray(width * 4)
    for x in range(width):
        bitpos = x * bit_depth
        byte = row[bitpos // 8]
        shift = 8 - bit_depth - (bitpos % 8)
        sample = (byte >> shift) & max_sample
        if color_type == 0:
            gray = sample * 255 // max_sample
            a = 0 if (trns is not None and sample == trns) else 255
            o = x * 4
            out[o] = gray
            out[o + 1] = gray
            out[o + 2] = gray
            out[o + 3] = a
        else:
            if sample >= len(palette):
                raise PngError("palette index %d out of range" % sample)
            r, g, b = palette[sample]
            a = trns[sample] if trns and sample < len(trns) else 255
            o = x * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a
    return bytes(out)


def decode_png(data):
    """PNG bytes -> Image. Raises PngError on malformed input."""
    from .image import Image

    if not data.startswith(PNG_SIGNATURE):
        raise PngError("not a PNG file (bad signature)")
    pos = len(PNG_SIGNATURE)
    width = height = None
    bit_depth = color_type = interlace = None
    palette = None
    trns = None
    idat = bytearray()
    saw_iend = False
    while pos < len(data):
        ctype, payload, pos = _chunk(data, pos)
        if ctype == b"IHDR":
            if width is not None:
                raise PngError("two IHDR chunks")
            if len(payload) != 13:
                raise PngError("bad IHDR length")
            width, height, bit_depth, color_type, comp, filt, \
                interlace = struct.unpack(">IIBBBBB", payload)
            if comp != 0 or filt != 0:
                raise PngError("unknown compression/filter method")
            if interlace not in (0, 1):
                raise PngError("unknown interlace method %d" % interlace)
            if color_type not in _CHANNELS:
                raise PngError("unknown color type %d" % color_type)
            if bit_depth not in (1, 2, 4, 8, 16):
                raise PngError("unknown bit depth %d" % bit_depth)
            if color_type == 3 and bit_depth == 16:
                raise PngError("16-bit palette PNGs do not exist")
            if color_type in (2, 4, 6) and bit_depth not in (8, 16):
                raise PngError("bit depth %d is invalid for color "
                               "type %d" % (bit_depth, color_type))
            if width < 1 or height < 1:
                raise PngError("zero-size PNG")
        elif ctype == b"PLTE":
            if len(payload) % 3:
                raise PngError("PLTE length is not a multiple of 3")
            palette = [tuple(payload[i:i + 3])
                       for i in range(0, len(payload), 3)]
        elif ctype == b"tRNS":
            if color_type == 3:
                trns = list(payload)
            elif color_type == 0:
                if len(payload) != 2:
                    raise PngError("bad tRNS length for grayscale")
                trns, = struct.unpack(">H", payload)
            elif color_type == 2:
                if len(payload) != 6:
                    raise PngError("bad tRNS length for RGB")
                trns = struct.unpack(">HHH", payload)
            # tRNS on a type with its own alpha is ignored.
        elif ctype == b"IDAT":
            idat += payload
        elif ctype == b"IEND":
            saw_iend = True
            break
        elif ctype in _CRITICAL:
            raise PngError("unknown critical chunk %s"
                           % ctype.decode("ascii", "replace"))
        # Unknown ancillary chunks are skipped, per the spec.
    if width is None:
        raise PngError("no IHDR chunk")
    if color_type == 3 and palette is None:
        raise PngError("palette image without a PLTE chunk")
    if not idat:
        raise PngError("no IDAT chunk")
    if not saw_iend:
        raise PngError("no IEND chunk")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError("bad compressed data: %s" % exc)

    channels = _CHANNELS[color_type]
    bits_per_pixel = channels * bit_depth
    image = Image(width, height)
    out = image.data
    if interlace == 0:
        row_bytes = (width * bits_per_pixel + 7) // 8
        if len(raw) < height * (row_bytes + 1):
            raise PngError("not enough pixel data")
        bpp = max(1, bits_per_pixel // 8)
        rows = _unfilter(raw, width, height, bpp, row_bytes)
        for y, row in enumerate(rows):
            rgba = _unpack_row(row, width, color_type, bit_depth,
                               palette, trns)
            out[y * width * 4:(y + 1) * width * 4] = rgba
    else:
        pos = 0
        for x0, y0, dx, dy in _ADAM7:
            pw = (width - x0 + dx - 1) // dx if x0 < width else 0
            ph = (height - y0 + dy - 1) // dy if y0 < height else 0
            if pw == 0 or ph == 0:
                continue
            row_bytes = (pw * bits_per_pixel + 7) // 8
            need = ph * (row_bytes + 1)
            if len(raw) - pos < need:
                raise PngError("not enough pixel data (pass %d, %d)"
                               % (x0, y0))
            bpp = max(1, bits_per_pixel // 8)
            rows = _unfilter(raw[pos:pos + need], pw, ph, bpp,
                             row_bytes)
            pos += need
            for py, row in enumerate(rows):
                rgba = _unpack_row(row, pw, color_type, bit_depth,
                                   palette, trns)
                y = y0 + py * dy
                for px in range(pw):
                    x = x0 + px * dx
                    o = (y * width + x) * 4
                    out[o:o + 4] = rgba[px * 4:px * 4 + 4]
    return image


def read_png(path):
    """Read a PNG file into an Image. Raises PngError on a
    malformed file, OSError when the file cannot be read."""
    with open(path, "rb") as handle:
        return decode_png(handle.read())


def encode_png(image):
    """An Image as PNG bytes: 8-bit RGBA, filter 0 per row, one
    IDAT, no ancillary chunks."""
    width, height = image.width(), image.height()
    raw = bytearray()
    data = image.data
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter: None
        raw += data[y * stride:(y + 1) * stride]

    def chunk(ctype, payload):
        out = struct.pack(">I", len(payload)) + ctype + payload
        return out + struct.pack(
            ">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)

    png = bytearray(PNG_SIGNATURE)
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                      8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    return bytes(png)


def write_png(image, path):
    """Write an Image to path as a PNG. Raises OSError on
    failure."""
    with open(path, "wb") as handle:
        handle.write(encode_png(image))
