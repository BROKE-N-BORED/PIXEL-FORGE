"""Normal-map generation — a sprite into a normal map for 2D lighting.

A normal map is an image where each pixel's color IS a surface
direction: red is left/right, green is up/down, blue is toward
the viewer. A 2D game engine reads it alongside the sprite and
lights the sprite as if it had depth — a torch swung past a brick
wall lights each brick's edges — without any 3D geometry. This
module makes one from a sprite's brightness alone: bright reads
as high, dark reads as low.

Method: the sprite's brightness becomes a heightmap (Rec. 601
luminance, premultiplied by alpha — see below), a Sobel filter
reads the height slope at every pixel, and each slope becomes a
unit normal, encoded as color. Flat reads (128, 128, 255): no
left/right tilt, no up/down tilt, straight at the viewer.

Y convention — OpenGL, green up. A slope that rises toward the
bottom of the image (height increasing downward on screen)
reads green above 128; a slope that rises toward the top reads
green below 128. This is the convention 2D lighting in Godot and
in Unity expects: both read normal maps OpenGL-style, green up,
so a map made here drops straight into either engine's 2D lights
with no channel flip. (DirectX-style maps flip green; this
module does not make those.)

Strength: multiplies the slope before the normal is normalized,
so 1.0 is the true slope of the heightmap, 0.25 flattens
everything toward (128, 128, 255), and 4.0 makes gentle ramps
dramatic. Out-of-range values are clamped into 0.25-4.0, never
refused.

Heightmap: opaque pixels read their Rec. 601 luminance
(0.299 red + 0.587 green + 0.114 blue). Transparent pixels read
as height 0 — the background plane — and a half-transparent
pixel reads its luminance times its alpha, so a soft edge ramps
down to the background instead of stepping off a cliff. The
output keeps the input's alpha, and a fully transparent pixel
comes out flat (128, 128, 255): it has no surface, so it has no
direction. Note the silhouette edge: an opaque pixel next to a
transparent one sits on a real height step (luminance down to
0), so the sprite's outline tilts outward in the map — that is
the edge catching the light, and it is what 2D lighting wants.

Edges: the Sobel reads clamped neighbors at the image border
(the edge pixel stands in for the missing neighbor), so a border
pixel's slope comes only from inside the image — no artificial
gradient is invented at the frame. A 1-pixel-wide or 1-pixel-
tall image has no slope anywhere and comes out all flat, as does
a single-color image.

Performance: pure Python, about 1.1-1.3 s on a 512 x 512 image
(measured in the sandbox under load — a flat image is ~0.7 s,
random noise ~1.3 s). Too slow for the GUI thread at that size,
so the GUI runs it in a worker with Cancel, like the pixel-grid
snap. Small sprites (32 x 32, 64 x 64) take milliseconds.
"""

import math

from .image import Image
from .pixelize import PixelForgeError

DEFAULT_STRENGTH = 1.0
MIN_STRENGTH = 0.25
MAX_STRENGTH = 4.0

# The Sobel kernel spans two pixels (x - 1 to x + 1) and the
# weights on each side sum to 4 (1 + 2 + 1), so a linear ramp
# with a per-pixel height step d gives a Sobel sum of 8d.
# Dividing by 8 * 255 turns the sum into the true per-pixel
# slope in 0..1 height units, so strength 1.0 is the true slope
# of the heightmap.
_SOBEL_SCALE = 8.0 * 255.0

# Flat: no tilt in x or y, straight at the viewer.
FLAT_NORMAL = (128, 128, 255)


def luminance(r, g, b):
    """Rec. 601 luminance of one pixel, 0.0-255.0."""
    return 0.299 * r + 0.587 * g + 0.114 * b


def heightmap(image):
    """The sprite's brightness as heights: a flat list of floats,
    one per pixel, row-major, 0.0-255.0. Luminance times alpha
    over 255 — a transparent pixel is height 0 (the background
    plane) and a half-transparent pixel is half its luminance."""
    width, height = image.width(), image.height()
    data = image.data
    heights = [0.0] * (width * height)
    for index in range(width * height):
        offset = index * 4
        alpha = data[offset + 3]
        if alpha == 0:
            continue
        # luminance() inlined: a call per pixel costs ~0.1 s on
        # a 512 x 512 image.
        heights[index] = (0.299 * data[offset]
                          + 0.587 * data[offset + 1]
                          + 0.114 * data[offset + 2]) * (alpha / 255.0)
    return heights


def generate_normal_map(image, strength=DEFAULT_STRENGTH):
    """A normal map Image from a sprite Image, same size.

    strength multiplies the slope before normalizing (default
    1.0; clamped into 0.25-4.0). Raises PixelForgeError for a
    0-size image. The output alpha matches the input alpha, and
    fully transparent pixels come out flat."""
    if image.isNull():
        raise PixelForgeError(
            "cannot make a normal map from an image with no size")
    strength = max(MIN_STRENGTH,
                   min(MAX_STRENGTH, float(strength)))
    width, height = image.width(), image.height()
    heights = heightmap(image)
    out = Image(width, height)
    src = image.data
    dst = out.data
    flat_r, flat_g, flat_b = FLAT_NORMAL
    for y in range(height):
        y0 = y - 1 if y > 0 else 0
        y1 = y + 1 if y < height - 1 else height - 1
        row0 = y0 * width
        row1 = y * width
        row2 = y1 * width
        for x in range(width):
            index = row1 + x
            offset = index * 4
            if src[offset + 3] == 0:
                dst[offset] = flat_r
                dst[offset + 1] = flat_g
                dst[offset + 2] = flat_b
                dst[offset + 3] = 0
                continue
            x0 = x - 1 if x > 0 else 0
            x1 = x + 1 if x < width - 1 else width - 1
            # Sobel X: right column minus left column, each
            # weighted 1, 2, 1 down the rows. Sobel Y: bottom
            # row minus top row, each weighted 1, 2, 1 across.
            # Eight loads, each neighbor read once: indexing
            # the list twelve times per pixel costs ~0.3 s on
            # a 512 x 512 image.
            top_left = heights[row0 + x0]
            top = heights[row0 + x]
            top_right = heights[row0 + x1]
            mid_left = heights[row1 + x0]
            mid_right = heights[row1 + x1]
            bot_left = heights[row2 + x0]
            bot = heights[row2 + x]
            bot_right = heights[row2 + x1]
            gx = ((top_right + 2.0 * mid_right + bot_right)
                  - (top_left + 2.0 * mid_left + bot_left)) \
                / _SOBEL_SCALE
            gy = ((bot_left + 2.0 * bot + bot_right)
                  - (top_left + 2.0 * top + top_right)) \
                / _SOBEL_SCALE
            if gx == 0.0 and gy == 0.0:
                dst[offset] = flat_r
                dst[offset + 1] = flat_g
                dst[offset + 2] = flat_b
                dst[offset + 3] = src[offset + 3]
                continue
            # OpenGL convention (green up): image y points DOWN
            # the screen but texture y points UP, so the y
            # slope keeps its sign while x flips. A height
            # increase going down the image is a surface facing
            # up-screen: green above 128.
            nx = -gx * strength
            ny = gy * strength
            nz = 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            nx /= length
            ny /= length
            nz /= length
            dst[offset] = int((nx * 0.5 + 0.5) * 255.0 + 0.5)
            dst[offset + 1] = int((ny * 0.5 + 0.5) * 255.0 + 0.5)
            dst[offset + 2] = int((nz * 0.5 + 0.5) * 255.0 + 0.5)
            dst[offset + 3] = src[offset + 3]
    return out
