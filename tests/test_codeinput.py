"""Code input tests — static parsing of .py/.js/.ts/.json/.cs/.gd/
.lua, no execution.

Sample code files are written to a temp dir and parsed through the
public API (parse_file / parse_paths). Pixel-exact checks read the
rendered Image back pixel by pixel.

Qt-free (task 64): runs without PySide6.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.codeinput import (MAX_CODE_FILE_BYTES,  # noqa: E402
                                   NAMED_COLORS, _js_assignments,
                                   parse_color, parse_file, parse_paths,
                                   parse_source)
from pixel_forge.image import Image, argb  # noqa: E402
from pixel_forge.pixelize import PixelForgeError  # noqa: E402
from pixel_forge.pngio import write_png  # noqa: E402

PY_SAMPLE = '''
"""A tiny game file: palettes, maps, and things that cannot render."""

PALETTE = ["#ff0000", "#00ff00", "#0000ff"]

HERO = [
    [0, 1, 1, 0],
    [1, 1, 1, 1],
    [0, 2, 2, 0],
]

LEGEND = {".": None, "X": "#ff0000", "O": (0, 255, 0)}

SLIME = [
    ".XX.",
    "XOOX",
    ".XX.",
]

HEX_SPRITE = [
    ["#ff0000", None],
    [None, "#0000ff"],
]

RGB_SPRITE = [
    [(255, 0, 0), (0, 255, 0)],
]

INT_PALETTE = {0: "#000000", 1: "#ffffff"}

INDEXED = [[0, 1], [1, 0]]

DRAW_STEPS = [
    {"x": 0, "y": 0, "w": 8, "h": 8, "color": "#ff0000"},
]

SPRITE_COMPUTED = make_sprite(8, 8)

RAGGED_SPRITE = [[1, 1], [1]]

NO_LEGEND = ["AB", "BA"]

NAMED = ["red", "gold", "navy"]
'''

JS_SAMPLE = '''
// A tiny JS game file.
const PALETTE = ["#ff0000", "#00ff00", "#0000ff"];

/* the hero, as int rows */
const HERO = [
  [0, 1, 1, 0],
  [1, 1, 1, 1],
];

const LEGEND = { ".": null, "X": "#ff0000", "O": "#00ff00" };
const SLIME = [".XX.", "XOOX"];

const DRAW = [{x: 0, y: 0, w: 8, h: 8, color: "#ff0000"}];
const spriteComputed = makeSprite(8, 8);
export const EXTRA = [["#00ff00"]];
'''

TS_SAMPLE = '''
const TILE: number[][] = [[1, 0], [0, 1]];
const LABELS: Record<string, string> = { a: "#ff0000" };
'''


def pixel(image, x, y):
    px = image.pixel(x, y)
    return ((px >> 16) & 0xFF, (px >> 8) & 0xFF,
            px & 0xFF, (px >> 24) & 0xFF)


def by_name(result, name):
    for asset in result["assets"]:
        if asset["name"] == name:
            return asset
    return None


def by_note(result, name):
    for note in result["not_rendered"]:
        if note["name"] == name:
            return note
    return None


def write_sheet(path, width=64, height=32):
    """A real PNG on disk: left half red, right half blue."""
    image = Image(width, height)
    for y in range(height):
        for x in range(width):
            if x < width // 2:
                image.setPixel(x, y, argb(255, 0, 0, 255))
            else:
                image.setPixel(x, y, argb(0, 0, 255, 255))
    write_png(image, path)
    return path


class PythonParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "game.py")
        with open(cls.path, "w") as handle:
            handle.write(PY_SAMPLE)
        cls.result = parse_file(cls.path)

    def test_int_map_uses_file_palette(self):
        hero = by_name(self.result, "HERO")
        self.assertIsNotNone(hero)
        self.assertEqual(hero["kind"], "map")
        self.assertEqual((hero["width"], hero["height"]), (4, 3))
        self.assertIn("PALETTE", hero["detail"])
        # 0 is transparent, 1 is the first color (red), 2 is green.
        self.assertEqual(pixel(hero["image"], 0, 0)[3], 0)
        self.assertEqual(pixel(hero["image"], 1, 0), (255, 0, 0, 255))
        self.assertEqual(pixel(hero["image"], 1, 2), (0, 255, 0, 255))
        self.assertEqual(hero["colors"], 2)

    def test_full_range_map_keeps_file_palette(self):
        # Regression: a map using every color of its palette used
        # to fall back to the PICO-8 default — the coverage check
        # was off by one and the palette was silently dropped.
        result = parse_source(
            'PALETTE = ["#ff0000", "#00ff00", "#0000ff"]\n'
            "FULL = [[1, 2, 3]]\n", "game.py", "py")
        full = by_name(result, "FULL")
        self.assertIsNotNone(full)
        self.assertIn("PALETTE", full["detail"])
        self.assertEqual(pixel(full["image"], 0, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(full["image"], 1, 0),
                         (0, 255, 0, 255))
        self.assertEqual(pixel(full["image"], 2, 0),
                         (0, 0, 255, 255))

    def test_int_map_uses_int_keyed_palette(self):
        indexed = by_name(self.result, "INDEXED")
        self.assertIsNotNone(indexed)
        self.assertIn("INT_PALETTE", indexed["detail"])
        self.assertEqual(pixel(indexed["image"], 0, 0)[3], 0)
        self.assertEqual(pixel(indexed["image"], 1, 0),
                         (255, 255, 255, 255))

    def test_char_rows_with_legend(self):
        slime = by_name(self.result, "SLIME")
        self.assertIsNotNone(slime)
        self.assertEqual((slime["width"], slime["height"]), (4, 3))
        self.assertIn("LEGEND", slime["detail"])
        self.assertEqual(pixel(slime["image"], 0, 0)[3], 0)
        self.assertEqual(pixel(slime["image"], 1, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(slime["image"], 1, 1),
                         (0, 255, 0, 255))

    def test_hex_and_rgb_maps_pixel_exact(self):
        hexed = by_name(self.result, "HEX_SPRITE")
        self.assertEqual(pixel(hexed["image"], 0, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(hexed["image"], 1, 0)[3], 0)
        self.assertEqual(pixel(hexed["image"], 1, 1),
                         (0, 0, 255, 255))
        rgb = by_name(self.result, "RGB_SPRITE")
        self.assertEqual(pixel(rgb["image"], 0, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(rgb["image"], 1, 0), (0, 255, 0, 255))

    def test_palettes_rendered_as_strips(self):
        palette = by_name(self.result, "PALETTE")
        self.assertIsNotNone(palette)
        self.assertEqual(palette["kind"], "palette")
        self.assertEqual(palette["colors"], 3)
        # One 8x8 block per color: block centers are the colors.
        self.assertEqual(pixel(palette["image"], 4, 4),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(palette["image"], 12, 4),
                         (0, 255, 0, 255))
        self.assertEqual(pixel(palette["image"], 20, 4),
                         (0, 0, 255, 255))
        named = by_name(self.result, "NAMED")
        self.assertIsNotNone(named)
        self.assertEqual(pixel(named["image"], 4, 4),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(named["image"], 12, 4),
                         (255, 215, 0, 255))

    def test_legend_dict_is_also_a_palette(self):
        legend = by_name(self.result, "LEGEND")
        self.assertIsNotNone(legend)
        self.assertEqual(legend["kind"], "palette")
        self.assertEqual(legend["colors"], 3)

    def test_not_rendered_reasons(self):
        notes = {n["name"]: n["reason"]
                 for n in self.result["not_rendered"]}
        self.assertIn("DRAW_STEPS", notes)
        self.assertIn("draw-call", notes["DRAW_STEPS"])
        self.assertIn("SPRITE_COMPUTED", notes)
        self.assertIn("computed at run time", notes["SPRITE_COMPUTED"])
        self.assertIn("RAGGED_SPRITE", notes)
        self.assertIn("different lengths", notes["RAGGED_SPRITE"])
        self.assertIn("NO_LEGEND", notes)
        self.assertIn("no color legend", notes["NO_LEGEND"])
        self.assertIn("'A'", notes["NO_LEGEND"])

    def test_nothing_is_executed(self):
        # A file whose top level would explode if run still parses.
        evil = os.path.join(self.tmp, "evil.py")
        with open(evil, "w") as handle:
            handle.write("import os\nos.system('touch %s')\n"
                         "SPRITE = [[1]]\n"
                         % os.path.join(self.tmp, "pwned"))
        result = parse_file(evil)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "pwned")))
        self.assertIsNotNone(by_name(result, "SPRITE"))


class JsParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "game.js")
        with open(cls.path, "w") as handle:
            handle.write(JS_SAMPLE)
        cls.result = parse_file(cls.path)

    def test_js_int_map_and_palette(self):
        hero = by_name(self.result, "HERO")
        self.assertIsNotNone(hero)
        self.assertEqual((hero["width"], hero["height"]), (4, 2))
        self.assertEqual(pixel(hero["image"], 0, 0)[3], 0)
        self.assertEqual(pixel(hero["image"], 1, 0),
                         (255, 0, 0, 255))

    def test_js_char_rows_and_legend(self):
        slime = by_name(self.result, "SLIME")
        self.assertIsNotNone(slime)
        self.assertEqual(pixel(slime["image"], 1, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(slime["image"], 1, 1),
                         (0, 255, 0, 255))

    def test_js_export_const_found(self):
        extra = by_name(self.result, "EXTRA")
        self.assertIsNotNone(extra)
        self.assertEqual(pixel(extra["image"], 0, 0),
                         (0, 255, 0, 255))

    def test_js_not_rendered(self):
        notes = {n["name"]: n["reason"]
                 for n in self.result["not_rendered"]}
        self.assertIn("DRAW", notes)
        self.assertIn("draw-call", notes["DRAW"])
        self.assertIn("spriteComputed", notes)
        self.assertIn("computed at run time", notes["spriteComputed"])

    def test_ts_annotations_skipped(self):
        result = parse_source(TS_SAMPLE, "game.ts", "js")
        tile = by_name(result, "TILE")
        self.assertIsNotNone(tile)
        # LABELS is the file's only palette (one color): 1 is red.
        self.assertEqual(pixel(tile["image"], 0, 0), (255, 0, 0, 255))
        self.assertEqual(pixel(tile["image"], 1, 0)[3], 0)
        labels = by_name(result, "LABELS")
        self.assertIsNotNone(labels)
        self.assertEqual(labels["kind"], "palette")

    def test_js_comments_do_not_break_parsing(self):
        source = ("// const FAKE = [[1]];\n"
                  "/* const ALSO_FAKE = [[1]]; */\n"
                  "const REAL = [[1]];\n")
        result = parse_source(source, "game.js", "js")
        names = [a["name"] for a in result["assets"]]
        self.assertEqual(names, ["REAL"])


class ParseDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_missing_file_fails_cleanly(self):
        with self.assertRaises(PixelForgeError) as ctx:
            parse_file(os.path.join(self.tmp, "nope.py"))
        self.assertIn("not found", str(ctx.exception))

    def test_wrong_extension_fails_cleanly(self):
        path = self._write("notes.txt", "SPRITE = [[1]]\n")
        with self.assertRaises(PixelForgeError) as ctx:
            parse_file(path)
        self.assertIn("not a code file", str(ctx.exception))

    def test_python_syntax_error_fails_cleanly(self):
        path = self._write("broken.py", "def broken(:\n")
        with self.assertRaises(PixelForgeError) as ctx:
            parse_file(path)
        self.assertIn("could not parse", str(ctx.exception))

    def test_empty_file_has_no_assets(self):
        path = self._write("empty.py", "# nothing here\n")
        result = parse_file(path)
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["not_rendered"], [])

    def test_folder_merges_files(self):
        folder = os.path.join(self.tmp, "src")
        os.mkdir(folder)
        with open(os.path.join(folder, "a.py"), "w") as handle:
            handle.write("ONE = [[1]]\n")
        with open(os.path.join(folder, "b.js"), "w") as handle:
            handle.write("const TWO = [[2]];\n")
        with open(os.path.join(folder, "notes.txt"), "w") as handle:
            handle.write("ignored\n")
        result = parse_paths([folder])
        names = sorted(a["name"] for a in result["assets"])
        self.assertEqual(names, ["ONE", "TWO"])
        self.assertEqual(len(result["files"]), 2)

    def test_folder_with_no_code_files_fails(self):
        folder = os.path.join(self.tmp, "docs")
        os.mkdir(folder)
        with open(os.path.join(folder, "readme.txt"), "w") as handle:
            handle.write("no code\n")
        with self.assertRaises(PixelForgeError) as ctx:
            parse_paths([folder])
        self.assertIn(
            "no .py, .js, .ts, .json, .cs, .gd, or .lua files",
            str(ctx.exception))

    def test_folder_skips_bad_file_keeps_good_ones(self):
        # Task 65: one bad file must not abort the folder import.
        folder = os.path.join(self.tmp, "mixed")
        os.mkdir(folder)
        with open(os.path.join(folder, "good.py"), "w") as handle:
            handle.write("ONE = [[1]]\n")
        with open(os.path.join(folder, "broken.py"), "w") as handle:
            handle.write("def broken(:\n")
        with open(os.path.join(folder, "good.js"), "w") as handle:
            handle.write("const TWO = [[2]];\n")
        with open(os.path.join(folder, "junk.py"), "wb") as handle:
            handle.write(b"\x00\x01\x02\xff\xfe binary junk")
        with open(os.path.join(folder, "empty.py"), "w") as handle:
            handle.write("# nothing here\n")
        result = parse_paths([folder])
        names = sorted(a["name"] for a in result["assets"])
        self.assertEqual(names, ["ONE", "TWO"])
        # The empty file parsed fine (no assets is not a failure).
        self.assertEqual(len(result["files"]), 3)
        skipped = sorted(s["file"] for s in result["skipped"])
        self.assertEqual([os.path.basename(f) for f in skipped],
                         ["broken.py", "junk.py"])
        for skip in result["skipped"]:
            self.assertIn("could not parse", skip["reason"])
            # The binary file's SyntaxError has no line number —
            # the reason must not read "(line None)".
            self.assertNotIn("line None", skip["reason"])

    def test_folder_all_bad_fails_naming_each_file(self):
        folder = os.path.join(self.tmp, "allbad")
        os.mkdir(folder)
        with open(os.path.join(folder, "a.py"), "w") as handle:
            handle.write("def a(:\n")
        with open(os.path.join(folder, "b.py"), "w") as handle:
            handle.write("x = \n")
        with self.assertRaises(PixelForgeError) as ctx:
            parse_paths([folder])
        message = str(ctx.exception)
        self.assertIn("could not parse any of 2 files", message)
        self.assertIn("a.py", message)
        self.assertIn("b.py", message)

    def test_single_bad_file_error_unchanged(self):
        # Task 65 is folders only: one bad file on its own must
        # raise exactly the error parse_file raises today.
        path = self._write("solo.py", "def broken(:\n")
        with self.assertRaises(PixelForgeError) as ctx_file:
            parse_file(path)
        with self.assertRaises(PixelForgeError) as ctx_paths:
            parse_paths([path])
        self.assertEqual(str(ctx_file.exception),
                         str(ctx_paths.exception))

    def test_unreadable_file_skipped(self):
        # A file that cannot be read is skipped like a bad one.
        # (chmod 0 does not work as root, so the OSError is
        # simulated with a mock instead.)
        from pathlib import Path
        from unittest import mock
        folder = os.path.join(self.tmp, "locked")
        os.mkdir(folder)
        with open(os.path.join(folder, "good.py"), "w") as handle:
            handle.write("ONE = [[1]]\n")
        with open(os.path.join(folder, "locked.py"), "w") as handle:
            handle.write("TWO = [[2]]\n")
        orig = Path.read_text

        def flaky(self, *args, **kwargs):
            if self.name == "locked.py":
                raise OSError("permission denied")
            return orig(self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", flaky):
            result = parse_paths([folder])
        names = [a["name"] for a in result["assets"]]
        self.assertEqual(names, ["ONE"])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("locked.py", result["skipped"][0]["file"])
        self.assertIn("could not read", result["skipped"][0]["reason"])

    def test_default_int_colors_cycle_pico8(self):
        path = self._write("cycle.py", "BIG = [[17, 18]]\n")
        result = parse_file(path)
        big = by_name(result, "BIG")
        # 17 -> PICO-8[0] (black), 18 -> PICO-8[1] (dark blue).
        self.assertEqual(pixel(big["image"], 0, 0), (0, 0, 0, 255))
        self.assertEqual(pixel(big["image"], 1, 0), (29, 43, 83, 255))


class JsonInputTests(unittest.TestCase):
    """Task 46: JSON sprite input — Aseprite frames, pixel lists,
    palettes, and the shapes real tools emit."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def _write_json(self, name, doc):
        return self._write(name, json.dumps(doc))

    def test_aseprite_dict_frames(self):
        write_sheet(os.path.join(self.tmp, "sheet.png"))
        doc = {
            "frames": {
                "hero 0.aseprite": {
                    "frame": {"x": 0, "y": 0, "w": 32, "h": 32}},
                "hero 1.aseprite": {
                    "frame": {"x": 32, "y": 0, "w": 32, "h": 32}},
            },
            "meta": {"image": "sheet.png"},
            "frameTags": [{"name": "idle", "from": 0, "to": 1}],
        }
        path = self._write_json("hero.json", doc)
        result = parse_file(path)
        first = by_name(result, "hero 0")
        second = by_name(result, "hero 1")
        # The trailing ".aseprite" is stripped from frame names.
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["kind"], "frame")
        self.assertEqual((first["width"], first["height"]),
                         (32, 32))
        self.assertEqual(first["detail"], "frame from sheet.png")
        self.assertEqual(pixel(first["image"], 0, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(second["image"], 0, 0),
                         (0, 0, 255, 255))

    def test_aseprite_list_frames(self):
        write_sheet(os.path.join(self.tmp, "sheet.png"))
        doc = {
            "frames": [
                {"filename": "a.png",
                 "frame": {"x": 0, "y": 0, "w": 16, "h": 16}},
                {"filename": "b.png",
                 "frame": {"x": 16, "y": 0, "w": 16, "h": 16}},
            ],
            "meta": {"image": "sheet.png"},
        }
        path = self._write_json("list.json", doc)
        result = parse_file(path)
        self.assertIsNotNone(by_name(result, "a.png"))
        self.assertIsNotNone(by_name(result, "b.png"))
        self.assertEqual(by_name(result, "a.png")["kind"], "frame")

    def test_generic_frames_with_image_key(self):
        write_sheet(os.path.join(self.tmp, "sheet.png"))
        doc = {
            "frames": [{"name": "idle", "x": 0, "y": 0,
                        "w": 16, "h": 16}],
            "image": "sheet.png",
        }
        path = self._write_json("generic.json", doc)
        result = parse_file(path)
        frame = by_name(result, "idle")
        self.assertIsNotNone(frame)
        self.assertEqual(frame["kind"], "frame")
        self.assertEqual((frame["width"], frame["height"]),
                         (16, 16))

    def test_pixels_plus_palette(self):
        doc = {"pixels": [[0, 1], [1, 0]],
               "palette": ["#000000", "#ffffff"]}
        path = self._write_json("sprite.json", doc)
        result = parse_file(path)
        sprite = by_name(result, "sprite")
        self.assertIsNotNone(sprite)
        self.assertEqual(sprite["kind"], "map")
        self.assertEqual((sprite["width"], sprite["height"]),
                         (2, 2))
        # Index 1 is the first palette color; 0 is transparent.
        self.assertEqual(pixel(sprite["image"], 1, 0),
                         (0, 0, 0, 255))
        palette = by_name(result, "palette")
        self.assertIsNotNone(palette)
        self.assertEqual(palette["kind"], "palette")
        self.assertEqual(palette["colors"], 2)

    def test_int_keyed_palette_dict(self):
        # JSON object keys are strings — digit keys must read as
        # an int palette, not a char legend.
        doc = {"pixels": [[0, 1], [1, 0]],
               "palette": {"0": "#000000", "1": "#ffffff"}}
        path = self._write_json("indexed.json", doc)
        result = parse_file(path)
        sprite = by_name(result, "indexed")
        self.assertIsNotNone(sprite)
        self.assertEqual(pixel(sprite["image"], 1, 0),
                         (255, 255, 255, 255))

    def test_flat_pixels_with_width_height(self):
        doc = {"pixels": [0, 1, 1, 0], "width": 2, "height": 2}
        path = self._write_json("flat.json", doc)
        result = parse_file(path)
        sprite = by_name(result, "flat")
        self.assertIsNotNone(sprite)
        self.assertEqual(sprite["kind"], "map")
        self.assertEqual((sprite["width"], sprite["height"]),
                         (2, 2))

    def test_top_level_rows(self):
        path = self._write_json("rows.json", [[0, 1], [1, 0]])
        result = parse_file(path)
        sprite = by_name(result, "rows")
        self.assertIsNotNone(sprite)
        self.assertEqual(sprite["kind"], "map")

    def test_top_level_palette(self):
        path = self._write_json("pal.json", ["#ff0000", "#00ff00"])
        result = parse_file(path)
        palette = by_name(result, "pal")
        self.assertIsNotNone(palette)
        self.assertEqual(palette["kind"], "palette")
        self.assertEqual(palette["colors"], 2)

    def test_named_sprites_dict(self):
        doc = {"hero": [[1, 0], [0, 1]], "coin": [[1]]}
        path = self._write_json("named.json", doc)
        result = parse_file(path)
        hero = by_name(result, "hero")
        coin = by_name(result, "coin")
        self.assertIsNotNone(hero)
        self.assertIsNotNone(coin)
        self.assertEqual((hero["width"], hero["height"]), (2, 2))
        self.assertEqual((coin["width"], coin["height"]), (1, 1))

    def test_invalid_json_fails_plainly(self):
        path = self._write("broken.json", "{not json")
        with self.assertRaises(PixelForgeError) as ctx:
            parse_file(path)
        message = str(ctx.exception)
        self.assertIn("could not parse", message)
        self.assertIn("broken.json", message)
        self.assertIn("(line 1)", message)

    def test_frames_without_sheet_are_noted(self):
        doc = {"frames": {"a": {"frame": {"x": 0, "y": 0,
                                          "w": 8, "h": 8}}}}
        path = self._write_json("nosheet.json", doc)
        result = parse_file(path)
        self.assertEqual(result["assets"], [])
        note = by_note(result, "a")
        self.assertIsNotNone(note)
        self.assertIn("no sheet image", note["reason"])

    def test_frames_with_missing_sheet_are_noted(self):
        doc = {"frames": {"a": {"frame": {"x": 0, "y": 0,
                                          "w": 8, "h": 8}}},
               "meta": {"image": "gone.png"}}
        path = self._write_json("missingsheet.json", doc)
        result = parse_file(path)
        note = by_note(result, "a")
        self.assertIsNotNone(note)
        self.assertIn("image file not found: gone.png",
                      note["reason"])

    def test_frame_outside_sheet_is_noted(self):
        write_sheet(os.path.join(self.tmp, "sheet.png"))
        doc = {"frames": {"far": {"frame": {"x": 1000, "y": 0,
                                            "w": 8, "h": 8}}},
               "meta": {"image": "sheet.png"}}
        path = self._write_json("outside.json", doc)
        result = parse_file(path)
        note = by_note(result, "far")
        self.assertIsNotNone(note)
        self.assertIn("outside the sheet", note["reason"])

    def test_image_key_without_frames_is_an_image(self):
        write_sheet(os.path.join(self.tmp, "sheet.png"))
        doc = {"image": "sheet.png"}
        path = self._write_json("onlyimage.json", doc)
        result = parse_file(path)
        asset = by_name(result, "onlyimage")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")
        self.assertEqual((asset["width"], asset["height"]),
                         (64, 32))


class CSharpTests(unittest.TestCase):
    """Task 46: C# (Unity) detection."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _parse(self, source):
        from pathlib import Path
        return parse_source(source, Path("Hero.cs"), "cs")

    def test_color_and_color32(self):
        result = self._parse(
            "Color tint = new Color(1f, 0f, 0f, 1f);\n"
            "Color32 px = new Color32(255, 128, 0, 255);\n")
        tint = by_name(result, "tint")
        px = by_name(result, "px")
        self.assertIsNotNone(tint)
        self.assertEqual(tint["kind"], "color")
        self.assertEqual(tint["detail"], "#ff0000")
        self.assertIsNotNone(px)
        self.assertEqual(px["detail"], "#ff8000")

    def test_static_colors(self):
        result = self._parse(
            "Color b = Color.blue;\n"
            "Color g = Color.green;\n"
            "Color c = Color.clear;\n")
        self.assertEqual(by_name(result, "b")["detail"], "#0000ff")
        # Unity green is (0, 255, 0), not the CSS dark green.
        self.assertEqual(by_name(result, "g")["detail"], "#00ff00")
        self.assertEqual(by_name(result, "c")["detail"],
                         "rgba(0, 0, 0, 0)")

    def test_color_array_is_palette(self):
        result = self._parse(
            "Color[] palette = new Color[] "
            "{ Color.red, Color.green };\n")
        palette = by_name(result, "palette")
        self.assertIsNotNone(palette)
        self.assertEqual(palette["kind"], "palette")
        self.assertEqual(palette["colors"], 2)

    def test_rect_is_a_note(self):
        result = self._parse(
            "Rect frame = new Rect(0, 0, 32, 32);\n")
        self.assertIsNone(by_name(result, "frame"))
        note = by_note(result, "frame")
        self.assertIsNotNone(note)
        self.assertEqual(note["reason"],
                         "rectangle 32 x 32 at (0, 0), "
                         "a frame region, not pixels")

    def test_resources_load_finds_image(self):
        folder = os.path.join(self.tmp, "unity")
        os.makedirs(os.path.join(folder, "sprites"))
        write_sheet(os.path.join(folder, "sprites", "hero.png"))
        path = os.path.join(folder, "Hero.cs")
        with open(path, "w") as handle:
            handle.write(
                'Sprite s = Resources.Load<Sprite>("sprites/hero");\n')
        result = parse_file(path)
        asset = by_name(result, "s")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")
        self.assertEqual(asset["detail"], "image: sprites/hero")
        self.assertEqual((asset["width"], asset["height"]),
                         (64, 32))

    def test_resources_load_missing_is_a_note(self):
        result = self._parse(
            'Sprite s = Resources.Load<Sprite>("sprites/hero");\n')
        self.assertIsNone(by_name(result, "s"))
        note = by_note(result, "s")
        self.assertIsNotNone(note)
        self.assertIn("image file not found: sprites/hero",
                      note["reason"])

    def test_int_array_map(self):
        result = self._parse(
            "int[,] hero = new int[,] { {1, 0}, {0, 1} };\n")
        hero = by_name(result, "hero")
        self.assertIsNotNone(hero)
        self.assertEqual(hero["kind"], "map")
        self.assertEqual((hero["width"], hero["height"]), (2, 2))

    def test_class_inheritance_is_not_an_assignment(self):
        # The ": MonoBehaviour" colon must not swallow the next
        # "=" and report the class name as the assignment.
        result = self._parse(
            "using UnityEngine;\n"
            "public class Hero : MonoBehaviour {\n"
            "    public Color tint = new Color(1f, 0f, 0f, 1f);\n"
            "}\n")
        self.assertIsNone(by_name(result, "Hero"))
        self.assertIsNotNone(by_name(result, "tint"))

    def test_negative(self):
        result = self._parse(
            "public class Foo {\n"
            "    public int count = 0;\n"
            "    public void Run() { count += 1; }\n"
            "}\n")
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["not_rendered"], [])


class GdscriptTests(unittest.TestCase):
    """Task 46: GDScript (Godot) detection."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _parse(self, source):
        from pathlib import Path
        return parse_source(source, Path("hero.gd"), "gd")

    def test_colors(self):
        result = self._parse(
            "var tint = Color(1, 0, 0)\n"
            "var px = Color8(0, 128, 255)\n"
            "var named = Color.RED\n")
        self.assertEqual(by_name(result, "tint")["detail"],
                         "#ff0000")
        self.assertEqual(by_name(result, "px")["detail"],
                         "#0080ff")
        self.assertEqual(by_name(result, "named")["detail"],
                         "#ff0000")

    def test_color_array_is_palette(self):
        result = self._parse(
            "var palette = [Color.RED, Color.BLUE]\n")
        palette = by_name(result, "palette")
        self.assertIsNotNone(palette)
        self.assertEqual(palette["kind"], "palette")
        self.assertEqual(palette["colors"], 2)

    def test_load_res_searches_upward(self):
        # res:// means the project root, above the script folder.
        project = os.path.join(self.tmp, "project")
        os.makedirs(os.path.join(project, "sprites"))
        os.makedirs(os.path.join(project, "scripts"))
        write_sheet(os.path.join(project, "sprites", "hero.png"))
        path = os.path.join(project, "scripts", "hero.gd")
        with open(path, "w") as handle:
            handle.write('var tex = load("res://sprites/hero.png")\n')
        result = parse_file(path)
        asset = by_name(result, "tex")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")
        self.assertEqual((asset["width"], asset["height"]),
                         (64, 32))

    def test_preload_relative(self):
        folder = os.path.join(self.tmp, "godot")
        os.mkdir(folder)
        write_sheet(os.path.join(folder, "hero.png"))
        path = os.path.join(folder, "hero.gd")
        with open(path, "w") as handle:
            handle.write('const TEX = preload("hero.png")\n')
        result = parse_file(path)
        asset = by_name(result, "TEX")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")

    def test_rect2_forms(self):
        result = self._parse(
            "var a = Rect2(0, 0, 32, 32)\n"
            "var b = Rect2(Vector2(1, 2), Vector2(16, 16))\n")
        note_a = by_note(result, "a")
        note_b = by_note(result, "b")
        self.assertIsNotNone(note_a)
        self.assertEqual(note_a["reason"],
                         "rectangle 32 x 32 at (0, 0), "
                         "a frame region, not pixels")
        self.assertIsNotNone(note_b)
        self.assertEqual(note_b["reason"],
                         "rectangle 16 x 16 at (1, 2), "
                         "a frame region, not pixels")

    def test_negative(self):
        result = self._parse(
            "extends Node2D\n"
            "var count = 0\n"
            "func _ready():\n"
            "\tcount += 1\n")
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["not_rendered"], [])


class LuaTests(unittest.TestCase):
    """Task 46: Lua (Love2D) detection."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _parse(self, source):
        from pathlib import Path
        return parse_source(source, Path("main.lua"), "lua")

    def test_float_color_tables(self):
        result = self._parse(
            "local tint = {1, 0, 0}\n"
            "local warm = {1, 0.5, 0, 1}\n")
        self.assertEqual(by_name(result, "tint")["detail"],
                         "#ff0000")
        self.assertEqual(by_name(result, "warm")["detail"],
                         "#ff8000")

    def test_new_image(self):
        folder = os.path.join(self.tmp, "love")
        os.mkdir(folder)
        write_sheet(os.path.join(folder, "hero.png"))
        path = os.path.join(folder, "main.lua")
        with open(path, "w") as handle:
            handle.write(
                'local img = love.graphics.newImage("hero.png")\n')
        result = parse_file(path)
        asset = by_name(result, "img")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")
        self.assertEqual((asset["width"], asset["height"]),
                         (64, 32))

    def test_new_image_missing_is_a_note(self):
        result = self._parse(
            'local img = love.graphics.newImage("gone.png")\n')
        note = by_note(result, "img")
        self.assertIsNotNone(note)
        self.assertIn("image file not found: gone.png",
                      note["reason"])

    def test_new_quad_is_a_rect(self):
        result = self._parse(
            "local q = love.graphics.newQuad(0, 0, 32, 32, 64, 64)\n")
        note = by_note(result, "q")
        self.assertIsNotNone(note)
        self.assertEqual(note["reason"],
                         "rectangle 32 x 32 at (0, 0), "
                         "a frame region, not pixels")

    def test_float_palette(self):
        result = self._parse(
            "local palette = {{1.0, 0, 0}, {0, 1.0, 0}}\n")
        palette = by_name(result, "palette")
        self.assertIsNotNone(palette)
        self.assertEqual(palette["kind"], "palette")
        self.assertEqual(palette["colors"], 2)

    def test_int_only_nested_tables_read_as_map(self):
        # {{1, 0, 0}, {0, 1, 0}} is textually identical to a
        # 2 x 3 map of palette indices; with no float in it,
        # the map reading wins (documented in the module).
        result = self._parse(
            "local palette = {{1, 0, 0}, {0, 1, 0}}\n")
        asset = by_name(result, "palette")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "map")

    def test_negative(self):
        result = self._parse(
            "local count = 0\n"
            "function love.load()\n"
            "  count = count + 1\n"
            "end\n")
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["not_rendered"], [])


class RecursionTests(unittest.TestCase):
    """Task 46: a deeply-nested hostile file is a plain refusal,
    never a raw RecursionError reaching the GUI."""

    def _parse(self, source, name, language):
        from pathlib import Path
        return parse_source(source, Path(name), language)

    def test_deeply_nested_json(self):
        with self.assertRaises(PixelForgeError) as ctx:
            self._parse("[" * 3000 + "]" * 3000, "deep.json",
                        "json")
        self.assertIn("nested too deeply", str(ctx.exception))

    def test_deeply_nested_python(self):
        # ast.parse raises SyntaxError ("too many nested
        # parentheses") on this shape; either way it must land
        # as a PixelForgeError, never raw.
        with self.assertRaises(PixelForgeError):
            self._parse("x = " + "[" * 2000 + "]" * 2000,
                        "deep.py", "py")

    def test_deeply_nested_js(self):
        with self.assertRaises(PixelForgeError) as ctx:
            self._parse("const x = " + "[" * 2000 + "]" * 2000
                        + ";", "deep.js", "js")
        self.assertIn("nested too deeply", str(ctx.exception))

    def test_deeply_nested_lua(self):
        with self.assertRaises(PixelForgeError) as ctx:
            self._parse("local x = " + "{" * 2000 + "}" * 2000,
                        "deep.lua", "lua")
        self.assertIn("nested too deeply", str(ctx.exception))

    def test_deeply_nested_csharp(self):
        with self.assertRaises(PixelForgeError) as ctx:
            self._parse("var x = new int[] " + "{" * 2000
                        + "}" * 2000 + ";", "deep.cs", "cs")
        self.assertIn("nested too deeply", str(ctx.exception))


class FileOrderTests(unittest.TestCase):
    """Task 46: detected items appear in the order they occur in
    the file. ast.walk is breadth-first, so a nested-scope
    assignment after a module-level one used to come out first."""

    def _parse(self, source, name, language):
        from pathlib import Path
        return parse_source(source, Path(name), language)

    def test_python_nested_function_scope(self):
        result = self._parse(
            "def make():\n"
            "    INNER = [[1]]\n"
            "LATER = [[2]]\n",
            "order.py", "py")
        names = [a["name"] for a in result["assets"]]
        self.assertEqual(names, ["INNER", "LATER"])

    def test_python_class_body(self):
        result = self._parse(
            "class C:\n"
            "    FIRST = [[1]]\n"
            "SECOND = [[2]]\n",
            "order.py", "py")
        names = [a["name"] for a in result["assets"]]
        self.assertEqual(names, ["FIRST", "SECOND"])

    def test_js_order(self):
        result = self._parse(
            "const FIRST = [[1]];\n"
            "const SECOND = [[2]];\n",
            "order.js", "js")
        names = [a["name"] for a in result["assets"]]
        self.assertEqual(names, ["FIRST", "SECOND"])

    def test_loader_ref_slots_into_file_order(self):
        tmp = tempfile.mkdtemp()
        write_sheet(os.path.join(tmp, "hero.png"))
        path = os.path.join(tmp, "Order.cs")
        with open(path, "w") as handle:
            handle.write(
                "Color first = Color.red;\n"
                'Sprite s = Resources.Load<Sprite>("hero");\n'
                "Color second = Color.blue;\n")
        result = parse_file(path)
        names = [a["name"] for a in result["assets"]]
        self.assertEqual(names, ["first", "s", "second"])


class JsTimingTests(unittest.TestCase):
    """Task 46: the JS scan is linear now. A 10,000-assignment
    file took 1.29 s before (40,000 took 6.49 s); the bound here
    is generous so it is not flaky under load."""

    def test_10k_assignments_parse_fast(self):
        source = "".join("const a%d = %d;\n" % (i, i)
                         for i in range(10000))
        from pathlib import Path
        start = time.monotonic()
        parse_source(source, Path("big.js"), "js")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 10.0)
        # And the scan really saw all of them (a bare int is not
        # an asset, so check the detector directly).
        assignments, _refs = _js_assignments(source)
        self.assertEqual(len(assignments), 10000)
        self.assertEqual(assignments[-1][0], "a9999")


class IntColorTests(unittest.TestCase):
    """Task 46: 0xAARRGGBB int colors (alpha first), with
    0xRRGGBB unchanged."""

    def test_aarrggbb_with_alpha(self):
        self.assertEqual(parse_color(0x80FF0000),
                         (255, 0, 0, 128))
        self.assertEqual(parse_color(0xFF00FF00),
                         (0, 255, 0, 255))

    def test_rrggbb_unchanged(self):
        self.assertEqual(parse_color(0xFF0000), (255, 0, 0, 255))
        self.assertEqual(parse_color(0), (0, 0, 0, 255))
        self.assertEqual(parse_color(0xFFFFFF),
                         (255, 255, 255, 255))

    def test_out_of_range_is_not_a_color(self):
        self.assertIsNone(parse_color(-1))
        self.assertIsNone(parse_color(0x100000000))

    def test_hex_string_stays_rrggbbaa(self):
        # The same digits mean different things by form: an
        # 8-digit hex STRING is RRGGBBAA (CSS), an int is
        # AARRGGBB (C#/Java).
        self.assertEqual(parse_color("#ff000080"),
                         (255, 0, 0, 128))
        self.assertEqual(parse_color(0xFF000080),
                         (0, 0, 128, 255))

    def test_aarrggbb_in_a_palette(self):
        from pathlib import Path
        result = parse_source("PAL = [0x80FF0000]\n",
                              Path("x.py"), "py")
        pal = by_name(result, "PAL")
        self.assertIsNotNone(pal)
        self.assertEqual(pal["kind"], "palette")
        self.assertEqual(pixel(pal["image"], 0, 0),
                         (255, 0, 0, 128))


class NamedColorTests(unittest.TestCase):
    """Task 46: the seven new named colors, standard CSS values."""

    EXPECTED = {
        "skyblue": (135, 206, 235),
        "crimson": (220, 20, 60),
        "violet": (238, 130, 238),
        "turquoise": (64, 224, 208),
        "coral": (255, 127, 80),
        "khaki": (240, 230, 140),
        "indigo": (75, 0, 130),
    }

    def test_all_seven_pinned(self):
        for name, rgb in self.EXPECTED.items():
            self.assertEqual(NAMED_COLORS[name], rgb)
            self.assertEqual(parse_color(name), rgb + (255,))


class SizeGuardTests(unittest.TestCase):
    """Task 46: files over the size limit are refused BEFORE
    parsing, with the size named."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def test_over_limit_refused(self):
        path = os.path.join(self.tmp, "huge.py")
        body = "ONE = [[1]]\n"
        pad = "#" * (MAX_CODE_FILE_BYTES - len(body) + 1) + "\n"
        with open(path, "w") as handle:
            handle.write(pad + body)
        self.assertGreater(os.path.getsize(path),
                           MAX_CODE_FILE_BYTES)
        with self.assertRaises(PixelForgeError) as ctx:
            parse_file(path)
        message = str(ctx.exception)
        self.assertIn("too big to read", message)
        self.assertIn("huge.py", message)
        self.assertIn("limit", message)

    def test_at_limit_accepted(self):
        path = os.path.join(self.tmp, "max.py")
        body = "ONE = [[1]]\n"
        pad = "#" * (MAX_CODE_FILE_BYTES - len(body) - 1) + "\n"
        with open(path, "w") as handle:
            handle.write(pad + body)
        self.assertEqual(os.path.getsize(path), MAX_CODE_FILE_BYTES)
        result = parse_file(path)
        self.assertIsNotNone(by_name(result, "ONE"))


class ImageRefTests(unittest.TestCase):
    """Task 46: image-file references from code are followed
    (relative to the code file) and load into the preview."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _write(self, folder, name, text):
        path = os.path.join(folder, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_python_string_path_loads(self):
        folder = os.path.join(self.tmp, "pyref")
        os.mkdir(folder)
        write_sheet(os.path.join(folder, "hero.png"))
        path = self._write(folder, "game.py", 'HERO = "hero.png"\n')
        result = parse_file(path)
        asset = by_name(result, "HERO")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")
        self.assertEqual(asset["detail"], "image: hero.png")
        self.assertEqual((asset["width"], asset["height"]),
                         (64, 32))
        self.assertEqual(pixel(asset["image"], 0, 0),
                         (255, 0, 0, 255))

    def test_missing_file_is_a_note(self):
        path = self._write(self.tmp, "gone.py",
                           'HERO = "gone.png"\n')
        result = parse_file(path)
        self.assertIsNone(by_name(result, "HERO"))
        note = by_note(result, "HERO")
        self.assertIsNotNone(note)
        self.assertIn("image file not found: gone.png",
                      note["reason"])
        self.assertIn("gone.py", note["reason"])

    def test_absolute_path_not_followed(self):
        # The file EXISTS at that absolute path — it is still
        # not followed.
        folder = os.path.join(self.tmp, "abs")
        os.mkdir(folder)
        target = write_sheet(os.path.join(folder, "hero.png"))
        path = self._write(folder, "game.py",
                           'HERO = "%s"\n' % target)
        result = parse_file(path)
        self.assertIsNone(by_name(result, "HERO"))
        note = by_note(result, "HERO")
        self.assertIsNotNone(note)
        self.assertIn("absolute path, not followed",
                      note["reason"])

    def test_windows_drive_path_not_followed(self):
        path = self._write(self.tmp, "win.py",
                           'HERO = "C:\\\\art\\\\hero.png"\n')
        result = parse_file(path)
        note = by_note(result, "HERO")
        self.assertIsNotNone(note)
        self.assertIn("absolute path, not followed",
                      note["reason"])

    def test_parent_folder_traversal(self):
        folder = os.path.join(self.tmp, "parent")
        sub = os.path.join(folder, "sub")
        os.makedirs(sub)
        write_sheet(os.path.join(folder, "hero.png"))
        path = self._write(sub, "code.py",
                           'HERO = "../hero.png"\n')
        result = parse_file(path)
        asset = by_name(result, "HERO")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "image")

    def test_non_image_string_ignored(self):
        path = self._write(self.tmp, "plain.py",
                           'TITLE = "My Game"\n'
                           'PATH = "sprites/hero.txt"\n')
        result = parse_file(path)
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["not_rendered"], [])

    def test_query_string_stripped(self):
        folder = os.path.join(self.tmp, "query")
        os.mkdir(folder)
        write_sheet(os.path.join(folder, "hero.png"))
        path = self._write(folder, "game.py",
                           'HERO = "hero.png?v=2"\n')
        result = parse_file(path)
        self.assertIsNotNone(by_name(result, "HERO"))


class RectTests(unittest.TestCase):
    """Task 46: a single rectangle is a note, never a palette."""

    def _parse(self, source):
        from pathlib import Path
        return parse_source(source, Path("x.py"), "py")

    def test_rect_dict_is_a_note(self):
        result = self._parse(
            'FRAME = {"x": 0, "y": 0, "w": 32, "h": 32}\n')
        self.assertIsNone(by_name(result, "FRAME"))
        note = by_note(result, "FRAME")
        self.assertIsNotNone(note)
        self.assertEqual(note["reason"],
                         "rectangle 32 x 32 at (0, 0), "
                         "a frame region, not pixels")

    def test_width_height_keys(self):
        result = self._parse(
            'FRAME = {"x": 1, "y": 2, "width": 16, "height": 16}\n')
        note = by_note(result, "FRAME")
        self.assertIsNotNone(note)
        self.assertEqual(note["reason"],
                         "rectangle 16 x 16 at (1, 2), "
                         "a frame region, not pixels")

    def test_rect_is_not_a_palette_for_maps(self):
        result = self._parse(
            'FRAME = {"x": 0, "y": 0, "w": 32, "h": 32}\n'
            "HERO = [[1, 0], [0, 1]]\n")
        hero = by_name(result, "HERO")
        self.assertIsNotNone(hero)
        self.assertIn("default colors", hero["detail"])
        self.assertNotIn("FRAME", hero["detail"])


class ColorAssetTests(unittest.TestCase):
    """Task 46: a single color is a swatch asset."""

    def _parse(self, source):
        from pathlib import Path
        return parse_source(source, Path("x.py"), "py")

    def test_bare_tuple_is_a_color(self):
        result = self._parse("TINT = (255, 0, 0)\n")
        asset = by_name(result, "TINT")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "color")
        self.assertEqual(asset["detail"], "#ff0000")
        self.assertEqual((asset["width"], asset["height"]), (8, 8))
        self.assertEqual(pixel(asset["image"], 0, 0),
                         (255, 0, 0, 255))

    def test_tuple_with_alpha(self):
        result = self._parse("TINT = (255, 0, 0, 128)\n")
        asset = by_name(result, "TINT")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["detail"], "rgba(255, 0, 0, 128)")

    def test_hex_string_is_a_color(self):
        result = self._parse('TINT = "#ff0000"\n')
        asset = by_name(result, "TINT")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "color")

    def test_named_string_is_a_color(self):
        result = self._parse('TINT = "red"\n')
        asset = by_name(result, "TINT")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["detail"], "#ff0000")

    def test_bare_int_is_not_a_color(self):
        result = self._parse("MAX_HEALTH = 100\n")
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["not_rendered"], [])

    def test_plain_word_is_not_a_color(self):
        # "abc" is three hex letters, but it is not written as
        # a color — no swatch.
        result = self._parse('WORD = "abc"\n')
        self.assertEqual(result["assets"], [])

    def test_flat_int_list_still_a_palette(self):
        result = self._parse("PAL = [255, 0, 0]\n")
        asset = by_name(result, "PAL")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["kind"], "palette")


class FirstAssignmentTests(unittest.TestCase):
    """The textual scan must find the first assignment even when
    nothing but whitespace precedes it (an empty "last char"
    used to read as a comparison and skip it)."""

    def _parse(self, source, name, language):
        from pathlib import Path
        return parse_source(source, Path(name), language)

    def test_js_first_at_offset_zero(self):
        result = self._parse(
            'const A = ["#ff0000"];\nconst B = ["#00ff00"];\n',
            "x.js", "js")
        self.assertIsNotNone(by_name(result, "A"))
        self.assertIsNotNone(by_name(result, "B"))

    def test_csharp_first_at_offset_zero(self):
        result = self._parse("Color c = Color.red;\n", "x.cs", "cs")
        self.assertIsNotNone(by_name(result, "c"))

    def test_gdscript_first_at_offset_zero(self):
        result = self._parse("var c = Color.RED\n", "x.gd", "gd")
        self.assertIsNotNone(by_name(result, "c"))

    def test_lua_first_at_offset_zero(self):
        result = self._parse("local c = {1, 0, 0}\n", "x.lua", "lua")
        self.assertIsNotNone(by_name(result, "c"))


if __name__ == "__main__":
    unittest.main()
