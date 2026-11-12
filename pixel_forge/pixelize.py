"""Pixel Forge core — image to pixel sprite, no AI, no network.

Pipeline: load with QImage, downscale FIRST with nearest-neighbour
sampling (honest pixel art: each output pixel is one sampled source
pixel), then quantize the small image in pure Python. Quantizing after
the downscale keeps the pure-Python step fast — the image is at most a
few hundred pixels on a side by then.

Transparency: pixels with alpha 0 stay transparent, are excluded from
the palette, and take no part in dithering. Other pixels keep their
original alpha value; only their color is quantized.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, qRgba

from .palettes import (NAMED_PALETTES, median_cut, nearest_color,
                       select_palette_colors)

MIN_COLORS = 2
MAX_COLORS = 256


class PixelForgeError(Exception):
    """A readable, user-facing error (bad input, unreadable file,
    failed save). Callers print the message, never a traceback."""


def load_image(path):
    """Load an image file into a 32-bit QImage.

    Supports whatever QImage supports (PNG, JPG, BMP, WebP, GIF first
    frame, and more). Raises PixelForgeError with a readable message
    when the file is missing or unreadable.
    """
    p = Path(path)
    if not p.is_file():
        raise PixelForgeError("not found: %s" % path)
    image = QImage(str(p))
    if image.isNull():
        raise PixelForgeError(
            "could not read as an image: %s "
            "(unsupported format or damaged file)" % path)
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def compute_target_size(src_w, src_h, width, height, keep_aspect):
    """Output dimensions for a source of src_w x src_h.

    Without keep-aspect the output is exactly width x height. With it,
    the source is fitted inside the width x height box, keeping its
    aspect ratio, never smaller than 1 pixel on a side.
    """
    if width < 1 or height < 1:
        raise PixelForgeError(
            "size must be at least 1 pixel in each direction")
    if not keep_aspect:
        return width, height
    scale = min(width / src_w, height / src_h)
    return (max(1, round(src_w * scale)),
            max(1, round(src_h * scale)))


def downscale(image, width, height, keep_aspect=False):
    """Downscale with nearest-neighbour sampling (FastTransformation)."""
    target_w, target_h = compute_target_size(
        image.width(), image.height(), width, height, keep_aspect)
    return image.scaled(target_w, target_h,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation)


def opaque_pixels(image):
    """RGB tuples of every pixel with alpha > 0."""
    colors = []
    for y in range(image.height()):
        for x in range(image.width()):
            px = image.pixel(x, y)
            if (px >> 24) & 0xFF:
                colors.append(((px >> 16) & 0xFF,
                               (px >> 8) & 0xFF,
                               px & 0xFF))
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
    """
    width, height = image.width(), image.height()
    out = QImage(width, height, QImage.Format.Format_ARGB32)
    cache = {}
    for y in range(height):
        for x in range(width):
            px = image.pixel(x, y)
            alpha = (px >> 24) & 0xFF
            if alpha == 0:
                out.setPixel(x, y, 0)
                continue
            r = (px >> 16) & 0xFF
            g = (px >> 8) & 0xFF
            b = px & 0xFF
            if dither:
                threshold = (BAYER_4X4[y % 4][x % 4] + 0.5) / 16.0 - 0.5
                shift = int(round(threshold * BAYER_SPREAD))
                r = min(255, max(0, r + shift))
                g = min(255, max(0, g + shift))
                b = min(255, max(0, b + shift))
            nr, ng, nb = nearest_color((r, g, b), palette, cache)
            out.setPixel(x, y, qRgba(nr, ng, nb, alpha))
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
