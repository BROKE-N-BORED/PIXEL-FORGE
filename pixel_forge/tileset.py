"""Tileset / autotile tools — Wang 16 / blob 47 tilesets from a
few base tiles, plus Tiled .tsx/.tmx and Godot 4 .tres export
(task 37).

Conventions (pinned by tests in tests/test_tileset.py):

Wang 16 — two base tiles, terrain A and terrain B, the same size.
Tile index = the 4-bit edge mask: bit 0 = north edge, 1 = east,
2 = south, 3 = west; a set bit means that edge is terrain B.
Tile 0 is all A, tile 15 is all B. Construction: each base tile is
split into 4 quadrants; a quadrant whose two meeting edges are the
same terrain takes that terrain's quadrant whole; a quadrant whose
edges differ is split along the corner diagonal — the half nearer
the quadrant's horizontal edge (north for the top quadrants,
south for the bottom) takes that edge's terrain, the other half
takes the vertical edge's terrain. The diagonal pixels themselves
take the horizontal edge's terrain. (A corner pixel belongs to
both meeting edges, so no construction can satisfy both when the
edges differ — the edge-pixel tests skip the corner pixels.)

Blob 47 — one base tile, the terrain drawn against transparency.
Mask = 8 neighbor bits, clockwise from the north edge: bit 0 = N,
1 = NE, 2 = E, 3 = SE, 4 = S, 5 = SW, 6 = W, 7 = NW. A set bit
means a terrain neighbor in that direction. A corner bit only
matters when both adjacent edge bits are set — a corner neighbor
with an adjacent edge missing can never show — so the 256 masks
collapse to 47 valid ones. Tile index = the mask's position in
the sorted list of valid masks (mask 0 -> index 0, mask 255 ->
index 46). Construction, per quadrant: both adjacent edges
present and the corner present -> the base tile's quadrant whole;
both edges present, corner missing -> a diagonal cut, terrain
toward the tile center; exactly one edge present -> the half of
the quadrant next to that edge; no adjacent edges -> transparent.
Mask 255 is the base tile unchanged; mask 0 is fully transparent.

The blob mask bits are in Tiled's wangid order (top, top-right,
right, bottom-right, bottom, bottom-left, left, top-left), so a
blob tile's wangid is its mask bits as 0/1 values. A Wang tile's
wangid takes its edge values from the mask and each corner from
the next edge clockwise (NW corner takes the north edge's color,
NE the east, SE the south, SW the west) — a diagonal-split corner
is half A half B, and Tiled wants one color per corner.

autotile_map(grid) maps a boolean terrain grid to blob tile
indices (None for empty cells); wang_map(grid) maps a 0/1 terrain
grid to Wang tile indices from the NEIGHBORS' terrains, with
out-of-bounds counting as terrain A — the cell's own terrain does
not enter the index, because a Wang tile's edges show what is on
the other side of each edge. The demo .tmx uses one or the other:
blob mode paints a terrain rectangle in the middle of empty
ground, Wang mode fills the whole map with terrain A and a
terrain B rectangle in the middle.

Exports: write_tsx (a Tiled tileset, XML via xml.etree, with a
wangset), write_tmx (a minimal valid Tiled map, CSV layer data),
write_tres (a Godot 4 TileSet resource, text format). The .tres
follows the shape Godot itself writes: [gd_resource
type="TileSet" format=3], an ext_resource for the PNG, a
TileSetAtlasSource with one X:Y/0 = 0 line per tile plus per-tile
terrain_set / terrain / terrains_peering_bit/<side> lines, a
TileSetTerrainSet in match corners and sides mode, and a
[resource] section naming the tile size, the terrain set, and the
source. The terrain bits match the tiles' masks: blob peering
bits are 0 where the neighbor is terrain, -1 where it is not (the
center terrain is 0 except for the empty mask-0 tile, which is
-1); Wang bits are 0 for terrain A, 1 for terrain B, and the
center terrain is whichever terrain occupies more of the tile's
pixels (split quadrants are half and half, so only the whole
quadrants decide; a tie goes to A).

Verified structurally here — the TSX/TMX parse back with
xml.etree and the .tres is checked line by line — but no Tiled
or Godot runs in this sandbox, so opening the exports in the real
editors is a real-PC check, not something proven here. The README
says so.

Stdlib-only: no Qt, no ffmpeg — the command line uses this module
directly, and the tests run on bare python.
"""

import io
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .image import Image
from .pixelize import PixelForgeError
from .spritesheet import pack_frames

WANG_TILE_COUNT = 16
BLOB_TILE_COUNT = 47

# Modes, in the order the GUI lists them.
TILESET_MODES = ("wang", "blob")
MODE_NAMES = {"wang": "Wang 16", "blob": "Blob 47"}

# What one export writes (the PNG always comes along — a .tsx,
# .tmx, or .tres without its image is useless, and the .tmx needs
# its .tsx too).
TILESET_EXPORTS = ("png", "tsx", "tmx", "tres", "all")
EXPORT_FILES = {
    "png": ("png",),
    "tsx": ("png", "tsx"),
    "tmx": ("png", "tsx", "tmx"),
    "tres": ("png", "tres"),
    "all": ("png", "tsx", "tmx", "tres"),
}

DEMO_MAP_SIZE = 10  # default demo .tmx size, square
MIN_DEMO_MAP_SIZE = 3  # smaller than 3 x 3 the demo has no interior

# --- bit conventions ------------------------------------------------
# Wang: bit 0 = north edge, 1 = east, 2 = south, 3 = west; a set
# bit means that edge is terrain B. Tile index = the mask.
WANG_BITS = {"N": 0, "E": 1, "S": 2, "W": 3}
# Blob: 8 neighbor bits, clockwise from the north edge.
BLOB_BITS = {"N": 0, "NE": 1, "E": 2, "SE": 3, "S": 4, "SW": 5,
             "W": 6, "NW": 7}
# Blob neighbor offsets in bit order.
BLOB_NEIGHBORS = ((0, -1), (1, -1), (1, 0), (1, 1),
                  (0, 1), (-1, 1), (-1, 0), (-1, -1))
# Wang neighbor offsets in bit order.
WANG_NEIGHBORS = ((0, -1), (1, 0), (0, 1), (-1, 0))
# Each blob corner and the two edges that must both be present
# for it to show.
BLOB_CORNERS = {"NE": ("N", "E"), "SE": ("S", "E"),
                "SW": ("S", "W"), "NW": ("N", "W")}

# Tiled wangid order: top, top-right, right, bottom-right, bottom,
# bottom-left, left, top-left — clockwise from the top edge.
WANGID_ORDER = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Tiled wangset colors — editor markers, not game art. The same
# colors are used for the Godot terrains, so the two exports
# agree about which terrain is which.
WANG_COLOR_A = "#4caf50"
WANG_COLOR_B = "#2196f3"
BLOB_COLOR = "#4caf50"
TILED_VERSION = "1.10"
TILED_APP_VERSION = "1.10.0"

# Godot 4: terrain set mode 0 is match corners and sides — the
# mode that compares both edges and corners, which is what both
# sets describe. (Match corners alone would need a corner-only
# set; match sides alone a sides-only set.)
GODOT_TERRAIN_MODE_MATCH_CORNERS_AND_SIDES = 0
# Godot peering-bit property names, clockwise from the top side —
# the same order as the blob mask bits and the Tiled wangid.
GODOT_PEERING_BITS = ("top_side", "top_right_corner", "right_side",
                      "bottom_right_corner", "bottom_side",
                      "bottom_left_corner", "left_side",
                      "top_left_corner")


def _valid_blob_mask(mask):
    """A blob mask is valid when every set corner bit has both
    adjacent edge bits set — a corner neighbor with an adjacent
    edge missing can never show, so no tile is built for it."""
    for corner, (first, second) in BLOB_CORNERS.items():
        if mask & (1 << BLOB_BITS[corner]):
            if not (mask & (1 << BLOB_BITS[first])
                    and mask & (1 << BLOB_BITS[second])):
                return False
    return True


# The 47 valid blob masks, sorted ascending. Tile index = the
# mask's position here: mask 0 -> index 0, mask 255 -> index 46.
BLOB_MASKS = [mask for mask in range(256) if _valid_blob_mask(mask)]
BLOB_MASK_TO_INDEX = {mask: index
                      for index, mask in enumerate(BLOB_MASKS)}


# --- construction ---------------------------------------------------
# Quadrants, in the order NW, NE, SW, SE. v_edge is the edge at
# the quadrant's v=0 side — its horizontal edge (north for the
# top quadrants, south for the bottom); u_edge is the vertical
# one. flip_u/flip_v say whether the local coordinate is measured
# from the quadrant's far edge: the local origin is the quadrant's
# OUTER corner either way (top-left for NW, top-right for NE,
# bottom-left for SW, bottom-right for SE).
_QUADRANTS = (
    ("N", "W", "NW", False, False),   # NW
    ("N", "E", "NE", True, False),    # NE
    ("S", "W", "SW", False, True),    # SW
    ("S", "E", "SE", True, True),     # SE
)


def _quadrant_rects(width, height):
    """The four quadrant rectangles as (x0, y0, qw, qh), in
    _QUADRANTS order. The split is at width // 2 and height // 2,
    so for an odd size the east/south quadrants are one pixel
    bigger — every pixel belongs to exactly one quadrant."""
    half_w, half_h = width // 2, height // 2
    return ((0, 0, half_w, half_h),
            (half_w, 0, width - half_w, half_h),
            (0, half_h, half_w, height - half_h),
            (half_w, half_h, width - half_w, height - half_h))


def _check_tile(image, what):
    if image is None or image.isNull():
        raise PixelForgeError("%s is empty" % what)


def _blit_quadrant(src_data, dst_data, width, x0, y0, qw, qh):
    """Copy one quadrant rectangle from src to dst, row by row —
    the fast path for a quadrant taken whole."""
    stride = width * 4
    for row in range(qh):
        offset = ((y0 + row) * width + x0) * 4
        dst_data[offset:offset + qw * 4] = \
            src_data[offset:offset + qw * 4]


def wang_tiles(tile_a, tile_b):
    """The 16 Wang tiles from two same-size base tiles: index is
    the edge mask (bit 0 = north, 1 = east, 2 = south, 3 = west;
    set = terrain B). Tile 0 is all A, tile 15 is all B."""
    _check_tile(tile_a, "terrain A tile")
    _check_tile(tile_b, "terrain B tile")
    if ((tile_a.width(), tile_a.height())
            != (tile_b.width(), tile_b.height())):
        raise PixelForgeError(
            "the two base tiles must be the same size "
            "(terrain A is %d x %d, terrain B is %d x %d)"
            % (tile_a.width(), tile_a.height(),
               tile_b.width(), tile_b.height()))
    return [_wang_tile(tile_a, tile_b, mask)
            for mask in range(WANG_TILE_COUNT)]


def _wang_tile(tile_a, tile_b, mask):
    width, height = tile_a.width(), tile_a.height()
    out = Image(width, height)
    a_data, b_data, o_data = tile_a.data, tile_b.data, out.data
    for (v_edge, u_edge, _corner, flip_u, flip_v), \
            (x0, y0, qw, qh) in zip(_QUADRANTS,
                                    _quadrant_rects(width, height)):
        v_is_b = bool(mask & (1 << WANG_BITS[v_edge]))
        u_is_b = bool(mask & (1 << WANG_BITS[u_edge]))
        if v_is_b == u_is_b:
            # Both meeting edges are the same terrain: the whole
            # quadrant comes from that terrain's base tile.
            _blit_quadrant(b_data if v_is_b else a_data, o_data,
                           width, x0, y0, qw, qh)
            continue
        # The edges differ: split along the corner diagonal. u > v
        # is nearer the horizontal edge, u < v nearer the vertical
        # one; the diagonal pixels (u == v) take the horizontal
        # edge's terrain.
        v_src = b_data if v_is_b else a_data
        u_src = b_data if u_is_b else a_data
        for v in range(qh):
            for u in range(qw):
                src = v_src if u >= v else u_src
                x = x0 + (qw - 1 - u if flip_u else u)
                y = y0 + (qh - 1 - v if flip_v else v)
                offset = (y * width + x) * 4
                o_data[offset:offset + 4] = src[offset:offset + 4]
    return out


def blob_tiles(tile):
    """The 47 blob tiles from one base tile: index is the mask's
    position in BLOB_MASKS (mask 0 -> index 0, fully transparent;
    mask 255 -> index 46, the base tile unchanged)."""
    _check_tile(tile, "base tile")
    return [_blob_tile(tile, mask) for mask in BLOB_MASKS]


def _blob_tile(tile, mask):
    width, height = tile.width(), tile.height()
    out = Image(width, height)
    src_data, o_data = tile.data, out.data
    for (v_edge, u_edge, corner, flip_u, flip_v), \
            (x0, y0, qw, qh) in zip(_QUADRANTS,
                                    _quadrant_rects(width, height)):
        v_on = bool(mask & (1 << BLOB_BITS[v_edge]))
        u_on = bool(mask & (1 << BLOB_BITS[u_edge]))
        c_on = bool(mask & (1 << BLOB_BITS[corner]))
        if not v_on and not u_on:
            continue  # no adjacent neighbors: transparent
        if v_on and u_on and c_on:
            # The corner's neighbors are all present: whole.
            _blit_quadrant(src_data, o_data, width, x0, y0, qw, qh)
            continue
        for v in range(qh):
            for u in range(qw):
                if v_on and u_on:
                    # Both edges, corner missing: diagonal cut,
                    # terrain toward the tile center. The cut runs
                    # from the midpoint of one outer edge to the
                    # midpoint of the other (u/qw + v/qh >= 1/2).
                    show = (2 * (u * qh + v * qw) >= qw * qh)
                elif v_on:
                    # Only the horizontal edge: the half next to it.
                    show = 2 * v < qh
                else:
                    # Only the vertical edge: the half next to it.
                    show = 2 * u < qw
                if not show:
                    continue
                x = x0 + (qw - 1 - u if flip_u else u)
                y = y0 + (qh - 1 - v if flip_v else v)
                offset = (y * width + x) * 4
                o_data[offset:offset + 4] = \
                    src_data[offset:offset + 4]
    return out


# --- tile data ------------------------------------------------------
def wang_id(mask):
    """The 8 Tiled wangid values for a Wang mask: 1 = terrain A,
    2 = terrain B, in Tiled's clockwise order (top, top-right,
    right, bottom-right, bottom, bottom-left, left, top-left).
    Edges come from the mask; each corner takes the next edge
    clockwise (NW takes north, NE east, SE south, SW west)."""
    edge = {d: (2 if mask & (1 << WANG_BITS[d]) else 1)
            for d in ("N", "E", "S", "W")}
    values = dict(edge)
    values.update({"NW": edge["N"], "NE": edge["E"],
                   "SE": edge["S"], "SW": edge["W"]})
    return [values[d] for d in WANGID_ORDER]


def blob_id(mask):
    """The 8 Tiled wangid values for a blob mask: 1 where that
    neighbor is terrain, 0 where it is not, in Tiled's clockwise
    order. The blob bits are already in that order, so this is
    the mask bits as 0/1 values."""
    return [1 if mask & (1 << BLOB_BITS[d]) else 0
            for d in WANGID_ORDER]


def wang_center_terrain(mask):
    """The terrain at the center of a Wang tile (for Godot's
    per-tile `terrain`): whichever terrain occupies more of the
    tile's pixels. Split quadrants are half A half B, so only the
    whole quadrants decide; a tie goes to A (0)."""
    whole_a = whole_b = 0
    for v_edge, u_edge, _corner, _flip_u, _flip_v in _QUADRANTS:
        v_is_b = bool(mask & (1 << WANG_BITS[v_edge]))
        u_is_b = bool(mask & (1 << WANG_BITS[u_edge]))
        if v_is_b == u_is_b:
            if v_is_b:
                whole_b += 1
            else:
                whole_a += 1
    return 1 if whole_b > whole_a else 0


def _wang_peering_bits(mask):
    """Godot's 8 peering-bit values for a Wang mask, in
    GODOT_PEERING_BITS order: 0 = terrain A, 1 = terrain B.
    Sides come from the mask; corners take the next edge
    clockwise, the same rule as wang_id."""
    edge = {d: (1 if mask & (1 << WANG_BITS[d]) else 0)
            for d in ("N", "E", "S", "W")}
    values = {"top_side": edge["N"],
              "top_right_corner": edge["E"],
              "right_side": edge["E"],
              "bottom_right_corner": edge["S"],
              "bottom_side": edge["S"],
              "bottom_left_corner": edge["W"],
              "left_side": edge["W"],
              "top_left_corner": edge["N"]}
    return [values[name] for name in GODOT_PEERING_BITS]


def _blob_peering_bits(mask):
    """Godot's 8 peering-bit values for a blob mask, in
    GODOT_PEERING_BITS order: 0 where the neighbor is terrain,
    -1 where it is not (Godot's "any terrain"). The blob bits
    are already in that order."""
    return [0 if mask & (1 << bit) else -1 for bit in range(8)]


# --- maps -----------------------------------------------------------
def _check_grid(grid, what):
    if not grid:
        return 0, 0
    width = len(grid[0])
    for row in grid:
        if len(row) != width:
            raise PixelForgeError(
                "%s rows must all be the same length" % what)
    return width, len(grid)


def autotile_map(grid):
    """Blob tile indices for a grid of booleans (terrain present
    or not), one per cell: the index comes from the cell's 8
    neighbors, out-of-bounds counting as absent. Empty cells get
    None (no tile). A corner neighbor only counts when both
    adjacent edge neighbors are present — the same rule that
    makes the set 47 tiles, so a diagonal-only neighbor never
    produces a mask with no tile."""
    width, height = _check_grid(grid, "terrain grid")
    out = []
    for y in range(height):
        row = []
        for x in range(width):
            if not grid[y][x]:
                row.append(None)
                continue
            mask = 0
            for bit, (dx, dy) in enumerate(BLOB_NEIGHBORS):
                nx, ny = x + dx, y + dy
                if (0 <= ny < height and 0 <= nx < width
                        and grid[ny][nx]):
                    mask |= 1 << bit
            # Prune corner bits whose adjacent edges are missing.
            for corner, (first, second) in BLOB_CORNERS.items():
                bit = 1 << BLOB_BITS[corner]
                if mask & bit:
                    if not (mask & (1 << BLOB_BITS[first])
                            and mask & (1 << BLOB_BITS[second])):
                        mask &= ~bit
            row.append(BLOB_MASK_TO_INDEX[mask])
        out.append(row)
    return out


def wang_map(grid):
    """Wang tile indices for a grid of terrain values (0 = A,
    anything else = B), one per cell: the index comes from the
    NEIGHBORS' terrains (bit 0 = north neighbor is B, 1 = east,
    2 = south, 3 = west), out-of-bounds counting as terrain A.
    The cell's own terrain does not enter the index — a Wang
    tile's edges show what is on the other side of each edge."""
    width, height = _check_grid(grid, "terrain grid")
    out = []
    for y in range(height):
        row = []
        for x in range(width):
            mask = 0
            for bit, (dx, dy) in enumerate(WANG_NEIGHBORS):
                nx, ny = x + dx, y + dy
                if (0 <= ny < height and 0 <= nx < width
                        and grid[ny][nx]):
                    mask |= 1 << bit
            row.append(mask)
        out.append(row)
    return out


def demo_cells(mode, map_size=DEMO_MAP_SIZE):
    """The demo map's GID grid: rows of tile GIDs, 0 = empty.
    Blob mode paints a terrain rectangle in the middle of empty
    ground; Wang mode fills the whole map with terrain A and a
    terrain B rectangle in the middle. GID = tile index + 1
    (firstgid 1, one tileset)."""
    if mode not in TILESET_MODES:
        raise PixelForgeError(
            "unknown tileset mode %r (want wang or blob)" % (mode,))
    if map_size < MIN_DEMO_MAP_SIZE:
        raise PixelForgeError(
            "demo map size must be at least %d" % MIN_DEMO_MAP_SIZE)
    start = map_size // 3
    end = map_size - map_size // 3

    def inside(x, y):
        return start <= x < end and start <= y < end

    if mode == "blob":
        grid = [[inside(x, y) for x in range(map_size)]
                for y in range(map_size)]
        indices = autotile_map(grid)
        return [[0 if index is None else index + 1
                 for index in row] for row in indices]
    grid = [[1 if inside(x, y) else 0 for x in range(map_size)]
            for y in range(map_size)]
    indices = wang_map(grid)
    return [[index + 1 for index in row] for row in indices]


# --- file writers ---------------------------------------------------
def _write_bytes_atomic(path, data):
    """Write bytes atomically (temp file + fsync + os.replace),
    the same shape as engine_export's JSON writer: a crash
    mid-write must not leave a half-written file next to a good
    PNG."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + ".tmp")
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError("could not write %s: %s"
                              % (target, exc))


def _write_xml(path, root):
    """An XML tree to disk, indented and atomic, with the XML
    declaration Tiled expects."""
    ET.indent(root, space=" ")
    tree = ET.ElementTree(root)
    buffer = io.BytesIO()
    tree.write(buffer, encoding="utf-8", xml_declaration=True)
    _write_bytes_atomic(path, buffer.getvalue())


def write_tsx(path, png_name, tiles, columns, mode):
    """Write a Tiled .tsx tileset: tile size, tile count,
    columns, the PNG reference, and a wangset — two wangcolors
    (Terrain A, Terrain B) for Wang, one (Terrain) for blob —
    with one wangtile per tile carrying its Wang ID. png_name is
    the PNG file's name as the .tsx should reference it (a plain
    file name, same folder)."""
    if mode not in TILESET_MODES:
        raise PixelForgeError(
            "unknown tileset mode %r (want wang or blob)" % (mode,))
    if not tiles:
        raise PixelForgeError("no tiles to write")
    tile_w, tile_h = tiles[0].width(), tiles[0].height()
    rows = (len(tiles) + columns - 1) // columns
    root = ET.Element("tileset", {
        "version": TILED_VERSION, "tiledversion": TILED_APP_VERSION,
        "name": Path(png_name).stem,
        "tilewidth": str(tile_w), "tileheight": str(tile_h),
        "tilecount": str(len(tiles)), "columns": str(columns)})
    ET.SubElement(root, "image", {
        "source": png_name,
        "width": str(columns * tile_w),
        "height": str(rows * tile_h)})
    wangsets = ET.SubElement(root, "wangsets")
    wangset = ET.SubElement(wangsets, "wangset", {
        "name": "Terrain", "type": "mixed", "tile": "-1"})
    if mode == "wang":
        colors = (("Terrain A", WANG_COLOR_A),
                  ("Terrain B", WANG_COLOR_B))
    else:
        colors = (("Terrain", BLOB_COLOR),)
    for name, color in colors:
        ET.SubElement(wangset, "wangcolor", {
            "name": name, "color": color, "tile": "-1",
            "probability": "1"})
    for index in range(len(tiles)):
        if mode == "wang":
            values = wang_id(index)
        else:
            values = blob_id(BLOB_MASKS[index])
        ET.SubElement(wangset, "wangtile", {
            "tileid": str(index),
            "wangid": ",".join(str(value) for value in values)})
    _write_xml(path, root)


def write_tmx(path, tsx_name, tile_w, tile_h, cells):
    """Write a minimal valid Tiled .tmx map: one tileset
    reference (firstgid 1) and one tile layer, CSV data. cells is
    rows of GIDs, 0 = empty. tsx_name is the .tsx file's name as
    the .tmx should reference it (a plain file name, same
    folder)."""
    if not cells or not cells[0]:
        raise PixelForgeError("cannot write an empty map")
    height = len(cells)
    width = len(cells[0])
    for row in cells:
        if len(row) != width:
            raise PixelForgeError(
                "map rows must all be the same length")
    root = ET.Element("map", {
        "version": TILED_VERSION, "tiledversion": TILED_APP_VERSION,
        "orientation": "orthogonal", "renderorder": "right-down",
        "width": str(width), "height": str(height),
        "tilewidth": str(tile_w), "tileheight": str(tile_h),
        "infinite": "0", "nextlayerid": "2", "nextobjectid": "1"})
    ET.SubElement(root, "tileset", {
        "firstgid": "1", "source": tsx_name})
    layer = ET.SubElement(root, "layer", {
        "id": "1", "name": "Terrain",
        "width": str(width), "height": str(height)})
    data = ET.SubElement(layer, "data", {"encoding": "csv"})
    data.text = ("\n" + "\n".join(
        ",".join(str(gid) for gid in row) for row in cells) + "\n")
    _write_xml(path, root)


def _godot_color(hex_color):
    """A #rrggbb color as Godot writes it: Color(r, g, b, 1)."""
    red = int(hex_color[1:3], 16) / 255.0
    green = int(hex_color[3:5], 16) / 255.0
    blue = int(hex_color[5:7], 16) / 255.0
    return "Color(%.6f, %.6f, %.6f, 1)" % (red, green, blue)


def write_tres(path, png_name, tiles, columns, mode):
    """Write a Godot 4 TileSet resource (.tres, text format): an
    ext_resource for the PNG, a TileSetAtlasSource with one tile
    per atlas cell plus per-tile terrain_set / terrain /
    terrains_peering_bit lines, a TileSetTerrainSet in match
    corners and sides mode, and a [resource] section naming the
    tile size, the terrain set, and the source. png_name is the
    PNG file's name; it is referenced as res://<name>, so the
    .tres and the PNG belong in the same folder of the Godot
    project (the README says so)."""
    if mode not in TILESET_MODES:
        raise PixelForgeError(
            "unknown tileset mode %r (want wang or blob)" % (mode,))
    if not tiles:
        raise PixelForgeError("no tiles to write")
    tile_w, tile_h = tiles[0].width(), tiles[0].height()
    if mode == "wang":
        terrains = (("Terrain A", WANG_COLOR_A),
                    ("Terrain B", WANG_COLOR_B))
    else:
        terrains = (("Terrain", BLOB_COLOR),)
    lines = ['[gd_resource type="TileSet" format=3]', ""]
    lines.append(
        '[ext_resource type="Texture2D" path="res://%s" id="1"]'
        % png_name)
    lines.append("")
    lines.append(
        '[sub_resource type="TileSetAtlasSource" '
        'id="TileSetAtlasSource_1"]')
    lines.append('texture = ExtResource("1")')
    lines.append("texture_region_size = Vector2i(%d, %d)"
                 % (tile_w, tile_h))
    for index in range(len(tiles)):
        col, row = index % columns, index // columns
        # The tile declaration comes first: the tile must exist
        # before its TileData properties are set.
        lines.append("%d:%d/0 = 0" % (col, row))
        if mode == "wang":
            center = wang_center_terrain(index)
            bits = _wang_peering_bits(index)
        else:
            mask = BLOB_MASKS[index]
            center = -1 if mask == 0 else 0
            bits = _blob_peering_bits(mask)
        lines.append("%d:%d/0/terrain_set = 0" % (col, row))
        lines.append("%d:%d/0/terrain = %d" % (col, row, center))
        for name, value in zip(GODOT_PEERING_BITS, bits):
            lines.append("%d:%d/0/terrains_peering_bit/%s = %d"
                         % (col, row, name, value))
    lines.append("")
    lines.append(
        '[sub_resource type="TileSetTerrainSet" '
        'id="TileSetTerrainSet_1"]')
    lines.append("mode = %d"
                 % GODOT_TERRAIN_MODE_MATCH_CORNERS_AND_SIDES)
    entries = ['{\n"name": "%s",\n"color": %s\n}'
               % (name, _godot_color(color))
               for name, color in terrains]
    lines.append("terrains = Array[Dictionary]([%s])"
                 % ", ".join(entries))
    lines.append("")
    lines.append("[resource]")
    lines.append("tile_size = Vector2i(%d, %d)" % (tile_w, tile_h))
    lines.append(
        'terrain_sets = Array[TileSetTerrainSet]'
        '([SubResource("TileSetTerrainSet_1")])')
    lines.append('sources/0 = SubResource("TileSetAtlasSource_1")')
    lines.append("")
    _write_bytes_atomic(path, "\n".join(lines).encode("utf-8"))


def export_tileset(mode, tiles, out_dir, stem, export="all",
                   map_size=DEMO_MAP_SIZE):
    """Write a tileset's files into out_dir, named from stem.
    Returns the list of Path objects written, in order: the PNG
    first, then .tsx, .tmx, .tres as the export asks for. The PNG
    always comes along; the .tmx brings its .tsx. The .tmx is a
    demo map, so it is named <stem>-demo.tmx, not <stem>.tmx."""
    if mode not in TILESET_MODES:
        raise PixelForgeError(
            "unknown tileset mode %r (want wang or blob)" % (mode,))
    if export not in TILESET_EXPORTS:
        raise PixelForgeError(
            "unknown export %r (want one of %s)"
            % (export, ", ".join(TILESET_EXPORTS)))
    if not tiles:
        raise PixelForgeError("no tiles to export")
    if map_size < MIN_DEMO_MAP_SIZE:
        raise PixelForgeError(
            "demo map size must be at least %d" % MIN_DEMO_MAP_SIZE)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sheet, layout = pack_frames(tiles)
    columns = layout["columns"]
    written = []
    png_name = "%s.png" % stem
    png_path = out / png_name
    if not sheet.save(str(png_path)):
        raise PixelForgeError("could not write %s" % png_path)
    written.append(png_path)
    tile_w, tile_h = tiles[0].width(), tiles[0].height()
    kinds = EXPORT_FILES[export]
    if "tsx" in kinds:
        tsx_path = out / ("%s.tsx" % stem)
        write_tsx(tsx_path, png_name, tiles, columns, mode)
        written.append(tsx_path)
    if "tmx" in kinds:
        tmx_path = out / ("%s-demo.tmx" % stem)
        cells = demo_cells(mode, map_size)
        write_tmx(tmx_path, "%s.tsx" % stem, tile_w, tile_h, cells)
        written.append(tmx_path)
    if "tres" in kinds:
        tres_path = out / ("%s.tres" % stem)
        write_tres(tres_path, png_name, tiles, columns, mode)
        written.append(tres_path)
    return written
