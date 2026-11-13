"""Minecraft skins and textures.

A Minecraft skin is a 64x64 (new format) or 64x32 (classic) PNG
whose regions lay the character out flat: head, body, arms, legs,
plus overlay layers (hat, jacket, sleeves, pants). Detection is by
size only — a 64x64 or 64x32 image is treated as a skin, so a
non-skin image of exactly that size would be misread; the region
layout is the standard one every skin editor uses, so a real skin
always lands right.

Parts are the FRONT view of each region (the face a game shows).
The classic 64x32 layout has no left arm or leg — those mirror the
right side, and the parts here do the same (mirrored copies).
The combined figure assembles the front views into one 16x32
sprite (head above body, arms at the sides, legs below), with
each overlay composited over its base part where the overlay has
opaque pixels.

A PNG that is not a skin falls back to texture handling: it is
pixelized through the Item 1 core at its native size (a 16x16
texture stays 16x16) with a 256-color from-image palette and no
dithering, so the pixels come through unchanged — except that a
texture bigger than TEXTURE_MAX_DIMENSION on its longest side
is downscaled to fit that cap first. The core at native size
with a 256-color palette took 209 s on a 4000x3000 photo, so
the cap is what keeps a big photo import to seconds. The Code
tab's texture controls (task 26) pass a size, palette, color
limit, and dithering through pixelize_texture — the defaults
above are what a fresh import uses, so an untouched texture
still renders exactly as it did before those controls existed.
"""

from pathlib import Path

from .image import Image
from .pixelize import (PixelForgeError, compute_target_size,
                       downscale, load_image, pixelize_image)

# Longest side a texture is pixelized at. 512 matches the Image
# tab's max preset; anything bigger is downscaled (keeping its
# aspect ratio) before the pixelize core runs.
TEXTURE_MAX_DIMENSION = 512

# Front-view regions on the 64x64 layout: name -> (x, y, w, h).
# The 64x32 layout shares head, hat, body, right arm, right leg.
REGIONS_64 = {
    "head": (8, 8, 8, 8),
    "hat": (40, 8, 8, 8),
    "body": (20, 20, 8, 12),
    "jacket": (20, 36, 8, 12),
    "right_arm": (44, 20, 4, 12),
    "right_sleeve": (44, 36, 4, 12),
    "left_arm": (36, 52, 4, 12),
    "left_sleeve": (52, 52, 4, 12),
    "right_leg": (4, 20, 4, 12),
    "right_pants": (4, 36, 4, 12),
    "left_leg": (20, 52, 4, 12),
    "left_pants": (4, 52, 4, 12),
}

# Display/export order: base part, then its overlay, top to bottom.
PART_ORDER_64 = ["head", "hat", "body", "jacket",
                 "right_arm", "right_sleeve",
                 "left_arm", "left_sleeve",
                 "right_leg", "right_pants",
                 "left_leg", "left_pants"]
PART_ORDER_32 = ["head", "hat", "body",
                 "right_arm", "left_arm",
                 "right_leg", "left_leg"]

# Classic 64x32: the left limbs mirror the right ones.
MIRROR_32 = {"left_arm": "right_arm", "left_leg": "right_leg"}

# Overlay per base part, for the combined figure.
OVERLAYS = {"head": "hat", "body": "jacket",
            "right_arm": "right_sleeve", "left_arm": "left_sleeve",
            "right_leg": "right_pants", "left_leg": "left_pants"}

# Where each base part sits in the 16x32 figure: name -> (x, y).
FIGURE_LAYOUT = {"head": (4, 0), "body": (4, 8),
                 "right_arm": (0, 8), "left_arm": (12, 8),
                 "right_leg": (4, 20), "left_leg": (8, 20)}
FIGURE_WIDTH = 16
FIGURE_HEIGHT = 32


def detect_skin(image):
    """\"64x64\" or \"64x32\" when the image is skin-sized, else None."""
    if image.width() == 64 and image.height() == 64:
        return "64x64"
    if image.width() == 64 and image.height() == 32:
        return "64x32"
    return None


def skin_parts(image):
    """Front-view part images, in display order: {name: Image}.

    Raises PixelForgeError when the image is not skin-sized.
    Classic skins get mirrored left limbs; overlays that do not
    exist in the classic layout are simply absent."""
    layout = detect_skin(image)
    if layout is None:
        raise PixelForgeError(
            "not a Minecraft skin: %d x %d — skins are 64x64 or "
            "64x32" % (image.width(), image.height()))
    parts = {}
    names = PART_ORDER_64 if layout == "64x64" else PART_ORDER_32
    for name in names:
        if name in MIRROR_32 and layout == "64x32":
            source = MIRROR_32[name]
            x, y, w, h = REGIONS_64[source]
            parts[name] = _mirror_horizontal(image.copy(x, y, w, h))
        else:
            x, y, w, h = REGIONS_64[name]
            parts[name] = image.copy(x, y, w, h)
    return parts


def _mirror_horizontal(image):
    """A left-right mirror, copied pixel by pixel."""
    out = Image(image.width(), image.height())
    for y in range(image.height()):
        for x in range(image.width()):
            out.setPixel(image.width() - 1 - x, y,
                         image.pixel(x, y))
    return out


def _composite(base, overlay):
    """Overlay drawn over base: opaque overlay pixels replace,
    transparent ones let the base show through."""
    out = base.copy()
    for y in range(overlay.height()):
        for x in range(overlay.width()):
            px = overlay.pixel(x, y)
            if (px >> 24) & 0xFF:
                out.setPixel(x, y, px)
    return out


def skin_figure(image):
    """The combined 16x32 figure: front views assembled into one
    sprite, overlays composited over their base parts. Raises
    PixelForgeError when the image is not skin-sized."""
    parts = skin_parts(image)
    figure = Image(FIGURE_WIDTH, FIGURE_HEIGHT)
    for name, (fx, fy) in FIGURE_LAYOUT.items():
        part = parts[name]
        overlay_name = OVERLAYS.get(name)
        if overlay_name in parts:
            part = _composite(part, parts[overlay_name])
        for y in range(part.height()):
            for x in range(part.width()):
                px = part.pixel(x, y)
                if (px >> 24) & 0xFF:
                    figure.setPixel(fx + x, fy + y, px)
    return figure


def texture_target_size(image):
    """(width, height) a texture is pixelized at: its native size
    when the longest side fits TEXTURE_MAX_DIMENSION, else fitted
    inside the cap box keeping the aspect ratio."""
    if max(image.width(), image.height()) <= TEXTURE_MAX_DIMENSION:
        return image.width(), image.height()
    return compute_target_size(image.width(), image.height(),
                               TEXTURE_MAX_DIMENSION,
                               TEXTURE_MAX_DIMENSION, True)


def pixelize_texture(image, width=None, height=None, palette="image",
                     max_colors=256, dither=False):
    """A non-skin image as a texture: through the Item 1 core.

    Defaults are the original behavior — native size, 256-color
    from-image palette, no dithering, so the pixels come through
    unchanged. Pass width/height to render at another size, a
    named palette (\"pico8\", \"gameboy\", \"grayscale\") or a
    smaller max_colors to limit the colors, dither=True for
    ordered dithering. Images bigger than
    TEXTURE_MAX_DIMENSION on the longest side are downscaled to
    fit the cap first (aspect kept), so a big photo import takes
    seconds instead of minutes; smaller textures are untouched.
    A requested size is never bigger than the cap box either —
    the Code tab's size spins cap at 512, and a caller asking
    for more gets the cap fit instead."""
    target_w, target_h = texture_target_size(image)
    if (target_w, target_h) != (image.width(), image.height()):
        image = downscale(image, target_w, target_h)
    if width is None:
        width = image.width()
    if height is None:
        height = image.height()
    width = min(width, TEXTURE_MAX_DIMENSION)
    height = min(height, TEXTURE_MAX_DIMENSION)
    return pixelize_image(image, width=width, height=height,
                          palette=palette, max_colors=max_colors,
                          dither=dither)


def extract_skin(path, out_dir):
    """Extract a skin's parts + combined figure into out_dir.

    Writes <part>.png per part and figure.png. Returns the list
    of written paths, figure last. Raises PixelForgeError when
    the file is missing, unreadable, or not a skin."""
    image = load_image(path)
    layout = detect_skin(image)
    if layout is None:
        raise PixelForgeError(
            "not a Minecraft skin: %s is %d x %d — skins are "
            "64x64 or 64x32" % (Path(path).name, image.width(),
                                image.height()))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, part in skin_parts(image).items():
        target = out / ("%s.png" % name)
        if not part.save(str(target), "PNG"):
            raise PixelForgeError("could not write: %s" % target)
        written.append(target)
    figure_path = out / "figure.png"
    if not skin_figure(image).save(str(figure_path), "PNG"):
        raise PixelForgeError("could not write: %s" % figure_path)
    written.append(figure_path)
    return written
