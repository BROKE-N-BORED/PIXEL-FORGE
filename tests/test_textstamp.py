"""Tests for text stamping (task 47).

The pixel font is data, so the tests pin it: a few glyphs
exact, the full set well-formed, and the stamp math (measure,
clip, color, scale) exact. The system-font render lives in
the GUI and is tested there — this module is Qt-free.
"""

import unittest

from pixel_forge.image import Image, argb
from pixel_forge.pixelize import PixelForgeError
from pixel_forge.textstamp import (
    GLYPH_ADVANCE, GLYPH_HEIGHT, GLYPH_WIDTH, MAX_SCALE, MIN_SCALE,
    glyph_for, measure_text, parse_hex_color, render_text,
    stamp_image, stamp_text, unsupported_characters)


def inked_pixels(image):
    """The set of (x, y) with any opacity."""
    found = set()
    for y in range(image.height()):
        for x in range(image.width()):
            if (image.pixel(x, y) >> 24) != 0:
                found.add((x, y))
    return found


class FontDataTests(unittest.TestCase):
    def test_font_covers_printable_ascii(self):
        import pixel_forge.textstamp as textstamp
        glyphs = textstamp._GLYPHS
        self.assertEqual(len(glyphs), 95)
        for code in range(32, 127):
            self.assertIn(chr(code), glyphs)

    def test_every_glyph_is_5_wide_7_tall(self):
        import pixel_forge.textstamp as textstamp
        for char, glyph in textstamp._GLYPHS.items():
            self.assertEqual(len(glyph), GLYPH_HEIGHT, char)
            for row in glyph:
                self.assertEqual(len(row), GLYPH_WIDTH, char)
                self.assertTrue(set(row) <= {"#", "."}, char)

    def test_glyph_a_is_exact(self):
        self.assertEqual(glyph_for("A"), (
            ".###.",
            "#...#",
            "#...#",
            "#####",
            "#...#",
            "#...#",
            "#...#"))

    def test_glyph_zero_is_exact(self):
        self.assertEqual(glyph_for("0"), (
            ".###.",
            "#...#",
            "#..##",
            "#.#.#",
            "##..#",
            "#...#",
            ".###."))

    def test_space_is_blank(self):
        self.assertEqual(glyph_for(" "), (".....",) * 7)

    def test_unknown_character_is_blank(self):
        self.assertEqual(glyph_for("é"), glyph_for(" "))
        self.assertEqual(glyph_for("☃"), glyph_for(" "))

    def test_unsupported_characters_names_them_in_order(self):
        self.assertEqual(unsupported_characters("café ☃"),
                         ["é", "☃"])
        self.assertEqual(unsupported_characters("HELLO"), [])
        self.assertEqual(unsupported_characters("éé"), ["é"])


class MeasureTests(unittest.TestCase):
    def test_one_character(self):
        self.assertEqual(measure_text("A"), (5, 7))

    def test_two_characters_share_no_trailing_space(self):
        self.assertEqual(measure_text("HI"), (11, 7))

    def test_scale_multiplies(self):
        self.assertEqual(measure_text("HI", 2), (22, 14))
        self.assertEqual(measure_text("HI", 4), (44, 28))

    def test_empty_text_is_zero_wide(self):
        self.assertEqual(measure_text(""), (0, 7))

    def test_scale_out_of_range_refused(self):
        for bad in (0, 5, -1, 2.5, True, "2"):
            with self.assertRaises(PixelForgeError):
                measure_text("A", bad)

    def test_line_break_refused(self):
        for bad in ("a\nb", "a\rb", "a\tb", "a\x07b"):
            with self.assertRaises(PixelForgeError):
                measure_text(bad)


class RenderTests(unittest.TestCase):
    def test_render_a_matches_the_glyph(self):
        mask = render_text("A")
        self.assertEqual((mask.width(), mask.height()), (5, 7))
        self.assertEqual(inked_pixels(mask),
                         {(1, 0), (2, 0), (3, 0),
                          (0, 1), (4, 1),
                          (0, 2), (4, 2),
                          (0, 3), (1, 3), (2, 3), (3, 3), (4, 3),
                          (0, 4), (4, 4),
                          (0, 5), (4, 5),
                          (0, 6), (4, 6)})

    def test_render_is_white_on_transparent(self):
        mask = render_text("I")
        self.assertEqual(mask.pixel(2, 0), argb(255, 255, 255))
        self.assertEqual(mask.pixel(0, 0), 0)

    def test_render_scale_doubles_each_font_pixel(self):
        small = render_text("I")
        big = render_text("I", 2)
        self.assertEqual((big.width(), big.height()), (10, 14))
        for y in range(7):
            for x in range(5):
                want = small.pixel(x, y) != 0
                for dy in range(2):
                    for dx in range(2):
                        self.assertEqual(
                            big.pixel(x * 2 + dx, y * 2 + dy) != 0,
                            want, (x, y, dx, dy))

    def test_render_two_characters_with_spacing(self):
        mask = render_text("II")
        self.assertEqual((mask.width(), mask.height()), (11, 7))
        # The spacing column between the two I glyphs is empty.
        for y in range(7):
            self.assertEqual(mask.pixel(5, y), 0)


class StampTests(unittest.TestCase):
    def test_stamp_paints_in_color(self):
        image = Image(32, 32)
        painted = stamp_text(image, "HI", 2, 3, argb(255, 0, 0))
        self.assertGreater(painted, 0)
        # H's top-left ink lands at (2, 3); the untouched
        # corner stays transparent.
        self.assertEqual(image.pixel(2, 3), argb(255, 0, 0))
        self.assertEqual(image.pixel(0, 0), 0)

    def test_stamp_paints_opaque_over_anything(self):
        image = Image(16, 16)
        image.fill(argb(0, 0, 255))
        stamp_text(image, "I", 0, 0, argb(255, 0, 0))
        self.assertEqual(image.pixel(2, 0), argb(255, 0, 0))
        # Pixels the glyph does not ink keep the old color.
        self.assertEqual(image.pixel(0, 0), argb(0, 0, 255))

    def test_stamp_clips_at_the_edge(self):
        image = Image(8, 8)
        painted = stamp_text(image, "HI", 4, 0, argb(0, 255, 0))
        self.assertGreater(painted, 0)
        self.assertLess(painted, len(inked_pixels(render_text("HI"))))

    def test_stamp_fully_outside_paints_nothing(self):
        image = Image(8, 8)
        self.assertEqual(
            stamp_text(image, "HI", 20, 20, argb(0, 255, 0)), 0)
        self.assertEqual(image, Image(8, 8))

    def test_stamp_negative_origin_clips(self):
        image = Image(8, 8)
        painted = stamp_text(image, "HI", -2, -2, argb(0, 255, 0))
        self.assertGreater(painted, 0)

    def test_stamp_empty_text_refused(self):
        with self.assertRaises(PixelForgeError):
            stamp_text(Image(8, 8), "", 0, 0, argb(0, 0, 0))

    def test_stamp_returns_the_painted_count(self):
        image = Image(32, 32)
        painted = stamp_text(image, "I", 0, 0, argb(0, 0, 0))
        self.assertEqual(painted, len(inked_pixels(render_text("I"))))


class StampImageTests(unittest.TestCase):
    def test_blits_any_mask_in_color(self):
        mask = Image(2, 2)
        mask.setPixel(0, 0, argb(255, 255, 255))
        mask.setPixel(1, 1, argb(128, 255, 255, 255))
        image = Image(4, 4)
        painted = stamp_image(image, mask, 1, 1, argb(0, 0, 255))
        self.assertEqual(painted, 2)
        self.assertEqual(image.pixel(1, 1), argb(0, 0, 255))
        # A half-opaque mask pixel still stamps fully opaque.
        self.assertEqual(image.pixel(2, 2), argb(0, 0, 255))
        self.assertEqual(image.pixel(2, 1), 0)

    def test_empty_mask_paints_nothing(self):
        self.assertEqual(
            stamp_image(Image(4, 4), Image(0, 0), 0, 0,
                        argb(0, 0, 0)), 0)


class ParseHexColorTests(unittest.TestCase):
    def test_hash_and_bare_forms(self):
        self.assertEqual(parse_hex_color("#ff8000"), (255, 128, 0))
        self.assertEqual(parse_hex_color("ff8000"), (255, 128, 0))
        self.assertEqual(parse_hex_color("  #00FF00 "),
                         (0, 255, 0))

    def test_bad_colors_are_none(self):
        for bad in ("", "#fff", "#ff80000", "xyz", "#gg0000",
                    "#ff80"):
            self.assertIsNone(parse_hex_color(bad), bad)


if __name__ == "__main__":
    unittest.main()
