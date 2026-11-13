"""Core pixelizer tests.

Qt-free (task 64): runs on bare python as well as under the Qt
test env (see AGENTS.md).
"""
import os
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.palettes import PICO8  # noqa: E402

from helpers import (  # noqa: E402
    make_gradient_image, make_solid_image,
    make_transparent_image, write_image)
from pixel_forge.image import Image  # noqa: E402
from pixel_forge.pixelize import (  # noqa: E402
    PixelForgeError, compute_target_size, count_opaque_colors,
    downscale, load_image, opaque_pixels, pixelize_file,
    pixelize_image, quantize)

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

try:
    from PySide6.QtGui import QImage  # noqa: F401
    HAS_QT = True
except ImportError:
    HAS_QT = False


def make_two_color_image():
    """64x64: green quarter top-left, dark red everywhere else.

    The regression image for the palette bugs — two colors far
    apart, so a broken palette step shows up as one flat color.
    """
    image = Image(64, 64)
    image.fill(0xFF880000)
    for y in range(32):
        for x in range(32):
            image.setPixel(x, y, 0xFF00FF00)
    return image


class PixelizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def _gradient_png(self, name="gradient.png"):
        return write_image(make_gradient_image(),
                           os.path.join(self.tmp, name))

    # ------------------------------------------------------------ load

    def test_load_missing_file_fails_cleanly(self):
        with self.assertRaises(PixelForgeError) as caught:
            load_image(os.path.join(self.tmp, "nope.png"))
        self.assertIn("not found", str(caught.exception))

    def test_load_non_image_fails_cleanly(self):
        path = os.path.join(self.tmp, "words.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("this is not an image at all")
        with self.assertRaises(PixelForgeError) as caught:
            load_image(path)
        message = str(caught.exception)
        self.assertIn(str(path), message)
        if HAS_QT:
            self.assertIn("could not read as an image", message)
        else:
            # No Qt: only PNG loads, and the refusal names the fix.
            self.assertIn("is not a PNG", message)
            self.assertIn("Install PySide6", message)

    def test_load_damaged_png_fails_cleanly(self):
        # A damaged PNG fails the same way with and without Qt —
        # the signature is right, so the stdlib path tries to
        # decode it and fails like Qt does.
        from pixel_forge.pngio import PNG_SIGNATURE
        path = os.path.join(self.tmp, "damaged.png")
        with open(path, "wb") as fh:
            fh.write(PNG_SIGNATURE + b"garbagegarbagegarbage")
        with self.assertRaises(PixelForgeError) as caught:
            load_image(path)
        self.assertIn("could not read as an image",
                      str(caught.exception))

    @unittest.skipIf(HAS_QT, "needs the no-Qt refusal path")
    def test_jpg_without_qt_refuses_plainly(self):
        # Without Qt there is no JPEG decoder: a JPG input fails
        # with a plain message naming the file and the fix, never
        # a traceback.
        path = os.path.join(self.tmp, "photo.jpg")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0" + b"junk" * 10)
        with self.assertRaises(PixelForgeError) as caught:
            load_image(path)
        message = str(caught.exception)
        self.assertIn("is not a PNG", message)
        self.assertIn(str(path), message)
        self.assertIn("Install PySide6", message)

    @unittest.skipUnless(HAS_QT, "PySide6 not available")
    def test_jpg_loads(self):
        # Image.save writes PNG only, so the JPG fixture is
        # written through Qt — the load under test is
        # load_image's Qt path.
        from helpers import ensure_app
        from pixel_forge.qtimage import to_qimage
        ensure_app()
        path = os.path.join(self.tmp, "photo.jpg")
        self.assertTrue(to_qimage(make_gradient_image(80, 60))
                        .save(path, "JPG"))
        image = load_image(path)
        self.assertEqual((image.width(), image.height()), (80, 60))

    @unittest.skipUnless(HAS_QT, "PySide6 not available")
    def test_gif_first_frame_loads(self):
        # Qt reads GIF but cannot write it, so the fixture is a real
        # 1x1 GIF (base64) written as raw bytes.
        import base64
        gif_bytes = base64.b64decode(
            "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        path = os.path.join(self.tmp, "pixel.gif")
        with open(path, "wb") as fh:
            fh.write(gif_bytes)
        image = load_image(path)
        self.assertEqual((image.width(), image.height()), (1, 1))

    # -------------------------------------------------------- downscale

    def test_downscale_exact_size(self):
        image = make_gradient_image(200, 100)
        small = downscale(image, 32, 32)
        self.assertEqual((small.width(), small.height()), (32, 32))

    def test_downscale_custom_width_height(self):
        image = make_gradient_image(200, 100)
        small = downscale(image, 64, 48)
        self.assertEqual((small.width(), small.height()), (64, 48))

    def test_keep_aspect_fits_the_box(self):
        self.assertEqual(compute_target_size(200, 100, 32, 32, True),
                         (32, 16))
        self.assertEqual(compute_target_size(100, 200, 32, 32, True),
                         (16, 32))
        self.assertEqual(compute_target_size(200, 100, 64, 48, True),
                         (64, 32))

    def test_keep_aspect_never_below_one_pixel(self):
        self.assertEqual(compute_target_size(1000, 1, 32, 32, True),
                         (32, 1))

    def test_downscale_keep_aspect(self):
        image = make_gradient_image(200, 100)
        small = downscale(image, 32, 32, keep_aspect=True)
        self.assertEqual((small.width(), small.height()), (32, 16))

    def test_zero_size_rejected(self):
        with self.assertRaises(PixelForgeError):
            compute_target_size(100, 100, 0, 32, False)

    def test_zero_source_rejected_keep_aspect(self):
        # B11: a 0 x 0 source with keep-aspect used to divide by
        # zero (ZeroDivisionError); it is a plain error now.
        for src_w, src_h in ((0, 0), (0, 32), (32, 0)):
            with self.assertRaises(PixelForgeError) as ctx:
                compute_target_size(src_w, src_h, 32, 32, True)
            self.assertIn("%d x %d" % (src_w, src_h), str(ctx.exception))

    def test_zero_source_rejected_without_keep_aspect(self):
        # No division happens without keep-aspect, but a 0-size
        # source still has no size to compute from.
        for src_w, src_h in ((0, 0), (0, 32), (32, 0)):
            with self.assertRaises(PixelForgeError):
                compute_target_size(src_w, src_h, 32, 32, False)

    def test_negative_source_rejected(self):
        for src_w, src_h in ((-1, 32), (32, -5), (-2, -2)):
            with self.assertRaises(PixelForgeError) as ctx:
                compute_target_size(src_w, src_h, 32, 32, True)
            self.assertIn("%d x %d" % (src_w, src_h), str(ctx.exception))

    def test_keep_aspect_known_sizes_unchanged(self):
        # The guard must not change any normal computation.
        self.assertEqual(compute_target_size(64, 48, 32, 32, True),
                         (32, 24))
        self.assertEqual(compute_target_size(64, 48, 64, 64, True),
                         (64, 48))
        self.assertEqual(compute_target_size(48, 64, 32, 32, True),
                         (24, 32))
        self.assertEqual(compute_target_size(1, 1, 32, 32, True),
                         (32, 32))

    def test_downscale_zero_size_image_rejected(self):
        # downscale routes through compute_target_size, so a
        # 0-size Image fails plainly instead of dividing by zero.
        from pixel_forge.image import Image
        with self.assertRaises(PixelForgeError):
            downscale(Image(0, 0), 32, 32, keep_aspect=True)

    # --------------------------------------------------------- quantize

    def test_palette_count_respected_from_image(self):
        image = make_gradient_image(200, 100)
        result = pixelize_image(image, 32, palette="image",
                                max_colors=4, dither=False)
        self.assertLessEqual(count_opaque_colors(result), 4)

    def test_palette_count_respected_named_palette(self):
        image = make_gradient_image(200, 100)
        result = pixelize_image(image, 32, palette="pico8",
                                max_colors=8, dither=False)
        self.assertLessEqual(count_opaque_colors(result), 8)

    def test_named_palette_colors_come_from_the_palette(self):
        image = make_gradient_image(200, 100)
        result = pixelize_image(image, 16, palette="pico8",
                                max_colors=16, dither=False)
        used = set(opaque_pixels(result))
        self.assertTrue(used <= set(PICO8))

    def test_named_palette_max_colors_keeps_used_colors(self):
        # Regression: PICO-8 at 4 colors used to cut the palette by
        # index and this picture came out one flat grey.
        image = make_two_color_image()
        result = pixelize_image(image, 64, palette="pico8",
                                max_colors=4, dither=False)
        used = set(opaque_pixels(result))
        self.assertIn((0, 228, 54), used)
        self.assertIn((126, 37, 83), used)

    def test_dithered_small_palette_stays_dark(self):
        # Regression: Floyd-Steinberg feedback ran away on tiny
        # palettes — flat red on Game Boy came out 95% lightest.
        image = make_two_color_image()
        result = pixelize_image(image, 64, palette="gameboy",
                                max_colors=4, dither=True)
        used = Counter(opaque_pixels(result))
        self.assertEqual(used.get((155, 188, 15), 0), 0)
        self.assertGreater(used[(15, 56, 15)], used[(139, 172, 15)])

    def test_dithered_grayscale_stays_dark(self):
        # Regression: flat red on grayscale dithered to a uniform
        # mid-grey under Floyd-Steinberg.
        image = make_two_color_image()
        result = pixelize_image(image, 64, palette="grayscale",
                                max_colors=4, dither=True)
        used = set(opaque_pixels(result))
        self.assertTrue(all(max(rgb) <= 85 for rgb in used))
        self.assertIn((51, 51, 51), used)
        self.assertIn((85, 85, 85), used)

    def test_transparency_preserved(self):
        image = make_transparent_image(64, 64)
        result = pixelize_image(image, 32, palette="image",
                                max_colors=16, dither=False)
        self.assertEqual((result.width(), result.height()), (32, 32))
        for y in range(32):
            for x in range(16, 32):
                self.assertEqual((result.pixel(x, y) >> 24) & 0xFF, 0,
                                 "transparent half must stay transparent")
        for y in range(32):
            for x in range(16):
                self.assertEqual((result.pixel(x, y) >> 24) & 0xFF, 255)

    def test_transparent_pixels_excluded_from_palette(self):
        image = make_transparent_image(64, 64)
        result = pixelize_image(image, 32, palette="image",
                                max_colors=16, dither=False)
        # Only one opaque color in the source, so only one out.
        self.assertEqual(count_opaque_colors(result), 1)

    def test_dithering_changes_output(self):
        image = make_gradient_image(200, 100)
        plain = pixelize_image(image, 32, palette="image",
                               max_colors=4, dither=False)
        dithered = pixelize_image(image, 32, palette="image",
                                  max_colors=4, dither=True)
        diffs = 0
        for y in range(32):
            for x in range(32):
                if plain.pixel(x, y) != dithered.pixel(x, y):
                    diffs += 1
        self.assertGreater(diffs, 0)

    def test_no_dither_is_deterministic(self):
        image = make_gradient_image(200, 100)
        first = pixelize_image(image, 32, palette="image",
                               max_colors=8, dither=False)
        second = pixelize_image(image, 32, palette="image",
                                max_colors=8, dither=False)
        for y in range(32):
            for x in range(32):
                self.assertEqual(first.pixel(x, y), second.pixel(x, y))

    def test_quantize_maps_to_palette(self):
        image = make_solid_image(8, 8, 255, 255, 255)
        result = quantize(image, PICO8, dither=False)
        self.assertEqual(count_opaque_colors(result), 1)

    # ------------------------------------------------------- validation

    def test_bad_color_counts_rejected(self):
        image = make_gradient_image(64, 64)
        for bad in (0, 1, 257, 1000):
            with self.assertRaises(PixelForgeError):
                pixelize_image(image, 16, max_colors=bad)

    def test_unknown_palette_rejected(self):
        image = make_gradient_image(64, 64)
        with self.assertRaises(PixelForgeError) as caught:
            pixelize_image(image, 16, palette="neon")
        self.assertIn("unknown palette", str(caught.exception))

    # ------------------------------------------------------------- file

    def test_pixelize_file_round_trip(self):
        src = self._gradient_png()
        out = os.path.join(self.tmp, "out.png")
        width, height, colors = pixelize_file(src, out, width=32,
                                              max_colors=8)
        self.assertEqual((width, height), (32, 32))
        self.assertLessEqual(colors, 8)
        back = load_image(out)
        self.assertFalse(back.isNull())
        self.assertEqual((back.width(), back.height()), (32, 32))

    def test_pixelize_file_missing_input(self):
        with self.assertRaises(PixelForgeError):
            pixelize_file(os.path.join(self.tmp, "missing.png"),
                          os.path.join(self.tmp, "out.png"))

    def test_pixelize_file_unwritable_output(self):
        src = self._gradient_png()
        with self.assertRaises(PixelForgeError) as caught:
            pixelize_file(src, os.path.join(self.tmp, "no-dir",
                                            "out.png"))
        self.assertIn("could not write", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
