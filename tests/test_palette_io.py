"""Tests for palette file import/export + the user palette store
(task 39). Bare-python: no Qt, no fixtures on disk — the sample
files are built in the test, including a tiny .ase writer."""
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.palette_io import (  # noqa: E402
    PaletteStore, parse_ase, parse_gpl, parse_hex, parse_pal,
    parse_txt, read_palette_file, write_gpl, write_hex,
    write_palette_file)
from pixel_forge.palettes import NAMED_PALETTES  # noqa: E402
from pixel_forge.pixelize import PixelForgeError  # noqa: E402


def _ase_string(text):
    """An ASE length-prefixed UTF-16BE string (length counts the
    NUL terminator)."""
    encoded = text.encode("utf-16-be") + b"\x00\x00"
    return struct.pack(">H", len(text) + 1) + encoded


def _ase_color(name, model, values):
    """One ASE color-entry block."""
    payload = _ase_string(name) + model + b"".join(
        struct.pack(">f", value) for value in values)
    return struct.pack(">HI", 0x0001, len(payload)) + payload


def _ase_file(blocks):
    """An ASE file holding the given blocks."""
    return (b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks))
            + b"".join(blocks))


class HexParserTests(unittest.TestCase):
    def test_lf_and_crlf_and_hash(self):
        # Lospec's own .hex files use CRLF; both line endings
        # and both spellings must parse.
        _name, colors = parse_hex(
            "#2e222f\r\n3e3546\r\n\r\n#625565\r\n", "test.hex")
        self.assertEqual(colors, [(46, 34, 47), (62, 53, 70),
                                  (98, 85, 101)])

    def test_bad_line_names_the_line(self):
        with self.assertRaises(PixelForgeError) as caught:
            parse_hex("2e222f\nnotacolor\n", "test.hex")
        self.assertIn("line 2", str(caught.exception))
        self.assertIn("test.hex", str(caught.exception))

    def test_short_hex_refuses(self):
        with self.assertRaises(PixelForgeError):
            parse_hex("abc\n", "test.hex")


class TxtParserTests(unittest.TestCase):
    def test_name_after_space_or_comma(self):
        _name, colors = parse_txt(
            "2e222f Dark\n3e3546, Purple\n625565\n", "test.txt")
        self.assertEqual(colors, [(46, 34, 47), (62, 53, 70),
                                  (98, 85, 101)])

    def test_hash_prefix_accepted(self):
        _name, colors = parse_txt("#2e222f\n", "test.txt")
        self.assertEqual(colors, [(46, 34, 47)])


class GplParserTests(unittest.TestCase):
    SAMPLE = """GIMP Palette
Name: My Palette
Columns: 4
# A comment line
 46  34  47 Dark
 62  53  70 Purple
 98  85 101
"""

    def test_header_comments_and_names(self):
        name, colors = parse_gpl(self.SAMPLE, "test.gpl")
        self.assertEqual(name, "My Palette")
        self.assertEqual(colors, [(46, 34, 47), (62, 53, 70),
                                  (98, 85, 101)])

    def test_missing_header_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            parse_gpl("46 34 47\n", "test.gpl")
        self.assertIn("not a GIMP palette", str(caught.exception))

    def test_bad_rgb_line_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            parse_gpl("GIMP Palette\n46 34\n", "test.gpl")
        self.assertIn("line 2", str(caught.exception))

    def test_channel_out_of_range_refuses(self):
        with self.assertRaises(PixelForgeError):
            parse_gpl("GIMP Palette\n46 34 999\n", "test.gpl")


class PalParserTests(unittest.TestCase):
    SAMPLE = "JASC-PAL\n0100\n3\n46 34 47\n62 53 70\n98 85 101\n"

    def test_valid(self):
        _name, colors = parse_pal(self.SAMPLE, "test.pal")
        self.assertEqual(colors, [(46, 34, 47), (62, 53, 70),
                                  (98, 85, 101)])

    def test_wrong_signature_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            parse_pal("NOT-A-PALETTE\n0100\n0\n", "test.pal")
        self.assertIn("not a JASC palette", str(caught.exception))

    def test_count_mismatch_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            parse_pal("JASC-PAL\n0100\n3\n46 34 47\n", "test.pal")
        self.assertIn("says it has 3 colors but has 1",
                      str(caught.exception))


class AseParserTests(unittest.TestCase):
    def test_rgb_and_cmyk_entries(self):
        # One group holding one RGB entry (kept) and one CMYK
        # entry (ignored) — the group name becomes the palette
        # name, the RGB float 1.0/0.5/0.0 becomes (255, 128, 0).
        group_start = (struct.pack(
            ">HI", 0xC001, len(_ase_string("Sunset")))
            + _ase_string("Sunset"))
        group_end = struct.pack(">HI", 0xC002, 0)
        data = _ase_file([
            group_start,
            _ase_color("Orange", b"RGB ", (1.0, 0.5, 0.0)),
            _ase_color("Print", b"CMYK", (0.0, 0.5, 1.0, 0.0)),
            group_end,
        ])
        name, colors = parse_ase(data, "test.ase")
        self.assertEqual(name, "Sunset")
        self.assertEqual(colors, [(255, 128, 0)])

    def test_wrong_signature_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            parse_ase(b"NOPE" + b"\x00" * 8, "test.ase")
        self.assertIn("not an Adobe Swatch Exchange file",
                      str(caught.exception))

    def test_no_rgb_refuses(self):
        data = _ase_file([
            _ase_color("Print", b"CMYK", (0.0, 0.5, 1.0, 0.0)),
        ])
        with self.assertRaises(PixelForgeError) as caught:
            parse_ase(data, "test.ase")
        self.assertIn("no RGB colors", str(caught.exception))
        self.assertIn("1 non-RGB", str(caught.exception))

    def test_truncated_refuses(self):
        data = _ase_file([
            _ase_color("Orange", b"RGB ", (1.0, 0.5, 0.0)),
        ])
        with self.assertRaises(PixelForgeError):
            parse_ase(data[:-3], "test.ase")


class ReadPaletteFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, content, binary=False):
        path = os.path.join(self.dir, name)
        mode = "wb" if binary else "w"
        with open(path, mode) as handle:
            handle.write(content)
        return path

    def test_name_defaults_to_stem(self):
        path = self._write("my-palette.hex", "2e222f\n")
        name, colors = read_palette_file(path)
        self.assertEqual(name, "my-palette")
        self.assertEqual(colors, [(46, 34, 47)])

    def test_unknown_extension_refuses(self):
        path = self._write("palette.png", "2e222f\n")
        with self.assertRaises(PixelForgeError) as caught:
            read_palette_file(path)
        self.assertIn("unknown palette format", str(caught.exception))

    def test_empty_file_refuses(self):
        path = self._write("empty.hex", "\n\n")
        with self.assertRaises(PixelForgeError) as caught:
            read_palette_file(path)
        self.assertIn("no colors found", str(caught.exception))

    def test_257_colors_refuses_naming_the_count(self):
        lines = "".join("%06x\n" % i for i in range(257))
        path = self._write("big.hex", lines)
        with self.assertRaises(PixelForgeError) as caught:
            read_palette_file(path)
        self.assertIn("257 colors", str(caught.exception))

    def test_256_colors_accepted(self):
        lines = "".join("%06x\n" % i for i in range(256))
        path = self._write("max.hex", lines)
        _name, colors = read_palette_file(path)
        self.assertEqual(len(colors), 256)

    def test_duplicates_kept(self):
        # The file is the authority — dedupe is the editor's
        # job, not the importer's.
        path = self._write("dup.hex", "2e222f\n2e222f\n")
        _name, colors = read_palette_file(path)
        self.assertEqual(colors, [(46, 34, 47), (46, 34, 47)])

    def test_ase_round_trip_through_file(self):
        data = _ase_file([
            _ase_color("Orange", b"RGB ", (1.0, 0.5, 0.0)),
        ])
        path = self._write("swatches.ase", data, binary=True)
        name, colors = read_palette_file(path)
        self.assertEqual(name, "swatches")
        self.assertEqual(colors, [(255, 128, 0)])


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_hex_round_trip(self):
        path = os.path.join(self.dir, "out.hex")
        write_hex(path, "Mine", [(46, 34, 47), (62, 53, 70)])
        name, colors = read_palette_file(path)
        self.assertEqual(name, "out")
        self.assertEqual(colors, [(46, 34, 47), (62, 53, 70)])

    def test_gpl_round_trip_keeps_name(self):
        path = os.path.join(self.dir, "out.gpl")
        write_gpl(path, "Mine", [(46, 34, 47), (62, 53, 70)])
        name, colors = read_palette_file(path)
        self.assertEqual(name, "Mine")
        self.assertEqual(colors, [(46, 34, 47), (62, 53, 70)])

    def test_write_by_extension(self):
        path = os.path.join(self.dir, "out.gpl")
        write_palette_file(path, "Mine", [(46, 34, 47)])
        _name, colors = read_palette_file(path)
        self.assertEqual(colors, [(46, 34, 47)])

    def test_unknown_export_extension_refuses(self):
        path = os.path.join(self.dir, "out.ase")
        with self.assertRaises(PixelForgeError) as caught:
            write_palette_file(path, "Mine", [(46, 34, 47)])
        self.assertIn("use .hex or .gpl", str(caught.exception))


class BundledPaletteTests(unittest.TestCase):
    """The bundled Lospec palettes, pinned against the .hex
    files they were copied from (counts, first, middle, and
    last colors — verified against
    ~/workspace/pf-test-assets/palettes/*.hex)."""

    def test_resurrect_64(self):
        colors = NAMED_PALETTES["resurrect64"]
        self.assertEqual(len(colors), 64)
        self.assertEqual(colors[0], (46, 34, 47))       # 2e222f
        self.assertEqual(colors[32], (145, 219, 105))   # 91db69
        self.assertEqual(colors[-1], (253, 203, 176))   # fdcbb0

    def test_endesga_32(self):
        colors = NAMED_PALETTES["endesga32"]
        self.assertEqual(len(colors), 32)
        self.assertEqual(colors[0], (190, 74, 47))      # be4a2f
        self.assertEqual(colors[16], (18, 78, 137))     # 124e89
        self.assertEqual(colors[-1], (194, 133, 105))   # c28569

    def test_sweetie_16(self):
        colors = NAMED_PALETTES["sweetie16"]
        self.assertEqual(len(colors), 16)
        self.assertEqual(colors[0], (26, 28, 44))       # 1a1c2c
        self.assertEqual(colors[8], (41, 54, 111))      # 29366f
        self.assertEqual(colors[-1], (51, 60, 87))      # 333c57


class PaletteStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PaletteStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load_round_trip(self):
        self.store.save("Sunset", [(46, 34, 47), (62, 53, 70)])
        fresh = PaletteStore(self.tmp.name)
        self.assertEqual(fresh.user_palettes(),
                         {"Sunset": [(46, 34, 47), (62, 53, 70)]})
        self.assertEqual(fresh.get("Sunset"),
                         [(46, 34, 47), (62, 53, 70)])
        # The file stores hex strings, like the spec.
        with open(os.path.join(self.tmp.name,
                               "palettes.json")) as handle:
            data = json.loads(handle.read())
        self.assertEqual(
            data["palettes"]["Sunset"]["colors"],
            ["#2e222f", "#3e3546"])

    def test_builtin_lookup(self):
        self.assertEqual(self.store.get("pico8", builtin=True),
                         NAMED_PALETTES["pico8"])
        self.assertIsNone(self.store.get("pico8"))
        colors, builtin = self.store.lookup("gameboy")
        self.assertTrue(builtin)
        self.assertEqual(colors, NAMED_PALETTES["gameboy"])
        self.assertIsNone(self.store.lookup("image"))
        self.assertIsNone(self.store.lookup("no such palette"))

    def test_find_is_case_insensitive(self):
        self.store.save("Sunset", [(46, 34, 47)])
        self.assertEqual(self.store.find("sunset"), "Sunset")
        self.assertEqual(self.store.find("SUNSET"), "Sunset")
        self.assertIsNone(self.store.find("dawn"))

    def test_save_replaces_case_insensitively(self):
        self.store.save("Sunset", [(46, 34, 47)])
        self.store.save("sunset", [(1, 2, 3)])
        self.assertEqual(self.store.user_palettes(),
                         {"sunset": [(1, 2, 3)]})

    def test_builtin_name_refuses(self):
        for name in ("pico8", "PICO-8", "Resurrect 64",
                     "image", "From image", "sweetie16"):
            with self.assertRaises(PixelForgeError) as caught:
                self.store.save(name, [(1, 2, 3)])
            self.assertIn("built-in palette name",
                          str(caught.exception))

    def test_skip_bad_entry_keeps_good_ones(self):
        # The v0.21 rule: one bad entry is skipped with a reason,
        # never drops the rest.
        path = os.path.join(self.tmp.name, "palettes.json")
        payload = {"format": "pixel-forge-palettes", "version": 1,
                   "palettes": {
                       "Good": {"colors": ["#2e222f"]},
                       "Broken": {"colors": ["not a color"]},
                       "pico8": {"colors": ["#000000"]},
                   }}
        with open(path, "w") as handle:
            json.dump(payload, handle)
        store = PaletteStore(self.tmp.name)
        self.assertEqual(store.user_palettes(),
                         {"Good": [(46, 34, 47)]})
        skipped = dict(store.skipped_palettes())
        self.assertIn("Broken", skipped)
        self.assertIn("pico8", skipped)
        self.assertIn("built-in", skipped["pico8"])
        # Saving keeps the skipped entries verbatim in the file.
        store.save("New", [(1, 2, 3)])
        with open(path) as handle:
            data = json.loads(handle.read())
        self.assertEqual(data["palettes"]["Broken"],
                         {"colors": ["not a color"]})
        self.assertEqual(data["palettes"]["pico8"],
                         {"colors": ["#000000"]})

    def test_unparseable_file_raises(self):
        path = os.path.join(self.tmp.name, "palettes.json")
        with open(path, "w") as handle:
            handle.write("{not json")
        with self.assertRaises(PixelForgeError):
            PaletteStore(self.tmp.name).user_palettes()

    def test_wrong_format_raises(self):
        path = os.path.join(self.tmp.name, "palettes.json")
        with open(path, "w") as handle:
            json.dump({"format": "something-else"}, handle)
        with self.assertRaises(PixelForgeError):
            PaletteStore(self.tmp.name).user_palettes()

    def test_rename(self):
        self.store.save("Sunset", [(46, 34, 47)])
        self.store.rename("Sunset", "Dawn")
        self.assertEqual(self.store.user_palettes(),
                         {"Dawn": [(46, 34, 47)]})

    def test_rename_to_existing_refuses(self):
        self.store.save("Sunset", [(46, 34, 47)])
        self.store.save("Dawn", [(1, 2, 3)])
        with self.assertRaises(PixelForgeError):
            self.store.rename("Sunset", "dawn")

    def test_rename_builtin_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            self.store.rename("pico8", "Mine")
        self.assertIn("cannot be renamed", str(caught.exception))

    def test_delete(self):
        self.store.save("Sunset", [(46, 34, 47)])
        self.store.delete("Sunset")
        self.assertEqual(self.store.user_palettes(), {})

    def test_delete_builtin_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            self.store.delete("pico8")
        self.assertIn("cannot be deleted", str(caught.exception))

    def test_delete_missing_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            self.store.delete("nope")
        self.assertIn("no palette named", str(caught.exception))

    def test_import_file(self):
        path = os.path.join(self.tmp.name, "mine.hex")
        with open(path, "w") as handle:
            handle.write("2e222f\n3e3546\n")
        name = self.store.import_file(path)
        self.assertEqual(name, "mine")
        self.assertEqual(self.store.get("mine"),
                         [(46, 34, 47), (62, 53, 70)])

    def test_import_file_with_name_and_overwrite(self):
        path = os.path.join(self.tmp.name, "mine.hex")
        with open(path, "w") as handle:
            handle.write("2e222f\n")
        self.store.import_file(path, name="Custom")
        with self.assertRaises(PixelForgeError) as caught:
            self.store.import_file(path, name="custom")
        self.assertIn("already exists", str(caught.exception))
        self.store.import_file(path, name="custom", overwrite=True)
        self.assertEqual(self.store.get("custom"), [(46, 34, 47)])

    def test_import_bad_file_touches_nothing(self):
        path = os.path.join(self.tmp.name, "bad.hex")
        with open(path, "w") as handle:
            handle.write("not a color\n")
        with self.assertRaises(PixelForgeError):
            self.store.import_file(path)
        self.assertEqual(self.store.user_palettes(), {})

    def test_export_palette(self):
        self.store.save("Sunset", [(46, 34, 47)])
        out = os.path.join(self.tmp.name, "sunset.gpl")
        self.store.export_palette("Sunset", out)
        name, colors = read_palette_file(out)
        self.assertEqual(name, "Sunset")
        self.assertEqual(colors, [(46, 34, 47)])

    def test_export_builtin(self):
        out = os.path.join(self.tmp.name, "gb.hex")
        self.store.export_palette("gameboy", out, builtin=True)
        _name, colors = read_palette_file(out)
        self.assertEqual(colors, NAMED_PALETTES["gameboy"])

    def test_export_missing_refuses(self):
        out = os.path.join(self.tmp.name, "nope.hex")
        with self.assertRaises(PixelForgeError):
            self.store.export_palette("nope", out)

    def test_all_palettes_order(self):
        self.store.save("Zebra", [(1, 2, 3)])
        self.store.save("Apple", [(4, 5, 6)])
        found = self.store.all_palettes()
        names = [name for name, _c, _b in found]
        self.assertEqual(names[:6],
                         ["pico8", "gameboy", "grayscale",
                          "resurrect64", "endesga32", "sweetie16"])
        self.assertEqual(names[6:], ["Apple", "Zebra"])
        self.assertTrue(all(builtin for _n, _c, builtin in found[:6]))
        self.assertFalse(any(builtin for _n, _c, builtin in found[6:]))


class ExtractTests(unittest.TestCase):
    """extract_palette_from_image — the median-cut extraction
    the CLI and the GUI Extract button share."""

    def _image(self, pixels):
        from pixel_forge.image import Image, argb
        image = Image(len(pixels), 1)
        for x, (r, g, b, a) in enumerate(pixels):
            image.setPixel(x, 0, argb(r, g, b, a))
        return image

    def test_extracts_median_cut_colors(self):
        from pixel_forge.palette_io import extract_palette_from_image
        image = self._image([(255, 0, 0, 255)] * 4
                            + [(0, 0, 255, 255)] * 4)
        colors = extract_palette_from_image(image, 2)
        self.assertEqual(colors, [(0, 0, 255), (255, 0, 0)])

    def test_transparent_pixels_ignored(self):
        from pixel_forge.palette_io import extract_palette_from_image
        image = self._image([(255, 0, 0, 255), (0, 0, 0, 0)])
        colors = extract_palette_from_image(image, 16)
        self.assertEqual(colors, [(255, 0, 0)])

    def test_fully_transparent_gives_black(self):
        from pixel_forge.palette_io import extract_palette_from_image
        image = self._image([(0, 0, 0, 0)] * 4)
        self.assertEqual(extract_palette_from_image(image, 16),
                         [(0, 0, 0)])

    def test_bad_color_count_refuses(self):
        from pixel_forge.palette_io import extract_palette_from_image
        image = self._image([(255, 0, 0, 255)])
        for count in (0, 257):
            with self.assertRaises(PixelForgeError):
                extract_palette_from_image(image, count)


if __name__ == "__main__":
    unittest.main()
