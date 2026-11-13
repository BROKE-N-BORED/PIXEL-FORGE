"""Spritesheet split tests (task 40) — grid geometry, grid cutting,
metadata round-trip, sibling discovery, and the split-sheet CLI.

Qt-free (task 64): runs without PySide6. The dialog + landing tests
live in test_sheet_split_gui.py.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import ensure_app_if_qt, make_solid_image, write_image  # noqa: E402
from pixel_forge.cli import main  # noqa: E402
from pixel_forge.pixelize import PixelForgeError, load_image  # noqa: E402
from pixel_forge.spritesheet import (  # noqa: E402
    find_sibling_metadata, frames_from_grid, frames_from_sheet,
    grid_layout, load_metadata, pack_frames, split_with_metadata,
    write_sheet)

ensure_app_if_qt()

# Distinct frame colors: (r, g, b) per frame index.
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
          (255, 0, 255), (0, 255, 255), (128, 128, 128),
          (255, 128, 0), (0, 128, 255)]


def solid_frames(count, size=16):
    return [make_solid_image(size, size, *COLORS[i])
            for i in range(count)]


class GridLayoutTests(unittest.TestCase):
    def test_frame_size_form(self):
        layout = grid_layout(64, 32, frame_w=16, frame_h=16)
        self.assertEqual(layout["columns"], 4)
        self.assertEqual(layout["rows"], 2)
        self.assertEqual(layout["frame_width"], 16)
        self.assertEqual(layout["frame_height"], 16)
        self.assertEqual(len(layout["positions"]), 8)
        self.assertEqual(layout["positions"][0], (0, 0))
        self.assertEqual(layout["positions"][4], (0, 16))
        self.assertEqual(layout["positions"][7], (48, 16))

    def test_counts_form(self):
        layout = grid_layout(64, 32, cols=4, rows=2)
        self.assertEqual(layout["frame_width"], 16)
        self.assertEqual(layout["frame_height"], 16)
        self.assertEqual(len(layout["positions"]), 8)

    def test_both_forms_agree(self):
        layout = grid_layout(64, 32, frame_w=16, frame_h=16,
                             cols=4, rows=2)
        self.assertEqual(layout["columns"], 4)
        self.assertEqual(layout["rows"], 2)

    def test_both_forms_disagree(self):
        # 16 px frames on a 64 px sheet mean 4 columns; asking
        # for 2 columns means 32 px frames — the two disagree.
        with self.assertRaises(PixelForgeError) as caught:
            grid_layout(64, 64, frame_w=16, frame_h=16,
                        cols=2, rows=2)
        self.assertIn("32 x 32", str(caught.exception))
        self.assertIn("16 x 16", str(caught.exception))

    def test_spacing_and_margin(self):
        layout = grid_layout(56, 56, frame_w=16, frame_h=16,
                             spacing=2, margin=2)
        self.assertEqual(layout["columns"], 3)
        self.assertEqual(layout["rows"], 3)
        self.assertEqual(layout["positions"][0], (2, 2))
        self.assertEqual(layout["positions"][1], (20, 2))
        self.assertEqual(layout["positions"][3], (2, 20))

    def test_spacing_and_margin_from_counts(self):
        layout = grid_layout(56, 56, cols=3, rows=3,
                             spacing=2, margin=2)
        self.assertEqual(layout["frame_width"], 16)
        self.assertEqual(layout["frame_height"], 16)

    def test_neither_pair_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            grid_layout(64, 64)
        self.assertIn("frame size", str(caught.exception))

    def test_half_pair_refuses(self):
        # Width without height is not a pair — same refusal.
        with self.assertRaises(PixelForgeError):
            grid_layout(64, 64, frame_w=16)
        with self.assertRaises(PixelForgeError):
            grid_layout(64, 64, cols=4)

    def test_non_whole_frame_size_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            grid_layout(64, 64, frame_w=15, frame_h=16)
        message = str(caught.exception)
        self.assertIn("64 px wide", message)
        self.assertIn("15 px", message)

    def test_non_whole_counts_refuse(self):
        with self.assertRaises(PixelForgeError) as caught:
            grid_layout(64, 64, cols=3, rows=3)
        self.assertIn("3 columns x 3 rows", str(caught.exception))

    def test_cells_under_one_pixel_refuse(self):
        with self.assertRaises(PixelForgeError) as caught:
            grid_layout(16, 16, cols=32, rows=1)
        self.assertIn("under 1 pixel", str(caught.exception))

    def test_margin_eats_sheet_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            grid_layout(16, 16, frame_w=8, frame_h=8, margin=10)
        self.assertIn("margin", str(caught.exception))

    def test_negative_values_refuse(self):
        with self.assertRaises(PixelForgeError):
            grid_layout(64, 64, frame_w=16, frame_h=16, spacing=-1)
        with self.assertRaises(PixelForgeError):
            grid_layout(64, 64, frame_w=16, frame_h=16, margin=-1)

    def test_single_frame_grid(self):
        layout = grid_layout(32, 32, frame_w=32, frame_h=32)
        self.assertEqual(layout["columns"], 1)
        self.assertEqual(layout["rows"], 1)
        self.assertEqual(layout["positions"], [(0, 0)])


class FramesFromGridTests(unittest.TestCase):
    def test_pixels_match_by_cell(self):
        frames = solid_frames(4)
        sheet, _layout = pack_frames(frames, cols=2)
        got = frames_from_grid(sheet, frame_w=16, frame_h=16)
        self.assertEqual(len(got), 4)
        for index in range(4):
            self.assertEqual(got[index].data, frames[index].data)

    def test_counts_form_matches(self):
        frames = solid_frames(6)
        sheet, _layout = pack_frames(frames, cols=3)
        got = frames_from_grid(sheet, cols=3, rows=2)
        self.assertEqual(len(got), 6)
        for index in range(6):
            self.assertEqual(got[index].data, frames[index].data)

    def test_spacing_skips_gaps(self):
        # A 2x2 grid with 4 px gaps: build the sheet by hand so
        # the gaps are a fifth color the frames must not pick up.
        from pixel_forge.image import Image, argb
        # 2 columns of 16 px with a 4 px gap: 16 + 4 + 16 = 36.
        sheet = Image(36, 36)
        sheet.fill(argb(9, 9, 9))
        for index, (x, y) in enumerate(
                [(0, 0), (20, 0), (0, 20), (20, 20)]):
            cell = make_solid_image(16, 16, *COLORS[index])
            sheet_data = sheet.data
            for row in range(16):
                s = row * 16 * 4
                d = ((y + row) * 36 + x) * 4
                sheet_data[d:d + 16 * 4] = \
                    cell.data[s:s + 16 * 4]
        got = frames_from_grid(sheet, frame_w=16, frame_h=16,
                               spacing=4)
        self.assertEqual(len(got), 4)
        for index in range(4):
            self.assertEqual(
                got[index].data,
                make_solid_image(16, 16, *COLORS[index]).data)

    def test_margin_offsets_cells(self):
        from pixel_forge.image import Image, argb
        sheet = Image(36, 36)
        sheet.fill(argb(9, 9, 9))
        cell = make_solid_image(16, 16, *COLORS[0])
        for row in range(16):
            s = row * 16 * 4
            d = ((2 + row) * 36 + 2) * 4
            sheet.data[d:d + 16 * 4] = cell.data[s:s + 16 * 4]
        got = frames_from_grid(sheet, frame_w=16, frame_h=16,
                               margin=2)
        # A 36 px sheet with margin 2 holds one 16 px cell per
        # side only when the usable span divides evenly: 32 px
        # usable means 2 columns of 16 — the second column is
        # the background color, which is exactly what a grid
        # split of a half-empty sheet returns.
        self.assertEqual(len(got), 4)
        self.assertEqual(got[0].data, cell.data)


class SiblingMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_found(self):
        png = write_image(make_solid_image(16, 16, 1, 2, 3),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as handle:
            handle.write("{}")
        self.assertEqual(find_sibling_metadata(png), Path(meta))

    def test_not_found(self):
        png = write_image(make_solid_image(16, 16, 1, 2, 3),
                          os.path.join(self.tmp, "bare.png"))
        self.assertIsNone(find_sibling_metadata(png))


class SplitWithMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.frames = solid_frames(4)
        self.png = os.path.join(self.tmp, "walk-sheet.png")
        self.meta = os.path.join(self.tmp, "walk-sheet.json")
        write_sheet(self.frames, ["f1", "f2", "f3", "f4"],
                    self.png, self.meta, cols=2, fps=8,
                    durations=[100, 200, 300, 400],
                    tags=[{"name": "walk", "from": 0, "to": 3}])

    def test_round_trip_pixels_durations_tags(self):
        metadata = load_metadata(self.meta)
        frames, durations, tags = split_with_metadata(
            load_image(self.png), metadata)
        self.assertEqual(len(frames), 4)
        for index in range(4):
            self.assertEqual(frames[index].data,
                             self.frames[index].data)
        self.assertEqual(durations, [100, 200, 300, 400])
        self.assertEqual(tags,
                         [{"name": "walk", "from": 0, "to": 3}])

    def test_mismatched_png_refuses(self):
        metadata = load_metadata(self.meta)
        small = make_solid_image(16, 16, 1, 2, 3)
        with self.assertRaises(PixelForgeError) as caught:
            split_with_metadata(small, metadata)
        message = str(caught.exception)
        self.assertIn("16 x 16", message)
        self.assertIn("32 x 32", message)
        self.assertIn("not a pair", message)

    def test_missing_duration_falls_back_to_fps(self):
        metadata = load_metadata(self.meta)
        del metadata["frames"][1]["duration_ms"]
        _frames, durations, _tags = split_with_metadata(
            load_image(self.png), metadata)
        # fps 8 -> 125 ms for the frame with no duration.
        self.assertEqual(durations, [100, 125, 300, 400])

    def test_zero_duration_falls_back_to_fps(self):
        metadata = load_metadata(self.meta)
        metadata["frames"][0]["duration_ms"] = 0
        _frames, durations, _tags = split_with_metadata(
            load_image(self.png), metadata)
        self.assertEqual(durations[0], 125)

    def test_no_tags_gives_empty_list(self):
        meta2 = os.path.join(self.tmp, "plain.json")
        write_sheet(self.frames, ["f1", "f2", "f3", "f4"],
                    self.png, meta2, cols=2, fps=8)
        metadata = load_metadata(meta2)
        _frames, _durations, tags = split_with_metadata(
            load_image(self.png), metadata)
        self.assertEqual(tags, [])

    def test_frames_from_sheet_still_plays(self):
        # The old path is unchanged: frames_from_sheet cuts the
        # same frames in the same order.
        metadata = load_metadata(self.meta)
        played = frames_from_sheet(load_image(self.png), metadata)
        self.assertEqual(len(played), 4)
        for index in range(4):
            self.assertEqual(played[index].data,
                             self.frames[index].data)


class SplitSheetCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.frames = solid_frames(4)
        self.png = os.path.join(self.tmp, "walk-sheet.png")
        self.meta = os.path.join(self.tmp, "walk-sheet.json")
        write_sheet(self.frames, ["f1", "f2", "f3", "f4"],
                    self.png, self.meta, cols=2, fps=8,
                    durations=[100, 200, 300, 400],
                    tags=[{"name": "walk", "from": 0, "to": 3}])
        self.bare = os.path.join(self.tmp, "bare.png")
        sheet, _layout = pack_frames(self.frames, cols=2)
        write_image(sheet, self.bare)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_metadata_mode_writes_frames(self):
        out_dir = os.path.join(self.tmp, "split")
        code, out, err = self._run(
            ["split-sheet", self.png, "--out", out_dir])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("4 frames", out)
        for index in range(1, 5):
            path = os.path.join(out_dir,
                                "frame_%03d.png" % index)
            self.assertTrue(os.path.isfile(path), path)
            self.assertEqual(
                load_image(path).data,
                self.frames[index - 1].data)

    def test_metadata_mode_json_reports_durations_tags(self):
        out_dir = os.path.join(self.tmp, "split-json")
        code, out, _err = self._run(
            ["split-sheet", self.png, "--out", out_dir, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "metadata")
        self.assertEqual(payload["frames"], 4)
        self.assertEqual(payload["durations"],
                         [100, 200, 300, 400])
        self.assertEqual(payload["tags"],
                         [{"name": "walk", "from": 0, "to": 3}])

    def test_sibling_metadata_found_automatically(self):
        # No --meta: the JSON next to the PNG is used.
        out_dir = os.path.join(self.tmp, "split-auto")
        code, _out, _err = self._run(
            ["split-sheet", self.png, "--out", out_dir, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(_out)
        self.assertEqual(payload["mode"], "metadata")

    def test_explicit_meta(self):
        other = os.path.join(self.tmp, "elsewhere.json")
        with open(other, "w") as handle:
            json.dump(load_metadata(self.meta), handle)
        out_dir = os.path.join(self.tmp, "split-explicit")
        code, out, _err = self._run(
            ["split-sheet", self.png, "--meta", other,
             "--out", out_dir, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["frames"], 4)

    def test_grid_mode(self):
        out_dir = os.path.join(self.tmp, "split-grid")
        code, out, _err = self._run(
            ["split-sheet", self.bare, "--grid", "16x16",
             "--out", out_dir, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "grid")
        self.assertEqual(payload["frames"], 4)
        self.assertEqual(
            load_image(os.path.join(out_dir, "frame_001.png")).data,
            self.frames[0].data)

    def test_cols_rows_mode(self):
        out_dir = os.path.join(self.tmp, "split-cr")
        code, _out, _err = self._run(
            ["split-sheet", self.bare, "--cols", "2", "--rows", "2",
             "--out", out_dir])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "frame_004.png")))

    def test_default_out_dir(self):
        code, _out, _err = self._run(
            ["split-sheet", self.bare, "--grid", "16x16"])
        self.assertEqual(code, 0)
        default = os.path.join(self.tmp, "bare-frames")
        self.assertTrue(os.path.isdir(default))
        self.assertTrue(os.path.isfile(
            os.path.join(default, "frame_001.png")))

    def test_no_metadata_no_grid_refuses(self):
        code, _out, err = self._run(["split-sheet", self.bare])
        self.assertEqual(code, 1)
        self.assertIn("no metadata found", err)
        self.assertNotIn("Traceback", err)

    def test_missing_meta_refuses_cleanly(self):
        code, _out, err = self._run(
            ["split-sheet", self.png, "--meta",
             os.path.join(self.tmp, "missing.json")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def test_bad_grid_refuses_cleanly(self):
        code, _out, err = self._run(
            ["split-sheet", self.bare, "--grid", "15x16"])
        self.assertEqual(code, 1)
        self.assertIn("do not divide it evenly", err)
        self.assertNotIn("Traceback", err)

    def test_malformed_grid_refuses_cleanly(self):
        code, _out, err = self._run(
            ["split-sheet", self.bare, "--grid", "wide"])
        self.assertEqual(code, 1)
        self.assertIn("--grid takes WIDTHxHEIGHT", err)
        self.assertNotIn("Traceback", err)

    def test_meta_and_grid_refuse(self):
        code, _out, err = self._run(
            ["split-sheet", self.png, "--meta", self.meta,
             "--grid", "16x16"])
        self.assertEqual(code, 1)
        self.assertIn("cannot be combined", err)

    def test_spacing_with_metadata_refuses(self):
        code, _out, err = self._run(
            ["split-sheet", self.png, "--spacing", "2"])
        self.assertEqual(code, 1)
        self.assertIn("only apply to a grid split", err)

    def test_missing_sheet_refuses_cleanly(self):
        code, _out, err = self._run(
            ["split-sheet", os.path.join(self.tmp, "nope.png")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def test_quiet_suppresses_info(self):
        out_dir = os.path.join(self.tmp, "split-quiet")
        code, out, _err = self._run(
            ["split-sheet", self.png, "--out", out_dir, "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
