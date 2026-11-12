"""Palette unit tests — no Qt needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.palettes import (  # noqa: E402
    GAMEBOY, GRAYSCALE, NAMED_PALETTES, PICO8, median_cut, nearest_color,
    select_palette_colors)


class NamedPaletteTests(unittest.TestCase):
    def test_pico8_has_16_unique_colors(self):
        self.assertEqual(len(PICO8), 16)
        self.assertEqual(len(set(PICO8)), 16)

    def test_gameboy_has_4_unique_colors(self):
        self.assertEqual(len(GAMEBOY), 4)
        self.assertEqual(len(set(GAMEBOY)), 4)

    def test_grayscale_is_16_monotonic_shades(self):
        self.assertEqual(len(GRAYSCALE), 16)
        self.assertEqual(GRAYSCALE[0], (0, 0, 0))
        self.assertEqual(GRAYSCALE[-1], (255, 255, 255))
        values = [c[0] for c in GRAYSCALE]
        self.assertEqual(values, sorted(values))
        for r, g, b in GRAYSCALE:
            self.assertEqual(r, g)
            self.assertEqual(g, b)

    def test_named_palettes_registry(self):
        self.assertEqual(set(NAMED_PALETTES),
                         {"pico8", "gameboy", "grayscale"})


class SelectTests(unittest.TestCase):
    def test_no_selection_when_it_fits(self):
        colors = [(255, 0, 0)] * 10
        self.assertEqual(select_palette_colors(PICO8, colors, 16), PICO8)
        self.assertEqual(select_palette_colors(PICO8, colors, 256), PICO8)

    def test_keeps_the_colors_the_image_uses(self):
        # Regression: the old index cut kept a grey instead of the
        # green and dark purple this image maps to, and the picture
        # came out one flat color.
        colors = [(0, 255, 0)] * 25 + [(136, 0, 0)] * 75
        result = select_palette_colors(PICO8, colors, 4)
        self.assertIn((0, 228, 54), result)
        self.assertIn((126, 37, 83), result)
        self.assertLessEqual(len(result), 4)

    def test_most_used_first(self):
        colors = [(255, 255, 255)] * 90 + [(0, 0, 0)] * 10
        result = select_palette_colors(PICO8, colors, 1)
        self.assertEqual(result, [(255, 241, 232)])

    def test_ties_go_to_earliest_palette_entry(self):
        colors = [(255, 255, 255)] * 5 + [(0, 0, 0)] * 5
        result = select_palette_colors(PICO8, colors, 1)
        self.assertEqual(result, [(0, 0, 0)])

    def test_fewer_used_than_max_returns_only_used(self):
        colors = [(255, 0, 0)] * 10
        result = select_palette_colors(PICO8, colors, 4)
        self.assertEqual(len(result), 1)


class MedianCutTests(unittest.TestCase):
    def test_fewer_colors_than_max_returned_as_is(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        self.assertEqual(median_cut(colors, 16), sorted(colors))

    def test_respects_max_colors(self):
        colors = [(r, g, 128) for r in range(0, 256, 16)
                  for g in range(0, 256, 16)]
        result = median_cut(colors, 8)
        self.assertLessEqual(len(result), 8)
        self.assertGreater(len(result), 0)

    def test_deterministic(self):
        colors = [(r, g, b) for r in range(0, 256, 32)
                  for g in range(0, 256, 32) for b in (0, 128, 255)]
        self.assertEqual(median_cut(colors, 12), median_cut(colors, 12))

    def test_single_color_in_single_color_out(self):
        self.assertEqual(median_cut([(9, 9, 9)] * 50, 16), [(9, 9, 9)])


class NearestColorTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(nearest_color((255, 0, 0), PICO8), (255, 0, 77))

    def test_closest_wins(self):
        palette = [(0, 0, 0), (255, 255, 255)]
        self.assertEqual(nearest_color((10, 10, 10), palette), (0, 0, 0))
        self.assertEqual(nearest_color((250, 250, 250), palette),
                         (255, 255, 255))

    def test_cache_is_used(self):
        cache = {}
        nearest_color((10, 20, 30), PICO8, cache)
        self.assertIn((10, 20, 30), cache)
        first = nearest_color((10, 20, 30), PICO8, cache)
        second = nearest_color((10, 20, 30), PICO8, cache)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
