"""Timing smoke tests (task 59, review section 8).

A 4000 x 3000 photo pixelizes to 64 x 64 and to its capped
texture size inside generous bounds — so a regression back to
walking all 12 million pixels in Python fails loudly instead
of freezing the window (review W2: the uncapped texture path
used to freeze the UI for 209 s on this size).

Measured on the v0.73 tree in the sandbox (throttled,
shared CPU): pixelize to 64 x 64 took 0.03-0.10 s across 3
runs; pixelize_texture took 1.08-1.20 s across 2 runs. The
bounds are 10 s and 15 s — 100x and 12x the measured times,
so ordinary load swings cannot trip them, while a
full-source Python scan (20 s or more for 12 million pixels)
still fails. The texture half at 1024 x 1024 is already
covered by test_skins.TextureCapTests; this file pins the
4000 x 3000 repro size for both paths.

The image is built once per class (0.6 s, 48 MB) and shared:
neither pixelize_image nor pixelize_texture mutates its
input. The build itself is not timed.
"""

import time
import unittest

from helpers import make_noisy_image
from pixel_forge.pixelize import pixelize_image
from pixel_forge.skins import pixelize_texture


class TimingSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.photo = make_noisy_image(4000, 3000)

    def test_pixelize_4000x3000_to_64x64_under_bound(self):
        start = time.monotonic()
        out = pixelize_image(self.photo, 64, 64)
        elapsed = time.monotonic() - start
        self.assertEqual((out.width(), out.height()), (64, 64))
        self.assertLess(
            elapsed, 10,
            "4000x3000 -> 64x64 pixelize took %.1f s — the "
            "downscale may be walking the full image" % elapsed)

    def test_texture_4000x3000_under_bound(self):
        start = time.monotonic()
        texture = pixelize_texture(self.photo)
        elapsed = time.monotonic() - start
        self.assertEqual((texture.width(), texture.height()),
                         (512, 384))
        self.assertLess(
            elapsed, 15,
            "4000x3000 texture took %.1f s — the 512 cap may "
            "have regressed" % elapsed)


if __name__ == "__main__":
    unittest.main()
