"""Palettes for Pixel Forge.

Named palettes are fixed color lists. "From image" palettes come from
median-cut over the downscaled image's opaque pixels. Every palette is
a list of (r, g, b) tuples; transparency never enters a palette —
transparent pixels are excluded before this module ever sees them.
"""

# PICO-8 — the classic 16-color fantasy-console palette.
PICO8 = [
    (0, 0, 0), (29, 43, 83), (126, 37, 83), (0, 135, 81),
    (171, 82, 54), (95, 87, 79), (194, 195, 199), (255, 241, 232),
    (255, 0, 77), (255, 163, 0), (255, 236, 39), (0, 228, 54),
    (41, 173, 255), (131, 118, 156), (255, 119, 168), (255, 204, 170),
]

# Game Boy — the 4-shade original handheld palette (darkest first).
GAMEBOY = [
    (15, 56, 15), (48, 98, 48), (139, 172, 15), (155, 188, 15),
]

# Grayscale — 16 evenly spaced shades, black to white.
GRAYSCALE = [(i * 17, i * 17, i * 17) for i in range(16)]

# Bundled Lospec palettes (task 39) — copied verbatim from
# Lospec's .hex files (resurrect-64.hex, endesga-32.hex,
# sweetie-16.hex). The counts and first colors are pinned by
# tests/test_palette_io.py against those files.
RESURRECT_64 = [
    (46, 34, 47), (62, 53, 70), (98, 85, 101), (150, 108, 108),
    (171, 148, 122), (105, 79, 98), (127, 112, 138), (155, 171, 178),
    (199, 220, 208), (255, 255, 255), (110, 39, 39), (179, 56, 49),
    (234, 79, 54), (245, 125, 74), (174, 35, 52), (232, 59, 59),
    (251, 107, 29), (247, 150, 23), (249, 194, 43), (122, 48, 69),
    (158, 69, 57), (205, 104, 61), (230, 144, 78), (251, 185, 84),
    (76, 62, 36), (103, 102, 51), (162, 169, 71), (213, 224, 75),
    (251, 255, 134), (22, 90, 76), (35, 144, 99), (30, 188, 115),
    (145, 219, 105), (205, 223, 108), (49, 54, 56), (55, 78, 74),
    (84, 126, 100), (146, 169, 132), (178, 186, 144), (11, 94, 101),
    (11, 138, 143), (14, 175, 155), (48, 225, 185), (143, 248, 226),
    (50, 51, 83), (72, 74, 119), (77, 101, 180), (77, 155, 230),
    (143, 211, 255), (69, 41, 63), (107, 62, 117), (144, 94, 169),
    (168, 132, 243), (234, 173, 237), (117, 60, 84), (162, 75, 111),
    (207, 101, 127), (237, 128, 153), (131, 28, 93), (195, 36, 84),
    (240, 79, 120), (246, 129, 129), (252, 167, 144), (253, 203, 176),
]

ENDESGA_32 = [
    (190, 74, 47), (215, 118, 67), (234, 212, 170), (228, 166, 114),
    (184, 111, 80), (115, 62, 57), (62, 39, 49), (162, 38, 51),
    (228, 59, 68), (247, 118, 34), (254, 174, 52), (254, 231, 97),
    (99, 199, 77), (62, 137, 72), (38, 92, 66), (25, 60, 62),
    (18, 78, 137), (0, 153, 219), (44, 232, 245), (255, 255, 255),
    (192, 203, 220), (139, 155, 180), (90, 105, 136), (58, 68, 102),
    (38, 43, 68), (24, 20, 37), (255, 0, 68), (104, 56, 108),
    (181, 80, 136), (246, 117, 122), (232, 183, 150), (194, 133, 105),
]

SWEETIE_16 = [
    (26, 28, 44), (93, 39, 93), (177, 62, 83), (239, 125, 87),
    (255, 205, 117), (167, 240, 112), (56, 183, 100), (37, 113, 121),
    (41, 54, 111), (59, 93, 201), (65, 166, 246), (115, 239, 247),
    (244, 244, 244), (148, 176, 194), (86, 108, 134), (51, 60, 87),
]

NAMED_PALETTES = {
    "pico8": PICO8,
    "gameboy": GAMEBOY,
    "grayscale": GRAYSCALE,
    "resurrect64": RESURRECT_64,
    "endesga32": ENDESGA_32,
    "sweetie16": SWEETIE_16,
}

PALETTE_LABELS = {
    "image": "From image",
    "pico8": "PICO-8",
    "gameboy": "Game Boy",
    "grayscale": "Grayscale",
    "resurrect64": "Resurrect 64",
    "endesga32": "Endesga 32",
    "sweetie16": "Sweetie 16",
}


def select_palette_colors(palette, colors, max_colors):
    """Pick the max_colors palette entries an image actually uses.

    Every pixel color votes for its nearest palette entry; the
    entries with the most votes win (ties go to the earliest
    palette entry, so results are deterministic). Returns at most
    max_colors entries, in palette order — fewer when the image
    only uses a few entries, since unused entries add nothing.

    This replaced index-based truncation, which cut the palette
    by position and could throw away the exact colors the image
    needed: a green-and-red picture on PICO-8 at 4 colors came
    out a single flat grey, because the green and the dark purple
    were both cut.
    """
    if max_colors < 1:
        raise ValueError("max_colors must be at least 1")
    palette = list(palette)
    if len(palette) <= max_colors:
        return palette
    if not colors:
        return palette[:max_colors]
    counts = {}
    cache = {}
    for rgb in colors:
        nearest = nearest_color(rgb, palette, cache)
        counts[nearest] = counts.get(nearest, 0) + 1
    order = {entry: i for i, entry in enumerate(palette)}
    ranked = sorted(counts,
                    key=lambda entry: (-counts[entry], order[entry]))
    chosen = ranked[:max_colors]
    return sorted(chosen, key=order.__getitem__)


def median_cut(colors, max_colors):
    """Build a palette of at most max_colors entries from pixel colors.

    Standard median-cut: split the box with the widest channel range
    at its median until there are enough boxes, then average each box.
    Deterministic — same input colors always give the same palette.

    max_colors is validated FIRST, before the empty-list early
    return — it used to be checked after, so median_cut([], 0)
    silently returned [] instead of failing (review B14). An empty
    color list with a valid max_colors still returns [].
    """
    if max_colors < 1:
        # Imported here, not at module level: pixelize imports this
        # module, so a top-level import would be circular. This only
        # runs on the error path, never in normal use.
        from .pixelize import PixelForgeError
        raise PixelForgeError(
            "max_colors must be at least 1 (got %r)" % (max_colors,))
    distinct = sorted(set(colors))
    if len(distinct) <= max_colors:
        return distinct
    boxes = [distinct]
    while len(boxes) < max_colors:
        widest_idx = -1
        widest_range = -1
        for i, box in enumerate(boxes):
            if len(box) < 2:
                continue
            rs = [c[0] for c in box]
            gs = [c[1] for c in box]
            bs = [c[2] for c in box]
            spread = max(max(rs) - min(rs), max(gs) - min(gs),
                         max(bs) - min(bs))
            if spread > widest_range:
                widest_range = spread
                widest_idx = i
        if widest_idx < 0:
            break
        box = boxes.pop(widest_idx)
        rs = [c[0] for c in box]
        gs = [c[1] for c in box]
        bs = [c[2] for c in box]
        ranges = (max(rs) - min(rs), max(gs) - min(gs), max(bs) - min(bs))
        channel = ranges.index(max(ranges))
        ordered = sorted(box, key=lambda c: c[channel])
        mid = len(ordered) // 2
        boxes.append(ordered[:mid])
        boxes.append(ordered[mid:])
    palette = []
    for box in boxes:
        n = len(box)
        palette.append((sum(c[0] for c in box) // n,
                        sum(c[1] for c in box) // n,
                        sum(c[2] for c in box) // n))
    return sorted(set(palette))


def nearest_color(rgb, palette, cache=None):
    """Closest palette color to rgb (redmean distance).

    The redmean approximation weights each channel by how the eye
    sees it: with rmean = (r1 + r2) / 2, red is weighted
    (2 + rmean / 256), green 4, and blue (2 + (255 - rmean) / 256)
    — green counts most, and red counts more against a bright red
    background. This replaced squared Euclidean distance (task 39):
    where two palette entries were near-tied under Euclidean, the
    redmean winner is the one that actually looks closer, so
    pixelize output can shift slightly from v0.48 in those cases.

    Ties go to the earliest palette entry, so results are
    deterministic. cache (a dict) is an optional memo keyed by rgb.

    An empty palette raises PixelForgeError — it used to raise a
    bare IndexError from palette[0] (review B14). The guard runs
    before the cache lookup, so an empty palette always fails,
    even for a color the cache already knows.
    """
    if not palette:
        # Imported here, not at module level: pixelize imports this
        # module, so a top-level import would be circular. This only
        # runs on the error path, never in normal use.
        from .pixelize import PixelForgeError
        raise PixelForgeError(
            "cannot find the nearest color in an empty palette")
    if cache is not None:
        hit = cache.get(rgb)
        if hit is not None:
            return hit
    r, g, b = rgb
    best = palette[0]
    best_d = None
    for pr, pg, pb in palette:
        dr = r - pr
        dg = g - pg
        db = b - pb
        rmean = (r + pr) / 2
        d = ((2 + rmean / 256) * dr * dr + 4 * dg * dg
             + (2 + (255 - rmean) / 256) * db * db)
        if best_d is None or d < best_d:
            best_d = d
            best = (pr, pg, pb)
    if cache is not None:
        cache[rgb] = best
    return best
