"""Code input tests — static parsing of .py/.js/.ts, no execution.

Sample code files are written to a temp dir and parsed through the
public API (parse_file / parse_paths). Pixel-exact checks read the
rendered QImage back pixel by pixel.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import ensure_app  # noqa: E402
from pixel_forge.codeinput import (parse_file, parse_paths,  # noqa: E402
                                   parse_source)
from pixel_forge.pixelize import PixelForgeError  # noqa: E402

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
    c = image.pixelColor(x, y)
    return c.red(), c.green(), c.blue(), c.alpha()


def by_name(result, name):
    for asset in result["assets"]:
        if asset["name"] == name:
            return asset
    return None


class PythonParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()
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
        ensure_app()
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
        ensure_app()
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
        self.assertIn("no .py, .js, or .ts files", str(ctx.exception))

    def test_default_int_colors_cycle_pico8(self):
        path = self._write("cycle.py", "BIG = [[17, 18]]\n")
        result = parse_file(path)
        big = by_name(result, "BIG")
        # 17 -> PICO-8[0] (black), 18 -> PICO-8[1] (dark blue).
        self.assertEqual(pixel(big["image"], 0, 0), (0, 0, 0, 255))
        self.assertEqual(pixel(big["image"], 1, 0), (29, 43, 83, 255))


if __name__ == "__main__":
    unittest.main()
