"""Pixel Forge core — image to pixel sprite, no AI, no network.

Pipeline: load into a plain RGBA buffer (image.Image), downscale
FIRST with nearest-neighbour sampling (honest pixel art: each
output pixel is one sampled source pixel), then quantize the small
image in pure Python. Quantizing after the downscale keeps the
pure-Python step fast — the image is at most a few hundred pixels
on a side by then.

This module never imports Qt at module level: the core runs with
no PySide6 at all, so the command line works on a bare system
python. PNGs decode through the stdlib codec (pngio) when Qt is
absent; when PySide6 IS present, decoding goes through Qt's libpng
instead — the stdlib decoder is complete and correct, but ~100x
slower than libpng on Paeth-heavy photos (a 1080p frame: ~3.3 s
vs ~0.03 s), and the GUI must not freeze for half a minute on a
big PNG import. The two decoders are pinned pixel-identical by
the cross-check in tests/test_pngio.py, and the reference-output
test runs the stdlib path (bare python) against the v0.18 outputs.

Transparency: pixels with alpha 0 stay transparent, are excluded
from the palette, and take no part in dithering. Other pixels keep
their original alpha value; only their color is quantized.
"""

import sys
from pathlib import Path

from . import pngio
from .image import Image
from .palettes import (NAMED_PALETTES, median_cut, nearest_color,
                       select_palette_colors)

MIN_COLORS = 2
MAX_COLORS = 256


def plural(count, singular, plural_form=None):
    """'1 asset' / '7 assets' — never the programmer phrasing
    with a parenthesized s (task 22). Lives here because every
    module that talks to the user already imports this one."""
    if count == 1:
        return "1 %s" % singular
    if plural_form is None:
        plural_form = singular + "s"
    return "%d %s" % (count, plural_form)


class PixelForgeError(Exception):
    """A readable, user-facing error (bad input, unreadable file,
    failed save). Callers print the message, never a traceback."""


def load_image(path):
    """Load an image file into an Image (32-bit RGBA buffer).

    With PySide6 present, whatever Qt reads (PNG, JPG, BMP,
    WebP, GIF first frame, and more). Without it, PNG only,
    through the stdlib codec — any other format refuses plainly
    with the fix (install PySide6) instead of a traceback.
    Raises PixelForgeError with a readable message when the
    file is missing or unreadable.
    """
    p = Path(path)
    if not p.is_file():
        raise PixelForgeError("not found: %s" % path)
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QImage
        from .qtimage import from_qimage
    except ImportError:
        qimage = None
    else:
        # QImage needs an application instance so Qt finds its
        # image format plugins (notably JPEG) in a headless
        # process. The GUI already has one; the CLI makes one
        # here, on first use, instead of at startup.
        if QCoreApplication.instance() is None:
            QCoreApplication(sys.argv)
        qimage = QImage(str(p))
        if qimage.isNull():
            raise PixelForgeError(
                "could not read as an image: %s "
                "(unsupported format or damaged file)" % path)
        return from_qimage(qimage)
    # No Qt: PNG through the stdlib codec, nothing else.
    try:
        with open(p, "rb") as handle:
            signature = handle.read(8)
    except OSError as exc:
        raise PixelForgeError("could not read %s: %s" % (path, exc))
    if signature != pngio.PNG_SIGNATURE:
        raise PixelForgeError(
            "%s is not a PNG — PySide6 is not installed, so only "
            "PNG files load. Install PySide6 to load JPG, BMP and "
            "other formats." % path)
    try:
        return pngio.read_png(p)
    except pngio.PngError:
        raise PixelForgeError(
            "could not read as an image: %s "
            "(unsupported format or damaged file)" % path)
    except OSError as exc:
        raise PixelForgeError("could not read %s: %s" % (path, exc))


def compute_target_size(src_w, src_h, width, height, keep_aspect):
    """Output dimensions for a source of src_w x src_h.

    Without keep-aspect the output is exactly width x height. With it,
    the source is fitted inside the width x height box, keeping its
    aspect ratio, never smaller than 1 pixel on a side.

    A source of 0 (or negative) width or height is rejected with a
    plain error — with keep-aspect the fit divides by the source
    dimensions, so an empty source used to raise ZeroDivisionError
    (review B11). The guard runs for both aspect modes: a 0-size
    source has no size to compute from, whatever the mode.
    """
    if src_w < 1 or src_h < 1:
        raise PixelForgeError(
            "cannot size a %d x %d image" % (src_w, src_h))
    if width < 1 or height < 1:
        raise PixelForgeError(
            "size must be at least 1 pixel in each direction")
    if not keep_aspect:
        return width, height
    scale = min(width / src_w, height / src_h)
    return (max(1, round(src_w * scale)),
            max(1, round(src_h * scale)))


def downscale(image, width, height, keep_aspect=False):
    """Downscale with nearest-neighbour sampling — the same
    sampling Qt's FastTransformation uses (Image.scaled
    reproduces it exactly, probed against QImage.scaled)."""
    target_w, target_h = compute_target_size(
        image.width(), image.height(), width, height, keep_aspect)
    return image.scaled(target_w, target_h)


def opaque_pixels(image):
    """RGB tuples of every pixel with alpha > 0."""
    colors = []
    data = image.data
    for i in range(0, len(data), 4):
        if data[i + 3]:
            colors.append((data[i], data[i + 1], data[i + 2]))
    return colors


def count_opaque_colors(image):
    """How many distinct colors the opaque pixels use."""
    return len(set(opaque_pixels(image)))


def resolve_palette(image, palette_name, max_colors):
    """The working palette for a downscaled image.

    "image" builds a median-cut palette from the image's own opaque
    pixels; named palettes keep the max_colors entries the image
    actually uses most (a named palette smaller than max_colors is
    used whole).
    """
    if max_colors < MIN_COLORS or max_colors > MAX_COLORS:
        raise PixelForgeError(
            "colors must be between %d and %d" % (MIN_COLORS, MAX_COLORS))
    if palette_name == "image":
        colors = opaque_pixels(image)
        if not colors:
            return [(0, 0, 0)]
        return median_cut(colors, max_colors)
    if palette_name not in NAMED_PALETTES:
        raise PixelForgeError(
            "unknown palette: %s (pick one of: image, %s)"
            % (palette_name, ", ".join(sorted(NAMED_PALETTES))))
    named = NAMED_PALETTES[palette_name]
    if len(named) <= max_colors:
        return list(named)
    return select_palette_colors(named, opaque_pixels(image), max_colors)


# 4x4 Bayer matrix — ordered-dithering thresholds, 0-15.
BAYER_4X4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)
# Threshold swing in channel steps: each pixel is nudged by up to
# +/-32 before the nearest-color lookup.
BAYER_SPREAD = 64


def quantize(image, palette, dither=True):
    """Map every pixel of a small image onto the palette.

    With dithering, ordered (Bayer) thresholds nudge each pixel
    before the nearest-color lookup, so flat areas break into a
    stable two-color pattern instead of banding — the classic
    crosshatch pixel-art look. Floyd-Steinberg error diffusion
    used to do this job, but on tiny palettes its feedback loop
    runs away: a flat red area on the Game Boy palette came out
    almost entirely the lightest shade, worse than no dithering
    at all. Ordered dithering has no feedback, so it cannot
    drift. Transparent pixels are skipped entirely.

    An empty palette raises PixelForgeError — the lookup used to
    raise a bare IndexError (review B14). The guard runs up front,
    so a fully transparent image fails the same way as any other.
    """
    if not palette:
        raise PixelForgeError("cannot quantize with an empty palette")
    width, height = image.width(), image.height()
    out = Image(width, height)
    src = image.data
    dst = out.data
    cache = {}
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            offset = row + x * 4
            alpha = src[offset + 3]
            if alpha == 0:
                continue  # out starts transparent
            r = src[offset]
            g = src[offset + 1]
            b = src[offset + 2]
            if dither:
                threshold = (BAYER_4X4[y % 4][x % 4] + 0.5) / 16.0 - 0.5
                shift = int(round(threshold * BAYER_SPREAD))
                r = min(255, max(0, r + shift))
                g = min(255, max(0, g + shift))
                b = min(255, max(0, b + shift))
            nr, ng, nb = nearest_color((r, g, b), palette, cache)
            dst[offset] = nr
            dst[offset + 1] = ng
            dst[offset + 2] = nb
            dst[offset + 3] = alpha
    return out


def pixelize_image(image, width=32, height=None, keep_aspect=False,
                   palette="image", max_colors=16, dither=True):
    """Full pipeline on an in-memory image: downscale, then quantize."""
    if height is None:
        height = width
    small = downscale(image, width, height, keep_aspect)
    working = resolve_palette(small, palette, max_colors)
    return quantize(small, working, dither)


def pixelize_file(input_path, output_path, width=32, height=None,
                  keep_aspect=False, palette="image", max_colors=16,
                  dither=True):
    """Full pipeline on files. Returns (width, height, colors_used).

    Saves a transparent PNG. Raises PixelForgeError with a readable
    message on any failure.
    """
    image = load_image(input_path)
    result = pixelize_image(image, width=width, height=height,
                            keep_aspect=keep_aspect, palette=palette,
                            max_colors=max_colors, dither=dither)
    if not result.save(str(output_path), "PNG"):
        raise PixelForgeError("could not write: %s" % output_path)
    return result.width(), result.height(), count_opaque_colors(result)
