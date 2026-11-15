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

Reassembly (task 45) rebuilds a canonical 64x64 skin from a
skin of either layout: a 64x64 skin comes back unchanged, a
64x32 skin gets its classic parts placed at the 64x64 positions
with the left limbs mirrored in from the right side, and the
overlays the classic layout never had stay transparent — nothing
is invented. Reassembling and re-loading gives the same parts
(the round-trip guarantee). The 3D preview's boxes come from
figure_boxes: six body boxes plus an overlay box for each
overlay layer that has any opaque pixel, each face carrying its
UV rect into the 64x64 texture.

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

# Every face of every part on the 64x64 layout: part -> face ->
# (x, y, w, h). The single source of truth for the layout — the
# front-view regions below are derived from it, so the split,
# the reassembly, and the 3D preview can never disagree about
# where a face lives. Each part's block holds its faces in the
# same arrangement: top above the front, bottom right of the
# top, right left of the front, left right of the front, back
# right of the left. The 64x32 layout shares head, hat, body,
# right arm, right leg.
PART_FACES = {
    "head": {"top": (8, 0, 8, 8), "bottom": (16, 0, 8, 8),
             "right": (0, 8, 8, 8), "front": (8, 8, 8, 8),
             "left": (16, 8, 8, 8), "back": (24, 8, 8, 8)},
    "hat": {"top": (40, 0, 8, 8), "bottom": (48, 0, 8, 8),
            "right": (32, 8, 8, 8), "front": (40, 8, 8, 8),
            "left": (48, 8, 8, 8), "back": (56, 8, 8, 8)},
    "body": {"top": (20, 16, 8, 4), "bottom": (28, 16, 8, 4),
             "right": (16, 20, 4, 12), "front": (20, 20, 8, 12),
             "left": (28, 20, 4, 12), "back": (32, 20, 8, 12)},
    "jacket": {"top": (20, 32, 8, 4), "bottom": (28, 32, 8, 4),
               "right": (16, 36, 4, 12), "front": (20, 36, 8, 12),
               "left": (28, 36, 4, 12), "back": (32, 36, 8, 12)},
    "right_arm": {"top": (44, 16, 4, 4), "bottom": (48, 16, 4, 4),
                  "right": (40, 20, 4, 12),
                  "front": (44, 20, 4, 12),
                  "left": (48, 20, 4, 12),
                  "back": (52, 20, 4, 12)},
    "right_sleeve": {"top": (44, 32, 4, 4),
                     "bottom": (48, 32, 4, 4),
                     "right": (40, 36, 4, 12),
                     "front": (44, 36, 4, 12),
                     "left": (48, 36, 4, 12),
                     "back": (52, 36, 4, 12)},
    "left_arm": {"top": (36, 48, 4, 4), "bottom": (40, 48, 4, 4),
                 "right": (32, 52, 4, 12),
                 "front": (36, 52, 4, 12),
                 "left": (40, 52, 4, 12),
                 "back": (44, 52, 4, 12)},
    "left_sleeve": {"top": (52, 48, 4, 4),
                    "bottom": (56, 48, 4, 4),
                    "right": (48, 52, 4, 12),
                    "front": (52, 52, 4, 12),
                    "left": (56, 52, 4, 12),
                    "back": (60, 52, 4, 12)},
    "right_leg": {"top": (4, 16, 4, 4), "bottom": (8, 16, 4, 4),
                  "right": (0, 20, 4, 12), "front": (4, 20, 4, 12),
                  "left": (8, 20, 4, 12), "back": (12, 20, 4, 12)},
    "right_pants": {"top": (4, 32, 4, 4), "bottom": (8, 32, 4, 4),
                    "right": (0, 36, 4, 12),
                    "front": (4, 36, 4, 12),
                    "left": (8, 36, 4, 12),
                    "back": (12, 36, 4, 12)},
    "left_leg": {"top": (20, 48, 4, 4), "bottom": (24, 48, 4, 4),
                 "right": (16, 52, 4, 12),
                 "front": (20, 52, 4, 12),
                 "left": (24, 52, 4, 12),
                 "back": (28, 52, 4, 12)},
    "left_pants": {"top": (4, 48, 4, 4), "bottom": (8, 48, 4, 4),
                   "right": (0, 52, 4, 12), "front": (4, 52, 4, 12),
                   "left": (8, 52, 4, 12),
                   "back": (12, 52, 4, 12)},
}

# Front-view regions on the 64x64 layout: name -> (x, y, w, h).
# Derived from PART_FACES — never edited by hand.
REGIONS_64 = {name: faces["front"]
              for name, faces in PART_FACES.items()}

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


# Mirroring a part (the classic layout's left limbs): each face
# is flipped left-right, and the side faces swap — the mirrored
# part's left face shows the source part's right face, and its
# right face the source's left.
_MIRROR_FACE = {"top": "top", "bottom": "bottom",
                "right": "left", "front": "front",
                "left": "right", "back": "back"}

# Classic parts: the ones a 64x32 skin actually carries.
_CLASSIC_PARTS = ("head", "hat", "body", "right_arm", "right_leg")


def _copy_rect(src, rect, dst):
    """One rectangle copied at the same coordinates, pixels
    intact (transparent stays transparent)."""
    x, y, w, h = rect
    for row in range(h):
        s = ((y + row) * src.width() + x) * 4
        d = ((y + row) * dst.width() + x) * 4
        dst.data[d:d + w * 4] = src.data[s:s + w * 4]


def _copy_rect_mirrored(src, src_rect, dst, dst_rect):
    """One rectangle copied left-right mirrored into another
    rectangle of the same size."""
    sx, sy, w, h = src_rect
    dx, dy, _, _ = dst_rect
    for row in range(h):
        for col in range(w):
            dst.setPixel(dx + col, dy + row,
                         src.pixel(sx + w - 1 - col, sy + row))


def reassemble_skin(image):
    """A canonical 64x64 skin from a skin of either layout.

    A 64x64 skin comes back unchanged (a copy). A 64x32 skin
    gets its classic parts placed at the 64x64 positions, its
    left limbs mirrored in from the right side, and the overlays
    the classic layout never had left transparent — no pixels
    are invented. Reassembling and re-loading gives the same
    parts. Raises PixelForgeError when the image is not
    skin-sized."""
    layout = detect_skin(image)
    if layout is None:
        raise PixelForgeError(
            "not a Minecraft skin: %d x %d — skins are 64x64 or "
            "64x32" % (image.width(), image.height()))
    if layout == "64x64":
        return image.copy()
    out = Image(64, 64)
    for name in _CLASSIC_PARTS:
        for rect in PART_FACES[name].values():
            _copy_rect(image, rect, out)
    for target, source in MIRROR_32.items():
        for face, src_face in _MIRROR_FACE.items():
            _copy_rect_mirrored(image, PART_FACES[source][src_face],
                                out, PART_FACES[target][face])
    return out


def reassemble_skin_file(path, out_path):
    """Reassemble a skin file into a canonical 64x64 PNG at
    out_path. Returns the output path. Raises PixelForgeError
    when the file is missing, unreadable, or not a skin, or
    when the PNG cannot be written."""
    skin = reassemble_skin(load_image(path))
    if not skin.save(str(out_path), "PNG"):
        raise PixelForgeError("could not write: %s" % out_path)
    return Path(out_path)


# Body boxes for the 3D preview: part -> ((center x, y, z),
# (width, height, depth)). Units are texture pixels, y up, feet
# at y=0; the figure stands 32 tall and 16 wide.
FIGURE_BOXES = {
    "head": ((0, 28, 0), (8, 8, 8)),
    "body": ((0, 18, 0), (8, 12, 4)),
    "right_arm": ((-6, 18, 0), (4, 12, 4)),
    "left_arm": ((6, 18, 0), (4, 12, 4)),
    "right_leg": ((-2, 6, 0), (4, 12, 4)),
    "left_leg": ((2, 6, 0), (4, 12, 4)),
}

# How much bigger an overlay box is than its base box on every
# side, so the layer sits proud of the skin beneath it.
OVERLAY_GROW = 0.5

_OVERLAY_BASE = {overlay: base
                 for base, overlay in OVERLAYS.items()}


def _part_has_pixels(image, name):
    """True when any face of the part has an opaque pixel."""
    for (x, y, w, h) in PART_FACES[name].values():
        for row in range(y, y + h):
            off = (row * image.width() + x) * 4
            for col in range(w):
                if image.data[off + col * 4 + 3]:
                    return True
    return False


def _box_for(name, overlay):
    """One preview box: part, center, size, overlay flag, and a
    (u0, v0, u1, v1) rect per face into the 64x64 texture, u and
    v measured from the texture's top-left corner."""
    base = _OVERLAY_BASE[name] if overlay else name
    center, size = FIGURE_BOXES[base]
    if overlay:
        size = tuple(dim + OVERLAY_GROW for dim in size)
    faces = {}
    for face, (x, y, w, h) in PART_FACES[name].items():
        faces[face] = (x / 64.0, y / 64.0,
                       (x + w) / 64.0, (y + h) / 64.0)
    return {"part": name, "center": center, "size": size,
            "overlay": overlay, "faces": faces}


def figure_boxes(image):
    """The 3D preview's boxes for a 64x64 skin: six body boxes
    plus an overlay box for each overlay layer that has any
    opaque pixel (a blank overlay draws nothing). Each box is a
    dict with part, center, size, overlay, and faces — see
    _box_for. Raises PixelForgeError unless the image is 64x64:
    a classic skin goes through reassemble_skin first, so the
    preview always works from the canonical layout."""
    if image.width() != 64 or image.height() != 64:
        raise PixelForgeError(
            "not a 64x64 skin: %d x %d — reassemble a classic "
            "skin first" % (image.width(), image.height()))
    boxes = [_box_for(name, False)
             for name in ("head", "body", "right_arm", "left_arm",
                          "right_leg", "left_leg")]
    for overlay in ("hat", "jacket", "right_sleeve",
                    "left_sleeve", "right_pants", "left_pants"):
        if _part_has_pixels(image, overlay):
            boxes.append(_box_for(overlay, True))
    return boxes


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
                     max_colors=256, dither=False, palettes=None):
    """A non-skin image as a texture: through the Item 1 core.

    Defaults are the original behavior — native size, 256-color
    from-image palette, no dithering, so the pixels come through
    unchanged. Pass width/height to render at another size, a
    named palette ("pico8", "gameboy", "grayscale", "resurrect64",
    "endesga32", "sweetie16", or a saved user palette via
    palettes) or a smaller max_colors to limit the colors,
    dither=True for ordered dithering. Images bigger than
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
                          dither=dither, palettes=palettes)


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
