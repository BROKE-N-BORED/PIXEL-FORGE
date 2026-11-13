"""Atlas packing tests — MaxRects placement, trim, padding,
extrude, multipack, dedupe, pivots, 9-slice, and the atlas
metadata format (task 38).

Qt-free (task 64): runs without PySide6.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import make_solid_image, write_image  # noqa: E402
from pixel_forge.atlas import (  # noqa: E402
    DEFAULT_MAX_SIZE, DEFAULT_PADDING, FORMAT_NAME, FORMAT_VERSION,
    PACKER_NAME, build_atlas_metadata, pack_atlas, trim_transparent,
    write_atlas)
from pixel_forge.image import Image, argb  # noqa: E402
from pixel_forge.pixelize import PixelForgeError  # noqa: E402

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

RED = argb(255, 0, 0)
GREEN = argb(0, 255, 0)
BLUE = argb(0, 0, 255)
TRANSPARENT = 0


def rects_overlap(a, b):
    """Two frame dicts' rects overlap, or not. Touching edges
    do not count."""
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


def assert_no_overlaps(test, frames):
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            if frames[i]["sheet"] != frames[j]["sheet"]:
                continue
            test.assertFalse(
                rects_overlap(frames[i], frames[j]),
                "%s overlaps %s" % (frames[i]["name"],
                                    frames[j]["name"]))


def assert_placed_once(test, result, names):
    """Every input name appears exactly once across frames and
    aliases — nothing dropped, nothing placed twice."""
    seen = []
    for frame in result["frames"]:
        seen.append(frame["name"])
        seen.extend(frame["aliases"])
    test.assertEqual(sorted(seen), sorted(names))


class TrimTests(unittest.TestCase):
    def test_trims_to_content_box(self):
        image = Image(16, 16)
        for y in range(4, 10):
            for x in range(3, 9):
                image.setPixel(x, y, RED)
        trimmed, left, top, right, bottom = trim_transparent(image)
        self.assertEqual((trimmed.width(), trimmed.height()),
                         (6, 6))
        self.assertEqual((left, top, right, bottom), (3, 4, 7, 6))
        # The insets add up: left + width + right is the source.
        self.assertEqual(left + trimmed.width() + right, 16)
        self.assertEqual(top + trimmed.height() + bottom, 16)
        self.assertEqual(trimmed.pixel(0, 0), RED)

    def test_fully_transparent_becomes_one_by_one(self):
        trimmed, left, top, right, bottom = trim_transparent(
            Image(8, 8))
        self.assertEqual((trimmed.width(), trimmed.height()),
                         (1, 1))
        self.assertEqual(trimmed.pixel(0, 0), TRANSPARENT)
        self.assertEqual((left, top, right, bottom), (0, 0, 7, 7))

    def test_fully_transparent_keeps_its_placement(self):
        # The frame count never changes silently: a fully
        # transparent sprite still gets a frame entry.
        sprites = [("solid", make_solid_image(8, 8, 1, 2, 3)),
                   ("empty", Image(8, 8))]
        result = pack_atlas(sprites, max_size=64, padding=0,
                            trim=True)
        self.assertEqual(len(result["frames"]), 2)
        empty = [f for f in result["frames"]
                 if f["name"] == "empty"][0]
        self.assertEqual((empty["w"], empty["h"]), (1, 1))
        self.assertEqual((empty["source_w"], empty["source_h"]),
                         (8, 8))

    def test_opaque_image_trims_to_itself(self):
        image = make_solid_image(8, 8, 9, 9, 9)
        trimmed, left, top, right, bottom = trim_transparent(image)
        self.assertEqual((trimmed.width(), trimmed.height()),
                         (8, 8))
        self.assertEqual((left, top, right, bottom), (0, 0, 0, 0))

    def test_trim_records_insets_in_metadata(self):
        image = Image(16, 16)
        for y in range(4, 10):
            for x in range(3, 9):
                image.setPixel(x, y, RED)
        result = pack_atlas([("cut", image)], max_size=64,
                            padding=0, trim=True)
        frame = result["frames"][0]
        self.assertEqual((frame["w"], frame["h"]), (6, 6))
        self.assertEqual(frame["trimmed"],
                         {"left": 3, "top": 4,
                          "right": 7, "bottom": 6})
        self.assertEqual((frame["source_w"], frame["source_h"]),
                         (16, 16))


class MaxRectsTests(unittest.TestCase):
    def test_known_case_fits_sixty_four(self):
        # Four 16 x 16 + one 32 x 32, no padding: the 32 x 32
        # takes the left half, the four 16 x 16 fill the right
        # half — 64 x 32 exactly.
        sprites = [("big", make_solid_image(32, 32, 255, 0, 0))]
        sprites += [("s%d" % i,
                     make_solid_image(16, 16, 0, i * 40, 0))
                    for i in range(4)]
        result = pack_atlas(sprites, max_size=64, padding=0)
        self.assertEqual(result["sheet_count"], 1)
        sheet = result["sheets"][0]
        self.assertEqual((sheet.width(), sheet.height()), (64, 32))
        assert_no_overlaps(self, result["frames"])
        assert_placed_once(self, result,
                           [name for name, _ in sprites])
        # The pixels land where the metadata says: the big
        # sprite is red at (0, 0), each small one its own
        # green at its own rect.
        for frame in result["frames"]:
            self.assertEqual(
                sheet.pixel(frame["x"], frame["y"]),
                RED if frame["name"] == "big"
                else argb(0, int(frame["name"][1]) * 40, 0))

    def test_mixed_sizes_never_overlap(self):
        # A scatter of sizes — the packer's one hard promise.
        sizes = [(64, 64), (48, 32), (32, 48), (16, 16),
                 (24, 24), (40, 8), (8, 40), (12, 20), (20, 12),
                 (32, 32), (10, 10), (56, 16)]
        sprites = [("s%d" % i, make_solid_image(
            w, h, (i * 37) % 256, (i * 91) % 256, (i * 53) % 256))
            for i, (w, h) in enumerate(sizes)]
        result = pack_atlas(sprites, max_size=256, padding=1)
        self.assertEqual(result["sheet_count"], 1)
        assert_no_overlaps(self, result["frames"])
        assert_placed_once(self, result,
                           [name for name, _ in sprites])
        sheet = result["sheets"][0]
        self.assertLessEqual(sheet.width(), 256)
        self.assertLessEqual(sheet.height(), 256)

    def test_frames_stay_in_input_order(self):
        # The packer places largest first, but the frames list
        # keeps input order — animation frames stay in playback
        # order.
        sprites = [("tiny", make_solid_image(8, 8, 1, 1, 1)),
                   ("huge", make_solid_image(64, 64, 2, 2, 2)),
                   ("mid", make_solid_image(32, 32, 3, 3, 3))]
        result = pack_atlas(sprites, max_size=256, padding=0)
        self.assertEqual([f["name"] for f in result["frames"]],
                         ["tiny", "huge", "mid"])

    def test_oversize_sprite_refuses_naming_sprite_and_cap(self):
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas([("huge", make_solid_image(2048, 100, 0, 0, 0))],
                       max_size=1024, padding=0)
        message = str(caught.exception)
        self.assertIn("huge", message)
        self.assertIn("2048 x 100", message)
        self.assertIn("1024 x 1024", message)

    def test_padding_counts_toward_the_cap(self):
        # A 64 x 64 sprite with 1 px padding is a 66 x 66 cell —
        # it does not fit a 64 x 64 cap.
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas([("edge", make_solid_image(64, 64, 1, 1, 1))],
                       max_size=64, padding=1)
        self.assertIn("edge", str(caught.exception))

    def test_duplicate_names_refuse(self):
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas([("same", make_solid_image(8, 8, 1, 1, 1)),
                        ("same", make_solid_image(8, 8, 2, 2, 2))],
                       max_size=64, padding=0)
        self.assertIn("same", str(caught.exception))

    def test_no_sprites_refuses(self):
        with self.assertRaises(PixelForgeError):
            pack_atlas([], max_size=64, padding=0)

    def test_bad_settings_refuse(self):
        sprite = [("one", make_solid_image(8, 8, 1, 1, 1))]
        for kwargs in ({"max_size": 32}, {"max_size": 8192},
                       {"padding": -1}, {"padding": 65},
                       {"extrude": -1}, {"extrude": 17}):
            with self.assertRaises(PixelForgeError, msg=kwargs):
                pack_atlas(sprite, **kwargs)

    def test_extrude_past_padding_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas([("one", make_solid_image(8, 8, 1, 1, 1))],
                       max_size=64, padding=1, extrude=2)
        message = str(caught.exception)
        self.assertIn("2", message)
        self.assertIn("1", message)


class PaddingExtrudeTests(unittest.TestCase):
    def test_placements_are_at_least_padding_apart(self):
        # The packer keeps a padding ring around every sprite,
        # so the cell rects (sprite + ring) never overlap — and
        # the sprite rects are at least `padding` apart wherever
        # they face each other.
        sprites = [("a", make_solid_image(16, 16, 255, 0, 0)),
                   ("b", make_solid_image(16, 16, 0, 255, 0)),
                   ("c", make_solid_image(16, 16, 0, 0, 255)),
                   ("d", make_solid_image(16, 16, 9, 9, 9))]
        padding = 3
        result = pack_atlas(sprites, max_size=128, padding=padding)
        frames = result["frames"]
        cells = [(f["x"] - padding, f["y"] - padding,
                  f["w"] + 2 * padding, f["h"] + 2 * padding)
                 for f in frames]
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                a, b = cells[i], cells[j]
                self.assertFalse(
                    a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
                    and a[1] < b[1] + b[3] and b[1] < a[1] + a[3],
                    "cells %d and %d overlap" % (i, j))
        # And the sprite rects themselves: wherever two share
        # an x-range, their y-gap is at least the padding, and
        # wherever they share a y-range, their x-gap is too.
        for i in range(len(frames)):
            for j in range(i + 1, len(frames)):
                a, b = frames[i], frames[j]
                x_overlap = (a["x"] < b["x"] + b["w"]
                             and b["x"] < a["x"] + a["w"])
                y_overlap = (a["y"] < b["y"] + b["h"]
                             and b["y"] < a["y"] + a["h"])
                if x_overlap and not y_overlap:
                    gap = (b["y"] - (a["y"] + a["h"])
                           if b["y"] >= a["y"]
                           else a["y"] - (b["y"] + b["h"]))
                    self.assertGreaterEqual(gap, padding)
                if y_overlap and not x_overlap:
                    gap = (b["x"] - (a["x"] + a["w"])
                           if b["x"] >= a["x"]
                           else a["x"] - (b["x"] + b["w"]))
                    self.assertGreaterEqual(gap, padding)

    def test_extrude_repeats_edge_pixels(self):
        # One 4 x 4 sprite, padding 2, extrude 2: the whole
        # 8 x 8 sheet is the sprite's color.
        result = pack_atlas(
            [("one", make_solid_image(4, 4, 255, 0, 0))],
            max_size=64, padding=2, extrude=2)
        sheet = result["sheets"][0]
        self.assertEqual((sheet.width(), sheet.height()), (8, 8))
        for y in range(8):
            for x in range(8):
                self.assertEqual(sheet.pixel(x, y), RED, (x, y))

    def test_extrude_stops_at_its_amount(self):
        # Padding 3, extrude 1: the ring 1 px out is the edge
        # color, the ring 2 px out is still transparent.
        result = pack_atlas(
            [("one", make_solid_image(4, 4, 0, 0, 255))],
            max_size=64, padding=3, extrude=1)
        sheet = result["sheets"][0]
        frame = result["frames"][0]
        self.assertEqual((sheet.width(), sheet.height()), (10, 10))
        self.assertEqual(sheet.pixel(frame["x"] - 1, frame["y"]),
                         BLUE)
        self.assertEqual(sheet.pixel(frame["x"] - 2, frame["y"]),
                         TRANSPARENT)
        self.assertEqual(sheet.pixel(frame["x"],
                                     frame["y"] - 1), BLUE)
        self.assertEqual(sheet.pixel(frame["x"],
                                     frame["y"] - 2), TRANSPARENT)
        # Corners get the corner pixel.
        self.assertEqual(sheet.pixel(frame["x"] - 1,
                                     frame["y"] - 1), BLUE)

    def test_extrude_never_covers_another_sprite(self):
        # Two sprites side by side with extrude: each sprite's
        # own pixels are untouched, and the gap between them
        # holds extruded edge pixels, not the other sprite.
        sprites = [("red", make_solid_image(8, 8, 255, 0, 0)),
                   ("green", make_solid_image(8, 8, 0, 255, 0))]
        result = pack_atlas(sprites, max_size=64, padding=2,
                            extrude=2)
        sheet = result["sheets"][0]
        frames = {f["name"]: f for f in result["frames"]}
        red, green = frames["red"], frames["green"]
        for frame, color in ((red, RED), (green, GREEN)):
            for y in range(frame["y"], frame["y"] + 8):
                for x in range(frame["x"], frame["x"] + 8):
                    self.assertEqual(sheet.pixel(x, y), color,
                                     (x, y))
        # The padding ring around red is red, around green is
        # green — neither reaches the other sprite's rect.
        self.assertEqual(sheet.pixel(red["x"] - 1, red["y"]), RED)
        self.assertEqual(sheet.pixel(green["x"] - 1, green["y"]),
                         GREEN)


class MultipackDedupeTests(unittest.TestCase):
    def test_single_sheet_refuses_when_full(self):
        sprites = [("m%d" % i,
                    make_solid_image(32, 32, i * 20 % 256, 0, 0))
                   for i in range(10)]
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas(sprites, max_size=64, padding=0)
        message = str(caught.exception)
        self.assertIn("6 of 10", message)
        self.assertIn("64 x 64", message)
        self.assertIn("--multipack", message)

    def test_multipack_spills_and_places_each_once(self):
        sprites = [("m%d" % i,
                    make_solid_image(32, 32, i * 20 % 256, 0, 0))
                   for i in range(10)]
        result = pack_atlas(sprites, max_size=64, padding=0,
                            multipack=True)
        self.assertEqual(result["sheet_count"], 3)
        self.assertEqual(len(result["sheets"]), 3)
        assert_placed_once(self, result,
                           [name for name, _ in sprites])
        assert_no_overlaps(self, result["frames"])
        # Every sheet holds at most 4 (a 64 x 64 sheet fits
        # four 32 x 32 sprites).
        per_sheet = {}
        for frame in result["frames"]:
            per_sheet[frame["sheet"]] = \
                per_sheet.get(frame["sheet"], 0) + 1
        self.assertEqual(sorted(per_sheet.values()), [2, 4, 4])

    def test_identical_sprites_pack_once(self):
        sprites = [("a", make_solid_image(8, 8, 1, 2, 3)),
                   ("b", make_solid_image(8, 8, 9, 9, 9)),
                   ("a2", make_solid_image(8, 8, 1, 2, 3))]
        result = pack_atlas(sprites, max_size=64, padding=0)
        self.assertEqual(result["deduped"], 1)
        self.assertEqual(len(result["frames"]), 2)
        first = result["frames"][0]
        self.assertEqual(first["name"], "a")
        self.assertEqual(first["aliases"], ["a2"])
        self.assertEqual(result["frames"][1]["aliases"], [])

    def test_dedupe_uses_trimmed_pixels(self):
        # Two sprites that differ only in transparent borders
        # are the same sprite once trimmed.
        bordered = Image(16, 16)
        for y in range(4, 12):
            for x in range(4, 12):
                bordered.setPixel(x, y, RED)
        plain = Image(8, 8)
        plain.fill(RED)
        result = pack_atlas([("bordered", bordered),
                             ("plain", plain)],
                            max_size=64, padding=0, trim=True)
        self.assertEqual(result["deduped"], 1)
        self.assertEqual(result["frames"][0]["aliases"], ["plain"])

    def test_dedupe_off_without_trim(self):
        # Without trim the same two sprites are different sizes
        # and both pack.
        bordered = Image(16, 16)
        for y in range(4, 12):
            for x in range(4, 12):
                bordered.setPixel(x, y, RED)
        plain = Image(8, 8)
        plain.fill(RED)
        result = pack_atlas([("bordered", bordered),
                             ("plain", plain)],
                            max_size=64, padding=0, trim=False)
        self.assertEqual(result["deduped"], 0)
        self.assertEqual(len(result["frames"]), 2)


class PivotSliceTests(unittest.TestCase):
    def test_default_pivot_is_center_and_no_slice(self):
        result = pack_atlas(
            [("one", make_solid_image(8, 8, 1, 1, 1))],
            max_size=64, padding=0)
        frame = result["frames"][0]
        self.assertEqual(frame["pivot"], [0.5, 0.5])
        self.assertIsNone(frame["slice"])

    def test_pivots_and_slices_land_in_frames(self):
        sprites = [("a", make_solid_image(16, 16, 1, 1, 1)),
                   ("b", make_solid_image(16, 16, 2, 2, 2))]
        result = pack_atlas(
            sprites, max_size=64, padding=0,
            pivots={"a": (0, 1)},
            slices={"b": (2, 3, 2, 3)})
        frames = {f["name"]: f for f in result["frames"]}
        self.assertEqual(frames["a"]["pivot"], [0.0, 1.0])
        self.assertEqual(frames["a"]["slice"], None)
        self.assertEqual(frames["b"]["pivot"], [0.5, 0.5])
        self.assertEqual(frames["b"]["slice"], [2, 3, 2, 3])

    def test_bad_pivot_refuses_naming_the_sprite(self):
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas(
                [("a", make_solid_image(8, 8, 1, 1, 1))],
                max_size=64, padding=0, pivots={"a": (1.5, 0.5)})
        self.assertIn("a", str(caught.exception))
        with self.assertRaises(PixelForgeError):
            pack_atlas(
                [("a", make_solid_image(8, 8, 1, 1, 1))],
                max_size=64, padding=0, pivots={"a": (0.5,)})
        with self.assertRaises(PixelForgeError):
            pack_atlas(
                [("a", make_solid_image(8, 8, 1, 1, 1))],
                max_size=64, padding=0,
                pivots={"ghost": (0.5, 0.5)})

    def test_bad_slice_refuses(self):
        sprite = [("a", make_solid_image(8, 8, 1, 1, 1))]
        # Borders that leave no middle.
        with self.assertRaises(PixelForgeError) as caught:
            pack_atlas(sprite, max_size=64, padding=0,
                       slices={"a": (4, 0, 4, 0)})
        self.assertIn("a", str(caught.exception))
        # Negative, fractional, or the wrong count.
        for borders in ((-1, 0, 0, 0), (0.5, 0, 0, 0),
                        (1, 1, 1)):
            with self.assertRaises(PixelForgeError, msg=borders):
                pack_atlas(sprite, max_size=64, padding=0,
                           slices={"a": borders})


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_metadata_shape(self):
        sprites = [("a", make_solid_image(16, 16, 255, 0, 0)),
                   ("b", make_solid_image(8, 8, 0, 255, 0))]
        result = pack_atlas(sprites, max_size=128, padding=2,
                            extrude=1, trim=True)
        metadata = build_atlas_metadata(result, ["atlas.png"],
                                        fps=8)
        self.assertEqual(metadata["format"], FORMAT_NAME)
        self.assertEqual(metadata["version"], FORMAT_VERSION)
        self.assertEqual(metadata["packer"], PACKER_NAME)
        self.assertEqual(metadata["max_size"], 128)
        self.assertEqual(metadata["padding"], 2)
        self.assertEqual(metadata["extrude"], 1)
        self.assertTrue(metadata["trim"])
        self.assertEqual(metadata["deduped"], 0)
        self.assertEqual(metadata["fps"], 8)
        self.assertEqual(len(metadata["sheets"]), 1)
        self.assertEqual(metadata["sheets"][0]["file"], "atlas.png")
        self.assertEqual((metadata["sheets"][0]["width"],
                          metadata["sheets"][0]["height"]),
                         (result["sheets"][0].width(),
                          result["sheets"][0].height()))
        self.assertEqual(len(metadata["frames"]), 2)
        for frame in metadata["frames"]:
            for key in ("name", "index", "sheet", "x", "y", "w",
                        "h", "source_w", "source_h", "trimmed",
                        "pivot", "slice", "aliases",
                        "duration_ms"):
                self.assertIn(key, frame, frame)
            self.assertEqual(frame["duration_ms"], 125)

    def test_durations_follow_input_order_not_placement(self):
        # A duplicate mid-list must not shift the durations of
        # the sprites after it — the mapping goes through the
        # input names, never by walking placed frames.
        sprites = [("a", make_solid_image(8, 8, 1, 2, 3)),
                   ("b", make_solid_image(8, 8, 9, 9, 9)),
                   ("a2", make_solid_image(8, 8, 1, 2, 3))]
        result = pack_atlas(sprites, max_size=64, padding=0)
        metadata = build_atlas_metadata(
            result, ["atlas.png"], durations=[100, 200, 300])
        durations = {f["name"]: f["duration_ms"]
                     for f in metadata["frames"]}
        self.assertEqual(durations, {"a": 100, "b": 200})

    def test_tags_validated_and_stored(self):
        sprites = [("a", make_solid_image(8, 8, 1, 1, 1)),
                   ("b", make_solid_image(8, 8, 2, 2, 2))]
        result = pack_atlas(sprites, max_size=64, padding=0)
        tags = [{"name": "walk", "from": 0, "to": 1}]
        metadata = build_atlas_metadata(result, ["atlas.png"],
                                        fps=8, tags=tags)
        self.assertEqual(metadata["tags"], tags)
        with self.assertRaises(PixelForgeError):
            build_atlas_metadata(
                result, ["atlas.png"], fps=8,
                tags=[{"name": "bad", "from": 0, "to": 5}])
        # No tags: the key stays out, like the grid format.
        plain = build_atlas_metadata(result, ["atlas.png"], fps=8)
        self.assertNotIn("tags", plain)

    def test_write_atlas_single_sheet(self):
        images = [make_solid_image(16, 16, 255, 0, 0),
                  make_solid_image(8, 8, 0, 255, 0)]
        out_png = os.path.join(self.tmp, "atlas.png")
        out_json = os.path.join(self.tmp, "atlas.json")
        metadata, files = write_atlas(
            images, ["a.png", "b.png"], out_png, out_json,
            max_size=128, padding=1, fps=8)
        self.assertEqual(files, [out_png, out_json])
        self.assertTrue(os.path.isfile(out_png))
        self.assertTrue(os.path.isfile(out_json))
        with open(out_json) as handle:
            stored = json.load(handle)
        self.assertEqual(stored, metadata)
        self.assertEqual(stored["format"], FORMAT_NAME)
        self.assertEqual(stored["sheets"][0]["file"], "atlas.png")
        self.assertEqual(len(stored["frames"]), 2)

    def test_write_atlas_multipack_names_sheets(self):
        images = [make_solid_image(32, 32, i * 20 % 256, 0, 0)
                  for i in range(10)]
        names = ["m%d.png" % i for i in range(10)]
        out_png = os.path.join(self.tmp, "atlas.png")
        out_json = os.path.join(self.tmp, "atlas.json")
        metadata, files = write_atlas(
            images, names, out_png, out_json,
            max_size=64, padding=0, multipack=True, fps=8)
        self.assertEqual(metadata["sheets"][0]["file"],
                         "atlas_0.png")
        self.assertEqual(metadata["sheets"][1]["file"],
                         "atlas_1.png")
        self.assertEqual(metadata["sheets"][2]["file"],
                         "atlas_2.png")
        self.assertEqual(files, [
            os.path.join(self.tmp, "atlas_0.png"),
            os.path.join(self.tmp, "atlas_1.png"),
            os.path.join(self.tmp, "atlas_2.png"),
            out_json])
        for path in files:
            self.assertTrue(os.path.isfile(path), path)
        # The plain atlas.png is NOT written when multipacking.
        self.assertFalse(os.path.isfile(out_png))
        # Every sprite is on exactly one sheet.
        seen = [f["name"] for f in metadata["frames"]]
        seen += [a for f in metadata["frames"]
                 for a in f["aliases"]]
        self.assertEqual(sorted(seen), sorted(names))

    def test_write_atlas_round_trips_pixels(self):
        # The PNG holds the sprites at the metadata's rects.
        from pixel_forge.pixelize import load_image
        images = [make_solid_image(16, 16, 255, 0, 0),
                  make_solid_image(16, 16, 0, 255, 0)]
        out_png = os.path.join(self.tmp, "round.png")
        out_json = os.path.join(self.tmp, "round.json")
        metadata, _files = write_atlas(
            images, ["red.png", "green.png"], out_png, out_json,
            max_size=64, padding=0, fps=8)
        sheet = load_image(out_png)
        for frame, color in zip(metadata["frames"], (RED, GREEN)):
            self.assertEqual(
                sheet.pixel(frame["x"], frame["y"]), color)


if __name__ == "__main__":
    unittest.main()
