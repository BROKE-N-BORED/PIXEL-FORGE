"""Minecraft skin tests — region extraction, figure, fallback.

Synthetic skins are built with one distinct color per region, so
every part can be checked pixel-exact. Region coordinates are the
standard skin layout (see skins.py).

Qt-free (task 64): runs without PySide6.
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import (make_gradient_image, make_noisy_image,  # noqa: E402
                     make_solid_image, write_image)
from pixel_forge.image import Image, argb  # noqa: E402
from pixel_forge.palettes import NAMED_PALETTES  # noqa: E402
from pixel_forge.pixelize import (PixelForgeError,  # noqa: E402
                                 count_opaque_colors, pixelize_image)
from pixel_forge.skins import (detect_skin, extract_skin,  # noqa: E402
                               pixelize_texture, skin_figure,
                               skin_parts, texture_target_size)

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

# Distinct colors per front-view region.
REGION_COLORS = {
    "head": (255, 0, 0), "hat": (0, 255, 0),
    "body": (0, 0, 255), "jacket": (255, 255, 0),
    "right_arm": (255, 0, 255), "right_sleeve": (0, 255, 255),
    "left_arm": (128, 0, 0), "left_sleeve": (0, 128, 0),
    "right_leg": (0, 0, 128), "right_pants": (128, 128, 0),
    "left_leg": (128, 0, 128), "left_pants": (0, 128, 128),
}

FRONTS = {
    "head": (8, 8, 8, 8), "hat": (40, 8, 8, 8),
    "body": (20, 20, 8, 12), "jacket": (20, 36, 8, 12),
    "right_arm": (44, 20, 4, 12), "right_sleeve": (44, 36, 4, 12),
    "left_arm": (36, 52, 4, 12), "left_sleeve": (52, 52, 4, 12),
    "right_leg": (4, 20, 4, 12), "right_pants": (4, 36, 4, 12),
    "left_leg": (20, 52, 4, 12), "left_pants": (4, 52, 4, 12),
}


def pixel(image, x, y):
    px = image.pixel(x, y)
    return ((px >> 16) & 0xFF, (px >> 8) & 0xFF,
            px & 0xFF, (px >> 24) & 0xFF)


def fill_rect(image, rect, rgb):
    x, y, w, h = rect
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            image.setPixel(xx, yy, argb(*rgb, 255))


def make_skin(layout="64x64", hat_solid=True):
    """A synthetic skin: every front region its own color, the
    rest transparent. For 64x32 only the classic regions exist.
    hat_solid=False leaves the hat transparent except one pixel,
    to test overlay compositing."""
    height = 64 if layout == "64x64" else 32
    image = Image(64, height)
    names = (REGION_COLORS if layout == "64x64" else
             {k: v for k, v in REGION_COLORS.items()
              if k in ("head", "hat", "body", "right_arm",
                       "right_leg")})
    for name, rgb in names.items():
        if name == "hat" and not hat_solid:
            continue
        fill_rect(image, FRONTS[name], rgb)
    if not hat_solid:
        image.setPixel(40, 8, argb(0, 255, 0, 255))
    return image


class SkinDetectionTests(unittest.TestCase):
    def test_detect_layouts(self):
        self.assertEqual(detect_skin(make_skin("64x64")), "64x64")
        self.assertEqual(detect_skin(make_skin("64x32")), "64x32")
        self.assertIsNone(detect_skin(make_solid_image(16, 16, 1, 2, 3)))
        self.assertIsNone(detect_skin(make_solid_image(64, 16, 1, 2, 3)))

    def test_parts_pixel_exact_64x64(self):
        parts = skin_parts(make_skin("64x64"))
        self.assertEqual(list(parts), [
            "head", "hat", "body", "jacket",
            "right_arm", "right_sleeve",
            "left_arm", "left_sleeve",
            "right_leg", "right_pants",
            "left_leg", "left_pants"])
        for name, rgb in REGION_COLORS.items():
            part = parts[name]
            x, y, w, h = FRONTS[name]
            self.assertEqual((part.width(), part.height()), (w, h),
                             name)
            self.assertEqual(pixel(part, 0, 0), rgb + (255,), name)
            self.assertEqual(pixel(part, w - 1, h - 1),
                             rgb + (255,), name)

    def test_parts_classic_mirror_left_limbs(self):
        skin = make_skin("64x32")
        # Asymmetric right arm: left column red, right column blue.
        for y in range(20, 32):
            skin.setPixel(44, y, argb(255, 0, 0, 255))
            skin.setPixel(47, y, argb(0, 0, 255, 255))
        parts = skin_parts(skin)
        self.assertEqual(list(parts), [
            "head", "hat", "body", "right_arm", "left_arm",
            "right_leg", "left_leg"])
        self.assertEqual(pixel(parts["right_arm"], 0, 0),
                         (255, 0, 0, 255))
        self.assertEqual(pixel(parts["right_arm"], 3, 0),
                         (0, 0, 255, 255))
        # The left arm is the right arm mirrored.
        self.assertEqual(pixel(parts["left_arm"], 0, 0),
                         (0, 0, 255, 255))
        self.assertEqual(pixel(parts["left_arm"], 3, 0),
                         (255, 0, 0, 255))
        # The left leg mirrors the right leg too.
        self.assertEqual(pixel(parts["left_leg"], 0, 0),
                         REGION_COLORS["right_leg"] + (255,))

    def test_not_a_skin_raises(self):
        with self.assertRaises(PixelForgeError) as ctx:
            skin_parts(make_solid_image(16, 16, 1, 2, 3))
        self.assertIn("not a Minecraft skin", str(ctx.exception))


class SkinFigureTests(unittest.TestCase):
    def test_figure_layout_and_overlays(self):
        figure = skin_figure(make_skin("64x64"))
        self.assertEqual((figure.width(), figure.height()), (16, 32))
        # Overlays are solid here, so they cover their base parts.
        self.assertEqual(pixel(figure, 8, 4),
                         REGION_COLORS["hat"] + (255,))  # head
        self.assertEqual(pixel(figure, 8, 14),
                         REGION_COLORS["jacket"] + (255,))  # body
        self.assertEqual(pixel(figure, 2, 14),
                         REGION_COLORS["right_sleeve"] + (255,))
        self.assertEqual(pixel(figure, 14, 14),
                         REGION_COLORS["left_sleeve"] + (255,))
        self.assertEqual(pixel(figure, 6, 26),
                         REGION_COLORS["right_pants"] + (255,))
        self.assertEqual(pixel(figure, 10, 26),
                         REGION_COLORS["left_pants"] + (255,))

    def test_figure_partial_overlay(self):
        # Hat is one opaque pixel: the figure shows the hat there
        # and the head everywhere else.
        figure = skin_figure(make_skin("64x64", hat_solid=False))
        self.assertEqual(pixel(figure, 4, 0),
                         REGION_COLORS["hat"] + (255,))
        self.assertEqual(pixel(figure, 8, 4),
                         REGION_COLORS["head"] + (255,))

    def test_figure_classic_has_no_overlays(self):
        figure = skin_figure(make_skin("64x32"))
        self.assertEqual((figure.width(), figure.height()), (16, 32))
        # Hat still overlays the head (it exists in classic skins).
        self.assertEqual(pixel(figure, 8, 4),
                         REGION_COLORS["hat"] + (255,))
        self.assertEqual(pixel(figure, 8, 14),
                         REGION_COLORS["body"] + (255,))
        # Left arm mirrors the right arm.
        self.assertEqual(pixel(figure, 14, 14),
                         REGION_COLORS["right_arm"] + (255,))


class SkinExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    def test_extract_writes_parts_and_figure(self):
        path = write_image(make_skin("64x64"),
                           os.path.join(self.tmp, "steve.png"))
        out = os.path.join(self.tmp, "steve-parts")
        written = extract_skin(path, out)
        names = sorted(p.name for p in written)
        self.assertEqual(len(names), 13)
        self.assertIn("figure.png", names)
        self.assertIn("head.png", names)
        self.assertIn("left_pants.png", names)
        for name in names:
            self.assertTrue(os.path.isfile(os.path.join(out, name)),
                            name)

    def test_extract_classic_writes_seven_parts(self):
        path = write_image(make_skin("64x32"),
                           os.path.join(self.tmp, "old.png"))
        written = extract_skin(path, os.path.join(self.tmp, "old"))
        names = sorted(p.name for p in written)
        self.assertEqual(len(names), 8)
        self.assertNotIn("jacket.png", names)

    def test_extract_not_a_skin_fails(self):
        path = write_image(make_solid_image(16, 16, 1, 2, 3),
                           os.path.join(self.tmp, "tex.png"))
        with self.assertRaises(PixelForgeError) as ctx:
            extract_skin(path, os.path.join(self.tmp, "out"))
        self.assertIn("not a Minecraft skin", str(ctx.exception))

    def test_extract_missing_file_fails(self):
        with self.assertRaises(PixelForgeError):
            extract_skin(os.path.join(self.tmp, "nope.png"),
                         os.path.join(self.tmp, "out"))

    def test_texture_pixelize_keeps_native_size(self):
        texture = pixelize_texture(make_solid_image(16, 16, 9, 8, 7))
        self.assertEqual((texture.width(), texture.height()),
                         (16, 16))
        self.assertEqual(pixel(texture, 0, 0), (9, 8, 7, 255))


class TextureCapTests(unittest.TestCase):
    """v0.8 regression: the texture path caps at 512 px (review
    W2 — a 4000x3000 photo used to freeze the UI for 209 s)."""
    def test_texture_target_size(self):
        self.assertEqual(
            texture_target_size(make_solid_image(64, 64, 1, 2, 3)),
            (64, 64))
        self.assertEqual(
            texture_target_size(make_solid_image(512, 512, 1, 2, 3)),
            (512, 512))
        self.assertEqual(
            texture_target_size(make_solid_image(1024, 768, 1, 2, 3)),
            (512, 384))
        self.assertEqual(
            texture_target_size(make_solid_image(300, 600, 1, 2, 3)),
            (256, 512))

    def test_large_photo_caps_at_512_and_finishes_fast(self):
        # Uncapped, a 1024x1024 photo took 21.8 s on the sandbox
        # CPU; capped it takes ~2 s. The 15 s bound is ~9x the
        # capped time and still catches an uncapped regression.
        image = make_noisy_image(1024, 1024)
        start = time.monotonic()
        texture = pixelize_texture(image)
        elapsed = time.monotonic() - start
        self.assertEqual((texture.width(), texture.height()),
                         (512, 512))
        self.assertLess(elapsed, 15,
                        "1024x1024 texture took %.1f s — the cap "
                        "may have regressed" % elapsed)

    def test_small_texture_unchanged_by_cap(self):
        # A texture under the cap must be pixel-for-pixel what the
        # uncapped core produces: same call, same image.
        image = make_noisy_image(64, 64, rects=200)
        capped = pixelize_texture(image)
        direct = pixelize_image(image, width=64, height=64,
                                palette="image", max_colors=256,
                                dither=False)
        self.assertEqual((capped.width(), capped.height()),
                         (64, 64))
        for y in range(64):
            for x in range(64):
                self.assertEqual(capped.pixel(x, y),
                                 direct.pixel(x, y),
                                 "pixel differs at %d,%d" % (x, y))


class TextureSettingsTests(unittest.TestCase):
    """Task 26: pixelize_texture takes a size, palette, color
    limit, and dithering — the Code tab's texture controls."""

    def test_texture_with_size_settings(self):
        image = make_noisy_image(64, 64, rects=200)
        texture = pixelize_texture(image, width=32, height=16)
        self.assertEqual((texture.width(), texture.height()),
                         (32, 16))

    def test_texture_with_palette_and_colors(self):
        image = make_gradient_image(64, 64)
        texture = pixelize_texture(image, palette="pico8",
                                   max_colors=4)
        self.assertLessEqual(count_opaque_colors(texture), 4)
        pico8 = {(r, g, b) for r, g, b in NAMED_PALETTES["pico8"]}
        for y in range(texture.height()):
            for x in range(texture.width()):
                r, g, b, a = pixel(texture, x, y)
                if a:
                    self.assertIn((r, g, b), pico8,
                                  "pixel %d,%d not a PICO-8 color"
                                  % (x, y))

    def test_texture_dither_changes_output(self):
        image = make_gradient_image(64, 64)
        flat = pixelize_texture(image, palette="gameboy",
                                max_colors=4, dither=False)
        dithered = pixelize_texture(image, palette="gameboy",
                                    max_colors=4, dither=True)
        self.assertEqual((flat.width(), flat.height()),
                         (dithered.width(), dithered.height()))
        differ = sum(
            1 for y in range(64) for x in range(64)
            if flat.pixel(x, y) != dithered.pixel(x, y))
        self.assertGreater(differ, 0,
                           "dithering changed no pixels")

    def test_texture_defaults_match_explicit_defaults(self):
        # The bare call and the explicit default call are the
        # same render — the defaults are the old behavior.
        image = make_noisy_image(64, 64, rects=200)
        bare = pixelize_texture(image)
        explicit = pixelize_texture(image, width=64, height=64,
                                    palette="image", max_colors=256,
                                    dither=False)
        self.assertEqual((bare.width(), bare.height()),
                         (explicit.width(), explicit.height()))
        for y in range(64):
            for x in range(64):
                self.assertEqual(bare.pixel(x, y),
                                 explicit.pixel(x, y),
                                 "pixel differs at %d,%d" % (x, y))

    def test_texture_settings_respect_the_cap(self):
        image = make_noisy_image(1024, 1024, rects=200)
        capped = pixelize_texture(image, width=512, height=512)
        self.assertEqual((capped.width(), capped.height()),
                         (512, 512))
        small = pixelize_texture(image, width=64, height=64)
        self.assertEqual((small.width(), small.height()),
                         (64, 64))
        # A request over the cap gets the cap fit, not the
        # request — the core must never see a 1024-wide image.
        over = pixelize_texture(image, width=1024, height=1024)
        self.assertEqual((over.width(), over.height()),
                         (512, 512))


if __name__ == "__main__":
    unittest.main()
