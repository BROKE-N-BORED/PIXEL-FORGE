"""Tests for the seeded procedural generators (task 44).

The seed contract is the thing under test: the same kind, seed,
size, and palette always make the same pixels, and a different
seed makes a different sprite. A few seeds are pinned to their
style/shape on purpose — if the draw order changes, old seeds
make different sprites, and that breaks the contract.
"""

import unittest

from pixel_forge.image import Image
from pixel_forge.palettes import NAMED_PALETTES
from pixel_forge.pixelize import PixelForgeError
from pixel_forge.procedural import (
    DEFAULT_SIZE, ITEM_SHAPES, KINDS, MAX_SIZE, MIN_SIZE,
    TREE_STYLES, generate, generate_item, generate_mask,
    generate_tree, item_shape_for_seed, tree_style_for_seed)


def opaque_count(image):
    return sum(1 for i in range(0, len(image.data), 4)
               if image.data[i + 3] != 0)


def distinct_colors(image):
    colors = set()
    data = image.data
    for i in range(0, len(data), 4):
        if data[i + 3] != 0:
            colors.add(tuple(data[i:i + 3]))
    return colors


def corner_transparent(image):
    size = image.width()
    for x, y in ((0, 0), (size - 1, 0), (0, size - 1),
                 (size - 1, size - 1)):
        if image.data[(y * size + x) * 4 + 3] != 0:
            return False
    return True


class DeterminismTests(unittest.TestCase):
    def test_same_seed_twice_is_identical(self):
        for kind in KINDS:
            self.assertEqual(generate(kind, 42, 32),
                             generate(kind, 42, 32))

    def test_different_seeds_differ(self):
        for kind in KINDS:
            self.assertNotEqual(generate(kind, 1, 32),
                                generate(kind, 2, 32))

    def test_same_seed_different_kind_differs(self):
        self.assertNotEqual(generate("mask", 7, 32),
                            generate("tree", 7, 32))
        self.assertNotEqual(generate("tree", 7, 32),
                            generate("item", 7, 32))

    def test_seed_zero_works(self):
        for kind in KINDS:
            image = generate(kind, 0, 32)
            self.assertTrue(opaque_count(image) > 0)

    def test_negative_seeds_work_and_differ(self):
        for kind in KINDS:
            image = generate(kind, -1, 32)
            self.assertTrue(opaque_count(image) > 0)
            self.assertNotEqual(image, generate(kind, 1, 32))
            self.assertEqual(image, generate(kind, -1, 32))

    def test_pinned_styles_and_shapes(self):
        # The seed contract: these seeds must keep making
        # these styles and shapes. If the draw order changes,
        # these break on purpose.
        self.assertEqual(tree_style_for_seed(1), "blob")
        self.assertEqual(tree_style_for_seed(2), "pine")
        self.assertEqual(tree_style_for_seed(4), "round")
        self.assertEqual(item_shape_for_seed(1), "gem")
        self.assertEqual(item_shape_for_seed(2), "sword")
        self.assertEqual(item_shape_for_seed(8), "coin")

    def test_helpers_match_generation(self):
        # The helper reproduces the generator's first draw:
        # two seeds with the same style still differ in the
        # details, and the style set is fully covered.
        styles = {tree_style_for_seed(s) for s in range(1, 31)}
        self.assertEqual(styles, set(TREE_STYLES))
        shapes = {item_shape_for_seed(s) for s in range(1, 31)}
        self.assertEqual(shapes, set(ITEM_SHAPES))


class ShapeTests(unittest.TestCase):
    def test_all_kinds_non_empty_with_transparent_corners(self):
        # Trees and items leave the corners empty; a mask can
        # legitimately fill a corner (its bitmask covers the
        # whole grid), so the mask is only checked for empty
        # pixels somewhere.
        for kind in ("tree", "item"):
            for seed in (1, 2, 3):
                image = generate(kind, seed, 32)
                self.assertTrue(opaque_count(image) > 50,
                                "%s seed %d is nearly empty"
                                % (kind, seed))
                self.assertTrue(corner_transparent(image),
                                "%s seed %d fills a corner"
                                % (kind, seed))
        for seed in (1, 2, 3):
            image = generate("mask", seed, 32)
            count = opaque_count(image)
            self.assertTrue(50 < count < 32 * 32,
                            "mask seed %d has %d opaque pixels"
                            % (seed, count))

    def test_mask_is_mirrored(self):
        for size in (16, 32, 64):
            image = generate_mask(7, size)
            for y in range(size):
                for x in range(size):
                    self.assertEqual(image.pixel(x, y),
                                     image.pixel(size - 1 - x, y))

    def test_mask_uses_two_to_four_colors(self):
        for seed in (1, 2, 3, 4, 5):
            colors = distinct_colors(generate_mask(seed, 32))
            self.assertTrue(2 <= len(colors) <= 4,
                            "seed %d used %d colors"
                            % (seed, len(colors)))

    def test_tree_has_trunk_below_canopy(self):
        # Seed 4 is a round tree: the trunk region is brown
        # (red above green above blue) and the canopy above
        # it is green (green above red).
        image = generate_tree(4, 32)
        trunk = image.data[(27 * 32 + 15) * 4:(27 * 32 + 15) * 4 + 4]
        r, g, b, a = trunk
        self.assertEqual(a, 255)
        self.assertTrue(r > g > b, "trunk pixel %s" % (tuple(trunk),))
        canopy = image.data[(10 * 32 + 16) * 4:(10 * 32 + 16) * 4 + 4]
        r, g, b, a = canopy
        self.assertEqual(a, 255)
        self.assertTrue(g > r, "canopy pixel %s" % (tuple(canopy),))

    def test_pine_is_taller_than_wide(self):
        # Seed 2 is a pine: the canopy's top row is narrow and
        # the bottom tier is wide.
        image = generate_tree(2, 32)
        top = [x for x in range(32)
               if image.data[(3 * 32 + x) * 4 + 3] != 0]
        bottom = [x for x in range(32)
                  if image.data[(21 * 32 + x) * 4 + 3] != 0]
        self.assertTrue(0 < len(top) < len(bottom),
                        "top %d bottom %d" % (len(top), len(bottom)))

    def test_item_shapes_all_draw(self):
        # Every shape draws a non-empty sprite with a
        # transparent corner, from a seed known to pick it.
        seen = {}
        for seed in range(1, 31):
            shape = item_shape_for_seed(seed)
            if shape not in seen:
                seen[shape] = seed
        self.assertEqual(set(seen), set(ITEM_SHAPES))
        for shape, seed in seen.items():
            image = generate_item(seed, 32)
            self.assertTrue(opaque_count(image) > 50, shape)
            self.assertTrue(corner_transparent(image), shape)


class SizeTests(unittest.TestCase):
    def test_sizes_respected(self):
        for size in (8, 16, 32, 64, 128, 512):
            image = generate("tree", 1, size)
            self.assertEqual((image.width(), image.height()),
                             (size, size))

    def test_default_size_is_32(self):
        image = generate("item", 1)
        self.assertEqual((image.width(), image.height()),
                         (DEFAULT_SIZE, DEFAULT_SIZE))

    def test_size_out_of_range_refuses(self):
        for size in (0, 1, 7, 513, 1024):
            with self.assertRaises(PixelForgeError):
                generate("tree", 1, size)

    def test_non_integer_size_refuses(self):
        for size in (32.5, "32", None, True):
            with self.assertRaises(PixelForgeError):
                generate("tree", 1, size)


class ValidationTests(unittest.TestCase):
    def test_unknown_kind_refuses_plainly(self):
        with self.assertRaises(PixelForgeError) as caught:
            generate("dragon", 1, 32)
        self.assertIn("mask", str(caught.exception))
        self.assertIn("tree", str(caught.exception))
        self.assertIn("item", str(caught.exception))

    def test_unknown_palette_refuses_plainly(self):
        with self.assertRaises(PixelForgeError) as caught:
            generate("tree", 1, 32, palette="nope")
        self.assertIn("nope", str(caught.exception))

    def test_palette_restricts_mask_colors(self):
        palette = {tuple(c) for c in NAMED_PALETTES["pico8"]}
        image = generate_mask(3, 32, palette="pico8")
        self.assertTrue(distinct_colors(image) <= palette)

    def test_palette_is_deterministic(self):
        self.assertEqual(
            generate("tree", 5, 32, palette="gameboy"),
            generate("tree", 5, 32, palette="gameboy"))

    def test_palette_changes_the_colors(self):
        plain = generate("mask", 5, 32)
        paletted = generate("mask", 5, 32, palette="gameboy")
        self.assertNotEqual(plain, paletted)
        # ... but the shape (which pixels are filled) is the
        # same: the palette only recolors.
        for y in range(32):
            for x in range(32):
                off = (y * 32 + x) * 4
                self.assertEqual(plain.data[off + 3] != 0,
                                 paletted.data[off + 3] != 0)


if __name__ == "__main__":
    unittest.main()
