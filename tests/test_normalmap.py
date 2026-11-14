"""Normal-map unit tests — no Qt needed (task 43).

The numbers are pinned against hand-computed Sobel values, not
against the implementation: a linear ramp's center pixel is
worked out in the test comments, so a scale or sign error in the
module fails the test instead of matching it.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.image import Image  # noqa: E402
from pixel_forge.normalmap import (  # noqa: E402
    DEFAULT_STRENGTH, FLAT_NORMAL, MAX_STRENGTH, MIN_STRENGTH,
    generate_normal_map, heightmap, luminance)
from pixel_forge.pixelize import PixelForgeError  # noqa: E402


def ramp_horizontal(width=32, height=8):
    """Dark on the left, bright on the right, linear."""
    img = Image(width, height)
    for y in range(height):
        for x in range(width):
            value = round(x * 255 / (width - 1))
            off = (y * width + x) * 4
            img.data[off:off + 4] = bytes((value, value, value, 255))
    return img


def ramp_vertical(width=8, height=32):
    """Dark at the top, bright at the bottom, linear."""
    img = Image(width, height)
    for y in range(height):
        for x in range(width):
            value = round(y * 255 / (height - 1))
            off = (y * width + x) * 4
            img.data[off:off + 4] = bytes((value, value, value, 255))
    return img


def pixel(image, x, y):
    off = (y * image.width() + x) * 4
    return tuple(image.data[off:off + 4])


def flat_pixels(image):
    """True when every pixel is the flat normal (128, 128, 255)."""
    data = image.data
    for index in range(0, len(data), 4):
        if tuple(data[index:index + 3]) != FLAT_NORMAL:
            return False
    return True


class FlatTests(unittest.TestCase):
    def test_flat_image_is_all_flat(self):
        img = Image(16, 16)
        img.fill(0xFF404040)
        result = generate_normal_map(img)
        self.assertTrue(flat_pixels(result))
        self.assertEqual(pixel(result, 0, 0), (128, 128, 255, 255))

    def test_single_color_image_is_all_flat(self):
        img = Image(8, 8)
        img.fill(0xFFFF0000)
        self.assertTrue(flat_pixels(generate_normal_map(img)))

    def test_one_pixel_image_is_flat(self):
        img = Image(1, 1)
        img.fill(0xFF123456)
        result = generate_normal_map(img)
        self.assertEqual(pixel(result, 0, 0), (128, 128, 255, 255))

    def test_one_pixel_wide_image_is_flat(self):
        # A 1-wide image has no slope anywhere: the clamped
        # neighbors are the pixel itself in x, and the column
        # is one color in y.
        img = Image(1, 16)
        img.fill(0xFF808080)
        self.assertTrue(flat_pixels(generate_normal_map(img)))

    def test_zero_size_raises_plain_error(self):
        with self.assertRaises(PixelForgeError):
            generate_normal_map(Image(0, 0))
        with self.assertRaises(PixelForgeError):
            generate_normal_map(Image(0, 5))

    def test_output_size_and_alpha_match_input(self):
        img = Image(6, 4)
        img.fill(0x80404040)
        result = generate_normal_map(img)
        self.assertEqual((result.width(), result.height()), (6, 4))
        self.assertEqual(pixel(result, 3, 2)[3], 0x80)


class RampTests(unittest.TestCase):
    def test_horizontal_ramp_points_left(self):
        # Dark left, bright right: the slope faces left, so red
        # drops below 128 and green stays flat. Center pixel,
        # worked out by hand: the Sobel sum at x=16 is
        # 4 * (140 - 123) = 68 (neighbors 123 and 140), slope
        # 68 / 2040 = 0.0333, nx = -0.0333, normalized -0.0333,
        # R = (-0.0333 * 0.5 + 0.5) * 255 = 123.25 -> 123.
        result = generate_normal_map(ramp_horizontal())
        self.assertEqual(pixel(result, 16, 4), (123, 128, 255, 255))

    def test_horizontal_ramp_red_below_flat_inside(self):
        result = generate_normal_map(ramp_horizontal())
        for x in range(2, 30):
            self.assertLess(pixel(result, x, 4)[0], 128)
            self.assertEqual(pixel(result, x, 4)[1], 128)

    def test_vertical_ramp_points_up(self):
        # Dark top, bright bottom: height increases going down
        # the image, so the slope faces up-screen — OpenGL
        # green-up means green ABOVE 128. Mirror of the
        # horizontal case: G = 255 - 123 = 132.
        result = generate_normal_map(ramp_vertical())
        self.assertEqual(pixel(result, 4, 16), (128, 132, 255, 255))

    def test_vertical_ramp_reversed_points_down(self):
        img = Image(8, 32)
        for y in range(32):
            for x in range(8):
                value = round((31 - y) * 255 / 31)
                off = (y * 8 + x) * 4
                img.data[off:off + 4] = bytes((value, value, value,
                                               255))
        result = generate_normal_map(img)
        self.assertEqual(pixel(result, 4, 16), (128, 123, 255, 255))

    def test_border_slope_is_half_the_interior(self):
        # Clamped neighbors: at the left edge the missing
        # neighbor is the edge pixel itself, so the slope is
        # half the interior slope — the border tilts, but less.
        result = generate_normal_map(ramp_horizontal())
        border_red = pixel(result, 0, 4)[0]
        inner_red = pixel(result, 16, 4)[0]
        self.assertLess(border_red, 128)
        self.assertGreater(border_red, inner_red)


class SphereTests(unittest.TestCase):
    def _sphere(self, size=33, radius=15):
        img = Image(size, size)
        center = size // 2
        for y in range(size):
            for x in range(size):
                dist = math.hypot(x - center, y - center)
                value = round(255 * max(0.0, 1.0 - dist / radius))
                off = (y * size + x) * 4
                img.data[off:off + 4] = bytes((value, value, value,
                                               255))
        return img

    def test_sphere_normals_point_outward(self):
        result = generate_normal_map(self._sphere())
        # Center is the top of the bump: flat.
        self.assertEqual(pixel(result, 16, 16)[:3], FLAT_NORMAL)
        # Top of the sphere faces up-screen: green above 128.
        self.assertGreater(pixel(result, 16, 9)[1], 128)
        # Bottom faces down-screen: green below 128.
        self.assertLess(pixel(result, 16, 23)[1], 128)
        # Left faces left: red below 128.
        self.assertLess(pixel(result, 9, 16)[0], 128)
        # Right faces right: red above 128.
        self.assertGreater(pixel(result, 23, 16)[0], 128)


class TransparencyTests(unittest.TestCase):
    def test_transparent_pixels_come_out_flat_and_clear(self):
        img = Image(4, 4)
        img.fill(0xFF808080)
        img.data[0:4] = bytes((0, 0, 0, 0))
        result = generate_normal_map(img)
        self.assertEqual(pixel(result, 0, 0), (128, 128, 255, 0))

    def test_opaque_neighbor_of_transparent_tilts(self):
        # The transparent pixel is height 0, so the opaque
        # pixel next to it sits on a real step — the
        # silhouette edge catches the light.
        img = Image(4, 4)
        img.fill(0xFF808080)
        img.data[0:4] = bytes((0, 0, 0, 0))
        result = generate_normal_map(img)
        self.assertNotEqual(pixel(result, 1, 0)[:3], FLAT_NORMAL)

    def test_half_transparent_height_is_half_luminance(self):
        img = Image(1, 1)
        img.data[0:4] = bytes((200, 200, 200, 128))
        heights = heightmap(img)
        self.assertAlmostEqual(
            heights[0], luminance(200, 200, 200) * 128 / 255.0,
            places=6)

    def test_alpha_is_preserved(self):
        img = Image(3, 3)
        img.fill(0xC0808080)
        result = generate_normal_map(img)
        for y in range(3):
            for x in range(3):
                self.assertEqual(pixel(result, x, y)[3], 0xC0)


class StrengthTests(unittest.TestCase):
    def _deviation(self, strength):
        result = generate_normal_map(ramp_horizontal(),
                                     strength=strength)
        return 128 - pixel(result, 16, 4)[0]

    def test_stronger_means_further_from_flat(self):
        self.assertLess(self._deviation(0.25), self._deviation(1.0))
        self.assertLess(self._deviation(1.0), self._deviation(4.0))

    def test_strength_is_monotonic(self):
        deviations = [self._deviation(s)
                      for s in (0.25, 0.5, 1.0, 2.0, 4.0)]
        self.assertEqual(deviations, sorted(deviations))
        self.assertEqual(len(set(deviations)), len(deviations))

    def test_strength_clamps_to_the_documented_range(self):
        self.assertEqual(
            generate_normal_map(ramp_horizontal(), strength=0.1),
            generate_normal_map(ramp_horizontal(),
                                strength=MIN_STRENGTH))
        self.assertEqual(
            generate_normal_map(ramp_horizontal(), strength=99.0),
            generate_normal_map(ramp_horizontal(),
                                strength=MAX_STRENGTH))

    def test_default_strength_is_one(self):
        self.assertEqual(DEFAULT_STRENGTH, 1.0)
        self.assertEqual(
            generate_normal_map(ramp_horizontal()),
            generate_normal_map(ramp_horizontal(), strength=1.0))


if __name__ == "__main__":
    unittest.main()
