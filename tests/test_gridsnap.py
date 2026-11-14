"""Grid-snap unit tests — no Qt needed (task 42).

The detector is calibrated against deterministic synthetics:
a known sprite upscaled by nearest (sharp), bilinear (pure
ramps — the harsh case), and soft-edge (flat interiors with
smoothed edges — what AI output actually looks like). The
calibration numbers (exact cells, origins, the 99.9% soft-edge
match) were measured in the sandbox before these tests were
pinned; see the module docstring for the method and limits.
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.gridsnap import (  # noqa: E402
    clean_image, detect_grid, is_pixel_clean, snap_and_quantise,
    snap_to_grid)
from pixel_forge.image import Image  # noqa: E402
from pixel_forge.palettes import NAMED_PALETTES  # noqa: E402
from pixel_forge.pixelize import PixelForgeError  # noqa: E402

# The helpers below are the calibration harness's synthetics,
# copied exactly — the pinned numbers (origins, match rates)
# were measured against THESE images, and the detector's phase
# reads depend on the content.
PALETTE_8 = [(200, 0, 0), (0, 160, 0), (0, 0, 200), (220, 180, 0),
             (120, 0, 160), (0, 180, 180), (240, 240, 240),
             (30, 30, 30)]


def make_sprite(size=32, seed=7):
    """A deterministic sprite: flat regions, hard edges, a few
    stray single pixels — like real pixel art."""
    rnd = random.Random(seed)
    img = Image(size, size)
    data = img.data
    for y in range(size):
        for x in range(size):
            # Big flat blobs + a border + speckles.
            if x in (0, size - 1) or y in (0, size - 1):
                c = PALETTE_8[7]
            elif (x // 6 + y // 5) % 3 == 0:
                c = PALETTE_8[(x // 4 + y // 7) % 6]
            elif rnd.random() < 0.04:
                c = PALETTE_8[rnd.randrange(8)]
            else:
                c = PALETTE_8[(x // 8) % 4]
            off = (y * size + x) * 4
            data[off:off + 4] = bytes((c[0], c[1], c[2], 255))
    return img


def make_flat_sprite(size=32):
    """A mostly-flat 1x sprite: one big block, one small block."""
    img = Image(size, size)
    data = img.data
    for y in range(size):
        for x in range(size):
            r, g, b = (30, 120, 200)
            if 10 <= x < 20 and 10 <= y < 20:
                r, g, b = (220, 60, 40)
            off = (y * size + x) * 4
            data[off:off + 4] = bytes((r, g, b, 255))
    return img


def nearest_upscale(src, factor):
    sw, sh = src.width(), src.height()
    out = Image(sw * factor, sh * factor)
    for y in range(sh * factor):
        for x in range(sw * factor):
            sx, sy = x // factor, y // factor
            s = (sy * sw + sx) * 4
            d = (y * sw * factor + x) * 4
            out.data[d:d + 4] = src.data[s:s + 4]
    return out


def bilinear_upscale(src, out_w, out_h):
    """Bilinear resample to an arbitrary size (simulates the
    AI blur: off-grid, blended edges)."""
    sw, sh = src.width(), src.height()
    out = Image(out_w, out_h)
    sx = sw / out_w
    sy = sh / out_h
    for y in range(out_h):
        fy = (y + 0.5) * sy - 0.5
        y0 = max(0, min(sh - 1, int(fy)))
        y1 = max(0, min(sh - 1, y0 + 1))
        ty = max(0.0, min(1.0, fy - y0))
        for x in range(out_w):
            fx = (x + 0.5) * sx - 0.5
            x0 = max(0, min(sw - 1, int(fx)))
            x1 = max(0, min(sw - 1, x0 + 1))
            tx = max(0.0, min(1.0, fx - x0))
            d = (y * out_w + x) * 4
            for c in range(4):
                v00 = src.data[(y0 * sw + x0) * 4 + c]
                v10 = src.data[(y0 * sw + x1) * 4 + c]
                v01 = src.data[(y1 * sw + x0) * 4 + c]
                v11 = src.data[(y1 * sw + x1) * 4 + c]
                top = v00 * (1 - tx) + v10 * tx
                bot = v01 * (1 - tx) + v11 * tx
                out.data[d + c] = int(round(top * (1 - ty)
                                            + bot * ty))
    return out


def soft_upscale(src, factor, edge=0.45):
    """Blocks with sigmoid-smoothed edges — closer to real AI
    output than a bilinear ramp: each cell is flat in the
    middle and blends only near the boundary. The blend is
    centred mid-cell, so the true grid origin is phase
    factor / 2 (4.0 at factor 8)."""
    sw, sh = src.width(), src.height()
    out = Image(sw * factor, sh * factor)
    for y in range(sh * factor):
        gy = y / factor
        y0 = min(sh - 1, int(gy))
        y1 = min(sh - 1, y0 + 1)
        fy = gy - int(gy)
        ty = 0.0 if fy < 0.5 - edge else (
            1.0 if fy > 0.5 + edge else
            (fy - (0.5 - edge)) / (2 * edge))
        ty = ty * ty * (3 - 2 * ty)
        for x in range(sw * factor):
            gx = x / factor
            x0 = min(sw - 1, int(gx))
            x1 = min(sw - 1, x0 + 1)
            fx = gx - int(gx)
            tx = 0.0 if fx < 0.5 - edge else (
                1.0 if fx > 0.5 + edge else
                (fx - (0.5 - edge)) / (2 * edge))
            tx = tx * tx * (3 - 2 * tx)
            d = (y * sw * factor + x) * 4
            for c in range(4):
                v00 = src.data[(y0 * sw + x0) * 4 + c]
                v10 = src.data[(y0 * sw + x1) * 4 + c]
                v01 = src.data[(y1 * sw + x0) * 4 + c]
                v11 = src.data[(y1 * sw + x1) * 4 + c]
                top = v00 * (1 - tx) + v10 * tx
                bot = v01 * (1 - tx) + v11 * tx
                out.data[d + c] = int(round(top * (1 - ty)
                                            + bot * ty))
    return out


def make_gradient(width=64, height=64):
    img = Image(width, height)
    for y in range(height):
        for x in range(width):
            off = (y * width + x) * 4
            img.data[off:off + 4] = bytes((x * 4 % 256, y * 4 % 256,
                                           128, 255))
    return img


def make_noise(width=64, height=64, seed=3):
    rnd = random.Random(seed)
    img = Image(width, height)
    for i in range(0, len(img.data), 4):
        img.data[i:i + 4] = bytes((rnd.randrange(256),
                                   rnd.randrange(256),
                                   rnd.randrange(256), 255))
    return img


def speckle(image, frac, seed=1):
    """Random-pixel noise over a copy — the detector must
    survive up to about 1% of it."""
    rnd = random.Random(seed)
    out = Image(image.width(), image.height())
    out.data[:] = image.data
    for i in range(0, len(out.data), 4):
        if rnd.random() < frac:
            out.data[i:i + 4] = bytes((rnd.randrange(256),
                                       rnd.randrange(256),
                                       rnd.randrange(256), 255))
    return out


def match_stats(out, orig):
    """(exact %, mean abs error) over the overlapping region."""
    w, h = out.width(), out.height()
    ow, oh = orig.width(), orig.height()
    mw, mh = min(w, ow), min(h, oh)
    exact = 0
    total = 0
    err = 0.0
    for y in range(mh):
        for x in range(mw):
            o = out.data[(y * w + x) * 4:(y * w + x) * 4 + 4]
            r = orig.data[(y * ow + x) * 4:(y * ow + x) * 4 + 4]
            total += 1
            if o == r:
                exact += 1
            err += sum(abs(o[c] - r[c]) for c in range(3)) / 3.0
    return 100.0 * exact / total, err / total


class DetectGridTests(unittest.TestCase):
    def test_clean_sprite_returns_one(self):
        self.assertEqual(detect_grid(make_sprite(32)),
                         (1.0, 0.0, 0.0))

    def test_flat_sprite_returns_one(self):
        self.assertEqual(detect_grid(make_flat_sprite(32)),
                         (1.0, 0.0, 0.0))

    def test_is_pixel_clean(self):
        self.assertTrue(is_pixel_clean(make_sprite(32)))
        self.assertTrue(is_pixel_clean(make_flat_sprite(32)))
        self.assertFalse(is_pixel_clean(
            bilinear_upscale(make_sprite(32), 256, 256)))
        self.assertFalse(is_pixel_clean(make_gradient()))

    def test_nearest_upscales_exact(self):
        sprite = make_sprite(32)
        for factor in (2, 4, 8, 16):
            grid = detect_grid(nearest_upscale(sprite, factor))
            self.assertIsNotNone(grid, factor)
            self.assertAlmostEqual(grid[0], float(factor),
                                   places=2, msg=factor)
            self.assertAlmostEqual(grid[1], 0.0, places=1,
                                   msg=factor)
            self.assertAlmostEqual(grid[2], 0.0, places=1,
                                   msg=factor)

    def test_nearest_offset(self):
        sprite = make_sprite(32)
        up = nearest_upscale(sprite, 8)
        shifted = Image(256 + 3, 256 + 5)
        for y in range(256):
            for x in range(256):
                s = (y * 256 + x) * 4
                d = ((y + 5) * 259 + x + 3) * 4
                shifted.data[d:d + 4] = up.data[s:s + 4]
        grid = detect_grid(shifted)
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid[0], 8.0, places=2)
        self.assertAlmostEqual(grid[1], 3.0, delta=0.5)
        self.assertAlmostEqual(grid[2], 5.0, delta=0.5)

    def test_bilinear_upscales_exact(self):
        sprite = make_sprite(32)
        # The origin sits half a cell before the first cell
        # centre: cell 4 -> 3.5, cell 8 -> 7.5, cell 16 -> 15.5.
        for factor, origin in ((4, 3.5), (8, 7.5), (16, 15.5)):
            up = bilinear_upscale(sprite, 32 * factor, 32 * factor)
            grid = detect_grid(up)
            self.assertIsNotNone(grid, factor)
            self.assertAlmostEqual(grid[0], float(factor),
                                   places=2, msg=factor)
            self.assertAlmostEqual(grid[1], origin, delta=0.6,
                                   msg=factor)
            self.assertAlmostEqual(grid[2], origin, delta=0.6,
                                   msg=factor)

    def test_bilinear_fractional_sizes(self):
        grid = detect_grid(bilinear_upscale(make_sprite(48), 512, 512))
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid[0], 512 / 48, delta=0.1)
        grid = detect_grid(bilinear_upscale(make_sprite(40), 220, 220))
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid[0], 5.5, places=2)

    def test_soft_edge_upscales(self):
        sprite = make_sprite(32)
        for edge in (0.25, 0.15):
            grid = detect_grid(soft_upscale(sprite, 8, edge=edge))
            self.assertIsNotNone(grid, edge)
            self.assertAlmostEqual(grid[0], 8.0, delta=0.05,
                                   msg=edge)
            # The blend is centred mid-cell, so the true
            # origin is phase 4.0 in both directions.
            self.assertAlmostEqual(grid[1], 4.0, delta=1.0, msg=edge)
            self.assertAlmostEqual(grid[2], 4.0, delta=1.0, msg=edge)

    def test_no_grid_gradient_noise(self):
        self.assertIsNone(detect_grid(make_gradient()))
        self.assertIsNone(detect_grid(make_noise()))

    def test_vertical_stripes(self):
        # Grid in x only (the vertical direction is flat).
        # Four colors cycling, 256 wide — the strong-grid
        # override fires over the clean test.
        img = Image(256, 256)
        for y in range(256):
            for x in range(256):
                c = PALETTE_8[(x // 8) % 4]
                off = (y * 256 + x) * 4
                img.data[off:off + 4] = bytes((c[0], c[1], c[2],
                                               255))
        grid = detect_grid(img)
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid[0], 8.0, places=2)

    def test_bilinear_x2_reports_no_grid(self):
        # A documented limit: at cell 2 the second-derivative
        # signal is ~1 by construction and the first-derivative
        # signal is ~1.2 across the ramp — an honest no-grid,
        # not a wrong grid.
        up = bilinear_upscale(make_sprite(32), 64, 64)
        self.assertIsNone(detect_grid(up))

    def test_zero_size_raises(self):
        with self.assertRaises(PixelForgeError):
            detect_grid(Image(0, 0))

    def test_tiny_image_no_grid(self):
        # A tiny clean sprite still returns (1, 0, 0) — the
        # no-grid case for a tiny image is a tiny non-sprite.
        self.assertIsNone(detect_grid(make_gradient(4, 4)))
        self.assertEqual(detect_grid(make_sprite(4)),
                         (1.0, 0.0, 0.0))

    def test_speckle_tolerance(self):
        up = soft_upscale(make_sprite(32), 8, edge=0.25)
        grid = detect_grid(speckle(up, 0.01))
        self.assertIsNotNone(grid)
        self.assertAlmostEqual(grid[0], 8.0, delta=0.1)
        # At 2% the noise wins and the detector says so.
        self.assertIsNone(detect_grid(speckle(up, 0.02)))


class SnapToGridTests(unittest.TestCase):
    def test_median_not_mean(self):
        # One cell, 15 pixels of one color and a single outlier:
        # the median is the majority color, the mean is not.
        img = Image(4, 4)
        for i in range(0, len(img.data), 4):
            img.data[i:i + 4] = bytes((100, 100, 100, 255))
        img.data[0:4] = bytes((255, 0, 0, 255))
        out = snap_to_grid(img, 4.0)
        self.assertEqual((out.width(), out.height()), (1, 1))
        self.assertEqual(bytes(out.data[0:4]),
                         bytes((100, 100, 100, 255)))

    def test_core_median_ignores_border(self):
        # A cell whose border is one color and whose middle
        # half is another: the core median reads the middle,
        # a whole-cell median would read the border (48 of 64
        # pixels). This is what makes the snap exact on
        # blurred cells, where the edges are blend ramps.
        img = Image(16, 16)
        for cy in range(2):
            for cx in range(2):
                for y in range(8):
                    for x in range(8):
                        core = 2 <= x < 6 and 2 <= y < 6
                        r, g, b = ((0, 0, 200) if core
                                   else (200, 0, 0))
                        off = ((cy * 8 + y) * 16 + cx * 8 + x) * 4
                        img.data[off:off + 4] = bytes((r, g, b, 255))
        out = snap_to_grid(img, 8.0)
        self.assertEqual((out.width(), out.height()), (2, 2))
        for i in range(0, len(out.data), 4):
            self.assertEqual(bytes(out.data[i:i + 4]),
                             bytes((0, 0, 200, 255)))

    def test_min_cover(self):
        # Two cells side by side: one 25% opaque, one 75%.
        # (50% is the boundary — a cell at exactly half
        # coverage survives.)
        img = Image(8, 4)
        for y in range(4):
            for x in range(8):
                opaque = x < 1 or x >= 5
                off = (y * 8 + x) * 4
                img.data[off:off + 4] = bytes(
                    (10, 20, 30, 255 if opaque else 0))
        out = snap_to_grid(img, 4.0)
        # The thin cell goes transparent and is cropped away;
        # the covered cell survives.
        self.assertEqual((out.width(), out.height()), (1, 1))
        self.assertEqual(bytes(out.data[0:4]),
                         bytes((10, 20, 30, 255)))

    def test_edge_sliver_trimmed(self):
        # A 17-wide image at cell 8: the last column is a 1px
        # sliver, all opaque. Coverage is judged against the
        # nominal cell area, so it reads 1/8 covered and is
        # trimmed — the output is 2 wide, not 3.
        img = Image(17, 16)
        for i in range(0, len(img.data), 4):
            img.data[i:i + 4] = bytes((10, 20, 30, 255))
        out = snap_to_grid(img, 8.0)
        self.assertEqual((out.width(), out.height()), (2, 2))

    def test_crop_to_bbox(self):
        img = Image(32, 32)
        for y in range(8, 24):
            for x in range(8, 24):
                off = (y * 32 + x) * 4
                img.data[off:off + 4] = bytes((10, 20, 30, 255))
        out = snap_to_grid(img, 4.0)
        self.assertEqual((out.width(), out.height()), (4, 4))

    def test_fully_transparent(self):
        out = snap_to_grid(Image(16, 16), 4.0)
        self.assertEqual((out.width(), out.height()), (1, 1))
        self.assertEqual(bytes(out.data[0:4]), bytes(4))

    def test_snap_errors(self):
        img = make_sprite(32)
        with self.assertRaises(PixelForgeError):
            snap_to_grid(img, 0.5)
        with self.assertRaises(PixelForgeError):
            snap_to_grid(Image(0, 0), 4.0)

    def test_snap_and_quantise_uses_known_grid(self):
        up = soft_upscale(make_sprite(32), 8, edge=0.25)
        out = snap_and_quantise(up, (8.0, 4.0, 4.0),
                                max_colors=16)
        exact, _mae = match_stats(out, make_sprite(32))
        self.assertGreaterEqual(exact, 99.0)


class CleanImageTests(unittest.TestCase):
    def test_clean_soft_edge_matches_original(self):
        # Realistic AI output: flat interiors, smoothed edges.
        # The snap recovers the source sprite almost exactly.
        sprite = make_sprite(32)
        up = soft_upscale(sprite, 8, edge=0.25)
        out, grid = clean_image(up, max_colors=16)
        self.assertAlmostEqual(grid[0], 8.0, delta=0.05)
        exact, mae = match_stats(out, sprite)
        self.assertGreaterEqual(exact, 99.0)
        self.assertLess(mae, 1.0)

    def test_clean_bilinear_is_close_not_exact(self):
        # On a pure bilinear ramp the source colors are not
        # present in any output pixel, so no local estimator
        # recovers them exactly — and the quantise step bakes
        # the blend colors into the palette, so the exact
        # match is 0%. What the snap does deliver is
        # closeness: a mean error of a few levels out of 255.
        sprite = make_sprite(32)
        up = bilinear_upscale(sprite, 256, 256)
        out, grid = clean_image(up, max_colors=16)
        self.assertAlmostEqual(grid[0], 8.0, places=2)
        _exact, mae = match_stats(out, sprite)
        self.assertLess(mae, 8.0)

    def test_clean_named_palette(self):
        up = soft_upscale(make_sprite(32), 8, edge=0.25)
        out, _grid = clean_image(up, palette="pico8", max_colors=16)
        allowed = set(NAMED_PALETTES["pico8"])
        for i in range(0, len(out.data), 4):
            if out.data[i + 3] == 0:
                continue
            rgb = tuple(out.data[i:i + 3])
            self.assertIn(rgb, allowed)

    def test_clean_no_grid_raises(self):
        with self.assertRaises(PixelForgeError):
            clean_image(make_gradient())

    def test_clean_already_clean_quantises_only(self):
        # A 1x sprite with more colors than the limit: the
        # grid is (1, 0, 0), the size is unchanged, and the
        # output is quantised to the limit.
        img = Image(8, 8)
        rnd = random.Random(11)
        for i in range(0, len(img.data), 4):
            img.data[i:i + 4] = bytes((rnd.randrange(256),
                                       rnd.randrange(256),
                                       rnd.randrange(256), 255))
        out, grid = clean_image(img, max_colors=4)
        self.assertEqual(grid, (1.0, 0.0, 0.0))
        self.assertEqual((out.width(), out.height()), (8, 8))
        colors = set()
        for i in range(0, len(out.data), 4):
            colors.add(tuple(out.data[i:i + 3]))
        self.assertLessEqual(len(colors), 4)


if __name__ == "__main__":
    unittest.main()
