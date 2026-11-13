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

NAMED_PALETTES = {
    "pico8": PICO8,
    "gameboy": GAMEBOY,
    "grayscale": GRAYSCALE,
}

PALETTE_LABELS = {
    "image": "From image",
    "pico8": "PICO-8",
    "gameboy": "Game Boy",
    "grayscale": "Grayscale",
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
    """Closest palette color to rgb (squared Euclidean distance).

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
        d = (r - pr) * (r - pr) + (g - pg) * (g - pg) + (b - pb) * (b - pb)
        if best_d is None or d < best_d:
            best_d = d
            best = (pr, pg, pb)
    if cache is not None:
        cache[rgb] = best
    return best
