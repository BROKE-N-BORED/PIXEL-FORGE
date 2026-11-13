"""Layer compositing tests — stdlib only, no Qt (task 35).

The pure operations behind the Image tab's layer stack: the
Porter-Duff "over" blend, compositing a stack bottom to top
with hidden layers skipped, and the content check the
delete-layer confirm uses. The GUI wiring lives in test_gui.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.image import Image, argb  # noqa: E402
from pixel_forge.layers import (Layer, blend_over,  # noqa: E402
                                composite_layers, has_content)
from pixel_forge.pixelize import PixelForgeError  # noqa: E402

RED = argb(255, 0, 0)
GREEN = argb(0, 255, 0)
BLUE = argb(0, 0, 255)
CLEAR = 0


def _image(width, height, value=0):
    image = Image(width, height)
    image.fill(value)
    return image


class BlendOverTests(unittest.TestCase):
    def test_opaque_over_wins(self):
        self.assertEqual(blend_over(BLUE, RED), RED)

    def test_transparent_over_leaves_base(self):
        self.assertEqual(blend_over(BLUE, CLEAR), BLUE)

    def test_over_transparent_is_over(self):
        self.assertEqual(blend_over(CLEAR, RED), RED)

    def test_two_transparent_stay_transparent(self):
        self.assertEqual(blend_over(CLEAR, CLEAR), 0)

    def test_half_over_opaque_mixes(self):
        # Half red over opaque blue: alpha 255, red 128, blue 127.
        half_red = (128 << 24) | 0xFF0000
        self.assertEqual(blend_over(BLUE, half_red), 0xFF80007F)

    def test_half_over_half(self):
        # Half red over half blue: alpha 191, red 170, blue 84.
        half_red = (128 << 24) | 0xFF0000
        half_blue = (128 << 24) | 0x0000FF
        self.assertEqual(blend_over(half_blue, half_red),
                         0xBFAA0054)


class CompositeLayersTests(unittest.TestCase):
    def test_order_matters(self):
        bottom = Layer("bottom", _image(2, 2, BLUE))
        top = Layer("top", _image(2, 2, RED))
        self.assertEqual(
            composite_layers([bottom, top]).pixel(0, 0), RED)
        self.assertEqual(
            composite_layers([top, bottom]).pixel(0, 0), BLUE)

    def test_hidden_layer_contributes_nothing(self):
        bottom = Layer("bottom", _image(2, 2, BLUE))
        top = Layer("top", _image(2, 2, RED), visible=False)
        out = composite_layers([bottom, top])
        self.assertEqual(out.pixel(0, 0), BLUE)
        self.assertEqual(out.pixel(1, 1), BLUE)

    def test_semi_transparent_blends(self):
        bottom = Layer("bottom", _image(1, 1, BLUE))
        half_red = (128 << 24) | 0xFF0000
        top = Layer("top", _image(1, 1, half_red))
        self.assertEqual(composite_layers([bottom, top]).pixel(0, 0),
                         blend_over(BLUE, half_red))

    def test_empty_stack_is_transparent(self):
        out = composite_layers([])
        self.assertTrue(out.isNull())
        self.assertEqual((out.width(), out.height()), (0, 0))

    def test_single_layer_passes_through(self):
        layer = Layer("only", _image(3, 3, GREEN))
        out = composite_layers([layer])
        self.assertEqual(out.pixel(1, 1), GREEN)
        # A copy, not the layer's own image: editing the
        # composite must never rewrite the layer.
        out.setPixel(1, 1, RED)
        self.assertEqual(layer.image.pixel(1, 1), GREEN)

    def test_mismatched_sizes_raise_plainly(self):
        small = Layer("small", _image(2, 2, RED))
        big = Layer("big", _image(3, 3, BLUE))
        with self.assertRaises(PixelForgeError):
            composite_layers([small, big])

    def test_inputs_untouched(self):
        bottom = Layer("bottom", _image(2, 2, BLUE))
        top = Layer("top", _image(2, 2, RED))
        composite_layers([bottom, top])
        self.assertEqual(bottom.image.pixel(0, 0), BLUE)
        self.assertEqual(top.image.pixel(0, 0), RED)


class LayerTests(unittest.TestCase):
    def test_defaults(self):
        layer = Layer("Background", _image(4, 4))
        self.assertEqual(layer.name, "Background")
        self.assertTrue(layer.visible)
        self.assertEqual(layer.image.width(), 4)


class HasContentTests(unittest.TestCase):
    def test_transparent_image_has_no_content(self):
        self.assertFalse(has_content(_image(4, 4)))

    def test_one_opaque_pixel_is_content(self):
        image = _image(4, 4)
        image.setPixel(2, 2, RED)
        self.assertTrue(has_content(image))

    def test_one_semi_transparent_pixel_is_content(self):
        image = _image(4, 4)
        image.setPixel(0, 0, (1 << 24) | 0xFF0000)
        self.assertTrue(has_content(image))


if __name__ == "__main__":
    unittest.main()
