"""Spritesheet tests — layout geometry, packing pixels, metadata,
folder loading, and playback from the sheet. No fixtures needed:
small solid-color frames, each a distinct color so a wrong position
shows up as a wrong pixel.

Qt-free (task 64): runs without PySide6."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import make_solid_image, write_image  # noqa: E402
from pixel_forge.pixelize import PixelForgeError, load_image  # noqa: E402
from pixel_forge.spritesheet import (auto_columns, build_metadata,  # noqa: E402
                                     check_uniform, duration_for_fps,
                                     extract_frame, frames_from_sheet,
                                     layout_frames, load_frame_dir,
                                     load_metadata, pack_frames,
                                     validate_tags, write_sheet)

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

# Distinct frame colors: (r, g, b) per frame index.
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
          (255, 0, 255), (0, 255, 255), (128, 64, 32), (10, 20, 30),
          (200, 100, 50), (50, 200, 100)]


def make_frames(count, size=32):
    return [make_solid_image(size, size, *COLORS[i % len(COLORS)])
            for i in range(count)]


def pixel_rgb(image, x, y):
    px = image.pixel(x, y)
    return ((px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF,
            (px >> 24) & 0xFF)


class LayoutTests(unittest.TestCase):
    def test_auto_columns_squareish(self):
        self.assertEqual(auto_columns(1), 1)
        self.assertEqual(auto_columns(4), 2)
        self.assertEqual(auto_columns(5), 3)
        self.assertEqual(auto_columns(9), 3)
        self.assertEqual(auto_columns(10), 4)
        self.assertEqual(auto_columns(16), 4)

    def test_layout_exact_positions(self):
        # 5 frames of 32x32, 2 columns, no padding: 3 rows.
        layout = layout_frames(5, 32, 32, cols=2)
        self.assertEqual(layout["columns"], 2)
        self.assertEqual(layout["rows"], 3)
        self.assertEqual((layout["sheet_width"],
                          layout["sheet_height"]), (64, 96))
        self.assertEqual(layout["positions"],
                         [(0, 0), (32, 0), (0, 32), (32, 32),
                          (0, 64)])

    def test_layout_partial_last_row(self):
        # 7 frames, 3 columns: rows of 3, 3, 1 — the last row has
        # two empty cells, which stay transparent (see PackTests).
        layout = layout_frames(7, 16, 16, cols=3)
        self.assertEqual((layout["columns"], layout["rows"]), (3, 3))
        self.assertEqual((layout["sheet_width"],
                          layout["sheet_height"]), (48, 48))
        self.assertEqual(layout["positions"][-1], (0, 32))

    def test_layout_auto_columns(self):
        layout = layout_frames(10, 32, 32)
        self.assertEqual(layout["columns"], 4)
        self.assertEqual(layout["rows"], 3)
        self.assertEqual((layout["sheet_width"],
                          layout["sheet_height"]), (128, 96))

    def test_layout_padding_between_frames_only(self):
        # Padding is a gap BETWEEN frames: no outer border, so
        # frame 0 still starts at (0, 0) and the sheet grows by
        # (cols-1)*padding, not cols*padding.
        layout = layout_frames(4, 32, 32, cols=2, padding=4)
        self.assertEqual((layout["sheet_width"],
                          layout["sheet_height"]), (68, 68))
        self.assertEqual(layout["positions"],
                         [(0, 0), (36, 0), (0, 36), (36, 36)])

    def test_layout_single_frame(self):
        layout = layout_frames(1, 32, 32)
        self.assertEqual((layout["columns"], layout["rows"]), (1, 1))
        self.assertEqual((layout["sheet_width"],
                          layout["sheet_height"]), (32, 32))
        self.assertEqual(layout["positions"], [(0, 0)])

    def test_layout_invalid(self):
        for kwargs in ({"count": 0}, {"cols": 0},
                       {"padding": -1}, {"frame_w": 0}):
            base = {"count": 4, "frame_w": 32, "frame_h": 32}
            base.update(kwargs)
            with self.assertRaises(PixelForgeError, msg=kwargs):
                layout_frames(**base)

    def test_duration_from_fps(self):
        self.assertEqual(duration_for_fps(8), 125)
        self.assertEqual(duration_for_fps(24), 42)
        self.assertEqual(duration_for_fps(1), 1000)
        self.assertEqual(duration_for_fps(30), 33)
        for bad in (0, -1, None):
            with self.assertRaises(PixelForgeError, msg=bad):
                duration_for_fps(bad)


class PackTests(unittest.TestCase):
    def test_sheet_pixels_land_on_their_positions(self):
        frames = make_frames(4)
        sheet, layout = pack_frames(frames, cols=2)
        self.assertEqual((sheet.width(), sheet.height()), (64, 64))
        # Center of each cell is that frame's color.
        for index, (x, y) in enumerate(layout["positions"]):
            self.assertEqual(pixel_rgb(sheet, x + 16, y + 16),
                             COLORS[index] + (255,))

    def test_padding_gaps_are_transparent(self):
        frames = make_frames(2)
        sheet, _ = pack_frames(frames, cols=2, padding=4)
        # The 4 px gutter between the frames is transparent.
        self.assertEqual(pixel_rgb(sheet, 32, 16)[3], 0)
        self.assertEqual(pixel_rgb(sheet, 35, 16)[3], 0)
        # And the frames themselves are still opaque.
        self.assertEqual(pixel_rgb(sheet, 16, 16)[3], 255)
        self.assertEqual(pixel_rgb(sheet, 52, 16)[3], 255)

    def test_empty_cells_in_partial_row_are_transparent(self):
        frames = make_frames(5)
        sheet, layout = pack_frames(frames, cols=3)
        self.assertEqual((layout["columns"], layout["rows"]), (3, 2))
        # Row 2 has two frames (left, middle) and one empty cell.
        self.assertEqual(pixel_rgb(sheet, 16, 48)[3], 255)
        self.assertEqual(pixel_rgb(sheet, 48, 48)[3], 255)
        self.assertEqual(pixel_rgb(sheet, 80, 48)[3], 0)

    def test_single_frame_sheet(self):
        frames = make_frames(1)
        sheet, layout = pack_frames(frames)
        self.assertEqual((sheet.width(), sheet.height()), (32, 32))
        self.assertEqual(pixel_rgb(sheet, 16, 16), COLORS[0] + (255,))

    def test_empty_pack_fails(self):
        with self.assertRaises(PixelForgeError):
            pack_frames([])

    def test_mixed_sizes_fail_with_names(self):
        frames = make_frames(2)
        frames.append(make_solid_image(16, 16, 1, 2, 3))
        with self.assertRaises(PixelForgeError) as ctx:
            pack_frames(frames)
        self.assertIn("same size", str(ctx.exception))

    def test_check_uniform_names_the_odd_frame(self):
        frames = [("a.png", make_solid_image(32, 32, 1, 2, 3)),
                  ("b.png", make_solid_image(16, 16, 1, 2, 3))]
        with self.assertRaises(PixelForgeError) as ctx:
            check_uniform(frames)
        self.assertIn("b.png", str(ctx.exception))
        self.assertIn("16 x 16", str(ctx.exception))


class MetadataTests(unittest.TestCase):
    def test_metadata_fields_and_positions(self):
        frames = make_frames(5)
        _sheet, layout = pack_frames(frames, cols=2, padding=2)
        names = ["frame_%03d.png" % (i + 1) for i in range(5)]
        meta = build_metadata("walk-sheet.png", names, 32, 32,
                              layout, 8)
        self.assertEqual(meta["format"], "pixel-forge-spritesheet")
        self.assertEqual(meta["version"], 1)
        self.assertEqual(meta["sheet"], "walk-sheet.png")
        self.assertEqual((meta["sheet_width"], meta["sheet_height"]),
                         (66, 100))
        self.assertEqual((meta["frame_width"], meta["frame_height"]),
                         (32, 32))
        self.assertEqual((meta["columns"], meta["rows"]), (2, 3))
        self.assertEqual(meta["padding"], 2)
        self.assertEqual(meta["fps"], 8)
        self.assertEqual(len(meta["frames"]), 5)
        for index, entry in enumerate(meta["frames"]):
            self.assertEqual(entry["name"], names[index])
            self.assertEqual(entry["index"], index)
            self.assertEqual((entry["x"], entry["y"]),
                             layout["positions"][index])
            self.assertEqual((entry["w"], entry["h"]), (32, 32))
            self.assertEqual(entry["duration_ms"], 125)

    def test_metadata_durations_follow_fps(self):
        _sheet, layout = pack_frames(make_frames(2), cols=2)
        meta = build_metadata("s.png", ["a.png", "b.png"], 32, 32,
                              layout, 24)
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [42, 42])

    def test_write_and_load_round_trip(self):
        tmp = tempfile.mkdtemp()
        frames = make_frames(6)
        names = ["frame_%03d.png" % (i + 1) for i in range(6)]
        png = os.path.join(tmp, "sheet.png")
        js = os.path.join(tmp, "sheet.json")
        meta = write_sheet(frames, names, png, js, cols=3,
                           padding=1, fps=12)
        self.assertTrue(os.path.isfile(png))
        self.assertTrue(os.path.isfile(js))
        loaded = load_metadata(js)
        self.assertEqual(loaded, meta)
        # The saved PNG matches the metadata: size + frame pixels.
        image = load_image(png)
        self.assertEqual((image.width(), image.height()),
                         (meta["sheet_width"], meta["sheet_height"]))
        for index, entry in enumerate(meta["frames"]):
            self.assertEqual(
                pixel_rgb(image, entry["x"] + 16, entry["y"] + 16),
                COLORS[index] + (255,))

    def test_write_sheet_creates_parent_dirs(self):
        tmp = tempfile.mkdtemp()
        png = os.path.join(tmp, "deep", "nested", "sheet.png")
        js = os.path.join(tmp, "deep", "nested", "sheet.json")
        write_sheet(make_frames(2), ["a.png", "b.png"], png, js)
        self.assertTrue(os.path.isfile(png))
        self.assertTrue(os.path.isfile(js))

    def test_write_sheet_replace_failure_keeps_previous(self):
        # The metadata write is atomic (same fix class as
        # save_batch): a failed replace leaves the previous
        # metadata intact and no temp file behind.
        from unittest import mock
        tmp = tempfile.mkdtemp()
        frames = make_frames(2)
        names = ["a.png", "b.png"]
        png = os.path.join(tmp, "sheet.png")
        js = os.path.join(tmp, "sheet.json")
        write_sheet(frames, names, png, js)
        with open(js, "rb") as handle:
            before = handle.read()
        with mock.patch("os.replace",
                        side_effect=OSError("no space")):
            with self.assertRaises(PixelForgeError) as ctx:
                write_sheet(frames, names, png, js)
        self.assertIn("could not write", str(ctx.exception))
        with open(js, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertFalse(
            os.path.exists(os.path.join(tmp, "sheet.json.tmp")))

    def test_write_sheet_mixed_sizes_names_the_file(self):
        tmp = tempfile.mkdtemp()
        frames = [make_solid_image(32, 32, 1, 2, 3),
                  make_solid_image(16, 16, 1, 2, 3)]
        with self.assertRaises(PixelForgeError) as ctx:
            write_sheet(frames, ["a.png", "b.png"],
                        os.path.join(tmp, "sheet.png"),
                        os.path.join(tmp, "sheet.json"))
        self.assertIn("b.png", str(ctx.exception))
        self.assertIn("16 x 16", str(ctx.exception))

    def test_load_metadata_rejects_garbage(self):
        tmp = tempfile.mkdtemp()
        bad_json = os.path.join(tmp, "bad.json")
        with open(bad_json, "w") as handle:
            handle.write("{not json")
        with self.assertRaises(PixelForgeError):
            load_metadata(bad_json)
        wrong_format = os.path.join(tmp, "wrong.json")
        with open(wrong_format, "w") as handle:
            json.dump({"format": "something-else", "version": 1,
                       "frames": [{"x": 0, "y": 0, "w": 1, "h": 1}]},
                      handle)
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(wrong_format)
        self.assertIn("not a Pixel Forge spritesheet",
                      str(ctx.exception))
        no_frames = os.path.join(tmp, "empty.json")
        with open(no_frames, "w") as handle:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "frames": []}, handle)
        with self.assertRaises(PixelForgeError):
            load_metadata(no_frames)
        with self.assertRaises(PixelForgeError):
            load_metadata(os.path.join(tmp, "missing.json"))


class LoadMetadataValidationTests(unittest.TestCase):
    """B10: load_metadata validates every frame entry at load —
    bad metadata fails here with a plain named error, never later
    as a TypeError in extract_frame or a silently blank frame."""

    def _write_meta(self, frames, **overrides):
        meta = {"format": "pixel-forge-spritesheet", "version": 1,
                "sheet": "sheet.png", "sheet_width": 64,
                "sheet_height": 32, "frame_width": 32,
                "frame_height": 32, "columns": 2, "rows": 1,
                "padding": 0, "fps": 8, "frames": frames}
        meta.update(overrides)
        path = os.path.join(tempfile.mkdtemp(), "sheet.json")
        with open(path, "w") as handle:
            json.dump(meta, handle)
        return path

    def _frame(self, **overrides):
        frame = {"name": "frame_001.png", "index": 0,
                 "x": 0, "y": 0, "w": 32, "h": 32,
                 "duration_ms": 125}
        frame.update(overrides)
        return frame

    def test_null_frame_rejected_without_type_error(self):
        path = self._write_meta([None, self._frame(index=1, x=32)])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0", str(ctx.exception))
        self.assertIn("null", str(ctx.exception))

    def test_non_dict_frame_rejected(self):
        path = self._write_meta(["frame_001.png"])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0", str(ctx.exception))
        self.assertIn("not an object", str(ctx.exception))

    def test_string_coords_rejected(self):
        path = self._write_meta([self._frame(x="12")])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0 (frame_001.png)", str(ctx.exception))
        self.assertIn("'x'", str(ctx.exception))
        self.assertIn("whole number", str(ctx.exception))

    def test_missing_field_rejected(self):
        frame = self._frame()
        del frame["h"]
        path = self._write_meta([frame])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0", str(ctx.exception))
        self.assertIn("'h'", str(ctx.exception))

    def test_missing_name_rejected(self):
        frame = self._frame()
        del frame["name"]
        path = self._write_meta([frame])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0", str(ctx.exception))
        self.assertIn("'name'", str(ctx.exception))

    def test_bool_coord_rejected(self):
        # bool is an int subclass — a plain isinstance check
        # would let True through as 1.
        path = self._write_meta([self._frame(x=True)])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0 (frame_001.png)", str(ctx.exception))
        self.assertIn("'x'", str(ctx.exception))

    def test_out_of_bounds_rect_rejected(self):
        path = self._write_meta(
            [self._frame(name="frame_002.png", index=1, x=48)])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        # The label uses the frame's position in the list (0) —
        # the index field itself is untrusted input.
        self.assertIn("frame 0 (frame_002.png)", str(ctx.exception))
        self.assertIn("80, 32", str(ctx.exception))
        self.assertIn("64 x 32", str(ctx.exception))

    def test_negative_origin_rejected(self):
        path = self._write_meta([self._frame(x=-8)])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0 (frame_001.png)", str(ctx.exception))
        self.assertIn("x=-8", str(ctx.exception))

    def test_zero_size_rejected(self):
        path = self._write_meta([self._frame(w=0)])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("frame 0 (frame_001.png)", str(ctx.exception))
        self.assertIn("0 x 32", str(ctx.exception))

    def test_string_duration_rejected(self):
        path = self._write_meta([self._frame(duration_ms="125")])
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("duration_ms", str(ctx.exception))

    def test_duration_optional(self):
        frame = self._frame()
        del frame["duration_ms"]
        loaded = load_metadata(self._write_meta([frame]))
        self.assertEqual(len(loaded["frames"]), 1)

    def test_frames_not_a_list_rejected(self):
        path = self._write_meta(
            {"0": self._frame()})
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("not a list", str(ctx.exception))

    def test_string_sheet_size_rejected(self):
        path = self._write_meta([self._frame()], sheet_width="64")
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("'sheet_width'", str(ctx.exception))

    def test_valid_hand_written_sheet_loads(self):
        frames = [self._frame(),
                  self._frame(name="frame_002.png", index=1, x=32)]
        loaded = load_metadata(self._write_meta(frames))
        self.assertEqual(loaded["frames"], frames)
        self.assertEqual(loaded["sheet_width"], 64)


class FrameDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_frames(self, names, size=32):
        for index, name in enumerate(names):
            write_image(make_solid_image(
                size, size, *COLORS[index % len(COLORS)]),
                os.path.join(self.tmp, name))

    def test_loads_in_playback_order(self):
        self._write_frames(["frame_001.png", "frame_002.png",
                            "frame_010.png"])
        frames = load_frame_dir(self.tmp)
        self.assertEqual([name for name, _ in frames],
                         ["frame_001.png", "frame_002.png",
                          "frame_010.png"])

    def test_frame_1000_sorts_after_frame_999(self):
        self._write_frames(["frame_999.png", "frame_1000.png",
                            "frame_002.png"])
        frames = load_frame_dir(self.tmp)
        self.assertEqual([name for name, _ in frames],
                         ["frame_002.png", "frame_999.png",
                          "frame_1000.png"])

    def test_stray_png_sorts_last_not_first(self):
        # B9: cover.png used to become frame 0.
        self._write_frames(["frame_001.png", "frame_002.png",
                            "frame_003.png", "cover.png"])
        frames = load_frame_dir(self.tmp)
        self.assertEqual([name for name, _ in frames],
                         ["frame_001.png", "frame_002.png",
                          "frame_003.png", "cover.png"])

    def test_frame_2_sorts_before_frame_10(self):
        self._write_frames(["frame_10.png", "frame_2.png"])
        frames = load_frame_dir(self.tmp)
        self.assertEqual([name for name, _ in frames],
                         ["frame_2.png", "frame_10.png"])

    def test_non_png_files_ignored(self):
        self._write_frames(["frame_001.png"])
        with open(os.path.join(self.tmp, "notes.txt"), "w") as handle:
            handle.write("not a frame")
        frames = load_frame_dir(self.tmp)
        self.assertEqual(len(frames), 1)

    def test_missing_dir_fails_cleanly(self):
        with self.assertRaises(PixelForgeError) as ctx:
            load_frame_dir(os.path.join(self.tmp, "nope"))
        self.assertIn("not found", str(ctx.exception))

    def test_empty_dir_fails_cleanly(self):
        with self.assertRaises(PixelForgeError) as ctx:
            load_frame_dir(self.tmp)
        self.assertIn("no PNG frames", str(ctx.exception))

    def test_mixed_sizes_fail_cleanly(self):
        write_image(make_solid_image(32, 32, 1, 2, 3),
                    os.path.join(self.tmp, "frame_001.png"))
        write_image(make_solid_image(16, 16, 1, 2, 3),
                    os.path.join(self.tmp, "frame_002.png"))
        frames = load_frame_dir(self.tmp)
        with self.assertRaises(PixelForgeError) as ctx:
            check_uniform(frames)
        self.assertIn("same size", str(ctx.exception))


class SheetPlaybackTests(unittest.TestCase):
    def test_extract_frame_matches_source(self):
        frames = make_frames(6)
        sheet, layout = pack_frames(frames, cols=3, padding=2)
        names = ["frame_%03d.png" % (i + 1) for i in range(6)]
        meta = build_metadata("s.png", names, 32, 32, layout, 8)
        for index in range(6):
            cut = extract_frame(sheet, meta, index)
            self.assertEqual((cut.width(), cut.height()), (32, 32))
            # Every sampled pixel is the source frame's color.
            for x, y in ((0, 0), (16, 16), (31, 31)):
                self.assertEqual(pixel_rgb(cut, x, y),
                                 COLORS[index] + (255,))

    def test_frames_from_sheet_in_order(self):
        frames = make_frames(4)
        sheet, layout = pack_frames(frames, cols=2)
        meta = build_metadata("s.png", ["a.png", "b.png", "c.png",
                                        "d.png"], 32, 32, layout, 8)
        played = frames_from_sheet(sheet, meta)
        self.assertEqual(len(played), 4)
        for index, frame in enumerate(played):
            self.assertEqual(pixel_rgb(frame, 16, 16),
                             COLORS[index] + (255,))

    def test_extract_out_of_range_fails_cleanly(self):
        sheet, layout = pack_frames(make_frames(2), cols=2)
        meta = build_metadata("s.png", ["a.png", "b.png"], 32, 32,
                              layout, 8)
        for bad in (-1, 2, 99):
            with self.assertRaises(PixelForgeError, msg=bad):
                extract_frame(sheet, meta, bad)


class DurationsTagsTests(unittest.TestCase):
    """Task 34: per-frame durations in the sheet metadata and
    animation tags — build, write, load, and validation."""

    def _layout(self, count=4):
        _sheet, layout = pack_frames(make_frames(count), cols=2)
        return layout

    def _names(self, count=4):
        return ["frame_%03d.png" % (i + 1) for i in range(count)]

    def test_durations_land_in_metadata(self):
        meta = build_metadata("s.png", self._names(), 32, 32,
                              self._layout(), 8,
                              durations=[125, 500, 250, 1000])
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [125, 500, 250, 1000])
        # The fps still records the base speed.
        self.assertEqual(meta["fps"], 8)

    def test_fps_default_unchanged_without_durations(self):
        meta = build_metadata("s.png", self._names(), 32, 32,
                              self._layout(), 8)
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [125, 125, 125, 125])
        # And no tags key appears at all.
        self.assertNotIn("tags", meta)

    def test_durations_length_mismatch_fails(self):
        with self.assertRaises(PixelForgeError) as ctx:
            build_metadata("s.png", self._names(), 32, 32,
                           self._layout(), 8, durations=[125, 500])
        self.assertIn("4 frames but 2 durations", str(ctx.exception))

    def test_bad_duration_values_fail(self):
        for bad in ([125, 0, 250, 125], [125, -5, 250, 125],
                    [125, "250", 250, 125], [125, True, 250, 125]):
            with self.assertRaises(PixelForgeError, msg=bad):
                build_metadata("s.png", self._names(), 32, 32,
                               self._layout(), 8, durations=bad)

    def test_tags_land_in_metadata(self):
        tags = [{"name": "walk", "from": 0, "to": 1},
                {"name": "idle", "from": 2, "to": 3}]
        meta = build_metadata("s.png", self._names(), 32, 32,
                              self._layout(), 8, tags=tags)
        self.assertEqual(meta["tags"], tags)
        # Stored as copies — later edits to the caller's list
        # must not rewrite the metadata.
        tags[0]["to"] = 3
        self.assertEqual(meta["tags"][0]["to"], 1)

    def test_empty_tags_list_stores_no_key(self):
        meta = build_metadata("s.png", self._names(), 32, 32,
                              self._layout(), 8, tags=[])
        self.assertNotIn("tags", meta)

    def test_write_sheet_round_trips_durations_and_tags(self):
        tmp = tempfile.mkdtemp()
        png = os.path.join(tmp, "sheet.png")
        js = os.path.join(tmp, "sheet.json")
        tags = [{"name": "walk", "from": 0, "to": 2}]
        meta = write_sheet(make_frames(4), self._names(), png, js,
                           cols=2, fps=8,
                           durations=[125, 500, 250, 125],
                           tags=tags)
        self.assertEqual(meta["tags"], tags)
        loaded = load_metadata(js)
        self.assertEqual(loaded, meta)
        self.assertEqual([f["duration_ms"]
                          for f in loaded["frames"]],
                         [125, 500, 250, 125])

    def test_validate_tags_valid(self):
        validate_tags([{"name": "walk", "from": 0, "to": 3}], 4)
        validate_tags([], 4)
        validate_tags(None, 4)

    def test_validate_tags_rejects_bad(self):
        cases = [
            ("not a list", "is not a list"),
            ([{"name": "a", "from": 0}], "is missing 'to'"),
            ([{"name": "", "from": 0, "to": 1}],
             "name must be non-empty text"),
            ([{"name": 7, "from": 0, "to": 1}],
             "name must be non-empty text"),
            ([{"name": "a", "from": True, "to": 1}],
             "'from' must be a whole number"),
            ([{"name": "a", "from": 0, "to": "3"}],
             "'to' must be a whole number"),
            ([{"name": "a", "from": -1, "to": 1}],
             "frame positions start at 0"),
            ([{"name": "a", "from": 2, "to": 1}],
             "start must not come after the end"),
            ([{"name": "a", "from": 0, "to": 4}],
             "only has 4 frames"),
            ([{"name": "a", "from": 0, "to": 1},
              {"name": "a", "from": 2, "to": 3}],
             "appears more than once"),
        ]
        for tags, fragment in cases:
            with self.assertRaises(PixelForgeError,
                                   msg=repr(tags)) as ctx:
                validate_tags(tags, 4)
            self.assertIn(fragment, str(ctx.exception))

    def test_load_metadata_tags_validation(self):
        tmp = tempfile.mkdtemp()
        base = {"format": "pixel-forge-spritesheet", "version": 1,
                "sheet": "s.png", "sheet_width": 66,
                "sheet_height": 66, "frame_width": 32,
                "frame_height": 32, "columns": 2, "rows": 2,
                "padding": 0, "fps": 8,
                "frames": [{"name": "a.png", "index": 0, "x": 0,
                            "y": 0, "w": 32, "h": 32,
                            "duration_ms": 125},
                           {"name": "b.png", "index": 1, "x": 34,
                            "y": 0, "w": 32, "h": 32,
                            "duration_ms": 500}]}
        # Valid tags load.
        good = dict(base)
        good["tags"] = [{"name": "walk", "from": 0, "to": 1}]
        path = os.path.join(tmp, "good.json")
        with open(path, "w") as handle:
            json.dump(good, handle)
        self.assertEqual(load_metadata(path)["tags"], good["tags"])
        # Bad tags fail at load with a plain reason.
        bad = dict(base)
        bad["tags"] = [{"name": "walk", "from": 0, "to": 5}]
        path = os.path.join(tmp, "bad.json")
        with open(path, "w") as handle:
            json.dump(bad, handle)
        with self.assertRaises(PixelForgeError) as ctx:
            load_metadata(path)
        self.assertIn("only has 2 frames", str(ctx.exception))
        # Old metadata without tags loads unchanged.
        path = os.path.join(tmp, "old.json")
        with open(path, "w") as handle:
            json.dump(base, handle)
        loaded = load_metadata(path)
        self.assertNotIn("tags", loaded)


if __name__ == "__main__":
    unittest.main()
