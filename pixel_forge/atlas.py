"""Atlas packing — MaxRects bin-packing for mixed-size sprites
(task 38).

The grid packer (spritesheet.pack_frames) lays same-size frames in
a grid and stays exactly as it is — this module is the other
packer, for sprites of different sizes. It packs with MaxRects
and the Best Short Side Fit heuristic: sprites go in largest
first, each one into the free rectangle that leaves the least
waste. Sprites are never rotated — rotating pixel art changes
its look, so a sprite that fits only sideways does not fit.

Padding is a ring kept around every sprite: the packer reserves
padding pixels on all four sides of each sprite, so the gap
between two sprites is twice the padding and the sheet edge
carries one padding ring. That ring is what extrude writes into
— extrude repeats each sprite's edge pixels outward into its own
ring, the standard fix for bleeding when the atlas is scaled, and
can never reach another sprite (extrude larger than padding is
refused plainly). The default padding is 1, the atlas
convention; the grid packer's default of 0 is untouched.

Trim cuts each sprite to the bounding box of its non-transparent
pixels and records the cut insets, so a game can put the sprite
back where it was. A fully transparent sprite trims to a 1 x 1
transparent tile and KEEPS its placement — the frame count never
changes silently. Dedupe packs byte-identical sprites once; the
other names become aliases of the same rect, and the result
reports how many sprites were deduped. An aliased frame's
duration is the first occurrence's duration — the atlas stores
one rect per unique sprite, so duplicate frames share the first
one's timing.

The metadata format is new — `pixel-forge-atlas` version 1 —
and does not reuse or break the `pixel-forge-spritesheet`
format. It carries the packer name, the settings, the sheets
list (one PNG per sheet when multipack spills), and the frames
list in input order, each frame with its sheet index, rect,
source size, trim insets, pivot, 9-slice borders, and aliases.

Stdlib-only: no Qt, no ffmpeg — the command line uses this
module directly, and the tests run on bare python.
"""

import hashlib
import json
import math
import os
from pathlib import Path

from .image import Image
from .pixelize import PixelForgeError
from .spritesheet import duration_for_fps, validate_tags

FORMAT_NAME = "pixel-forge-atlas"
FORMAT_VERSION = 1
PACKER_NAME = "maxrects-bssf"

# The atlas cap: a sheet never grows past this in either
# direction. 1024 is the default — big enough for a full
# animation, small enough for any GPU made this century.
DEFAULT_MAX_SIZE = 1024
MIN_MAX_SIZE = 64
MAX_MAX_SIZE = 4096

# Padding default: 1 px, the atlas convention (prevents
# bleeding when the atlas is scaled). The grid packer's
# default of 0 is untouched.
DEFAULT_PADDING = 1
MAX_PADDING = 64

DEFAULT_EXTRUDE = 0
MAX_EXTRUDE = 16

DEFAULT_PIVOT = (0.5, 0.5)


def trim_transparent(image):
    """Cut an image to the bounding box of its non-transparent
    pixels. Returns (trimmed Image, left, top, right, bottom) —
    the insets are the pixels cut from each edge, so
    left + trimmed width + right is the source width, and the
    same for height. A fully transparent image trims to a 1 x 1
    transparent tile at the top-left corner, so it keeps a
    placement and the frame count never changes."""
    width, height = image.width(), image.height()
    if width < 1 or height < 1:
        raise PixelForgeError(
            "cannot trim a %d x %d image" % (width, height))
    data = image.data
    left, right = width, -1
    top, bottom = height, -1
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if data[row + x * 4 + 3]:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    if right < 0:
        # Fully transparent: a 1 x 1 tile at (0, 0). The insets
        # still add up — 0 + 1 + (width - 1) is the width.
        return Image(1, 1), 0, 0, width - 1, height - 1
    trimmed = image.copy(left, top, right - left + 1,
                         bottom - top + 1)
    return (trimmed, left, top,
            width - 1 - right, height - 1 - bottom)


def _rects_overlap(a, b):
    """Two (x, y, w, h) rects overlap, or not. Touching edges
    do not count — a sprite may sit flush against another's
    cell."""
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def _split_free(free, placed):
    """The pieces of free rect `free` left after `placed` is
    cut out of it — up to four slabs (left, right, top,
    bottom), each kept only when it has a positive size. The
    slabs overlap each other at the corners; the prune step
    removes any rect fully inside another, so the list stays
    honest."""
    fx, fy, fw, fh = free
    px, py, pw, ph = placed
    pieces = []
    if px > fx:
        pieces.append((fx, fy, px - fx, fh))
    if px + pw < fx + fw:
        pieces.append((px + pw, fy, fx + fw - (px + pw), fh))
    if py > fy:
        pieces.append((fx, fy, fw, py - fy))
    if py + ph < fy + fh:
        pieces.append((fx, py + ph, fw, fy + fh - (py + ph)))
    return pieces


def _prune_free(free_rects):
    """Drop every free rect fully inside another free rect.
    The list is short (one per placed sprite, roughly), so the
    quadratic pass is cheap."""
    kept = []
    for i, rect in enumerate(free_rects):
        inside = False
        for j, other in enumerate(free_rects):
            if i == j:
                continue
            if (other[0] <= rect[0] and other[1] <= rect[1]
                    and other[0] + other[2] >= rect[0] + rect[2]
                    and other[1] + other[3] >= rect[1] + rect[3]):
                inside = True
                break
        if not inside:
            kept.append(rect)
    return kept


class _Bin:
    """One MaxRects bin — a square sheet that sprites are
    placed into, largest first, Best Short Side Fit."""

    def __init__(self, size):
        self.size = size
        self.free = [(0, 0, size, size)]
        self.placements = {}  # name -> (x, y, w, h) cell rect

    def try_place(self, name, cell_w, cell_h):
        """Place a cell_w x cell_h cell, or return None when
        no free rect fits it. Best Short Side Fit: the free
        rect whose shorter leftover side is smallest wins;
        ties break on the longer leftover side, then on the
        free rect's position, so the result is deterministic."""
        best = None
        best_score = None
        for rect in self.free:
            if rect[2] < cell_w or rect[3] < cell_h:
                continue
            leftover_w = rect[2] - cell_w
            leftover_h = rect[3] - cell_h
            score = (min(leftover_w, leftover_h),
                     max(leftover_w, leftover_h),
                     rect[1], rect[0])
            if best_score is None or score < best_score:
                best_score = score
                best = rect
        if best is None:
            return None
        cell = (best[0], best[1], cell_w, cell_h)
        new_free = []
        for rect in self.free:
            if _rects_overlap(rect, cell):
                new_free.extend(_split_free(rect, cell))
            else:
                new_free.append(rect)
        self.free = _prune_free(new_free)
        self.placements[name] = cell
        return cell


def _validate_pivot(name, pivot):
    """A pivot is (x, y), each 0 to 1 — 0.5, 0.5 is the
    center. Anything else refuses naming the sprite."""
    if (not isinstance(pivot, (tuple, list)) or len(pivot) != 2):
        raise PixelForgeError(
            "pivot for %s must be two numbers (got %r)"
            % (name, pivot))
    for value in pivot:
        if (isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0 or value > 1):
            raise PixelForgeError(
                "pivot for %s must be between 0 and 1 (got %r)"
                % (name, pivot))
    return (float(pivot[0]), float(pivot[1]))


def _validate_slice(name, borders, width, height):
    """9-slice borders are (left, top, right, bottom) insets in
    pixels — whole numbers, 0 or more, leaving at least one
    pixel of middle in each direction. None means no 9-slice."""
    if borders is None:
        return None
    if (not isinstance(borders, (tuple, list))
            or len(borders) != 4):
        raise PixelForgeError(
            "9-slice borders for %s must be four numbers (got %r)"
            % (name, borders))
    for value in borders:
        if (not isinstance(value, int)
                or isinstance(value, bool) or value < 0):
            raise PixelForgeError(
                "9-slice borders for %s must be whole numbers of "
                "0 or more (got %r)" % (name, borders))
    left, top, right, bottom = borders
    if left + right >= width or top + bottom >= height:
        raise PixelForgeError(
            "9-slice borders for %s (%d, %d, %d, %d) leave no "
            "middle in a %d x %d sprite"
            % (name, left, top, right, bottom, width, height))
    return (left, top, right, bottom)


def _dedupe_key(image):
    """Identical pixel data packs once — the key is the size
    plus a hash of the bytes."""
    digest = hashlib.sha256(image.data).hexdigest()
    return (image.width(), image.height(), digest)


def pack_atlas(sprites, max_size=DEFAULT_MAX_SIZE,
               padding=DEFAULT_PADDING, extrude=DEFAULT_EXTRUDE,
               trim=False, multipack=False,
               pivots=None, slices=None):
    """Pack mixed-size sprites into one or more atlas sheets.

    sprites is a list of (name, Image) in input order — the
    frames list in the result keeps that order, so animation
    frames stay in playback order even though the packer
    places them largest first. Names must be unique (they are
    the identity for pivots, slices, and aliases).

    Returns a dict: "sheets" (list of Image, one per sheet),
    "frames" (one placement per placed sprite, in input order
    of first occurrence, each with name, sheet index, x, y, w,
    h, source_w, source_h, trimmed insets, pivot, slice, and
    aliases), "deduped" (how many sprites became aliases),
    "sheet_count", "input_names" (every input name in input
    order, aliases included — the metadata builder needs it
    to hand each frame its own duration), and "settings" (the
    packer settings, for the metadata). Raises PixelForgeError
    naming the sprite and the cap when a sprite is bigger than
    max_size, and naming the count and the cap when the sprites
    do not fit in one sheet without --multipack.
    """
    if not sprites:
        raise PixelForgeError("no sprites to pack")
    if not isinstance(max_size, int) or isinstance(max_size, bool):
        raise PixelForgeError(
            "max_size must be a whole number (got %r)" % (max_size,))
    if max_size < MIN_MAX_SIZE or max_size > MAX_MAX_SIZE:
        raise PixelForgeError(
            "max_size must be between %d and %d (got %d)"
            % (MIN_MAX_SIZE, MAX_MAX_SIZE, max_size))
    if not isinstance(padding, int) or isinstance(padding, bool):
        raise PixelForgeError(
            "padding must be a whole number (got %r)" % (padding,))
    if padding < 0 or padding > MAX_PADDING:
        raise PixelForgeError(
            "padding must be between 0 and %d (got %d)"
            % (MAX_PADDING, padding))
    if not isinstance(extrude, int) or isinstance(extrude, bool):
        raise PixelForgeError(
            "extrude must be a whole number (got %r)" % (extrude,))
    if extrude < 0 or extrude > MAX_EXTRUDE:
        raise PixelForgeError(
            "extrude must be between 0 and %d (got %d)"
            % (MAX_EXTRUDE, extrude))
    if extrude > padding:
        raise PixelForgeError(
            "extrude (%d px) is larger than the padding (%d px) "
            "— extrude only writes into the padding, so it "
            "cannot reach past it" % (extrude, padding))
    seen_names = set()
    for name, _image in sprites:
        if name in seen_names:
            raise PixelForgeError(
                "two sprites are named %r — names must be unique"
                % name)
        seen_names.add(name)
    pivots = pivots or {}
    slices = slices or {}
    for name in pivots:
        if name not in seen_names:
            raise PixelForgeError(
                "a pivot was given for %r, which is not one of "
                "the sprites" % name)
    for name in slices:
        if name not in seen_names:
            raise PixelForgeError(
                "9-slice borders were given for %r, which is not "
                "one of the sprites" % name)

    # Trim first (when asked), then dedupe on what will
    # actually be packed — two sprites that differ only in
    # transparent borders are the same sprite once trimmed.
    prepared = []  # (name, packed image, source_w, source_h, insets)
    for name, image in sprites:
        source_w, source_h = image.width(), image.height()
        if trim:
            packed, left, top, right, bottom = trim_transparent(
                image)
        else:
            packed = image
            left, top, right, bottom = 0, 0, 0, 0
        prepared.append((name, packed, source_w, source_h,
                         (left, top, right, bottom)))
    unique = []  # one entry per packed sprite: (name, packed, ...)
    aliases_of = {}  # placed name -> [alias names]
    deduped = 0
    by_key = {}
    for entry in prepared:
        name, packed = entry[0], entry[1]
        key = _dedupe_key(packed)
        if key in by_key:
            aliases_of[by_key[key]].append(name)
            deduped += 1
        else:
            by_key[key] = name
            aliases_of[name] = []
            unique.append(entry)

    # Every sprite's cell must fit the cap on its own, or no
    # sheet can hold it — refuse naming the sprite and the cap.
    cells = {}
    for name, packed, _sw, _sh, _insets in unique:
        cell_w = packed.width() + 2 * padding
        cell_h = packed.height() + 2 * padding
        if cell_w > max_size or cell_h > max_size:
            raise PixelForgeError(
                "sprite %s is %d x %d but the atlas cap is "
                "%d x %d — raise --max-size"
                % (name, packed.width(), packed.height(),
                   max_size, max_size))
        cells[name] = (cell_w, cell_h)

    # Largest first, for a tighter pack — ties break on area,
    # then name, so the pack is deterministic.
    order = sorted(unique,
                   key=lambda e: (-max(e[1].width(), e[1].height()),
                                  -(e[1].width() * e[1].height()),
                                  e[0]))
    remaining = list(order)
    sheets = []  # (bin, [placed entries]) per sheet
    while remaining:
        # Start the bin at the smallest square that could hold
        # everything left by area (and at least the largest
        # cell), then double until everything left fits or the
        # cap is hit. A partial attempt is DISCARDED and
        # retried bigger — closing the sheet on the first
        # attempt that places anything would strand sprites
        # that fit fine in a bigger bin. Only at the cap does
        # a partial attempt close the sheet, spilling the rest
        # to the next one.
        area = sum(cells[e[0]][0] * cells[e[0]][1]
                   for e in remaining)
        side = max(max(cells[e[0]][0] for e in remaining),
                   max(cells[e[0]][1] for e in remaining),
                   math.ceil(math.sqrt(area)))
        # The area estimate can run past the cap when many
        # sprites are left — the first attempt is at the cap,
        # and what does not fit spills to the next sheet.
        side = min(side, max_size)
        while True:
            attempt = _Bin(side)
            placed = []
            for entry in remaining:
                name = entry[0]
                cell_w, cell_h = cells[name]
                if attempt.try_place(name, cell_w, cell_h):
                    placed.append(entry)
            if len(placed) == len(remaining):
                sheets.append((attempt, placed))
                remaining = []
                break
            if side >= max_size:
                if not placed:
                    # Impossible: every cell fits the cap on
                    # its own, so the first sprite always fits
                    # a fresh bin. Defensive only.
                    raise PixelForgeError(
                        "internal error: could not place %s in "
                        "a %d x %d atlas"
                        % (remaining[0][0], max_size, max_size))
                sheets.append((attempt, placed))
                for entry in placed:
                    remaining.remove(entry)
                break
            side = min(side * 2, max_size)
        if remaining and not multipack:
            raise PixelForgeError(
                "%d of %d sprites do not fit in one %d x %d "
                "atlas — raise --max-size or pass --multipack"
                % (len(remaining), len(sprites),
                   max_size, max_size))

    # Render each sheet, cropped to the union of its cells, then
    # extrude every sprite into its own padding ring.
    sheet_images = []
    frame_by_name = {}
    for sheet_index, (attempt, placed) in enumerate(sheets):
        max_x = max(cell[0] + cell[2]
                    for cell in attempt.placements.values())
        max_y = max(cell[1] + cell[3]
                    for cell in attempt.placements.values())
        sheet = Image(max_x, max_y)
        packed_by_name = {e[0]: e[1] for e in unique}
        for entry in placed:
            name = entry[0]
            packed = packed_by_name[name]
            cell_x, cell_y, _cw, _ch = attempt.placements[name]
            x, y = cell_x + padding, cell_y + padding
            _blit(sheet, packed, x, y)
        if extrude:
            for entry in placed:
                name = entry[0]
                packed = packed_by_name[name]
                cell_x, cell_y, _cw, _ch = attempt.placements[name]
                _extrude(sheet, packed, cell_x + padding,
                         cell_y + padding, extrude)
        sheet_images.append(sheet)
        for entry in placed:
            name = entry[0]
            packed = packed_by_name[name]
            cell_x, cell_y, _cw, _ch = attempt.placements[name]
            x, y = cell_x + padding, cell_y + padding
            left, top, right, bottom = entry[4]
            pivot = _validate_pivot(
                name, pivots.get(name, DEFAULT_PIVOT))
            borders = _validate_slice(
                name, slices.get(name), packed.width(),
                packed.height())
            frame_by_name[name] = {
                "name": name, "sheet": sheet_index,
                "x": x, "y": y,
                "w": packed.width(), "h": packed.height(),
                "source_w": entry[2], "source_h": entry[3],
                "trimmed": {"left": left, "top": top,
                            "right": right, "bottom": bottom},
                "pivot": [pivot[0], pivot[1]],
                "slice": (list(borders) if borders is not None
                          else None),
                "aliases": list(aliases_of[name]),
            }
    # Frames in input order (of first occurrence), so animation
    # frames stay in playback order.
    frames = [frame_by_name[e[0]] for e in unique]
    return {
        "sheets": sheet_images,
        "frames": frames,
        "deduped": deduped,
        "sheet_count": len(sheet_images),
        # The full input order of names — aliases are grouped
        # under their placed frame in "frames", so this is the
        # only record of where each input sprite sat. The
        # metadata builder uses it to hand each frame its own
        # duration.
        "input_names": [name for name, _image in sprites],
        "settings": {
            "packer": PACKER_NAME,
            "max_size": max_size,
            "padding": padding,
            "extrude": extrude,
            "trim": bool(trim),
        },
    }


def _blit(sheet, image, x, y):
    """Copy an image onto the sheet at (x, y) — a plain copy,
    like pack_frames: the sheet starts transparent and sprites
    never overlap, so no blending is needed."""
    sheet_data = sheet.data
    sheet_w = sheet.width()
    src = image.data
    src_stride = image.width() * 4
    for row in range(image.height()):
        s = row * src_stride
        d = ((y + row) * sheet_w + x) * 4
        sheet_data[d:d + src_stride] = src[s:s + src_stride]


def _extrude(sheet, image, x, y, amount):
    """Repeat an image's edge pixels outward by `amount` into
    the padding around it. amount never exceeds the padding,
    so this only ever writes into the sprite's own ring —
    never over another sprite. Corners get the corner pixel."""
    width, height = image.width(), image.height()
    data = sheet.data
    sheet_w = sheet.width()
    src = image.data

    def copy_row(src_y, dst_y):
        s = src_y * width * 4
        d = (dst_y * sheet_w + x) * 4
        data[d:d + width * 4] = src[s:s + width * 4]

    def copy_col(src_x, dst_x, top, bottom):
        # The column spans the extruded rows above and below
        # the sprite too — those rows take the corner pixel,
        # so the source row is clamped to the sprite's edge
        # (an unclamped row would read outside the image).
        for row in range(top, bottom):
            src_row = min(max(row - y, 0), height - 1)
            s = src_row * width * 4 + src_x * 4
            d = (row * sheet_w + dst_x) * 4
            data[d:d + 4] = src[s:s + 4]

    # Top and bottom rows, stretched the full sprite width.
    for step in range(1, amount + 1):
        copy_row(0, y - step)
        copy_row(height - 1, y + height - 1 + step)
    # Left and right columns, including the extruded rows
    # above and below — so the corners land as the corner
    # pixel, not transparent.
    top, bottom = y - amount, y + height + amount
    for step in range(1, amount + 1):
        copy_col(0, x - step, top, bottom)
        copy_col(width - 1, x + width - 1 + step, top, bottom)


def build_atlas_metadata(result, sheet_names, fps=None,
                         durations=None, tags=None):
    """The metadata dict for a pack_atlas result.

    sheet_names are the PNG file names, one per sheet ("" when
    a sheet has not been saved yet, as in the preview dialog —
    the name is filled in at export). durations is one
    millisecond count per INPUT sprite, in input order — a
    placed frame takes the duration of its first occurrence
    (an aliased frame shares the first one's timing; see the
    module docstring). When durations is missing and fps is
    given, every frame gets 1000 / fps. tags, when given, is
    a list of {"name", "from", "to"} animation tags, validated
    before it is stored; an empty or missing list leaves the
    "tags" key out.
    """
    frames_in = result["frames"]
    if durations is not None:
        # durations run over the INPUT sprites (aliases
        # included) — the count check is against the input
        # count, and each placed frame takes the duration of
        # its own name's position in input order. Aliases are
        # NOT contiguous in input order (a duplicate can sit
        # anywhere), so the mapping goes through input_names,
        # never by walking placed frames plus alias counts.
        input_names = result["input_names"]
        if len(durations) != len(input_names):
            raise PixelForgeError(
                "internal error: %d sprites but %d durations"
                % (len(input_names), len(durations)))
        for duration in durations:
            if (not isinstance(duration, int)
                    or isinstance(duration, bool) or duration < 1):
                raise PixelForgeError(
                    "frame durations must be whole numbers of at "
                    "least 1 ms (got %r)" % (duration,))
    if tags:
        validate_tags(tags, len(frames_in))
    default_duration = (duration_for_fps(fps)
                        if fps is not None else None)
    duration_by_name = {}
    if durations is not None:
        duration_by_name = dict(zip(result["input_names"],
                                    durations))
    frames = []
    for index, frame in enumerate(frames_in):
        entry = dict(frame)
        entry["index"] = index
        if durations is not None:
            entry["duration_ms"] = duration_by_name[frame["name"]]
        elif default_duration is not None:
            entry["duration_ms"] = default_duration
        frames.append(entry)
    sheets = [{"file": name, "width": sheet.width(),
               "height": sheet.height()}
              for name, sheet in zip(sheet_names, result["sheets"])]
    metadata = {"format": FORMAT_NAME, "version": FORMAT_VERSION,
                "packer": result["settings"]["packer"],
                "max_size": result["settings"]["max_size"],
                "padding": result["settings"]["padding"],
                "extrude": result["settings"]["extrude"],
                "trim": result["settings"]["trim"],
                "deduped": result["deduped"],
                "sheets": sheets,
                "frames": frames}
    if fps is not None:
        metadata["fps"] = fps
    if tags:
        metadata["tags"] = [dict(tag) for tag in tags]
    return metadata


def write_atlas(images, names, out_png, out_json,
                max_size=DEFAULT_MAX_SIZE,
                padding=DEFAULT_PADDING, extrude=DEFAULT_EXTRUDE,
                trim=False, multipack=False,
                pivots=None, slices=None, fps=None,
                durations=None, tags=None):
    """Pack images, save the atlas PNGs + metadata JSON.

    With one sheet, the PNG is out_png as given. With several
    (multipack), the PNGs are named from out_png's stem —
    stem_0.png, stem_1.png — and the JSON names them all. The
    JSON's "sheets" list is the PNG file names, so the set
    stays linked when moved together. Parent folders are
    created as needed. The JSON write is atomic (tmp + fsync
    + os.replace), like write_sheet. Returns (metadata dict,
    list of written paths: the PNGs, then the JSON). Raises
    PixelForgeError with a readable message on any failure.
    """
    if len(images) != len(names):
        raise PixelForgeError(
            "internal error: %d images but %d names"
            % (len(images), len(names)))
    result = pack_atlas(list(zip(names, images)),
                        max_size=max_size, padding=padding,
                        extrude=extrude, trim=trim,
                        multipack=multipack,
                        pivots=pivots, slices=slices)
    png_path = Path(out_png)
    json_path = Path(out_json)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_paths = []
    if result["sheet_count"] == 1:
        png_paths.append(png_path)
    else:
        for index in range(result["sheet_count"]):
            png_paths.append(png_path.with_name(
                "%s_%d%s" % (png_path.stem, index,
                             png_path.suffix)))
    for sheet, path in zip(result["sheets"], png_paths):
        if not sheet.save(str(path), "PNG"):
            raise PixelForgeError("could not write: %s" % path)
    metadata = build_atlas_metadata(
        result, [path.name for path in png_paths],
        fps=fps, durations=durations, tags=tags)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = json_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as handle:
            json.dump(metadata, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, json_path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError("could not write %s: %s"
                              % (json_path, exc))
    return metadata, [str(path) for path in png_paths] + [str(json_path)]
