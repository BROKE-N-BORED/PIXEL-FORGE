"""Tileset / autotile tests — Wang 16, blob 47, the map
functions, and the Tiled .tsx/.tmx + Godot .tres writers
(task 37).

The TSX/TMX writers are verified by parsing their output back
with xml.etree; the .tres writer by line-by-line text checks
plus a bracket-balance check. No Tiled or Godot runs here, so
this is a structural verification — the README says so.

Qt-free (task 64): runs without PySide6.
"""
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import make_solid_image, write_image  # noqa: E402
from pixel_forge.image import Image, argb  # noqa: E402
from pixel_forge.pixelize import PixelForgeError, load_image  # noqa: E402
from pixel_forge.tileset import (  # noqa: E402
    BLOB_MASK_TO_INDEX, BLOB_MASKS, DEMO_MAP_SIZE, autotile_map,
    blob_id, blob_tiles, demo_cells, export_tileset, wang_center_terrain,
    wang_id, wang_map, wang_tiles, write_tmx, write_tres, write_tsx)

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

RED = argb(255, 0, 0)
GREEN = argb(0, 255, 0)
BLUE = argb(0, 0, 255)
YELLOW = argb(255, 255, 0)
TRANSPARENT = 0


def quadrant_base(size=16):
    """A base tile with one color per quadrant — NW red, NE
    green, SW blue, SE yellow — so a test can tell which
    quadrant a pixel came from."""
    image = Image(size, size)
    half = size // 2
    for y in range(size):
        for x in range(size):
            if x < half and y < half:
                image.setPixel(x, y, RED)
            elif x >= half and y < half:
                image.setPixel(x, y, GREEN)
            elif x < half:
                image.setPixel(x, y, BLUE)
            else:
                image.setPixel(x, y, YELLOW)
    return image


class WangTests(unittest.TestCase):
    def setUp(self):
        self.tile_a = make_solid_image(16, 16, 255, 0, 0)
        self.tile_b = make_solid_image(16, 16, 0, 0, 255)
        self.tiles = wang_tiles(self.tile_a, self.tile_b)

    def test_sixteen_tiles(self):
        self.assertEqual(len(self.tiles), 16)
        for tile in self.tiles:
            self.assertEqual((tile.width(), tile.height()),
                             (16, 16))

    def test_tile_zero_is_all_a_and_fifteen_is_all_b(self):
        self.assertEqual(self.tiles[0], self.tile_a)
        self.assertEqual(self.tiles[15], self.tile_b)

    def test_every_tile_edge_pixels_match_mask(self):
        # Every non-corner edge pixel matches its mask bit (bit 0
        # = north, 1 = east, 2 = south, 3 = west; set = B). The
        # corner pixels belong to two edges at once, so no
        # construction can satisfy both when the edges differ —
        # they take the horizontal edge's terrain (pinned by
        # test_diagonal_split_quadrant) and are skipped here.
        for mask, tile in enumerate(self.tiles):
            want = [BLUE if mask & (1 << bit) else RED
                    for bit in range(4)]
            for x in range(1, 15):
                self.assertEqual(tile.pixel(x, 0), want[0],
                                 "mask %d north edge" % mask)
                self.assertEqual(tile.pixel(x, 15), want[2],
                                 "mask %d south edge" % mask)
            for y in range(1, 15):
                self.assertEqual(tile.pixel(15, y), want[1],
                                 "mask %d east edge" % mask)
                self.assertEqual(tile.pixel(0, y), want[3],
                                 "mask %d west edge" % mask)

    def test_diagonal_split_quadrant(self):
        # Tile 1 (north edge B only): the NW quadrant's edges
        # differ, so it splits along the corner diagonal — the
        # half nearer the north edge is B, the half nearer the
        # west edge is A, and the diagonal itself takes the
        # north (horizontal) edge's terrain.
        tile = self.tiles[1]
        self.assertEqual(tile.pixel(6, 1), BLUE)  # near north
        self.assertEqual(tile.pixel(1, 6), RED)   # near west
        self.assertEqual(tile.pixel(3, 3), BLUE)  # on the diagonal
        # The SE quadrant's edges are both A — solid red.
        self.assertEqual(tile.pixel(12, 12), RED)

    def test_mismatched_base_sizes_refuse(self):
        other = make_solid_image(32, 32, 0, 0, 255)
        with self.assertRaises(PixelForgeError) as caught:
            wang_tiles(self.tile_a, other)
        self.assertIn("16 x 16", str(caught.exception))
        self.assertIn("32 x 32", str(caught.exception))

    def test_empty_base_refuses(self):
        with self.assertRaises(PixelForgeError):
            wang_tiles(Image(0, 0), self.tile_b)
        with self.assertRaises(PixelForgeError):
            wang_tiles(self.tile_a, Image(0, 0))

    def test_odd_size_tiles_build(self):
        # The quadrant split puts the extra pixel in the
        # east/south quadrants — every pixel still belongs to
        # exactly one quadrant, and the edge midpoints still
        # match the mask.
        odd_a = make_solid_image(15, 15, 255, 0, 0)
        odd_b = make_solid_image(15, 15, 0, 0, 255)
        tiles = wang_tiles(odd_a, odd_b)
        self.assertEqual(len(tiles), 16)
        self.assertEqual(tiles[0], odd_a)
        self.assertEqual(tiles[15], odd_b)
        self.assertEqual(tiles[1].pixel(7, 0), BLUE)
        self.assertEqual(tiles[1].pixel(7, 14), RED)

    def test_wang_id(self):
        self.assertEqual(wang_id(0), [1] * 8)
        self.assertEqual(wang_id(15), [2] * 8)
        # Mask 5: north and south are B, east and west are A.
        # Corners take the next edge clockwise (NW north, NE
        # east, SE south, SW west), in Tiled's order (top,
        # top-right, right, bottom-right, bottom, bottom-left,
        # left, top-left).
        self.assertEqual(wang_id(5), [2, 1, 1, 2, 2, 1, 1, 2])
        self.assertEqual(wang_id(1), [2, 1, 1, 1, 1, 1, 1, 2])

    def test_center_terrain(self):
        self.assertEqual(wang_center_terrain(0), 0)   # all A
        self.assertEqual(wang_center_terrain(15), 1)  # all B
        self.assertEqual(wang_center_terrain(1), 0)   # mostly A
        self.assertEqual(wang_center_terrain(7), 1)   # mostly B
        # Mask 5 (north + south B): all four quadrants split,
        # so no whole quadrant decides — the tie goes to A.
        self.assertEqual(wang_center_terrain(5), 0)


class BlobTests(unittest.TestCase):
    def setUp(self):
        self.base = quadrant_base()
        self.tiles = blob_tiles(self.base)

    def test_forty_seven_valid_masks(self):
        self.assertEqual(len(BLOB_MASKS), 47)
        self.assertEqual(BLOB_MASKS, sorted(BLOB_MASKS))
        self.assertEqual(BLOB_MASKS[0], 0)
        self.assertEqual(BLOB_MASKS[-1], 255)
        # Every mask in the list is valid by the corner rule,
        # checked independently of the module's own test: a
        # corner bit is set only when both adjacent edge bits
        # are set. Bits, clockwise from north: N=0, NE=1, E=2,
        # SE=3, S=4, SW=5, W=6, NW=7.
        for mask in BLOB_MASKS:
            for corner, first, second in ((1, 0, 2), (3, 4, 2),
                                          (5, 4, 6), (7, 0, 6)):
                if mask & (1 << corner):
                    self.assertTrue(mask & (1 << first),
                                    "mask %d" % mask)
                    self.assertTrue(mask & (1 << second),
                                    "mask %d" % mask)

    def test_forty_seven_tiles(self):
        self.assertEqual(len(self.tiles), 47)
        for tile in self.tiles:
            self.assertEqual((tile.width(), tile.height()),
                             (16, 16))

    def test_mask_255_is_base_unchanged(self):
        self.assertEqual(self.tiles[BLOB_MASK_TO_INDEX[255]],
                         self.base)

    def test_mask_0_fully_transparent(self):
        tile = self.tiles[BLOB_MASK_TO_INDEX[0]]
        for y in range(16):
            for x in range(16):
                self.assertEqual(tile.pixel(x, y), TRANSPARENT)

    def test_corner_only_mask_is_impossible(self):
        # A corner neighbor with neither adjacent edge neighbor
        # can never show, so no tile exists for it.
        for mask in (2, 8, 32, 128, 42, 170):
            self.assertNotIn(mask, BLOB_MASKS,
                             "mask %d should not exist" % mask)

    def test_quadrants_match_mask(self):
        # Mask 1 (north neighbor only): the top quadrants show
        # their top halves, the bottom quadrants are transparent.
        north = self.tiles[BLOB_MASK_TO_INDEX[1]]
        self.assertEqual(north.pixel(4, 2), RED)
        self.assertEqual(north.pixel(12, 2), GREEN)
        self.assertEqual(north.pixel(4, 6), TRANSPARENT)
        self.assertEqual(north.pixel(12, 6), TRANSPARENT)
        self.assertEqual(north.pixel(4, 12), TRANSPARENT)
        self.assertEqual(north.pixel(12, 12), TRANSPARENT)
        # Mask 7 (north, north-east, east): the NE quadrant is
        # whole, the NW quadrant its top half, the SE quadrant
        # its right half, the SW quadrant transparent.
        corner = self.tiles[BLOB_MASK_TO_INDEX[7]]
        self.assertEqual(corner.pixel(12, 2), GREEN)
        self.assertEqual(corner.pixel(12, 6), GREEN)
        self.assertEqual(corner.pixel(4, 2), RED)
        self.assertEqual(corner.pixel(4, 6), TRANSPARENT)
        self.assertEqual(corner.pixel(14, 12), YELLOW)
        self.assertEqual(corner.pixel(10, 12), TRANSPARENT)
        self.assertEqual(corner.pixel(4, 12), TRANSPARENT)
        # Mask 85 (all four edges, no corners): every quadrant
        # is cut with a diagonal — the outer corner is
        # transparent, the center side shows the base tile.
        inner = self.tiles[BLOB_MASK_TO_INDEX[85]]
        self.assertEqual(inner.pixel(0, 0), TRANSPARENT)
        self.assertEqual(inner.pixel(7, 7), RED)
        self.assertEqual(inner.pixel(15, 0), TRANSPARENT)
        self.assertEqual(inner.pixel(8, 7), GREEN)
        self.assertEqual(inner.pixel(0, 15), TRANSPARENT)
        self.assertEqual(inner.pixel(7, 8), BLUE)
        self.assertEqual(inner.pixel(15, 15), TRANSPARENT)
        self.assertEqual(inner.pixel(8, 8), YELLOW)

    def test_blob_id(self):
        self.assertEqual(blob_id(255), [1] * 8)
        self.assertEqual(blob_id(0), [0] * 8)
        self.assertEqual(blob_id(1), [1, 0, 0, 0, 0, 0, 0, 0])
        # Mask 124 (east, south-east, south, south-west, west).
        self.assertEqual(blob_id(124), [0, 0, 1, 1, 1, 1, 1, 0])

    def test_empty_base_refuses(self):
        with self.assertRaises(PixelForgeError):
            blob_tiles(Image(0, 0))


class MapTests(unittest.TestCase):
    def test_autotile_3x3_block(self):
        grid = [[True] * 3 for _ in range(3)]
        result = autotile_map(grid)
        # Masks, clockwise bits (N=1, NE=2, E=4, SE=8, S=16,
        # SW=32, W=64, NW=128): top-left 28, top-middle 124,
        # top-right 112, middle-left 31, center 255,
        # middle-right 241, bottom-left 7, bottom-middle 199,
        # bottom-right 193.
        expected = [[28, 124, 112],
                    [31, 255, 241],
                    [7, 199, 193]]
        for y in range(3):
            for x in range(3):
                self.assertEqual(
                    result[y][x], BLOB_MASK_TO_INDEX[expected[y][x]],
                    "cell (%d, %d)" % (x, y))

    def test_autotile_isolated_cell(self):
        grid = [[False, False, False],
                [False, True, False],
                [False, False, False]]
        result = autotile_map(grid)
        self.assertEqual(result[1][1], BLOB_MASK_TO_INDEX[0])
        self.assertIsNone(result[0][0])
        self.assertIsNone(result[2][2])

    def test_autotile_empty_cells_are_none(self):
        grid = [[True, False],
                [False, True]]
        result = autotile_map(grid)
        self.assertIsNone(result[0][1])
        self.assertIsNone(result[1][0])

    def test_autotile_diagonal_only_neighbor_pruned(self):
        # Two cells touching only diagonally: each sees only a
        # corner neighbor, which needs both adjacent edges to
        # show — so both prune to mask 0.
        grid = [[True, False],
                [False, True]]
        result = autotile_map(grid)
        self.assertEqual(result[0][0], BLOB_MASK_TO_INDEX[0])
        self.assertEqual(result[1][1], BLOB_MASK_TO_INDEX[0])

    def test_autotile_out_of_bounds_absent(self):
        self.assertEqual(autotile_map([[True]]),
                         [[BLOB_MASK_TO_INDEX[0]]])
        self.assertEqual(autotile_map([]), [])

    def test_autotile_ragged_grid_refuses(self):
        with self.assertRaises(PixelForgeError):
            autotile_map([[True], [True, True]])

    def test_wang_map(self):
        # One B cell in an A field: the B cell itself sees all-A
        # neighbors (index 0); the cell north of it sees a B
        # neighbor to its south (bit 2, index 4).
        grid = [[0, 0, 0],
                [0, 1, 0],
                [0, 0, 0]]
        result = wang_map(grid)
        self.assertEqual(result[1][1], 0)
        self.assertEqual(result[0][1], 4)
        self.assertEqual(result[2][1], 1)
        self.assertEqual(result[1][0], 2)
        self.assertEqual(result[1][2], 8)
        # A solid B field: every cell sees B on all sides.
        self.assertEqual(wang_map([[1, 1, 1]] * 3)[1][1], 15)

    def test_wang_map_out_of_bounds_is_a(self):
        self.assertEqual(wang_map([[1]]), [[0]])
        self.assertEqual(wang_map([]), [])

    def test_demo_cells_blob(self):
        cells = demo_cells("blob", 10)
        self.assertEqual(len(cells), 10)
        self.assertEqual(len(cells[0]), 10)
        # Outside the rectangle: empty. Inside: tiles.
        self.assertEqual(cells[0][0], 0)
        self.assertEqual(cells[9][9], 0)
        self.assertEqual(cells[5][5], BLOB_MASK_TO_INDEX[255] + 1)
        self.assertEqual(cells[3][3], BLOB_MASK_TO_INDEX[28] + 1)
        self.assertEqual(cells[3][6], BLOB_MASK_TO_INDEX[112] + 1)
        self.assertEqual(cells[6][6], BLOB_MASK_TO_INDEX[193] + 1)

    def test_demo_cells_wang(self):
        cells = demo_cells("wang", 10)
        # The whole map is filled: no empty cells.
        self.assertTrue(all(gid != 0 for row in cells for gid in row))
        self.assertEqual(cells[0][0], 1)   # all-A corner
        self.assertEqual(cells[5][5], 16)  # all-B interior

    def test_demo_cells_small_size_refuses(self):
        with self.assertRaises(PixelForgeError):
            demo_cells("blob", 2)
        with self.assertRaises(PixelForgeError):
            demo_cells("wang", 1)

    def test_demo_cells_unknown_mode_refuses(self):
        with self.assertRaises(PixelForgeError):
            demo_cells("sideways", 10)


class TsxTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tile_a = make_solid_image(16, 16, 255, 0, 0)
        self.tile_b = make_solid_image(16, 16, 0, 0, 255)

    def _read_tsx(self, path):
        return ET.parse(path).getroot()

    def test_wang_tsx_parses_back(self):
        tiles = wang_tiles(self.tile_a, self.tile_b)
        path = os.path.join(self.dir, "tiles.tsx")
        write_tsx(path, "tiles.png", tiles, 4, "wang")
        root = self._read_tsx(path)
        self.assertEqual(root.tag, "tileset")
        self.assertEqual(root.get("tilewidth"), "16")
        self.assertEqual(root.get("tileheight"), "16")
        self.assertEqual(root.get("tilecount"), "16")
        self.assertEqual(root.get("columns"), "4")
        image = root.find("image")
        self.assertEqual(image.get("source"), "tiles.png")
        self.assertEqual(image.get("width"), "64")
        self.assertEqual(image.get("height"), "64")
        wangset = root.find("wangsets/wangset")
        self.assertIsNotNone(wangset)
        self.assertEqual(wangset.get("name"), "Terrain")
        self.assertEqual(wangset.get("type"), "mixed")
        colors = wangset.findall("wangcolor")
        self.assertEqual([c.get("name") for c in colors],
                         ["Terrain A", "Terrain B"])
        wangtiles = wangset.findall("wangtile")
        self.assertEqual(len(wangtiles), 16)
        for index, element in enumerate(wangtiles):
            self.assertEqual(element.get("tileid"), str(index))
            self.assertEqual(
                element.get("wangid"),
                ",".join(str(v) for v in wang_id(index)),
                "tile %d" % index)

    def test_blob_tsx_parses_back(self):
        tiles = blob_tiles(quadrant_base())
        path = os.path.join(self.dir, "tiles.tsx")
        write_tsx(path, "tiles.png", tiles, 7, "blob")
        root = self._read_tsx(path)
        self.assertEqual(root.get("tilecount"), "47")
        self.assertEqual(root.get("columns"), "7")
        image = root.find("image")
        self.assertEqual(image.get("width"), "112")
        self.assertEqual(image.get("height"), "112")
        wangset = root.find("wangsets/wangset")
        colors = wangset.findall("wangcolor")
        self.assertEqual([c.get("name") for c in colors],
                         ["Terrain"])
        wangtiles = wangset.findall("wangtile")
        self.assertEqual(len(wangtiles), 47)
        for index, element in enumerate(wangtiles):
            self.assertEqual(element.get("tileid"), str(index))
            self.assertEqual(
                element.get("wangid"),
                ",".join(str(v) for v in blob_id(BLOB_MASKS[index])),
                "tile %d" % index)

    def test_tsx_unknown_mode_refuses(self):
        with self.assertRaises(PixelForgeError):
            write_tsx(os.path.join(self.dir, "x.tsx"), "x.png",
                      [self.tile_a], 1, "sideways")


class TmxTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _read_tmx(self, path):
        root = ET.parse(path).getroot()
        layer = root.find("layer")
        data = layer.find("data")
        rows = [[int(v) for v in line.split(",")]
                for line in data.text.strip().split("\n")]
        return root, layer, rows

    def test_blob_tmx_parses_back(self):
        cells = demo_cells("blob", 10)
        path = os.path.join(self.dir, "demo.tmx")
        write_tmx(path, "tiles.tsx", 16, 16, cells)
        root, layer, rows = self._read_tmx(path)
        self.assertEqual(root.tag, "map")
        self.assertEqual(root.get("width"), "10")
        self.assertEqual(root.get("height"), "10")
        self.assertEqual(root.get("tilewidth"), "16")
        self.assertEqual(root.get("tileheight"), "16")
        tileset = root.find("tileset")
        self.assertEqual(tileset.get("firstgid"), "1")
        self.assertEqual(tileset.get("source"), "tiles.tsx")
        self.assertEqual(layer.get("name"), "Terrain")
        self.assertEqual(layer.find("data").get("encoding"), "csv")
        self.assertEqual(rows, cells)
        # The rectangle's interior is the all-neighbors tile,
        # its border the right edge tiles.
        self.assertEqual(rows[5][5], BLOB_MASK_TO_INDEX[255] + 1)
        self.assertEqual(rows[3][3], BLOB_MASK_TO_INDEX[28] + 1)
        self.assertEqual(rows[0][0], 0)

    def test_wang_tmx_parses_back(self):
        cells = demo_cells("wang", 10)
        path = os.path.join(self.dir, "demo.tmx")
        write_tmx(path, "tiles.tsx", 16, 16, cells)
        _root, _layer, rows = self._read_tmx(path)
        self.assertEqual(rows, cells)
        self.assertTrue(all(gid != 0 for row in rows for gid in row))

    def test_tmx_ragged_cells_refuse(self):
        with self.assertRaises(PixelForgeError):
            write_tmx(os.path.join(self.dir, "x.tmx"), "x.tsx",
                      16, 16, [[1, 2], [3]])

    def test_tmx_empty_refuses(self):
        with self.assertRaises(PixelForgeError):
            write_tmx(os.path.join(self.dir, "x.tmx"), "x.tsx",
                      16, 16, [])


def _brackets_balanced(text):
    """Every [, (, { has a matching closer, in order."""
    pairs = {"[": "]", "(": ")", "{": "}"}
    stack = []
    for char in text:
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return False
    return not stack


class TresTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tile_a = make_solid_image(16, 16, 255, 0, 0)
        self.tile_b = make_solid_image(16, 16, 0, 0, 255)

    def _tile_lines(self, text, col, row):
        """The .tres lines for one atlas cell, as a dict of
        property path -> value."""
        prefix = "%d:%d/0" % (col, row)
        found = {}
        for line in text.split("\n"):
            if line.startswith(prefix + "/"):
                key, _, value = line.partition(" = ")
                found[key[len(prefix) + 1:]] = value
            elif line == "%s = 0" % prefix:
                found[""] = "0"
        return found

    def test_wang_tres_text(self):
        tiles = wang_tiles(self.tile_a, self.tile_b)
        path = os.path.join(self.dir, "tiles.tres")
        write_tres(path, "tiles.png", tiles, 4, "wang")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(
            text.startswith('[gd_resource type="TileSet" format=3]'))
        self.assertIn(
            '[ext_resource type="Texture2D" '
            'path="res://tiles.png" id="1"]', text)
        self.assertIn("texture_region_size = Vector2i(16, 16)", text)
        self.assertIn("tile_size = Vector2i(16, 16)", text)
        self.assertIn('[sub_resource type="TileSetTerrainSet"',
                      text)
        self.assertIn("mode = 0", text)
        self.assertIn('"name": "Terrain A"', text)
        self.assertIn('"name": "Terrain B"', text)
        self.assertIn('terrain_sets = Array[TileSetTerrainSet]'
                      '([SubResource("TileSetTerrainSet_1")])', text)
        self.assertIn('sources/0 = '
                      'SubResource("TileSetAtlasSource_1")', text)
        # One tile declaration per tile, in row-major atlas order.
        declarations = [line for line in text.split("\n")
                        if line.endswith("/0 = 0")
                        and "/" not in line.split("/0 = 0")[0][2:]]
        self.assertEqual(len(declarations), 16)
        # Tile 0 (all A): center terrain 0, every peering bit 0.
        tile0 = self._tile_lines(text, 0, 0)
        self.assertEqual(tile0[""], "0")
        self.assertEqual(tile0["terrain_set"], "0")
        self.assertEqual(tile0["terrain"], "0")
        for name in ("top_side", "top_right_corner", "right_side",
                     "bottom_right_corner", "bottom_side",
                     "bottom_left_corner", "left_side",
                     "top_left_corner"):
            self.assertEqual(tile0["terrains_peering_bit/" + name],
                             "0", name)
        # Tile 15 (all B, atlas 3:3): center 1, every bit 1.
        tile15 = self._tile_lines(text, 3, 3)
        self.assertEqual(tile15["terrain"], "1")
        for name in ("top_side", "right_side", "bottom_side",
                     "left_side"):
            self.assertEqual(tile15["terrains_peering_bit/" + name],
                             "1", name)
        # Tile 1 (north edge B, atlas 1:0): the top side and the
        # top-left corner are B, the rest A.
        tile1 = self._tile_lines(text, 1, 0)
        self.assertEqual(tile1["terrain"], "0")
        self.assertEqual(tile1["terrains_peering_bit/top_side"], "1")
        self.assertEqual(
            tile1["terrains_peering_bit/top_left_corner"], "1")
        self.assertEqual(tile1["terrains_peering_bit/left_side"],
                         "0")
        self.assertEqual(tile1["terrains_peering_bit/right_side"],
                         "0")
        self.assertTrue(_brackets_balanced(text))

    def test_blob_tres_text(self):
        tiles = blob_tiles(quadrant_base())
        path = os.path.join(self.dir, "tiles.tres")
        write_tres(path, "tiles.png", tiles, 7, "blob")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(
            text.startswith('[gd_resource type="TileSet" format=3]'))
        self.assertIn('"name": "Terrain"', text)
        self.assertNotIn("Terrain A", text)
        declarations = [line for line in text.split("\n")
                        if line.endswith("/0 = 0")
                        and "/" not in line.split("/0 = 0")[0][2:]]
        self.assertEqual(len(declarations), 47)
        # Tile 0 (mask 0, atlas 0:0): no terrain anywhere —
        # center -1, every peering bit -1.
        tile0 = self._tile_lines(text, 0, 0)
        self.assertEqual(tile0["terrain"], "-1")
        for name in ("top_side", "top_right_corner", "right_side",
                     "bottom_right_corner", "bottom_side",
                     "bottom_left_corner", "left_side",
                     "top_left_corner"):
            self.assertEqual(tile0["terrains_peering_bit/" + name],
                             "-1", name)
        # Tile 46 (mask 255, atlas 4:6): center 0, every bit 0.
        tile46 = self._tile_lines(text, 4, 6)
        self.assertEqual(tile46["terrain"], "0")
        for name in ("top_side", "top_right_corner", "right_side",
                     "bottom_right_corner", "bottom_side",
                     "bottom_left_corner", "left_side",
                     "top_left_corner"):
            self.assertEqual(tile46["terrains_peering_bit/" + name],
                             "0", name)
        # Tile 1 (mask 1, north neighbor only, atlas 1:0): the
        # top side is terrain, the rest are not.
        tile1 = self._tile_lines(text, 1, 0)
        self.assertEqual(tile1["terrain"], "0")
        self.assertEqual(tile1["terrains_peering_bit/top_side"], "0")
        self.assertEqual(tile1["terrains_peering_bit/left_side"],
                         "-1")
        self.assertEqual(
            tile1["terrains_peering_bit/top_right_corner"], "-1")
        self.assertTrue(_brackets_balanced(text))

    def test_tres_unknown_mode_refuses(self):
        with self.assertRaises(PixelForgeError):
            write_tres(os.path.join(self.dir, "x.tres"), "x.png",
                       [self.tile_a], 1, "sideways")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tile_a = make_solid_image(16, 16, 255, 0, 0)
        self.tile_b = make_solid_image(16, 16, 0, 0, 255)
        self.wang = wang_tiles(self.tile_a, self.tile_b)
        self.blob = blob_tiles(quadrant_base())

    def test_export_all_writes_four_files(self):
        out = os.path.join(self.dir, "out")
        files = export_tileset("wang", self.wang, out, "tiles")
        names = [path.name for path in files]
        self.assertEqual(names, ["tiles.png", "tiles.tsx",
                                 "tiles-demo.tmx", "tiles.tres"])
        for path in files:
            self.assertTrue(path.is_file(), str(path))
        # The PNG reads back as the packed sheet, tiles in index
        # order: tile 5 sits at column 1, row 1.
        sheet = load_image(str(files[0]))
        self.assertEqual((sheet.width(), sheet.height()), (64, 64))
        self.assertEqual(sheet.pixel(16, 16),
                         self.wang[5].pixel(0, 0))
        self.assertEqual(sheet.pixel(31, 31),
                         self.wang[5].pixel(15, 15))

    def test_export_png_only(self):
        files = export_tileset("blob", self.blob,
                               os.path.join(self.dir, "out"),
                               "tiles", export="png")
        self.assertEqual([path.name for path in files],
                         ["tiles.png"])

    def test_export_tsx(self):
        files = export_tileset("blob", self.blob,
                               os.path.join(self.dir, "out"),
                               "tiles", export="tsx")
        self.assertEqual([path.name for path in files],
                         ["tiles.png", "tiles.tsx"])

    def test_export_tmx_brings_tsx(self):
        files = export_tileset("wang", self.wang,
                               os.path.join(self.dir, "out"),
                               "tiles", export="tmx")
        self.assertEqual([path.name for path in files],
                         ["tiles.png", "tiles.tsx",
                          "tiles-demo.tmx"])

    def test_export_tres(self):
        files = export_tileset("wang", self.wang,
                               os.path.join(self.dir, "out"),
                               "tiles", export="tres")
        self.assertEqual([path.name for path in files],
                         ["tiles.png", "tiles.tres"])

    def test_export_creates_nested_folder(self):
        out = os.path.join(self.dir, "deep", "deeper")
        files = export_tileset("wang", self.wang, out, "tiles",
                               export="png")
        self.assertTrue(files[0].is_file())

    def test_export_unknown_mode_and_export_refuse(self):
        out = os.path.join(self.dir, "out")
        with self.assertRaises(PixelForgeError):
            export_tileset("sideways", self.wang, out, "tiles")
        with self.assertRaises(PixelForgeError):
            export_tileset("wang", self.wang, out, "tiles",
                           export="sideways")

    def test_export_empty_tiles_refuses(self):
        with self.assertRaises(PixelForgeError):
            export_tileset("wang", [], os.path.join(self.dir, "out"),
                           "tiles")


if __name__ == "__main__":
    unittest.main()
