"""Seeded procedural sprite generators — a sprite from a number.

Three kinds, all deterministic: the same kind, seed, size, and
palette always make the same pixels, so a seed is worth writing
down. A different seed makes a different sprite (the seed feeds
every choice: shapes, colors, and details).

- "mask": a mirrored-mask sprite — a random bitmask mirrored
  across the vertical axis, filled with 2-4 colors. The classic
  spaceship/alien trick.
- "tree": a small tree — trunk plus a canopy in one of three
  styles (round, pine, blob), picked by the seed.
- "item": a small item — a potion, sword, gem, coin, or chest,
  picked by the seed, with colors picked by the seed.

Seed contract: generate() builds two random.Randoms per sprite
— one for shapes, one for colors — and draws from each in a
fixed order, so the result depends only on (kind, seed, size,
palette). The RNGs are seeded with the kind and the seed
together ("tree:1234"), not the bare number: bare small seeds
cluster badly on the first draw (seeds 1-4 all picked the same
tree style), and the kind in the seed means the same number
makes a different sprite for each kind. Two RNGs (not one)
means the palette only recolors: the shape draws never see the
color draws, so the same seed makes the same shape with any
palette. Seed 0 is a valid seed like any other, and negative
seeds work too (seed -1 is a different sprite from seed 1).
The first draw of the shape RNG in the tree and item
generators is the style/shape pick, so tree_style_for_seed()
and item_shape_for_seed() reproduce the choice without
generating.

Shapes are drawn on a 32 x 32 logical canvas and scaled to the
requested size with nearest-neighbour sampling (Image.scaled),
so a 64 x 64 sprite is the same sprite with bigger pixels.
Sprites are transparent where the shape has no pixels. Sizes
outside 8-512 are refused plainly: below 8 the shapes are mush,
above 512 the sprite is no longer a sprite.

Colors: with no palette, each generator picks from small curated
lists that read correctly (green trees, brown trunks, gold
coins). With a palette name (one of the named palettes), the
main colors come from that palette instead, picked by the seed
— the shapes stay the same, the colors match the palette.
"""

import random

from .image import Image
from .palettes import NAMED_PALETTES
from .pixelize import PixelForgeError

KINDS = ("mask", "tree", "item")

KIND_LABELS = {
    "mask": "Mirrored mask",
    "tree": "Tree",
    "item": "Item",
}

TREE_STYLES = ("round", "pine", "blob")

ITEM_SHAPES = ("potion", "sword", "gem", "coin", "chest")

DEFAULT_SIZE = 32
MIN_SIZE = 8
MAX_SIZE = 512

# The logical canvas every shape is drawn on, then scaled.
_CANVAS = 32

# Curated colors (r, g, b). The mask picks 2-4 from MASK_COLORS;
# the tree and item generators pick per role.
MASK_COLORS = [
    (255, 107, 107), (255, 193, 7), (107, 203, 119),
    (77, 171, 247), (204, 93, 232), (255, 146, 32),
    (245, 245, 245), (38, 38, 46),
]
TRUNK_COLORS = [(139, 90, 43), (111, 72, 36), (160, 110, 60)]
CANOPY_COLORS = [(46, 139, 53), (34, 112, 43), (74, 160, 60),
                 (24, 96, 58)]
SHADE_COLORS = [(27, 94, 32), (20, 77, 30), (46, 109, 36)]
LEAF_LIGHT = (139, 195, 74)
APPLE_COLOR = (220, 38, 38)
LIQUID_COLORS = [(233, 30, 99), (156, 39, 176), (33, 150, 243),
                 (0, 188, 140), (255, 152, 0)]
GEM_COLORS = [(77, 171, 247), (233, 30, 99), (0, 188, 140),
              (204, 93, 232), (255, 193, 7)]
GLASS_COLOR = (178, 223, 238)
CORK_COLOR = (160, 110, 60)
SILVER_COLOR = (220, 224, 230)
SILVER_DARK = (148, 158, 172)
GOLD_COLOR = (255, 193, 7)
GOLD_DARK = (200, 145, 10)
WOOD_COLOR = (139, 90, 43)
WOOD_DARK = (96, 60, 28)
WHITE = (255, 255, 255)


def _set(image, x, y, color):
    """One pixel, silently clipped to the canvas."""
    if 0 <= x < _CANVAS and 0 <= y < _CANVAS:
        off = (y * _CANVAS + x) * 4
        image.data[off] = color[0]
        image.data[off + 1] = color[1]
        image.data[off + 2] = color[2]
        image.data[off + 3] = 255


def _rect(image, x0, y0, x1, y1, color):
    """A filled rectangle, logical coords, [x0, x1) x [y0, y1)."""
    for y in range(max(0, y0), min(_CANVAS, y1)):
        for x in range(max(0, x0), min(_CANVAS, x1)):
            _set(image, x, y, color)


def _disc(image, cx, cy, radius, color):
    """A filled disc centered on (cx, cy) with the given radius."""
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) \
                    <= radius * radius:
                _set(image, x, y, color)


def _palette_colors(palette):
    """The color pool for a palette name, or None for curated."""
    if palette is None:
        return None
    if palette not in NAMED_PALETTES:
        raise PixelForgeError(
            "unknown palette: %s (pick one of: %s)"
            % (palette, ", ".join(sorted(NAMED_PALETTES))))
    return [tuple(color) for color in NAMED_PALETTES[palette]]


def _pick(crng, pool, curated):
    """One color: from the palette pool when given, else curated."""
    if pool is not None:
        return crng.choice(pool)
    return crng.choice(curated)


def _validate_size(size):
    if not isinstance(size, int) or isinstance(size, bool) \
            or size < MIN_SIZE or size > MAX_SIZE:
        raise PixelForgeError(
            "size must be a whole number from %d to %d (got %r)"
            % (MIN_SIZE, MAX_SIZE, size))


def _validate(kind, size):
    if kind not in KINDS:
        raise PixelForgeError(
            "unknown kind: %s (pick one of: %s)"
            % (kind, ", ".join(KINDS)))
    _validate_size(size)


def _rng(kind, seed):
    """The generator's shape RNG: seeded with kind + seed
    together (see the module docstring for why the bare number
    is not used)."""
    return random.Random("%s:%d" % (kind, seed))


def _color_rng(kind, seed):
    """The generator's color RNG: a separate stream from the
    shape RNG, so the palette only recolors (see the module
    docstring)."""
    return random.Random("%s-colors:%d" % (kind, seed))


def tree_style_for_seed(seed):
    """The tree style a seed picks — the generator's first draw."""
    return _rng("tree", seed).choice(TREE_STYLES)


def item_shape_for_seed(seed):
    """The item shape a seed picks — the generator's first draw."""
    return _rng("item", seed).choice(ITEM_SHAPES)


def generate_mask(seed, size=DEFAULT_SIZE, palette=None):
    """A mirrored-mask sprite: an 8 x 8 bitmask, the left half
    random, the right half its mirror, filled with 2-4 colors."""
    _validate_size(size)
    pool = _palette_colors(palette)
    rng = _rng("mask", seed)
    crng = _color_rng("mask", seed)
    image = Image(_CANVAS, _CANVAS)
    # 8 rows x 4 random columns; column x mirrors to 7 - x.
    filled = [[rng.random() < 0.5 for _ in range(4)]
              for _ in range(8)]
    if not any(any(row) for row in filled):
        filled[3][1] = True
        filled[4][1] = True
    count = crng.randint(2, 4)
    if pool is not None:
        colors = crng.sample(pool, min(count, len(pool)))
    else:
        colors = crng.sample(MASK_COLORS, count)
    cell = _CANVAS // 8
    for row in range(8):
        for col in range(4):
            if not filled[row][col]:
                continue
            # One color draw per mirrored pair, so the mirror
            # is exact in color as well as shape.
            color = crng.choice(colors)
            _rect(image, col * cell, row * cell,
                  (col + 1) * cell, (row + 1) * cell, color)
            _rect(image, (7 - col) * cell, row * cell,
                  (8 - col) * cell, (row + 1) * cell, color)
    if size == _CANVAS:
        return image
    return image.scaled(size, size)


def _tree_round(image, rng, canopy, shade, apples):
    _disc(image, 16, 13, 9, canopy)
    # Shade along the bottom-right arc, highlight top-left.
    _disc(image, 20, 17, 5, shade)
    _disc(image, 16, 13, 8, canopy)
    _rect(image, 11, 7, 13, 9, LEAF_LIGHT)
    if apples:
        for _ in range(4):
            _set(image, rng.randint(10, 22), rng.randint(8, 18),
                 APPLE_COLOR)


def _tree_pine(image, rng, canopy, shade, apples):
    # Four tiers, each a triangle of rows widening downward;
    # the tiers overlap by a row so there is no gap between
    # them, and the right side of each tier is shaded.
    for top, width in ((2, 12), (7, 16), (12, 20), (17, 24)):
        for row in range(6):
            half = 1 + (width // 2 - 1) * row // 5
            _rect(image, 16 - half, top + row, 16 + half + 1,
                  top + row + 1, canopy)
            _rect(image, 16 + half // 2, top + row,
                  16 + half + 1, top + row + 1, shade)
    if apples:
        for _ in range(3):
            _set(image, rng.randint(11, 21), rng.randint(6, 20),
                 APPLE_COLOR)


def _tree_blob(image, rng, canopy, shade, apples):
    _disc(image, 16, 12, 8, canopy)
    # Random bumps on the rim make the outline irregular.
    for _ in range(5):
        _disc(image, rng.randint(9, 23), rng.randint(6, 18),
              rng.randint(2, 4), canopy)
    _disc(image, 20, 16, 5, shade)
    _disc(image, 15, 12, 7, canopy)
    _rect(image, 11, 7, 13, 9, LEAF_LIGHT)
    if apples:
        for _ in range(4):
            _set(image, rng.randint(10, 22), rng.randint(8, 18),
                 APPLE_COLOR)


def generate_tree(seed, size=DEFAULT_SIZE, palette=None):
    """A tree: trunk plus a canopy in the style the seed picks
    (round, pine, or blob), with a soft shadow underneath."""
    _validate_size(size)
    pool = _palette_colors(palette)
    rng = _rng("tree", seed)
    crng = _color_rng("tree", seed)
    style = rng.choice(TREE_STYLES)
    trunk = _pick(crng, pool, TRUNK_COLORS)
    canopy = _pick(crng, pool, CANOPY_COLORS)
    shade = _pick(crng, pool, SHADE_COLORS)
    apples = rng.random() < 0.5
    image = Image(_CANVAS, _CANVAS)
    if style == "pine":
        _rect(image, 15, 22, 18, 30, trunk)
        _tree_pine(image, rng, canopy, shade, apples)
    else:
        _rect(image, 14, 21, 18, 30, trunk)
        _rect(image, 17, 21, 18, 30, WOOD_DARK)
        if style == "round":
            _tree_round(image, rng, canopy, shade, apples)
        else:
            _tree_blob(image, rng, canopy, shade, apples)
    # Soft shadow under the trunk.
    for x in range(11, 22):
        off = (30 * _CANVAS + x) * 4
        image.data[off] = 0
        image.data[off + 1] = 0
        image.data[off + 2] = 0
        image.data[off + 3] = 64
    if size == _CANVAS:
        return image
    return image.scaled(size, size)


def _item_potion(image, crng, pool):
    liquid = _pick(crng, pool, LIQUID_COLORS)
    # Body outline, then the liquid inside it, then the neck
    # and cork on top. Rows widen from the shoulders down.
    _rect(image, 12, 9, 20, 12, GLASS_COLOR)
    _rect(image, 10, 12, 22, 26, GLASS_COLOR)
    _rect(image, 12, 26, 20, 27, GLASS_COLOR)
    _rect(image, 11, 13, 21, 25, liquid)
    _rect(image, 12, 13, 15, 14, WHITE)
    _rect(image, 14, 5, 18, 10, GLASS_COLOR)
    _rect(image, 14, 2, 18, 6, CORK_COLOR)
    _set(image, 12, 11, WHITE)
    _set(image, 12, 12, WHITE)


def _item_sword(image, crng, pool):
    guard = _pick(crng, pool, [GOLD_COLOR, SILVER_DARK])
    # Blade with a point, crossguard, grip, pommel.
    _set(image, 15, 1, SILVER_COLOR)
    _set(image, 16, 1, SILVER_COLOR)
    _rect(image, 15, 2, 17, 4, SILVER_COLOR)
    _rect(image, 14, 4, 18, 20, SILVER_COLOR)
    _rect(image, 15, 6, 17, 18, SILVER_DARK)
    _rect(image, 9, 20, 23, 23, guard)
    _rect(image, 14, 23, 18, 28, WOOD_COLOR)
    _rect(image, 14, 25, 18, 26, WOOD_DARK)
    _rect(image, 14, 27, 18, 28, WOOD_DARK)
    _rect(image, 13, 28, 19, 30, guard)


def _item_gem(image, crng, pool):
    gem = _pick(crng, pool, GEM_COLORS)
    dark = tuple(max(0, c - 70) for c in gem)
    light = tuple(min(255, c + 70) for c in gem)
    # A diamond: rows widen to the middle, then narrow.
    for row in range(20):
        half = 2 + (10 * row // 19) if row < 10 \
            else 2 + (10 * (19 - row) // 19)
        color = light if row < 7 else (gem if row < 13 else dark)
        _rect(image, 16 - half, 6 + row, 16 + half + 1,
              7 + row, color)
    _rect(image, 12, 8, 14, 10, WHITE)
    _rect(image, 14, 19, 19, 22, dark)


def _item_coin(image, crng, pool):
    gold = _pick(crng, pool, [GOLD_COLOR, GOLD_DARK])
    dark = GOLD_DARK if gold == GOLD_COLOR else GOLD_COLOR
    _disc(image, 16, 16, 11, dark)
    _disc(image, 16, 16, 9, gold)
    _disc(image, 16, 16, 4, dark)
    _rect(image, 15, 15, 17, 17, gold)
    _rect(image, 11, 9, 13, 11, WHITE)


def _item_chest(image, crng, pool):
    wood = _pick(crng, pool, [WOOD_COLOR, (101, 67, 33)])
    # Lid (arched top rows), body, bands, lock.
    _rect(image, 10, 9, 22, 11, wood)
    _rect(image, 8, 11, 24, 16, wood)
    _rect(image, 7, 16, 25, 27, wood)
    _rect(image, 9, 11, 11, 27, WOOD_DARK)
    _rect(image, 21, 11, 23, 27, WOOD_DARK)
    _rect(image, 14, 15, 18, 21, GOLD_COLOR)
    _set(image, 15, 17, WOOD_DARK)
    _set(image, 16, 17, WOOD_DARK)
    _rect(image, 14, 15, 18, 16, GOLD_DARK)


_ITEM_DRAWERS = {
    "potion": _item_potion,
    "sword": _item_sword,
    "gem": _item_gem,
    "coin": _item_coin,
    "chest": _item_chest,
}


def generate_item(seed, size=DEFAULT_SIZE, palette=None):
    """An item: a potion, sword, gem, coin, or chest, picked by
    the seed, with colors picked by the seed."""
    _validate_size(size)
    pool = _palette_colors(palette)
    rng = _rng("item", seed)
    crng = _color_rng("item", seed)
    shape = rng.choice(ITEM_SHAPES)
    image = Image(_CANVAS, _CANVAS)
    _ITEM_DRAWERS[shape](image, crng, pool)
    if size == _CANVAS:
        return image
    return image.scaled(size, size)


_GENERATORS = {
    "mask": generate_mask,
    "tree": generate_tree,
    "item": generate_item,
}


def generate(kind, seed, size=DEFAULT_SIZE, palette=None):
    """One generated sprite: kind "mask", "tree", or "item",
    driven by seed. Same kind, seed, size, and palette always
    make the same pixels. Raises PixelForgeError for an unknown
    kind, palette, or a size outside 8-512."""
    _validate(kind, size)
    return _GENERATORS[kind](seed, size=size, palette=palette)
