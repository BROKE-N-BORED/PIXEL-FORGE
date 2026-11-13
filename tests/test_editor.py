"""Pixel editor raster tests — stdlib only, no Qt (task 33).

The pure operations behind the Image tab's editor: flood fill,
line and rectangle drawing, the distinct colors of a result,
and the eyedropper read. The GUI wiring lives in test_gui.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.editor import (distinct_colors, draw_line,  # noqa: E402
                                draw_rect_outline, flood_fill,
                                line_points, pick_color,
                                rect_points)
from pixel_forge.image import Image, argb  # noqa: E402

RED = argb(255, 0, 0)
GREEN = argb(0, 255, 0)
BLUE = argb(0, 0, 255)
WHITE = argb(255, 255, 255)
CLEAR = 0


def _image(width, height, value=0):
    image = Image(width, height)
    image.fill(value)
    return image


class DistinctColorsTests(unittest.TestCase):
    def test_first_appearance_order(self):
        image = _image(3, 1)
        image.setPixel(0, 0, BLUE)
        image.setPixel(1, 0, RED)
        image.setPixel(2, 0, BLUE)
        self.assertEqual(distinct_colors(image), [BLUE, RED])

    def test_transparent_pixels_are_not_a_color(self):
        image = _image(2, 1)
        image.setPixel(0, 0, RED)
        # (1, 0) stays transparent.
        self.assertEqual(distinct_colors(image), [RED])

    def test_empty_image_has_no_colors(self):
        self.assertEqual(distinct_colors(_image(4, 4)), [])


class PickColorTests(unittest.TestCase):
    def test_reads_the_pixel(self):
        image = _image(2, 2, RED)
        image.setPixel(1, 0, GREEN)
        self.assertEqual(pick_color(image, 1, 0), GREEN)
        self.assertEqual(pick_color(image, 0, 1), RED)

    def test_outside_is_none(self):
        image = _image(2, 2, RED)
        self.assertIsNone(pick_color(image, 2, 0))
        self.assertIsNone(pick_color(image, -1, 0))
        self.assertIsNone(pick_color(image, 0, 2))


class FloodFillTests(unittest.TestCase):
    def test_fills_the_connected_region_only(self):
        image = _image(5, 1, RED)
        image.setPixel(2, 0, BLUE)  # a wall across the row
        filled = flood_fill(image, 0, 0, GREEN)
        self.assertEqual(filled, 2)
        self.assertEqual(image.pixel(0, 0), GREEN)
        self.assertEqual(image.pixel(1, 0), GREEN)
        self.assertEqual(image.pixel(2, 0), BLUE)
        self.assertEqual(image.pixel(3, 0), RED)
        self.assertEqual(image.pixel(4, 0), RED)

    def test_fill_wraps_around_a_wall(self):
        # A U-shaped wall: the fill reaches the inside through
        # the open top.
        image = _image(5, 5, RED)
        for x in range(5):
            image.setPixel(x, 4, BLUE)
        for y in range(1, 5):
            image.setPixel(0, y, BLUE)
            image.setPixel(4, y, BLUE)
        # 25 pixels minus 11 wall pixels (the bottom row of 5
        # plus 3 new pixels on each side — the corners are
        # already in the bottom row).
        filled = flood_fill(image, 2, 0, GREEN)
        self.assertEqual(filled, 5 * 5 - 11)
        self.assertEqual(image.pixel(2, 2), GREEN)
        self.assertEqual(image.pixel(0, 4), BLUE)

    def test_already_matching_is_a_noop(self):
        image = _image(3, 3, RED)
        self.assertEqual(flood_fill(image, 1, 1, RED), 0)
        self.assertEqual(image.pixel(0, 0), RED)

    def test_outside_is_a_noop(self):
        image = _image(3, 3, RED)
        self.assertEqual(flood_fill(image, 3, 3, GREEN), 0)

    def test_whole_image_fills_without_recursion(self):
        # 512 x 512 through a recursive fill would blow the
        # stack; the iterative fill must land all of them.
        image = _image(512, 512, RED)
        self.assertEqual(flood_fill(image, 0, 0, GREEN),
                         512 * 512)
        self.assertEqual(image.pixel(511, 511), GREEN)


class LinePointsTests(unittest.TestCase):
    def test_horizontal_and_vertical(self):
        self.assertEqual(line_points(0, 0, 3, 0),
                         [(0, 0), (1, 0), (2, 0), (3, 0)])
        self.assertEqual(line_points(1, 1, 1, 3),
                         [(1, 1), (1, 2), (1, 3)])

    def test_diagonal(self):
        self.assertEqual(line_points(0, 0, 3, 3),
                         [(0, 0), (1, 1), (2, 2), (3, 3)])

    def test_steep_slope_has_no_gaps(self):
        points = line_points(0, 0, 2, 5)
        self.assertEqual(points[0], (0, 0))
        self.assertEqual(points[-1], (2, 5))
        # Every step moves at most one pixel in each axis.
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            self.assertLessEqual(abs(bx - ax), 1)
            self.assertLessEqual(abs(by - ay), 1)

    def test_reversed_direction(self):
        self.assertEqual(line_points(3, 0, 0, 0),
                         [(3, 0), (2, 0), (1, 0), (0, 0)])

    def test_single_point(self):
        self.assertEqual(line_points(2, 2, 2, 2), [(2, 2)])

    def test_draw_clips_to_the_image(self):
        image = _image(3, 3, RED)
        draw_line(image, -2, 1, 5, 1, GREEN)
        for x in range(3):
            self.assertEqual(image.pixel(x, 1), GREEN)
        self.assertEqual(image.pixel(0, 0), RED)


class RectPointsTests(unittest.TestCase):
    def test_outline_only(self):
        points = set(rect_points(1, 1, 3, 3))
        self.assertEqual(points, {
            (1, 1), (2, 1), (3, 1),
            (1, 2), (3, 2),
            (1, 3), (2, 3), (3, 3)})
        self.assertNotIn((2, 2), points)

    def test_either_drag_direction(self):
        self.assertEqual(set(rect_points(3, 3, 1, 1)),
                         set(rect_points(1, 1, 3, 3)))

    def test_one_pixel_wide_is_a_line(self):
        self.assertEqual(rect_points(1, 1, 1, 3),
                         [(1, 1), (1, 3), (1, 2)])

    def test_one_by_one_is_one_pixel(self):
        self.assertEqual(rect_points(2, 2, 2, 2), [(2, 2)])

    def test_draw_clips_to_the_image(self):
        image = _image(3, 3, RED)
        draw_rect_outline(image, -1, -1, 1, 1, GREEN)
        self.assertEqual(image.pixel(0, 0), RED)  # interior
        self.assertEqual(image.pixel(1, 0), GREEN)
        self.assertEqual(image.pixel(0, 1), GREEN)
        self.assertEqual(image.pixel(1, 1), GREEN)
        self.assertEqual(image.pixel(2, 2), RED)


class PencilAndEraserTests(unittest.TestCase):
    def test_pencil_sets_the_pixel(self):
        image = _image(2, 2, RED)
        image.setPixel(1, 1, GREEN)
        self.assertEqual(image.pixel(1, 1), GREEN)
        self.assertEqual(image.pixel(0, 0), RED)

    def test_eraser_sets_transparent(self):
        image = _image(2, 2, RED)
        image.setPixel(0, 1, CLEAR)
        self.assertEqual(image.pixel(0, 1), CLEAR)
        self.assertEqual(image.pixel(0, 0), RED)


if __name__ == "__main__":
    unittest.main()
