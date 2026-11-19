"""Qt GUI smoke tests — launch the window offscreen, drive it.

Run with the Qt test env (see AGENTS.md):
  LD_LIBRARY_PATH=~/workspace/qt-libs/root/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen \
  ~/workspace/forge-v2-testenv/bin/python -m unittest tests.test_gui
Skipped when PySide6 is not importable.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from helpers import (make_gradient_image, make_noisy_image,  # noqa: E402
                         make_solid_image, make_test_video, stub_env,
                         write_image, write_stub_engine)
    from PySide6.QtCore import QRect, QSettings, Qt
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, \
        QDialog, QFileDialog, QInputDialog, QLabel, QMessageBox, \
        QPushButton, QScrollArea, QTabWidget, QWidget
except ImportError as exc:
    # Task 63: every test here needs Qt, so skip the module when
    # PySide6 is what is missing — but a genuinely broken import
    # still fails loudly instead of hiding behind the skip.
    # (This replaces the old per-class HAS_QT decorators: one of
    # the classes never had the decorator, so the module skipped
    # nothing and errored on import instead.)
    if exc.name and exc.name.startswith("PySide6"):
        raise unittest.SkipTest("PySide6 not available")
    raise


def visible_texts(widget):
    """Every user-visible string in a window: button text, labels,
    checkbox text, combo items, tab titles, window titles. Used to
    forbid "&"."""
    texts = [widget.windowTitle()]
    for child in widget.findChildren(QWidget):
        if isinstance(child, (QPushButton, QLabel, QCheckBox)):
            texts.append(child.text())
        if isinstance(child, QComboBox):
            for i in range(child.count()):
                texts.append(child.itemText(i))
        if isinstance(child, QTabWidget):
            for i in range(child.count()):
                texts.append(child.tabText(i))
    return texts


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def test_window_shows_with_title(self):
        # Task 22: the title carries the version, from
        # __version__ — never hardcoded, so it cannot drift.
        from pixel_forge import __version__
        self.win.show()
        self.app.processEvents()
        self.assertTrue(self.win.isVisible())
        self.assertEqual(self.win.windowTitle(),
                         "Pixel Forge %s" % __version__)

    def test_window_fits_a_768px_screen(self):
        # Regression (v0.6 review, W1): the window opened 1020x872
        # with a 872 px minimum — taller than the 1366x768 target
        # screen, so the Export buttons were out of reach. Every
        # tab's control column now lives in a scroll area, so the
        # window must shrink to 700 px or less, and a resize to
        # 800x600 must actually take effect — and hold — on every
        # tab.
        self.win.show()
        self.app.processEvents()
        min_height = self.win.minimumSizeHint().height()
        self.assertLessEqual(min_height, 700,
                             "window minimum height %d px" % min_height)
        self.win.resize(800, 600)
        self.app.processEvents()
        self.assertEqual(self.win.height(), 600,
                         "resize to 800x600 refused: window is "
                         "%d px tall" % self.win.height())
        for index in range(self.win.tabs.count()):
            self.win.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertEqual(
                self.win.height(), 600,
                "tab %s grew the window to %d px"
                % (self.win.tabs.tabText(index), self.win.height()))

    def test_no_bare_ampersand_in_visible_text(self):
        # Qt eats a bare & in visible widget text (mnemonic marker —
        # it rendered as "_" while text() still returned the raw
        # string, so a string check alone would stay green on a wrong
        # label). Write "and" instead, everywhere.
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def test_controls_present_with_expected_options(self):
        sizes = [self.win.size_combo.itemData(i)
                 for i in range(self.win.size_combo.count())]
        self.assertEqual(sizes, [16, 32, 48, 64, 128, None])
        palettes = [self.win.palette_combo.itemData(i)
                    for i in range(self.win.palette_combo.count())]
        self.assertEqual(palettes, ["image", "pico8", "gameboy",
                                    "grayscale", "resurrect64",
                                    "endesga32", "sweetie16"])
        self.assertEqual((self.win.colors_spin.minimum(),
                          self.win.colors_spin.maximum()), (2, 256))
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertTrue(self.win.dither_check.isChecked())
        self.assertFalse(self.win.aspect_check.isChecked())
        self.assertFalse(self.win.export_btn.isEnabled())
        self.assertEqual(self.win.zoom_slider.value(), 8)
        self.assertEqual(self.win.zoom_label.text(), "8x")

    def test_load_image_produces_preview(self):
        self.assertTrue(self.win.load_image(self.src))
        self.app.processEvents()
        result = self.win.result_image
        self.assertIsNotNone(result)
        self.assertEqual((result.width(), result.height()), (32, 32))
        self.assertIsNotNone(self.win.after_label.pixmap())
        self.assertFalse(self.win.after_label.pixmap().isNull())
        self.assertIsNotNone(self.win.before_label.pixmap())
        self.assertFalse(self.win.before_label.pixmap().isNull())
        self.assertTrue(self.win.export_btn.isEnabled())
        self.assertIn("32 x 32", self.win.info_label.text())

    def test_original_preview_fits_whole_image(self):
        # The Original preview fits its pane whole; zoom applies to
        # the Pixel art side only. (A 200x100 source at 8x zoom would
        # otherwise show just one corner of sky.) The Pixel art side
        # is the 32x32 result at the current zoom — the fit zoom
        # since task 22, not the old fixed 8x.
        self.win.load_image(self.src)
        before = self.win.before_label.pixmap()
        self.assertLessEqual(max(before.width(), before.height()), 352)
        after = self.win.after_label.pixmap()
        zoom = self.win.zoom_slider.value()
        self.assertEqual((after.width(), after.height()),
                         (32 * zoom, 32 * zoom))

    def test_palette_change_repixelizes(self):
        self.win.load_image(self.src)
        index = self.win.palette_combo.findData("pico8")
        self.win.palette_combo.setCurrentIndex(index)
        self.app.processEvents()
        from pixel_forge.pixelize import (count_opaque_colors,
                                          opaque_pixels)
        from pixel_forge.palettes import PICO8
        result = self.win.result_image
        self.assertLessEqual(count_opaque_colors(result), 16)
        self.assertTrue(set(opaque_pixels(result)) <= set(PICO8))

    def test_size_preset_change_repixelizes(self):
        self.win.load_image(self.src)
        index = self.win.size_combo.findData(16)
        self.win.size_combo.setCurrentIndex(index)
        self.app.processEvents()
        result = self.win.result_image
        self.assertEqual((result.width(), result.height()), (16, 16))
        self.assertEqual(self.win.width_spin.value(), 16)
        self.assertEqual(self.win.height_spin.value(), 16)

    def test_custom_size_switches_combo_to_custom(self):
        self.win.load_image(self.src)
        self.win.width_spin.setValue(48)
        self.app.processEvents()
        self.assertIsNone(self.win.size_combo.currentData())
        result = self.win.result_image
        self.assertEqual((result.width(), result.height()), (48, 32))

    def test_export_writes_png(self):
        self.win.load_image(self.src)
        out = os.path.join(self.tmp, "exported.png")
        self.assertTrue(self.win.export_image(out))
        self.assertTrue(os.path.isfile(out))
        from PySide6.QtGui import QImage
        image = QImage(out)
        self.assertEqual((image.width(), image.height()), (32, 32))

    def test_export_adds_png_extension(self):
        self.win.load_image(self.src)
        out = os.path.join(self.tmp, "noext")
        self.assertTrue(self.win.export_image(out))
        self.assertTrue(os.path.isfile(out + ".png"))

    def test_window_screenshot(self):
        self.win.load_image(self.src)
        self.win.show()
        self.app.processEvents()
        shot = os.path.join(self.tmp, "pixel-forge-gui.png")
        self.win.grab().save(shot)
        self.assertTrue(os.path.exists(shot))
        self.assertGreater(os.path.getsize(shot), 1000)


class BgRemoveGuiTests(unittest.TestCase):
    """The Image tab's Remove background button (task 41).

    The removal subprocess is stubbed at the bgremove module
    boundary — no test runs rembg or downloads a model. The
    real run is proven by the proof script and the real-model
    test in test_bgremove.py.
    """
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_solid_image(32, 32, 200, 0, 0),
                              os.path.join(cls.tmp, "red.png"))
        cls.other = write_image(
            make_solid_image(32, 32, 0, 0, 200),
            os.path.join(cls.tmp, "blue.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _wait_bgremove(self, timeout=30):
        deadline = time.monotonic() + timeout
        while self.win.bgremove_worker is not None:
            if time.monotonic() > deadline:
                raise AssertionError(
                    "bgremove worker did not finish")
            self.app.processEvents()
            time.sleep(0.05)
        self.app.processEvents()

    @staticmethod
    def _half_transparent(src, dst, model=None, timeout_s=600,
                          cancel_event=None):
        """A stub removal: the right half goes transparent."""
        from pixel_forge.pixelize import load_image
        from pixel_forge.pngio import write_png

        image = load_image(src)
        data = image.data
        width = image.width()
        for y in range(image.height()):
            for x in range(width // 2, width):
                data[(y * width + x) * 4 + 3] = 0
        write_png(image, dst)

    def test_button_disabled_without_image(self):
        self.assertFalse(self.win.bgremove_btn.isEnabled())
        self.assertFalse(self.win.bgremove_cancel_btn.isEnabled())

    def test_button_enabled_after_load(self):
        self.assertTrue(self.win.load_image(self.src))
        self.assertTrue(self.win.bgremove_btn.isEnabled())
        self.assertFalse(self.win.bgremove_cancel_btn.isEnabled())

    def test_missing_deps_warns(self):
        import pixel_forge.bgremove as bgremove
        from pixel_forge.pixelize import PixelForgeError

        self.win.load_image(self.src)
        with mock.patch.object(
                bgremove, "require_child_ready",
                side_effect=PixelForgeError(
                    "background removal needs rembg")), \
                mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.win.bgremove_btn.click()
            self.app.processEvents()
        self.assertEqual(warned.call_count, 1)
        self.assertEqual(warned.call_args[0][1],
                         "Could not remove background")
        self.assertIn("rembg", warned.call_args[0][2])
        self.assertIsNone(self.win.bgremove_worker)

    def test_missing_model_warns(self):
        import pixel_forge.bgremove as bgremove
        from pixel_forge.pixelize import PixelForgeError

        self.win.load_image(self.src)
        with mock.patch.object(
                bgremove, "require_child_ready",
                side_effect=PixelForgeError(
                    "the model is not downloaded")), \
                mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.win.bgremove_btn.click()
            self.app.processEvents()
        self.assertEqual(warned.call_count, 1)
        self.assertIn("not downloaded", warned.call_args[0][2])
        self.assertIsNone(self.win.bgremove_worker)

    def test_worker_replaces_source(self):
        import pixel_forge.bgremove as bgremove

        self.win.load_image(self.src)
        original = self.win.source_image
        with mock.patch.object(bgremove, "require_child_ready",
                               lambda: None), \
                mock.patch.object(bgremove,
                                  "remove_background_file",
                                  self._half_transparent):
            self.win.bgremove_btn.click()
            self.assertFalse(self.win.bgremove_btn.isEnabled())
            self.assertTrue(
                self.win.bgremove_cancel_btn.isEnabled())
            self._wait_bgremove()
        result = self.win.source_image
        self.assertIsNot(result, original)
        data = result.data
        self.assertEqual(data[3], 255)
        self.assertEqual(data[(32 - 1) * 4 + 3], 0)
        self.assertTrue(self.win.bgremove_btn.isEnabled())
        self.assertFalse(self.win.bgremove_cancel_btn.isEnabled())
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Background removed")

    def test_worker_failure_warns_and_keeps_source(self):
        import pixel_forge.bgremove as bgremove
        from pixel_forge.pixelize import PixelForgeError

        self.win.load_image(self.src)
        original = self.win.source_image

        def failing(src, dst, model=None, timeout_s=600,
                    cancel_event=None):
            raise PixelForgeError("the model file is damaged")

        with mock.patch.object(bgremove, "require_child_ready",
                               lambda: None), \
                mock.patch.object(bgremove,
                                  "remove_background_file",
                                  failing), \
                mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.win.bgremove_btn.click()
            self._wait_bgremove()
        self.assertEqual(warned.call_count, 1)
        self.assertIn("damaged", warned.call_args[0][2])
        self.assertIs(self.win.source_image, original)
        self.assertTrue(self.win.bgremove_btn.isEnabled())

    def test_cancel_leaves_image_unchanged(self):
        import pixel_forge.bgremove as bgremove

        self.win.load_image(self.src)
        original = self.win.source_image

        def blocking(src, dst, model=None, timeout_s=600,
                     cancel_event=None):
            while cancel_event is None or not cancel_event.is_set():
                time.sleep(0.05)
            raise bgremove.RemovalCancelled()

        with mock.patch.object(bgremove, "require_child_ready",
                               lambda: None), \
                mock.patch.object(bgremove,
                                  "remove_background_file",
                                  blocking):
            self.win.bgremove_btn.click()
            deadline = time.monotonic() + 10
            while not self.win.bgremove_cancel_btn.isEnabled():
                if time.monotonic() > deadline:
                    raise AssertionError("worker did not start")
                self.app.processEvents()
                time.sleep(0.05)
            self.win.bgremove_cancel_btn.click()
            self._wait_bgremove()
        self.assertIs(self.win.source_image, original)
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Cancelled — the image is unchanged")
        self.assertTrue(self.win.bgremove_btn.isEnabled())

    def test_stale_result_is_ignored(self):
        import pixel_forge.bgremove as bgremove

        self.win.load_image(self.src)
        first = self.win.source_image
        release = threading.Event()

        def slow(src, dst, model=None, timeout_s=600,
                 cancel_event=None):
            release.wait(timeout=30)
            BgRemoveGuiTests._half_transparent(src, dst)

        with mock.patch.object(bgremove, "require_child_ready",
                               lambda: None), \
                mock.patch.object(bgremove,
                                  "remove_background_file",
                                  slow):
            self.win.bgremove_btn.click()
            self.app.processEvents()
            # A new image lands mid-run (a drop lands even with
            # the Import button disabled).
            self.assertTrue(self.win.load_image(self.other))
            second = self.win.source_image
            self.assertIsNot(second, first)
            release.set()
            self._wait_bgremove()
        # The old image's result must not overwrite the new one.
        self.assertIs(self.win.source_image, second)
        data = self.win.source_image.data
        self.assertEqual(tuple(data[0:3]), (0, 0, 200))
        self.assertIn("no longer loaded",
                      self.win.statusBar().currentMessage())


class GridSnapGuiTests(unittest.TestCase):
    """The Image tab's Snap to pixel grid button (task 42).

    The snap runs in a worker — the tests pump the event loop
    until the worker finishes (the BgRemoveGuiTests pattern).
    The noisy image is a soft-edge upscale of a known sprite,
    so the snapped result is checkable against the original.
    """
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        from test_gridsnap import (make_gradient, make_sprite,
                                   match_stats, soft_upscale)
        cls.match_stats = staticmethod(match_stats)
        cls.tmp = tempfile.mkdtemp()
        cls.sprite = make_sprite(32)
        cls.noisy = write_image(
            soft_upscale(cls.sprite, 8, edge=0.25),
            os.path.join(cls.tmp, "noisy.png"))
        cls.clean = write_image(cls.sprite,
                                os.path.join(cls.tmp, "clean.png"))
        cls.grad = write_image(make_gradient(64, 64),
                               os.path.join(cls.tmp, "grad.png"))
        cls.other = write_image(
            make_solid_image(32, 32, 0, 0, 200),
            os.path.join(cls.tmp, "blue.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _wait_gridsnap(self, timeout=30):
        deadline = time.monotonic() + timeout
        while self.win.gridsnap_worker is not None:
            if time.monotonic() > deadline:
                raise AssertionError(
                    "gridsnap worker did not finish")
            self.app.processEvents()
            time.sleep(0.05)
        self.app.processEvents()

    def test_button_disabled_without_image(self):
        self.assertFalse(self.win.gridsnap_btn.isEnabled())
        self.assertFalse(self.win.gridsnap_cancel_btn.isEnabled())

    def test_button_enabled_after_load(self):
        self.assertTrue(self.win.load_image(self.noisy))
        self.assertTrue(self.win.gridsnap_btn.isEnabled())
        self.assertFalse(self.win.gridsnap_cancel_btn.isEnabled())

    def test_no_grid_leaves_image_alone(self):
        self.assertTrue(self.win.load_image(self.grad))
        original = self.win.source_image
        self.win.gridsnap_btn.click()
        self._wait_gridsnap()
        self.assertIs(self.win.source_image, original)
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Could not find a pixel grid in this "
                         "image, so the image is unchanged")
        self.assertTrue(self.win.gridsnap_btn.isEnabled())

    def test_already_clean_leaves_image_alone(self):
        self.assertTrue(self.win.load_image(self.clean))
        original = self.win.source_image
        self.win.gridsnap_btn.click()
        self._wait_gridsnap()
        self.assertIs(self.win.source_image, original)
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "This is already a clean pixel sprite, "
                         "so the image is unchanged")
        self.assertTrue(self.win.gridsnap_btn.isEnabled())

    def test_snap_replaces_source(self):
        self.assertTrue(self.win.load_image(self.noisy))
        original = self.win.source_image
        self.win.gridsnap_btn.click()
        self.assertFalse(self.win.gridsnap_btn.isEnabled())
        self.assertTrue(self.win.gridsnap_cancel_btn.isEnabled())
        self._wait_gridsnap()
        result = self.win.source_image
        self.assertIsNot(result, original)
        # 32 or 33 per side, not exactly 32: the synthetic's
        # edge clamping duplicates the edge source pixels into
        # an extra row or column on whichever side the sliver
        # survives min_cover (see the module docstring). The
        # overlapping 32x32 region is the check.
        self.assertIn(result.width(), (32, 33))
        self.assertIn(result.height(), (32, 33))
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Snapped to a 8 px grid (%d x %d sprite)"
                         % (result.width(), result.height()))
        exact, _mae = self.match_stats(result, self.sprite)
        self.assertGreaterEqual(exact, 99.0)
        self.assertTrue(self.win.gridsnap_btn.isEnabled())
        self.assertFalse(self.win.gridsnap_cancel_btn.isEnabled())

    def test_worker_failure_warns_and_keeps_source(self):
        import pixel_forge.gridsnap as gridsnap
        from pixel_forge.pixelize import PixelForgeError

        self.win.load_image(self.noisy)
        original = self.win.source_image
        with mock.patch.object(
                gridsnap, "detect_grid",
                side_effect=PixelForgeError(
                    "the image has no size")), \
                mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.win.gridsnap_btn.click()
            self._wait_gridsnap()
        self.assertEqual(warned.call_count, 1)
        self.assertEqual(warned.call_args[0][1],
                         "Could not snap to the pixel grid")
        self.assertIn("no size", warned.call_args[0][2])
        self.assertIs(self.win.source_image, original)
        self.assertTrue(self.win.gridsnap_btn.isEnabled())

    def test_cancel_leaves_image_unchanged(self):
        import pixel_forge.gridsnap as gridsnap

        self.win.load_image(self.noisy)
        original = self.win.source_image

        def blocking_detect(image):
            # Wait until the Cancel button sets the worker's
            # cancel event, then return a grid — the worker
            # checks the event and reports cancelled.
            worker = self.win.gridsnap_worker
            while not worker._cancel_event.is_set():
                time.sleep(0.05)
            return (8.0, 0.0, 0.0)

        with mock.patch.object(gridsnap, "detect_grid",
                               blocking_detect):
            self.win.gridsnap_btn.click()
            deadline = time.monotonic() + 10
            while not self.win.gridsnap_cancel_btn.isEnabled():
                if time.monotonic() > deadline:
                    raise AssertionError("worker did not start")
                self.app.processEvents()
                time.sleep(0.05)
            self.win.gridsnap_cancel_btn.click()
            self._wait_gridsnap()
        self.assertIs(self.win.source_image, original)
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Cancelled, the image is unchanged")
        self.assertTrue(self.win.gridsnap_btn.isEnabled())

    def test_stale_result_is_ignored(self):
        import pixel_forge.gridsnap as gridsnap

        self.win.load_image(self.noisy)
        first = self.win.source_image
        release = threading.Event()

        def slow_detect(image):
            release.wait(timeout=30)
            return (8.0, 0.0, 0.0)

        with mock.patch.object(gridsnap, "detect_grid",
                               slow_detect):
            self.win.gridsnap_btn.click()
            self.app.processEvents()
            # A new image lands mid-run (a drop lands even with
            # the Import button disabled).
            self.assertTrue(self.win.load_image(self.other))
            second = self.win.source_image
            self.assertIsNot(second, first)
            release.set()
            self._wait_gridsnap()
        # The old image's result must not overwrite the new one.
        self.assertIs(self.win.source_image, second)
        data = self.win.source_image.data
        self.assertEqual(tuple(data[0:3]), (0, 0, 200))
        self.assertIn("no longer loaded",
                      self.win.statusBar().currentMessage())


class NormalMapGuiTests(unittest.TestCase):
    """The Image tab's Normal map section (task 43).

    Generation runs in a worker — the tests pump the event
    loop until it finishes (the GridSnapGuiTests pattern).
    The edge image is half black, half white: the pixelizer
    keeps the hard edge exactly, so the map's tilt at the
    edge is checkable, and the map is compared against the
    module's own output on the same result.
    """
    @classmethod
    def setUpClass(cls):
        from pixel_forge.image import Image

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        edge = Image(32, 32)
        for y in range(32):
            for x in range(32):
                value = 0 if x < 16 else 255
                off = (y * 32 + x) * 4
                edge.data[off:off + 4] = bytes(
                    (value, value, value, 255))
        cls.edge = write_image(edge,
                               os.path.join(cls.tmp, "edge.png"))
        cls.other = write_image(
            make_solid_image(32, 32, 0, 0, 200),
            os.path.join(cls.tmp, "blue.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _wait_normalmap(self, timeout=30):
        deadline = time.monotonic() + timeout
        while self.win.normalmap_worker is not None:
            if time.monotonic() > deadline:
                raise AssertionError(
                    "normal map worker did not finish")
            self.app.processEvents()
            time.sleep(0.05)
        self.app.processEvents()

    def _generate(self):
        self.assertTrue(self.win.load_image(self.edge))
        self.win.normalmap_btn.click()
        self._wait_normalmap()
        return self.win.normal_map_image

    def test_buttons_disabled_without_image(self):
        self.assertFalse(self.win.normalmap_btn.isEnabled())
        self.assertFalse(self.win.normalmap_show_btn.isEnabled())
        self.assertFalse(self.win.normalmap_export_btn.isEnabled())
        self.assertFalse(self.win.normalmap_cancel_btn.isEnabled())
        self.assertFalse(self.win.normalmap_show_btn.isChecked())

    def test_strength_spin_default_and_range(self):
        spin = self.win.normalmap_strength_spin
        self.assertEqual(spin.value(), 1.0)
        self.assertEqual(spin.minimum(), 0.25)
        self.assertEqual(spin.maximum(), 4.0)

    def test_generate_enabled_after_load(self):
        self.assertTrue(self.win.load_image(self.edge))
        self.assertTrue(self.win.normalmap_btn.isEnabled())
        self.assertFalse(self.win.normalmap_show_btn.isEnabled())
        self.assertFalse(self.win.normalmap_export_btn.isEnabled())

    def test_generate_shows_map_and_status(self):
        from pixel_forge.normalmap import generate_normal_map

        result = self._generate()
        self.assertIsNotNone(result)
        self.assertEqual((result.width(), result.height()),
                         (self.win.result_image.width(),
                          self.win.result_image.height()))
        # The GUI's map is exactly the module's map on the
        # same result, at the default strength.
        self.assertEqual(
            result,
            generate_normal_map(self.win.result_image,
                                strength=1.0))
        # The hard edge tilts left: red well below flat.
        off = (16 * result.width() + 16) * 4
        self.assertLess(result.data[off], 100)
        self.assertTrue(
            self.win.statusBar().currentMessage().startswith(
                "Normal map generated"))
        self.assertTrue(self.win.normalmap_show_btn.isChecked())
        self.assertEqual(self.win.normalmap_show_btn.text(),
                         "Show sprite")
        self.assertTrue(self.win.normalmap_export_btn.isEnabled())
        self.assertTrue(self.win.normalmap_btn.isEnabled())
        self.assertFalse(self.win.normalmap_cancel_btn.isEnabled())
        # The editor tools go quiet while the map is showing.
        for button in self.win.editor_tool_buttons.values():
            self.assertFalse(button.isEnabled())

    def test_generate_uses_the_strength_spin(self):
        from pixel_forge.normalmap import generate_normal_map

        self.assertTrue(self.win.load_image(self.edge))
        self.win.normalmap_strength_spin.setValue(4.0)
        self.win.normalmap_btn.click()
        self._wait_normalmap()
        self.assertEqual(
            self.win.normal_map_image,
            generate_normal_map(self.win.result_image,
                                strength=4.0))

    def test_show_toggle_switches_back_to_the_sprite(self):
        self._generate()
        # Pixel (0, 0) of the preview is the first sprite pixel
        # at any zoom: the map's is blue, the sprite's is black.
        shown = self.win.after_label.pixmap().toImage().pixel(0, 0)
        self.win.normalmap_show_btn.click()
        self.app.processEvents()
        self.assertFalse(self.win._showing_normal_map)
        self.assertFalse(self.win.normalmap_show_btn.isChecked())
        self.assertEqual(self.win.normalmap_show_btn.text(),
                         "Show normal map")
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Showing the sprite")
        for button in self.win.editor_tool_buttons.values():
            self.assertTrue(button.isEnabled())
        # The preview really changed: the sprite's pixels are
        # not the map's pixels.
        sprite = self.win.after_label.pixmap().toImage().pixel(0, 0)
        self.assertNotEqual(shown, sprite)

    def test_export_writes_png(self):
        from pixel_forge.pixelize import load_image

        self._generate()
        out = os.path.join(self.tmp, "edge-normal.png")
        self.assertTrue(self.win.export_normal_map(out))
        self.assertTrue(os.path.isfile(out))
        image = load_image(out)
        self.assertEqual((image.width(), image.height()), (32, 32))
        self.assertEqual(image, self.win.normal_map_image)

    def test_new_load_clears_the_map(self):
        self._generate()
        self.assertTrue(self.win.load_image(self.other))
        self.assertIsNone(self.win.normal_map_image)
        self.assertFalse(self.win._showing_normal_map)
        self.assertFalse(self.win.normalmap_show_btn.isChecked())
        self.assertEqual(self.win.normalmap_show_btn.text(),
                         "Show normal map")
        self.assertFalse(self.win.normalmap_show_btn.isEnabled())
        self.assertFalse(self.win.normalmap_export_btn.isEnabled())
        self.assertTrue(self.win.normalmap_btn.isEnabled())

    def test_cancel_leaves_no_map(self):
        import pixel_forge.normalmap as normalmap

        self.win.load_image(self.edge)
        # Captured before the patch: calling the module
        # attribute inside the mock would recurse into the
        # mock itself.
        real_generate = normalmap.generate_normal_map

        def blocking_generate(image, strength=1.0):
            worker = self.win.normalmap_worker
            while not worker._cancel_event.is_set():
                time.sleep(0.05)
            return real_generate(image, strength=strength)

        with mock.patch.object(normalmap, "generate_normal_map",
                               blocking_generate):
            self.win.normalmap_btn.click()
            deadline = time.monotonic() + 10
            while not self.win.normalmap_cancel_btn.isEnabled():
                if time.monotonic() > deadline:
                    raise AssertionError("worker did not start")
                self.app.processEvents()
                time.sleep(0.05)
            self.win.normalmap_cancel_btn.click()
            self._wait_normalmap()
        self.assertIsNone(self.win.normal_map_image)
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Cancelled, the normal map is unchanged")
        self.assertTrue(self.win.normalmap_btn.isEnabled())

    def test_worker_failure_warns(self):
        import pixel_forge.normalmap as normalmap
        from pixel_forge.pixelize import PixelForgeError

        self.win.load_image(self.edge)
        with mock.patch.object(
                normalmap, "generate_normal_map",
                side_effect=PixelForgeError("test failure")), \
                mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.win.normalmap_btn.click()
            self._wait_normalmap()
        self.assertEqual(warned.call_count, 1)
        self.assertEqual(warned.call_args[0][1],
                         "Could not generate the normal map")
        self.assertIn("test failure", warned.call_args[0][2])
        self.assertIsNone(self.win.normal_map_image)
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Normal map failed")
        self.assertTrue(self.win.normalmap_btn.isEnabled())

    def test_stale_result_is_ignored(self):
        import pixel_forge.normalmap as normalmap

        self.win.load_image(self.edge)
        release = threading.Event()
        real_generate = normalmap.generate_normal_map

        def slow_generate(image, strength=1.0):
            release.wait(timeout=30)
            return real_generate(image, strength=strength)

        with mock.patch.object(normalmap, "generate_normal_map",
                               slow_generate):
            self.win.normalmap_btn.click()
            self.app.processEvents()
            # A new image lands mid-run: the result changes
            # under the worker.
            self.assertTrue(self.win.load_image(self.other))
            release.set()
            self._wait_normalmap()
        self.assertIsNone(self.win.normal_map_image)
        self.assertIn("changed",
                      self.win.statusBar().currentMessage())


class ProceduralGuiTests(unittest.TestCase):
    """The Image tab's Generate section (task 44).

    Generation is synchronous (milliseconds), so the tests
    click Generate and check the loaded sprite right away —
    no worker to wait for.
    """
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.other = write_image(
            make_solid_image(32, 32, 0, 0, 200),
            os.path.join(cls.tmp, "blue.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _pick_kind(self, kind):
        combo = self.win.procedural_kind_combo
        index = combo.findData(kind)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)
        self.app.processEvents()

    def _generate(self, kind="tree", seed=42, size=32):
        self._pick_kind(kind)
        self.win.procedural_seed_spin.setValue(seed)
        self.win.procedural_size_spin.setValue(size)
        self.win.procedural_generate_btn.click()
        self.app.processEvents()

    def test_nothing_picked_generate_quiet(self):
        combo = self.win.procedural_kind_combo
        self.assertEqual(combo.currentIndex(), -1)
        self.assertIsNone(combo.currentData())
        self.assertFalse(self.win.procedural_generate_btn.isEnabled())
        self.assertTrue(self.win.procedural_random_btn.isEnabled())
        self.assertEqual(self.win.procedural_seed_spin.value(), 1)
        self.assertEqual(self.win.procedural_size_spin.value(), 32)
        self.assertEqual(self.win.procedural_seed_label.text(), "")
        self.assertIsNone(self.win._generated_seed)

    def test_kind_pick_enables_generate(self):
        self._pick_kind("mask")
        self.assertTrue(
            self.win.procedural_generate_btn.isEnabled())
        self.win.procedural_kind_combo.setCurrentIndex(-1)
        self.app.processEvents()
        self.assertFalse(
            self.win.procedural_generate_btn.isEnabled())

    def test_generate_loads_the_sprite(self):
        from pixel_forge.procedural import generate

        self._generate()
        self.assertEqual(self.win.source_image,
                         generate("tree", 42, 32))
        self.assertIsNone(self.win.source_path)
        self.assertEqual((self.win.result_image.width(),
                          self.win.result_image.height()),
                         (32, 32))
        self.assertTrue(self.win.export_btn.isEnabled())
        self.assertTrue(self.win.save_library_btn.isEnabled())

    def test_seed_label_and_status_name_the_seed(self):
        self._generate(kind="item", seed=7)
        label = self.win.procedural_seed_label.text()
        self.assertIn("Seed 7", label)
        self.assertIn("Item", label)
        self.assertEqual(self.win._generated_seed, 7)
        self.assertEqual(self.win._generated_kind, "item")
        message = self.win.statusBar().currentMessage()
        self.assertTrue(message.startswith("Generated "),
                        message)
        self.assertIn("seed 7", message)
        self.assertIn("32 x 32", message)

    def test_status_names_the_shape(self):
        from pixel_forge.procedural import item_shape_for_seed

        self._generate(kind="item", seed=8)
        self.assertIn(item_shape_for_seed(8),
                      self.win.statusBar().currentMessage())

    def test_random_picks_a_new_seed(self):
        self.win.procedural_seed_spin.setValue(1)
        changed = False
        for _ in range(5):
            self.win.procedural_random_btn.click()
            self.app.processEvents()
            if self.win.procedural_seed_spin.value() != 1:
                changed = True
                break
        self.assertTrue(changed)

    def test_size_flows_through(self):
        self._generate(kind="mask", seed=3, size=40)
        self.assertEqual((self.win.source_image.width(),
                          self.win.source_image.height()),
                         (40, 40))
        self.assertEqual(self.win.width_spin.value(), 40)
        self.assertEqual(self.win.height_spin.value(), 40)
        self.assertIsNone(self.win.size_combo.currentData())
        self.assertEqual((self.win.result_image.width(),
                          self.win.result_image.height()),
                         (40, 40))

    def test_new_load_clears_the_seed(self):
        self._generate()
        self.assertEqual(self.win._generated_seed, 42)
        self.assertTrue(self.win.load_image(self.other))
        self.assertIsNone(self.win._generated_seed)
        self.assertIsNone(self.win._generated_kind)
        self.assertEqual(self.win.procedural_seed_label.text(), "")

    def test_discard_confirm_guards_generation(self):
        from pixel_forge.gui import MainWindow

        self._generate()
        self.win._result_edited = True
        with mock.patch.object(
                MainWindow, "_confirm_discard_edits",
                return_value=False) as asked:
            self._pick_kind("mask")
            self.win.procedural_seed_spin.setValue(9)
            self.win.procedural_generate_btn.click()
            self.app.processEvents()
        self.assertEqual(asked.call_count, 1)
        # Declining keeps the old sprite and its seed.
        self.assertEqual(self.win._generated_seed, 42)
        with mock.patch.object(
                MainWindow, "_confirm_discard_edits",
                return_value=True):
            self.win.procedural_generate_btn.click()
            self.app.processEvents()
        self.assertEqual(self.win._generated_seed, 9)
        self.assertEqual(self.win._generated_kind, "mask")


class VideoGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        # 2 seconds at 24 fps, 8 fps extraction = 16 frames.
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))
        # 30 seconds, for the B7 range tests: a range set on this
        # clip sits beyond the 2-second clip's length.
        cls.long_clip = make_test_video(
            os.path.join(cls.tmp, "long.mp4"), duration=30)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(1)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        """Pump the event loop until the Video tab settles — no
        worker running and the debounce timer idle. The video
        path is async since v0.9, so every load and control
        change needs this before its results are asserted."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def test_video_tab_exists_with_controls(self):
        tabs = [self.win.tabs.tabText(i)
                for i in range(self.win.tabs.count())]
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code",
                                "Library", "Tileset"])
        fps = [self.win.video_fps_combo.itemData(i)
               for i in range(self.win.video_fps_combo.count())]
        self.assertEqual(fps, [2, 4, 8, 12, 24])
        self.assertEqual(self.win.video_fps_combo.currentData(), 8)
        self.assertEqual(self.win.video_every_spin.value(), 1)
        self.assertFalse(self.win.video_export_btn.isEnabled())
        self.assertFalse(self.win.video_play_btn.isEnabled())

    def test_no_bare_ampersand_on_video_tab(self):
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def test_load_video_populates_strip(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.app.processEvents()
        self.assertIn(len(self.win.video_frames), (15, 16))
        self.assertEqual(self.win.video_strip.count(),
                         len(self.win.video_frames))
        self.assertEqual(len(self.win.video_keep),
                         len(self.win.video_frames))
        self.assertTrue(all(self.win.video_keep))
        first = self.win.video_frames[0]
        self.assertEqual((first.width(), first.height()), (32, 32))
        self.assertIsNotNone(self.win.video_preview_label.pixmap())
        self.assertFalse(self.win.video_preview_label.pixmap().isNull())
        self.assertTrue(self.win.video_export_btn.isEnabled())
        self.assertTrue(self.win.video_play_btn.isEnabled())
        self.assertIn("32 x 32", self.win.video_info_label.text())
        self.assertIn("Frame 1 of", self.win.video_counter_label.text())

    def test_range_cleared_on_new_import(self):
        # B7: the start/end range belongs to the old clip. Set a
        # range on the 30-second clip, load the 2-second clip, and
        # the range must be back to the whole clip (empty edits)
        # with frames extracted from the new clip.
        self.assertTrue(self.win.load_video(self.long_clip))
        self.assertTrue(self._wait_for_video(timeout=60))
        self.win.video_start_edit.setText("5")
        self.win.video_end_edit.setText("10")
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_start_edit.text(), "")
        self.assertEqual(self.win.video_end_edit.text(), "")
        self.assertEqual(self.win.video_path, self.clip)
        self.assertIn(len(self.win.video_frames), (15, 16))

    def test_stale_range_beyond_new_clip_yields_frames(self):
        # B7 pre-fix repro: a range entirely beyond the new clip's
        # length used to yield "no frames extracted" and a failed
        # import. The new import must clear the range and land
        # frames instead.
        self.assertTrue(self.win.load_video(self.long_clip))
        self.assertTrue(self._wait_for_video(timeout=60))
        self.win.video_start_edit.setText("25")
        self.win.video_end_edit.setText("28")
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_start_edit.text(), "")
        self.assertEqual(self.win.video_end_edit.text(), "")
        self.assertEqual(self.win.video_path, self.clip)
        self.assertIn(len(self.win.video_frames), (15, 16))
        self.assertTrue(self.win.video_export_btn.isEnabled())

    def test_other_video_settings_survive_new_import(self):
        # B7 clears the range only — fps, size, palette, colors,
        # dither, every-Nth, and the preset picker are the user's
        # working choices and must be unchanged by a new import.
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(4))
        self.win.video_every_spin.setValue(2)
        self.win.video_width_spin.setValue(48)
        self.win.video_height_spin.setValue(48)
        self.win.video_palette_combo.setCurrentIndex(
            self.win.video_palette_combo.findData("gameboy"))
        self.win.video_colors_spin.setValue(4)
        self.win.video_dither_check.setChecked(False)
        self.assertTrue(self._wait_for_video())
        before = (self.win.video_fps_combo.currentData(),
                  self.win.video_every_spin.value(),
                  self.win.video_width_spin.value(),
                  self.win.video_height_spin.value(),
                  self.win.video_palette_combo.currentData(),
                  self.win.video_colors_spin.value(),
                  self.win.video_dither_check.isChecked(),
                  self.win.video_preset_combo.currentText())
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        after = (self.win.video_fps_combo.currentData(),
                 self.win.video_every_spin.value(),
                 self.win.video_width_spin.value(),
                 self.win.video_height_spin.value(),
                 self.win.video_palette_combo.currentData(),
                 self.win.video_colors_spin.value(),
                 self.win.video_dither_check.isChecked(),
                 self.win.video_preset_combo.currentText())
        self.assertEqual(after, before)
        self.assertEqual(self.win.video_start_edit.text(), "")
        self.assertEqual(self.win.video_end_edit.text(), "")

    def test_strip_thumbnails_are_not_blank(self):
        from PySide6.QtCore import Qt
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        item = self.win.video_strip.item(0)
        icon = item.icon()
        self.assertFalse(icon.isNull())
        pixmap = icon.pixmap(64, 64)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole), 0)

    def test_keep_drop_changes_export(self):
        from PySide6.QtCore import Qt
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        total = len(self.win.video_frames)
        # Drop the first two frames.
        for row in (0, 1):
            item = self.win.video_strip.item(row)
            item.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertEqual(self.win.video_keep.count(True), total - 2)
        out_dir = os.path.join(self.tmp, "exported")
        self.assertTrue(self.win.export_frames(out_dir))
        files = sorted(os.listdir(out_dir))
        self.assertEqual(len(files), total - 2)
        self.assertEqual(files[0], "frame_001.png")
        self.assertEqual(files[-1],
                         "frame_%03d.png" % (total - 2))
        # Exported frames are the kept ones, renumbered from 1.
        from PySide6.QtGui import QImage
        image = QImage(os.path.join(out_dir, "frame_001.png"))
        self.assertEqual((image.width(), image.height()), (32, 32))

    def test_drop_all_disables_export_and_play(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win._on_drop_all()
        self.app.processEvents()
        self.assertFalse(any(self.win.video_keep))
        self.assertFalse(self.win.video_export_btn.isEnabled())
        self.assertFalse(self.win.video_play_btn.isEnabled())
        self.assertFalse(self.win.export_frames(
            os.path.join(self.tmp, "empty-export")))
        self.win._on_keep_all()
        self.assertTrue(all(self.win.video_keep))
        self.assertTrue(self.win.video_export_btn.isEnabled())

    def test_playback_advances_and_loops(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        total = len(self.win.video_frames)
        self.assertEqual(self.win.video_current, 0)
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_current, 1)
        self.win.video_current = total - 1
        self.win._advance_video_frame()
        # Loop is on by default: wraps to the first kept frame.
        self.assertEqual(self.win.video_current, 0)

    def test_playback_skips_dropped_frames(self):
        from PySide6.QtCore import Qt
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win.video_strip.item(1).setCheckState(
            Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.win.video_current = 0
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_current, 2)

    def test_playback_without_loop_stops_at_end(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        total = len(self.win.video_frames)
        self.win.video_loop_check.setChecked(False)
        self.win.video_current = total - 1
        self.win._on_play_pause()  # starts playing
        self.assertTrue(self.win.video_timer.isActive())
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_current, total - 1)
        self.assertFalse(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_play_btn.text(), "Play")

    def test_play_pause_toggles_timer(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win._on_play_pause()
        self.assertTrue(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_play_btn.text(), "Pause")
        self.win._on_play_pause()
        self.assertFalse(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_play_btn.text(), "Play")

    def test_size_change_repixelizes_frames(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        index = self.win.video_size_combo.findData(16)
        self.win.video_size_combo.setCurrentIndex(index)
        self.assertTrue(self._wait_for_video())
        for frame in self.win.video_frames:
            self.assertEqual((frame.width(), frame.height()), (16, 16))
        # Keep/drop choices survive a re-pixelize.
        self.assertTrue(all(self.win.video_keep))

    def test_fps_change_reextracts(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (15, 16))
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (7, 8))

    def test_control_change_returns_quickly(self):
        # v0.9: a control change schedules a debounced run and
        # returns at once — the old synchronous code blocked the
        # window for seconds on every change.
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        index = self.win.video_fps_combo.findData(4)
        start = time.monotonic()
        self.win.video_fps_combo.setCurrentIndex(index)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0)
        # Nothing has run yet — the change is debounced, not
        # executed inline.
        self.assertTrue(self.win._video_debounce_timer.isActive())
        self.assertIsNone(self.win.video_worker)
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (7, 8))

    def test_rapid_changes_run_once(self):
        # v0.9: five width changes in a row run exactly once,
        # with the settled value — a dragged slider used to
        # re-pixelize on every tick.
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        starts = []
        real_start = self.win._start_video_run

        def counting(settings):
            starts.append(settings["mode"])
            return real_start(settings)

        self.win._start_video_run = counting
        for width in (24, 40, 48, 56, 64):
            self.win.video_width_spin.setValue(width)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(starts, ["repixelize"])
        for frame in self.win.video_frames:
            self.assertEqual((frame.width(), frame.height()),
                             (64, 32))

    def test_superseded_run_results_are_ignored(self):
        # v0.9: two runs started back to back — the first one's
        # results must be dropped by the stale guard, not shown.
        from pixel_forge.image import Image, argb
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        original_count = len(self.win.video_frames)
        self.win._repixelize_video()
        first = self.win.video_worker
        self.assertIsNotNone(first)
        self.win._repixelize_video()
        second = self.win.video_worker
        self.assertIsNot(first, second)
        fake = Image(8, 8)
        fake.fill(argb(255, 0, 0, 255))
        first.done.emit({
            "mode": "repixelize", "frames": [fake], "raw": [],
            "raw_dir": None, "fps": 8, "fresh": False,
            "path": None})
        self.assertEqual(len(self.win.video_frames), original_count)
        self.assertEqual(self.win.video_frames[0].width(), 32)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(len(self.win.video_frames), original_count)
        self.assertEqual(self.win.video_frames[0].width(), 32)

    def test_async_frames_match_synchronous_pipeline(self):
        # v0.9: the worker runs the same extract + pixelize calls
        # the v0.8 synchronous tab did — the frames must be
        # pixel-identical to running that pipeline directly.
        import shutil
        from pixel_forge.pixelize import load_image, pixelize_image
        from pixel_forge.video import extract_frames
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        tmp = tempfile.mkdtemp(prefix="pf-v09-sync-check-")
        try:
            raw = extract_frames(
                self.clip, tmp,
                fps=self.win.video_fps_combo.currentData(),
                every=self.win.video_every_spin.value())
            expected = [pixelize_image(
                load_image(path),
                width=self.win.video_width_spin.value(),
                height=self.win.video_height_spin.value(),
                keep_aspect=self.win.video_aspect_check.isChecked(),
                palette=self.win.video_palette_combo.currentData(),
                max_colors=self.win.video_colors_spin.value(),
                dither=self.win.video_dither_check.isChecked())
                for path in raw]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(len(self.win.video_frames), len(expected))
        for got, want in zip(self.win.video_frames, expected):
            self.assertEqual((got.width(), got.height()),
                             (want.width(), want.height()))
            for y in range(got.height()):
                for x in range(got.width()):
                    self.assertEqual(got.pixel(x, y),
                                     want.pixel(x, y))

    def test_load_keeps_window_responsive(self):
        # v0.9: the import runs on a worker thread, so the event
        # loop must keep running while it works — load_video
        # returns at once with an "Extracting frames…"
        # indicator (task 90: the indicator, not the status
        # message, is the "what is running" display now).
        from PySide6.QtCore import QTimer
        fired = []
        self.assertTrue(self.win.load_video(self.clip))
        self.assertIn("Extracting frames",
                      self.win._activity_text.text())
        QTimer.singleShot(0, lambda: fired.append(True))
        self.app.processEvents()
        self.assertTrue(fired, "event loop did not run during load")
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (15, 16))

    def test_clicking_strip_frame_shows_it(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        item = self.win.video_strip.item(5)
        self.win._on_strip_item_clicked(item)
        self.assertEqual(self.win.video_current, 5)
        self.assertIn("Frame 6 of", self.win.video_counter_label.text())

    def test_load_video_without_ffmpeg_fails_cleanly(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        from pixel_forge import video as video_mod
        with mock.patch.object(video_mod, "find_ffmpeg",
                               return_value=None):
            with mock.patch.object(gui_mod.QMessageBox, "warning") \
                    as warn:
                self.assertFalse(self.win.load_video(self.clip))
        self.assertTrue(warn.called)
        message = warn.call_args[0][2]
        self.assertIn("sudo apt install ffmpeg", message)
        self.assertEqual(self.win.video_frames, [])

    def test_video_tab_screenshot(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win.show()
        self.app.processEvents()
        shot = os.path.join(self.tmp, "pixel-forge-video.png")
        self.win.grab().save(shot)
        self.assertTrue(os.path.exists(shot))
        self.assertGreater(os.path.getsize(shot), 1000)


class SpritesheetGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        # 2 seconds at 24 fps, 8 fps extraction = 16 frames.
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))
        # 30 seconds, for the B8 new-import test: a second clip
        # to load after the first, so the fps controls are
        # asserted across a real import.
        cls.long_clip = make_test_video(
            os.path.join(cls.tmp, "long.mp4"), duration=30)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(1)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        """Pump the event loop until the Video tab settles — no
        worker running and the debounce timer idle. The video
        path is async since v0.9, so every load and control
        change needs this before its results are asserted."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _make_dialog(self):
        from pixel_forge.gui import SpritesheetDialog
        images, names = self.win._kept_sheet_frames()
        cols, padding, fps = self.win._sheet_settings()
        return SpritesheetDialog(
            self.win, images, names, cols, padding, fps,
            os.path.join(self.tmp, "sheet.png"))

    def test_sheet_controls_present_and_disabled_before_load(self):
        cols = [self.win.sheet_cols_combo.itemData(i)
                for i in range(self.win.sheet_cols_combo.count())]
        self.assertEqual(cols, [None, 2, 3, 4, 5, 6, 8, 10, 12, 16])
        self.assertIsNone(self.win.sheet_cols_combo.currentData())
        self.assertEqual((self.win.sheet_padding_spin.minimum(),
                          self.win.sheet_padding_spin.maximum()),
                         (0, 32))
        self.assertEqual(self.win.sheet_padding_spin.value(), 0)
        self.assertEqual(self.win.sheet_fps_spin.value(), 8)
        self.assertFalse(self.win.sheet_preview_btn.isEnabled())
        self.assertFalse(self.win.sheet_export_btn.isEnabled())

    def test_sheet_buttons_enable_after_load(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.app.processEvents()
        self.assertTrue(self.win.sheet_preview_btn.isEnabled())
        self.assertTrue(self.win.sheet_export_btn.isEnabled())

    def test_drop_all_disables_sheet_buttons(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win._on_drop_all()
        self.app.processEvents()
        self.assertFalse(self.win.sheet_preview_btn.isEnabled())
        self.assertFalse(self.win.sheet_export_btn.isEnabled())
        self.assertFalse(self.win.export_spritesheet(
            os.path.join(self.tmp, "empty-sheet.png")))
        self.win._on_keep_all()
        self.assertTrue(self.win.sheet_preview_btn.isEnabled())
        self.assertTrue(self.win.sheet_export_btn.isEnabled())

    def test_sheet_fps_keeps_own_value_on_extraction_change(self):
        # B8: this test used to pin the overwrite — changing the
        # extraction fps dragged the sheet fps with it. The three
        # fps controls keep their own values now.
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.sheet_fps_spin.value(), 8)
        self.assertEqual(self.win.video_play_fps_spin.value(), 8)
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_fps_combo.currentData(), 4)
        self.assertEqual(self.win.sheet_fps_spin.value(), 8)
        self.assertEqual(self.win.video_play_fps_spin.value(), 8)
        self.assertEqual(len(self.win.video_frames), 8)

    def test_fps_controls_keep_own_values_on_reload(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win.video_play_fps_spin.setValue(2)
        self.win.sheet_fps_spin.setValue(12)
        # Every-Nth is an extract setting: changing it reloads
        # (re-extracts) without touching any fps control.
        self.win.video_every_spin.setValue(2)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_fps_combo.currentData(), 8)
        self.assertEqual(self.win.video_play_fps_spin.value(), 2)
        self.assertEqual(self.win.sheet_fps_spin.value(), 12)
        # And a pixelize-only change (re-pixelize, no re-extract)
        # leaves them alone too.
        self.win.video_colors_spin.setValue(4)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_fps_combo.currentData(), 8)
        self.assertEqual(self.win.video_play_fps_spin.value(), 2)
        self.assertEqual(self.win.sheet_fps_spin.value(), 12)

    def test_fps_controls_keep_own_values_on_new_import(self):
        # Task 100: the long clip needs the 60 s wait its
        # siblings use — the 30 s default timed out under load.
        self.win.load_video(self.long_clip)
        self.assertTrue(self._wait_for_video(timeout=60))
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.win.video_play_fps_spin.setValue(2)
        self.win.sheet_fps_spin.setValue(12)
        self.assertTrue(self._wait_for_video(timeout=60))
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video(timeout=60))
        self.assertEqual(self.win.video_fps_combo.currentData(), 4)
        self.assertEqual(self.win.video_play_fps_spin.value(), 2)
        self.assertEqual(self.win.sheet_fps_spin.value(), 12)
        self.assertEqual(len(self.win.video_frames), 8)

    def test_fps_labels(self):
        texts = visible_texts(self.win)
        self.assertIn("Extract fps", texts)
        self.assertIn("Preview fps", texts)
        self.assertIn("Sheet fps", texts)
        self.assertNotIn("Frames per second", texts)
        self.assertNotIn("Playback fps", texts)

    def test_playback_uses_preview_fps(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win.video_play_fps_spin.setValue(2)
        self.win._on_play_pause()
        try:
            self.assertTrue(self.win.video_timer.isActive())
            self.assertEqual(self.win.video_timer.interval(), 500)
        finally:
            self.win._stop_playback()

    def test_sheet_metadata_uses_sheet_fps_not_extraction(self):
        import json
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.win.sheet_fps_spin.setValue(12)
        self.assertTrue(self._wait_for_video())
        png = os.path.join(self.tmp, "fps-sheet.png")
        self.assertTrue(self.win.export_spritesheet(png))
        with open(os.path.join(self.tmp, "fps-sheet.json")) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["fps"], 12)
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [83] * len(meta["frames"]))

    def test_preview_dialog_shows_packed_sheet(self):
        import math
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        dialog = self._make_dialog()
        try:
            count = len(self.win.video_frames)
            cols = math.ceil(math.sqrt(count))
            rows = math.ceil(count / cols)
            self.assertEqual((dialog.sheet.width(),
                              dialog.sheet.height()),
                             (cols * 32, rows * 32))
            self.assertEqual(len(dialog.metadata["frames"]), count)
            self.assertEqual(dialog.metadata["fps"], 8)
            self.assertFalse(dialog.sheet_label.pixmap().isNull())
            self.assertFalse(dialog.frame_label.pixmap().isNull())
            self.assertIn("%d frames" % count,
                          dialog.info_label.text())
            self.assertIn("Frame 1 of %d" % count,
                          dialog.counter_label.text())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_no_bare_ampersand_in_dialog(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        dialog = self._make_dialog()
        try:
            for text in visible_texts(dialog):
                self.assertNotIn("&", text,
                                 "bare & in dialog text: %r" % text)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_dialog_playback_plays_from_the_sheet(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        dialog = self._make_dialog()
        try:
            # Playback frames are cut from the sheet, not the
            # sources: each one matches the kept frame's pixels.
            self.assertEqual(len(dialog.frames),
                             len(self.win.video_frames))
            for index, frame in enumerate(dialog.frames):
                source = self.win.video_frames[index]
                for x, y in ((0, 0), (16, 16), (31, 31)):
                    self.assertEqual(frame.pixel(x, y),
                                     source.pixel(x, y))
            self.assertEqual(dialog.current, 0)
            dialog._advance()
            self.assertEqual(dialog.current, 1)
            dialog.current = len(dialog.frames) - 1
            dialog._advance()
            self.assertEqual(dialog.current, 0)  # loops
            self.assertIn("Frame 1 of",
                          dialog.counter_label.text())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_dialog_play_pause_toggles_timer(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        dialog = self._make_dialog()
        try:
            dialog._on_play_pause()
            self.assertTrue(dialog.timer.isActive())
            self.assertEqual(dialog.play_btn.text(), "Pause")
            dialog._on_play_pause()
            self.assertFalse(dialog.timer.isActive())
            self.assertEqual(dialog.play_btn.text(), "Play")
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_export_spritesheet_writes_png_and_json(self):
        import json
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        total = len(self.win.video_frames)
        # Drop the first two frames: the sheet holds the kept ones.
        for row in (0, 1):
            item = self.win.video_strip.item(row)
            item.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        png = os.path.join(self.tmp, "walk-sheet.png")
        self.assertTrue(self.win.export_spritesheet(png))
        meta_path = os.path.join(self.tmp, "walk-sheet.json")
        self.assertTrue(os.path.isfile(png))
        self.assertTrue(os.path.isfile(meta_path))
        with open(meta_path) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["format"], "pixel-forge-spritesheet")
        self.assertEqual(meta["sheet"], "walk-sheet.png")
        self.assertEqual(len(meta["frames"]), total - 2)
        self.assertEqual(meta["frames"][0]["name"], "frame_001.png")
        self.assertEqual(meta["frames"][-1]["name"],
                         "frame_%03d.png" % (total - 2))
        image = QImage(png)
        self.assertEqual((image.width(), image.height()),
                         (meta["sheet_width"], meta["sheet_height"]))
        # The first frame of the sheet is the third video frame
        # (the first kept one), not the dropped first frame.
        cut = image.copy(meta["frames"][0]["x"],
                         meta["frames"][0]["y"], 32, 32)
        self.assertEqual(cut.pixel(16, 16),
                         self.win.video_frames[2].pixel(16, 16))

    def test_export_spritesheet_uses_cols_and_padding(self):
        import json
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        index = self.win.sheet_cols_combo.findData(2)
        self.win.sheet_cols_combo.setCurrentIndex(index)
        self.win.sheet_padding_spin.setValue(4)
        self.win.sheet_fps_spin.setValue(12)
        png = os.path.join(self.tmp, "cols-sheet.png")
        self.assertTrue(self.win.export_spritesheet(png))
        with open(os.path.join(self.tmp, "cols-sheet.json")) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["columns"], 2)
        self.assertEqual(meta["padding"], 4)
        self.assertEqual(meta["fps"], 12)
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [83] * len(meta["frames"]))
        self.assertEqual(meta["sheet_width"], 2 * 32 + 4)

    def test_spritesheet_dialog_screenshot(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        dialog = self._make_dialog()
        try:
            dialog.show()
            self.app.processEvents()
            shot = os.path.join(self.tmp, "pixel-forge-sheet.png")
            dialog.grab().save(shot)
            self.assertTrue(os.path.exists(shot))
            self.assertGreater(os.path.getsize(shot), 1000)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_atlas_controls_present_with_defaults(self):
        # Task 38: the packing combo starts on Grid — that IS
        # the current behavior, not a pre-selection — and the
        # atlas controls start disabled with the atlas
        # defaults (trim off, padding 1, extrude 0).
        packs = [self.win.sheet_pack_combo.itemData(i)
                 for i in range(self.win.sheet_pack_combo.count())]
        self.assertEqual(packs, ["grid", "maxrects"])
        self.assertEqual(self.win.sheet_pack_combo.currentData(),
                         "grid")
        self.assertFalse(self.win.sheet_trim_check.isChecked())
        self.assertFalse(self.win.sheet_trim_check.isEnabled())
        self.assertEqual((self.win.sheet_atlas_padding_spin.minimum(),
                          self.win.sheet_atlas_padding_spin.maximum()),
                         (0, 64))
        self.assertEqual(self.win.sheet_atlas_padding_spin.value(), 1)
        self.assertFalse(
            self.win.sheet_atlas_padding_spin.isEnabled())
        self.assertEqual((self.win.sheet_extrude_spin.minimum(),
                          self.win.sheet_extrude_spin.maximum()),
                         (0, 16))
        self.assertEqual(self.win.sheet_extrude_spin.value(), 0)
        self.assertFalse(self.win.sheet_extrude_spin.isEnabled())
        self.assertTrue(self.win.sheet_cols_combo.isEnabled())
        self.assertTrue(self.win.sheet_padding_spin.isEnabled())

    def test_pack_combo_toggles_control_sets(self):
        # Grid controls live under Grid, atlas controls under
        # MaxRects — each set is disabled under the other
        # packer, so a control never looks live while its value
        # is ignored.
        index = self.win.sheet_pack_combo.findData("maxrects")
        self.win.sheet_pack_combo.setCurrentIndex(index)
        self.assertFalse(self.win.sheet_cols_combo.isEnabled())
        self.assertFalse(self.win.sheet_padding_spin.isEnabled())
        self.assertTrue(self.win.sheet_trim_check.isEnabled())
        self.assertTrue(
            self.win.sheet_atlas_padding_spin.isEnabled())
        self.assertTrue(self.win.sheet_extrude_spin.isEnabled())
        index = self.win.sheet_pack_combo.findData("grid")
        self.win.sheet_pack_combo.setCurrentIndex(index)
        self.assertTrue(self.win.sheet_cols_combo.isEnabled())
        self.assertTrue(self.win.sheet_padding_spin.isEnabled())
        self.assertFalse(self.win.sheet_trim_check.isEnabled())
        self.assertFalse(
            self.win.sheet_atlas_padding_spin.isEnabled())
        self.assertFalse(self.win.sheet_extrude_spin.isEnabled())

    def test_maxrects_export_writes_atlas_meta(self):
        import json
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        total = len(self.win.video_frames)
        index = self.win.sheet_pack_combo.findData("maxrects")
        self.win.sheet_pack_combo.setCurrentIndex(index)
        png = os.path.join(self.tmp, "atlas-sheet.png")
        self.assertTrue(self.win.export_spritesheet(png))
        meta_path = os.path.join(self.tmp, "atlas-sheet.json")
        self.assertTrue(os.path.isfile(png))
        self.assertTrue(os.path.isfile(meta_path))
        with open(meta_path) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["format"], "pixel-forge-atlas")
        self.assertEqual(meta["packer"], "maxrects-bssf")
        self.assertEqual(meta["padding"], 1)
        self.assertEqual(len(meta["frames"]), total)
        self.assertEqual(meta["frames"][0]["name"], "frame_001.png")
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [125] * total)
        self.assertEqual(meta["sheets"][0]["file"],
                         "atlas-sheet.png")
        self.assertIn("Exported atlas: 1 sheet",
                      self.win.statusBar().currentMessage())
        self.assertIn("%d frames" % total,
                      self.win.statusBar().currentMessage())

    def test_maxrects_export_carries_durations_and_tags(self):
        import json
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.win.video_durations[0] = 500
        self.assertTrue(self.win._add_video_tag("walk", 0, 3))
        index = self.win.sheet_pack_combo.findData("maxrects")
        self.win.sheet_pack_combo.setCurrentIndex(index)
        png = os.path.join(self.tmp, "timed-atlas.png")
        self.assertTrue(self.win.export_spritesheet(png))
        with open(os.path.join(self.tmp, "timed-atlas.json")) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["frames"][0]["duration_ms"], 500)
        self.assertEqual(meta["frames"][1]["duration_ms"], 125)
        self.assertEqual(meta["tags"],
                         [{"name": "walk", "from": 0, "to": 3}])

    def test_grid_export_unchanged_with_pack_controls(self):
        # The default is still Grid, and a grid export with the
        # packing controls present writes the old format with
        # the old status message.
        import json
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.sheet_pack_combo.currentData(),
                         "grid")
        png = os.path.join(self.tmp, "still-grid.png")
        self.assertTrue(self.win.export_spritesheet(png))
        with open(os.path.join(self.tmp, "still-grid.json")) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["format"], "pixel-forge-spritesheet")
        self.assertIn("grid",
                      self.win.statusBar().currentMessage())

    def test_preview_dialog_maxrects_shows_packed_sheet(self):
        from pixel_forge.gui import SpritesheetDialog
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        images, names = self.win._kept_sheet_frames()
        durations = self.win._kept_durations()
        dialog = SpritesheetDialog(
            self.win, images, names, None, 0, 8,
            os.path.join(self.tmp, "atlas.png"),
            durations=durations, pack="maxrects",
            atlas_padding=1, extrude=0)
        try:
            self.assertEqual(dialog.metadata["format"],
                             "pixel-forge-atlas")
            self.assertFalse(dialog.sheet.isNull())
            self.assertIn("MaxRects atlas",
                          dialog.info_label.text())
            # Playback runs over the input frames — one per
            # kept frame, at the input durations.
            self.assertEqual(len(dialog.frames), len(images))
            self.assertEqual(dialog._frame_duration(0), 125)
            dialog._on_play_pause()
            self.assertEqual(dialog.timer.interval(), 125)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_preview_dialog_maxrects_keeps_deduped_frames(self):
        # Two identical frames dedupe to one rect in the atlas,
        # but the preview still plays both, in their own slots.
        from pixel_forge.gui import SpritesheetDialog
        frames = [make_solid_image(32, 32, 255, 0, 0),
                  make_solid_image(32, 32, 255, 0, 0),
                  make_solid_image(32, 32, 0, 255, 0)]
        names = ["a.png", "b.png", "c.png"]
        dialog = SpritesheetDialog(
            self.win, frames, names, None, 0, 8, "atlas.png",
            durations=[125, 500, 250], pack="maxrects")
        try:
            self.assertEqual(len(dialog.metadata["frames"]), 2)
            self.assertEqual(
                dialog.metadata["frames"][0]["aliases"], ["b.png"])
            self.assertEqual(len(dialog.frames), 3)
            self.assertEqual(dialog._frame_duration(0), 125)
            self.assertEqual(dialog._frame_duration(1), 500)
            self.assertEqual(dialog._frame_duration(2), 250)
        finally:
            dialog.close()
            dialog.deleteLater()


class PromptGuiTests(unittest.TestCase):
    """Prompt tab — generation runs against the fake sd-cli, so
    these prove the tab, the worker thread, and the keeper flow,
    not the real model."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.stub = write_stub_engine(cls.tmp)
        # The dummy model carries the PINNED file name: a
        # differently-named model hits the task-27 fallback
        # confirm on every generation, and these tests are not
        # testing that dialog.
        cls.model = os.path.join(
            cls.tmp, "LCM_Dreamshaper_v7-f16.gguf")
        with open(cls.model, "wb") as f:
            f.write(b"not a real model")

    def setUp(self):
        self._env = stub_env(self.stub, self.model)
        self._env.__enter__()
        # Batches and history live in the library folder now
        # (task 20), so generation must not write into the real
        # ~/pixel-forge-library/ during the suite.
        self.lib_dir = tempfile.mkdtemp()
        # The sandbox reports well under 3 GiB free, which would
        # trip the task-27 RAM confirm on every generation —
        # tests run with plenty of memory unless they test the
        # confirm itself.
        from unittest import mock
        self._ram_patch = mock.patch(
            "pixel_forge.gui.available_ram_bytes",
            return_value=8 * 1024 ** 3)
        self._ram_patch.start()
        from pixel_forge.gui import MainWindow
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self._ram_patch.stop()
        self._env.__exit__(None, None, None)
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _wait_for_worker(self, timeout=30):
        """Pump events until the prompt worker stops, or fail."""
        import time
        deadline = time.monotonic() + timeout
        while self.win._prompt_generating():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("prompt worker did not finish in %ss"
                          % timeout)
            time.sleep(0.05)
        self.app.processEvents()

    def _generate(self, prompt="a knight"):
        self.win.prompt_edit.setText(prompt)
        self.win._on_prompt_generate()
        self._wait_for_worker()

    def test_prompt_tab_exists_with_controls(self):
        tabs = [self.win.tabs.tabText(i)
                for i in range(self.win.tabs.count())]
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code",
                                "Library", "Tileset"])
        self.assertEqual(self.win.prompt_edit.placeholderText(),
                         "a knight with a red shield")
        self.assertTrue(self.win.prompt_negative_edit.text())
        self.assertTrue(self.win.prompt_generate_btn.isEnabled())
        self.assertFalse(self.win.prompt_cancel_btn.isEnabled())
        self.assertFalse(self.win.prompt_export_btn.isEnabled())
        self.assertEqual(self.win.prompt_progress.value(), 0)
        sizes = [self.win.prompt_size_combo.itemData(i)
                 for i in range(self.win.prompt_size_combo.count())]
        self.assertEqual(sizes, [16, 32, 48, 64, 128, None])
        palettes = [self.win.prompt_palette_combo.itemData(i)
                    for i in range(self.win.prompt_palette_combo.count())]
        self.assertEqual(palettes, ["image", "pico8", "gameboy",
                                    "grayscale", "resurrect64",
                                    "endesga32", "sweetie16"])
        self.assertEqual(self.win.prompt_colors_spin.value(), 16)
        self.assertTrue(self.win.prompt_dither_check.isChecked())

    def test_no_bare_ampersand_on_prompt_tab(self):
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def test_status_line_shows_engine_and_model(self):
        text = self.win.prompt_status_label.text()
        self.assertIn("Engine: found at", text)
        self.assertIn(self.stub, text)
        self.assertIn("Model: found at", text)
        self.assertIn(self.model, text)

    def test_status_label_is_tall_enough_for_wrapped_text(self):
        # The wrapped two-line status once overflowed into the Size
        # section below (38 px given, 51 px needed) because the
        # layout ignored the label's heightForWidth. The label must
        # be at least as tall as its wrapped text needs.
        self.win.show()
        self.app.processEvents()
        label = self.win.prompt_status_label
        needed = label.heightForWidth(230)
        self.assertGreaterEqual(label.height(), needed,
                                "status label %d px tall, wrapped "
                                "text needs %d px"
                                % (label.height(), needed))

    def test_status_line_shows_missing(self):
        from unittest import mock
        from pixel_forge import imagegen as imagegen_mod
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None), \
                mock.patch.object(imagegen_mod, "find_model",
                                  return_value=None):
            self.win._refresh_prompt_status()
        text = self.win.prompt_status_label.text()
        self.assertIn("Engine: missing", text)
        self.assertIn("--with-image-engine", text)
        self.assertIn("Model: missing", text)
        # B13: the line once named only ~/pixel-forge-models/,
        # so a user following it downloaded a second 2.1 GB copy
        # instead of using the shared home find_model checks first.
        self.assertIn("~/design-shell-models/", text)
        self.assertIn("~/pixel-forge-models/", text)
        self.assertLess(text.index("~/design-shell-models/"),
                        text.index("~/pixel-forge-models/"),
                        "shared home first — find_model checks it first")

    def test_status_label_is_tall_enough_when_missing(self):
        # The missing-model line is the longest status text (it
        # names both model homes); the label must still be tall
        # enough for its wrapped lines, not clipped into the Size
        # section below.
        from unittest import mock
        from pixel_forge import imagegen as imagegen_mod
        self.win.show()
        self.app.processEvents()
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None), \
                mock.patch.object(imagegen_mod, "find_model",
                                  return_value=None):
            self.win._refresh_prompt_status()
        self.app.processEvents()
        label = self.win.prompt_status_label
        needed = label.heightForWidth(230)
        self.assertGreaterEqual(label.height(), needed,
                                "status label %d px tall, wrapped "
                                "text needs %d px"
                                % (label.height(), needed))

    def test_generate_without_engine_refuses(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        from pixel_forge import imagegen as imagegen_mod
        self.win.prompt_edit.setText("a knight")
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None):
            with mock.patch.object(gui_mod.QMessageBox, "warning") \
                    as warn:
                self.win._on_prompt_generate()
        self.assertTrue(warn.called)
        self.assertIn("--with-image-engine", warn.call_args[0][2])
        self.assertIsNone(self.win.prompt_worker)
        self.assertEqual(self.win.prompt_strip.count(), 0)

    def test_generate_empty_prompt_refuses(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        self.win.prompt_edit.setText("   ")
        with mock.patch.object(gui_mod.QMessageBox, "warning") as warn:
            self.win._on_prompt_generate()
        self.assertTrue(warn.called)
        # Task 22: messages start with a capital letter.
        self.assertIn("Type a prompt first", warn.call_args[0][2])
        self.assertIsNone(self.win.prompt_worker)

    def test_prompt_cancel_with_corrupt_batch_shows_dialog(self):
        # Review B5: a corrupt batch.json on cancel used to raise an
        # uncaught JSONDecodeError out of the slot. Now the dialog
        # names the file and the window stays usable.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        from pixel_forge import imagegen as imagegen_mod
        work = tempfile.mkdtemp()
        _batch, bdir = imagegen_mod.new_batch(work, "a knight")
        path = bdir / "batch.json"
        good = path.read_bytes()
        truncated = good[:len(good) // 2]
        path.write_bytes(truncated)
        self.win.prompt_batch_dir = bdir
        self.win.prompt_worker = None
        self.win.prompt_candidates = []
        with mock.patch.object(gui_mod.QMessageBox, "warning") as warn:
            self.win._on_prompt_cancelled()
        self.assertTrue(warn.called)
        self.assertIn("batch.json", warn.call_args[0][2])
        self.assertIn("unreadable", warn.call_args[0][2])
        self.assertTrue(self.win.prompt_generate_btn.isEnabled())
        self.assertFalse(self.win.prompt_cancel_btn.isEnabled())
        self.assertEqual(path.read_bytes(), truncated)

    def test_pick_keeper_with_corrupt_batch_shows_status(self):
        # The keeper slot catches PixelForgeError already; with
        # load_batch wrapped, a corrupt batch lands there as a plain
        # status-bar message instead of a traceback. The candidate
        # file is valid, so the load succeeds first (task 80) and
        # the corrupt batch.json is what fails.
        from pixel_forge import imagegen as imagegen_mod
        work = tempfile.mkdtemp()
        _batch, bdir = imagegen_mod.new_batch(work, "a knight")
        write_image(make_solid_image(8, 8, 255, 0, 0),
                    str(bdir / "cand-01.png"))
        path = bdir / "batch.json"
        good = path.read_bytes()
        truncated = good[:len(good) // 2]
        path.write_bytes(truncated)
        self.win.prompt_batch_dir = bdir
        self.win._pick_prompt_keeper("cand-01.png")
        self.assertIn("batch.json",
                      self.win.statusBar().currentMessage())
        self.assertIsNone(self.win.prompt_keeper)
        self.assertEqual(path.read_bytes(), truncated)

    def test_generate_three_candidates_and_keeper(self):
        self._generate()
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertEqual(self.win.prompt_keeper, "cand-01.png")
        result = self.win.prompt_result_image
        self.assertIsNotNone(result)
        self.assertEqual((result.width(), result.height()), (32, 32))
        self.assertIsNotNone(self.win.prompt_after_label.pixmap())
        self.assertFalse(self.win.prompt_after_label.pixmap().isNull())
        self.assertIsNotNone(self.win.prompt_before_label.pixmap())
        self.assertFalse(
            self.win.prompt_before_label.pixmap().isNull())
        self.assertTrue(self.win.prompt_export_btn.isEnabled())
        self.assertIn("32 x 32", self.win.prompt_info_label.text())
        self.assertTrue(self.win.prompt_generate_btn.isEnabled())
        self.assertFalse(self.win.prompt_cancel_btn.isEnabled())
        from pixel_forge.imagegen import load_batch
        batch = load_batch(self.win.prompt_batch_dir)
        self.assertEqual(len(batch["candidates"]), 3)
        seeds = [c["seed"] for c in batch["candidates"]]
        self.assertEqual(len(set(seeds)), 3)
        self.assertEqual(batch["keeper"], "cand-01.png")

    def test_regenerate_with_candidates_asks_first_decline_keeps_batch(
            self):
        # B6: Re-Generate used to silently destroy the previous
        # batch, keeper included. Declining the confirm leaves
        # everything untouched — candidates, keeper, batch.json.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        self._generate()
        bdir = self.win.prompt_batch_dir
        batch_json = (bdir / "batch.json").read_bytes()
        candidates = [c["file"] for c in self.win.prompt_candidates]
        keeper = self.win.prompt_keeper
        worker = self.win.prompt_worker
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No) as ask:
            self.win.prompt_edit.setText("a dragon")
            self.win._on_prompt_generate()
        self.assertTrue(ask.called)
        text = ask.call_args[0][2]
        self.assertIn("3 candidates", text)
        self.assertIn("keeper", text)
        self.assertEqual(ask.call_args[0][4],
                         QMessageBox.StandardButton.No)
        self.assertEqual(self.win.prompt_batch_dir, bdir)
        self.assertEqual((bdir / "batch.json").read_bytes(), batch_json)
        self.assertEqual([c["file"] for c in self.win.prompt_candidates],
                         candidates)
        self.assertEqual(self.win.prompt_keeper, keeper)
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertIs(self.win.prompt_worker, worker)
        self.assertTrue(self.win.prompt_generate_btn.isEnabled())

    def test_regenerate_with_candidates_accept_replaces_batch(self):
        # Accepting the confirm replaces the batch in the tab —
        # but the old batch is no longer destroyed (task 20): its
        # folder stays, and it is recorded in History.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        self._generate()
        old_dir = self.win.prompt_batch_dir
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.win.prompt_edit.setText("a dragon")
            self.win._on_prompt_generate()
        self._wait_for_worker()
        self.assertTrue(old_dir.is_dir())
        self.assertTrue((old_dir / "batch.json").is_file())
        self.assertNotEqual(self.win.prompt_batch_dir, old_dir)
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertEqual(self.win.prompt_keeper, "cand-01.png")
        from pixel_forge.imagegen import load_batch
        batch = load_batch(self.win.prompt_batch_dir)
        self.assertEqual(batch["prompt"], "a dragon")
        entries = [self.win.prompt_history_list.item(i).text()
                   for i in range(self.win.prompt_history_list.count())]
        self.assertTrue(any("a knight" in e for e in entries),
                        entries)
        self.assertTrue(any("a dragon" in e for e in entries),
                        entries)

    def test_regenerate_without_candidates_asks_nothing(self):
        # No candidates means nothing to lose — Generate runs
        # straight away with no dialog.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod.QMessageBox, "question") as ask:
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
            self._wait_for_worker()
        self.assertFalse(ask.called)
        self.assertEqual(self.win.prompt_strip.count(), 3)

    def test_regenerate_confirm_without_keeper_omits_keeper_clause(
            self):
        # With candidates but no keeper picked, the dialog names
        # the candidates and says nothing about a keeper.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        self._generate()
        self.win.prompt_keeper = None
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No) as ask:
            self.win.prompt_edit.setText("a dragon")
            self.win._on_prompt_generate()
        self.assertTrue(ask.called)
        text = ask.call_args[0][2]
        self.assertIn("3 candidates", text)
        self.assertNotIn("keeper", text)

    def _use_other_model(self):
        """Point the stub env at a non-pinned .gguf for this test
        (task 27). tearDown exits whatever env is current."""
        other = os.path.join(self.tmp, "other-model.gguf")
        with open(other, "wb") as f:
            f.write(b"gguf")
        self._env.__exit__(None, None, None)
        self._env = stub_env(self.stub, other)
        self._env.__enter__()
        return other

    def test_non_pinned_model_asks_first_decline_keeps_tab(self):
        # Task 27: generating with a model that is not the pinned
        # LCM file asks first — default No — and declining leaves
        # the tab untouched: no worker, no batch, no dialog after.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        self._use_other_model()
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No) as ask:
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
        self.assertTrue(ask.called)
        title, text = ask.call_args[0][1], ask.call_args[0][2]
        self.assertEqual(title, "Use a different model?")
        self.assertIn("other-model.gguf", text)
        self.assertIn("LCM_Dreamshaper_v7-f16.gguf", text)
        self.assertEqual(ask.call_args[0][4],
                         QMessageBox.StandardButton.No)
        self.assertIsNone(self.win.prompt_worker)
        self.assertIsNone(self.win.prompt_batch_dir)
        self.assertEqual(self.win.prompt_strip.count(), 0)
        self.assertTrue(self.win.prompt_generate_btn.isEnabled())

    def test_non_pinned_model_accept_generates(self):
        # Accepting the fallback confirm generates normally.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        self._use_other_model()
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes) as ask:
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
        self.assertEqual(ask.call_count, 1)
        self._wait_for_worker()
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertEqual(self.win.prompt_keeper, "cand-01.png")

    def test_low_ram_asks_first_decline_keeps_tab(self):
        # Task 27: under 3 GiB free, generation asks first —
        # default No — naming the free memory and the ~2.1 GB
        # model. Declining leaves the tab untouched.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod, "available_ram_bytes",
                               return_value=1 * 1024 ** 3), \
                mock.patch.object(
                    gui_mod.QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.No) \
                as ask:
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
        self.assertTrue(ask.called)
        title, text = ask.call_args[0][1], ask.call_args[0][2]
        self.assertEqual(title, "Low memory?")
        self.assertIn("1.0 GB", text)
        self.assertIn("2.1 GB", text)
        self.assertEqual(ask.call_args[0][4],
                         QMessageBox.StandardButton.No)
        self.assertIsNone(self.win.prompt_worker)
        self.assertIsNone(self.win.prompt_batch_dir)
        self.assertEqual(self.win.prompt_strip.count(), 0)

    def test_low_ram_accept_generates(self):
        # Accepting the low-memory confirm generates normally.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod, "available_ram_bytes",
                               return_value=1 * 1024 ** 3), \
                mock.patch.object(
                    gui_mod.QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.Yes) \
                as ask:
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
        self.assertEqual(ask.call_count, 1)
        self._wait_for_worker()
        self.assertEqual(self.win.prompt_strip.count(), 3)

    def test_info_label_names_failed_keeper_load(self):
        # Task 27: a candidate whose file is corrupt fails to
        # load as keeper — the info label names it and the
        # reason, not just the status bar. The keeper stays the
        # good one.
        self._generate()
        bdir = self.win.prompt_batch_dir
        (bdir / "cand-02.png").write_bytes(b"not a png at all")
        self.win._pick_prompt_keeper("cand-02.png")
        self.app.processEvents()
        info = self.win.prompt_info_label.text()
        self.assertIn("cand-02.png", info)
        self.assertIn("Could not load", info)
        self.assertEqual(self.win.prompt_keeper, "cand-01.png")

    def test_failed_keeper_pick_leaves_batch_untouched(self):
        # Task 80: the pick used to mark the keeper in
        # batch.json before loading the image, so a corrupt
        # candidate left batch.json naming the bad file while
        # the tab kept the good one. A failed pick now leaves
        # batch.json byte-identical — the batch and the tab
        # agree on the good keeper.
        from pixel_forge.imagegen import load_batch
        self._generate()
        bdir = self.win.prompt_batch_dir
        before = (bdir / "batch.json").read_bytes()
        (bdir / "cand-02.png").write_bytes(b"not a png at all")
        self.win._pick_prompt_keeper("cand-02.png")
        self.app.processEvents()
        self.assertEqual((bdir / "batch.json").read_bytes(), before)
        batch = load_batch(bdir)
        self.assertEqual(batch["keeper"], "cand-01.png")
        self.assertEqual(self.win.prompt_keeper, "cand-01.png")
        status = {c["file"]: c["status"]
                  for c in batch["candidates"]}
        self.assertEqual(status["cand-01.png"], "kept")
        self.assertEqual(status["cand-02.png"], "pending")

    def test_failed_first_keeper_pick_leaves_no_keeper(self):
        # Task 80, no prior keeper: a corrupt first pick leaves
        # no keeper anywhere — batch.json keeps keeper None and
        # the candidate stays pending, and the tab agrees.
        from pixel_forge import imagegen as imagegen_mod
        work = tempfile.mkdtemp()
        _batch, bdir = imagegen_mod.new_batch(work, "a knight")
        batch = imagegen_mod.load_batch(bdir)
        batch["candidates"] = [
            {"file": "cand-01.png", "seed": 1, "status": "pending"},
        ]
        imagegen_mod.save_batch(bdir, batch)
        (bdir / "cand-01.png").write_bytes(b"not a png at all")
        self.win.prompt_batch_dir = bdir
        self.win.prompt_candidates = batch["candidates"]
        self.win._pick_prompt_keeper("cand-01.png")
        self.app.processEvents()
        self.assertIsNone(self.win.prompt_keeper)
        batch = imagegen_mod.load_batch(bdir)
        self.assertIsNone(batch["keeper"])
        self.assertEqual(batch["candidates"][0]["status"],
                         "pending")
        self.assertIn("cand-01.png",
                      self.win.prompt_info_label.text())

    def test_good_keeper_pick_marks_batch_and_tab(self):
        # Task 80, happy path: picking a good candidate marks
        # it keeper in both places and un-keeps the old one.
        from pixel_forge.imagegen import load_batch
        self._generate()
        bdir = self.win.prompt_batch_dir
        self.win._pick_prompt_keeper("cand-02.png")
        self.app.processEvents()
        self.assertEqual(self.win.prompt_keeper, "cand-02.png")
        batch = load_batch(bdir)
        self.assertEqual(batch["keeper"], "cand-02.png")
        status = {c["file"]: c["status"]
                  for c in batch["candidates"]}
        self.assertEqual(status["cand-01.png"], "pending")
        self.assertEqual(status["cand-02.png"], "kept")

    def test_info_label_names_failed_preview_pixelize(self):
        # Task 27: a pixelize failure in the preview names the
        # keeper and the reason in the info label.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        from pixel_forge.pixelize import PixelForgeError
        self._generate()
        with mock.patch.object(
                gui_mod, "pixelize_image",
                side_effect=PixelForgeError("boom")):
            self.win._update_prompt_preview()
        info = self.win.prompt_info_label.text()
        self.assertIn("cand-01.png", info)
        self.assertIn("Could not pixelize", info)
        self.assertIn("boom", info)

    def test_generation_does_not_block_gui(self):
        # Each candidate takes 1s with the stub; the window's event
        # loop must keep running through it — a timer fired mid-run
        # proves the GUI thread was never blocked.
        import time
        from PySide6.QtCore import QTimer
        os.environ["STUB_DELAY"] = "1"
        try:
            fired = []
            QTimer.singleShot(500, lambda: fired.append(True))
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
            checked = False
            deadline = time.monotonic() + 30
            while self.win._prompt_generating():
                self.app.processEvents()
                if not checked and time.monotonic() > deadline - 28.5:
                    # ~1.5s in: the timer must have fired while the
                    # worker was still running.
                    self.assertTrue(fired, "GUI event loop stalled "
                                    "during generation")
                    self.assertTrue(self.win._prompt_generating())
                    checked = True
                if time.monotonic() > deadline:
                    self.fail("prompt worker did not finish")
                time.sleep(0.05)
            # The worker may have finished between the last pump and
            # the isRunning check — deliver the queued done signal
            # before asserting on the strip.
            self.app.processEvents()
            self.assertTrue(checked)
            self.assertEqual(self.win.prompt_strip.count(), 3)
        finally:
            os.environ.pop("STUB_DELAY", None)

    def test_cancel_stops_generation(self):
        os.environ["STUB_DELAY"] = "1"
        try:
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
            import time
            time.sleep(0.5)
            self.app.processEvents()
            self.assertTrue(self.win._prompt_generating())
            self.win._on_prompt_cancel()
            self._wait_for_worker(timeout=10)
            self.assertFalse(self.win._prompt_generating())
            self.assertTrue(self.win.prompt_generate_btn.isEnabled())
            self.assertFalse(self.win.prompt_cancel_btn.isEnabled())
            self.assertIn("Cancelled",
                          self.win.statusBar().currentMessage())
        finally:
            os.environ.pop("STUB_DELAY", None)

    def test_clicking_candidate_changes_keeper(self):
        self._generate()
        item = self.win.prompt_strip.item(1)
        self.win._on_prompt_strip_clicked(item)
        self.assertEqual(self.win.prompt_keeper, "cand-02.png")
        from pixel_forge.imagegen import load_batch
        batch = load_batch(self.win.prompt_batch_dir)
        self.assertEqual(batch["keeper"], "cand-02.png")
        self.assertIsNotNone(self.win.prompt_result_image)

    def test_size_change_repixelizes_keeper(self):
        self._generate()
        index = self.win.prompt_size_combo.findData(16)
        self.win.prompt_size_combo.setCurrentIndex(index)
        self.app.processEvents()
        result = self.win.prompt_result_image
        self.assertEqual((result.width(), result.height()), (16, 16))

    def test_export_keeper_writes_png(self):
        from PySide6.QtGui import QImage
        self._generate()
        out = os.path.join(self.tmp, "keeper.png")
        self.assertTrue(self.win.export_prompt_keeper(out))
        self.assertTrue(os.path.isfile(out))
        image = QImage(out)
        self.assertEqual((image.width(), image.height()), (32, 32))

    def test_prompt_tab_screenshot(self):
        self._generate()
        self.win.show()
        self.app.processEvents()
        shot = os.path.join(self.tmp, "pixel-forge-prompt.png")
        self.win.grab().save(shot)
        self.assertTrue(os.path.exists(shot))
        self.assertGreater(os.path.getsize(shot), 1000)


class PromptIterationTests(unittest.TestCase):
    """Task 20 (prompt iteration): keeper seed, generation
    history, elapsed time, regenerate-one, "more like this one",
    prompt history, negative presets, steps/cfg/size controls."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.stub = write_stub_engine(cls.tmp)
        # Pinned file name, like PromptGuiTests: a
        # differently-named model hits the task-27 fallback
        # confirm on every generation.
        cls.model = os.path.join(
            cls.tmp, "LCM_Dreamshaper_v7-f16.gguf")
        with open(cls.model, "wb") as f:
            f.write(b"not a real model")

    def setUp(self):
        self._env = stub_env(self.stub, self.model)
        self._env.__enter__()
        self.lib_dir = tempfile.mkdtemp()
        # Plenty of memory, like PromptGuiTests — the sandbox
        # reports well under 3 GiB free, which would trip the
        # task-27 RAM confirm on every generation.
        from unittest import mock
        self._ram_patch = mock.patch(
            "pixel_forge.gui.available_ram_bytes",
            return_value=8 * 1024 ** 3)
        self._ram_patch.start()
        from pixel_forge.gui import MainWindow
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self._ram_patch.stop()
        self._env.__exit__(None, None, None)
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _wait_for_worker(self, timeout=30):
        deadline = time.monotonic() + timeout
        while self.win._prompt_generating():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("prompt worker did not finish in %ss"
                          % timeout)
            time.sleep(0.05)
        self.app.processEvents()

    def _generate(self, prompt="a knight"):
        self.win.prompt_edit.setText(prompt)
        self.win._on_prompt_generate()
        self._wait_for_worker()

    def test_seed_label_hidden_until_a_keeper_exists(self):
        self.assertTrue(self.win.prompt_seed_label.isHidden())
        self._generate()
        self.assertFalse(self.win.prompt_seed_label.isHidden())
        from pixel_forge.imagegen import load_batch
        batch = load_batch(self.win.prompt_batch_dir)
        keeper = next(c for c in batch["candidates"]
                      if c["file"] == self.win.prompt_keeper)
        self.assertEqual(self.win.prompt_seed_label.text(),
                         "Seed: %d" % keeper["seed"])

    def test_seed_label_follows_the_keeper(self):
        self._generate()
        from pixel_forge.imagegen import load_batch
        batch = load_batch(self.win.prompt_batch_dir)
        second = batch["candidates"][1]
        self.win._pick_prompt_keeper(second["file"])
        self.assertEqual(self.win.prompt_seed_label.text(),
                         "Seed: %d" % second["seed"])

    def test_generation_controls_default_to_the_lcm_pins(self):
        self.assertEqual(self.win.prompt_steps_spin.value(), 4)
        self.assertEqual(self.win.prompt_cfg_spin.value(), 1.5)
        self.assertEqual(self.win.prompt_gen_size_spin.value(), 512)

    def test_generation_controls_reach_the_engine(self):
        log = os.path.join(self.tmp, "iter-argv.log")
        if os.path.exists(log):
            os.remove(log)
        self.win.prompt_steps_spin.setValue(8)
        self.win.prompt_cfg_spin.setValue(2.5)
        self.win.prompt_gen_size_spin.setValue(256)
        with stub_env(self.stub, self.model, {"STUB_LOG": log}):
            self._generate()
        lines = open(log).read().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("--steps 8", line)
            self.assertIn("--cfg-scale 2.5", line)
            self.assertIn("-W 256 -H 256", line)

    def test_history_records_batch_and_reopens_it(self):
        self._generate("a knight")
        first_dir = self.win.prompt_batch_dir
        first_keeper = self.win.prompt_keeper
        from pixel_forge.imagegen import load_batch
        first_seed = next(
            c["seed"] for c in load_batch(first_dir)["candidates"]
            if c["file"] == first_keeper)
        # A second batch replaces the tab.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self._generate("a dragon")
        self.assertNotEqual(self.win.prompt_batch_dir, first_dir)
        # The first batch is in the list — reopen it.
        self.assertEqual(self.win.prompt_history_list.count(), 2)
        item = self.win.prompt_history_list.item(1)
        self.assertIn("a knight", item.text())
        self.win._on_prompt_history_clicked(item)
        self.assertEqual(self.win.prompt_batch_dir, first_dir)
        self.assertEqual(self.win.prompt_keeper, first_keeper)
        self.assertEqual(self.win.prompt_edit.text(), "a knight")
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertEqual(self.win.prompt_seed_label.text(),
                         "Seed: %d" % first_seed)

    def test_history_entry_with_missing_folder_warns(self):
        self._generate("a knight")
        first_dir = self.win.prompt_batch_dir
        shutil.rmtree(first_dir)
        self.win._refresh_prompt_history_list()
        item = self.win.prompt_history_list.item(0)
        self.assertIn("batch folder missing", item.text())
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod.QMessageBox,
                               "warning") as warn:
            self.win._on_prompt_history_clicked(item)
        self.assertTrue(warn.called)
        self.assertIn(str(first_dir), warn.call_args[0][2])
        # The tab is untouched — the current batch is still there.
        self.assertEqual(self.win.prompt_batch_dir, first_dir)
        self.assertEqual(self.win.prompt_strip.count(), 3)

    def test_corrupt_history_file_warns_and_starts_empty(self):
        from pixel_forge.history import HISTORY_FILENAME
        path = os.path.join(self.lib_dir, HISTORY_FILENAME)
        with open(path, "w") as handle:
            handle.write("{not json")
        from pixel_forge.gui import MainWindow
        win = MainWindow(library_dir=self.lib_dir)
        try:
            self.assertEqual(win.prompt_history_list.count(), 0)
            self.assertEqual(win.prompt_history_combo.count(), 0)
            self.assertIn("unreadable",
                          win.statusBar().currentMessage())
            # The corrupt file is left alone.
            with open(path) as handle:
                self.assertEqual(handle.read(), "{not json")
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_prompt_history_combo_reuses_a_prompt(self):
        self._generate("a knight")
        combo = self.win.prompt_history_combo
        self.assertEqual([combo.itemText(i)
                          for i in range(combo.count())],
                         ["a knight"])
        self.assertEqual(combo.currentIndex(), -1)
        self.win.prompt_edit.setText("")
        combo.setCurrentIndex(0)
        self.assertEqual(self.win.prompt_edit.text(), "a knight")
        self.assertEqual(combo.currentIndex(), -1)

    def test_prompt_history_persists_across_restart(self):
        self._generate("a knight")
        from pixel_forge.gui import MainWindow
        win = MainWindow(library_dir=self.lib_dir)
        try:
            combo = win.prompt_history_combo
            self.assertEqual([combo.itemText(i)
                              for i in range(combo.count())],
                             ["a knight"])
            self.assertEqual(win.prompt_history_list.count(), 1)
            self.assertIn("a knight",
                          win.prompt_history_list.item(0).text())
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_negative_presets_fill_the_field(self):
        from pixel_forge.imagegen import NEGATIVE_PRESETS
        combo = self.win.prompt_negative_preset_combo
        names = [combo.itemText(i) for i in range(combo.count())]
        self.assertEqual(names, list(NEGATIVE_PRESETS) + ["Custom"])
        self.assertEqual(combo.currentText(), "Default")
        combo.setCurrentIndex(names.index("No people"))
        self.assertEqual(self.win.prompt_negative_edit.text(),
                         NEGATIVE_PRESETS["No people"])
        # Typing by hand reads as Custom.
        self.win.prompt_negative_edit.setText("something else")
        self.assertEqual(combo.currentText(), "Custom")
        # Typing a preset's exact text selects it again.
        self.win.prompt_negative_edit.setText(
            NEGATIVE_PRESETS["Clean background"])
        self.assertEqual(combo.currentText(), "Clean background")

    def test_regenerate_one_replaces_only_the_selected(self):
        self._generate()
        from pixel_forge.imagegen import load_batch
        before = load_batch(self.win.prompt_batch_dir)
        seeds = {c["file"]: c["seed"]
                 for c in before["candidates"]}
        blobs = {c["file"]: (self.win.prompt_batch_dir
                             / c["file"]).read_bytes()
                 for c in before["candidates"]}
        keeper = self.win.prompt_keeper
        self.win.prompt_strip.setCurrentRow(1)
        self.win._on_prompt_regenerate_one()
        self._wait_for_worker()
        after = load_batch(self.win.prompt_batch_dir)
        after_seeds = {c["seed"] for c in after["candidates"]}
        self.assertEqual(len(after["candidates"]), 3)
        self.assertEqual(len(after_seeds), 3)
        self.assertNotEqual(after["candidates"][1]["seed"],
                            seeds["cand-02.png"])
        self.assertNotEqual(
            (self.win.prompt_batch_dir / "cand-02.png").read_bytes(),
            blobs["cand-02.png"])
        for name in ("cand-01.png", "cand-03.png"):
            kept = next(c for c in after["candidates"]
                        if c["file"] == name)
            self.assertEqual(kept["seed"], seeds[name])
            self.assertEqual(
                (self.win.prompt_batch_dir / name).read_bytes(),
                blobs[name])
        self.assertEqual(self.win.prompt_keeper, keeper)
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertIn("Regenerated cand-02.png",
                      self.win.statusBar().currentMessage())

    def test_regenerate_one_falls_back_to_the_keeper(self):
        # Nothing selected in the strip: the keeper is re-rolled.
        self._generate()
        from pixel_forge.imagegen import load_batch
        keeper = self.win.prompt_keeper
        old_seed = next(c["seed"]
                        for c in load_batch(
                            self.win.prompt_batch_dir)["candidates"]
                        if c["file"] == keeper)
        self.assertIsNone(self.win.prompt_strip.currentItem())
        self.win._on_prompt_regenerate_one()
        self._wait_for_worker()
        new_seed = next(c["seed"]
                        for c in load_batch(
                            self.win.prompt_batch_dir)["candidates"]
                        if c["file"] == keeper)
        self.assertNotEqual(new_seed, old_seed)
        self.assertEqual(self.win.prompt_keeper, keeper)
        self.assertEqual(self.win.prompt_seed_label.text(),
                         "Seed: %d" % new_seed)

    def test_more_like_this_uses_keeper_seeds(self):
        self._generate("a knight")
        from pixel_forge.imagegen import load_batch
        keeper_seed = next(
            c["seed"] for c in load_batch(
                self.win.prompt_batch_dir)["candidates"]
            if c["file"] == self.win.prompt_keeper)
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes) as ask:
            self.win._on_prompt_more_like()
        self.assertTrue(ask.called)
        self._wait_for_worker()
        batch = load_batch(self.win.prompt_batch_dir)
        self.assertEqual(batch["prompt"], "a knight")
        self.assertEqual([c["seed"] for c in batch["candidates"]],
                         [keeper_seed + 1, keeper_seed + 2,
                          keeper_seed + 3])
        self.assertEqual(self.win.prompt_edit.text(), "a knight")

    def test_more_like_this_decline_keeps_batch(self):
        self._generate("a knight")
        bdir = self.win.prompt_batch_dir
        keeper = self.win.prompt_keeper
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(
                gui_mod.QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No) as ask:
            self.win._on_prompt_more_like()
        self.assertTrue(ask.called)
        self.assertEqual(ask.call_args[0][4],
                         QMessageBox.StandardButton.No)
        self.assertEqual(self.win.prompt_batch_dir, bdir)
        self.assertEqual(self.win.prompt_keeper, keeper)
        self.assertEqual(self.win.prompt_strip.count(), 3)
        self.assertFalse(self.win._prompt_generating())

    def test_elapsed_time_shows_while_generating(self):
        # Each candidate takes 1 s with the stub; the progress
        # bar's text must carry the elapsed time mid-run — and it
        # must actually UPDATE past the 0:00 it started with
        # (a %-format bug once threw in the slot and left the
        # bar frozen at 0:00) — and the status bar reports the
        # total when the batch lands.
        with stub_env(self.stub, self.model, {"STUB_DELAY": "1"}):
            self.win.prompt_edit.setText("a knight")
            self.win._on_prompt_generate()
            deadline = time.monotonic() + 4.0
            seen = ""
            while time.monotonic() < deadline:
                self.app.processEvents()
                seen = self.win.prompt_progress.format()
                if "0:02" in seen:
                    break
                if not self.win._prompt_generating():
                    break
                time.sleep(0.1)
            self.assertIn("Generating", seen, seen)
            self.assertIn("0:02", seen, seen)
            self._wait_for_worker()
        self.assertIn("took",
                      self.win.statusBar().currentMessage())

    def test_iteration_buttons_enabled_state(self):
        self.assertFalse(self.win.prompt_regen_one_btn.isEnabled())
        self.assertFalse(self.win.prompt_more_like_btn.isEnabled())
        self._generate()
        self.assertTrue(self.win.prompt_regen_one_btn.isEnabled())
        self.assertTrue(self.win.prompt_more_like_btn.isEnabled())


class LibraryGuiTests(unittest.TestCase):
    """Library tab — every window gets its own temp library dir,
    so these never touch the real ~/pixel-forge-library/."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(4)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _add(self, name, tags=(), r=255, g=0, b=0):
        return self.win.library.add_image(
            make_solid_image(32, 32, r, g, b), name, tags=tags)

    def _select(self, row=0):
        item = self.win.library_list.item(row)
        self.win.library_list.setCurrentItem(item)
        self.win._on_library_item_clicked(item)
        return item

    def test_library_tab_exists_with_controls(self):
        tabs = [self.win.tabs.tabText(i)
                for i in range(self.win.tabs.count())]
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code",
                                "Library", "Tileset"])
        self.assertEqual(
            self.win.library_search_edit.placeholderText(),
            "Search name or tags")
        self.assertEqual(self.win.library_tag_combo.itemText(0),
                         "All tags")
        self.assertFalse(self.win.library_load_btn.isEnabled())
        self.assertFalse(self.win.library_rename_btn.isEnabled())
        self.assertFalse(self.win.library_delete_btn.isEnabled())
        self.assertFalse(self.win.library_export_btn.isEnabled())
        self.assertIn("Library is empty",
                      self.win.library_count_label.text())

    def test_empty_library_shows_no_sprites_yet(self):
        # v0.13 (review B1): an empty library shows a centered
        # "No sprites yet" label in the grid area, and the preview
        # pane shows its text instead of a blank void.
        self.win.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(self.win.library_empty_label.text(),
                         "No sprites yet")
        self.assertIs(self.win.library_stack.currentWidget(),
                      self.win.library_empty_label)
        self.assertTrue(self.win.library_empty_label.isVisible())
        self.assertFalse(self.win.library_list.isVisible())
        self.assertTrue(self.win.library_empty_label.alignment()
                        & Qt.AlignmentFlag.AlignHCenter)
        self.assertEqual(self.win.library_preview_label.text(),
                         "Select a sprite to preview it")
        pixmap = self.win.library_preview_label.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())

    def test_preview_text_shows_with_sprites_but_no_selection(self):
        # v0.13 (review B1): with sprites in the library but none
        # selected, the preview still shows its text — the null
        # pixmap used to wipe it.
        self._add("Knight")
        self.win._refresh_library()
        self.assertEqual(self.win.library_preview_label.text(),
                         "Select a sprite to preview it")
        pixmap = self.win.library_preview_label.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())
        self.assertIs(self.win.library_stack.currentWidget(),
                      self.win.library_list)

    def test_selecting_sprite_shows_preview_and_hides_empty_label(self):
        # v0.13 (review B1): selecting a sprite shows the real
        # preview and the empty label is gone.
        self._add("Knight")
        self.win._refresh_library()
        self.win.show()
        self.app.processEvents()
        self.app.processEvents()
        self._select(0)
        pixmap = self.win.library_preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertIs(self.win.library_stack.currentWidget(),
                      self.win.library_list)
        self.assertFalse(self.win.library_empty_label.isVisible())

    def test_deleting_last_sprite_brings_empty_label_back(self):
        # v0.13 (review B1): deleting the last sprite returns the
        # grid area to "No sprites yet" and the preview to text.
        from unittest import mock
        self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        self.assertIs(self.win.library_stack.currentWidget(),
                      self.win.library_list)
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.win._on_library_delete()
        self.assertEqual(self.win.library.entries(), [])
        self.assertIs(self.win.library_stack.currentWidget(),
                      self.win.library_empty_label)
        self.assertEqual(self.win.library_empty_label.text(),
                         "No sprites yet")
        self.assertEqual(self.win.library_preview_label.text(),
                         "Select a sprite to preview it")

    def test_no_bare_ampersand_on_library_tab(self):
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def test_malformed_entries_do_not_brick_startup(self):
        # v0.10: one malformed entry used to brick startup with
        # an uncaught KeyError (review W4). The window opens,
        # the good entries list, and the skip is warned about.
        from pixel_forge.gui import MainWindow
        lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        os.makedirs(lib_dir)
        good = {"id": "knight", "name": "Knight",
                "file": "knight.png", "kind": "sprite",
                "tags": ["hero"], "source": "image",
                "width": 32, "height": 32,
                "created": "2026-11-12T00:00:00+00:00"}
        no_name = dict(good, id="ghost", file="ghost.png")
        del no_name["name"]
        no_file = dict(good, id="wisp", name="Wisp")
        del no_file["file"]
        with open(os.path.join(lib_dir, "library.json"), "w") as f:
            json.dump({"format": "pixel-forge-library", "version": 1,
                       "entries": [good, no_name, no_file]}, f)
        win = MainWindow(library_dir=lib_dir)
        try:
            win.tabs.setCurrentIndex(4)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(win.library_list.count(), 1)
            self.assertIn("2 malformed entries skipped",
                          win.library_count_label.text())
            self.assertIn("skipped 2 malformed entries",
                          win.statusBar().currentMessage())
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_save_dialog_no_bare_ampersand_and_ok_needs_name(self):
        from pixel_forge.gui import SaveToLibraryDialog
        dialog = SaveToLibraryDialog(self.win, "Knight")
        for text in visible_texts(dialog):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)
        self.assertTrue(dialog.ok_btn.isEnabled())
        self.assertEqual(dialog.name_text(), "Knight")
        dialog.name_edit.setText("   ")
        self.assertFalse(dialog.ok_btn.isEnabled())
        dialog.name_edit.setText("Slime")
        dialog.tags_edit.setText("enemy, blue,")
        self.assertEqual(dialog.name_text(), "Slime")
        self.assertEqual(dialog.tags_list(), ["enemy", "blue"])
        dialog.close()

    def test_save_button_enabled_after_load(self):
        self.assertFalse(self.win.save_library_btn.isEnabled())
        self.assertTrue(self.win.load_image(self.src))
        self.assertTrue(self.win.save_library_btn.isEnabled())

    def test_save_image_result_to_library(self):
        self.assertTrue(self.win.load_image(self.src))
        entry = self.win.save_image_result_to_library(
            "Red Knight", ["hero", "red"])
        self.assertEqual(entry["name"], "Red Knight")
        self.assertEqual(entry["source"], "image")
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "red-knight.png")))
        self.assertEqual(self.win.library_list.count(), 1)
        self.assertEqual(self.win.library_list.item(0).text(),
                         "Red Knight")
        self.assertIn("1 sprite",
                      self.win.library_count_label.text())

    def test_save_prompt_keeper_to_library(self):
        self.win.prompt_result_image = make_solid_image(
            32, 32, 0, 255, 0)
        entry = self.win.save_prompt_keeper_to_library(
            "Slime", ["enemy"])
        self.assertEqual(entry["source"], "prompt")
        self.assertEqual(self.win.library_list.count(), 1)

    def test_search_filters_grid(self):
        self._add("Red Knight", ["hero"])
        self._add("Blue Slime", ["enemy"])
        self.win._refresh_library()
        self.assertEqual(self.win.library_list.count(), 2)
        self.win.library_search_edit.setText("knight")
        self.assertEqual(self.win.library_list.count(), 1)
        self.assertEqual(self.win.library_list.item(0).text(),
                         "Red Knight")
        self.assertIn("1 of 2",
                      self.win.library_count_label.text())
        self.win.library_search_edit.setText("")
        self.assertEqual(self.win.library_list.count(), 2)

    def test_tag_filter_filters_grid(self):
        self._add("Red Knight", ["hero"])
        self._add("Blue Slime", ["enemy"])
        self.win._refresh_library()
        tags = [self.win.library_tag_combo.itemText(i)
                for i in range(self.win.library_tag_combo.count())]
        self.assertEqual(tags, ["All tags", "enemy", "hero"])
        index = self.win.library_tag_combo.findData("enemy")
        self.win.library_tag_combo.setCurrentIndex(index)
        self.assertEqual(self.win.library_list.count(), 1)
        self.assertEqual(self.win.library_list.item(0).text(),
                         "Blue Slime")

    def test_click_entry_shows_preview_and_enables_buttons(self):
        self._add("Red Knight", ["hero", "red"])
        self.win._refresh_library()
        self._select(0)
        pixmap = self.win.library_preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        info = self.win.library_info_label.text()
        self.assertIn("Red Knight", info)
        self.assertIn("32 x 32", info)
        self.assertIn("hero, red", info)
        self.assertTrue(self.win.library_load_btn.isEnabled())
        self.assertTrue(self.win.library_rename_btn.isEnabled())
        self.assertTrue(self.win.library_delete_btn.isEnabled())
        self.assertTrue(self.win.library_export_btn.isEnabled())

    def test_missing_entry_shows_missing_in_grid_and_preview(self):
        # Task 68: a missing file keeps its existing markers —
        # "Missing" in the grid, "File missing" naming the file
        # in the preview — and the entry stays in the list.
        self._add("Knight")
        os.unlink(os.path.join(self.lib_dir, "knight.png"))
        self.win._refresh_library()
        self.assertEqual(self.win.library_list.count(), 1)
        item = self.win.library_list.item(0)
        self.assertEqual(item.text(), "Knight — Missing")
        self._select(0)
        self.assertEqual(self.win.library_preview_label.text(),
                         "File missing: knight.png")
        pixmap = self.win.library_preview_label.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())

    def test_corrupt_entry_shows_broken_in_grid_and_preview(self):
        # Task 68: a file that is there but cannot be read used
        # to show as a bare gray square, and the preview lied
        # "File missing" about it. The grid now marks it broken
        # with the reason in the tooltip, and the preview says
        # it cannot be read, with the reason — and the entry
        # stays in the list, not auto-deleted.
        self._add("Knight")
        with open(os.path.join(self.lib_dir, "knight.png"),
                  "wb") as handle:
            handle.write(b"not a png at all")
        self.win._refresh_library()
        self.assertEqual(self.win.library_list.count(), 1)
        item = self.win.library_list.item(0)
        self.assertEqual(item.text(), "Knight (broken)")
        self.assertIn("Can't read this image", item.toolTip())
        self.assertIn("damaged or not an image", item.toolTip())
        self._select(0)
        self.assertEqual(self.win.library_preview_label.text(),
                         "Can't read this image\n"
                         "the file is damaged or not an image")
        pixmap = self.win.library_preview_label.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())

    def test_readable_entry_shows_no_broken_marker(self):
        # Task 68: a readable entry is untouched — plain name
        # in the grid, no broken wording anywhere, real preview.
        self._add("Knight")
        self.win._refresh_library()
        item = self.win.library_list.item(0)
        self.assertEqual(item.text(), "Knight")
        self.assertNotIn("broken", item.toolTip())
        self._select(0)
        pixmap = self.win.library_preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertNotIn("Can't read",
                         self.win.library_preview_label.text())

    def test_repaired_entry_clears_broken_marker(self):
        # Task 68: the broken marker follows the file, not the
        # entry — replacing the corrupt file with a good one
        # clears it on the next refresh (the thumbnail cache
        # keys on size + mtime, so the rebuild re-checks).
        path = os.path.join(self.lib_dir, "knight.png")
        self._add("Knight")
        with open(path, "wb") as handle:
            handle.write(b"not a png at all")
        self.win._refresh_library()
        self.assertEqual(self.win.library_list.item(0).text(),
                         "Knight (broken)")
        write_image(make_solid_image(32, 32, 0, 255, 0), path)
        self.win._refresh_library()
        self.assertEqual(self.win.library_list.item(0).text(),
                         "Knight")
        self._select(0)
        pixmap = self.win.library_preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())

    def test_zero_size_entry_shows_broken_in_grid_and_preview(self):
        # Task 77: a 0-size image would ZeroDivisionError in the
        # preview (230 / max(0, 0)). load_image rejects 0-size
        # images from disk, so this is latent — the test stubs
        # the load to return one. It reads as a broken entry in
        # the grid and the preview, not a crash, and the entry
        # stays in the list.
        from unittest import mock

        import pixel_forge.gui as gui_mod
        from pixel_forge.image import Image
        self._add("Knight")
        self._add("Ghost")
        real_load = gui_mod.load_image
        ghost_path = os.path.join(self.lib_dir, "ghost.png")

        def stub_load(path):
            if os.fspath(path) == ghost_path:
                return Image(0, 0)
            return real_load(path)

        with mock.patch.object(gui_mod, "load_image",
                               side_effect=stub_load):
            # The good thumbs may already be cached from the
            # adds — clear the cache so the rebuild goes through
            # the stub.
            self.win._thumb_cache.clear()
            self.win._refresh_library()
            self.assertEqual(self.win.library_list.count(), 2)
            item = self.win.library_list.item(1)
            self.assertEqual(item.text(), "Ghost (broken)")
            self.assertIn("Can't read this image", item.toolTip())
            self.assertIn("no size", item.toolTip())
            self._select(1)
            self.assertEqual(
                self.win.library_preview_label.text(),
                "Can't read this image\nthe image has no size")
            pixmap = self.win.library_preview_label.pixmap()
            self.assertTrue(pixmap is None or pixmap.isNull())

    def test_zero_size_entry_leaves_normal_entries_previewing(self):
        # Task 77: the guard only fires for a 0-size image — a
        # normal entry next to it still previews exactly as
        # before, with no marker.
        from unittest import mock

        import pixel_forge.gui as gui_mod
        from pixel_forge.image import Image
        self._add("Knight")
        self._add("Ghost")
        real_load = gui_mod.load_image
        ghost_path = os.path.join(self.lib_dir, "ghost.png")

        def stub_load(path):
            if os.fspath(path) == ghost_path:
                return Image(0, 0)
            return real_load(path)

        with mock.patch.object(gui_mod, "load_image",
                               side_effect=stub_load):
            self.win._thumb_cache.clear()
            self.win._refresh_library()
            self.assertEqual(self.win.library_list.item(0).text(),
                             "Knight")
            self._select(0)
            pixmap = self.win.library_preview_label.pixmap()
            self.assertIsNotNone(pixmap)
            self.assertFalse(pixmap.isNull())
            self.assertEqual(
                self.win.library_preview_label.text(), "")

    def test_rename_entry(self):
        from unittest import mock
        self._add("Old Name")
        self.win._refresh_library()
        self._select(0)
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("New Name", True)):
            self.win._on_library_rename()
        self.assertEqual(self.win.library.entries()[0]["name"],
                         "New Name")
        self.assertEqual(self.win.library_list.item(0).text(),
                         "New Name")

    def test_rename_cancelled_keeps_name(self):
        from unittest import mock
        self._add("Old Name")
        self.win._refresh_library()
        self._select(0)
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("", False)):
            self.win._on_library_rename()
        self.assertEqual(self.win.library.entries()[0]["name"],
                         "Old Name")

    def test_delete_entry_confirmed(self):
        from unittest import mock
        entry = self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.win._on_library_delete()
        self.assertEqual(self.win.library.entries(), [])
        self.assertFalse(os.path.exists(
            os.path.join(self.lib_dir, entry["file"])))
        self.assertEqual(self.win.library_list.count(), 0)
        self.assertFalse(self.win.library_delete_btn.isEnabled())

    def test_delete_entry_declined_keeps_it(self):
        from unittest import mock
        self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.win._on_library_delete()
        self.assertEqual(len(self.win.library.entries()), 1)
        self.assertEqual(self.win.library_list.count(), 1)

    def test_load_into_image_tab(self):
        self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        self.win._on_library_load()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        self.assertIsNotNone(self.win.source_image)
        self.assertIsNotNone(self.win.result_image)
        self.assertEqual((self.win.result_image.width(),
                          self.win.result_image.height()), (32, 32))

    def test_export_copy_writes_file(self):
        from unittest import mock
        self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        dest = os.path.join(self.tmp, "knight-copy.png")
        with mock.patch.object(
                QFileDialog, "getSaveFileName",
                return_value=(dest, "")):
            self.win._on_library_export()
        self.assertTrue(os.path.isfile(dest))

    def _sheet(self, name="Walk cycle", meta_text=None):
        tmp = tempfile.mkdtemp()
        png = write_image(make_solid_image(64, 64, 255, 0, 0),
                          os.path.join(tmp, "walk.png"))
        meta = os.path.join(tmp, "walk.json")
        if meta_text is None:
            with open(meta, "w") as f:
                json.dump({"format": "pixel-forge-spritesheet",
                           "version": 1, "frames": []}, f)
        else:
            with open(meta, "w") as f:
                f.write(meta_text)
        return self.win.library.add_sheet(png, meta, name,
                                          frames=14)

    def _export(self, dest):
        from unittest import mock
        with mock.patch.object(
                QFileDialog, "getSaveFileName",
                return_value=(dest, "")):
            self.win._on_library_export()

    def test_export_sheet_rewrites_sheet_field(self):
        # Task 75: the exported JSON must name the EXPORTED
        # png, not the library png — and the library's own meta
        # must be untouched.
        entry = self._sheet("Walk cycle")
        self.win._refresh_library()
        self._select(0)
        dest = os.path.join(tempfile.mkdtemp(), "my-walk.png")
        self._export(dest)
        self.assertTrue(os.path.isfile(dest))
        with open(os.path.join(
                os.path.dirname(dest), "my-walk.json")) as f:
            exported = json.load(f)
        self.assertEqual(exported["sheet"], "my-walk.png")
        self.assertEqual(exported["frames"], [])
        with open(os.path.join(self.lib_dir,
                               entry["meta"])) as f:
            library_meta = json.load(f)
        self.assertEqual(library_meta["sheet"], "walk-cycle.png")

    def test_export_sheet_same_name_stays_consistent(self):
        # The user picks the same name as the library png: the
        # rewrite is a no-op and the pair is still consistent.
        self._sheet("Walk cycle")
        self.win._refresh_library()
        self._select(0)
        dest = os.path.join(tempfile.mkdtemp(), "walk-cycle.png")
        self._export(dest)
        with open(os.path.join(
                os.path.dirname(dest), "walk-cycle.json")) as f:
            exported = json.load(f)
        self.assertEqual(exported["sheet"], "walk-cycle.png")

    def test_export_sheet_non_json_meta_copied_verbatim(self):
        # A meta that is not a JSON object has no "sheet" field
        # to go stale — it is copied byte for byte, as before.
        entry = self._sheet("Walk cycle",
                            meta_text="not json at all\n")
        self.win._refresh_library()
        self._select(0)
        dest = os.path.join(tempfile.mkdtemp(), "my-walk.png")
        self._export(dest)
        exported = os.path.join(os.path.dirname(dest),
                                "my-walk.json")
        with open(exported, "rb") as f:
            exported_bytes = f.read()
        with open(os.path.join(self.lib_dir,
                               entry["meta"]), "rb") as f:
            library_bytes = f.read()
        self.assertEqual(exported_bytes, library_bytes)
        self.assertEqual(exported_bytes, b"not json at all\n")

    def test_export_sheet_missing_sheet_key_added(self):
        # A meta JSON with no "sheet" key (an older or
        # hand-edited meta): the export adds the key naming the
        # exported png, so the pair is self-consistent.
        entry = self._sheet("Walk cycle")
        with open(os.path.join(self.lib_dir,
                               entry["meta"]), "w") as f:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "frames": []}, f)
        self.win._refresh_library()
        self._select(0)
        dest = os.path.join(tempfile.mkdtemp(), "my-walk.png")
        self._export(dest)
        with open(os.path.join(
                os.path.dirname(dest), "my-walk.json")) as f:
            exported = json.load(f)
        self.assertEqual(exported["sheet"], "my-walk.png")
        # The library's own meta is untouched — still keyless.
        with open(os.path.join(self.lib_dir,
                               entry["meta"])) as f:
            library_meta = json.load(f)
        self.assertNotIn("sheet", library_meta)

    def test_persistence_across_windows(self):
        from pixel_forge.gui import MainWindow
        self.assertTrue(self.win.load_image(self.src))
        self.win.save_image_result_to_library("Knight", ["hero"])
        second = MainWindow(library_dir=self.lib_dir)
        try:
            second.tabs.setCurrentIndex(4)
            self.assertEqual(second.library_list.count(), 1)
            self.assertEqual(second.library_list.item(0).text(),
                             "Knight")
        finally:
            second.close()
            second.deleteLater()
            self.app.processEvents()

    def test_library_tab_screenshot(self):
        self._add("Red Knight", ["hero"])
        self.win._refresh_library()
        self._select(0)
        self.win.show()
        self.app.processEvents()
        shot = os.path.join(self.tmp, "pixel-forge-library.png")
        self.win.grab().save(shot)
        self.assertTrue(os.path.exists(shot))
        self.assertGreater(os.path.getsize(shot), 1000)


class PresetGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _pick(self, combo, key):
        index = combo.findData(key)
        self.assertGreaterEqual(index, 0, "no such preset: %s" % key)
        combo.setCurrentIndex(index)
        self.app.processEvents()

    def test_preset_pickers_present_on_all_tabs(self):
        for combo in (self.win.preset_combo,
                      self.win.video_preset_combo,
                      self.win.prompt_preset_combo):
            items = [combo.itemText(i)
                     for i in range(combo.count())]
            self.assertEqual(items, ["Custom", "Game Boy 32",
                                     "PICO-8 64", "Detailed 64",
                                     "Tiny 16", "Large 128",
                                     "Tall 32x64", "Wide 64x32",
                                     "Game Boy 64"])
            data = [combo.itemData(i)
                    for i in range(combo.count())]
            self.assertEqual(data, [None, "builtin:Game Boy 32",
                                    "builtin:PICO-8 64",
                                    "builtin:Detailed 64",
                                    "builtin:Tiny 16",
                                    "builtin:Large 128",
                                    "builtin:Tall 32x64",
                                    "builtin:Wide 64x32",
                                    "builtin:Game Boy 64"])
        self.assertFalse(self.win.preset_delete_btn.isEnabled())
        self.assertFalse(self.win.preset_export_btn.isEnabled())

    def test_apply_builtin_preset_moves_all_controls(self):
        # Move every control away from Game Boy 32 first.
        self.win.size_combo.setCurrentIndex(3)  # 64 x 64
        self.win.palette_combo.setCurrentIndex(1)  # PICO-8
        self.win.colors_spin.setValue(8)
        self.win.dither_check.setChecked(False)
        self.win.aspect_check.setChecked(True)
        self._pick(self.win.preset_combo, "builtin:Game Boy 32")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (32, 32))
        self.assertEqual(self.win.palette_combo.currentData(),
                         "gameboy")
        self.assertEqual(self.win.colors_spin.value(), 4)
        self.assertFalse(self.win.dither_check.isChecked())
        self.assertFalse(self.win.aspect_check.isChecked())
        self.assertEqual(self.win.preset_combo.currentText(),
                         "Game Boy 32")

    def test_apply_preset_repixelizes_loaded_image(self):
        src = write_image(make_gradient_image(),
                          os.path.join(self.tmp, "photo.png"))
        self.assertTrue(self.win.load_image(src))
        self._pick(self.win.preset_combo, "builtin:PICO-8 64")
        result = self.win.result_image
        self.assertEqual((result.width(), result.height()),
                         (64, 64))

    def test_apply_preset_on_video_and_prompt_tabs(self):
        self._pick(self.win.video_preset_combo, "builtin:PICO-8 64")
        self.assertEqual((self.win.video_width_spin.value(),
                          self.win.video_height_spin.value()),
                         (64, 64))
        self.assertEqual(self.win.video_palette_combo.currentData(),
                         "pico8")
        self._pick(self.win.prompt_preset_combo,
                   "builtin:Game Boy 32")
        self.assertEqual((self.win.prompt_width_spin.value(),
                          self.win.prompt_height_spin.value()),
                         (32, 32))
        self.assertEqual(
            self.win.prompt_palette_combo.currentData(), "gameboy")

    def test_manual_change_marks_custom(self):
        self._pick(self.win.preset_combo, "builtin:Game Boy 32")
        self.assertEqual(self.win.preset_combo.currentText(),
                         "Game Boy 32")
        self.win.colors_spin.setValue(8)
        self.assertEqual(self.win.preset_combo.currentText(),
                         "Custom")
        self.assertFalse(self.win.preset_delete_btn.isEnabled())

    def test_save_preset_via_dialog(self):
        from unittest import mock
        self.win.size_combo.setCurrentIndex(3)  # 64 x 64
        self.win.palette_combo.setCurrentIndex(1)  # PICO-8
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("My Look", True)):
            self.win._on_save_preset("image")
        stored = self.win.preset_store.get("My Look")
        self.assertEqual(stored["width"], 64)
        self.assertEqual(stored["palette"], "pico8")
        for combo in (self.win.preset_combo,
                      self.win.video_preset_combo,
                      self.win.prompt_preset_combo):
            self.assertIn("My Look",
                          [combo.itemText(i)
                           for i in range(combo.count())])
        self.assertEqual(self.win.preset_combo.currentText(),
                         "My Look")
        self.assertTrue(os.path.isfile(
            os.path.join(self.lib_dir, "presets.json")))

    def test_save_preset_cancelled(self):
        from unittest import mock
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("", False)):
            self.win._on_save_preset("image")
        self.assertEqual(self.win.preset_store.user_presets(), {})

    def test_delete_user_preset(self):
        from unittest import mock
        self.win.preset_store.save(
            "Temp", {"width": 32, "height": 32, "keep_aspect": False,
                     "palette": "image", "max_colors": 16,
                     "dither": True})
        self.win._refresh_preset_combos()
        self._pick(self.win.preset_combo, "user:Temp")
        self.assertTrue(self.win.preset_delete_btn.isEnabled())
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.win._on_delete_preset("image")
        self.assertEqual(self.win.preset_store.user_presets(), {})
        for combo in (self.win.preset_combo,
                      self.win.video_preset_combo,
                      self.win.prompt_preset_combo):
            self.assertNotIn("Temp",
                             [combo.itemText(i)
                              for i in range(combo.count())])

    def test_delete_declined_keeps_preset(self):
        from unittest import mock
        self.win.preset_store.save(
            "Temp", {"width": 32, "height": 32, "keep_aspect": False,
                     "palette": "image", "max_colors": 16,
                     "dither": True})
        self.win._refresh_preset_combos()
        self._pick(self.win.preset_combo, "user:Temp")
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.win._on_delete_preset("image")
        self.assertIsNotNone(self.win.preset_store.get("Temp"))

    def test_delete_button_disabled_for_builtin_and_custom(self):
        self._pick(self.win.preset_combo, "builtin:Game Boy 32")
        self.assertFalse(self.win.preset_delete_btn.isEnabled())
        self.win.preset_combo.setCurrentIndex(0)  # Custom
        self.assertFalse(self.win.preset_delete_btn.isEnabled())

    def test_user_preset_shadowing_builtin_shows_custom_label(self):
        self.win.preset_store.save(
            "Game Boy 32",
            {"width": 64, "height": 64, "keep_aspect": False,
             "palette": "gameboy", "max_colors": 4, "dither": False})
        self.win._refresh_preset_combos()
        items = [self.win.preset_combo.itemText(i)
                 for i in range(self.win.preset_combo.count())]
        self.assertIn("Game Boy 32", items)
        self.assertIn("Game Boy 32 (custom)", items)
        self._pick(self.win.preset_combo, "user:Game Boy 32")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (64, 64))
        self._pick(self.win.preset_combo, "builtin:Game Boy 32")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (32, 32))

    def test_no_bare_ampersand_with_user_preset(self):
        self.win.preset_store.save(
            "My Look", {"width": 32, "height": 32,
                        "keep_aspect": False, "palette": "image",
                        "max_colors": 16, "dither": True})
        self.win._refresh_preset_combos()
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def _window_with_presets(self, presets):
        """A fresh window whose library already holds `presets`
        (a name -> settings dict) in presets.json. The setUp
        window is closed first; tearDown closes this one."""
        from pixel_forge.gui import MainWindow
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        os.makedirs(lib_dir)
        with open(os.path.join(lib_dir, "presets.json"), "w") as f:
            json.dump({"format": "pixel-forge-presets", "version": 1,
                       "presets": presets}, f)
        self.win = MainWindow(library_dir=lib_dir)
        self.app.processEvents()
        return self.win

    def _warning_labels(self):
        return (self.win.preset_warning_label,
                self.win.video_preset_warning_label,
                self.win.prompt_preset_warning_label)

    def test_skipped_preset_warning_visible_on_all_tabs(self):
        # Task 61: the warning must be VISIBLE, not just the
        # status bar — one label under each picker, naming the
        # skipped preset, while the good presets still list.
        good = {"width": 32, "height": 32, "keep_aspect": False,
                "palette": "pico8", "max_colors": 8, "dither": True}
        self._window_with_presets(
            {"My Look": good,
             "Game Boy 32": dict(good, palette="gameboy"),
             "Broken": dict(good, width=0)})
        for label in self._warning_labels():
            self.assertFalse(label.isHidden())
            self.assertIn("Preset 'Broken' skipped", label.text())
            self.assertIn("size", label.text())
        for combo in (self.win.preset_combo,
                      self.win.video_preset_combo,
                      self.win.prompt_preset_combo):
            items = [combo.itemText(i)
                     for i in range(combo.count())]
            self.assertIn("My Look", items)
            self.assertIn("Game Boy 32 (custom)", items)
            self.assertNotIn("Broken", items)

    def test_all_bad_presets_show_warning_and_builtins_only(self):
        good = {"width": 32, "height": 32, "keep_aspect": False,
                "palette": "pico8", "max_colors": 8, "dither": True}
        self._window_with_presets(
            {"Broken": dict(good, width=0),
             # A blank palette is still invalid (v0.49: any
             # non-empty palette name is accepted, so a preset
             # may name a saved palette).
             "Also Broken": dict(good, palette="")})
        for label in self._warning_labels():
            self.assertFalse(label.isHidden())
            self.assertIn("Preset 'Broken' skipped", label.text())
            self.assertIn("Preset 'Also Broken' skipped",
                          label.text())
        for combo in (self.win.preset_combo,
                      self.win.video_preset_combo,
                      self.win.prompt_preset_combo):
            items = [combo.itemText(i)
                     for i in range(combo.count())]
            self.assertEqual(items, ["Custom", "Game Boy 32",
                                     "PICO-8 64", "Detailed 64",
                                     "Tiny 16", "Large 128",
                                     "Tall 32x64", "Wide 64x32",
                                     "Game Boy 64"])

    def test_no_warning_label_when_no_skips(self):
        for label in self._warning_labels():
            self.assertTrue(label.isHidden())
            self.assertEqual(label.text(), "")

    # --------------------------------------------------- task 23

    def test_new_builtins_apply(self):
        self._pick(self.win.preset_combo, "builtin:Tiny 16")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (16, 16))
        self.assertEqual(self.win.size_combo.currentData(), 16)
        self._pick(self.win.preset_combo, "builtin:Large 128")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (128, 128))
        self._pick(self.win.preset_combo, "builtin:Tall 32x64")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (32, 64))
        # Non-square: the size combo has no such preset, so it
        # reads Custom.
        self.assertIsNone(self.win.size_combo.currentData())
        self._pick(self.win.preset_combo, "builtin:Wide 64x32")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (64, 32))
        self._pick(self.win.preset_combo, "builtin:Game Boy 64")
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (64, 64))
        self.assertEqual(self.win.palette_combo.currentData(),
                         "gameboy")
        self.assertEqual(self.win.colors_spin.value(), 4)

    def _save_video_preset(self, name="Video Look", **overrides):
        settings = {"width": 32, "height": 32, "keep_aspect": False,
                    "palette": "pico8", "max_colors": 16,
                    "dither": True, "extract_fps": 4,
                    "preview_fps": 12, "sheet_fps": 8,
                    "sheet_cols": 4, "sheet_padding": 2}
        settings.update(overrides)
        self.win.preset_store.save(name, settings)
        self.win._refresh_preset_combos()
        return settings

    def _video_control_values(self):
        return (self.win.video_fps_combo.currentData(),
                self.win.video_play_fps_spin.value(),
                self.win.sheet_fps_spin.value(),
                self.win.sheet_cols_combo.currentData(),
                self.win.sheet_padding_spin.value())

    def test_video_preset_applies_video_fields(self):
        self._save_video_preset()
        self._pick(self.win.video_preset_combo, "user:Video Look")
        self.assertEqual(self._video_control_values(),
                         (4, 12, 8, 4, 2))
        # The shared fields apply too.
        self.assertEqual((self.win.video_width_spin.value(),
                          self.win.video_height_spin.value()),
                         (32, 32))
        self.assertEqual(self.win.video_palette_combo.currentData(),
                         "pico8")

    def test_preset_without_video_fields_leaves_them_alone(self):
        # Move every video control away from its default, then
        # pick a preset with no video fields: the shared fields
        # apply, the video controls stay exactly where they were.
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(24))
        self.win.video_play_fps_spin.setValue(2)
        self.win.sheet_fps_spin.setValue(30)
        self.win.sheet_cols_combo.setCurrentIndex(
            self.win.sheet_cols_combo.findData(8))
        self.win.sheet_padding_spin.setValue(5)
        before = self._video_control_values()
        self.assertEqual(before, (24, 2, 30, 8, 5))
        self._pick(self.win.video_preset_combo, "builtin:PICO-8 64")
        self.assertEqual(self._video_control_values(), before)
        self.assertEqual((self.win.video_width_spin.value(),
                          self.win.video_height_spin.value()),
                         (64, 64))

    def test_video_fields_ignored_on_image_and_prompt_tabs(self):
        self._save_video_preset()
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(24))
        before = self._video_control_values()
        self._pick(self.win.preset_combo, "user:Video Look")
        self.assertEqual(self._video_control_values(), before)
        self.assertEqual((self.win.width_spin.value(),
                          self.win.height_spin.value()), (32, 32))
        self._pick(self.win.prompt_preset_combo, "user:Video Look")
        self.assertEqual(self._video_control_values(), before)
        self.assertEqual((self.win.prompt_width_spin.value(),
                          self.win.prompt_height_spin.value()),
                         (32, 32))

    def test_video_preset_with_auto_columns(self):
        self._save_video_preset(sheet_cols=None)
        self.win.sheet_cols_combo.setCurrentIndex(
            self.win.sheet_cols_combo.findData(4))
        self._pick(self.win.video_preset_combo, "user:Video Look")
        self.assertIsNone(self.win.sheet_cols_combo.currentData())

    def test_applying_video_preset_keeps_picker_on_it(self):
        self._save_video_preset()
        self._pick(self.win.video_preset_combo, "user:Video Look")
        self.assertEqual(self.win.video_preset_combo.currentText(),
                         "Video Look")
        self.assertTrue(
            self.win.video_preset_export_btn.isEnabled())

    def test_hand_changing_video_control_marks_preset_custom(self):
        self._save_video_preset()
        self._pick(self.win.video_preset_combo, "user:Video Look")
        self.win.sheet_fps_spin.setValue(30)
        self.assertEqual(self.win.video_preset_combo.currentText(),
                         "Custom")
        self.assertFalse(
            self.win.video_preset_export_btn.isEnabled())
        # Same for the extract fps: re-pick, then change it.
        self._pick(self.win.video_preset_combo, "user:Video Look")
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(24))
        self.assertEqual(self.win.video_preset_combo.currentText(),
                         "Custom")

    def test_hand_changing_video_control_keeps_plain_preset(self):
        # A preset without video fields does not cover fps or
        # sheet settings, so changing them invalidates nothing —
        # the picker stays on the preset.
        self._pick(self.win.video_preset_combo, "builtin:PICO-8 64")
        self.win.sheet_fps_spin.setValue(30)
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(24))
        self.win.sheet_cols_combo.setCurrentIndex(
            self.win.sheet_cols_combo.findData(8))
        self.assertEqual(self.win.video_preset_combo.currentText(),
                         "PICO-8 64")

    def test_video_tab_save_captures_video_fields(self):
        from unittest import mock
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(12))
        self.win.video_play_fps_spin.setValue(2)
        self.win.sheet_fps_spin.setValue(24)
        self.win.sheet_cols_combo.setCurrentIndex(
            self.win.sheet_cols_combo.findData(6))
        self.win.sheet_padding_spin.setValue(3)
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("Video Look", True)):
            self.win._on_save_preset("video")
        stored = self.win.preset_store.get("Video Look")
        self.assertEqual(stored["extract_fps"], 12)
        self.assertEqual(stored["preview_fps"], 2)
        self.assertEqual(stored["sheet_fps"], 24)
        self.assertEqual(stored["sheet_cols"], 6)
        self.assertEqual(stored["sheet_padding"], 3)

    def test_image_tab_save_has_no_video_fields(self):
        from unittest import mock
        with mock.patch.object(QInputDialog, "getText",
                               return_value=("Still Look", True)):
            self.win._on_save_preset("image")
        stored = self.win.preset_store.get("Still Look")
        for field in ("extract_fps", "preview_fps", "sheet_fps",
                      "sheet_cols", "sheet_padding"):
            self.assertNotIn(field, stored)

    def test_export_import_buttons_present_on_all_tabs(self):
        for prefix in ("", "video_", "prompt_"):
            export_btn = getattr(self.win,
                                 prefix + "preset_export_btn")
            import_btn = getattr(self.win,
                                 prefix + "preset_import_btn")
            self.assertEqual(export_btn.text(), "Export preset")
            self.assertEqual(import_btn.text(), "Import preset")
            self.assertTrue(export_btn.toolTip().strip())
            self.assertTrue(import_btn.toolTip().strip())
            # Export disabled with no preset picked; Import
            # always available.
            self.assertFalse(export_btn.isEnabled())
            self.assertTrue(import_btn.isEnabled())

    def test_export_button_enabled_with_preset_picked(self):
        self._pick(self.win.preset_combo, "builtin:Game Boy 32")
        self.assertTrue(self.win.preset_export_btn.isEnabled())
        self.win.preset_combo.setCurrentIndex(0)  # Custom
        self.assertFalse(self.win.preset_export_btn.isEnabled())
        self._save_video_preset()
        self._pick(self.win.video_preset_combo, "user:Video Look")
        self.assertTrue(
            self.win.video_preset_export_btn.isEnabled())
        # A hand-changed control drops the picker to Custom and
        # disables Export again.
        self.win.video_colors_spin.setValue(4)
        self.assertFalse(
            self.win.video_preset_export_btn.isEnabled())

    def test_export_preset_writes_file(self):
        from unittest import mock
        path = os.path.join(tempfile.mkdtemp(), "shared.json")
        self._pick(self.win.preset_combo, "builtin:Tiny 16")
        with mock.patch.object(
                QFileDialog, "getSaveFileName",
                return_value=(path, "Pixel Forge preset (*.json)")):
            self.win._on_export_preset("image")
        with open(path) as handle:
            data = json.load(handle)
        self.assertEqual(data["format"], "pixel-forge-preset")
        self.assertEqual(data["name"], "Tiny 16")
        self.assertEqual(data["settings"]["width"], 16)
        self.assertIn("Exported preset Tiny 16",
                      self.win.statusBar().currentMessage())

    def test_export_preset_cancelled_writes_nothing(self):
        from unittest import mock
        path = os.path.join(tempfile.mkdtemp(), "shared.json")
        self._pick(self.win.preset_combo, "builtin:Tiny 16")
        with mock.patch.object(
                QFileDialog, "getSaveFileName",
                return_value=("", "")):
            self.win._on_export_preset("image")
        self.assertFalse(os.path.exists(path))

    def test_import_preset_round_trip(self):
        from unittest import mock
        from pixel_forge.presets import write_preset_export
        path = os.path.join(tempfile.mkdtemp(), "shared.json")
        write_preset_export(path, "Shared Look",
                            {"width": 48, "height": 48,
                             "keep_aspect": True, "palette": "pico8",
                             "max_colors": 8, "dither": False,
                             "extract_fps": 4})
        with mock.patch.object(
                QFileDialog, "getOpenFileName",
                return_value=(path, "Pixel Forge preset (*.json)")):
            self.win._on_import_preset("image")
        stored = self.win.preset_store.get("Shared Look")
        self.assertEqual(stored["width"], 48)
        self.assertEqual(stored["extract_fps"], 4)
        self.assertEqual(self.win.preset_combo.currentText(),
                         "Shared Look")
        self.assertIn("Imported preset Shared Look",
                      self.win.statusBar().currentMessage())

    def test_import_bad_file_refuses_and_touches_nothing(self):
        from unittest import mock
        import pixel_forge.gui as gui_mod
        path = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(path, "w") as handle:
            handle.write("{not json")
        with mock.patch.object(
                QFileDialog, "getOpenFileName",
                return_value=(path, "Pixel Forge preset (*.json)")), \
                mock.patch.object(gui_mod, "warn_dialog") as warned:
            self.win._on_import_preset("image")
        warned.assert_called_once()
        self.assertEqual(warned.call_args[0][1],
                         "Could not import preset")
        self.assertEqual(self.win.preset_store.user_presets(), {})

    def test_import_shadowing_builtin_needs_no_question(self):
        from unittest import mock
        from pixel_forge.presets import write_preset_export
        path = os.path.join(tempfile.mkdtemp(), "shared.json")
        write_preset_export(path, "Game Boy 32",
                            {"width": 64, "height": 64,
                             "keep_aspect": False,
                             "palette": "gameboy", "max_colors": 4,
                             "dither": False})
        with mock.patch.object(
                QFileDialog, "getOpenFileName",
                return_value=(path, "Pixel Forge preset (*.json)")), \
                mock.patch.object(
                    QMessageBox, "question",
                    side_effect=AssertionError(
                        "no question for a built-in name")):
            self.win._on_import_preset("image")
        items = [self.win.preset_combo.itemText(i)
                 for i in range(self.win.preset_combo.count())]
        self.assertIn("Game Boy 32 (custom)", items)
        self.assertEqual(self.win.preset_store.get("Game Boy 32")
                         ["width"], 64)
        self.assertEqual(
            self.win.preset_store.get("Game Boy 32",
                                      builtin=True)["width"], 32)

    def _import_file_named(self, name, width, answer):
        """Import a preset file over an existing user preset,
        answering the replace question with `answer`."""
        from unittest import mock
        from pixel_forge.presets import write_preset_export
        path = os.path.join(tempfile.mkdtemp(), "shared.json")
        write_preset_export(path, name,
                            {"width": width, "height": 32,
                             "keep_aspect": False, "palette": "pico8",
                             "max_colors": 16, "dither": True})
        with mock.patch.object(
                QFileDialog, "getOpenFileName",
                return_value=(path, "Pixel Forge preset (*.json)")), \
                mock.patch.object(
                    QMessageBox, "question",
                    return_value=answer) as asked:
            self.win._on_import_preset("image")
        return asked

    def test_import_clash_declined_keeps_old_preset(self):
        self.win.preset_store.save(
            "Mine", {"width": 32, "height": 32, "keep_aspect": False,
                     "palette": "pico8", "max_colors": 16,
                     "dither": True})
        asked = self._import_file_named(
            "Mine", 16, QMessageBox.StandardButton.No)
        asked.assert_called_once()
        self.assertEqual(self.win.preset_store.get("Mine")["width"],
                         32)

    def test_import_clash_accepted_replaces_preset(self):
        self.win.preset_store.save(
            "Mine", {"width": 32, "height": 32, "keep_aspect": False,
                     "palette": "pico8", "max_colors": 16,
                     "dither": True})
        asked = self._import_file_named(
            "Mine", 16, QMessageBox.StandardButton.Yes)
        asked.assert_called_once()
        self.assertEqual(self.win.preset_store.get("Mine")["width"],
                         16)


class SheetLibraryGuiTests(unittest.TestCase):
    """Saving a spritesheet to the library needs a real video."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(1)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        """Pump the event loop until the Video tab settles — no
        worker running and the debounce timer idle. The video
        path is async since v0.9, so every load and control
        change needs this before its results are asserted."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def test_save_sheet_button_follows_kept_frames(self):
        self.assertFalse(self.win.sheet_library_btn.isEnabled())
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertTrue(self.win.sheet_library_btn.isEnabled())
        self.win._on_drop_all()
        self.assertFalse(self.win.sheet_library_btn.isEnabled())
        self.win._on_keep_all()
        self.assertTrue(self.win.sheet_library_btn.isEnabled())

    def test_save_sheet_to_library(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        entry = self.win.save_sheet_to_library("Walk cycle",
                                               ["walk"])
        self.assertEqual(entry["kind"], "sheet")
        self.assertEqual(entry["frames"], 16)
        self.assertEqual(entry["source"], "video")
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "walk-cycle.png")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "walk-cycle.json")))
        self.win.tabs.setCurrentIndex(4)
        self.assertEqual(self.win.library_list.count(), 1)
        item = self.win.library_list.item(0)
        self.win.library_list.setCurrentItem(item)
        self.win._on_library_item_clicked(item)
        self.assertIn("spritesheet, 16 frames",
                      self.win.library_info_label.text())

    def test_saved_sheet_meta_names_existing_library_file(self):
        """Task 62: the GUI saves the sheet from a temp file
        always called sheet.png — the library copy's meta must
        name the PNG as it sits in the library, not sheet.png."""
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        entry = self.win.save_sheet_to_library("Walk cycle",
                                               ["walk"])
        with open(os.path.join(self.lib_dir,
                               entry["meta"])) as f:
            lib_meta = json.load(f)
        self.assertEqual(lib_meta["sheet"], entry["file"])
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, lib_meta["sheet"])))


class CodeTabTests(unittest.TestCase):
    """Code tab — code parsing, skins, textures, export, library."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n"
                         "DRAW = [{'x': 0, 'y': 0, 'w': 4, 'h': 4,"
                         " 'color': '#ff0000'}]\n")
        cls.empty_py = os.path.join(cls.tmp, "empty.py")
        with open(cls.empty_py, "w") as handle:
            handle.write("# no sprites here\n")
        cls.mixed = os.path.join(cls.tmp, "mixed")
        os.mkdir(cls.mixed)
        with open(os.path.join(cls.mixed, "a.py"), "w") as handle:
            handle.write("HERO = [[0, 1], [1, 0]]\n")
        with open(os.path.join(cls.mixed, "b.js"), "w") as handle:
            handle.write("const GEM = [[1, 2], [2, 1]];\n")
        with open(os.path.join(cls.mixed, "broken.py"), "w") as handle:
            handle.write("def broken(:\n")
        skin = make_solid_image(64, 64, 10, 20, 30)
        cls.skin = write_image(skin, os.path.join(cls.tmp,
                                                  "steve.png"))
        cls.texture = write_image(
            make_solid_image(16, 16, 1, 2, 3),
            os.path.join(cls.tmp, "grass.png"))
        cls.big_texture = write_image(
            make_noisy_image(1024, 1024),
            os.path.join(cls.tmp, "photo.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(3)

    def _wait_for_texture(self, timeout=30):
        """Pump the event loop until the texture worker lands (or
        the timeout hits). Returns True when the Code tab holds a
        texture — the texture path is async since v0.8."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.code_kind == "texture"
                    and self.win.texture_worker is None):
                return True
            time.sleep(0.05)
        return False

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def test_code_tab_exists_with_controls(self):
        tabs = [self.win.tabs.tabText(i)
                for i in range(self.win.tabs.count())]
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code",
                                "Library", "Tileset"])
        self.assertEqual(self.win.code_import_btn.text(),
                         "Import code")
        self.assertEqual(self.win.code_import_asset_btn.text(),
                         "Import game asset")
        self.assertFalse(self.win.code_export_btn.isEnabled())
        self.assertFalse(self.win.code_export_parts_btn.isEnabled())
        self.assertFalse(self.win.code_save_library_btn.isEnabled())
        self.assertEqual(self.win.code_zoom_slider.value(), 8)

    def test_no_bare_ampersand_on_code_tab(self):
        self.assertTrue(self.win.load_code(self.game))
        self.assertTrue(self.win.load_game_asset(self.skin))
        self.assertTrue(self.win.load_game_asset(self.texture))
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def test_load_code_lists_assets_and_previews(self):
        self.assertTrue(self.win.load_code(self.game))
        self.assertEqual(self.win.code_list.count(), 2)
        texts = [self.win.code_list.item(i).text()
                 for i in range(self.win.code_list.count())]
        self.assertTrue(any(t.startswith("HERO — map, 2 x 2")
                            for t in texts))
        self.assertTrue(any(t.startswith("PALETTE — palette")
                            for t in texts))
        self.assertIn("DRAW", self.win.code_skipped_label.text())
        self.assertIn("draw-call",
                      self.win.code_skipped_label.text())
        pixmap = self.win.code_preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertTrue(self.win.code_export_btn.isEnabled())
        self.assertTrue(self.win.code_save_library_btn.isEnabled())
        self.assertFalse(self.win.code_export_parts_btn.isEnabled())
        # First asset in file order (PALETTE) is selected.
        self.assertIn("PALETTE", self.win.code_info_label.text())

    def test_selecting_an_asset_changes_preview(self):
        self.assertTrue(self.win.load_code(self.game))
        item = self.win.code_list.item(1)
        self.win.code_list.setCurrentItem(item)
        self.win._on_code_item_clicked(item)
        self.assertIn("HERO", self.win.code_info_label.text())
        self.assertEqual((self.win.code_image.width(),
                          self.win.code_image.height()), (2, 2))

    def test_load_code_no_assets_fails_cleanly(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod.QMessageBox, "warning") \
                as warn:
            self.assertFalse(self.win.load_code(self.empty_py))
        self.assertEqual(self.win.code_list.count(), 0)
        warn.assert_called_once()

    def test_load_code_folder_skips_bad_file(self):
        # Task 65: a mixed folder lists the good assets with
        # thumbnails and reports the bad file in the skipped
        # box — no dialog, no abort.
        self.assertTrue(self.win.load_code(self.mixed))
        self.assertEqual(self.win.code_list.count(), 2)
        texts = [self.win.code_list.item(i).text()
                 for i in range(self.win.code_list.count())]
        self.assertTrue(any(t.startswith("HERO — map, 2 x 2")
                            for t in texts))
        self.assertTrue(any(t.startswith("GEM — map, 2 x 2")
                            for t in texts))
        label = self.win.code_skipped_label.text()
        self.assertIn("broken.py", label)
        self.assertIn("could not parse", label)
        pixmap = self.win.code_preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertIn("1 file skipped",
                      self.win.statusBar().currentMessage())

    def test_load_code_clears_stale_skipped_lines(self):
        # A later clean import must not leave the old skipped
        # file in the reasons box.
        self.assertTrue(self.win.load_code(self.mixed))
        self.assertIn("broken.py",
                      self.win.code_skipped_label.text())
        self.assertTrue(self.win.load_code(self.game))
        label = self.win.code_skipped_label.text()
        self.assertNotIn("broken.py", label)
        self.assertIn("DRAW", label)

    def test_load_skin_lists_parts_and_figure(self):
        self.assertTrue(self.win.load_game_asset(self.skin))
        # 12 parts + the combined figure.
        self.assertEqual(self.win.code_list.count(), 13)
        texts = [self.win.code_list.item(i).text()
                 for i in range(self.win.code_list.count())]
        self.assertTrue(any(t.startswith("head — part, 8 x 8")
                            for t in texts))
        self.assertTrue(any(t.startswith("figure — figure, 16 x 32")
                            for t in texts))
        self.assertTrue(self.win.code_export_parts_btn.isEnabled())
        self.assertIn("Minecraft skin (64x64)",
                      self.win.statusBar().currentMessage())

    def test_load_texture_falls_back_with_note(self):
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        self.assertEqual(self.win.code_list.count(), 1)
        self.assertTrue(self.win.code_list.item(0).text()
                        .startswith("grass — texture, 16 x 16"))
        self.assertIn("not a Minecraft skin",
                      self.win.code_info_label.text())
        self.assertFalse(self.win.code_export_parts_btn.isEnabled())

    def test_load_large_texture_downscales_and_notes_it(self):
        # v0.8 regression (review W2): a 1024x1024 photo used to
        # freeze the window ~22 s at native size. Now it caps at
        # 512 and the info line says so — never silently resized.
        self.assertTrue(self.win.load_game_asset(self.big_texture))
        self.assertTrue(self._wait_for_texture())
        self.assertEqual(self.win.code_list.count(), 1)
        self.assertTrue(self.win.code_list.item(0).text()
                        .startswith("photo — texture, 512 x 512"))
        self.assertEqual((self.win.code_image.width(),
                          self.win.code_image.height()), (512, 512))
        self.assertIn("downscaled from 1024 x 1024 to 512 x 512",
                      self.win.code_info_label.text())

    def test_texture_load_keeps_window_responsive(self):
        # v0.8: the texture loads on a worker thread, so the event
        # loop must keep running while it works — load_game_asset
        # returns at once with a "Loading texture…" indicator
        # (task 90: the indicator, not the status message, is
        # the "what is running" display now).
        from PySide6.QtCore import QTimer
        fired = []
        self.assertTrue(self.win.load_game_asset(self.big_texture))
        self.assertIn("Loading texture",
                      self.win._activity_text.text())
        QTimer.singleShot(0, lambda: fired.append(True))
        self.app.processEvents()
        self.assertTrue(fired, "event loop did not run during load")
        self.assertTrue(self._wait_for_texture())
        self.assertEqual(self.win.code_kind, "texture")

    def test_export_code_asset(self):
        self.assertTrue(self.win.load_code(self.game))
        item = self.win.code_list.item(1)  # HERO, 2 x 2
        self.win.code_list.setCurrentItem(item)
        self.win._on_code_item_clicked(item)
        out_path = os.path.join(self.tmp, "hero-out.png")
        self.assertTrue(self.win.export_code_asset(out_path))
        self.assertTrue(os.path.isfile(out_path))
        from PySide6.QtGui import QImage
        image = QImage(out_path)
        self.assertEqual((image.width(), image.height()), (2, 2))

    def test_save_code_asset_to_library(self):
        self.assertTrue(self.win.load_code(self.game))
        entry = self.win.save_code_asset_to_library(
            "Hero from code", ["hero"])
        self.assertEqual(entry["name"], "Hero from code")
        self.assertEqual(entry["source"], "code")
        self.assertEqual(entry["kind"], "sprite")
        self.assertTrue(os.path.isfile(
            os.path.join(self.lib_dir, "hero-from-code.png")))
        self.assertEqual(len(self.win.library.entries()), 1)

    def test_save_skin_part_to_library(self):
        self.assertTrue(self.win.load_game_asset(self.skin))
        entry = self.win.save_code_asset_to_library("Skin head", [])
        self.assertEqual(entry["source"], "skin")

    def test_save_texture_to_library(self):
        # Task 79: a plain texture saved from the Code tab is
        # recorded as source "texture", not "skin".
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        entry = self.win.save_code_asset_to_library(
            "Grass texture", [])
        self.assertEqual(entry["source"], "texture")
        self.assertEqual(entry["kind"], "sprite")
        self.assertTrue(os.path.isfile(
            os.path.join(self.lib_dir, "grass-texture.png")))

    def test_texture_filter_shows_only_textures(self):
        # Task 79: the Library tab's From filter offers
        # Texture, and filtering by it shows only the texture
        # — the skin stays under Skin. Nothing is pre-filtered:
        # the combo starts on All sources.
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        self.win.save_code_asset_to_library("Grass texture", [])
        self.assertTrue(self.win.load_game_asset(self.skin))
        self.win.save_code_asset_to_library("Skin head", [])
        combo = self.win.library_source_combo
        self.assertIsNone(combo.currentData())
        index = combo.findData("texture")
        self.assertGreaterEqual(index, 0)
        self.assertEqual(combo.itemText(index), "Texture")
        self.win.tabs.setCurrentIndex(4)
        self.app.processEvents()
        texts = [self.win.library_list.item(row).text()
                 for row in range(self.win.library_list.count())]
        self.assertEqual(sorted(texts),
                         ["Grass texture", "Skin head"])
        combo.setCurrentIndex(index)
        self.app.processEvents()
        texts = [self.win.library_list.item(row).text()
                 for row in range(self.win.library_list.count())]
        self.assertEqual(texts, ["Grass texture"])
        combo.setCurrentIndex(combo.findData("skin"))
        self.app.processEvents()
        texts = [self.win.library_list.item(row).text()
                 for row in range(self.win.library_list.count())]
        self.assertEqual(texts, ["Skin head"])
        combo.setCurrentIndex(combo.findData(None))
        self.app.processEvents()
        texts = [self.win.library_list.item(row).text()
                 for row in range(self.win.library_list.count())]
        self.assertEqual(sorted(texts),
                         ["Grass texture", "Skin head"])

    def test_code_labels_fit_their_text(self):
        # Regression: the info label wrapped but the layout gave
        # it too little height, so the text overflowed into the
        # controls below. (The skipped-reasons label lives in a
        # scroll box, so its full text is reachable by scrolling
        # instead of sizing the column.)
        self.win.show()
        self.app.processEvents()
        self.assertTrue(self.win.load_code(self.game))
        item = self.win.code_list.item(1)  # HERO
        self.win.code_list.setCurrentItem(item)
        self.win._on_code_item_clicked(item)
        self.app.processEvents()
        label = self.win.code_info_label
        self.assertGreaterEqual(
            label.height(),
            label.heightForWidth(label.width()),
            "info label text clipped: %r" % label.text()[:40])
        # The controls below the reasons list must still have
        # room: the zoom slider and the info label are visible,
        # not squeezed to nothing by a long reasons list.
        self.assertGreater(self.win.code_zoom_slider.height(), 10)
        self.assertGreater(label.height(), 10)


class CodeInputExpansionTests(unittest.TestCase):
    """Task 46 — JSON, C#, GDScript, and Lua input on the Code
    tab, plus image references that open the image."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.sheet = write_image(
            make_solid_image(64, 32, 255, 0, 0),
            os.path.join(cls.tmp, "sheet.png"))
        # An Aseprite-style JSON export plus its sheet.
        doc = {
            "frames": {
                "hero 0.aseprite": {
                    "frame": {"x": 0, "y": 0, "w": 32, "h": 32}},
                "hero 1.aseprite": {
                    "frame": {"x": 32, "y": 0, "w": 32, "h": 32}},
            },
            "meta": {"image": "sheet.png"},
        }
        cls.aseprite = os.path.join(cls.tmp, "hero.json")
        with open(cls.aseprite, "w") as handle:
            json.dump(doc, handle)
        cls.csharp = os.path.join(cls.tmp, "Hero.cs")
        with open(cls.csharp, "w") as handle:
            handle.write(
                "using UnityEngine;\n"
                "public class Hero : MonoBehaviour {\n"
                "    public Color tint = new Color(1f, 0f, 0f, 1f);\n"
                "    public Rect frame = new Rect(0, 0, 32, 32);\n"
                "    public Color[] palette = new Color[] "
                "{ Color.red, Color.green };\n"
                "}\n")
        cls.gdscript = os.path.join(cls.tmp, "hero.gd")
        with open(cls.gdscript, "w") as handle:
            handle.write(
                "extends Node2D\n"
                "var tint = Color(1, 0, 0)\n"
                "var palette = [Color.RED, Color.BLUE]\n")
        cls.lua = os.path.join(cls.tmp, "main.lua")
        with open(cls.lua, "w") as handle:
            handle.write(
                "local tint = {1, 0, 0}\n"
                "local palette = {{1.0, 0, 0}, {0, 1.0, 0}}\n")
        # A C# file whose loader call names the sheet (no
        # extension — Unity Resources.Load style).
        cls.loader = os.path.join(cls.tmp, "Loader.cs")
        with open(cls.loader, "w") as handle:
            handle.write(
                'Sprite s = Resources.Load<Sprite>("sheet");\n')

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(3)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _texts(self):
        return [self.win.code_list.item(i).text()
                for i in range(self.win.code_list.count())]

    def test_json_input_lands_in_the_list(self):
        self.assertTrue(self.win.load_code(self.aseprite))
        texts = self._texts()
        self.assertTrue(any(t.startswith("hero 0 — frame, 32 x 32")
                            for t in texts))
        self.assertTrue(any(t.startswith("hero 1 — frame, 32 x 32")
                            for t in texts))
        # Selecting a frame previews the crop.
        item = self.win.code_list.item(0)
        self.win.code_list.setCurrentItem(item)
        self.win._on_code_item_clicked(item)
        self.assertIn("hero 0", self.win.code_info_label.text())
        self.assertEqual((self.win.code_image.width(),
                          self.win.code_image.height()), (32, 32))

    def test_csharp_items_show(self):
        self.assertTrue(self.win.load_code(self.csharp))
        texts = self._texts()
        self.assertTrue(any(t.startswith("tint — color")
                            for t in texts))
        self.assertTrue(any(t.startswith("palette — palette")
                            for t in texts))
        # The Rect is listed under "Found but not rendered".
        self.assertIn("frame", self.win.code_skipped_label.text())
        self.assertIn("rectangle",
                      self.win.code_skipped_label.text())

    def test_gdscript_items_show(self):
        self.assertTrue(self.win.load_code(self.gdscript))
        texts = self._texts()
        self.assertTrue(any(t.startswith("tint — color")
                            for t in texts))
        self.assertTrue(any(t.startswith("palette — palette")
                            for t in texts))

    def test_lua_items_show(self):
        self.assertTrue(self.win.load_code(self.lua))
        texts = self._texts()
        self.assertTrue(any(t.startswith("tint — color")
                            for t in texts))
        self.assertTrue(any(t.startswith("palette — palette")
                            for t in texts))

    def test_image_reference_opens_the_image(self):
        self.assertTrue(self.win.load_code(self.loader))
        texts = self._texts()
        self.assertTrue(any(t.startswith("s — image, 64 x 32")
                            for t in texts))
        item = self.win.code_list.item(0)
        self.win.code_list.setCurrentItem(item)
        self.win._on_code_item_clicked(item)
        self.assertEqual((self.win.code_image.width(),
                          self.win.code_image.height()), (64, 32))
        # The preview is the referenced sheet itself (solid red).
        px = self.win.code_image.pixel(0, 0)
        self.assertEqual(((px >> 16) & 0xFF, (px >> 8) & 0xFF,
                          px & 0xFF, (px >> 24) & 0xFF),
                         (255, 0, 0, 255))

    def test_code_detects_text_covers_new_languages(self):
        text = self.win._code_detects_text()
        for phrase in (".json", ".cs", ".gd", ".lua", "Aseprite",
                       "Image references", "Rectangles",
                       "Single colors", "5 MB"):
            self.assertIn(phrase, text, "missing from Code detects "
                          "text: %r" % phrase)


class CodeTextureControlsTests(unittest.TestCase):
    """Task 26 — the Code tab's texture controls and the skin
    override. Visibility is checked with isHidden (the flag
    setVisible controls): isVisible is False for every widget
    while the window was never shown."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write("HERO = [[0, 1], [1, 0]]\n")
        # A 64x64 non-skin: a solid color, so it is unmistakably
        # not a Minecraft skin — but skin-sized, so detection by
        # size alone reads it as one.
        cls.nonskin = write_image(
            make_solid_image(64, 64, 200, 30, 30),
            os.path.join(cls.tmp, "nonskin.png"))
        cls.texture = write_image(
            make_solid_image(16, 16, 1, 2, 3),
            os.path.join(cls.tmp, "grass.png"))
        # 48x48, deliberately NOT skin-sized: a 64x64 gradient
        # would be detected as a skin and never reach the
        # texture path at all.
        cls.gradient = write_image(
            make_gradient_image(48, 48),
            os.path.join(cls.tmp, "gradient.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(3)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_texture(self, timeout=30):
        """Pump until the texture worker lands (or the timeout
        hits). Returns True when the Code tab holds a texture."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.code_kind == "texture"
                    and self.win.texture_worker is None):
                return True
            time.sleep(0.05)
        return False

    def _wait_for_code_size(self, width, height, timeout=30):
        """Pump until the selected asset renders at the given
        size — a control change is debounced 300 ms and the
        re-render runs on a worker, so the size lands later."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            image = self.win.code_image
            if (image is not None
                    and image.width() == width
                    and image.height() == height
                    and self.win.texture_worker is None):
                return True
            time.sleep(0.05)
        return False

    def _wait_for_asset_count(self, count, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.code_list.count() == count
                    and self.win.texture_worker is None):
                return True
            time.sleep(0.05)
        return False

    def test_texture_controls_visible_with_native_defaults(self):
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        self.assertFalse(self.win.code_texture_section.isHidden())
        self.assertEqual(self.win.code_texture_width_spin.value(),
                         16)
        self.assertEqual(self.win.code_texture_height_spin.value(),
                         16)
        self.assertEqual(
            self.win.code_texture_palette_combo.currentData(),
            "image")
        self.assertEqual(self.win.code_texture_colors_spin.value(),
                         256)
        self.assertFalse(
            self.win.code_texture_dither_check.isChecked())
        # A plain texture is not skin-sized: no override button.
        self.assertTrue(self.win.code_treat_texture_btn.isHidden())

    def test_texture_size_change_rerenders(self):
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        self.win.code_texture_width_spin.setValue(8)
        self.win.code_texture_height_spin.setValue(8)
        self.assertTrue(self._wait_for_code_size(8, 8))
        self.assertIn("8 x 8", self.win.code_info_label.text())
        # Export writes the current rendering, not the import.
        out_path = os.path.join(self.tmp, "grass-8.png")
        self.assertTrue(self.win.export_code_asset(out_path))
        from PySide6.QtGui import QImage
        image = QImage(out_path)
        self.assertEqual((image.width(), image.height()), (8, 8))

    def test_texture_palette_and_colors_rerender(self):
        from pixel_forge.pixelize import count_opaque_colors
        self.assertTrue(self.win.load_game_asset(self.gradient))
        self.assertTrue(self._wait_for_texture())
        self.assertGreater(count_opaque_colors(self.win.code_image),
                           4)
        self.win.code_texture_palette_combo.setCurrentIndex(
            self.win.code_texture_palette_combo.findData("pico8"))
        self.win.code_texture_colors_spin.setValue(4)
        deadline = time.monotonic() + 30
        landed = False
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.texture_worker is None
                    and count_opaque_colors(self.win.code_image)
                    <= 4):
                landed = True
                break
            time.sleep(0.05)
        self.assertTrue(landed, "palette re-render never landed")
        self.assertIn("PICO-8", self.win.code_info_label.text())
        self.assertIn("4 colors", self.win.code_info_label.text())

    def test_texture_dither_changes_output(self):
        from pixel_forge.pixelize import count_opaque_colors
        self.assertTrue(self.win.load_game_asset(self.gradient))
        self.assertTrue(self._wait_for_texture())
        self.win.code_texture_palette_combo.setCurrentIndex(
            self.win.code_texture_palette_combo.findData(
                "gameboy"))
        self.win.code_texture_colors_spin.setValue(4)
        # Wait for the gameboy re-render to land first, so the
        # before/after comparison isolates the dither toggle —
        # the size never changes here, so a size wait would
        # return at once on the still-old render.
        deadline = time.monotonic() + 30
        landed = False
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.texture_worker is None
                    and count_opaque_colors(self.win.code_image)
                    <= 4):
                landed = True
                break
            time.sleep(0.05)
        self.assertTrue(landed, "gameboy re-render never landed")
        width, height = (self.win.code_image.width(),
                         self.win.code_image.height())
        before = [self.win.code_image.pixel(x, y)
                  for y in range(height) for x in range(width)]
        self.win.code_texture_dither_check.setChecked(True)
        deadline = time.monotonic() + 30
        changed = False
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self.win.texture_worker is None:
                after = [self.win.code_image.pixel(x, y)
                         for y in range(height) for x in range(width)]
                if after != before:
                    changed = True
                    break
            time.sleep(0.05)
        self.assertTrue(changed, "dithering changed no pixels")

    def test_skin_shows_override_not_texture_controls(self):
        self.assertTrue(self.win.load_game_asset(self.nonskin))
        self.assertEqual(self.win.code_list.count(), 13)
        self.assertFalse(self.win.code_treat_texture_btn.isHidden())
        self.assertEqual(self.win.code_treat_texture_btn.text(),
                         "Treat as texture")
        self.assertTrue(self.win.code_texture_section.isHidden())
        self.assertTrue(self.win.code_export_parts_btn.isEnabled())

    def test_treat_as_texture_renders_one_texture(self):
        self.assertTrue(self.win.load_game_asset(self.nonskin))
        self.win.code_treat_texture_btn.click()
        self.assertTrue(self._wait_for_asset_count(1))
        self.assertEqual(self.win.code_kind, "texture")
        self.assertEqual((self.win.code_image.width(),
                          self.win.code_image.height()), (64, 64))
        self.assertFalse(self.win.code_export_parts_btn.isEnabled())
        self.assertEqual(self.win.code_treat_texture_btn.text(),
                         "Treat as skin")
        self.assertFalse(self.win.code_texture_section.isHidden())
        self.assertIn("treated as a texture",
                      self.win.code_info_label.text())

    def test_overridden_skin_saves_as_texture(self):
        # Task 79: a 64x64 non-skin treated as a texture via the
        # override saves as source "texture" — and toggling
        # back to a skin saves as "skin" again. The source
        # follows how the asset is being treated, both ways.
        self.assertTrue(self.win.load_game_asset(self.nonskin))
        self.win.code_treat_texture_btn.click()
        self.assertTrue(self._wait_for_asset_count(1))
        self.assertEqual(self.win.code_kind, "texture")
        entry = self.win.save_code_asset_to_library(
            "Nonskin texture", [])
        self.assertEqual(entry["source"], "texture")
        self.win.code_treat_texture_btn.click()
        self.app.processEvents()
        self.assertEqual(self.win.code_kind, "skin")
        entry = self.win.save_code_asset_to_library(
            "Nonskin skin", [])
        self.assertEqual(entry["source"], "skin")

    def test_treat_as_skin_restores_parts(self):
        self.assertTrue(self.win.load_game_asset(self.nonskin))
        self.win.code_treat_texture_btn.click()
        self.assertTrue(self._wait_for_asset_count(1))
        self.win.code_treat_texture_btn.click()
        self.app.processEvents()
        self.assertEqual(self.win.code_kind, "skin")
        self.assertEqual(self.win.code_list.count(), 13)
        self.assertTrue(self.win.code_export_parts_btn.isEnabled())
        self.assertEqual(self.win.code_treat_texture_btn.text(),
                         "Treat as texture")
        self.assertTrue(self.win.code_texture_section.isHidden())

    def test_new_import_resets_override(self):
        self.assertTrue(self.win.load_game_asset(self.nonskin))
        self.win.code_treat_texture_btn.click()
        self.assertTrue(self._wait_for_asset_count(1))
        self.assertTrue(self.win.load_game_asset(self.nonskin))
        self.assertEqual(self.win.code_kind, "skin")
        self.assertEqual(self.win.code_list.count(), 13)
        self.assertEqual(self.win.code_treat_texture_btn.text(),
                         "Treat as texture")

    def test_code_import_hides_texture_controls(self):
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        self.assertFalse(self.win.code_texture_section.isHidden())
        self.assertTrue(self.win.load_code(self.game))
        self.assertTrue(self.win.code_texture_section.isHidden())
        self.assertTrue(self.win.code_treat_texture_btn.isHidden())


class SkinPreviewGuiTests(unittest.TestCase):
    """Task 45 — the Code tab's skin export + 3D preview
    buttons, and the preview dialog's scene. The dialog is
    never shown here: offscreen has no GL context, so the live
    render is proven by the Xvfb proof script instead."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge.image import argb
        skin = make_solid_image(64, 64, 10, 20, 30)
        for y in range(8, 16):
            for x in range(8, 16):
                skin.setPixel(x, y, argb(255, 0, 0, 255))
        cls.skin = write_image(
            skin, os.path.join(cls.tmp, "steve.png"))
        cls.texture = write_image(
            make_solid_image(16, 16, 1, 2, 3),
            os.path.join(cls.tmp, "grass.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(3)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_texture(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.code_kind == "texture"
                    and self.win.texture_worker is None):
                return True
            time.sleep(0.05)
        return False

    def test_buttons_disabled_with_nothing_loaded(self):
        self.assertFalse(self.win.code_export_skin_btn.isEnabled())
        self.assertFalse(self.win.code_preview_skin_btn.isEnabled())
        self.assertIn("Load a Minecraft skin",
                      self.win.code_export_skin_btn.toolTip())
        self.assertIn("Load a Minecraft skin",
                      self.win.code_preview_skin_btn.toolTip())

    def test_buttons_enable_with_skin_loaded(self):
        self.assertTrue(self.win.load_game_asset(self.skin))
        self.assertTrue(self.win.code_export_skin_btn.isEnabled())
        self.assertTrue(self.win.code_preview_skin_btn.isEnabled())
        self.assertIn("64x64",
                      self.win.code_export_skin_btn.toolTip())
        self.assertIn("3D figure",
                      self.win.code_preview_skin_btn.toolTip())

    def test_buttons_stay_disabled_for_texture(self):
        self.assertTrue(self.win.load_game_asset(self.texture))
        self.assertTrue(self._wait_for_texture())
        self.assertFalse(self.win.code_export_skin_btn.isEnabled())
        self.assertFalse(self.win.code_preview_skin_btn.isEnabled())
        self.assertIn("Only Minecraft skins",
                      self.win.code_export_skin_btn.toolTip())

    def test_treat_as_texture_disables_and_back_enables(self):
        self.assertTrue(self.win.load_game_asset(self.skin))
        self.assertTrue(self.win.code_export_skin_btn.isEnabled())
        self.win.code_treat_texture_btn.click()
        self.assertTrue(self._wait_for_texture())
        self.assertFalse(self.win.code_export_skin_btn.isEnabled())
        self.assertFalse(self.win.code_preview_skin_btn.isEnabled())
        self.assertIn("being treated as a texture",
                      self.win.code_export_skin_btn.toolTip())
        self.win.code_treat_texture_btn.click()
        self.app.processEvents()
        self.assertTrue(self.win.code_export_skin_btn.isEnabled())
        self.assertTrue(self.win.code_preview_skin_btn.isEnabled())

    def test_export_skin_png_writes_file(self):
        from unittest import mock
        from pixel_forge.pixelize import load_image
        self.assertTrue(self.win.load_game_asset(self.skin))
        out = os.path.join(self.tmp, "exported-skin.png")
        with mock.patch(
                "pixel_forge.gui.QFileDialog.getSaveFileName",
                return_value=(out, "PNG image")):
            self.win.code_export_skin_btn.click()
        self.assertTrue(os.path.isfile(out))
        back = load_image(out)
        self.assertEqual((back.width(), back.height()), (64, 64))
        self.assertEqual(bytes(back.data),
                         bytes(load_image(self.skin).data))
        self.assertIn("Exported the skin",
                      self.win.statusBar().currentMessage())

    def test_export_cancelled_writes_nothing(self):
        from unittest import mock
        self.assertTrue(self.win.load_game_asset(self.skin))
        with mock.patch(
                "pixel_forge.gui.QFileDialog.getSaveFileName",
                return_value=("", "")):
            self.win.code_export_skin_btn.click()
        self.assertFalse(os.path.isfile(
            os.path.join(self.tmp, "steve-skin.png")))

    def test_preview_button_opens_dialog(self):
        from unittest import mock
        self.assertTrue(self.win.load_game_asset(self.skin))
        opened = []

        class StubDialog:
            def __init__(self, parent, image):
                opened.append((parent, image))

            def exec(self):
                return 0

        with mock.patch("pixel_forge.gui.SkinPreviewDialog",
                        StubDialog):
            self.win.code_preview_skin_btn.click()
        self.assertEqual(len(opened), 1)
        parent, image = opened[0]
        self.assertIs(parent, self.win)
        self.assertEqual((image.width(), image.height()), (64, 64))

    def test_preview_dialog_scene_has_six_body_boxes(self):
        from pixel_forge.pixelize import load_image
        from pixel_forge.skinview import SkinPreviewDialog
        dialog = SkinPreviewDialog(self.win,
                                   load_image(self.skin))
        try:
            parts = [box["part"]
                     for box in dialog.preview.boxes]
            for name in ("head", "body", "right_arm", "left_arm",
                         "right_leg", "left_leg"):
                self.assertIn(name, parts)
            # The solid test skin has every overlay opaque, so
            # all twelve boxes are present.
            self.assertEqual(len(dialog.preview.boxes), 12)
            self.assertEqual(len(dialog.preview.vertices),
                             12 * 36 * 6)
            self.assertEqual(dialog.windowTitle(),
                             "3D skin preview")
            self.assertIn("Drag the figure",
                          dialog.findChild(QLabel).text())
            # The default view is a three-quarter angle, not
            # straight on.
            self.assertEqual(dialog.preview.yaw, -30.0)
            self.assertEqual(dialog.preview.pitch, 12.0)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_no_em_dash_or_parens_s_in_new_strings(self):
        texts = [
            self.win.code_export_skin_btn.text(),
            self.win.code_export_skin_btn.toolTip(),
            self.win.code_preview_skin_btn.text(),
            self.win.code_preview_skin_btn.toolTip(),
        ]
        for text in texts:
            self.assertNotIn("\u2014", text, text)
            self.assertNotIn("(s)", text, text)


class B15MinorGroupTests(unittest.TestCase):
    """Task 19 (B15 minor group): the GUI items — negative-prompt
    scroll, Code info duplication, frame-export overwrite confirm,
    drag-and-drop, and the non-pinned-model flag."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.image = write_image(make_solid_image(64, 64, 10, 200, 30),
                                os.path.join(cls.tmp, "sprite.png"))
        cls.clip = os.path.join(cls.tmp, "clip.mp4")
        make_test_video(cls.clip)
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n")
        cls.gem = os.path.join(cls.tmp, "gem.py")
        with open(cls.gem, "w") as handle:
            handle.write("GEM = [[1, 2], [2, 1]]\n")
        cls.zip = os.path.join(cls.tmp, "archive.zip")
        with open(cls.zip, "wb") as handle:
            handle.write(b"PK\x03\x04not a real zip")

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _drag_enter(self, paths):
        from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
        from PySide6.QtGui import QDragEnterEvent
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        event = QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.win.dragEnterEvent(event)
        return event.isAccepted()

    def _drop(self, paths):
        from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
        from PySide6.QtGui import QDropEvent
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        event = QDropEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.win.dropEvent(event)

    def test_negative_prompt_shows_the_start(self):
        # B15: the pre-filled default used to show scrolled to the
        # end ("eformed, distorted…"), reading as a typo.
        from pixel_forge.imagegen import DEFAULT_NEGATIVE
        edit = self.win.prompt_negative_edit
        self.assertEqual(edit.text(), DEFAULT_NEGATIVE)
        self.assertEqual(edit.cursorPosition(), 0)
        # The prompt field itself has no pre-filled text — a
        # placeholder instead — so it needs no such fix.
        self.assertEqual(self.win.prompt_edit.text(), "")
        self.assertTrue(self.win.prompt_edit.placeholderText())

    def test_palette_info_shows_color_count_once(self):
        # B15: the info label showed "2 colors" twice for palette
        # assets — once in the header, once as the detail line.
        self.assertTrue(self.win.load_code(self.game))
        # PALETTE is first in file order, so it is selected.
        text = self.win.code_info_label.text()
        self.assertIn("PALETTE", text)
        self.assertEqual(text.count("2 colors"), 1,
                         "color count duplicated: %r" % text)
        # A map asset keeps its (non-duplicating) detail line.
        item = self.win.code_list.item(1)  # HERO
        self.win.code_list.setCurrentItem(item)
        self.win._on_code_item_clicked(item)
        self.assertIn("int rows",
                      self.win.code_info_label.text())

    def test_export_frames_asks_before_overwriting(self):
        # B15: exporting into a folder that already holds
        # frame_001.png used to overwrite silently.
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        from pixel_forge import gui as gui_mod
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        out_dir = os.path.join(self.tmp, "overwrite")
        os.mkdir(out_dir)
        sentinel = os.path.join(out_dir, "frame_001.png")
        with open(sentinel, "wb") as handle:
            handle.write(b"do not touch")
        with mock.patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=out_dir), \
                mock.patch.object(
                    gui_mod.QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.No) \
                as ask:
            self.win._on_export_frames()
        ask.assert_called_once()
        with open(sentinel, "rb") as handle:
            self.assertEqual(handle.read(), b"do not touch",
                             "declining still overwrote the file")
        with mock.patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=out_dir), \
                mock.patch.object(
                    gui_mod.QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.Yes):
            self.win._on_export_frames()
        with open(sentinel, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"\x89PNG"),
                            "accepting did not overwrite with a PNG")

    def test_export_frames_without_existing_files_does_not_ask(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        out_dir = os.path.join(self.tmp, "fresh-export")
        with mock.patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=out_dir), \
                mock.patch.object(
                    gui_mod.QMessageBox, "question") as ask:
            self.win._on_export_frames()
        ask.assert_not_called()
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "frame_001.png")))

    def test_drag_enter_rejects_unsupported_file(self):
        # B15: a .zip used to get a drop cursor and then a warning
        # box after the drop.
        self.assertFalse(self._drag_enter([self.zip]))
        self.assertTrue(self._drag_enter([self.image]))
        self.assertTrue(self._drag_enter([self.zip, self.image]))

    def test_multi_file_drop_imports_everything(self):
        # B15: a multi-file drop used to import only the first
        # file and silently ignore the rest.
        self._drop([self.image, self.clip, self.game, self.gem,
                    self.zip])
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.source_path, self.image)
        self.assertIn(len(self.win.video_frames), (15, 16))
        names = [self.win.code_list.item(i).text()
                 for i in range(self.win.code_list.count())]
        self.assertTrue(any(n.startswith("HERO") for n in names))
        self.assertTrue(any(n.startswith("GEM") for n in names))
        # Code was the last tab switched to, so it stays visible.
        self.assertEqual(self.win.tabs.currentIndex(), 3)
        message = self.win.statusBar().currentMessage()
        self.assertIn("Imported", message)
        self.assertIn("archive.zip", message)
        self.assertIn("skipped", message)

    def test_drop_only_unsupported_file_warns(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod.QMessageBox, "warning") \
                as warn:
            self._drop([self.zip])
        warn.assert_called_once()
        self.assertIn("archive.zip", warn.call_args[0][2])
        self.assertIsNone(self.win.source_image)

    def test_status_line_flags_non_pinned_model(self):
        # B15: find_model picks up ANY .gguf, so a wrong model was
        # used without a word. The status line names the file and
        # flags it when it is not the pinned LCM file.
        from unittest import mock
        from pathlib import Path
        from pixel_forge import imagegen as imagegen_mod
        from pixel_forge.imagegen import MODEL_FILENAME
        engine = Path(self.tmp) / "sd-cli"
        engine.write_bytes(b"fake")
        other = Path(self.tmp) / "other-model.gguf"
        other.write_bytes(b"gguf")
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=engine), \
                mock.patch.object(imagegen_mod, "find_model",
                                  return_value=other):
            self.win._refresh_prompt_status()
        text = self.win.prompt_status_label.text()
        self.assertIn("other-model.gguf", text)
        self.assertIn("not the pinned LCM file", text)
        pinned = Path(self.tmp) / MODEL_FILENAME
        pinned.write_bytes(b"gguf")
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=engine), \
                mock.patch.object(imagegen_mod, "find_model",
                                  return_value=pinned):
            self.win._refresh_prompt_status()
        text = self.win.prompt_status_label.text()
        self.assertIn("found at", text)
        self.assertNotIn("not the pinned LCM file", text)


class ShortcutsMenusTests(unittest.TestCase):
    """Task 21 (shortcuts + menus + help): the menu bar, Ctrl+O /
    Ctrl+S routing, Ctrl+1..6 tab switching, Enter-to-generate,
    the Help menu dialogs, and the tooltip audit."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _menus(self):
        bar = self.win.menuBar()
        return {action.text(): action.menu()
                for action in bar.actions()}

    def test_menu_bar_has_file_edit_view_and_help_menus(self):
        # Task 67 adds the View menu (the Light theme switch);
        # task 86 adds the Tools menu and grows File, View,
        # and Help into the full bar.
        menus = self._menus()
        self.assertEqual(sorted(menus),
                         ["Edit", "File", "Help", "Tools",
                          "View"])
        file_items = [(a.text(), a.shortcut().toString())
                      for a in menus["File"].actions()]
        # Task 29 adds the Recent files submenu; it has no
        # shortcut, and it is disabled while the session is
        # empty (this window's library folder is fresh). Task
        # 86 adds Open image / Open video after Export and
        # Quit at the end; separators read as empty-text
        # actions.
        self.assertEqual(file_items, [("Import", "Ctrl+O"),
                                      ("Export", "Ctrl+S"),
                                      ("", ""),
                                      ("Open image...", ""),
                                      ("Open video...", ""),
                                      ("", ""),
                                      ("Recent files", ""),
                                      ("", ""),
                                      ("Quit", "Ctrl+Q")])
        self.assertFalse(menus["File"].actions()[6].isEnabled())
        # Task 86 adds Fit preview to the View menu, after a
        # separator.
        view_items = [a.text()
                      for a in menus["View"].actions()]
        self.assertEqual(view_items, ["Light theme", "",
                                      "Fit preview to window"])
        # Task 86 adds the Tools menu.
        tools_items = [a.text()
                       for a in menus["Tools"].actions()]
        self.assertEqual(tools_items,
                         ["Remove background",
                          "Snap to pixel grid",
                          "Generate normal map",
                          "",
                          "Generate a sprite",
                          "Text tool",
                          "",
                          "Run self-test..."])
        # Task 30 adds the Edit menu: Undo (Ctrl+Z) and Redo
        # (Ctrl+Y, also Ctrl+Shift+Z), both disabled on a fresh
        # window — there is nothing to undo or redo yet.
        edit_items = [(a.text(), a.shortcut().toString())
                      for a in menus["Edit"].actions()]
        self.assertEqual(edit_items, [("Undo", "Ctrl+Z"),
                                      ("Redo", "Ctrl+Y")])
        self.assertFalse(menus["Edit"].actions()[0].isEnabled())
        self.assertFalse(menus["Edit"].actions()[1].isEnabled())
        self.assertEqual(
            [s.toString()
             for s in menus["Edit"].actions()[1].shortcuts()],
            ["Ctrl+Y", "Ctrl+Shift+Z"])
        help_items = [(a.text(), a.shortcut().toString())
                      for a in menus["Help"].actions()]
        # Task 48 adds the update check after a separator (the
        # separator reads as an empty-text action): a manual
        # check, then the startup opt-in. Task 86 adds How to
        # Use (F1) at the top.
        self.assertEqual(help_items,
                         [("How to Use", "F1"),
                          ("About Pixel Forge", ""),
                          ("What the Code tab detects", ""),
                          ("Library location", ""),
                          ("", ""),
                          ("Check for updates", ""),
                          ("Check for updates on startup", "")])

    def test_about_text_shows_real_version(self):
        # The About text reads __version__, so it can never drift
        # from the shipped version — a hardcoded string would.
        from pixel_forge import __version__
        text = self.win._about_text()
        self.assertIn("Pixel Forge", text)
        self.assertIn(__version__, text)
        self.assertIn("pixel art", text)

    def test_about_dialog_shows_version(self):
        # The real dialog, opened through the Help menu action —
        # a QTimer watcher reads it and closes it.
        from PySide6.QtCore import QTimer
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QMessageBox):
                seen.append(dialog.text())
                dialog.accept()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self._menus()["Help"].actions()[1].trigger()
        finally:
            watcher.stop()
        from pixel_forge import __version__
        self.assertEqual(len(seen), 1)
        self.assertIn(__version__, seen[0])

    def test_code_detects_text_names_maps_palettes_skins_and_limits(self):
        # Grounded in codeinput.py and skins.py: the four map
        # kinds, palettes, skins, and the honest limits.
        text = self.win._code_detects_text()
        for phrase in ("int rows", "hex rows", "RGB rows",
                       "char rows", "Palettes", "Minecraft skin",
                       "never run", "512 x 512",
                       "found but not rendered",
                       "detected by size only"):
            self.assertIn(phrase, text, "missing from Code detects "
                          "text: %r" % phrase)

    def test_code_detects_dialog_opens_with_the_text(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog, QTextEdit
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QDialog):
                edit = dialog.findChild(QTextEdit)
                if edit is not None:
                    seen.append(edit.toPlainText())
                dialog.accept()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self._menus()["Help"].actions()[2].trigger()
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn("Minecraft skin", seen[0])
        self.assertIn("int rows", seen[0])

    def test_library_location_names_actual_folder(self):
        text = self.win._library_location_text()
        self.assertIn(self.lib_dir, text)

    def test_library_location_dialog_shows_the_folder(self):
        from PySide6.QtCore import QTimer
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QMessageBox):
                seen.append(dialog.text())
                dialog.accept()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self._menus()["Help"].actions()[3].trigger()
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn(self.lib_dir, seen[0])

    def test_open_library_folder_uses_xdg_open(self):
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod.subprocess, "Popen") as popen:
            self.win._open_library_folder()
        popen.assert_called_once_with(
            ["xdg-open", str(self.win.library.directory)])

    def test_ctrl_number_switches_tabs(self):
        # The real key path: Ctrl+3 lands on the Prompt tab.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.win.show()
        self.app.processEvents()
        self.win.tabs.setCurrentIndex(0)
        QTest.keyClick(self.win, Qt.Key.Key_3,
                       Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(self.win.tabs.currentIndex(), 2)
        QTest.keyClick(self.win, Qt.Key.Key_5,
                       Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(self.win.tabs.currentIndex(), 4)
        QTest.keyClick(self.win, Qt.Key.Key_6,
                       Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(self.win.tabs.currentIndex(), 5)
        QTest.keyClick(self.win, Qt.Key.Key_1,
                       Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(self.win.tabs.currentIndex(), 0)

    def test_enter_in_prompt_field_generates(self):
        # Enter in the prompt field is the Generate button.
        from unittest import mock
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.win.show()
        self.app.processEvents()
        self.win.tabs.setCurrentIndex(2)
        with mock.patch.object(self.win, "_on_prompt_generate") \
                as generate:
            self.win.prompt_edit.setFocus()
            QTest.keyClick(self.win.prompt_edit, Qt.Key.Key_Return)
            self.app.processEvents()
        generate.assert_called_once()

    def test_enter_in_other_fields_does_not_generate(self):
        # Only the prompt field generates on Enter — the negative
        # field, the search box, and the time fields keep their
        # own behavior.
        from unittest import mock
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.win.show()
        self.app.processEvents()
        with mock.patch.object(self.win, "_on_prompt_generate") \
                as generate:
            self.win.tabs.setCurrentIndex(2)
            self.win.prompt_negative_edit.setFocus()
            QTest.keyClick(self.win.prompt_negative_edit,
                           Qt.Key.Key_Return)
            self.win.tabs.setCurrentIndex(1)
            self.win.video_start_edit.setFocus()
            QTest.keyClick(self.win.video_start_edit,
                           Qt.Key.Key_Return)
            self.win.tabs.setCurrentIndex(4)
            self.win.library_search_edit.setFocus()
            QTest.keyClick(self.win.library_search_edit,
                           Qt.Key.Key_Return)
            self.app.processEvents()
        generate.assert_not_called()

    def test_ctrl_o_routes_import_per_tab(self):
        from unittest import mock
        routes = [(0, "_on_import"),
                  (1, "_on_video_import"),
                  (3, "_on_import_code")]
        for index, handler in routes:
            with mock.patch.object(self.win, handler) as call:
                self.win.tabs.setCurrentIndex(index)
                self.win._on_shortcut_import()
            call.assert_called_once()
        # Prompt tab: nothing to import — silent no-op.
        with mock.patch.object(self.win, "_on_import") as imp, \
                mock.patch.object(self.win, "_on_video_import") \
                as vid, \
                mock.patch.object(self.win, "_on_import_code") \
                as code:
            self.win.tabs.setCurrentIndex(2)
            self.win._on_shortcut_import()
        imp.assert_not_called()
        vid.assert_not_called()
        code.assert_not_called()
        # Library tab: no import of its own — it takes the user
        # to the Image tab and opens its import dialog.
        with mock.patch.object(self.win, "_on_import") as call:
            self.win.tabs.setCurrentIndex(4)
            self.win._on_shortcut_import()
        call.assert_called_once()
        self.assertEqual(self.win.tabs.currentIndex(), 0)

    def test_ctrl_s_routes_export_per_tab(self):
        from unittest import mock
        routes = [(0, "_on_export"),
                  (1, "_on_export_frames"),
                  (2, "_on_export_prompt_keeper"),
                  (3, "_on_export_code_asset"),
                  (4, "_on_library_export")]
        for index, handler in routes:
            with mock.patch.object(self.win, handler) as call:
                self.win.tabs.setCurrentIndex(index)
                self.win._on_shortcut_export()
            call.assert_called_once()

    def test_ctrl_s_noop_with_nothing_to_export(self):
        # A fresh window, every tab: Ctrl+S must open no dialog
        # and crash nothing — each export handler guards its own
        # empty state.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(
                QFileDialog, "getSaveFileName") as save, \
                mock.patch.object(
                    QFileDialog, "getExistingDirectory") as folder, \
                mock.patch.object(
                    gui_mod.QMessageBox, "warning") as warn:
            for index in range(5):
                self.win.tabs.setCurrentIndex(index)
                self.win._on_shortcut_export()
                self.app.processEvents()
        save.assert_not_called()
        folder.assert_not_called()
        warn.assert_not_called()

    def test_shortcuts_do_not_fire_while_modal_dialog_open(self):
        # WindowShortcut context: with a modal dialog up, the
        # main window is not the active window, so its shortcuts
        # stay quiet — Ctrl+3 must not switch tabs, Ctrl+O must
        # not import.
        from unittest import mock
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.win.show()
        self.app.processEvents()
        self.win.tabs.setCurrentIndex(0)
        box = QMessageBox(self.win)
        box.setWindowTitle("modal")
        box.setText("blocking")
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.show()
        self.app.processEvents()
        with mock.patch.object(self.win, "_on_import") as imp:
            QTest.keyClick(box, Qt.Key.Key_3,
                           Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(box, Qt.Key.Key_O,
                           Qt.KeyboardModifier.ControlModifier)
            self.app.processEvents()
        imp.assert_not_called()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        box.close()

    def test_every_control_has_a_tooltip(self):
        # Task 21: every control group carries a one-line tooltip
        # saying what it does. The rule covers buttons, combos,
        # spin boxes, sliders, checkboxes, line edits, and lists
        # — everything the user can touch. Spin boxes carry their
        # own tooltip; their internal qt_spinbox_lineedit is not
        # a control and is skipped.
        from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox,
                                       QComboBox, QLineEdit,
                                       QListWidget, QPushButton,
                                       QSlider)
        types = (QPushButton, QComboBox, QAbstractSpinBox, QSlider,
                 QCheckBox, QLineEdit, QListWidget)
        missing = []
        total = 0
        for cls in types:
            for widget in self.win.findChildren(cls):
                if isinstance(widget, QLineEdit) and isinstance(
                        widget.parent(), QAbstractSpinBox):
                    continue  # spin box internals, not a control
                total += 1
                if not widget.toolTip().strip():
                    label = ""
                    if isinstance(widget, (QPushButton, QCheckBox)):
                        label = widget.text()
                    missing.append("%s %r"
                                   % (widget.__class__.__name__,
                                      label))
        self.assertEqual(missing, [],
                         "controls without a tooltip: %r" % missing)
        self.assertGreaterEqual(total, 80,
                                "only %d controls audited" % total)


class MenuBarTests(unittest.TestCase):
    """Task 86 (full menu bar): every Tools menu action calls
    a real action, File opens an image or a video directly,
    View fits the preview, and Quit closes the window."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _menu_action(self, menu_name, text):
        bar = self.win.menuBar()
        for action in bar.actions():
            if action.text() == menu_name:
                for item in action.menu().actions():
                    if item.text() == text:
                        return item
        self.fail("no %r in the %s menu" % (text, menu_name))

    def test_every_tools_action_is_enabled_and_has_a_tooltip(self):
        # Nothing in the Tools menu is a placeholder: every
        # entry is enabled and explains itself.
        bar = self.win.menuBar()
        for action in bar.actions():
            if action.text() == "Tools":
                tools = action.menu()
                break
        else:
            self.fail("no Tools menu")
        seen = []
        for item in tools.actions():
            if item.isSeparator():
                continue
            seen.append(item.text())
            self.assertTrue(item.isEnabled(), item.text())
            self.assertTrue(item.toolTip().strip(), item.text())
        self.assertEqual(len(seen), 6)

    def test_open_image_goes_to_image_tab_and_imports(self):
        self.win.tabs.setCurrentIndex(1)
        with mock.patch.object(self.win, "_on_import") as imp:
            self._menu_action("File", "Open image...").trigger()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        imp.assert_called_once_with()

    def test_open_video_goes_to_video_tab_and_imports(self):
        with mock.patch.object(self.win, "_on_video_import") as imp:
            self._menu_action("File", "Open video...").trigger()
        self.assertEqual(self.win.tabs.currentIndex(), 1)
        imp.assert_called_once_with()

    def test_quit_closes_the_window(self):
        self.win.show()
        self.app.processEvents()
        self.assertTrue(self.win.isVisible())
        self._menu_action("File", "Quit").trigger()
        self.app.processEvents()
        self.assertFalse(self.win.isVisible())

    def test_fit_preview_calls_the_current_tabs_fit(self):
        # Image tab fits the image preview; the Library tab
        # has no preview zoom, so it does nothing and does
        # not crash.
        with mock.patch.object(self.win,
                               "_on_fit_image_zoom") as fit:
            self._menu_action("View",
                              "Fit preview to window").trigger()
        fit.assert_called_once_with()
        self.win.tabs.setCurrentIndex(4)
        with mock.patch.object(self.win,
                               "_on_fit_image_zoom") as fit:
            self._menu_action("View",
                              "Fit preview to window").trigger()
        fit.assert_not_called()

    def test_tool_actions_run_their_button_when_it_can(self):
        # With the button enabled, the menu entry presses it:
        # a spy on the button's clicked signal proves the
        # wiring without running the worker (every handler
        # returns early with no image loaded).
        for text, button in (
                ("Remove background", self.win.bgremove_btn),
                ("Snap to pixel grid", self.win.gridsnap_btn),
                ("Generate normal map", self.win.normalmap_btn)):
            clicked = []
            button.clicked.connect(lambda _c: clicked.append(True))
            button.setEnabled(True)
            self.win.tabs.setCurrentIndex(4)
            self._menu_action("Tools", text).trigger()
            self.assertEqual(self.win.tabs.currentIndex(), 0)
            self.assertEqual(clicked, [True], text)

    def test_tool_actions_explain_when_their_button_cannot(self):
        # With no image loaded the buttons are disabled, so
        # the menu entry goes to the tab and the status bar
        # says what is needed first — never silence.
        for text, phrase in (
                ("Remove background", "Load an image first"),
                ("Snap to pixel grid", "Load an image first"),
                ("Generate normal map", "Pixelize an image "
                 "first")):
            self._menu_action("Tools", text).trigger()
            self.assertEqual(self.win.tabs.currentIndex(), 0)
            self.assertIn(phrase,
                          self.win.statusBar().currentMessage(),
                          text)

    def test_generate_sprite_goes_to_image_tab_and_the_picker(self):
        self.win.show()
        self.app.processEvents()
        self.win.tabs.setCurrentIndex(4)
        self._menu_action("Tools", "Generate a sprite").trigger()
        self.app.processEvents()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        self.assertTrue(self.win.procedural_kind_combo.hasFocus())
        self.assertIn("Pick a kind",
                      self.win.statusBar().currentMessage())

    def test_text_tool_picks_up_the_text_tool(self):
        # With no result the tool button is disabled, so the
        # menu entry explains what is needed first.
        self._menu_action("Tools", "Text tool").trigger()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        self.assertIsNone(self.win._editor_tool)
        self.assertIn("Pixelize an image first",
                      self.win.statusBar().currentMessage())
        # With the button able to run, the menu entry picks
        # the tool up, like clicking the toolbar button.
        self.win.editor_tool_buttons["text"].setEnabled(True)
        self.win.tabs.setCurrentIndex(4)
        self._menu_action("Tools", "Text tool").trigger()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        self.assertEqual(self.win._editor_tool, "text")
        self.assertTrue(
            self.win.editor_tool_buttons["text"].isChecked())

    def test_selftest_menu_explains_the_command(self):
        # The self-test is a command line check, so the menu
        # entry explains it — a QTimer watcher reads the
        # dialog and closes it.
        from PySide6.QtCore import QTimer
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QMessageBox):
                seen.append(dialog.text())
                dialog.accept()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self._menu_action("Tools", "Run self-test...").trigger()
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn("pixel-forge --self-test", seen[0])
        self.assertIn("7 minutes", seen[0])
        self.assertIn("self-test", seen[0])

    def test_no_em_dash_or_parens_s_in_new_menu_strings(self):
        texts = []
        bar = self.win.menuBar()
        for action in bar.actions():
            menu = action.menu()
            if menu is None:
                continue
            for item in menu.actions():
                texts.append(item.text())
                texts.append(item.toolTip())
        texts.append(self.win._selftest_text())
        for text in texts:
            self.assertNotIn("\u2014", text, text)
            self.assertNotIn("(s)", text, text)


class HowToUseTests(unittest.TestCase):
    """Task 86 (How to Use index, U22): one window, a section
    per tab plus first steps, in plain words, reopening after
    close, every entry naming where the thing lives."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        if self.win._howto_dialog is not None:
            self.win._howto_dialog.close()
            self.win._howto_dialog.deleteLater()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _open_index(self):
        bar = self.win.menuBar()
        for action in bar.actions():
            if action.text() == "Help":
                for item in action.menu().actions():
                    if item.text() == "How to Use":
                        item.trigger()
                        return self.win._howto_dialog
        self.fail("no How to Use in the Help menu")

    def test_help_menu_opens_the_index(self):
        dialog = self._open_index()
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.windowTitle(),
                         "How to Use Pixel Forge")

    def test_index_lists_every_section(self):
        dialog = self._open_index()
        sections = [dialog.section_list.item(i).text()
                    for i in range(dialog.section_list.count())]
        self.assertEqual(sections,
                         ["First steps", "Image", "Video",
                          "Prompt", "Code", "Library", "Tileset"])
        text = dialog.text.toPlainText()
        for section in sections:
            self.assertIn(section, text)

    def test_every_section_names_where_things_live(self):
        # Every section's entries name the tab or menu the
        # thing lives on — the index never says "somewhere".
        from pixel_forge.gui_shared import HOWTO_SECTIONS
        for title, body in HOWTO_SECTIONS:
            if title == "First steps":
                self.assertIn("Image tab", body)
                continue
            self.assertIn("%s tab" % title, body,
                          "%s section never names its tab" % title)
            self.assertIn("menu", body.lower(),
                          "%s section never names a menu" % title)

    def test_nothing_selected_by_default(self):
        # The window is shown first: with a hidden window the
        # list never takes focus, so the check would pass
        # without proving anything. A list that takes focus
        # with no current item picks its first row — the text
        # takes the opening focus instead, so no section is
        # selected.
        self.win.show()
        self.app.processEvents()
        dialog = self._open_index()
        self.app.processEvents()
        self.assertEqual(dialog.section_list.currentRow(), -1)
        # The full text is still visible from the top — the
        # index never opens on an empty pane.
        self.assertTrue(dialog.text.toPlainText().startswith(
            "First steps"))

    def test_picking_a_section_jumps_to_it(self):
        dialog = self._open_index()
        dialog.section_list.setCurrentRow(3)
        self.assertEqual(dialog.text.textCursor().block().text(),
                         "Prompt")
        dialog.section_list.setCurrentRow(6)
        self.assertEqual(dialog.text.textCursor().block().text(),
                         "Tileset")

    def test_index_closes_and_reopens(self):
        dialog = self._open_index()
        dialog.close()
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        again = self._open_index()
        self.assertIs(again, dialog)
        self.assertTrue(dialog.isVisible())

    def test_index_remembers_geometry_with_settings(self):
        from pixel_forge.gui_shared import HowToUseDialog
        ini = os.path.join(self.lib_dir, "settings.ini")
        settings = QSettings(ini, QSettings.Format.IniFormat)
        first = HowToUseDialog(self.win, settings)
        first.show()
        first.setGeometry(QRect(50, 60, 500, 400))
        self.app.processEvents()
        first.close()
        self.app.processEvents()
        self.assertEqual(settings.value("howto/x", type=int), 50)
        self.assertEqual(settings.value("howto/width", type=int),
                         500)
        second = HowToUseDialog(self.win, settings)
        second.show()
        self.app.processEvents()
        self.assertEqual(second.geometry(), QRect(50, 60, 500, 400))
        second.close()
        first.deleteLater()
        second.deleteLater()
        self.app.processEvents()

    def test_index_controls_have_tooltips(self):
        dialog = self._open_index()
        self.assertTrue(dialog.section_list.toolTip().strip())
        self.assertTrue(dialog.text.toolTip().strip())
        for button in dialog.findChildren(QPushButton):
            self.assertTrue(button.toolTip().strip(),
                            button.text())

    def test_no_em_dash_or_parens_s_in_index_text(self):
        from pixel_forge.gui_shared import HOWTO_SECTIONS
        for title, body in HOWTO_SECTIONS:
            self.assertNotIn("\u2014", title, title)
            self.assertNotIn("\u2014", body, title)
            self.assertNotIn("(s)", title, title)
            self.assertNotIn("(s)", body, title)


class VideoDisclosureTests(unittest.TestCase):
    """Task 87 (progressive disclosure on the Video tab, U21):
    the 61-widget scroll column is six collapsible sections,
    with Export pinned below the scroll area so the export
    buttons stay visible without scrolling at 1020x640."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(1)  # Video tab

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _section_of(self, widget):
        """The CollapsibleSection a widget lives in, or None."""
        from pixel_forge.gui_shared import CollapsibleSection
        ancestor = widget.parent()
        while ancestor is not None:
            if isinstance(ancestor, CollapsibleSection):
                return ancestor
            ancestor = ancestor.parent()
        return None

    def _show_at_default_size(self):
        self.win.resize(1020, 640)
        self.win.show()
        self.app.processEvents()

    def _export_rect_in_window(self):
        export = self.win.video_sections["export"]
        rect = export.rect()
        top = export.mapTo(self.win, rect.topLeft()).y()
        bottom = export.mapTo(self.win, rect.bottomRight()).y()
        return top, bottom

    def test_six_sections_exist_with_titles(self):
        sections = self.win.video_sections
        self.assertEqual(
            [sections[key].title() for key in
             ("frames", "size", "palette", "preset", "animation",
              "export")],
            ["Frames", "Size", "Palette", "Preset", "Animation",
             "Export"])

    def test_default_states(self):
        sections = self.win.video_sections
        self.assertTrue(sections["frames"].isExpanded())
        self.assertTrue(sections["export"].isExpanded())
        for key in ("size", "palette", "preset", "animation"):
            self.assertFalse(sections[key].isExpanded(), key)

    def test_each_section_collapses_and_expands(self):
        # The window is shown first: with a hidden window every
        # isVisible() reads False, so both halves of this test
        # would pass without proving anything.
        self._show_at_default_size()
        for key, section in self.win.video_sections.items():
            section.setExpanded(False)
            self.app.processEvents()
            self.assertFalse(section.isExpanded(), key)
            self.assertFalse(section._body.isVisible(), key)
            self.assertTrue(
                section.header_button().text().startswith(
                    "\u25b8"), key)
            # Back open through the header button, the way a
            # user does it — not the setter.
            section.header_button().click()
            self.app.processEvents()
            self.assertTrue(section.isExpanded(), key)
            self.assertTrue(section._body.isVisible(), key)
            self.assertTrue(
                section.header_button().text().startswith(
                    "\u25be"), key)

    def test_more_than_one_section_stays_open(self):
        # A tool box shows one section at a time; these stay
        # open together — a user mid-task needs Frames and
        # Animation at once.
        sections = self.win.video_sections
        sections["frames"].setExpanded(True)
        sections["animation"].setExpanded(True)
        sections["size"].setExpanded(True)
        self.assertTrue(sections["frames"].isExpanded())
        self.assertTrue(sections["animation"].isExpanded())
        self.assertTrue(sections["size"].isExpanded())

    def test_every_control_still_present(self):
        # Named, not counted: every control the old column
        # held must still exist on the window.
        for attr in (
                "video_import_btn", "video_split_btn",
                "video_fps_combo", "video_every_spin",
                "video_start_edit", "video_end_edit",
                "video_estimate_label",
                "video_size_combo", "video_width_spin",
                "video_height_spin", "video_aspect_check",
                "video_palette_combo", "video_colors_spin",
                "video_dither_check",
                "video_preset_combo", "video_preset_save_btn",
                "video_preset_delete_btn",
                "video_zoom_slider", "video_zoom_fit_btn",
                "sheet_cols_combo", "sheet_padding_spin",
                "sheet_fps_spin", "sheet_pack_combo",
                "sheet_trim_check", "sheet_atlas_padding_spin",
                "sheet_extrude_spin", "sheet_preview_btn",
                "video_tag_list", "video_tag_add_btn",
                "video_tag_play_btn", "video_tag_delete_btn",
                "video_info_label", "video_export_btn",
                "sheet_export_btn", "video_gif_btn",
                "engine_export_btn", "sheet_library_btn"):
            self.assertTrue(hasattr(self.win, attr), attr)

    def test_controls_live_in_their_sections(self):
        win = self.win
        expected = {
            "video_import_btn": "Frames",
            "video_fps_combo": "Frames",
            "video_every_spin": "Frames",
            "video_size_combo": "Size",
            "video_aspect_check": "Size",
            "video_palette_combo": "Palette",
            "video_dither_check": "Palette",
            "video_preset_combo": "Preset",
            "video_zoom_slider": "Animation",
            "sheet_cols_combo": "Animation",
            "video_tag_list": "Animation",
            "video_export_btn": "Export",
            "sheet_export_btn": "Export",
            "video_gif_btn": "Export",
            "engine_export_btn": "Export",
            "sheet_library_btn": "Export",
        }
        for attr, title in expected.items():
            section = self._section_of(getattr(win, attr))
            self.assertIsNotNone(section, attr)
            self.assertEqual(section.title(), title, attr)

    def test_export_sits_outside_the_scroll_area(self):
        scroll = self.win.video_controls_scroll
        export = self.win.video_sections["export"]
        ancestor = export.parent()
        while ancestor is not None:
            self.assertIsNot(ancestor, scroll,
                             "Export is inside the scroll area")
            ancestor = ancestor.parent()
        # The five scrolling sections ARE inside it.
        for key in ("frames", "size", "palette", "preset",
                    "animation"):
            section = self.win.video_sections[key]
            ancestor = section.parent()
            inside = False
            while ancestor is not None:
                if ancestor is scroll:
                    inside = True
                    break
                ancestor = ancestor.parent()
            self.assertTrue(inside, key)

    def test_export_visible_at_defaults(self):
        self._show_at_default_size()
        top, bottom = self._export_rect_in_window()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(bottom, self.win.height())

    def test_export_visible_with_all_sections_expanded(self):
        self._show_at_default_size()
        for section in self.win.video_sections.values():
            section.setExpanded(True)
        self.app.processEvents()
        top, bottom = self._export_rect_in_window()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(bottom, self.win.height())

    def test_state_round_trips_through_settings(self):
        from pixel_forge.gui import MainWindow
        ini = os.path.join(self.lib_dir, "settings.ini")
        settings = QSettings(ini, QSettings.Format.IniFormat)
        first = MainWindow(library_dir=self.lib_dir,
                           settings=settings)
        first.video_sections["frames"].setExpanded(False)
        first.video_sections["size"].setExpanded(True)
        self.assertFalse(
            settings.value("video_sections/frames", type=bool))
        self.assertTrue(
            settings.value("video_sections/size", type=bool))
        first.close()
        first.deleteLater()
        self.app.processEvents()
        second = MainWindow(library_dir=self.lib_dir,
                            settings=settings)
        try:
            self.assertFalse(
                second.video_sections["frames"].isExpanded())
            self.assertTrue(
                second.video_sections["size"].isExpanded())
            # Untouched sections keep their defaults.
            self.assertTrue(
                second.video_sections["export"].isExpanded())
            self.assertFalse(
                second.video_sections["palette"].isExpanded())
        finally:
            second.close()
            second.deleteLater()
            self.app.processEvents()

    def test_headers_have_tooltips(self):
        for key, section in self.win.video_sections.items():
            self.assertTrue(
                section.header_button().toolTip().strip(), key)

    def test_no_em_dash_or_parens_s_in_section_text(self):
        for section in self.win.video_sections.values():
            title = section.title()
            self.assertNotIn("\u2014", title, title)
            self.assertNotIn("(s)", title, title)
            tip = section.header_button().toolTip()
            self.assertNotIn("\u2014", tip, title)
            self.assertNotIn("(s)", tip, title)


class PolishTests(unittest.TestCase):
    """Task 22 polish pass. Contrast and the "(s)" ban are in
    test_polish.py (bare python); everything here needs the real
    window."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))
        skin = make_solid_image(64, 64, 10, 20, 30)
        cls.skin = write_image(skin,
                               os.path.join(cls.tmp, "skin.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def test_status_bar_updates_on_tab_switch(self):
        hints = {
            "Image": "Import an image to turn it into a pixel sprite.",
            "Video": "Import a video to turn its frames into sprites.",
            "Prompt": "Describe a sprite and press Generate.",
            "Code": ("Import code or a game asset to see the sprites "
                     "it defines."),
            "Library": "Your saved sprites — click one to preview it.",
            "Tileset": ("Build a Wang or blob tileset from base "
                        "tiles, then export it for Tiled or Godot."),
        }
        # Tab 0 is already current at startup, so currentChanged
        # never fires for it until another tab is visited first —
        # visit it last.
        for index in [1, 2, 3, 4, 5, 0]:
            self.win.tabs.setCurrentIndex(index)
            self.app.processEvents()
            name = self.win.tabs.tabText(index)
            self.assertEqual(self.win.statusBar().currentMessage(),
                             hints[name], "tab %s" % name)

    def test_fit_zoom_on_image_load(self):
        self.win.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(self.win.load_image(self.src))
        self.app.processEvents()
        result = self.win.result_image
        viewport = self.win.after_scroll.viewport()
        fit = min(viewport.width() / result.width(),
                  viewport.height() / result.height())
        expected = max(1, min(16, int(fit)))
        self.assertEqual(self.win.zoom_slider.value(), expected,
                         "viewport %d x %d, result %d x %d"
                         % (viewport.width(), viewport.height(),
                            result.width(), result.height()))

    def test_fit_button_refits_after_manual_change(self):
        self.win.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(self.win.load_image(self.src))
        fitted = self.win.zoom_slider.value()
        other = 1 if fitted != 1 else 16
        self.win.zoom_slider.setValue(other)
        self.assertEqual(self.win.zoom_slider.value(), other)
        self.win.zoom_fit_btn.click()
        self.assertEqual(self.win.zoom_slider.value(), fitted)

    def test_repixelize_keeps_zoom(self):
        # A control change re-pixelizes but must not snap the
        # zoom back — only a new load fits.
        self.win.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(self.win.load_image(self.src))
        self.win.zoom_slider.setValue(3)
        self.win.colors_spin.setValue(4)
        self.app.processEvents()
        self.assertEqual(self.win.zoom_slider.value(), 3)

    def test_library_shows_display_labels(self):
        self.win.library.add_image(
            make_solid_image(32, 32, 255, 0, 0), "Knight",
            source="skin", palette="pico8")
        self.win.tabs.setCurrentIndex(4)
        self.app.processEvents()
        item = self.win.library_list.item(0)
        self.win.library_list.setCurrentItem(item)
        self.win._on_library_item_clicked(item)
        text = self.win.library_info_label.text()
        self.assertIn("PICO-8", text)
        self.assertIn("Skin", text)
        self.assertNotIn("Palette: pico8", text)
        self.assertNotIn("From: skin", text)

    def test_error_dialog_title_names_the_failure(self):
        # The real dialog, opened by a real failure — a QTimer
        # watcher reads its title and closes it.
        from PySide6.QtCore import QTimer
        bad = os.path.join(self.tmp, "bad.png")
        with open(bad, "wb") as handle:
            handle.write(b"not a png at all")
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QMessageBox):
                seen.append((dialog.windowTitle(), dialog.text()))
                dialog.accept()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self.assertFalse(self.win.load_image(bad))
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        title, message = seen[0]
        self.assertEqual(title, "Could not import image")
        self.assertTrue(message[:1].isupper(),
                        "message starts lowercase: %r" % message)

    def test_prompt_counter(self):
        # Task 67: the over-limit color moved into the
        # stylesheets (QLabel[overLimit="true"]) — the counter
        # flips a property now, not an inline style.
        from pixel_forge.imagegen import MAX_PROMPT_LEN
        self.assertEqual(self.win.prompt_counter_label.text(),
                         "0 / 1000")
        self.win.prompt_edit.setText("hello")
        self.assertEqual(self.win.prompt_counter_label.text(),
                         "5 / 1000")
        self.assertFalse(
            self.win.prompt_counter_label.property("overLimit"))
        self.win.prompt_edit.setText("x" * (MAX_PROMPT_LEN + 1))
        self.assertEqual(self.win.prompt_counter_label.text(),
                         "1001 / 1000")
        self.assertTrue(
            self.win.prompt_counter_label.property("overLimit"))

    def test_export_default_next_to_source(self):
        from unittest import mock
        self.assertTrue(self.win.load_image(self.src))
        seen = []

        def fake_dialog(*args, **kwargs):
            seen.append(args[2])
            return ("", "")

        with mock.patch.object(QFileDialog, "getSaveFileName",
                               side_effect=fake_dialog):
            self.win._on_export()
        self.assertEqual(seen, [os.path.join(self.tmp,
                                             "photo-pixel.png")])

    def test_export_default_uses_last_used_folder(self):
        folder = tempfile.mkdtemp()
        self.assertIsNone(self.win._last_export_dir())
        self.win._remember_export_dir(folder)
        self.assertEqual(self.win._last_export_dir(), folder)
        self.assertEqual(
            self.win._export_default("sprite.png"),
            os.path.join(folder, "sprite.png"))
        # A source file beats the remembered folder.
        self.assertEqual(
            self.win._export_default("sprite.png", "/src/photo.png"),
            "/src/sprite.png")
        # The folder survives a new window on the same library.
        from pixel_forge.gui import MainWindow
        other = MainWindow(library_dir=self.lib_dir)
        try:
            self.assertEqual(other._last_export_dir(), folder)
        finally:
            other.close()
            other.deleteLater()
            self.app.processEvents()
        # A corrupt record falls back silently.
        self.win._export_folders_path().write_text(
            "not json", encoding="utf-8")
        self.assertIsNone(self.win._last_export_dir())
        self.assertEqual(self.win._export_default("sprite.png"),
                         "sprite.png")
        # A remembered folder that no longer exists is ignored.
        gone = os.path.join(tempfile.mkdtemp(), "gone")
        self.win._remember_export_dir(gone)
        self.assertIsNone(self.win._last_export_dir())

    def test_skipped_box_is_taller_and_counts_skips(self):
        # A folder with one good file and one unparseable file:
        # the good assets import, the bad file is counted.
        from PySide6.QtWidgets import QScrollArea
        good = os.path.join(self.tmp, "good.py")
        with open(good, "w", encoding="utf-8") as handle:
            handle.write("HERO = [[1, 0], [0, 1]]\n")
        bad = os.path.join(self.tmp, "bad.py")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("this is not python (\n")
        self.assertTrue(self.win.load_code([good, bad]))
        self.assertEqual(
            self.win.code_skipped_heading.text(),
            "Found but not rendered (1 skipped)")
        scrolls = [w for w in self.win.findChildren(QScrollArea)
                   if w.widget() is self.win.code_skipped_label]
        self.assertEqual(len(scrolls), 1)
        self.assertEqual(scrolls[0].minimumHeight(), 110)
        self.assertEqual(scrolls[0].maximumHeight(), 110)

    def test_download_button_visible_without_model(self):
        from unittest import mock
        from pathlib import Path
        from pixel_forge import imagegen as imagegen_mod
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None), \
                mock.patch.object(imagegen_mod, "find_model",
                                  return_value=None):
            self.win._refresh_prompt_status()
        self.assertFalse(self.win.prompt_download_btn.isHidden())
        # A model found: the button hides — nothing to download.
        model = Path(self.tmp) / "model.gguf"
        model.write_bytes(b"gguf")
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None), \
                mock.patch.object(imagegen_mod, "find_model",
                                  return_value=model):
            self.win._refresh_prompt_status()
        self.assertTrue(self.win.prompt_download_btn.isHidden())

    def test_export_parts_tooltip_explains_disabled_state(self):
        btn = self.win.code_export_parts_btn
        self.assertFalse(btn.isEnabled())
        self.assertEqual(btn.toolTip(),
                         "Load a Minecraft skin to export its parts")
        # Code, not a skin: still disabled, with the reason.
        code = os.path.join(self.tmp, "hero.py")
        with open(code, "w", encoding="utf-8") as handle:
            handle.write("HERO = [[1, 0], [0, 1]]\n")
        self.assertTrue(self.win.load_code(code))
        self.assertFalse(btn.isEnabled())
        self.assertEqual(btn.toolTip(),
                         "Only Minecraft skins have parts to export")
        # A skin: enabled, tooltip describes the action.
        self.assertTrue(self.win.load_game_asset(self.skin))
        self.assertTrue(btn.isEnabled())
        self.assertEqual(btn.toolTip(),
                         "Export the skin's parts as separate PNGs")

    def test_no_programmer_phrasing_in_visible_text(self):
        for text in visible_texts(self.win):
            self.assertNotIn("(s)", text,
                             "programmer phrasing in visible text: %r"
                             % text)

    def test_preset_button_row_fits_the_column(self):
        # The preset row (Save + Delete side by side) measured
        # 269 px at the stylesheet's button padding — wider than
        # the column's viewport whenever the scrollbar shows, so
        # the whole column overflowed and the right edge of every
        # widget clipped (the counter read "0 / 10", the status
        # lines broke mid-word). The dark stylesheet must be
        # applied: without it the default padding is smaller and
        # the row fits, which is why no earlier screenshot caught
        # it.
        from pixel_forge.gui import DARK_STYLESHEET
        from PySide6.QtWidgets import QScrollArea
        self.app.setStyleSheet(DARK_STYLESHEET)
        try:
            self.win.show()
            self.app.processEvents()
            self.app.processEvents()
            for prefix in ("", "video_", "prompt_"):
                save = getattr(self.win, prefix + "preset_save_btn")
                delete = getattr(self.win,
                                 prefix + "preset_delete_btn")
                row = (save.minimumSizeHint().width()
                       + delete.minimumSizeHint().width() + 6)
                # Button -> ... -> viewport -> scroll area: walk
                # up to the scroll area instead of assuming a
                # fixed depth — the Video tab's preset picker
                # lives inside a collapsible section now (task
                # 87), so its chain is two widgets longer than
                # the Image and Prompt tabs'. The viewport is
                # the width that matters.
                scroll = save.parentWidget()
                while (scroll is not None and not isinstance(
                        scroll, QScrollArea)):
                    scroll = scroll.parentWidget()
                self.assertIsInstance(scroll, QScrollArea)
                viewport = scroll.viewport().width()
                self.assertLessEqual(
                    row, viewport,
                    "%spreset row is %d px, wider than the %d px "
                    "viewport — the column clips on the right"
                    % (prefix, row, viewport))
        finally:
            self.app.setStyleSheet("")

    def test_spritesheet_dialog_has_scrollbar_slack(self):
        from pixel_forge.gui import SpritesheetDialog
        frames = [make_solid_image(16, 16, 255, 0, 0),
                  make_solid_image(16, 16, 0, 255, 0)]
        dialog = SpritesheetDialog(
            self.win, frames, ["frame_001.png", "frame_002.png"],
            cols=2, padding=0, fps=8, default_name="sheet.png")
        try:
            holder = dialog.sheet_label.parentWidget()
            margins = holder.layout().contentsMargins()
            self.assertEqual((margins.left(), margins.top(),
                              margins.right(), margins.bottom()),
                             (8, 8, 8, 8))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


class LibraryDepthGuiTests(unittest.TestCase):
    """Library depth (task 24): filters, sort, tag editing,
    thumbnail cache, badges, repair, duplicates, backup, and
    the sheet frame picker."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(4)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _add(self, name, tags=(), r=255, g=0, b=0, w=32, h=32,
             source="image"):
        return self.win.library.add_image(
            make_solid_image(w, h, r, g, b), name, tags=tags,
            source=source)

    def _add_sheet(self, name="Walk cycle"):
        from pixel_forge.spritesheet import write_sheet
        frames = [make_solid_image(16, 16, 255, 0, 0),
                  make_solid_image(16, 16, 0, 255, 0)]
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        write_sheet(frames, ["frame_001.png", "frame_002.png"],
                    png, meta, cols=2, padding=0, fps=8)
        return self.win.library.add_sheet(png, meta, name,
                                          frames=2)

    def _select(self, row=0):
        item = self.win.library_list.item(row)
        self.win.library_list.setCurrentItem(item)
        self.win._on_library_item_clicked(item)
        return item

    def _item_texts(self):
        return [self.win.library_list.item(row).text()
                for row in range(self.win.library_list.count())]

    def _set_combo(self, combo, data):
        combo.setCurrentIndex(combo.findData(data))
        self.app.processEvents()

    def test_filter_combos_exist_with_sensible_defaults(self):
        self.assertEqual(
            self.win.library_kind_combo.itemText(0), "All kinds")
        self.assertEqual(
            self.win.library_source_combo.itemText(0),
            "All sources")
        self.assertEqual(
            self.win.library_collection_combo.itemText(0),
            "All collections")
        self.assertEqual(
            self.win.library_sort_combo.itemText(0), "Saved order")
        self.assertEqual(
            self.win.library_kind_combo.currentData(), None)
        self.assertEqual(
            self.win.library_sort_combo.currentData(), "saved")

    def test_kind_source_and_collection_filters_compose(self):
        self._add("Knight", tags=["hero"], source="image")
        self._add("Slime", source="code")
        sheet = self._add_sheet()
        self.win.library.update(sheet["id"], collection="walks")
        self.win._refresh_library()
        self.assertEqual(len(self._item_texts()), 3)
        self._set_combo(self.win.library_kind_combo, "sheet")
        self.assertEqual(self._item_texts(),
                         ["Walk cycle — Sheet"])
        self._set_combo(self.win.library_kind_combo, None)
        self._set_combo(self.win.library_source_combo, "code")
        self.assertEqual(self._item_texts(), ["Slime"])
        self._set_combo(self.win.library_source_combo, None)
        self._set_combo(self.win.library_collection_combo,
                        "walks")
        self.assertEqual(self._item_texts(),
                         ["Walk cycle — Sheet"])
        self._set_combo(self.win.library_collection_combo, "")
        self.assertEqual(sorted(self._item_texts()),
                         ["Knight", "Slime"])
        self._set_combo(self.win.library_collection_combo, None)
        self.assertEqual(len(self._item_texts()), 3)

    def test_sort_orders(self):
        self._add("Zebra", w=16, h=16)
        self._add("Apple", w=64, h=64)
        self._add("Mango", w=32, h=32)
        self.win._refresh_library()
        self.assertEqual(self._item_texts(),
                         ["Zebra", "Apple", "Mango"])
        self._set_combo(self.win.library_sort_combo, "name")
        self.assertEqual(self._item_texts(),
                         ["Apple", "Mango", "Zebra"])
        self._set_combo(self.win.library_sort_combo, "largest")
        self.assertEqual(self._item_texts(),
                         ["Apple", "Mango", "Zebra"])
        # Newest/oldest need distinct created stamps — the three
        # adds above happened inside one second, so their stamps
        # tie. Rewrite the stamps in the index and re-read.
        path = os.path.join(self.lib_dir, "library.json")
        with open(path) as f:
            data = json.load(f)
        stamps = {"Zebra": "2026-11-12T00:00:00+00:00",
                  "Apple": "2026-11-12T01:00:00+00:00",
                  "Mango": "2026-11-12T02:00:00+00:00"}
        for entry in data["entries"]:
            entry["created"] = stamps[entry["name"]]
        with open(path, "w") as f:
            json.dump(data, f)
        from pixel_forge.library import Library
        self.win.library = Library(self.lib_dir)
        self.win._refresh_library()
        self._set_combo(self.win.library_sort_combo, "newest")
        self.assertEqual(self._item_texts(),
                         ["Mango", "Apple", "Zebra"])
        self._set_combo(self.win.library_sort_combo, "oldest")
        self.assertEqual(self._item_texts(),
                         ["Zebra", "Apple", "Mango"])

    def test_sheet_and_missing_badges(self):
        self._add("Knight")
        self._add_sheet()
        os.unlink(os.path.join(self.lib_dir, "knight.png"))
        self.win._refresh_library()
        self.assertEqual(sorted(self._item_texts()),
                         ["Knight — Missing",
                          "Walk cycle — Sheet"])

    def test_thumbnail_cache_builds_once_per_entry(self):
        self._add("Knight")
        self._add("Slime", r=0, g=0, b=255)
        self._add_sheet()
        self.win._thumb_builds = 0
        for _ in range(5):
            self.win._refresh_library()
        self.assertEqual(self.win._thumb_builds, 3)
        # Typing in the search box is a refresh too — still no
        # rebuild (this was the original defect: every keystroke
        # rebuilt every thumbnail).
        self.win.library_search_edit.setText("k")
        self.app.processEvents()
        self.win.library_search_edit.setText("kn")
        self.app.processEvents()
        self.assertEqual(self.win._thumb_builds, 3)
        # A changed file rebuilds just that one thumbnail.
        write_image(make_solid_image(48, 48, 255, 0, 0),
                    os.path.join(self.lib_dir, "knight.png"))
        self.win.library_search_edit.setText("")
        self.app.processEvents()
        self.win._refresh_library()
        self.assertEqual(self.win._thumb_builds, 4)

    def test_edit_tags_dialog_round_trip(self):
        from pixel_forge.gui import EditTagsDialog
        entry = self._add("Knight", tags=["hero"])
        dialog = EditTagsDialog(self.win, entry)
        try:
            self.assertEqual(dialog.tags_edit.text(), "hero")
            self.assertEqual(dialog.collection_edit.text(), "")
            dialog.tags_edit.setText("hero, red")
            dialog.collection_edit.setText("heroes")
            dialog.accept()
            self.assertEqual(dialog.tags_list(),
                             ["hero", "red"])
            self.assertEqual(dialog.collection_text(), "heroes")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_edit_tags_button_updates_entry_and_filters(self):
        from unittest import mock
        import pixel_forge.gui as gui_mod
        entry = self._add("Knight", tags=["hero"])
        self.win._refresh_library()
        self._select(0)

        class _StubDialog:
            def __init__(self, parent, entry):
                pass

            def exec(self):
                from PySide6.QtWidgets import QDialog
                return QDialog.DialogCode.Accepted

            def tags_list(self):
                return ["hero", "red"]

            def collection_text(self):
                return "heroes"

        with mock.patch.object(gui_mod, "EditTagsDialog",
                               _StubDialog):
            self.win._on_library_edit_tags()
        updated = self.win.library.find(entry["id"])
        self.assertEqual(updated["tags"], ["hero", "red"])
        self.assertEqual(updated["collection"], "heroes")
        self.assertIn("heroes", [self.win.library_collection_combo
                                 .itemText(i) for i in range(
                                     self.win.library_collection_combo
                                     .count())])
        self.assertIn("red", [self.win.library_tag_combo.itemText(i)
                              for i in range(
                                  self.win.library_tag_combo
                                  .count())])
        # The selection survives the refresh, so Edit tags stays
        # enabled; with nothing selected it is disabled.
        self.assertTrue(self.win.library_edit_tags_btn.isEnabled())
        self.win._clear_library_selection()
        self.assertFalse(self.win.library_edit_tags_btn
                         .isEnabled())

    def test_orphan_repair_asks_first(self):
        from unittest import mock
        self._add("Knight")
        self._add("Slime", r=0, g=0, b=255)
        os.unlink(os.path.join(self.lib_dir, "knight.png"))
        self.win._refresh_library()
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.win._on_library_repair()
        self.assertEqual(len(self.win.library.entries()), 2)
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.win._on_library_repair()
        self.assertEqual(
            [e["name"] for e in self.win.library.entries()],
            ["Slime"])
        self.assertEqual(self.win.library_list.count(), 1)
        # With no orphans left, Repair says so and asks nothing.
        with mock.patch.object(
                QMessageBox, "question",
                side_effect=AssertionError("must not ask")):
            self.win._on_library_repair()
        self.assertIn("No missing files",
                      self.win.statusBar().currentMessage())

    def test_duplicate_marker_and_removal(self):
        from unittest import mock
        self._add("Knight")
        self._add("Knight copy")
        self._add("Slime", r=0, g=0, b=255)
        self.win._refresh_library()
        self._select(0)
        self.assertIn("Duplicate of: Knight copy",
                      self.win.library_info_label.text())
        self._select(2)
        self.assertNotIn("Duplicate",
                         self.win.library_info_label.text())
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.win._on_library_find_duplicates()
        self.assertEqual(len(self.win.library.entries()), 3)
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.win._on_library_find_duplicates()
        self.assertEqual(
            [e["name"] for e in self.win.library.entries()],
            ["Knight", "Slime"])
        self.assertFalse(os.path.exists(os.path.join(
            self.lib_dir, "knight-copy.png")))
        with mock.patch.object(
                QMessageBox, "question",
                side_effect=AssertionError("must not ask")):
            self.win._on_library_find_duplicates()
        self.assertIn("No duplicates",
                      self.win.statusBar().currentMessage())

    def test_export_and_import_library_round_trip(self):
        from unittest import mock
        from pixel_forge.gui import MainWindow
        self._add("Knight", tags=["hero"])
        self._add_sheet()
        backup = os.path.join(tempfile.mkdtemp(), "backup")
        with mock.patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=backup):
            self.win._on_library_export_all()
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "knight.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "walk-cycle.json")))
        lib2_dir = os.path.join(tempfile.mkdtemp(), "lib2")
        win2 = MainWindow(library_dir=lib2_dir)
        try:
            win2.tabs.setCurrentIndex(4)
            with mock.patch.object(
                    QFileDialog, "getExistingDirectory",
                    return_value=backup):
                win2._on_library_import()
            self.assertEqual(win2.library_list.count(), 2)
            self.assertEqual(
                [e["name"] for e in win2.library.entries()],
                ["Knight", "Walk cycle"])
            # Importing the same backup again skips, never
            # duplicates.
            with mock.patch.object(
                    QFileDialog, "getExistingDirectory",
                    return_value=backup):
                win2._on_library_import()
            self.assertEqual(len(win2.library.entries()), 2)
            self.assertIn("skipped 2 sprites already here",
                          win2.statusBar().currentMessage())
        finally:
            win2.close()
            win2.deleteLater()
            self.app.processEvents()

    def test_import_bad_folder_warns_plainly(self):
        from unittest import mock
        import pixel_forge.gui as gui_mod
        seen = []
        with mock.patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=os.path.join(tempfile.mkdtemp(),
                                          "nope")), \
            mock.patch.object(
                gui_mod, "warn_dialog",
                side_effect=lambda p, t, m: seen.append((t, m))):
            self.win._on_library_import()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "Could not import the library")

    def test_sheet_frame_dialog(self):
        from pixel_forge.gui import SheetFrameDialog
        from pixel_forge.spritesheet import load_metadata
        self._add_sheet()
        meta = load_metadata(os.path.join(
            self.lib_dir, "walk-cycle.json"))
        dialog = SheetFrameDialog(self.win, "Walk cycle", meta)
        try:
            self.assertFalse(dialog.load_frame_btn.isEnabled())
            self.assertEqual(dialog.frame_list.count(), 2)
            dialog.frame_list.setCurrentRow(1)
            self.assertTrue(dialog.load_frame_btn.isEnabled())
            dialog.load_frame_btn.click()
            self.assertEqual(dialog.frame_index(), 1)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()
        dialog = SheetFrameDialog(self.win, "Walk cycle", meta)
        try:
            dialog.load_sheet_btn.click()
            self.assertIsNone(dialog.frame_index())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_sheet_frame_dialog_picks_nothing_on_open(self):
        """Qt moves the list's current row to 0 when the dialog
        is shown — that auto-set must not enable "Load frame".
        Only a real pick (a click, or the arrow keys) does."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog
        from pixel_forge.gui import SheetFrameDialog
        from pixel_forge.spritesheet import load_metadata
        self._add_sheet()
        meta = load_metadata(os.path.join(
            self.lib_dir, "walk-cycle.json"))
        dialog = SheetFrameDialog(self.win, "Walk cycle", meta)
        seen = []
        state = {"clicked": False, "moved": False, "ticks": 0}

        def watch():
            state["ticks"] += 1
            row = dialog.frame_list.currentRow()
            enabled = dialog.load_frame_btn.isEnabled()
            if not state["clicked"]:
                # Wait for Qt's auto-set to row 0 (it lands
                # after the first paint, not at once) — the
                # button must stay disabled through it.
                seen.append((row, enabled))
                if state["ticks"] > 40:
                    # The auto-set never came; fail loudly
                    # rather than hang the suite.
                    dialog.reject()
                elif row == 0:
                    item = dialog.frame_list.item(0)
                    dialog.frame_list.itemClicked.emit(item)
                    state["clicked"] = True
            elif not state["moved"]:
                # A click on the current row is a pick.
                seen.append((row, enabled))
                dialog.frame_list.setCurrentRow(1)
                state["moved"] = True
            else:
                # Moving rows keeps the button enabled.
                seen.append((row, enabled))
                dialog.reject()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self.assertEqual(dialog.exec(),
                             QDialog.DialogCode.Rejected)
        finally:
            watcher.stop()
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()
        self.assertTrue(state["clicked"],
                        "Qt never auto-set row 0: %r" % (seen,))
        self.assertTrue(state["moved"])
        clicked_at = next(i for i, s in enumerate(seen) if s[1])
        # Everything before the click has the button disabled —
        # including the auto-set row 0.
        self.assertTrue(all(not s[1] for s in seen[:clicked_at]),
                        "button enabled before any pick: %r"
                        % (seen,))
        self.assertIn((0, False), seen[:clicked_at])
        # A click on the current row is a pick; moving rows
        # keeps the button enabled.
        self.assertEqual(seen[clicked_at], (0, True))
        self.assertEqual(seen[-1], (1, True))

    def test_load_sheet_entry_offers_frames(self):
        from unittest import mock
        import pixel_forge.gui as gui_mod
        self._add_sheet()
        self.win._refresh_library()
        self._select(0)

        class _StubDialog:
            index = 1

            def __init__(self, parent, name, metadata):
                pass

            def exec(self):
                from PySide6.QtWidgets import QDialog
                return QDialog.DialogCode.Accepted

            def frame_index(self):
                return _StubDialog.index

        with mock.patch.object(gui_mod, "SheetFrameDialog",
                               _StubDialog):
            self.win._on_library_load()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        # Frame 1 is the green 16x16 frame, not the 32x16 sheet.
        self.assertEqual((self.win.source_image.width(),
                          self.win.source_image.height()),
                         (16, 16))
        self.assertEqual(bytes(self.win.source_image.data[:4]),
                         b"\x00\xff\x00\xff")
        # And the "Load sheet" choice loads the packed sheet.
        _StubDialog.index = None
        self.win.tabs.setCurrentIndex(4)
        self._select(0)
        with mock.patch.object(gui_mod, "SheetFrameDialog",
                               _StubDialog):
            self.win._on_library_load()
        self.assertEqual((self.win.source_image.width(),
                          self.win.source_image.height()),
                         (32, 16))

    def test_load_sprite_entry_unchanged(self):
        self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        self.win._on_library_load()
        self.assertEqual(self.win.tabs.currentIndex(), 0)
        self.assertEqual((self.win.source_image.width(),
                          self.win.source_image.height()),
                         (32, 32))


class VideoGuardrailsTests(unittest.TestCase):
    """Task 25: the frame-count estimate + confirm, GIF input,
    dimmed dropped frames, strip reorder, multi-select."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        # 2 seconds at 24 fps, 8 fps extraction = 16 frames.
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))
        # The same clip as a GIF, 8 fps — GIF is a video input
        # now, not just a still image.
        cls.gif = os.path.join(cls.tmp, "clip.gif")
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-i", cls.clip, "-vf", "fps=8,scale=64:48",
                        cls.gif], check=True, timeout=60)
        # A GIF with DISTINCT per-frame delays (task 83) —
        # built with the concat demuxer so each frame keeps
        # its own delay: 0.1, 0.3, 0.2, 0.4 s, plus the
        # repeated last frame the demuxer adds (5 frames).
        # ffmpeg quantizes the delays when it writes the GIF,
        # so the tests below allow 30 ms either way.
        colors = ["red", "green", "blue", "yellow"]
        lines = []
        for index, delay in enumerate([0.1, 0.3, 0.2, 0.4]):
            png = os.path.join(cls.tmp, "timed-%d.png" % index)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=%s:s=32x32:d=0.1" % colors[index],
                 "-frames:v", "1", png],
                check=True, timeout=60)
            lines.append("file '%s'" % png)
            lines.append("duration %s" % delay)
        lines.append("file '%s'" % os.path.join(
            cls.tmp, "timed-3.png"))
        list_path = os.path.join(cls.tmp, "timed.txt")
        with open(list_path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        cls.timed_gif = os.path.join(cls.tmp, "timed.gif")
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-f", "concat", "-safe", "0",
                        "-i", list_path, cls.timed_gif],
                       check=True, timeout=60)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(1)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _set_fps(self, fps):
        combo = self.win.video_fps_combo
        combo.setCurrentIndex(combo.findData(fps))

    def _expected_label(self):
        # The label the estimate should show at the current
        # settings — computed through the same pure function the
        # window uses, so the test pins the wiring, not a number
        # baked in by hand.
        from pixel_forge import video as video_mod
        estimate = video_mod.estimate_frame_count(
            self.win.video_duration,
            self.win.video_fps_combo.currentData(),
            every=self.win.video_every_spin.value())
        return "about %d frames" % estimate

    def test_estimate_label_empty_without_video(self):
        self.assertEqual(self.win.video_estimate_label.text(), "")

    def test_estimate_label_shows_frame_count(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_estimate_label.text(),
                         self._expected_label())
        self.assertIn("16 frames",
                      self.win.video_estimate_label.text())

    def test_estimate_updates_with_fps_and_every(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self._set_fps(4)
        self.app.processEvents()
        self.assertEqual(self.win.video_estimate_label.text(),
                         "about 8 frames")
        self.win.video_every_spin.setValue(2)
        self.app.processEvents()
        self.assertEqual(self.win.video_estimate_label.text(),
                         "about 4 frames")
        # Let the debounced re-extract settle before tearDown.
        self.assertTrue(self._wait_for_video())

    def test_no_confirm_below_threshold(self):
        from PySide6.QtCore import QTimer
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None:
                seen.append(dialog.windowTitle())
                dialog.accept()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self._set_fps(4)
            self.assertTrue(self._wait_for_video())
        finally:
            watcher.stop()
        self.assertEqual(seen, [])
        self.assertEqual(len(self.win.video_frames), 8)

    def test_confirm_above_threshold_declined(self):
        from PySide6.QtCore import QTimer
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        # Stub a long clip: 100 seconds at 8 fps = 800 frames,
        # over the 500-frame threshold.
        self.win.video_duration = 100.0
        self._set_fps(8)
        frames_before = list(self.win.video_frames)
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QMessageBox):
                seen.append((dialog.windowTitle(), dialog.text()))
                no = dialog.button(QMessageBox.StandardButton.No)
                no.click()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self.assertFalse(self.win._reload_video_frames())
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn("800 frames", seen[0][0])
        self.assertIn("800 frames", seen[0][1])
        self.assertIsNone(self.win.video_worker)
        self.assertEqual(self.win.video_frames, frames_before)
        self.assertIn("cancelled",
                      self.win.statusBar().currentMessage())

    def test_confirm_above_threshold_accepted(self):
        from PySide6.QtCore import QTimer
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.win.video_duration = 100.0
        self._set_fps(8)
        seen = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and isinstance(dialog, QMessageBox):
                seen.append(dialog.windowTitle())
                yes = dialog.button(QMessageBox.StandardButton.Yes)
                yes.click()

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            self.assertTrue(self.win._reload_video_frames())
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (15, 16))

    def test_gif_imports_as_video(self):
        from pixel_forge.gui import video_filter
        self.assertEqual(self.win._classify_drop("clip.gif"), "video")
        self.assertIn("*.gif", video_filter())
        self.assertTrue(self.win.load_video(self.gif))
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (15, 16))
        self.assertEqual(self.win.video_strip.count(),
                         len(self.win.video_frames))

    def test_gif_import_keeps_native_delays(self):
        # Task 83: a GIF extracts at its own rate, frame for
        # frame, and each frame keeps the delay from the file
        # — not the Sheet fps default, and not a resample.
        self.assertTrue(self.win.load_video(self.timed_gif))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(len(self.win.video_frames), 5)
        durations = self.win.video_durations
        self.assertEqual(len(durations), 5)
        for got, want in zip(durations, [100, 300, 200, 400, 40]):
            self.assertAlmostEqual(got, want, delta=30)
        self.assertNotEqual(set(durations), {125})

    def test_gif_import_falls_back_when_probe_fails(self):
        # Task 83: a probe that fails entirely still imports —
        # every frame gets the Sheet fps default (125 ms).
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod, "probe_frame_durations",
                               return_value=None):
            self.assertTrue(self.win.load_video(self.timed_gif))
            self.assertTrue(self._wait_for_video())
        self.assertEqual(len(self.win.video_frames), 5)
        self.assertEqual(self.win.video_durations, [125] * 5)

    def test_gif_import_falls_back_per_frame(self):
        # Task 83: a frame with no readable delay keeps the
        # default while its neighbours keep theirs.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(
                gui_mod, "probe_frame_durations",
                return_value=[None, 300, None, None, None]):
            self.assertTrue(self.win.load_video(self.timed_gif))
            self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_durations,
                         [125, 300, 125, 125, 125])

    def test_gif_import_falls_back_when_probe_miscounts(self):
        # Task 83: a probe that returns the wrong number of
        # durations cannot map onto the frames — every frame
        # gets the default rather than a shifted timing.
        from unittest import mock
        from pixel_forge import gui as gui_mod
        with mock.patch.object(gui_mod, "probe_frame_durations",
                               return_value=[100, 200]):
            self.assertTrue(self.win.load_video(self.timed_gif))
            self.assertTrue(self._wait_for_video())
        self.assertEqual(len(self.win.video_frames), 5)
        self.assertEqual(self.win.video_durations, [125] * 5)

    def test_dropped_frame_is_dimmed(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())

        def center_alpha(item):
            image = item.icon().pixmap(64, 64).toImage()
            return image.pixelColor(32, 32).alpha()

        kept = self.win.video_strip.item(1)
        dropped = self.win.video_strip.item(0)
        self.assertGreater(center_alpha(kept), 200)
        dropped.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertLess(center_alpha(dropped), 150)
        self.assertGreater(center_alpha(kept), 200)
        # Re-checking restores the bright thumbnail.
        dropped.setCheckState(Qt.CheckState.Checked)
        self.app.processEvents()
        self.assertGreater(center_alpha(dropped), 200)

    def test_reorder_changes_export_and_sheet_order(self):
        from PySide6.QtCore import QModelIndex
        from pixel_forge.pixelize import load_image
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_order,
                         list(range(len(self.win.video_frames))))
        # Drag row 0 to position 2, through the model (the same
        # rowsMoved path a real drag takes).
        model = self.win.video_strip.model()
        self.assertTrue(model.moveRows(
            QModelIndex(), 0, 1, QModelIndex(), 3))
        self.app.processEvents()
        self.assertEqual(self.win.video_order[2], 0)
        self.assertEqual(self.win.video_order[0], 1)
        # The label keeps the ORIGINAL frame number.
        self.assertEqual(self.win.video_strip.item(2).text(),
                         "Frame 001")
        # Frame export follows the strip: the first file is the
        # frame now first in the strip (original frame 2).
        out = os.path.join(self.tmp, "reordered-export")
        self.assertTrue(self.win.export_frames(out))
        first = load_image(os.path.join(out, "frame_001.png"))
        self.assertEqual(first.data, self.win.video_frames[1].data)
        # The sheet follows the strip too.
        images, _names = self.win._kept_sheet_frames()
        self.assertIs(images[0], self.win.video_frames[1])

    def test_multi_select_keep_and_drop_selected(self):
        from PySide6.QtWidgets import QAbstractItemView
        self.assertEqual(
            self.win.video_strip.selectionMode(),
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertFalse(self.win.video_keep_selected_btn.isEnabled())
        self.assertFalse(self.win.video_drop_selected_btn.isEnabled())
        self.win.video_strip.item(0).setSelected(True)
        self.win.video_strip.item(2).setSelected(True)
        self.app.processEvents()
        self.assertTrue(self.win.video_keep_selected_btn.isEnabled())
        self.assertTrue(self.win.video_drop_selected_btn.isEnabled())
        self.win.video_drop_selected_btn.click()
        self.app.processEvents()
        self.assertFalse(self.win.video_keep[0])
        self.assertTrue(self.win.video_keep[1])
        self.assertFalse(self.win.video_keep[2])
        self.win.video_keep_selected_btn.click()
        self.app.processEvents()
        self.assertTrue(all(self.win.video_keep))

    def test_no_frame_preselected(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.video_strip.selectedItems(), [])
        self.assertEqual(self.win.video_strip.currentRow(), -1)


class BatchProcessingGuiTests(unittest.TestCase):
    """Task 28: Export all in the Code tab and the multi-file
    drop — every supported file imported, none silently skipped."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.image1 = write_image(make_solid_image(64, 64, 10, 200, 30),
                                 os.path.join(cls.tmp, "first.png"))
        cls.image2 = write_image(make_solid_image(48, 48, 200, 30, 10),
                                 os.path.join(cls.tmp, "second.png"))
        cls.clip1 = os.path.join(cls.tmp, "long.mp4")
        make_test_video(cls.clip1, duration=2)
        cls.clip2 = os.path.join(cls.tmp, "short.mp4")
        make_test_video(cls.clip2, duration=1)
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n")
        cls.zip = os.path.join(cls.tmp, "archive.zip")
        with open(cls.zip, "wb") as handle:
            handle.write(b"PK\x03\x04not a real zip")

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _drop(self, paths):
        from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
        from PySide6.QtGui import QDropEvent
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        event = QDropEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.win.dropEvent(event)

    def test_export_all_button_disabled_with_no_assets(self):
        self.assertFalse(self.win.code_export_all_btn.isEnabled())
        self.assertEqual(self.win.code_export_all_btn.text(),
                         "Export all")
        self.assertTrue(self.win.code_export_all_btn.toolTip())

    def test_export_all_writes_every_asset(self):
        from unittest.mock import patch
        from pixel_forge.pixelize import load_image
        self.assertTrue(self.win.load_code(self.game))
        self.assertTrue(self.win.code_export_all_btn.isEnabled())
        out_dir = os.path.join(self.tmp, "exported")
        os.mkdir(out_dir)
        with patch.object(QFileDialog, "getExistingDirectory",
                          return_value=out_dir):
            self.win.code_export_all_btn.click()
        self.app.processEvents()
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "palette.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "hero.png")))
        self.assertIn("Exported 2 assets",
                      self.win.statusBar().currentMessage())
        hero = load_image(os.path.join(out_dir, "hero.png"))
        self.assertEqual((hero.width(), hero.height()), (2, 2))

    def test_export_all_cancel_writes_nothing(self):
        from unittest.mock import patch
        self.assertTrue(self.win.load_code(self.game))
        out_dir = os.path.join(self.tmp, "cancelled")
        os.mkdir(out_dir)
        with patch.object(QFileDialog, "getExistingDirectory",
                          return_value=""):
            self.win.code_export_all_btn.click()
        self.app.processEvents()
        self.assertEqual(os.listdir(out_dir), [])

    def test_multi_file_drop_imports_every_image_and_video(self):
        self._drop([self.image1, self.image2,
                    self.clip1, self.clip2])
        self.assertTrue(self._wait_for_video())
        # The last image showing, the last video extracted: 1 s
        # at 8 fps is 8 frames, where the 2 s clip is 16.
        self.assertEqual(self.win.source_path, self.image2)
        self.assertEqual(len(self.win.video_frames), 8)
        message = self.win.statusBar().currentMessage()
        for name in ("first.png", "second.png",
                     "long.mp4", "short.mp4"):
            self.assertIn(name, message, message)
        self.assertNotIn("skipped", message)

    def test_multi_file_drop_unsupported_still_warned(self):
        self._drop([self.image1, self.zip])
        message = self.win.statusBar().currentMessage()
        self.assertIn("first.png", message)
        self.assertIn("skipped", message)
        self.assertIn("archive.zip", message)


class SessionGuiTests(unittest.TestCase):
    """Task 29: recent files in the File menu and session restore
    on startup. Every test gets its own library folder, so the
    session under test is isolated from the shared test library."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.image = write_image(
            make_solid_image(64, 64, 10, 200, 30),
            os.path.join(cls.tmp, "first.png"))
        cls.image2 = write_image(
            make_solid_image(48, 48, 200, 30, 10),
            os.path.join(cls.tmp, "second.png"))
        cls.skin = write_image(
            make_solid_image(64, 64, 30, 10, 200),
            os.path.join(cls.tmp, "skin.png"))
        cls.amp = write_image(
            make_solid_image(32, 32, 200, 200, 10),
            os.path.join(cls.tmp, "rock & roll.png"))
        cls.game1 = os.path.join(cls.tmp, "game1.py")
        with open(cls.game1, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n")
        cls.game2 = os.path.join(cls.tmp, "game2.py")
        with open(cls.game2, "w") as handle:
            handle.write("SWORD = [[0, 1], [1, 0]]\n")
        cls.clip = None
        try:
            cls.clip = make_test_video(
                os.path.join(cls.tmp, "clip.mp4"), duration=2)
        except Exception:
            pass  # video tests skip below when ffmpeg is missing

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        # restore=False: these tests drive restore by hand, so a
        # show() never restores behind the test's back.
        self.win = MainWindow(library_dir=self.lib_dir,
                              restore=False)
        self._extra_windows = []

    def tearDown(self):
        for extra in self._extra_windows:
            extra.close()
            extra.deleteLater()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _session_file(self):
        from pixel_forge.session import load_session
        data, corrupt = load_session(self.lib_dir)
        return data, corrupt

    def _wait_for_video(self, win=None, timeout=30):
        win = win or self.win
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (win.video_worker is None
                    and not win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def test_recent_menu_disabled_when_empty(self):
        self.assertFalse(self.win._recent_menu.isEnabled())
        self.assertEqual(self.win._recent_menu.actions(), [])

    def test_image_load_records_session(self):
        self.assertTrue(self.win.load_image(self.image))
        data, corrupt = self._session_file()
        self.assertFalse(corrupt)
        self.assertEqual(data["last_image"], self.image)
        self.assertEqual(data["recent"],
                         [{"path": self.image, "kind": "image"}])
        self.assertTrue(self.win._recent_menu.isEnabled())
        actions = self.win._recent_menu.actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text(), "first.png — image")
        self.assertEqual(actions[0].data(),
                         (self.image, "image"))

    def test_failed_load_records_nothing(self):
        from unittest import mock
        import pixel_forge.gui as gui_mod
        with mock.patch.object(gui_mod.QMessageBox, "warning"):
            self.assertFalse(self.win.load_image(
                os.path.join(self.tmp, "missing.png")))
        data, corrupt = self._session_file()
        self.assertEqual(data["recent"], [])
        self.assertIsNone(data["last_image"])
        self.assertFalse(os.path.exists(
            os.path.join(self.lib_dir, "session.json")))
        self.assertFalse(self.win._recent_menu.isEnabled())

    def test_video_load_records_session(self):
        if self.clip is None:
            raise unittest.SkipTest("ffmpeg not installed")
        self.assertTrue(self.win.load_video(self.clip))
        data, _corrupt = self._session_file()
        self.assertEqual(data["last_video"], self.clip)
        self.assertEqual(data["recent"],
                         [{"path": self.clip, "kind": "video"}])
        self.assertTrue(self._wait_for_video())

    def test_code_load_records_each_file_and_last_code(self):
        self.assertTrue(self.win.load_code([self.game1,
                                            self.game2]))
        data, _corrupt = self._session_file()
        self.assertEqual(data["last_code"],
                         [self.game1, self.game2])
        self.assertEqual(
            [e["path"] for e in data["recent"]],
            [self.game2, self.game1])
        self.assertTrue(all(e["kind"] == "code"
                            for e in data["recent"]))

    def test_game_asset_records_skin(self):
        self.assertTrue(self.win.load_game_asset(self.skin))
        data, _corrupt = self._session_file()
        self.assertEqual(data["recent"],
                         [{"path": self.skin, "kind": "skin"}])

    def test_recent_menu_lists_most_recent_first(self):
        self.assertTrue(self.win.load_image(self.image))
        self.assertTrue(self.win.load_code(self.game1))
        self.assertTrue(self.win.load_image(self.image2))
        texts = [a.text()
                 for a in self.win._recent_menu.actions()]
        self.assertEqual(texts, ["second.png — image",
                                 "game1.py — code",
                                 "first.png — image"])

    def test_recent_menu_click_loads_into_tab(self):
        if self.clip is None:
            raise unittest.SkipTest("ffmpeg not installed")
        self.assertTrue(self.win.load_image(self.image))
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.win.tabs.setCurrentIndex(0)
        actions = self.win._recent_menu.actions()
        video_action = [a for a in actions
                        if a.data()[1] == "video"][0]
        video_action.trigger()
        self.assertEqual(self.win.tabs.currentIndex(), 1)
        self.assertEqual(self.win.video_path, self.clip)
        self.assertTrue(self._wait_for_video())
        self.assertGreater(len(self.win.video_frames), 0)

    def test_missing_recent_entry_prunes_without_modal(self):
        self.assertTrue(self.win.load_image(self.image))
        os.unlink(self.image)
        try:
            action = self.win._recent_menu.actions()[0]
            action.trigger()
            self.app.processEvents()
            self.assertIsNone(QApplication.activeModalWidget())
            data, _corrupt = self._session_file()
            self.assertEqual(data["recent"], [])
            self.assertFalse(self.win._recent_menu.isEnabled())
            self.assertIn("No longer there: first.png",
                          self.win.statusBar().currentMessage())
        finally:
            # The shared setUpClass image is gone — put it back
            # for the tests after this one.
            write_image(make_solid_image(64, 64, 10, 200, 30),
                        self.image)

    def test_ampersand_in_filename_is_escaped(self):
        # Qt reads a bare & in menu text as a mnemonic marker and
        # eats it — the action text must double it, so the menu
        # shows the file's real name.
        self.assertTrue(self.win.load_image(self.amp))
        actions = self.win._recent_menu.actions()
        self.assertEqual(actions[0].text(),
                         "rock && roll.png — image")

    def test_restore_loads_last_image_video_code(self):
        if self.clip is None:
            raise unittest.SkipTest("ffmpeg not installed")
        from pixel_forge.gui import MainWindow
        self.assertTrue(self.win.load_image(self.image))
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertTrue(self.win.load_code(self.game1))
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        # A fresh window on the same library folder, restore on:
        # show() triggers the restore.
        win2 = MainWindow(library_dir=self.lib_dir)
        self._extra_windows.append(win2)
        win2.show()
        self.assertTrue(self._wait_for_video(win2))
        self.assertEqual(win2.source_path, self.image)
        self.assertEqual(win2.video_path, self.clip)
        self.assertGreater(len(win2.video_frames), 0)
        self.assertGreater(len(win2.code_assets), 0)
        # Nothing switches tabs: the Image tab stays current.
        self.assertEqual(win2.tabs.currentIndex(), 0)

    def test_restore_skips_missing_files_silently(self):
        from pixel_forge.gui import MainWindow
        from pixel_forge.session import (empty_session, save_session,
                                         set_last_code,
                                         set_last_image,
                                         set_last_video)
        data = empty_session()
        set_last_image(data, os.path.join(self.tmp, "gone.png"))
        set_last_video(data, os.path.join(self.tmp, "gone.mp4"))
        set_last_code(data, [os.path.join(self.tmp, "gone.py")])
        save_session(self.lib_dir, data)
        win2 = MainWindow(library_dir=self.lib_dir)
        self._extra_windows.append(win2)
        win2.show()
        self.app.processEvents()
        self.assertIsNone(QApplication.activeModalWidget())
        self.assertIsNone(win2.source_path)
        self.assertIsNone(win2.video_path)
        self.assertEqual(win2.code_assets, [])
        self.assertIn("Could not restore",
                      win2.statusBar().currentMessage())

    def test_restore_flag_off_skips_restore(self):
        # The mechanism main() uses for a command-line file: a
        # window built with restore=False never restores, even
        # with a session on disk.
        from pixel_forge.session import (empty_session, save_session,
                                         set_last_image)
        data = empty_session()
        set_last_image(data, self.image)
        save_session(self.lib_dir, data)
        self.win.show()
        self.app.processEvents()
        self.assertIsNone(self.win.source_path)

    def test_corrupt_session_reads_empty_and_warns(self):
        from pixel_forge.gui import MainWindow
        from pixel_forge.session import session_path
        os.makedirs(self.lib_dir, exist_ok=True)
        session_path(self.lib_dir).write_text(
            "{not json", encoding="utf-8")
        win2 = MainWindow(library_dir=self.lib_dir,
                          restore=False)
        self._extra_windows.append(win2)
        self.assertEqual(win2._session["recent"], [])
        self.assertFalse(win2._recent_menu.isEnabled())
        self.assertIn("unreadable",
                      win2.statusBar().currentMessage())
        # The bad file is left alone.
        self.assertEqual(
            session_path(self.lib_dir).read_text(
                encoding="utf-8"),
            "{not json")


class UndoGuiTests(unittest.TestCase):
    """Task 30: Undo/Redo in the Edit menu — library deletes,
    preset deletes, keep/drop choices, control changes. Every
    test gets its own library folder, so the trash under test
    is isolated from the shared test library."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.image = write_image(
            make_gradient_image(),
            os.path.join(cls.tmp, "photo.png"))
        cls.clip = None
        try:
            cls.clip = make_test_video(
                os.path.join(cls.tmp, "clip.mp4"), duration=2)
        except Exception:
            pass  # video tests skip below when ffmpeg is missing

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        # restore=False: no session restore behind the test's
        # back on show().
        self.win = MainWindow(library_dir=self.lib_dir,
                              restore=False)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _add(self, name, r=255, g=0, b=0):
        return self.win.library.add_image(
            make_solid_image(32, 32, r, g, b), name)

    def _select(self, row=0):
        item = self.win.library_list.item(row)
        self.win.library_list.setCurrentItem(item)
        self.win._on_library_item_clicked(item)
        return item

    def _confirm(self, answer):
        """Patch the Yes/No confirm to answer Yes or No."""
        from unittest import mock
        return mock.patch.object(
            QMessageBox, "question", return_value=answer)

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _pick(self, combo, key):
        index = combo.findData(key)
        self.assertGreaterEqual(index, 0, "no such preset: %s" % key)
        combo.setCurrentIndex(index)
        self.app.processEvents()

    def test_edit_menu_disabled_on_fresh_window(self):
        self.assertFalse(self.win._undo_action.isEnabled())
        self.assertFalse(self.win._redo_action.isEnabled())
        self.assertEqual(self.win._undo_action.text(), "Undo")
        self.assertEqual(self.win._redo_action.text(), "Redo")

    def test_library_delete_undo_restores_entry_and_file(self):
        first = self._add("Knight")
        second = self._add("Slime", r=0, g=255)
        third = self._add("Ghost", r=0, g=0, b=255)
        self.win._refresh_library()
        self._select(1)
        lib_file = os.path.join(self.lib_dir, second["file"])
        self.assertTrue(os.path.exists(lib_file))
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win._on_library_delete()
        self.assertEqual([e["name"] for e in
                          self.win.library.entries()],
                         ["Knight", "Ghost"])
        self.assertFalse(os.path.exists(lib_file))
        # The file waits in the trash while the delete is
        # undoable, and the menu names the step.
        trash_file = os.path.join(
            self.lib_dir, "trash", second["id"], second["file"])
        self.assertTrue(os.path.exists(trash_file))
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Delete Slime")
        self.assertTrue(self.win._undo_action.isEnabled())
        self.win._on_undo()
        entries = self.win.library.entries()
        self.assertEqual([e["name"] for e in entries],
                         ["Knight", "Slime", "Ghost"])
        self.assertEqual(entries[1]["id"], second["id"])
        self.assertEqual(entries[1]["file"], second["file"])
        self.assertTrue(os.path.exists(lib_file))
        self.assertFalse(os.path.exists(trash_file))
        self.assertEqual(self.win.library_list.count(), 3)
        # Redo deletes it again, and a second undo brings it
        # back once more.
        self.win._on_redo()
        self.assertEqual([e["name"] for e in
                          self.win.library.entries()],
                         ["Knight", "Ghost"])
        self.assertTrue(os.path.exists(trash_file))
        self.win._on_undo()
        self.assertEqual([e["name"] for e in
                          self.win.library.entries()],
                         ["Knight", "Slime", "Ghost"])
        self.assertTrue(os.path.exists(lib_file))

    def test_library_delete_undo_restores_sheet_pair(self):
        png = write_image(make_solid_image(64, 64, 0, 0, 255),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as handle:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "frames": []}, handle)
        sheet = self.win.library.add_sheet(png, meta, "Walk",
                                           frames=3)
        self.win._refresh_library()
        self._select(0)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win._on_library_delete()
        self.assertEqual(self.win.library.entries(), [])
        self.win._on_undo()
        entries = self.win.library.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], sheet["id"])
        self.assertTrue(os.path.exists(
            os.path.join(self.lib_dir, sheet["file"])))
        self.assertTrue(os.path.exists(
            os.path.join(self.lib_dir, sheet["meta"])))

    def test_trash_emptied_when_entry_leaves_stack(self):
        # The stack cap is 50; a 2-entry stack proves the
        # eviction path without pushing 51 deletes.
        from pixel_forge.undo import UndoStack
        self.win._undo_stack = UndoStack(cap=2)
        self.win._refresh_undo_actions()
        first = self._add("Knight")
        second = self._add("Slime", r=0, g=255)
        third = self._add("Ghost", r=0, g=0, b=255)
        self.win._refresh_library()
        for row in (0, 0, 0):
            self._select(row)
            with self._confirm(QMessageBox.StandardButton.Yes):
                self.win._on_library_delete()
        trash = os.path.join(self.lib_dir, "trash")
        # The oldest delete was evicted — its trash is gone for
        # real. The two newer deletes are still undoable.
        self.assertFalse(os.path.exists(
            os.path.join(trash, first["id"])))
        self.assertTrue(os.path.exists(
            os.path.join(trash, second["id"], second["file"])))
        self.assertTrue(os.path.exists(
            os.path.join(trash, third["id"], third["file"])))
        self.assertEqual(len(self.win._undo_stack), 2)
        # Only the two newer deletes can come back.
        self.win._on_undo()
        self.win._on_undo()
        self.assertEqual([e["name"] for e in
                          self.win.library.entries()],
                         ["Slime", "Ghost"])
        self.assertFalse(self.win._undo_action.isEnabled())

    def test_close_clears_trash(self):
        entry = self._add("Knight")
        self.win._refresh_library()
        self._select(0)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win._on_library_delete()
        trash = os.path.join(self.lib_dir, "trash")
        self.assertTrue(os.path.exists(
            os.path.join(trash, entry["id"], entry["file"])))
        self.win.close()
        self.assertFalse(os.path.exists(trash))

    def test_preset_delete_undo_restores_settings(self):
        settings = {"width": 16, "height": 16, "keep_aspect": False,
                    "palette": "pico8", "max_colors": 8,
                    "dither": True}
        self.win.preset_store.save("My tiny", settings)
        self.win._refresh_preset_combos()
        self._pick(self.win.preset_combo, "user:My tiny")
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win._on_delete_preset("image")
        self.assertIsNone(self.win.preset_store.get("My tiny"))
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Delete preset My tiny")
        self.win._on_undo()
        self.assertEqual(self.win.preset_store.get("My tiny"),
                         settings)
        # The picker re-selects the restored preset.
        self.assertEqual(self.win.preset_combo.currentData(),
                         "user:My tiny")
        self.win._on_redo()
        self.assertIsNone(self.win.preset_store.get("My tiny"))

    def test_keep_all_undo_restores_kept_set(self):
        if self.clip is None:
            self.skipTest("ffmpeg not installed")
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(len(self.win.video_frames), 16)
        # Drop frames 2 and 5, then Keep all.
        for row in (1, 4):
            self.win.video_strip.item(row).setSelected(True)
        self.win._on_drop_selected()
        self.assertEqual(len(self.win._kept_indices()), 14)
        self.win._on_keep_all()
        self.assertEqual(len(self.win._kept_indices()), 16)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Keep all frames")
        self.win._on_undo()
        self.assertEqual(len(self.win._kept_indices()), 14)
        self.assertEqual(self.win._kept_indices(),
                         [i for i in range(16) if i not in (1, 4)])
        # The strip, the dimming, and the counter all come back.
        self.assertEqual(
            self.win.video_strip.item(1).checkState(),
            Qt.CheckState.Unchecked)
        self.assertEqual(
            self.win.video_strip.item(0).checkState(),
            Qt.CheckState.Checked)
        self.assertIn("14 kept",
                      self.win.video_counter_label.text())
        self.win._on_redo()
        self.assertEqual(len(self.win._kept_indices()), 16)

    def test_checkbox_toggle_is_one_undo_step(self):
        if self.clip is None:
            self.skipTest("ffmpeg not installed")
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        item = self.win.video_strip.item(2)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertEqual(len(self.win._kept_indices()), 15)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Drop frame 3")
        self.win._on_undo()
        self.assertEqual(len(self.win._kept_indices()), 16)
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)

    def test_control_change_undo_restores_value_and_preview(self):
        self.assertTrue(self.win.load_image(self.image))
        before = self.win.info_label.text()
        before_count = int(before.split(", ")[1].split(" ")[0])
        self.win.colors_spin.setValue(4)
        self.app.processEvents()
        self.assertEqual(self.win.colors_spin.value(), 4)
        after_count = int(self.win.info_label.text().split(", ")[1]
                          .split(" ")[0])
        # The preview re-pixelized at 4 colors — fewer distinct
        # colors than the 16-color original.
        self.assertLess(after_count, before_count)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Change colors")
        self.win._on_undo()
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertEqual(self.win.info_label.text(), before)
        self.win._on_redo()
        self.assertEqual(self.win.colors_spin.value(), 4)
        redo_count = int(self.win.info_label.text().split(", ")[1]
                         .split(" ")[0])
        self.assertEqual(redo_count, after_count)

    def test_preset_application_is_one_undo_step(self):
        self.assertTrue(self.win.load_image(self.image))
        self.assertEqual(self.win.palette_combo.currentData(),
                         "image")
        self.assertEqual(self.win.colors_spin.value(), 16)
        self._pick(self.win.preset_combo, "builtin:Game Boy 32")
        self.assertEqual(self.win.palette_combo.currentData(),
                         "gameboy")
        self.assertEqual(self.win.colors_spin.value(), 4)
        # One step for the whole application, not one per
        # control — and it is named after the preset.
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Apply preset Game Boy 32")
        self.win._on_undo()
        self.assertEqual(self.win.palette_combo.currentData(),
                         "image")
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertEqual(self.win.preset_combo.currentData(), None)
        self.assertFalse(self.win._undo_action.isEnabled())

    def test_undo_redo_do_not_push_entries(self):
        self.assertTrue(self.win.load_image(self.image))
        self.win.colors_spin.setValue(8)
        self.win.width_spin.setValue(64)
        self.app.processEvents()
        self.assertEqual(len(self.win._undo_stack), 2)
        self.win._on_undo()
        self.win._on_undo()
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertEqual(self.win.width_spin.value(), 32)
        self.assertEqual(len(self.win._undo_stack), 0)
        self.assertFalse(self.win._undo_action.isEnabled())
        self.assertTrue(self.win._redo_action.isEnabled())
        self.win._on_redo()
        self.win._on_redo()
        self.assertEqual(self.win.colors_spin.value(), 8)
        self.assertEqual(self.win.width_spin.value(), 64)
        self.assertEqual(len(self.win._undo_stack), 2)
        self.assertFalse(self.win._redo_action.isEnabled())

    def test_edit_menu_names_the_step(self):
        self.assertTrue(self.win.load_image(self.image))
        self.win.dither_check.setChecked(False)
        self.app.processEvents()
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Change dither")
        self.assertTrue(self.win._undo_action.isEnabled())
        self.assertFalse(self.win._redo_action.isEnabled())
        self.win._on_undo()
        self.assertEqual(self.win._undo_action.text(), "Undo")
        self.assertFalse(self.win._undo_action.isEnabled())
        self.assertEqual(self.win._redo_action.text(),
                         "Redo Change dither")
        self.assertTrue(self.win._redo_action.isEnabled())

    def test_undo_action_doubles_ampersand(self):
        # A name with & in it must render literally in the menu,
        # not lose the & to Qt's mnemonic marker.
        self._add("Rock & Roll")
        self.win._refresh_library()
        self._select(0)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win._on_library_delete()
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Delete Rock && Roll")


class PixelEditorTests(unittest.TestCase):
    """Task 33: the pixel editor on the Image tab — tools,
    colors, undo, and the re-pixelize guard. The test image is
    a 32 x 32 source with a red left half and a blue right
    half, so the result has exactly two colors and the fill
    boundary is a straight line."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge.image import Image, argb
        source = Image(32, 32)
        for y in range(32):
            for x in range(32):
                source.setPixel(x, y, argb(255, 0, 0) if x < 16
                                else argb(0, 0, 255))
        cls.two_color = write_image(
            source, os.path.join(cls.tmp, "two.png"))
        cls.solid = write_image(
            make_solid_image(32, 32, 0, 255, 0),
            os.path.join(cls.tmp, "solid.png"))
        from pixel_forge.image import argb as _argb
        cls.red = _argb(255, 0, 0)
        cls.blue = _argb(0, 0, 255)
        cls.green = _argb(0, 255, 0)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir,
                              restore=False)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _load_two_color(self):
        self.assertTrue(self.win.load_image(self.two_color))
        self.app.processEvents()
        self.assertEqual(self.win.result_image.width(), 32)
        self.assertEqual(self.win.result_image.height(), 32)

    def _confirm(self, answer):
        """Patch the Yes/No confirm to answer Yes or No."""
        from unittest import mock
        return mock.patch.object(
            QMessageBox, "question", return_value=answer)

    def _pick_tool(self, tool):
        self.win.editor_tool_buttons[tool].click()
        self.app.processEvents()

    def _pick_color(self, index=0):
        swatches = list(self.win._editor_swatch_by_color.values())
        self.assertGreater(len(swatches), index)
        swatches[index].click()
        self.app.processEvents()

    def _pos(self, x, y):
        return self.win.after_label.pos_for_pixel(x, y)

    def _click(self, x, y):
        from PySide6.QtTest import QTest
        QTest.mouseClick(self.win.after_label,
                         Qt.MouseButton.LeftButton,
                         pos=self._pos(x, y))
        self.app.processEvents()

    def _drag(self, x0, y0, x1, y1):
        from PySide6.QtTest import QTest
        canvas = self.win.after_label
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton,
                         pos=self._pos(x0, y0))
        QTest.mouseMove(canvas, pos=self._pos(x1, y1))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton,
                           pos=self._pos(x1, y1))
        self.app.processEvents()

    def _pixel(self, x, y):
        return self.win.result_image.pixel(x, y)

    def test_tools_disabled_before_load(self):
        self.assertFalse(
            self.win.editor_tool_buttons["pencil"].isEnabled())
        self.assertFalse(self.win.editor_color_scroll.isEnabled())
        self._load_two_color()
        self.assertTrue(
            self.win.editor_tool_buttons["pencil"].isEnabled())
        self.assertTrue(self.win.editor_color_scroll.isEnabled())

    def test_nothing_selected_by_default(self):
        self._load_two_color()
        self.assertIsNone(self.win._editor_tool)
        self.assertIsNone(self.win._editor_color)
        for button in self.win.editor_tool_buttons.values():
            self.assertFalse(button.isChecked())
        for button in self.win._editor_swatch_by_color.values():
            self.assertFalse(button.isChecked())

    def test_click_with_no_tool_does_nothing(self):
        self._load_two_color()
        self._click(5, 5)
        self.assertEqual(len(self.win._undo_stack), 0)
        self.assertEqual(self._pixel(5, 5), self.red)
        self.assertFalse(self.win._result_edited)

    def test_pencil_click_paints_one_pixel(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.assertEqual(self._pixel(3, 4), self.red)
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertEqual(self.win._undo_action.text(), "Undo Draw")
        self.assertTrue(self.win._result_edited)
        self.assertIn("(edited)", self.win.info_label.text())

    def test_pencil_drag_is_one_undo_step(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._drag(2, 2, 2, 5)
        for y in range(2, 6):
            self.assertEqual(self._pixel(2, y), self.blue)
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertEqual(len(self.win._undo_stack), 1)
        # Undo restores the pixels and clears the edited flag;
        # redo paints them again.
        self.win._on_undo()
        for y in range(2, 6):
            self.assertEqual(self._pixel(2, y), self.red)
        self.assertFalse(self.win._result_edited)
        self.assertNotIn("(edited)", self.win.info_label.text())
        self.assertEqual(self.win._redo_action.text(), "Redo Draw")
        self.win._on_redo()
        for y in range(2, 6):
            self.assertEqual(self._pixel(2, y), self.blue)
        self.assertTrue(self.win._result_edited)

    def test_pencil_without_color_does_nothing(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._click(3, 3)
        self.assertEqual(len(self.win._undo_stack), 0)
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertIn("Pick a color",
                      self.win.statusBar().currentMessage())

    def test_eraser_paints_transparent(self):
        self._load_two_color()
        self._pick_tool("eraser")
        self._click(3, 3)
        self.assertEqual(self._pixel(3, 3), 0)
        self.assertEqual(self._pixel(3, 4), self.red)
        self.assertEqual(self.win._undo_action.text(), "Undo Erase")
        self.win._on_undo()
        self.assertEqual(self._pixel(3, 3), self.red)

    def test_fill_fills_the_region(self):
        self._load_two_color()
        self._pick_tool("fill")
        self._pick_color(1)  # blue
        self._click(2, 2)  # the red left half
        for x in (0, 8, 15):
            self.assertEqual(self._pixel(x, 15), self.blue)
        for x in (16, 24, 31):
            self.assertEqual(self._pixel(x, 15), self.blue)
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertEqual(self.win._undo_action.text(), "Undo Fill")
        self.win._on_undo()
        self.assertEqual(self._pixel(2, 2), self.red)
        self.assertEqual(self._pixel(20, 20), self.blue)

    def test_fill_with_matching_color_is_a_noop(self):
        self._load_two_color()
        self._pick_tool("fill")
        self._pick_color(0)  # red
        self._click(2, 2)  # already red
        self.assertEqual(len(self.win._undo_stack), 0)
        self.assertFalse(self.win._result_edited)

    def test_eyedropper_picks_the_color(self):
        self._load_two_color()
        self._pick_tool("eyedropper")
        self._click(20, 20)  # the blue right half
        self.assertEqual(self.win._editor_color, self.blue)
        self.assertEqual(len(self.win._undo_stack), 0)
        self.assertFalse(self.win._result_edited)
        swatch = self.win._editor_swatch_by_color[self.blue]
        self.assertTrue(swatch.isChecked())

    def test_eyedropper_on_transparent_picks_nothing(self):
        self._load_two_color()
        self._pick_tool("eraser")
        self._click(3, 3)
        self._pick_tool("eyedropper")
        self._click(3, 3)
        self.assertIsNone(self.win._editor_color)
        self.assertIn("transparent",
                      self.win.statusBar().currentMessage())

    def test_line_draws_and_undoes(self):
        self._load_two_color()
        self._pick_tool("line")
        self._pick_color(1)  # blue
        self._drag(2, 2, 5, 2)
        for x in range(2, 6):
            self.assertEqual(self._pixel(x, 2), self.blue)
        self.assertEqual(self._pixel(2, 3), self.red)
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertEqual(self.win._undo_action.text(), "Undo Line")
        self.win._on_undo()
        for x in range(2, 6):
            self.assertEqual(self._pixel(x, 2), self.red)

    def test_rectangle_draws_outline_only(self):
        self._load_two_color()
        self._pick_tool("rectangle")
        self._pick_color(1)  # blue
        self._drag(2, 2, 5, 5)
        for x in range(2, 6):
            self.assertEqual(self._pixel(x, 2), self.blue)
            self.assertEqual(self._pixel(x, 5), self.blue)
        for y in range(2, 6):
            self.assertEqual(self._pixel(2, y), self.blue)
            self.assertEqual(self._pixel(5, y), self.blue)
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertEqual(self._pixel(4, 4), self.red)
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Rectangle")

    def test_color_strip_lists_result_colors(self):
        self._load_two_color()
        colors = list(self.win._editor_swatch_by_color)
        self.assertEqual(colors, [self.red, self.blue])

    def test_canvas_mapping_round_trips(self):
        self._load_two_color()
        canvas = self.win.after_label
        for x, y in ((0, 0), (5, 7), (31, 31), (16, 16)):
            self.assertEqual(canvas.pixel_at(canvas.pos_for_pixel(
                x, y)), (x, y))
        from PySide6.QtCore import QPoint
        self.assertIsNone(canvas.pixel_at(QPoint(-100, -100)))

    def test_repixelize_guard_declines(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)
        self._click(3, 3)
        self.assertEqual(self._pixel(3, 3), self.blue)
        with self._confirm(QMessageBox.StandardButton.No) as ask:
            self.win.colors_spin.setValue(8)
            self.app.processEvents()
        self.assertEqual(ask.call_count, 1)
        # The control is back where it was, the edit survives,
        # and no control-change step was recorded.
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.assertTrue(self.win._result_edited)
        self.assertEqual(len(self.win._undo_stack), 1)

    def test_repixelize_guard_accepts(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)
        self._click(3, 3)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win.colors_spin.setValue(8)
            self.app.processEvents()
        self.assertEqual(self.win.colors_spin.value(), 8)
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertFalse(self.win._result_edited)
        self.assertNotIn("(edited)", self.win.info_label.text())
        # The edit entry stays on the stack below the control
        # change — undoing the control change first keeps the
        # snapshots consistent.
        self.assertEqual(len(self.win._undo_stack), 2)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Change colors")

    def test_preset_guard_declines(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)
        self._click(3, 3)
        index = self.win.preset_combo.findData("builtin:Game Boy 32")
        self.assertGreaterEqual(index, 0)
        with self._confirm(QMessageBox.StandardButton.No) as ask:
            self.win.preset_combo.setCurrentIndex(index)
            self.app.processEvents()
        self.assertEqual(ask.call_count, 1)
        self.assertIsNone(self.win.preset_combo.currentData())
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.assertTrue(self.win._result_edited)
        self.assertEqual(len(self.win._undo_stack), 1)

    def test_load_guard_declines_and_accepts(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)
        self._click(3, 3)
        with self._confirm(QMessageBox.StandardButton.No):
            self.assertFalse(self.win.load_image(self.solid))
        self.assertEqual(self.win.source_path, self.two_color)
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.assertTrue(self.win._result_edited)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.assertTrue(self.win.load_image(self.solid))
        self.assertEqual(self.win.source_path, self.solid)
        self.assertEqual(self._pixel(3, 3), self.green)
        self.assertFalse(self.win._result_edited)

    def test_export_writes_edited_pixels(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)
        self._click(3, 3)
        from pixel_forge.pixelize import load_image as core_load
        out = os.path.join(self.tmp, "edited.png")
        self.assertTrue(self.win.export_image(out))
        back = core_load(out)
        self.assertEqual(back.pixel(3, 3), self.blue)
        self.assertEqual(back.pixel(3, 4), self.red)

    def test_save_to_library_saves_edited_pixels(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)
        self._click(3, 3)
        entry = self.win.save_image_result_to_library("Edited", [])
        from pixel_forge.pixelize import load_image as core_load
        back = core_load(os.path.join(self.lib_dir, entry["file"]))
        self.assertEqual(back.pixel(3, 3), self.blue)
        self.assertEqual(back.pixel(3, 4), self.red)


class StampTextGuiTests(unittest.TestCase):
    """Task 47: the Text tool and the stamp dialog — click to
    place, type the text, stamp onto the active layer, one
    undo step. The test image is the same two-color 32 x 32
    as the editor tests, so the stamp's pixels read against
    known colors."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge.image import Image, argb
        source = Image(32, 32)
        for y in range(32):
            for x in range(32):
                source.setPixel(x, y, argb(255, 0, 0) if x < 16
                                else argb(0, 0, 255))
        cls.two_color = write_image(
            source, os.path.join(cls.tmp, "two.png"))
        from pixel_forge.image import argb as _argb
        cls.red = _argb(255, 0, 0)
        cls.blue = _argb(0, 0, 255)
        cls.black = _argb(0, 0, 0)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir,
                              restore=False)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _load_two_color(self):
        self.assertTrue(self.win.load_image(self.two_color))
        self.app.processEvents()
        self.assertEqual(self.win.result_image.width(), 32)

    def _dialog(self, x=0, y=0, color=None):
        from pixel_forge.gui import StampTextDialog
        if color is None:
            color = self.black
        dialog = StampTextDialog(self.win, self.win.result_image,
                                 x, y, color)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_text_tool_in_row_disabled_before_load(self):
        button = self.win.editor_tool_buttons["text"]
        self.assertEqual(button.text(), "Text")
        self.assertTrue(button.toolTip().strip())
        self.assertNotIn("—", button.toolTip())
        self.assertFalse(button.isEnabled())
        self._load_two_color()
        self.assertTrue(button.isEnabled())
        self.assertFalse(button.isChecked())

    def test_text_tool_hint_names_the_click(self):
        self._load_two_color()
        self.win.editor_tool_buttons["text"].click()
        self.app.processEvents()
        self.assertEqual(self.win._editor_tool, "text")
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Text: click where the text goes")

    def test_click_opens_dialog_at_the_click_and_stamps(self):
        from unittest import mock

        from PySide6.QtTest import QTest
        from pixel_forge.gui import QDialog
        from pixel_forge.textstamp import render_text

        self._load_two_color()
        before = self.win.result_image.copy()
        calls = []

        class StubDialog:
            def __init__(self, parent, image, x, y, color):
                calls.append((x, y, color))
                self.text_value = "HI"
                self.mask = render_text("HI")
                self.x = x
                self.y = y
                self.color = color

            def exec(self):
                return QDialog.DialogCode.Accepted

        self.win.editor_tool_buttons["text"].click()
        self.app.processEvents()
        with mock.patch("pixel_forge.gui.StampTextDialog",
                        StubDialog):
            QTest.mouseClick(
                self.win.after_label, Qt.MouseButton.LeftButton,
                pos=self.win.after_label.pos_for_pixel(5, 6))
            self.app.processEvents()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 5)
        self.assertEqual(calls[0][1], 6)
        # H's top-left ink lands at the click, in black (no
        # color was picked, so the dialog starts black).
        self.assertEqual(self.win.result_image.pixel(5, 6),
                         self.black)
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertIn("Stamped 'HI' at 5, 6",
                      self.win.statusBar().currentMessage())
        self.win._on_undo()
        self.assertEqual(self.win.result_image, before)

    def test_cancelled_dialog_stamps_nothing(self):
        from unittest import mock

        from PySide6.QtTest import QTest
        from pixel_forge.gui import QDialog

        self._load_two_color()
        before = self.win.result_image.copy()

        class StubDialog:
            def __init__(self, *args):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        self.win.editor_tool_buttons["text"].click()
        self.app.processEvents()
        with mock.patch("pixel_forge.gui.StampTextDialog",
                        StubDialog):
            QTest.mouseClick(
                self.win.after_label, Qt.MouseButton.LeftButton,
                pos=self.win.after_label.pos_for_pixel(5, 6))
            self.app.processEvents()
        self.assertEqual(self.win.result_image, before)
        self.assertEqual(len(self.win._undo_stack), 0)

    def test_dialog_stamp_disabled_until_text_typed(self):
        self._load_two_color()
        dialog = self._dialog()
        self.assertFalse(dialog.stamp_btn.isEnabled())
        dialog.text_edit.setText("HI")
        self.assertTrue(dialog.stamp_btn.isEnabled())
        dialog.text_edit.setText("")
        self.assertFalse(dialog.stamp_btn.isEnabled())

    def test_dialog_accept_sets_fields_and_mask(self):
        from pixel_forge.textstamp import render_text

        self._load_two_color()
        dialog = self._dialog(x=2, y=3)
        dialog.text_edit.setText("HI")
        self.assertEqual(dialog.x_spin.value(), 2)
        self.assertEqual(dialog.y_spin.value(), 3)
        dialog.stamp_btn.click()
        self.assertEqual(dialog.result(),
                         QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.text_value, "HI")
        self.assertIsNone(dialog.font_family)
        self.assertEqual(dialog.size, 1)
        self.assertEqual((dialog.x, dialog.y), (2, 3))
        self.assertEqual(dialog.color, self.black)
        self.assertEqual(dialog.mask, render_text("HI"))

    def test_dialog_info_names_the_stamp_size(self):
        self._load_two_color()
        dialog = self._dialog()
        dialog.text_edit.setText("HI")
        self.assertIn("Stamp is 11 x 7 pixels.",
                      dialog.info_label.text())
        self.assertIsNotNone(dialog.preview_label.pixmap())
        self.assertFalse(dialog.preview_label.pixmap().isNull())

    def test_dialog_outside_stamp_stays_open(self):
        self._load_two_color()
        dialog = self._dialog()
        dialog.text_edit.setText("I")
        dialog.x_spin.setValue(31)
        dialog.y_spin.setValue(31)
        self.assertIn("sits outside the sprite",
                      dialog.info_label.text())
        dialog.stamp_btn.click()
        self.assertNotEqual(dialog.result(),
                            QDialog.DialogCode.Accepted)

    def test_dialog_clipped_stamp_says_so(self):
        self._load_two_color()
        dialog = self._dialog()
        dialog.text_edit.setText("HI")
        dialog.x_spin.setValue(28)
        self.assertIn("cut off", dialog.info_label.text())
        dialog.stamp_btn.click()
        self.assertEqual(dialog.result(),
                         QDialog.DialogCode.Accepted)

    def test_dialog_names_unsupported_characters(self):
        self._load_two_color()
        dialog = self._dialog()
        dialog.text_edit.setText("café")
        self.assertIn("cannot draw: é", dialog.info_label.text())

    def test_dialog_scale_changes_the_stamp(self):
        self._load_two_color()
        dialog = self._dialog()
        dialog.text_edit.setText("HI")
        dialog.size_spin.setValue(2)
        self.assertIn("Stamp is 22 x 14 pixels.",
                      dialog.info_label.text())
        self.assertEqual((dialog.mask.width(),
                          dialog.mask.height()), (22, 14))

    def test_dialog_system_font_switches_size_control(self):
        from PySide6.QtGui import QFontDatabase

        families = QFontDatabase.families()
        if not families:
            self.skipTest("no system fonts installed")
        self._load_two_color()
        dialog = self._dialog()
        family = families[0]
        index = dialog.font_combo.findData(family)
        self.assertGreaterEqual(index, 0)
        dialog.font_combo.setCurrentIndex(index)
        self.assertEqual(dialog.size_label.text(), "Size")
        self.assertEqual(dialog.size_spin.minimum(), 6)
        self.assertEqual(dialog.size_spin.maximum(), 64)
        dialog.text_edit.setText("Hi")
        self.assertEqual(dialog.font_family, family)
        self.assertGreater(dialog.mask.width(), 0)
        self.assertGreater(dialog.mask.height(), 0)
        # Back to the pixel font restores the scale control.
        dialog.font_combo.setCurrentIndex(0)
        self.assertEqual(dialog.size_label.text(), "Scale")
        self.assertEqual(dialog.size_spin.minimum(), 1)
        self.assertEqual(dialog.size_spin.maximum(), 4)

    def test_dialog_controls_have_tooltips(self):
        from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox,
                                       QLineEdit, QPushButton)

        self._load_two_color()
        dialog = self._dialog()
        missing = []
        for cls in (QPushButton, QComboBox, QAbstractSpinBox,
                    QLineEdit):
            for widget in dialog.findChildren(cls):
                if isinstance(widget, QLineEdit) and isinstance(
                        widget.parent(), QAbstractSpinBox):
                    continue
                if not widget.toolTip().strip():
                    missing.append(widget.__class__.__name__)
        self.assertEqual(missing, [])

    def test_dialog_visible_strings_have_no_em_dash_or_s(self):
        from PySide6.QtWidgets import (QComboBox, QLabel, QLineEdit,
                                       QPushButton)

        self._load_two_color()
        dialog = self._dialog()
        dialog.text_edit.setText("HI")
        strings = [dialog.windowTitle()]
        for cls in (QPushButton, QLabel, QLineEdit, QComboBox):
            for widget in dialog.findChildren(cls):
                strings.append(widget.toolTip())
                if isinstance(widget, QPushButton):
                    strings.append(widget.text())
                if isinstance(widget, QLabel):
                    strings.append(widget.text())
                if isinstance(widget, QLineEdit):
                    strings.append(widget.placeholderText())
        for text in strings:
            self.assertNotIn("—", text, text)
            self.assertNotIn("(s)", text, text)

    def test_dialog_starts_in_the_picked_color(self):
        self._load_two_color()
        swatches = list(
            self.win._editor_swatch_by_color.values())
        swatches[0].click()
        self.app.processEvents()
        dialog = self._dialog(color=self.win._editor_color)
        self.assertEqual(dialog.color, self.win._editor_color)


class AnimationTimelineTests(unittest.TestCase):
    """Task 34: per-frame durations, the scrubber, onion skinning,
    animation tags, and GIF export — all on the Video tab."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        # 2 seconds at 24 fps, 8 fps extraction = 16 frames.
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(1)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _load(self):
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.assertEqual(len(self.win.video_frames), 16)

    def _show_frame(self, position):
        kept = self.win._kept_indices()
        self.win.video_current = kept[position]
        self.win._refresh_video_preview()
        self.app.processEvents()

    def _preview_pixels(self):
        from PySide6.QtGui import QImage
        pixmap = self.win.video_preview_label.pixmap()
        image = pixmap.toImage().convertToFormat(
            QImage.Format.Format_ARGB32)
        pixels = []
        for y in range(0, image.height(), 7):
            for x in range(0, image.width(), 7):
                pixels.append(image.pixel(x, y))
        return pixels

    def test_durations_default_from_sheet_fps(self):
        self._load()
        # Sheet fps is 8 by default: 1000 / 8 = 125 ms a frame.
        self.assertEqual(len(self.win.video_durations), 16)
        self.assertEqual(set(self.win.video_durations), {125})
        self.assertEqual(self.win.video_duration_spin.value(), 125)

    def test_duration_spin_changes_only_current_frame(self):
        self._load()
        self._show_frame(3)
        self.assertEqual(self.win.video_duration_spin.value(), 125)
        self.win.video_duration_spin.setValue(500)
        self.app.processEvents()
        self.assertEqual(self.win.video_durations[3], 500)
        others = [d for i, d in enumerate(self.win.video_durations)
                  if i != 3]
        self.assertEqual(set(others), {125})

    def test_durations_follow_reorder(self):
        from PySide6.QtCore import QModelIndex
        self._load()
        # Give every frame a distinct duration, then drag row 0
        # to position 2 through the model (the same rowsMoved
        # path a real drag takes).
        for i in range(16):
            self.win.video_durations[i] = 100 + i * 10
        model = self.win.video_strip.model()
        self.assertTrue(model.moveRows(
            QModelIndex(), 0, 1, QModelIndex(), 3))
        self.app.processEvents()
        self.assertEqual(self.win.video_order[2], 0)
        # The duration followed the frame: position 2 holds
        # original frame 0, whose duration is 100.
        self.assertEqual(self.win._kept_durations()[2], 100)
        self.assertEqual(self.win._kept_durations()[0], 110)

    def test_durations_survive_keep_drop(self):
        self._load()
        self.win.video_durations[0] = 500
        self.win.video_durations[1] = 250
        self.win.video_strip.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertEqual(self.win._kept_durations()[0], 250)
        # The dropped frame's duration is still stored — keeping
        # it again brings the 500 ms back.
        self.win.video_strip.item(0).setCheckState(Qt.CheckState.Checked)
        self.app.processEvents()
        self.assertEqual(self.win._kept_durations()[0], 500)

    def test_playback_interval_follows_duration(self):
        self._load()
        self.win.video_durations[0] = 500
        self.win.video_durations[1] = 100
        self._show_frame(0)
        self.win._on_play_pause()
        self.assertTrue(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_timer.interval(), 500)
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_current, 1)
        self.assertEqual(self.win.video_timer.interval(), 100)
        self.win._stop_playback()

    def test_playback_follows_strip_order_after_reorder(self):
        # Found while building task 34: the old advance compared
        # frame NUMBERS, so after a reorder playback played the
        # original order even though the strip, the export, and
        # the sheet all followed the strip.
        from PySide6.QtCore import QModelIndex
        self._load()
        model = self.win.video_strip.model()
        self.assertTrue(model.moveRows(
            QModelIndex(), 0, 1, QModelIndex(), 3))
        self.app.processEvents()
        # Strip order is now [1, 2, 0, 3, ...]. From position 2
        # (original frame 0), the next frame in strip order is
        # original frame 3 — the old code would have gone to
        # frame 1, the next HIGHER number.
        self._show_frame(2)
        self.assertEqual(self.win.video_current, 0)
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_current, 3)

    def test_scrubber_jumps_and_pauses(self):
        self._load()
        self.win._on_play_pause()
        self.assertTrue(self.win.video_timer.isActive())
        self.win.video_scrub_slider.setValue(5)
        self.app.processEvents()
        self.assertFalse(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_current, 5)
        # And the scrubber tracks playback: start again, advance
        # once, the slider follows.
        self.win._on_play_pause()
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_scrub_slider.value(), 6)
        self.win._stop_playback()

    def test_onion_toggle_changes_preview(self):
        self._load()
        self._show_frame(1)
        off = self._preview_pixels()
        self.win.video_onion_check.setChecked(True)
        self.app.processEvents()
        on = self._preview_pixels()
        self.assertNotEqual(off, on)
        # The first frame has no previous frame — the onion
        # toggle changes nothing there.
        self._show_frame(0)
        first_on = self._preview_pixels()
        self.win.video_onion_check.setChecked(False)
        self.app.processEvents()
        first_off = self._preview_pixels()
        self.assertEqual(first_on, first_off)

    def test_no_tag_preselected(self):
        self._load()
        self.assertEqual(self.win.video_tag_list.currentRow(), -1)
        self.assertFalse(self.win.video_tag_play_btn.isEnabled())
        self.assertFalse(self.win.video_tag_delete_btn.isEnabled())

    def test_tag_add_play_delete(self):
        self._load()
        self.assertTrue(self.win._add_video_tag("walk", 0, 3))
        self.assertEqual(self.win.video_tag_list.count(), 1)
        self.assertEqual(self.win.video_tag_list.item(0).text(),
                         "walk — frames 1–4")
        self.win.video_tag_list.setCurrentRow(0)
        self.win._on_video_tag_play()
        self.assertIsNotNone(self.win.video_play_tag)
        self.assertEqual(self.win.video_current, 0)
        self.assertTrue(self.win.video_timer.isActive())
        # Playback stays inside the tag and wraps within it
        # when Loop is on.
        self.win.video_loop_check.setChecked(True)
        for expected in (1, 2, 3, 0, 1):
            self.win._advance_video_frame()
            self.assertEqual(self.win.video_current, expected)
        self.win._stop_playback()
        # Main Play clears the tag: playback is all frames again.
        self.win._on_play_pause()
        self.assertIsNone(self.win.video_play_tag)
        self.win._stop_playback()
        self.win.video_tag_list.setCurrentRow(0)
        self.win._on_video_tag_delete()
        self.assertEqual(self.win.video_tag_list.count(), 0)
        self.assertEqual(self.win.video_tags, [])

    def test_duplicate_tag_name_refused(self):
        from unittest import mock
        self._load()
        self.assertTrue(self.win._add_video_tag("walk", 0, 3))
        with mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.assertFalse(self.win._add_video_tag("walk", 4, 7))
        self.assertEqual(self.win.video_tag_list.count(), 1)
        warned.assert_called_once()
        self.assertEqual(warned.call_args[0][1], "Could not add tag")

    def test_tag_clamped_on_keep_drop(self):
        self._load()
        self.assertTrue(self.win._add_video_tag("walk", 0, 15))
        self.assertTrue(self.win._add_video_tag("idle", 12, 15))
        for row in (12, 13, 14, 15):
            self.win.video_strip.item(row).setCheckState(
                Qt.CheckState.Unchecked)
        self.app.processEvents()
        # "walk" shortened to the frames left; "idle" had no
        # frames left in its range, so it is gone.
        self.assertEqual(self.win.video_tags,
                         [{"name": "walk", "from": 0, "to": 11}])
        self.assertIn("idle", self.win.statusBar().currentMessage())

    def _gif_frame_times(self, gif):
        import subprocess
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_frames",
             "-of", "csv=p=0", gif],
            capture_output=True, text=True, timeout=60)
        times = []
        for line in proc.stdout.splitlines():
            fields = line.split(",")
            times.append((float(fields[4]), float(fields[10])))
        return times

    def test_gif_export_end_to_end(self):
        self._load()
        gif = os.path.join(self.tmp, "anim.gif")
        self.assertTrue(self.win.export_gif(gif))
        self.assertTrue(os.path.isfile(gif))
        times = self._gif_frame_times(gif)
        # 16 frames + the repeated last frame.
        self.assertEqual(len(times), 17)
        total = times[-1][0] + times[-1][1]
        self.assertAlmostEqual(total, 2.0, delta=0.1)
        self.assertIn("Exported GIF", self.win.statusBar().currentMessage())

    def test_gif_export_one_tag(self):
        self._load()
        self.assertTrue(self.win._add_video_tag("walk", 0, 3))
        gif = os.path.join(self.tmp, "walk.gif")
        self.assertTrue(self.win.export_gif(
            gif, self.win.video_tags[0]))
        times = self._gif_frame_times(gif)
        self.assertEqual(len(times), 5)
        total = times[-1][0] + times[-1][1]
        self.assertAlmostEqual(total, 0.5, delta=0.1)

    def test_sheet_export_carries_durations_and_tags(self):
        self._load()
        self.win.video_durations[0] = 500
        self.win.video_durations[1] = 250
        self.assertTrue(self.win._add_video_tag("walk", 0, 3))
        png = os.path.join(self.tmp, "timed-sheet.png")
        self.assertTrue(self.win.export_spritesheet(png))
        meta_path = os.path.join(self.tmp, "timed-sheet.json")
        with open(meta_path) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["frames"][0]["duration_ms"], 500)
        self.assertEqual(meta["frames"][1]["duration_ms"], 250)
        self.assertEqual(meta["frames"][2]["duration_ms"], 125)
        self.assertEqual(meta["tags"],
                         [{"name": "walk", "from": 0, "to": 3}])

    def test_preview_fps_change_resets_durations(self):
        self._load()
        self.win.video_durations[0] = 500
        self.win.video_durations[5] = 900
        self.win.video_play_fps_spin.setValue(4)
        self.app.processEvents()
        # 1000 / 4 = 250 ms for every frame.
        self.assertEqual(set(self.win.video_durations), {250})

    def test_sheet_fps_change_resets_durations(self):
        # The Sheet fps is the sheet's base speed: changing it
        # resets durations the same way, or the metadata would
        # say fps 12 while every duration_ms still said 125.
        self._load()
        self.win.video_durations[0] = 500
        self.win.video_durations[5] = 900
        self.win.sheet_fps_spin.setValue(12)
        self.app.processEvents()
        # 1000 / 12 = 83 ms for every frame.
        self.assertEqual(set(self.win.video_durations), {83})

    def test_preview_dialog_plays_durations(self):
        from pixel_forge.gui import SpritesheetDialog
        frames = [make_solid_image(32, 32, 255, 0, 0),
                  make_solid_image(32, 32, 0, 255, 0),
                  make_solid_image(32, 32, 0, 0, 255)]
        names = ["a.png", "b.png", "c.png"]
        dialog = SpritesheetDialog(
            self.win, frames, names, 3, 0, 8, "sheet.png",
            durations=[125, 500, 250],
            tags=[{"name": "walk", "from": 0, "to": 2}])
        try:
            self.assertEqual(
                [f["duration_ms"]
                 for f in dialog.metadata["frames"]],
                [125, 500, 250])
            self.assertEqual(dialog.metadata["tags"],
                             [{"name": "walk", "from": 0, "to": 2}])
            dialog._on_play_pause()
            self.assertEqual(dialog.timer.interval(), 125)
            dialog._advance()
            self.assertEqual(dialog.timer.interval(), 500)
        finally:
            dialog.close()


class LayerGuiTests(unittest.TestCase):
    """Task 35: the layer stack on the Image tab — add, delete,
    reorder, rename, visibility, and the editor painting on the
    active layer. The test image is the same 32 x 32 red/blue
    split as the editor tests, so the base layer has exactly
    two colors."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge.image import Image, argb
        source = Image(32, 32)
        for y in range(32):
            for x in range(32):
                source.setPixel(x, y, argb(255, 0, 0) if x < 16
                                else argb(0, 0, 255))
        cls.two_color = write_image(
            source, os.path.join(cls.tmp, "two.png"))
        cls.solid = write_image(
            make_solid_image(32, 32, 0, 255, 0),
            os.path.join(cls.tmp, "solid.png"))
        from pixel_forge.image import argb as _argb
        cls.red = _argb(255, 0, 0)
        cls.blue = _argb(0, 0, 255)
        cls.green = _argb(0, 255, 0)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir,
                              restore=False)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _load_two_color(self):
        self.assertTrue(self.win.load_image(self.two_color))
        self.app.processEvents()
        self.assertEqual(self.win.result_image.width(), 32)
        self.assertEqual(self.win.result_image.height(), 32)

    def _confirm(self, answer):
        """Patch the Yes/No confirm to answer Yes or No."""
        from unittest import mock
        return mock.patch.object(
            QMessageBox, "question", return_value=answer)

    def _pick_tool(self, tool):
        self.win.editor_tool_buttons[tool].click()
        self.app.processEvents()

    def _pick_color(self, index=0):
        swatches = list(self.win._editor_swatch_by_color.values())
        self.assertGreater(len(swatches), index)
        swatches[index].click()
        self.app.processEvents()

    def _pos(self, x, y):
        return self.win.after_label.pos_for_pixel(x, y)

    def _click(self, x, y):
        from PySide6.QtTest import QTest
        QTest.mouseClick(self.win.after_label,
                         Qt.MouseButton.LeftButton,
                         pos=self._pos(x, y))
        self.app.processEvents()

    def _pixel(self, x, y):
        return self.win.result_image.pixel(x, y)

    def _layer_names(self):
        return [self.win.layer_list.item(row).text()
                for row in range(self.win.layer_list.count())]

    def _select_layer(self, row):
        # A real click moves the row AND fires itemClicked;
        # setCurrentRow alone does only the first. This build's
        # itemClicked takes the item only.
        item = self.win.layer_list.item(row)
        self.win.layer_list.setCurrentRow(row)
        self.win.layer_list.itemClicked.emit(item)
        self.app.processEvents()

    def _add_layer(self):
        self.win.layer_add_btn.click()
        self.app.processEvents()

    def test_layer_controls_disabled_before_load(self):
        self.assertFalse(self.win.layer_list.isEnabled())
        self.assertFalse(self.win.layer_add_btn.isEnabled())
        self.assertFalse(self.win.layer_delete_btn.isEnabled())
        self.assertFalse(self.win.layer_up_btn.isEnabled())
        self.assertFalse(self.win.layer_down_btn.isEnabled())
        self._load_two_color()
        self.assertTrue(self.win.layer_list.isEnabled())
        self.assertTrue(self.win.layer_add_btn.isEnabled())

    def test_load_builds_one_base_layer(self):
        self._load_two_color()
        self.assertEqual(len(self.win._layers), 1)
        base = self.win._layers[0]
        self.assertEqual(base.name, "Background")
        self.assertTrue(base.visible)
        self.assertIs(self.win._active_layer, base)
        self.assertEqual(self._layer_names(), ["Background"])
        # One layer: nothing to delete or move.
        self.assertFalse(self.win.layer_delete_btn.isEnabled())
        self.assertFalse(self.win.layer_up_btn.isEnabled())
        self.assertFalse(self.win.layer_down_btn.isEnabled())
        self.assertFalse(self.win._result_edited)

    def test_add_layer(self):
        self._load_two_color()
        self._add_layer()
        self.assertEqual(len(self.win._layers), 2)
        top = self.win._layers[1]
        self.assertEqual(top.name, "Layer 2")
        self.assertTrue(top.visible)
        self.assertIs(self.win._active_layer, top)
        self.assertEqual(self._layer_names(),
                         ["Layer 2", "Background"])
        # A transparent layer changes nothing visually.
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertEqual(self._pixel(20, 20), self.blue)
        # But the stack carries an edit now.
        self.assertTrue(self.win._result_edited)
        self.assertIn("(edited)", self.win.info_label.text())
        self.assertTrue(self.win.layer_delete_btn.isEnabled())
        self.assertFalse(self.win.layer_up_btn.isEnabled())
        self.assertTrue(self.win.layer_down_btn.isEnabled())
        self.assertEqual(len(self.win._undo_stack), 1)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Add layer")

    def test_draw_on_top_layer_composites_over_base(self):
        self._load_two_color()
        self._add_layer()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        # The composite shows the top layer's blue over the
        # base's red; the base layer itself is untouched.
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.assertEqual(self._pixel(3, 4), self.red)
        self.assertEqual(
            self.win._layers[0].image.pixel(3, 3), self.red)
        self.assertEqual(
            self.win._layers[1].image.pixel(3, 3), self.blue)
        self.assertEqual(self.win._undo_action.text(), "Undo Draw")

    def test_hide_top_layer_shows_base_alone(self):
        self._load_two_color()
        self._add_layer()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        self.assertEqual(self._pixel(3, 3), self.blue)
        item = self.win.layer_list.item(0)  # top layer
        item.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertFalse(self.win._layers[1].visible)
        self.assertEqual(self._pixel(3, 3), self.red)
        item.setCheckState(Qt.CheckState.Checked)
        self.app.processEvents()
        self.assertTrue(self.win._layers[1].visible)
        self.assertEqual(self._pixel(3, 3), self.blue)

    def test_rename_layer(self):
        self._load_two_color()
        item = self.win.layer_list.item(0)
        item.setText("Sky")
        self.app.processEvents()
        self.assertEqual(self.win._layers[0].name, "Sky")
        # A blank name puts the old name back.
        item.setText("   ")
        self.app.processEvents()
        self.assertEqual(item.text(), "Sky")
        self.assertEqual(self.win._layers[0].name, "Sky")

    def test_delete_empty_layer_asks_nothing(self):
        from unittest import mock
        self._load_two_color()
        self._add_layer()
        with mock.patch.object(
                QMessageBox, "question",
                side_effect=AssertionError("asked")):
            self.win.layer_delete_btn.click()
            self.app.processEvents()
        self.assertEqual(len(self.win._layers), 1)
        self.assertIs(self.win._active_layer,
                      self.win._layers[0])
        self.assertFalse(self.win._result_edited)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Delete layer")

    def test_delete_with_content_asks_and_no_keeps_layer(self):
        self._load_two_color()
        self._add_layer()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        with self._confirm(QMessageBox.StandardButton.No) as ask:
            self.win.layer_delete_btn.click()
            self.app.processEvents()
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(len(self.win._layers), 2)
        self.assertEqual(self._pixel(3, 3), self.blue)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win.layer_delete_btn.click()
            self.app.processEvents()
        self.assertEqual(len(self.win._layers), 1)
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Delete layer")

    def test_delete_bottom_layer_activates_layer_above(self):
        self._load_two_color()
        self._add_layer()
        self._add_layer()
        self.assertEqual(self._layer_names(),
                         ["Layer 3", "Layer 2", "Background"])
        self._select_layer(2)  # the base
        self.assertIs(self.win._active_layer,
                      self.win._layers[0])
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win.layer_delete_btn.click()
            self.app.processEvents()
        self.assertEqual(len(self.win._layers), 2)
        self.assertEqual(self.win._active_layer.name, "Layer 2")
        self.assertEqual(self._layer_names(),
                         ["Layer 3", "Layer 2"])

    def test_move_layer_up_and_down(self):
        self._load_two_color()
        self._add_layer()
        self._add_layer()
        self._select_layer(1)  # Layer 2, the middle layer
        self.assertTrue(self.win.layer_up_btn.isEnabled())
        self.assertTrue(self.win.layer_down_btn.isEnabled())
        self.win.layer_up_btn.click()
        self.app.processEvents()
        self.assertEqual(self._layer_names(),
                         ["Layer 2", "Layer 3", "Background"])
        self.assertEqual(self.win._undo_action.text(),
                         "Undo Move layer")
        self.assertFalse(self.win.layer_up_btn.isEnabled())
        self.assertTrue(self.win.layer_down_btn.isEnabled())
        self.win.layer_down_btn.click()
        self.app.processEvents()
        self.assertEqual(self._layer_names(),
                         ["Layer 3", "Layer 2", "Background"])
        self.win.layer_down_btn.click()
        self.app.processEvents()
        self.assertEqual(self._layer_names(),
                         ["Layer 3", "Background", "Layer 2"])
        self.assertFalse(self.win.layer_down_btn.isEnabled())
        self.assertTrue(self.win.layer_up_btn.isEnabled())

    def test_stack_undo_and_redo(self):
        self._load_two_color()
        self._add_layer()
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(len(self.win._layers), 1)
        self.assertFalse(self.win._result_edited)
        self.assertEqual(self.win._redo_action.text(),
                         "Redo Add layer")
        self.win._on_redo()
        self.app.processEvents()
        self.assertEqual(len(self.win._layers), 2)
        self.assertTrue(self.win._result_edited)
        # Delete (empty, no ask), then undo brings it back.
        self.win.layer_delete_btn.click()
        self.app.processEvents()
        self.assertEqual(len(self.win._layers), 1)
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(len(self.win._layers), 2)
        self.assertEqual(self.win._active_layer.name, "Layer 2")
        # Move down, then undo restores the order.
        self.win.layer_down_btn.click()
        self.app.processEvents()
        self.assertEqual(self._layer_names(),
                         ["Background", "Layer 2"])
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(self._layer_names(),
                         ["Layer 2", "Background"])
        self.assertEqual(self.win._redo_action.text(),
                         "Redo Move layer")

    def test_visibility_and_rename_are_not_undoable(self):
        self._load_two_color()
        self._add_layer()
        self.assertEqual(len(self.win._undo_stack), 1)
        item = self.win.layer_list.item(0)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertEqual(len(self.win._undo_stack), 1)
        item.setText("Sky")
        self.app.processEvents()
        self.assertEqual(len(self.win._undo_stack), 1)

    def test_repixelize_guard_asks_with_two_layers(self):
        self._load_two_color()
        self._add_layer()
        with self._confirm(QMessageBox.StandardButton.No) as ask:
            self.win.colors_spin.setValue(8)
            self.app.processEvents()
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.assertEqual(len(self.win._layers), 2)
        self.assertTrue(self.win._result_edited)
        self.assertEqual(len(self.win._undo_stack), 1)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.win.colors_spin.setValue(8)
            self.app.processEvents()
        self.assertEqual(self.win.colors_spin.value(), 8)
        self.assertEqual(len(self.win._layers), 1)
        self.assertEqual(self.win._layers[0].name, "Background")
        self.assertFalse(self.win._result_edited)
        self.assertNotIn("(edited)", self.win.info_label.text())

    def test_load_guard_keeps_stack_on_no(self):
        self._load_two_color()
        self._add_layer()
        with self._confirm(QMessageBox.StandardButton.No):
            self.assertFalse(self.win.load_image(self.solid))
        self.assertEqual(self.win.source_path, self.two_color)
        self.assertEqual(len(self.win._layers), 2)
        with self._confirm(QMessageBox.StandardButton.Yes):
            self.assertTrue(self.win.load_image(self.solid))
        self.assertEqual(self.win.source_path, self.solid)
        self.assertEqual(len(self.win._layers), 1)
        self.assertEqual(self._pixel(3, 3), self.green)
        self.assertFalse(self.win._result_edited)

    def test_export_writes_composited_pixels(self):
        self._load_two_color()
        self._add_layer()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        from pixel_forge.pixelize import load_image as core_load
        out = os.path.join(self.tmp, "layered.png")
        self.assertTrue(self.win.export_image(out))
        back = core_load(out)
        self.assertEqual(back.pixel(3, 3), self.blue)
        self.assertEqual(back.pixel(3, 4), self.red)
        self.assertEqual(back.pixel(20, 20), self.blue)

    def test_fill_fills_active_layer_only(self):
        self._load_two_color()
        self._add_layer()
        self._pick_tool("fill")
        self._pick_color(1)  # blue
        self._click(3, 3)
        # The top layer was fully transparent, so the fill
        # covers it whole; the composite is all blue, but the
        # base layer still holds its red and blue.
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.assertEqual(self._pixel(20, 20), self.blue)
        self.assertEqual(
            self.win._layers[0].image.pixel(3, 3), self.red)
        self.assertEqual(
            self.win._layers[0].image.pixel(20, 20), self.blue)
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertEqual(self._pixel(20, 20), self.blue)

    def test_eyedropper_picks_from_composite(self):
        self._load_two_color()
        self._add_layer()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        self._pick_tool("eyedropper")
        self._click(3, 3)
        self.assertEqual(self.win._editor_color, self.blue)
        item = self.win.layer_list.item(0)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self._click(3, 3)
        self.assertEqual(self.win._editor_color, self.red)

    def test_edits_on_two_layers_undo_in_order(self):
        self._load_two_color()
        self._pick_tool("pencil")
        self._pick_color(1)  # blue
        self._click(3, 3)
        self.assertEqual(self._pixel(3, 3), self.blue)
        self._add_layer()
        self._pick_color(0)  # red
        self._click(20, 20)
        self.assertEqual(self._pixel(20, 20), self.red)
        # Undo the top-layer edit: the base edit survives.
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(self._pixel(20, 20), self.blue)
        self.assertEqual(self._pixel(3, 3), self.blue)
        # Undo the add, then the base edit.
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(len(self.win._layers), 1)
        self.assertEqual(self._pixel(3, 3), self.blue)
        self.win._on_undo()
        self.app.processEvents()
        self.assertEqual(self._pixel(3, 3), self.red)
        self.assertFalse(self.win._result_edited)


class EngineExportGuiTests(unittest.TestCase):
    """Engine-ready export dialog (task 36): nothing picked by
    default, Export disabled until a preset is chosen, the note
    naming what each preset writes and drops, and real exports
    read back from disk. The dialog tests build the dialog
    directly with synthetic frames — no video load needed; only
    the button test loads a clip."""
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(1)
        self.out = tempfile.mkdtemp(dir=self.tmp)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _dialog(self, count=3):
        from pixel_forge.gui import EngineExportDialog
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        images = [make_solid_image(32, 32, *colors[i])
                  for i in range(count)]
        names = ["frame_%03d.png" % (i + 1) for i in range(count)]
        dialog = EngineExportDialog(
            self.win, images, names, [500, 100, 100][:count],
            [{"name": "walk", "from": 0, "to": count - 1}],
            cols=2, padding=0, fps=8, folder=self.out,
            stem="clip-sheet")
        return dialog

    def _pick(self, dialog, key):
        combo = dialog.engine_format_combo
        combo.setCurrentIndex(combo.findData(key))
        self.app.processEvents()

    def test_nothing_picked_by_default(self):
        dialog = self._dialog()
        self.assertEqual(dialog.engine_format_combo.currentIndex(),
                         -1)
        self.assertIsNone(dialog.format_key())
        self.assertFalse(dialog.engine_export_btn.isEnabled())
        self.assertIn("Pick a preset",
                      dialog.engine_note_label.text())

    def test_pick_enables_export_and_shows_the_note(self):
        dialog = self._dialog()
        self._pick(dialog, "aseprite")
        self.assertTrue(dialog.engine_export_btn.isEnabled())
        note = dialog.engine_note_label.text()
        self.assertIn("clip-sheet.png + clip-sheet.json", note)
        self.assertIn("carried", note)

    def test_texturepacker_note_names_the_drop(self):
        dialog = self._dialog()
        self._pick(dialog, "texturepacker")
        note = dialog.engine_note_label.text()
        self.assertIn("dropped", note)
        self.assertIn("durations", note)
        self._pick(dialog, "gamemaker")
        note = dialog.engine_note_label.text()
        self.assertIn("clip-sheet_strip3.png", note)
        self.assertIn("No JSON", note)

    def test_long_note_is_not_clipped(self):
        # The Godot note is the longest — several wrapped lines.
        # A word-wrapped label does not push its dialog taller
        # on its own, so the text used to overflow the label
        # and clip (caught on the v0.46 screenshot). The dialog
        # must grow until the label is at least as tall as the
        # text needs at its width.
        dialog = self._dialog()
        dialog.show()
        self.app.processEvents()
        self._pick(dialog, "godot")
        self.app.processEvents()
        label = dialog.engine_note_label
        needed = label.heightForWidth(label.width())
        self.assertGreaterEqual(label.height(), needed,
                                "label %d px tall, text needs %d"
                                % (label.height(), needed))

    def test_export_aseprite_json_writes_files(self):
        dialog = self._dialog()
        self._pick(dialog, "aseprite")
        dialog.engine_export_btn.click()
        self.app.processEvents()
        self.assertEqual(dialog.result(), 1)  # accepted
        png = os.path.join(self.out, "clip-sheet.png")
        meta = os.path.join(self.out, "clip-sheet.json")
        self.assertEqual([str(p) for p in dialog.written],
                         [png, meta])
        self.assertTrue(os.path.isfile(png))
        with open(meta) as handle:
            data = json.load(handle)
        self.assertEqual(data["meta"]["image"], "clip-sheet.png")
        self.assertEqual(data["frames"]["frame_001.png"]["duration"],
                         500)
        self.assertEqual(data["meta"]["frameTags"], [
            {"name": "walk", "from": 0, "to": 2,
             "direction": "forward"}])

    def test_export_gamemaker_writes_the_strip(self):
        dialog = self._dialog()
        self._pick(dialog, "gamemaker")
        dialog.engine_export_btn.click()
        self.app.processEvents()
        strip = os.path.join(self.out, "clip-sheet_strip3.png")
        self.assertEqual([str(p) for p in dialog.written], [strip])
        self.assertTrue(os.path.isfile(strip))
        from pixel_forge.pixelize import load_image
        image = load_image(strip)
        self.assertEqual((image.width(), image.height()), (96, 32))

    def test_export_unity_writes_aseprite_and_png(self):
        dialog = self._dialog()
        self._pick(dialog, "unity")
        dialog.engine_export_btn.click()
        self.app.processEvents()
        ase = os.path.join(self.out, "clip-sheet.aseprite")
        png = os.path.join(self.out, "clip-sheet.png")
        self.assertEqual([str(p) for p in dialog.written],
                         [ase, png])
        with open(ase, "rb") as handle:
            magic = handle.read(6)
        self.assertEqual(magic[4:], b"\xe0\xa5")  # 0xA5E0

    def test_no_bare_ampersand_in_dialog(self):
        dialog = self._dialog()
        for text in visible_texts(dialog):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

    def test_dialog_controls_have_tooltips(self):
        from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton
        dialog = self._dialog()
        missing = []
        for cls in (QPushButton, QComboBox, QLineEdit):
            for widget in dialog.findChildren(cls):
                if not widget.toolTip().strip():
                    missing.append(widget.__class__.__name__)
        self.assertEqual(missing, [])

    def test_engine_button_enables_with_frames(self):
        self.assertFalse(self.win.engine_export_btn.isEnabled())
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self._wait_for_video())
        self.app.processEvents()
        self.assertTrue(self.win.engine_export_btn.isEnabled())


class TilesetGuiTests(unittest.TestCase):
    """Task 37: the Tileset tab — mode picker, base tiles,
    Generate, exports, and Save to library."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.tile_a = write_image(make_solid_image(16, 16, 255, 0, 0),
                                 os.path.join(cls.tmp, "grass.png"))
        cls.tile_b = write_image(make_solid_image(16, 16, 0, 0, 255),
                                 os.path.join(cls.tmp, "water.png"))
        cls.tile_big = write_image(
            make_solid_image(32, 32, 0, 255, 0),
            os.path.join(cls.tmp, "big.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(5)
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _pick_mode(self, mode):
        combo = self.win.tileset_mode_combo
        combo.setCurrentIndex(combo.findData(mode))
        self.app.processEvents()

    def _generate_wang(self):
        self._pick_mode("wang")
        self.assertTrue(self.win.load_tileset_base(self.tile_a, "a"))
        self.assertTrue(self.win.load_tileset_base(self.tile_b, "b"))
        self.win.tileset_generate_btn.click()
        self.app.processEvents()

    def _generate_blob(self):
        self._pick_mode("blob")
        self.assertTrue(self.win.load_tileset_base(self.tile_a, "a"))
        self.win.tileset_generate_btn.click()
        self.app.processEvents()

    def test_tileset_tab_exists_with_controls(self):
        tabs = [self.win.tabs.tabText(i)
                for i in range(self.win.tabs.count())]
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code",
                                "Library", "Tileset"])
        self.assertEqual(self.win.tileset_generate_btn.text(),
                         "Generate tileset")
        self.assertEqual(self.win.tileset_export_png_btn.text(),
                         "Export PNG")
        self.assertEqual(self.win.tileset_export_tiled_btn.text(),
                         "Export for Tiled")
        self.assertEqual(self.win.tileset_export_godot_btn.text(),
                         "Export for Godot")
        self.assertEqual(self.win.tileset_save_library_btn.text(),
                         "Save to library")

    def test_generate_disabled_with_no_mode_picked(self):
        # Nothing selected by default — the mode combo starts
        # unpicked, so nothing can generate or export yet.
        self.assertEqual(self.win.tileset_mode_combo.currentIndex(),
                         -1)
        self.assertIsNone(self.win.tileset_mode)
        self.assertFalse(self.win.tileset_generate_btn.isEnabled())
        self.assertFalse(self.win.tileset_export_png_btn.isEnabled())
        self.assertFalse(
            self.win.tileset_export_tiled_btn.isEnabled())
        self.assertFalse(
            self.win.tileset_export_godot_btn.isEnabled())
        self.assertFalse(self.win.tileset_save_library_btn.isEnabled())

    def test_mode_pick_shows_second_picker(self):
        # Blob mode: one base tile, the second picker hidden.
        self._pick_mode("blob")
        self.assertTrue(self.win.tileset_b_section.isHidden())
        self.assertEqual(self.win.tileset_pick_a_btn.text(),
                         "Choose base tile")
        # Wang mode: the second picker shows and the first one
        # renames itself terrain A.
        self._pick_mode("wang")
        self.assertFalse(self.win.tileset_b_section.isHidden())
        self.assertEqual(self.win.tileset_pick_a_btn.text(),
                         "Choose terrain A")

    def test_generate_disabled_until_both_wang_tiles(self):
        self._pick_mode("wang")
        self.assertTrue(self.win.load_tileset_base(self.tile_a, "a"))
        self.assertFalse(self.win.tileset_generate_btn.isEnabled())
        self.assertTrue(self.win.load_tileset_base(self.tile_b, "b"))
        self.assertTrue(self.win.tileset_generate_btn.isEnabled())

    def test_wang_from_two_solid_tiles(self):
        from pixel_forge.image import argb
        self._generate_wang()
        self.assertEqual(len(self.win.tileset_tiles), 16)
        self.assertEqual(self.win.tileset_count_label.text(),
                         "16 tiles, 16 x 16 each")
        sheet = self.win.tileset_sheet
        self.assertEqual((sheet.width(), sheet.height()), (64, 64))
        # Tile 0 is all terrain A, tile 15 all terrain B.
        tile0 = self.win.tileset_tiles[0]
        tile15 = self.win.tileset_tiles[15]
        self.assertEqual(tile0.pixel(8, 8), argb(255, 0, 0))
        self.assertEqual(tile15.pixel(8, 8), argb(0, 0, 255))
        # Tile 1 (north edge B): the top edge is blue, the
        # bottom edge red — read off the middle of each edge,
        # away from the diagonal corners.
        tile1 = self.win.tileset_tiles[1]
        self.assertEqual(tile1.pixel(8, 0), argb(0, 0, 255))
        self.assertEqual(tile1.pixel(8, 15), argb(255, 0, 0))
        self.assertEqual(tile1.pixel(0, 8), argb(255, 0, 0))
        self.assertEqual(tile1.pixel(15, 8), argb(255, 0, 0))
        # Exports and Save to library enable once a set exists.
        self.assertTrue(self.win.tileset_export_png_btn.isEnabled())
        self.assertTrue(
            self.win.tileset_export_tiled_btn.isEnabled())
        self.assertTrue(
            self.win.tileset_export_godot_btn.isEnabled())
        self.assertTrue(self.win.tileset_save_library_btn.isEnabled())

    def test_blob_from_one_tile(self):
        self._generate_blob()
        self.assertEqual(len(self.win.tileset_tiles), 47)
        self.assertEqual(self.win.tileset_count_label.text(),
                         "47 tiles, 16 x 16 each")
        sheet = self.win.tileset_sheet
        self.assertEqual((sheet.width(), sheet.height()),
                         (112, 112))
        # Mask 0 (index 0) is fully transparent; mask 255
        # (index 46) is the base tile unchanged.
        tile0 = self.win.tileset_tiles[0]
        self.assertEqual(tile0.pixel(8, 8), 0)
        tile46 = self.win.tileset_tiles[46]
        self.assertEqual(tile46.pixel(8, 8),
                         self.win.tileset_base_a.pixel(8, 8))

    def test_new_base_clears_generated_set(self):
        self._generate_blob()
        self.assertIsNotNone(self.win.tileset_tiles)
        self.assertTrue(self.win.load_tileset_base(self.tile_b, "a"))
        self.assertIsNone(self.win.tileset_tiles)
        self.assertIsNone(self.win.tileset_sheet)
        self.assertEqual(self.win.tileset_count_label.text(),
                         "No tileset yet")
        self.assertFalse(self.win.tileset_export_png_btn.isEnabled())

    def test_mode_change_clears_generated_set(self):
        self._generate_blob()
        self.assertIsNotNone(self.win.tileset_tiles)
        self._pick_mode("wang")
        self.assertIsNone(self.win.tileset_tiles)
        self.assertFalse(self.win.tileset_generate_btn.isEnabled())

    def test_mismatched_wang_bases_warn(self):
        from unittest import mock
        self._pick_mode("wang")
        self.assertTrue(self.win.load_tileset_base(self.tile_a, "a"))
        self.assertTrue(self.win.load_tileset_base(self.tile_big, "b"))
        with mock.patch("pixel_forge.gui.warn_dialog") as warned:
            self.win.tileset_generate_btn.click()
            self.app.processEvents()
        self.assertTrue(warned.called)
        self.assertIsNone(self.win.tileset_tiles)

    def test_export_png_writes_file(self):
        from pixel_forge.pixelize import load_image
        self._generate_blob()
        path = os.path.join(self.tmp, "blob-tileset.png")
        self.assertTrue(self.win.export_tileset_png(path))
        self.assertTrue(os.path.isfile(path))
        sheet = load_image(path)
        self.assertEqual((sheet.width(), sheet.height()),
                         (112, 112))

    def test_save_tileset_to_library(self):
        from pixel_forge.pixelize import PixelForgeError
        with self.assertRaises(PixelForgeError):
            self.win.save_tileset_to_library("Nothing yet", [])
        self._generate_wang()
        entry = self.win.save_tileset_to_library(
            "Wang grass", ["tileset"])
        self.assertEqual(entry["name"], "Wang grass")
        self.assertEqual(self.win.library.entries()[0]["name"],
                         "Wang grass")

    def test_no_bare_ampersand_in_tileset_tab(self):
        for text in visible_texts(self.win.tileset_tab):
            self.assertNotIn("&", text,
                             "bare & in visible text: %r" % text)

    def test_tileset_controls_have_tooltips(self):
        from PySide6.QtWidgets import QComboBox, QPushButton, QSlider
        missing = []
        for cls in (QPushButton, QComboBox, QSlider):
            for widget in self.win.tileset_tab.findChildren(cls):
                if not widget.toolTip().strip():
                    missing.append(widget.__class__.__name__)
        self.assertEqual(missing, [])

    # -- blob shape (task 82) ------------------------------------

    def _pick_shape(self, shape):
        combo = self.win.tileset_shape_combo
        combo.setCurrentIndex(combo.findData(shape))
        self.app.processEvents()

    def test_blob_shape_starts_unpicked_and_hidden(self):
        # Nothing is picked until the user picks one, and the
        # section only shows in blob mode.
        self.assertTrue(self.win.tileset_shape_section.isHidden())
        self.assertEqual(self.win.tileset_shape_combo.currentIndex(),
                         -1)
        self.assertIsNone(self.win.tileset_shape)
        self.assertEqual(self.win._tileset_shape(), "square")
        combo = self.win.tileset_shape_combo
        self.assertEqual([combo.itemText(i)
                          for i in range(combo.count())],
                         ["Square", "Rounded"])
        self.assertEqual([combo.itemData(i)
                          for i in range(combo.count())],
                         ["square", "rounded"])
        self.assertTrue(combo.toolTip().strip())
        from PySide6.QtWidgets import QLabel
        texts = [label.text() for label in
                 self.win.tileset_shape_section.findChildren(QLabel)]
        self.assertIn("Square is the current look. Rounded makes "
                      "a lone cell a rounded diamond and rounds "
                      "the corners.", texts)

    def test_blob_mode_shows_shape_section(self):
        self._pick_mode("blob")
        self.assertFalse(self.win.tileset_shape_section.isHidden())
        self._pick_mode("wang")
        self.assertTrue(self.win.tileset_shape_section.isHidden())

    def test_generate_with_no_shape_picked_builds_square(self):
        # No shape picked: the square construction is what the
        # app already does — mask 0 (index 0) fully transparent.
        self._generate_blob()
        self.assertIsNone(self.win.tileset_shape)
        tile0 = self.win.tileset_tiles[0]
        self.assertEqual(tile0.pixel(8, 8), 0)
        self.assertEqual(tile0.pixel(7, 7), 0)

    def test_pick_rounded_generates_rounded(self):
        from pixel_forge.image import argb
        self._pick_mode("blob")
        self._pick_shape("rounded")
        self.assertEqual(self.win.tileset_shape, "rounded")
        self.assertTrue(self.win.load_tileset_base(self.tile_a, "a"))
        self.win.tileset_generate_btn.click()
        self.app.processEvents()
        self.assertEqual(len(self.win.tileset_tiles), 47)
        # Mask 0 (index 0) is the rounded diamond: opaque at
        # the center, transparent at the corners.
        tile0 = self.win.tileset_tiles[0]
        self.assertEqual(tile0.pixel(7, 7), argb(255, 0, 0))
        self.assertEqual(tile0.pixel(0, 0), 0)
        # Mask 255 (index 46) is still the base tile unchanged.
        tile46 = self.win.tileset_tiles[46]
        self.assertEqual(tile46.pixel(8, 8), argb(255, 0, 0))

    def test_shape_change_clears_generated_set(self):
        self._generate_blob()
        self.assertIsNotNone(self.win.tileset_tiles)
        self._pick_shape("rounded")
        self.assertIsNone(self.win.tileset_tiles)
        self.assertIsNone(self.win.tileset_sheet)
        self.assertEqual(self.win.tileset_count_label.text(),
                         "No tileset yet")
        self.assertFalse(self.win.tileset_export_png_btn.isEnabled())
        # Regenerating under the new shape builds the rounded set.
        self.win.tileset_generate_btn.click()
        self.app.processEvents()
        self.assertIsNotNone(self.win.tileset_tiles)
        from pixel_forge.image import argb
        self.assertEqual(self.win.tileset_tiles[0].pixel(7, 7),
                         argb(255, 0, 0))


class PaletteEditorGuiTests(unittest.TestCase):
    """Task 39: the palette editor dialog, the four palette
    pickers, and Extract palette."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.show()
        self.app.processEvents()
        self._dialogs = []

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.close()
            dialog.deleteLater()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _editor(self, **kwargs):
        from pixel_forge.gui import PaletteEditorDialog
        dialog = PaletteEditorDialog(self.win,
                                     self.win.palette_store,
                                     **kwargs)
        self._dialogs.append(dialog)
        return dialog

    def _pick_row(self, dialog, row):
        """Pick a palette row the way a real click does:
        setCurrentRow AND emit itemClicked (emitting alone does
        not move the current row)."""
        dialog.palette_list.setCurrentRow(row)
        item = dialog.palette_list.item(row)
        dialog.palette_list.itemClicked.emit(item)
        self.app.processEvents()

    def _row_for(self, dialog, kind, name):
        for row in range(dialog.palette_list.count()):
            item = dialog.palette_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == (kind, name):
                return row
        return -1

    def _combo_data(self, combo):
        return [combo.itemData(i) for i in range(combo.count())]

    def test_combos_list_builtins(self):
        for combo in self.win._palette_combos():
            self.assertEqual(self._combo_data(combo),
                             ["image", "pico8", "gameboy",
                              "grayscale", "resurrect64",
                              "endesga32", "sweetie16"])
            self.assertEqual(combo.currentData(), "image")

    def test_combos_list_user_palettes_after_separator(self):
        self.win.palette_store.save("Sunset", [(46, 34, 47)])
        self.win.palette_store.save("Apple", [(1, 2, 3)])
        self.win._refresh_palette_combos()
        for combo in self.win._palette_combos():
            data = self._combo_data(combo)
            # Built-ins, then a separator (data None), then the
            # user palettes sorted by name.
            self.assertEqual(
                data[:7],
                ["image", "pico8", "gameboy", "grayscale",
                 "resurrect64", "endesga32", "sweetie16"])
            self.assertIsNone(data[7])
            self.assertEqual(data[8:], ["Apple", "Sunset"])

    def test_editor_opens_with_nothing_picked(self):
        dialog = self._editor()
        # Before show: no row is current.
        self.assertEqual(dialog.palette_list.currentRow(), -1)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.palette_list.count(), 6)
        # Qt moves the list's current row to 0 when the dialog is
        # shown — that auto-set is not a pick (see
        # test_sheet_frame_dialog_picks_nothing_on_open): the
        # dialog still has nothing picked and the editor stays
        # disabled until the user picks a palette.
        self.assertIsNone(dialog._picked)
        self.assertFalse(dialog.name_edit.isEnabled())
        self.assertFalse(dialog.save_btn.isEnabled())
        self.assertFalse(dialog.delete_btn.isEnabled())
        self.assertFalse(dialog.duplicate_btn.isEnabled())
        self.assertFalse(dialog.export_btn.isEnabled())
        self.assertTrue(dialog.new_btn.isEnabled())
        self.assertTrue(dialog.import_btn.isEnabled())

    def test_editor_create_palette(self):
        dialog = self._editor()
        dialog.show()
        self.app.processEvents()
        dialog.new_btn.click()
        self.app.processEvents()
        self.assertTrue(dialog.start_from_combo.isVisible())
        dialog.name_edit.setText("Fresh")
        # Start from PICO-8, then save.
        combo = dialog.start_from_combo
        for i in range(combo.count()):
            if combo.itemData(i) == ("builtin", "pico8"):
                combo.setCurrentIndex(i)
                break
        self.app.processEvents()
        self.assertEqual(len(dialog._editing["colors"]), 16)
        self.assertTrue(dialog.save_btn.isEnabled())
        dialog.save_btn.click()
        self.app.processEvents()
        self.assertTrue(dialog.changed)
        self.assertEqual(
            self.win.palette_store.get("Fresh"),
            self.win.palette_store.get("pico8", builtin=True))

    def test_editor_create_empty_needs_a_color(self):
        dialog = self._editor()
        dialog.new_btn.click()
        dialog.name_edit.setText("Empty")
        self.app.processEvents()
        # A name but no colors — Save stays disabled.
        self.assertFalse(dialog.save_btn.isEnabled())

    def test_editor_edit_colors(self):
        from unittest import mock
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        self.win.palette_store.save("Mine", [(10, 20, 30),
                                             (40, 50, 60)])
        dialog = self._editor()
        self._pick_row(dialog, self._row_for(dialog, "user", "Mine"))
        self.assertEqual(dialog.name_edit.text(), "Mine")
        self.assertEqual(dialog.color_list.count(), 2)
        # Add a color via the picker (patched to return red).
        with mock.patch.object(
                QColorDialog, "getColor",
                return_value=QColor(255, 0, 0)):
            dialog.add_btn.click()
        self.app.processEvents()
        self.assertEqual(len(dialog._editing["colors"]), 3)
        self.assertEqual(dialog._editing["colors"][-1], (255, 0, 0))
        # Move the red one up, then set it by typing a hex.
        dialog.color_list.setCurrentRow(2)
        self.app.processEvents()
        dialog.up_btn.click()
        self.app.processEvents()
        self.assertEqual(dialog._editing["colors"][1], (255, 0, 0))
        dialog.color_list.setCurrentRow(1)
        dialog.hex_edit.setText("#00ff00")
        dialog.set_hex_btn.click()
        self.app.processEvents()
        self.assertEqual(dialog._editing["colors"][1], (0, 255, 0))
        # Remove the first color, then save.
        dialog.color_list.setCurrentRow(0)
        dialog.remove_btn.click()
        self.app.processEvents()
        dialog.save_btn.click()
        self.app.processEvents()
        self.assertEqual(self.win.palette_store.get("Mine"),
                         [(0, 255, 0), (40, 50, 60)])

    def test_editor_rename(self):
        self.win.palette_store.save("Old", [(1, 2, 3)])
        dialog = self._editor()
        self._pick_row(dialog, self._row_for(dialog, "user", "Old"))
        dialog.name_edit.setText("New")
        dialog.save_btn.click()
        self.app.processEvents()
        self.assertIsNone(self.win.palette_store.get("Old"))
        self.assertEqual(self.win.palette_store.get("New"),
                         [(1, 2, 3)])

    def test_editor_delete_asks_and_deletes(self):
        from unittest import mock
        self.win.palette_store.save("Doomed", [(1, 2, 3)])
        dialog = self._editor()
        self._pick_row(dialog,
                       self._row_for(dialog, "user", "Doomed"))
        # Declining keeps it.
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            dialog.delete_btn.click()
        self.app.processEvents()
        self.assertIsNotNone(self.win.palette_store.get("Doomed"))
        # Saying Yes deletes it.
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            dialog.delete_btn.click()
        self.app.processEvents()
        self.assertTrue(dialog.changed)
        self.assertIsNone(self.win.palette_store.get("Doomed"))
        self.assertEqual(dialog.palette_list.count(), 6)

    def test_editor_builtin_is_read_only(self):
        dialog = self._editor()
        self._pick_row(dialog,
                       self._row_for(dialog, "builtin", "pico8"))
        self.assertEqual(dialog.name_edit.text(), "PICO-8")
        self.assertFalse(dialog.name_edit.isEnabled())
        self.assertFalse(dialog.add_btn.isEnabled())
        self.assertFalse(dialog.remove_btn.isEnabled())
        self.assertFalse(dialog.save_btn.isEnabled())
        self.assertFalse(dialog.delete_btn.isEnabled())
        self.assertTrue(dialog.duplicate_btn.isEnabled())
        self.assertTrue(dialog.export_btn.isEnabled())

    def test_editor_duplicate_builtin(self):
        dialog = self._editor()
        self._pick_row(dialog,
                       self._row_for(dialog, "builtin", "gameboy"))
        dialog.duplicate_btn.click()
        self.app.processEvents()
        self.assertEqual(dialog.name_edit.text(), "Game Boy copy")
        self.assertEqual(len(dialog._editing["colors"]), 4)
        dialog.save_btn.click()
        self.app.processEvents()
        self.assertEqual(
            self.win.palette_store.get("Game Boy copy"),
            self.win.palette_store.get("gameboy", builtin=True))

    def test_editor_import_hex_through_picker(self):
        from unittest import mock
        path = os.path.join(self.tmp, "imported.hex")
        with open(path, "w") as handle:
            handle.write("2e222f\n3e3546\n")
        dialog = self._editor()
        with mock.patch.object(
                QFileDialog, "getOpenFileName",
                return_value=(path, "")):
            dialog.import_btn.click()
        self.app.processEvents()
        self.assertTrue(dialog.changed)
        self.assertEqual(self.win.palette_store.get("imported"),
                         [(46, 34, 47), (62, 53, 70)])
        self.assertEqual(dialog.palette_list.count(), 7)

    def test_editor_export(self):
        from unittest import mock
        self.win.palette_store.save("Mine", [(46, 34, 47)])
        dialog = self._editor()
        self._pick_row(dialog, self._row_for(dialog, "user", "Mine"))
        out_path = os.path.join(self.tmp, "mine.gpl")
        with mock.patch.object(
                QFileDialog, "getSaveFileName",
                return_value=(out_path, "")):
            dialog.export_btn.click()
        self.app.processEvents()
        with open(out_path) as handle:
            text = handle.read()
        self.assertIn("GIMP Palette", text)
        self.assertIn("Name: Mine", text)

    def test_extract_button_disabled_then_enabled(self):
        self.assertFalse(self.win.extract_palette_btn.isEnabled())
        self.assertTrue(self.win.load_image(self.src))
        self.assertTrue(self.win.extract_palette_btn.isEnabled())

    def test_extract_palette_opens_editor_prefilled(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog
        from pixel_forge.gui import PaletteEditorDialog
        self.assertTrue(self.win.load_image(self.src))
        seen = []

        def fake_exec(self):
            seen.append(self)
            return QDialog.DialogCode.Accepted

        with mock.patch.object(PaletteEditorDialog, "exec",
                               fake_exec):
            self.win.extract_palette_btn.click()
        self.assertEqual(len(seen), 1)
        dialog = seen[0]
        self.assertEqual(dialog.name_edit.text(), "photo palette")
        self.assertEqual(len(dialog._editing["colors"]), 16)

    def test_combos_refresh_after_editor_save(self):
        from unittest import mock
        from PySide6.QtWidgets import QDialog
        from pixel_forge.gui import PaletteEditorDialog

        def fake_exec(self):
            self._on_new()
            self.name_edit.setText("Fresh")
            self._editing["colors"] = [(1, 2, 3)]
            self._on_save()
            return QDialog.DialogCode.Accepted

        with mock.patch.object(PaletteEditorDialog, "exec",
                               fake_exec):
            self.win.palettes_btn.click()
        for combo in self.win._palette_combos():
            self.assertIn("Fresh", self._combo_data(combo))

    def test_combo_falls_back_when_palette_deleted(self):
        self.win.palette_store.save("Sunset", [(46, 34, 47)])
        self.win._refresh_palette_combos()
        combo = self.win.palette_combo
        combo.setCurrentIndex(combo.findData("Sunset"))
        self.assertEqual(combo.currentData(), "Sunset")
        self.win.palette_store.delete("Sunset")
        self.win._refresh_palette_combos()
        self.assertEqual(combo.currentData(), "image")
        self.assertIn("Sunset",
                      self.win.statusBar().currentMessage())

    def test_preset_apply_falls_back_when_palette_gone(self):
        self.win.palette_store.save("Sunset", [(46, 34, 47)])
        settings = {"width": 32, "height": 32, "keep_aspect": False,
                    "palette": "Sunset", "max_colors": 16,
                    "dither": True}
        self.win.palette_store.delete("Sunset")
        self.win._refresh_palette_combos()
        self.win._apply_settings_to_tab("image", settings)
        self.assertEqual(self.win.palette_combo.currentData(),
                         "image")
        self.assertIn("Sunset",
                      self.win.statusBar().currentMessage())

    def test_pixelize_with_user_palette(self):
        from pixel_forge.pixelize import opaque_pixels
        self.win.palette_store.save("Two", [(255, 0, 0),
                                            (0, 0, 255)])
        self.win._refresh_palette_combos()
        self.assertTrue(self.win.load_image(self.src))
        combo = self.win.palette_combo
        combo.setCurrentIndex(combo.findData("Two"))
        self.app.processEvents()
        self.win.update_preview()
        result = self.win._layers[0].image
        used = set(opaque_pixels(result))
        self.assertTrue(used <= {(255, 0, 0), (0, 0, 255)})
        self.assertEqual(len(used), 2)

    def test_editor_visible_text_has_no_banned_phrasing(self):
        dialog = self._editor()
        for text in visible_texts(dialog):
            self.assertNotIn("&", text,
                             "bare & in visible text: %r" % text)
            self.assertNotIn("(s)", text,
                             "programmer phrasing in visible text: "
                             "%r" % text)


class GeometryPersistenceTests(unittest.TestCase):
    """Task 66: the window remembers its size + position.

    Every test uses its own temp-file QSettings, so nothing
    touches the user's real settings — and each window gets a
    fresh QSettings on the same file, the way a new launch
    would. The startup update check is off in these settings
    (task 103 made it on by default): these tests are about
    geometry, not the network."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.tmp, "settings.ini")
        self.lib_dir = os.path.join(self.tmp, "lib")
        self._windows = []

    def tearDown(self):
        for win in self._windows:
            win.close()
            win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        # The startup update check is off in these settings
        # (task 103 made it on by default): these tests are
        # about geometry, not the network. The opt-out is
        # written here, not in setUp, so the settings file is
        # only created when a test actually asks for settings
        # — test_window_without_settings_keeps_default asserts
        # the file stays absent.
        settings = QSettings(self.settings_path,
                             QSettings.IniFormat)
        settings.setValue("check_updates_on_startup", False)
        settings.sync()
        return settings

    def _window(self, settings="default"):
        from pixel_forge.gui import MainWindow
        if settings == "default":
            settings = self._settings()
        win = MainWindow(library_dir=self.lib_dir, restore=False,
                         settings=settings)
        self._windows.append(win)
        return win

    def _rect(self, win):
        rect = win.geometry()
        return (rect.x(), rect.y(), rect.width(), rect.height())

    def test_window_reopens_at_saved_geometry(self):
        # Move + resize one window, close it, open a fresh one:
        # it opens where the first one closed.
        first = self._window()
        first.show()
        self.app.processEvents()
        first.setGeometry(52, 42, 700, 500)
        self.app.processEvents()
        saved = self._rect(first)
        # The layout minimum floors the 700-wide request, so
        # the saved geometry is whatever the window actually
        # is — the position and height are as requested.
        self.assertEqual(saved[:2], (52, 42))
        self.assertEqual(saved[3], 500)
        self.assertGreaterEqual(saved[2], 700)
        first.close()
        self.app.processEvents()
        # The values are on disk, not just in memory.
        stored = self._settings()
        self.assertEqual(
            [stored.value("window/%s" % key, type=int)
             for key in ("x", "y", "width", "height")],
            list(saved))
        second = self._window()
        second.show()
        self.app.processEvents()
        # Offscreen the screen (800 wide) is narrower than the
        # window's minimum width, so the clamp pulls the
        # restore to the left edge and Qt floors the width
        # back to the minimum — the height and y survive
        # untouched. On a real screen the restore is exact.
        # The x reads 0 or 2: offscreen nudges an edge-sitting
        # window 2 px in (probed on a bare QMainWindow too).
        restored = self._rect(second)
        self.assertEqual(restored[1:],
                         (saved[1], saved[2], saved[3]))
        self.assertIn(restored[0], (0, 2))

    def test_offscreen_saved_geometry_is_clamped(self):
        # A saved rectangle hanging off the bottom-right corner
        # is pulled back so the whole window is visible. The
        # expected corner is computed from the screen Qt
        # reports, not a hardcoded size; the size is floored
        # by the layout minimum, which the clamp does not
        # fight — exact size clamping is pinned by
        # test_clamp_geometry instead.
        avail = self.app.primaryScreen().availableGeometry()
        stored = self._settings()
        stored.setValue("window/x", avail.right() - 10)
        stored.setValue("window/y", avail.bottom() - 10)
        stored.setValue("window/width", 400)
        stored.setValue("window/height", 300)
        stored.sync()
        win = self._window()
        win.show()
        self.app.processEvents()
        hint = win.minimumSizeHint()
        self.assertEqual(
            self._rect(win),
            (avail.left() + avail.width() - 400,
             avail.top() + avail.height() - 300,
             max(400, hint.width()), max(300, hint.height())))

    def test_clamp_geometry(self):
        from pixel_forge.gui import clamp_geometry
        avail = QRect(0, 0, 800, 800)
        cases = [
            # Inside: untouched.
            (QRect(10, 10, 100, 100), QRect(10, 10, 100, 100)),
            # Off the bottom-right: pulled back, size kept.
            (QRect(700, 700, 400, 300), QRect(400, 500, 400, 300)),
            # Off the top-left: pulled back to the corner.
            (QRect(-50, -40, 200, 150), QRect(0, 0, 200, 150)),
            # Bigger than the screen: capped at the screen.
            (QRect(-5000, -5000, 5000, 5000),
             QRect(0, 0, 800, 800)),
            # Wider than the screen, fitting height: only the
            # width is capped, and the x goes to the left edge.
            (QRect(50, 50, 2000, 100), QRect(0, 50, 800, 100)),
        ]
        for rect, expected in cases:
            self.assertEqual(clamp_geometry(rect, avail), expected,
                             "clamp %r" % (rect.getRect(),))
        # No screen to clamp against: the rectangle is left
        # alone rather than collapsed.
        self.assertEqual(clamp_geometry(QRect(10, 10, 100, 100),
                                        QRect()),
                         QRect(10, 10, 100, 100))

    def test_no_saved_geometry_keeps_default(self):
        # First launch: nothing in the settings file, so the
        # window opens 1020x640 exactly as before.
        win = self._window()
        win.show()
        self.app.processEvents()
        self.assertEqual((win.width(), win.height()), (1020, 640))

    def test_window_without_settings_keeps_default(self):
        # A window built without settings (every existing test)
        # behaves exactly as before: 1020x640, nothing saved.
        win = self._window(settings=None)
        win.show()
        self.app.processEvents()
        self.assertEqual((win.width(), win.height()), (1020, 640))
        self.assertFalse(os.path.exists(self.settings_path))

    def test_partial_or_invalid_saved_geometry_keeps_default(self):
        # Half a saved rectangle (or a nonsense size) is not a
        # rectangle at all: fall back to the default.
        stored = self._settings()
        stored.setValue("window/x", 100)
        stored.setValue("window/y", 100)
        stored.sync()
        win = self._window()
        win.show()
        self.app.processEvents()
        self.assertEqual((win.width(), win.height()), (1020, 640))
        win.close()
        self.app.processEvents()
        stored.setValue("window/width", 0)
        stored.setValue("window/height", -5)
        stored.sync()
        again = self._window()
        again.show()
        self.app.processEvents()
        self.assertEqual((again.width(), again.height()),
                         (1020, 640))


class ThemeToggleTests(unittest.TestCase):
    """Task 67: the light/dark theme toggle.

    Every test uses its own temp-file QSettings, so nothing
    touches the user's real settings. tearDown restores the
    dark stylesheet, so a light theme left behind cannot leak
    into another test class. The startup update check is off
    in these settings (task 103 made it on by default):
    these tests are about the theme, not the network.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.tmp, "settings.ini")
        self.lib_dir = os.path.join(self.tmp, "lib")
        self._windows = []
        settings = QSettings(self.settings_path,
                             QSettings.IniFormat)
        settings.setValue("check_updates_on_startup", False)
        settings.sync()

    def tearDown(self):
        from pixel_forge.gui import DARK_STYLESHEET
        for win in self._windows:
            win.close()
            win.deleteLater()
        self.app.setStyleSheet(DARK_STYLESHEET)
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        return QSettings(self.settings_path, QSettings.IniFormat)

    def _window(self, settings="default"):
        from pixel_forge.gui import MainWindow
        if settings == "default":
            settings = self._settings()
        win = MainWindow(library_dir=self.lib_dir, restore=False,
                         settings=settings)
        self._windows.append(win)
        return win

    def test_toggle_switches_stylesheet_both_ways(self):
        from pixel_forge.gui import (DARK_STYLESHEET,
                                     LIGHT_STYLESHEET)
        win = self._window()
        win.show()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), DARK_STYLESHEET)
        self.assertFalse(win._light_theme_action.isChecked())
        win._light_theme_action.trigger()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), LIGHT_STYLESHEET)
        self.assertTrue(win._light_theme_action.isChecked())
        win._light_theme_action.trigger()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), DARK_STYLESHEET)
        self.assertFalse(win._light_theme_action.isChecked())

    def test_choice_persists_across_windows(self):
        from pixel_forge.gui import LIGHT_STYLESHEET
        first = self._window()
        first.show()
        self.app.processEvents()
        first._light_theme_action.trigger()
        self.app.processEvents()
        # The choice is on disk, not just in memory.
        stored = self._settings()
        self.assertEqual(stored.value("theme"), "light")
        first.close()
        self.app.processEvents()
        again = self._window()
        again.show()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), LIGHT_STYLESHEET)
        self.assertTrue(again._light_theme_action.isChecked())

    def test_no_saved_choice_defaults_to_dark(self):
        from pixel_forge.gui import DARK_STYLESHEET
        win = self._window()
        win.show()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), DARK_STYLESHEET)
        self.assertFalse(win._light_theme_action.isChecked())

    def test_window_without_settings_switches_but_saves_nothing(self):
        # No settings, no save: toggling still switches the
        # live theme, but nothing is written anywhere — and the
        # real settings file is untouched either way.
        from pixel_forge.gui import (DARK_STYLESHEET,
                                     LIGHT_STYLESHEET)
        self.app.setStyleSheet(DARK_STYLESHEET)
        real_conf = os.path.join(
            os.path.expanduser("~"), ".config", "BrokeNBored",
            "PixelForge.conf")
        before = os.path.exists(real_conf)
        win = self._window(settings=None)
        win.show()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), DARK_STYLESHEET)
        self.assertFalse(win._light_theme_action.isChecked())
        win._light_theme_action.trigger()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), LIGHT_STYLESHEET)
        self.assertEqual(os.path.exists(real_conf), before)

    def test_saved_light_theme_restores_on_open(self):
        from pixel_forge.gui import LIGHT_STYLESHEET
        stored = self._settings()
        stored.setValue("theme", "light")
        stored.sync()
        win = self._window()
        win.show()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), LIGHT_STYLESHEET)
        self.assertTrue(win._light_theme_action.isChecked())

    def test_invalid_saved_theme_falls_back_to_dark(self):
        from pixel_forge.gui import DARK_STYLESHEET
        stored = self._settings()
        stored.setValue("theme", "banana")
        stored.sync()
        win = self._window()
        win.show()
        self.app.processEvents()
        self.assertEqual(self.app.styleSheet(), DARK_STYLESHEET)
        self.assertFalse(win._light_theme_action.isChecked())

    def test_over_limit_counter_uses_theme_property(self):
        # The counter flips a property now (see
        # test_prompt_counter): the color comes from the
        # theme's stylesheet, so it stays readable in light.
        from pixel_forge.imagegen import MAX_PROMPT_LEN
        win = self._window()
        win.show()
        self.app.processEvents()
        win.prompt_edit.setText("hello")
        self.assertFalse(
            win.prompt_counter_label.property("overLimit"))
        win.prompt_edit.setText("x" * (MAX_PROMPT_LEN + 1))
        self.assertTrue(
            win.prompt_counter_label.property("overLimit"))


class UpdateCheckGuiTests(unittest.TestCase):
    """Tasks 48 + 103: the update check — the Help menu
    actions, the persisted choice, the manual check, and the
    automatic startup check. On by default since task 103, so
    a test that shows a window without wanting the startup
    check writes the switch off first (_disable_auto_update).
    No test touches the real network: pixel_forge.gui
    .check_for_update is stubbed, so the worker runs the
    stub. Every test uses its own temp-file QSettings, so
    nothing touches the user's real settings."""

    RELEASE_URL = ("https://github.com/BROKE-N-BORED/PIXEL-FORGE/"
                   "releases/tag/v0.70")

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.tmp, "settings.ini")
        self.lib_dir = os.path.join(self.tmp, "lib")
        self._windows = []

    def tearDown(self):
        for win in self._windows:
            win.close()
            win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        return QSettings(self.settings_path, QSettings.IniFormat)

    def _disable_auto_update(self):
        # Task 103: the startup check is on by default, so a
        # test that shows a window without wanting one writes
        # the switch off in the settings file first.
        settings = self._settings()
        settings.setValue("check_updates_on_startup", False)
        settings.sync()

    def _window(self, settings="default", show=True):
        from pixel_forge.gui import MainWindow
        if settings == "default":
            settings = self._settings()
        win = MainWindow(library_dir=self.lib_dir, restore=False,
                         settings=settings)
        self._windows.append(win)
        if show:
            win.show()
            self.app.processEvents()
        return win

    def _wait_for_check(self, win, timeout=10):
        deadline = time.time() + timeout
        while win.update_worker is not None \
                and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()
        self.assertIsNone(win.update_worker,
                          "the update check did not finish in %ss"
                          % timeout)

    def _watch_dialog(self, button_text, seen):
        # A timer watcher for a modal QMessageBox: when one
        # appears, record its text and click the named button.
        # Returns the watcher; the caller stops it.
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is None or not isinstance(dialog, QMessageBox):
                return
            seen.append(dialog.text())
            for button in dialog.buttons():
                if button.text() == button_text:
                    button.click()
                    return

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        return watcher

    def _help_actions(self, win):
        bar = win.menuBar()
        for action in bar.actions():
            if action.text() == "Help":
                return action.menu().actions()
        self.fail("no Help menu")

    def test_help_menu_update_actions(self):
        # The check is stubbed: with no saved setting the
        # startup check runs on show (on by default since
        # task 103), and the stub keeps it off the network.
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "current",
                              "latest": __version__, "url": ""}):
            win = self._window()
            self._wait_for_check(win)
        actions = [a.text() for a in self._help_actions(win)]
        self.assertIn("Check for updates", actions)
        self.assertIn("Check for updates on startup", actions)
        check = win._check_updates_action
        auto = win._auto_update_action
        self.assertTrue(check.toolTip().strip())
        self.assertTrue(auto.toolTip().strip())
        for text in (check.text(), check.toolTip(),
                     auto.text(), auto.toolTip()):
            self.assertNotIn("—", text)
            self.assertNotIn("(s)", text)
        self.assertTrue(auto.isChecked())
        self.assertTrue(auto.isCheckable())
        self.assertFalse(check.isCheckable())

    def test_update_check_on_by_default(self):
        # Task 103: a fresh install checks on launch. With no
        # saved setting, showing the window starts the worker
        # and the stubbed check is called.
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "current",
                              "latest": __version__, "url": ""}
                ) as checked:
            win = self._window()
            self._wait_for_check(win)
            checked.assert_called_once()
            self.assertTrue(win._auto_update_action.isChecked())
            self.assertTrue(win._load_update_check())

    def test_update_check_off_without_settings(self):
        win = self._window(settings=None)
        self.assertFalse(win._load_update_check())
        self.assertFalse(win._auto_update_action.isChecked())

    def test_toggle_persists_across_windows(self):
        # The check is stubbed for the whole test: the first
        # window reads the default ON and runs its startup
        # check on show — without the stub that would be a real
        # network request. Task 103: the action starts
        # checked, so the first trigger turns it OFF.
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "current",
                              "latest": __version__, "url": ""}):
            win = self._window()
            self._wait_for_check(win)
            self.assertTrue(win._auto_update_action.isChecked())
            win._auto_update_action.trigger()
            self.app.processEvents()
            saved = self._settings().value(
                "check_updates_on_startup", type=bool)
            self.assertFalse(saved)
            # A new window on the same settings file reads it
            # back and shows the action unchecked — and runs
            # no startup check.
            second = self._window()
            self.assertFalse(
                second._auto_update_action.isChecked())
            self.assertFalse(second._load_update_check())
            self.assertIsNone(second.update_worker)
            second._auto_update_action.trigger()
            self.app.processEvents()
            saved = self._settings().value(
                "check_updates_on_startup", type=bool)
            self.assertTrue(saved)

    def test_manual_check_newer_shows_dialog(self):
        # Task 103: the popup asks first — Update / Not now,
        # Not now the safe default — with the version and a
        # short what's-new. The watcher answers Not now.
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__
        self._disable_auto_update()
        win = self._window()
        seen = []
        watcher = self._watch_dialog("Not now", seen)
        try:
            with mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value={"state": "newer",
                                  "latest": "0.70",
                                  "url": self.RELEASE_URL,
                                  "notes": "The grid is fixed.",
                                  "assets": {}}):
                win._check_updates_action.trigger()
                self._wait_for_check(win)
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn("Pixel Forge 0.70 is out", seen[0])
        self.assertIn(__version__, seen[0])
        self.assertIn("The grid is fixed.", seen[0])
        self.assertNotIn("—", seen[0])
        self.assertNotIn("(s)", seen[0])
        self.assertIsNone(win.update_download_worker)

    def test_manual_check_newer_not_now_is_default(self):
        # U23: the safe choice is the default button, so
        # pressing Enter answers Not now, never Update.
        import pixel_forge.gui as gui_mod
        from PySide6.QtWidgets import QMessageBox
        self._disable_auto_update()
        win = self._window()
        defaults = []

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is None or not isinstance(dialog,
                                                QMessageBox):
                return
            defaults.append(dialog.defaultButton().text())
            for button in dialog.buttons():
                if button.text() == "Not now":
                    button.click()
                    return

        from PySide6.QtCore import QTimer
        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        try:
            with mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value={"state": "newer",
                                  "latest": "0.70",
                                  "url": self.RELEASE_URL,
                                  "notes": "", "assets": {}}):
                win._check_updates_action.trigger()
                self._wait_for_check(win)
        finally:
            watcher.stop()
        self.assertEqual(defaults, ["Not now"])

    def test_manual_check_current_shows_up_to_date(self):
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__
        self._disable_auto_update()
        win = self._window()
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "current",
                              "latest": __version__,
                              "url": self.RELEASE_URL}), \
                mock.patch.object(
                    gui_mod.QMessageBox, "information") as shown:
            win._check_updates_action.trigger()
            self._wait_for_check(win)
        shown.assert_called_once()
        text = shown.call_args[0][2]
        self.assertIn("newest version", text)
        self.assertIn(__version__, text)
        self.assertNotIn("—", text)

    def test_manual_check_failure_warns_with_reason(self):
        import pixel_forge.gui as gui_mod
        self._disable_auto_update()
        win = self._window()
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "unknown", "latest": None,
                              "url": None,
                              "reason": "could not reach GitHub: "
                                        "no route to host"}), \
                mock.patch.object(
                    gui_mod, "warn_dialog") as warned:
            win._check_updates_action.trigger()
            self._wait_for_check(win)
        warned.assert_called_once()
        self.assertEqual(warned.call_args[0][1],
                         "Could not check for updates")
        self.assertIn("could not reach GitHub",
                      warned.call_args[0][2])

    def test_automatic_check_runs_on_startup_when_opted_in(self):
        # The setting on, the window shown: the check runs
        # once, with no menu click, and a newer release shows
        # the same ask-first popup as a manual check.
        import pixel_forge.gui as gui_mod
        settings = self._settings()
        settings.setValue("check_updates_on_startup", True)
        settings.sync()
        seen = []
        watcher = self._watch_dialog("Not now", seen)
        try:
            with mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value={"state": "newer",
                                  "latest": "0.70",
                                  "url": self.RELEASE_URL,
                                  "notes": "", "assets": {}}):
                win = self._window()
                self._wait_for_check(win)
        finally:
            watcher.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn("Pixel Forge 0.70 is out", seen[0])

    def test_automatic_check_current_stays_quiet(self):
        # An automatic check that finds nothing says nothing —
        # no dialog, no status line.
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__
        settings = self._settings()
        settings.setValue("check_updates_on_startup", True)
        settings.sync()
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "current",
                              "latest": __version__,
                              "url": self.RELEASE_URL}), \
                mock.patch.object(
                    gui_mod.QMessageBox, "information") as shown:
            win = self._window()
            self._wait_for_check(win)
        shown.assert_not_called()
        # The status bar still holds the first-launch message —
        # the quiet check wrote nothing.
        self.assertEqual(win.statusBar().currentMessage(),
                         "Ready — import an image to begin.")

    def test_automatic_check_failure_is_status_bar_only(self):
        # An automatic failure never opens a modal on startup —
        # a status-bar line at most.
        import pixel_forge.gui as gui_mod
        settings = self._settings()
        settings.setValue("check_updates_on_startup", True)
        settings.sync()
        with mock.patch.object(
                gui_mod, "check_for_update",
                return_value={"state": "unknown", "latest": None,
                              "url": None,
                              "reason": "the update check timed "
                                        "out"}), \
                mock.patch.object(
                    gui_mod, "warn_dialog") as warned:
            win = self._window()
            self._wait_for_check(win)
        warned.assert_not_called()
        self.assertEqual(win.statusBar().currentMessage(),
                         "Could not check for updates.")

    def test_second_check_while_running_is_refused(self):
        # A check already running: a second ask is refused
        # plainly instead of starting a second request.
        import pixel_forge.gui as gui_mod
        from pixel_forge import __version__

        def slow_check(version):
            time.sleep(0.6)
            return {"state": "current", "latest": __version__,
                    "url": self.RELEASE_URL}

        self._disable_auto_update()
        win = self._window()
        with mock.patch.object(
                gui_mod, "check_for_update",
                side_effect=slow_check), \
                mock.patch.object(
                    gui_mod.QMessageBox, "information"):
            win._check_updates_action.trigger()
            self.app.processEvents()
            win._check_updates_action.trigger()
            self.app.processEvents()
            self.assertEqual(win.statusBar().currentMessage(),
                             "Already checking for updates.")
            self._wait_for_check(win)


class UpdateDownloadGuiTests(unittest.TestCase):
    """Task 103: the ask-first auto-update — the refusals
    (busy, source tree, missing files), the download worker,
    the checksum gate, cancel, and the restart question. No
    test touches the real network or a real pip: the check is
    stubbed, the download and install steps are stubbed
    through pixel_forge.gui, and the restart is a recorded
    call, never a real re-exec."""

    ZIP_URL = "https://example.test/pixel-forge-v0.84.zip"
    SIDECAR_URL = ("https://example.test/"
                   "pixel-forge-v0.84.zip.sha256")
    ZIP_BYTES = b"the 0.84 release bytes"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.DIGEST = hashlib.sha256(cls.ZIP_BYTES).hexdigest()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.tmp, "settings.ini")
        self.lib_dir = os.path.join(self.tmp, "lib")
        self.work_dir = os.path.join(self.tmp, "updates")
        self._windows = []
        self._downloads = []
        self._installs = []
        self._restarts = []

    def tearDown(self):
        for win in self._windows:
            win.close()
            win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        return QSettings(self.settings_path, QSettings.IniFormat)

    def _window(self):
        from pixel_forge.gui import MainWindow
        settings = self._settings()
        settings.setValue("check_updates_on_startup", False)
        settings.sync()
        win = MainWindow(library_dir=self.lib_dir, restore=False,
                         settings=self._settings())
        self._windows.append(win)
        win.show()
        self.app.processEvents()
        return win

    def _result(self, with_zip=True, with_sidecar=True):
        assets = {}
        if with_zip:
            assets["pixel-forge-v0.84.zip"] = {
                "url": self.ZIP_URL, "size": len(self.ZIP_BYTES)}
        if with_sidecar:
            assets["pixel-forge-v0.84.zip.sha256"] = {
                "url": self.SIDECAR_URL, "size": 80}
        return {"state": "newer", "latest": "0.84",
                "url": "https://github.com/BROKE-N-BORED/"
                       "PIXEL-FORGE/releases/tag/v0.84",
                "notes": "The update itself.",
                "assets": assets}

    def _watch_flow(self, restart_choice, seen):
        # A timer watcher for the two popups in order: click
        # Update on the ask-first popup, then the named button
        # on the restart popup. Records (title, text) pairs.
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        def watch():
            dialog = QApplication.activeModalWidget()
            if dialog is None or not isinstance(dialog,
                                                QMessageBox):
                return
            title = dialog.windowTitle()
            seen.append((title, dialog.text()))
            if title.startswith("Pixel Forge 0.84 is out"):
                want = "Update"
            elif title == "Update ready":
                want = restart_choice
            else:
                return
            for button in dialog.buttons():
                if button.text() == want:
                    button.click()
                    return

        watcher = QTimer()
        watcher.timeout.connect(watch)
        watcher.start(50)
        return watcher

    def _wait_for_check(self, win, timeout=10):
        deadline = time.time() + timeout
        while win.update_worker is not None \
                and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()
        self.assertIsNone(win.update_worker,
                          "the update check did not finish in %ss"
                          % timeout)

    def _wait_for_download(self, win, timeout=15):
        deadline = time.time() + timeout
        while win.update_download_worker is not None \
                and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()
        self.assertIsNone(win.update_download_worker,
                          "the update did not finish in %ss"
                          % timeout)

    def _fake_download(self, url, dest, expected_size=None,
                       cancel_event=None, progress=None,
                       urlopen=None):
        self._downloads.append(url)
        total = expected_size or len(self.ZIP_BYTES)
        if progress is not None:
            progress(0, total)
            progress(total, total)
        Path(dest).write_bytes(self.ZIP_BYTES)

    def _fake_install(self, zip_path, version):
        self._installs.append((str(zip_path), version))

    def _patch_flow(self, stack, gui_mod, digest=None,
                    download=None):
        # The seams a full pull goes through: an installed
        # root (not a source tree), a temp updates folder, a
        # sidecar fetch, the download, and the install.
        stack.enter_context(mock.patch.object(
            gui_mod, "find_installed_root",
            return_value=Path(
                "/fake/venv/lib/python3.12/site-packages/"
                "pixel_forge")))
        stack.enter_context(mock.patch.object(
            gui_mod, "updates_home",
            return_value=Path(self.work_dir)))
        stack.enter_context(mock.patch.object(
            gui_mod, "fetch_text",
            return_value="%s  pixel-forge-v0.84.zip\n"
                         % (digest or self.DIGEST)))
        stack.enter_context(mock.patch.object(
            gui_mod, "download_file",
            side_effect=download or self._fake_download))
        stack.enter_context(mock.patch.object(
            gui_mod, "install_update",
            side_effect=self._fake_install))

    def _run_flow(self, win, gui_mod, restart_choice, seen,
                  digest=None, download=None):
        # The whole pull from a manual check: the popup asks,
        # the watcher answers Update, the worker runs, the
        # restart popup answers the named button.
        stack = ExitStack()
        self._patch_flow(stack, gui_mod, digest=digest,
                         download=download)
        stack.enter_context(mock.patch.object(
            gui_mod, "restart_program",
            side_effect=lambda: self._restarts.append(True)))
        watcher = self._watch_flow(restart_choice, seen)
        try:
            with stack, mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value=self._result()):
                win._check_updates_action.trigger()
                self._wait_for_check(win)
                self._wait_for_download(win)
        finally:
            watcher.stop()

    def test_update_flow_restart_no(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        seen = []
        self._run_flow(win, gui_mod, "No", seen)
        titles = {title for title, _text in seen}
        self.assertIn("Pixel Forge 0.84 is out", titles)
        self.assertIn("Update ready", titles)
        self.assertEqual(len(self._installs), 1)
        zip_path, version = self._installs[0]
        self.assertEqual(version, "0.84")
        self.assertTrue(zip_path.startswith(self.work_dir))
        self.assertEqual(self._restarts, [])
        self.assertTrue(win.isVisible())
        self.assertFalse(win._activity_dot.isVisible())
        self.assertEqual(
            win.statusBar().currentMessage(),
            "The update will finish the next time Pixel Forge "
            "starts.")

    def test_update_flow_restart_yes(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        seen = []
        self._run_flow(win, gui_mod, "Yes", seen)
        self.assertEqual(len(self._restarts), 1)
        self.assertEqual(len(self._installs), 1)
        self.assertFalse(win.isVisible())

    def test_update_flow_shows_download_progress(self):
        import pixel_forge.gui as gui_mod
        win = self._window()

        def slow_download(url, dest, expected_size=None,
                          cancel_event=None, progress=None,
                          urlopen=None):
            total = expected_size or len(self.ZIP_BYTES)
            if progress is not None:
                progress(0, total)
            time.sleep(0.3)
            if progress is not None:
                progress(total // 2, total)
            time.sleep(0.3)
            Path(dest).write_bytes(self.ZIP_BYTES)
            if progress is not None:
                progress(total, total)

        seen = []
        stack = ExitStack()
        self._patch_flow(stack, gui_mod, download=slow_download)
        stack.enter_context(mock.patch.object(
            gui_mod, "restart_program",
            side_effect=lambda: self._restarts.append(True)))
        watcher = self._watch_flow("No", seen)
        try:
            with stack, mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value=self._result()):
                win._check_updates_action.trigger()
                self._wait_for_check(win)
                # Poll the indicator while the download runs:
                # the halfway count must show.
                deadline = time.time() + 10
                texts = []
                while time.time() < deadline:
                    self.app.processEvents()
                    texts.append(win._activity_text.text())
                    if "50%" in texts[-1]:
                        break
                    time.sleep(0.02)
                self.assertIn(
                    "Downloading the update… 50%", texts)
                self._wait_for_download(win)
        finally:
            watcher.stop()

    def test_checksum_mismatch_refuses_and_installs_nothing(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        seen = []
        with mock.patch.object(
                gui_mod, "warn_dialog") as warned:
            self._run_flow(win, gui_mod, "No", seen,
                           digest="0" * 64)
        self.assertEqual(self._installs, [])
        self.assertEqual(self._restarts, [])
        warned.assert_called_once()
        self.assertEqual(warned.call_args[0][1],
                         "Could not update")
        self.assertIn("does not match the checksum",
                      warned.call_args[0][2])
        self.assertFalse(win._activity_dot.isVisible())

    def test_unparseable_sidecar_refuses_and_installs_nothing(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        seen = []
        stack = ExitStack()
        self._patch_flow(stack, gui_mod)
        stack.enter_context(mock.patch.object(
            gui_mod, "fetch_text",
            return_value="not a checksum at all\n"))
        watcher = self._watch_flow("No", seen)
        try:
            with stack, mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value=self._result()), \
                    mock.patch.object(
                        gui_mod, "warn_dialog") as warned:
                win._check_updates_action.trigger()
                self._wait_for_check(win)
                self._wait_for_download(win)
        finally:
            watcher.stop()
        self.assertEqual(self._installs, [])
        warned.assert_called_once()
        self.assertIn("no checksum file",
                      warned.call_args[0][2])

    def test_cancel_stops_download_and_installs_nothing(self):
        import pixel_forge.gui as gui_mod
        from pixel_forge.updates import DownloadCancelled
        win = self._window()

        def blocking_download(url, dest, expected_size=None,
                              cancel_event=None, progress=None,
                              urlopen=None):
            if progress is not None:
                progress(0, expected_size or 100)
            deadline = time.time() + 10
            while time.time() < deadline:
                if cancel_event is not None \
                        and cancel_event.is_set():
                    raise DownloadCancelled()
                time.sleep(0.02)
            raise DownloadCancelled()

        seen = []
        stack = ExitStack()
        self._patch_flow(stack, gui_mod,
                         download=blocking_download)
        watcher = self._watch_flow("No", seen)
        try:
            with stack, mock.patch.object(
                    gui_mod, "check_for_update",
                    return_value=self._result()):
                win._check_updates_action.trigger()
                self._wait_for_check(win)
                # Wait for the indicator's Cancel button to
                # show, then press it.
                deadline = time.time() + 10
                while not win._activity_cancel.isVisible() \
                        and time.time() < deadline:
                    self.app.processEvents()
                    time.sleep(0.02)
                self.assertTrue(
                    win._activity_cancel.isVisible())
                win._activity_cancel.click()
                self._wait_for_download(win)
        finally:
            watcher.stop()
        self.assertEqual(self._installs, [])
        self.assertEqual(self._restarts, [])
        self.assertEqual(
            win.statusBar().currentMessage(),
            "Update cancelled, nothing changed")
        self.assertFalse(win._activity_dot.isVisible())

    def test_busy_refuses_update_plainly(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        win._activity_start("video", "Pixelizing frame 1 of 40")
        self.app.processEvents()
        with mock.patch.object(
                gui_mod, "warn_dialog") as warned:
            started = win._start_update_download(self._result())
        self.assertFalse(started)
        self.assertIsNone(win.update_download_worker)
        warned.assert_called_once()
        self.assertEqual(warned.call_args[0][1],
                         "Pixel Forge is busy")
        self.assertIn("Pixelizing frame 1 of 40",
                      warned.call_args[0][2])
        # Once the operation finishes, the same update starts.
        win._activity_finish("video")
        self.app.processEvents()
        seen = []
        stack = ExitStack()
        self._patch_flow(stack, gui_mod)
        watcher = self._watch_flow("No", seen)
        try:
            with stack:
                started = win._start_update_download(
                    self._result())
                self.assertTrue(started)
                self._wait_for_download(win)
        finally:
            watcher.stop()
        self.assertEqual(len(self._installs), 1)

    def test_source_tree_refuses_update(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        with mock.patch.object(
                gui_mod, "find_installed_root",
                return_value=None), \
                mock.patch.object(
                    gui_mod, "warn_dialog") as warned:
            started = win._start_update_download(self._result())
        self.assertFalse(started)
        self.assertIsNone(win.update_download_worker)
        warned.assert_called_once()
        self.assertEqual(warned.call_args[0][1],
                         "Cannot update this copy")
        self.assertIn("source folder", warned.call_args[0][2])

    def test_missing_sidecar_asset_refuses(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        with mock.patch.object(
                gui_mod, "find_installed_root",
                return_value=Path("/fake/site-packages/"
                                  "pixel_forge")), \
                mock.patch.object(
                    gui_mod, "warn_dialog") as warned:
            started = win._start_update_download(
                self._result(with_sidecar=False))
        self.assertFalse(started)
        self.assertIsNone(win.update_download_worker)
        warned.assert_called_once()
        self.assertIn("no checksum file",
                      warned.call_args[0][2])

    def test_missing_zip_asset_refuses(self):
        import pixel_forge.gui as gui_mod
        win = self._window()
        with mock.patch.object(
                gui_mod, "find_installed_root",
                return_value=Path("/fake/site-packages/"
                                  "pixel_forge")), \
                mock.patch.object(
                    gui_mod, "warn_dialog") as warned:
            started = win._start_update_download(
                self._result(with_zip=False))
        self.assertFalse(started)
        self.assertIsNone(win.update_download_worker)
        warned.assert_called_once()
        self.assertIn("no download", warned.call_args[0][2])

    def test_second_download_while_running_is_refused(self):
        import pixel_forge.gui as gui_mod
        from pixel_forge.updates import DownloadCancelled
        win = self._window()

        def blocking_download(url, dest, expected_size=None,
                              cancel_event=None, progress=None,
                              urlopen=None):
            deadline = time.time() + 10
            while time.time() < deadline:
                if cancel_event is not None \
                        and cancel_event.is_set():
                    raise DownloadCancelled()
                time.sleep(0.02)
            raise DownloadCancelled()

        stack = ExitStack()
        self._patch_flow(stack, gui_mod,
                         download=blocking_download)
        try:
            with stack:
                started = win._start_update_download(
                    self._result())
                self.assertTrue(started)
                self.assertEqual(
                    win.statusBar().currentMessage(), "")
                started = win._start_update_download(
                    self._result())
                self.assertFalse(started)
                self.assertEqual(
                    win.statusBar().currentMessage(),
                    "Already downloading the update.")
                win._stop_update_download_worker()
        finally:
            self.app.processEvents()


class OriginalPaneCacheTests(unittest.TestCase):
    """Task 60: the Original pane pixmap is cached across zoom
    drags — _refresh_pixmaps rebuilds only the result pane when
    the source and its fit have not changed, and the Prompt tab
    does the same for the keeper pane."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))

    def setUp(self):
        from pixel_forge import gui as gui_mod
        from pixel_forge.gui import MainWindow
        self.gui_mod = gui_mod
        self.win = MainWindow()
        self.win.load_image(self.src)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _count_before_builds(self, fn):
        """How many checkerboard_pixmap calls with smooth=True
        (the Original pane — the result pane uses smooth=False)
        fn triggers."""
        original = self.gui_mod.checkerboard_pixmap
        calls = []

        def counting(image, scale, smooth):
            calls.append(smooth)
            return original(image, scale, smooth)

        with mock.patch.object(self.gui_mod, "checkerboard_pixmap",
                               counting):
            fn()
        return sum(1 for smooth in calls if smooth)

    def test_second_refresh_reuses_the_original_pixmap(self):
        self.win._before_pixmap_cache = None
        self.assertEqual(self._count_before_builds(
            self.win._refresh_pixmaps), 1)
        self.assertEqual(self._count_before_builds(
            self.win._refresh_pixmaps), 0)
        self.assertIsNotNone(self.win.before_label.pixmap())

    def test_new_source_rebuilds_the_original_pixmap(self):
        from pixel_forge.image import Image
        self.win._refresh_pixmaps()
        self.win.source_image = Image(32, 32)
        self.assertEqual(self._count_before_builds(
            self.win._refresh_pixmaps), 1)
        self.assertEqual(self._count_before_builds(
            self.win._refresh_pixmaps), 0)

    def test_prompt_original_pixmap_cached(self):
        self.win.prompt_keeper_image = make_gradient_image()
        self.win._prompt_before_pixmap_cache = None
        self.assertEqual(self._count_before_builds(
            self.win._refresh_prompt_pixmaps), 1)
        self.assertEqual(self._count_before_builds(
            self.win._refresh_prompt_pixmaps), 0)
        self.assertIsNotNone(self.win.prompt_before_label.pixmap())

    def test_prompt_new_keeper_rebuilds_the_original_pixmap(self):
        from pixel_forge.image import Image
        self.win.prompt_keeper_image = make_gradient_image()
        self.win._refresh_prompt_pixmaps()
        self.win.prompt_keeper_image = Image(32, 32)
        self.assertEqual(self._count_before_builds(
            self.win._refresh_prompt_pixmaps), 1)
        self.assertEqual(self._count_before_builds(
            self.win._refresh_prompt_pixmaps), 0)


class AutosaveGuiTests(unittest.TestCase):
    """Task 55: autosave — crash recovery for in-progress work.

    A "crash" is simulated by overwriting the running marker with
    a foreign pid: that is exactly what a crashed run leaves
    behind (a same-process marker is a sibling window, not a
    crash). The recovery dialog is stubbed with a lambda on the
    window under test, so no modal ever blocks the run.
    """

    FOREIGN_PID = 999999999

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.image = write_image(
            make_solid_image(64, 64, 10, 200, 30),
            os.path.join(cls.tmp, "first.png"))
        cls.image2 = write_image(
            make_solid_image(48, 48, 200, 30, 10),
            os.path.join(cls.tmp, "second.png"))
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n")
        cls.base = write_image(
            make_solid_image(16, 16, 30, 200, 30),
            os.path.join(cls.tmp, "base.png"))
        cls.clip = None
        try:
            cls.clip = make_test_video(
                os.path.join(cls.tmp, "clip.mp4"), duration=2)
        except Exception:
            pass  # video test skips below when ffmpeg is missing

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self.auto_dir = os.path.join(tempfile.mkdtemp(), "auto")
        self.win = MainWindow(library_dir=self.lib_dir,
                              restore=True,
                              autosave_dir=self.auto_dir)
        self._extra_windows = []

    def tearDown(self):
        for extra in self._extra_windows:
            extra.close()
            extra.deleteLater()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    # -- helpers -------------------------------------------------

    def _show(self, win=None):
        win = win or self.win
        win.show()
        self.app.processEvents()
        return win

    def _crash(self):
        """Leave the crash behind: the marker of a dead process."""
        from pixel_forge.autosave import mark_running
        mark_running(self.auto_dir, pid=self.FOREIGN_PID)

    def _revived_window(self, answer, lib_dir=None):
        """A new window over the same autosave folder, with the
        recovery dialog stubbed to answer True (bring back) or
        False (start fresh)."""
        from pixel_forge.gui import MainWindow
        win = MainWindow(library_dir=lib_dir or self.lib_dir,
                         restore=True,
                         autosave_dir=self.auto_dir)
        win._ask_recovery = lambda summary: answer
        self._extra_windows.append(win)
        return self._show(win)

    def _wait_for_video(self, win, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (win.video_worker is None
                    and not win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _recovery_state(self):
        from pixel_forge.autosave import load_recovery
        state, corrupt = load_recovery(self.auto_dir)
        self.assertFalse(corrupt)
        return state

    # -- clean exit / no crash -----------------------------------

    def test_clean_exit_removes_marker_and_recovery(self):
        from pixel_forge.autosave import (
            crashed, has_recovery, marker_path)
        self._show()
        self.win.load_image(self.image)
        self.win._autosave_write_now()
        self.assertTrue(marker_path(self.auto_dir).exists())
        self.assertTrue(has_recovery(self.auto_dir))
        self.win.close()
        self.assertFalse(marker_path(self.auto_dir).exists())
        self.assertFalse(has_recovery(self.auto_dir))
        self.assertFalse(crashed(self.auto_dir))

    def test_second_window_in_this_process_is_not_a_crash(self):
        from pixel_forge.gui import MainWindow
        self._show()
        win2 = MainWindow(library_dir=self.lib_dir,
                          restore=True,
                          autosave_dir=self.auto_dir)
        self._extra_windows.append(win2)
        self.assertFalse(win2._autosave_crashed)
        self.assertFalse(win2._autosave_corrupt)

    def test_crash_with_no_work_offers_nothing(self):
        from pixel_forge.gui import MainWindow
        self._show()
        self._crash()
        win2 = MainWindow(library_dir=self.lib_dir,
                          restore=True,
                          autosave_dir=self.auto_dir)

        def _must_not_ask(summary):
            raise AssertionError("no dialog when nothing was open")

        win2._ask_recovery = _must_not_ask
        self._extra_windows.append(win2)
        self._show(win2)
        self.assertIsNone(win2.source_path)

    # -- image restore -------------------------------------------

    def test_crash_accept_restores_image_and_settings(self):
        self._show()
        self.win.load_image(self.image)
        self.win.width_spin.setValue(48)
        self.win.height_spin.setValue(48)
        self.win.tabs.setCurrentIndex(0)
        self.win._autosave_write_now()
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertEqual(win2.source_path, self.image)
        self.assertEqual(win2.width_spin.value(), 48)
        self.assertEqual(win2.height_spin.value(), 48)
        self.assertEqual(win2.tabs.currentIndex(), 0)
        self.assertIsNotNone(win2.result_image)

    def test_recovery_file_survives_a_restore(self):
        from pixel_forge.autosave import has_recovery
        self._show()
        self.win.load_image(self.image)
        self.win._autosave_write_now()
        self._crash()
        self._revived_window(answer=True)
        # A second crash during the restore must not lose it.
        self.assertTrue(has_recovery(self.auto_dir))

    def test_decline_discards_recovery_and_falls_back_to_session(self):
        from pixel_forge.autosave import has_recovery
        self._show()
        self.win.load_image(self.image)
        self.win._autosave_write_now()
        self._crash()
        win2 = self._revived_window(answer=False)
        self.assertFalse(has_recovery(self.auto_dir))
        # The session (same library folder) still restores the
        # last-loaded image — recovery is an extra, not a
        # replacement.
        self.assertEqual(win2.source_path, self.image)

    def test_edited_result_round_trips_its_pixels(self):
        from pixel_forge.autosave import recovery_image_path
        self._show()
        self.win.load_image(self.image)
        layer_image = self.win._layers[0].image
        layer_image.data[0:4] = bytes((9, 9, 9, 255))
        self.win._refresh_result()
        self.assertTrue(self.win._result_edited)
        self.win._autosave_write_now()
        self.assertTrue(recovery_image_path(self.auto_dir).exists())
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertTrue(win2._result_edited)
        self.assertEqual(bytes(win2.result_image.data[0:4]),
                         bytes((9, 9, 9, 255)))

    def test_procedural_sprite_restores_by_seed(self):
        from pixel_forge import procedural
        self._show()
        combo = self.win.procedural_kind_combo
        combo.setCurrentIndex(combo.findData("tree"))
        self.win.procedural_seed_spin.setValue(42)
        self.win._on_generate_procedural()
        self.assertEqual(self.win._generated_kind, "tree")
        # The tab's width moved on past the generated size:
        # the recovery must record the sprite's own size.
        self.win.width_spin.setValue(48)
        self.win.height_spin.setValue(48)
        self.win._autosave_write_now()
        state = self._recovery_state()
        self.assertEqual(state["image"]["generated"]["kind"],
                         "tree")
        self.assertEqual(state["image"]["generated"]["size"], 32)
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertEqual(win2._generated_kind, "tree")
        self.assertEqual(win2._generated_seed, 42)
        self.assertEqual(win2.source_image.width(), 32)
        self.assertEqual(win2.width_spin.value(), 48)
        expected = procedural.generate("tree", 42, size=32)
        self.assertEqual(bytes(win2.source_image.data),
                         bytes(expected.data))

    # -- corrupt recovery ----------------------------------------

    def test_corrupt_recovery_starts_fresh_without_a_dialog(self):
        from pixel_forge.autosave import recovery_path
        self._show()
        self.win.load_image(self.image)
        self.win._autosave_write_now()
        path = recovery_path(self.auto_dir)
        path.write_text("{broken", encoding="utf-8")
        self._crash()
        from pixel_forge.gui import MainWindow
        # A fresh library folder: the session would restore the
        # image on its own, and this test is about the recovery
        # path, not the session fallback.
        win2 = MainWindow(
            library_dir=os.path.join(tempfile.mkdtemp(), "lib"),
            restore=True,
            autosave_dir=self.auto_dir)

        def _must_not_ask(summary):
            raise AssertionError("no dialog on corrupt recovery")

        win2._ask_recovery = _must_not_ask
        self._extra_windows.append(win2)
        self._show(win2)
        self.assertTrue(win2._autosave_corrupt)
        self.assertIn("recovery file could not be read",
                      win2.statusBar().currentMessage())
        self.assertIsNone(win2.source_path)

    # -- write gating --------------------------------------------

    def test_no_write_when_nothing_changed(self):
        import pixel_forge.gui as gui_mod
        self._show()
        self.win.load_image(self.image)
        calls = []
        real = gui_mod.save_recovery

        def counting(directory, state):
            calls.append(1)
            return real(directory, state)

        with mock.patch.object(gui_mod, "save_recovery",
                               side_effect=counting):
            self.win._autosave_write_now()
            self.win._autosave_write_now()
            self.win._autosave_write_now()
        self.assertEqual(len(calls), 1)

    def test_debounce_timer_writes_after_two_seconds(self):
        from pixel_forge.autosave import has_recovery
        self._show()
        self.win.load_image(self.image)
        self.assertFalse(has_recovery(self.auto_dir))
        # A control change marks the state dirty; the timer
        # writes it on its own, with no explicit flush.
        self.win.width_spin.setValue(24)
        deadline = time.monotonic() + 6
        while (time.monotonic() < deadline
               and not has_recovery(self.auto_dir)):
            self.app.processEvents()
            time.sleep(0.05)
        self.assertTrue(has_recovery(self.auto_dir))

    # -- video / prompt / code / tileset restore -----------------

    def test_video_restore_brings_back_keep_and_drop(self):
        if self.clip is None:
            self.skipTest("ffmpeg not available")
        self._show()
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video(self.win))
        self.assertGreater(len(self.win.video_frames), 1)
        self.win.video_keep[1] = False
        self.win._refresh_after_keep_change()
        self.win._autosave_write_now()
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertEqual(win2.video_path, self.clip)
        self.assertTrue(self._wait_for_video(win2))
        self.assertEqual(len(win2.video_frames),
                         len(self.win.video_frames))
        self.assertFalse(win2.video_keep[1])
        self.assertTrue(win2.video_keep[0])
        self.assertIn("Brought back your video work",
                      win2.statusBar().currentMessage())

    def test_prompt_restore_brings_back_batch_and_typed_prompt(self):
        from pixel_forge.imagegen import (
            load_batch, new_batch, save_batch, set_keeper)
        self._show()
        batch, bdir = new_batch(
            os.path.join(self.tmp, "batches"),
            "a wooden stool", count=2)
        for i, name in enumerate(("cand-01.png", "cand-02.png")):
            write_image(make_solid_image(64, 64, 30 + i * 40,
                                         100, 60),
                        str(bdir / name))
            batch["candidates"].append({"file": name,
                                        "seed": 100 + i,
                                        "status": "pending"})
        save_batch(bdir, batch)
        set_keeper(bdir, "cand-02.png")
        batch = load_batch(bdir)
        self.win._load_prompt_batch(batch, bdir,
                                    batch["candidates"])
        # The typed prompt moved on past the batch's own prompt.
        self.win.prompt_edit.setText("a wooden chair")
        self.win._autosave_write_now()
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertEqual(str(win2.prompt_batch_dir), str(bdir))
        self.assertEqual(win2.prompt_edit.text(),
                         "a wooden chair")
        self.assertEqual(win2.prompt_keeper, "cand-02.png")
        self.assertEqual(len(win2.prompt_candidates), 2)

    def test_code_restore_brings_back_import_and_asset(self):
        self._show()
        self.win.load_code([self.game])
        self.assertEqual(len(self.win.code_assets), 2)
        self.win._show_code_asset(1)
        self.win._autosave_write_now()
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertEqual(len(win2.code_assets), 2)
        self.assertEqual(win2.code_current, 1)
        self.assertEqual(win2.code_kind, "code")

    def test_tileset_restore_regenerates_the_set(self):
        self._show()
        combo = self.win.tileset_mode_combo
        combo.setCurrentIndex(combo.findData("blob"))
        shape = self.win.tileset_shape_combo
        shape.setCurrentIndex(shape.findData("rounded"))
        self.assertTrue(self.win.load_tileset_base(self.base, "a"))
        self.win._autosave_write_now()
        self._crash()
        win2 = self._revived_window(answer=True)
        self.assertEqual(win2.tileset_mode, "blob")
        self.assertEqual(win2.tileset_shape, "rounded")
        self.assertEqual(win2.tileset_base_a_path, self.base)
        self.assertIsNotNone(win2.tileset_tiles)
        self.assertEqual(len(win2.tileset_tiles), 47)


class FieldExplanationTests(unittest.TestCase):
    """Task 89 (plain-words field explanations, U20): every
    field on the Image and Video tabs explains itself in one
    plain line directly underneath it."""

    # key -> (attribute of the control it explains, offset).
    # The offset is how many layout items after the control
    # (or its row) the line sits: 1 normally, 2 for the second
    # line under a shared row (the seed row holds the seed and
    # the size, each with its own line).
    EXPECTED = {
        # Image tab
        "normalmap_strength_spin": ("normalmap_strength_spin", 1),
        "procedural_kind_combo": ("procedural_kind_combo", 1),
        "procedural_seed_spin": ("procedural_seed_spin", 1),
        "procedural_size_spin": ("procedural_size_spin", 2),
        "size_combo": ("size_combo", 1),
        "image_size_row": ("width_spin", 1),
        "aspect_check": ("aspect_check", 1),
        "palette_combo": ("palette_combo", 1),
        "colors_spin": ("colors_spin", 1),
        "dither_check": ("dither_check", 1),
        "preset_combo": ("preset_combo", 1),
        "zoom_slider": ("zoom_slider", 1),
        "layer_list": ("layer_list", 1),
        # Video tab
        "video_fps_combo": ("video_fps_combo", 1),
        "video_every_spin": ("video_every_spin", 1),
        "video_start_edit": ("video_start_edit", 1),
        "video_end_edit": ("video_end_edit", 1),
        "video_size_combo": ("video_size_combo", 1),
        "video_size_row": ("video_width_spin", 1),
        "video_aspect_check": ("video_aspect_check", 1),
        "video_palette_combo": ("video_palette_combo", 1),
        "video_colors_spin": ("video_colors_spin", 1),
        "video_dither_check": ("video_dither_check", 1),
        "video_preset_combo": ("video_preset_combo", 1),
        "video_zoom_slider": ("video_zoom_slider", 1),
        "sheet_cols_combo": ("sheet_cols_combo", 1),
        "sheet_padding_spin": ("sheet_padding_spin", 1),
        "sheet_fps_spin": ("sheet_fps_spin", 1),
        "sheet_pack_combo": ("sheet_pack_combo", 1),
        "sheet_trim_check": ("sheet_trim_check", 1),
        "sheet_atlas_padding_spin": ("sheet_atlas_padding_spin", 1),
        "sheet_extrude_spin": ("sheet_extrude_spin", 1),
        "video_tag_list": ("video_tag_list", 1),
    }

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _item_index(self, layout, widget):
        """Index of the layout item holding a widget — directly,
        or inside a child row layout."""
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.widget() is widget:
                return i
            child = item.layout()
            if child is not None:
                for j in range(child.count()):
                    if child.itemAt(j).widget() is widget:
                        return i
        return -1

    def test_every_field_has_a_line_and_no_line_is_stray(self):
        # The registry is the coverage list: a field without a
        # line is a missing key, a line without a field is a
        # stray one.
        self.assertEqual(set(self.win.field_explanations),
                         set(self.EXPECTED))

    def test_lines_are_plain_words(self):
        # One short sentence, plain words, no em-dash, no "(s)".
        for key, label in self.win.field_explanations.items():
            text = label.text()
            self.assertTrue(text, key)
            self.assertEqual(label.objectName(), "explanation",
                             key)
            self.assertTrue(label.wordWrap(), key)
            self.assertNotIn("\u2014", text, key)
            self.assertNotIn("(s)", text, key)
            self.assertTrue(text.endswith("."), key)
            self.assertLessEqual(len(text), 70, key)

    def test_lines_sit_directly_under_their_fields(self):
        # The line is the next item after its field (or its
        # field's row) in the same layout — not somewhere
        # nearby in the column.
        for key, (attr, offset) in self.EXPECTED.items():
            label = self.win.field_explanations[key]
            layout = label.parent().layout()
            self.assertIsNotNone(layout, key)
            control = getattr(self.win, attr)
            control_index = self._item_index(layout, control)
            self.assertGreaterEqual(control_index, 0, key)
            self.assertEqual(layout.indexOf(label),
                             control_index + offset, key)

    def test_the_tasks_named_fields_say_what_they_do(self):
        # The five fields the task names by name, pinned word
        # for word — these are the ones a user trips over.
        texts = {key: label.text() for key, label in
                 self.win.field_explanations.items()}
        self.assertEqual(
            texts["video_fps_combo"],
            "How many frames to take from each second of video.")
        self.assertEqual(
            texts["video_every_spin"],
            "Keeps one frame in every N. Higher means fewer "
            "frames.")
        self.assertEqual(
            texts["colors_spin"],
            "The most colors the sprite may use. Fewer is "
            "chunkier.")
        self.assertEqual(
            texts["dither_check"],
            "Fakes shades between colors with a checker "
            "pattern.")
        self.assertEqual(
            texts["size_combo"],
            "The size of the finished sprite, in pixels.")

    def test_prompt_tab_has_no_explanation(self):
        # The Prompt tab is out of this build's scope — the
        # shared preset helper only adds the line for the
        # Image and Video tabs.
        self.assertNotIn("prompt_preset_combo",
                         self.win.field_explanations)

    def test_lines_do_not_widen_the_column(self):
        # A word-wrapped label's size hint is its unwrapped
        # text width — left alone, the longest line stretches
        # the whole column past the 268 px scroll area and the
        # right edge of every widget clips (the seed line's
        # tail and the preset buttons vanished this way before
        # the fix). Both columns' inner widgets must fit their
        # viewports, and every line must be tall enough for
        # its wrapped text. Each tab is checked with that tab
        # current — a hidden tab's widgets keep stale sizes.
        # The dark stylesheet is applied for the check: the
        # app always runs styled, and an unstyled window
        # leaves the hidden tab's wrapped labels one line
        # tall (their heights settle only once the style
        # polishes them).
        from PySide6.QtCore import QRect
        from pixel_forge.gui import DARK_STYLESHEET
        image_keys = [key for key in self.EXPECTED
                      if not key.startswith("video_")
                      and not key.startswith("sheet_")]
        video_keys = [key for key in self.EXPECTED
                      if key not in image_keys]
        previous_sheet = self.app.styleSheet()
        self.app.setStyleSheet(DARK_STYLESHEET)
        try:
            self._check_column_widths(image_keys, video_keys)
        finally:
            self.app.setStyleSheet(previous_sheet)

    def _check_column_widths(self, image_keys, video_keys):
        self.win.resize(1020, 640)
        self.win.show()
        self.app.processEvents()
        image_scroll = None
        for scroll in self.win.findChildren(QScrollArea):
            if scroll.widget() is self.win.import_btn.parent():
                image_scroll = scroll
        self.assertIsNotNone(image_scroll)
        for index, scroll, keys in (
                (0, image_scroll, image_keys),
                (1, self.win.video_controls_scroll, video_keys)):
            if index == 1:
                for section in self.win.video_sections.values():
                    section.setExpanded(True)
            self.win.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertLessEqual(
                scroll.widget().width(), scroll.viewport().width(),
                "tab %d column wider than its viewport" % index)
            for key in keys:
                label = self.win.field_explanations[key]
                metrics = label.fontMetrics()
                needed = metrics.boundingRect(
                    QRect(0, 0, label.width(), 10000),
                    Qt.TextFlag.TextWordWrap, label.text())
                self.assertGreaterEqual(label.height(),
                                        needed.height(), key)

    def test_lines_are_visible_when_the_window_is_shown(self):
        # The window is shown first: with a hidden window every
        # isVisible() reads False, so the check would pass
        # without proving anything. Each tab's lines are
        # checked with that tab current — a label on a tab
        # that is not showing reads hidden, whatever its
        # section does. The Video sections start collapsed
        # (except Frames and Export), so they are all opened
        # first.
        for section in self.win.video_sections.values():
            section.setExpanded(True)
        self.win.resize(1020, 640)
        self.win.show()
        self.app.processEvents()
        image_keys = [key for key in self.EXPECTED
                      if not key.startswith("video_")
                      and not key.startswith("sheet_")]
        self.win.tabs.setCurrentIndex(0)  # Image tab
        self.app.processEvents()
        for key in image_keys:
            self.assertTrue(
                self.win.field_explanations[key].isVisible(),
                key)
        self.win.tabs.setCurrentIndex(1)  # Video tab
        self.app.processEvents()
        for key in self.EXPECTED:
            if key not in image_keys:
                self.assertTrue(
                    self.win.field_explanations[key].isVisible(),
                    key)


class GridTests(unittest.TestCase):
    """Task 88 (v0.81) — the pixel-grid motif: a stylesheet
    background-image on the lists, the scroll viewports, and
    the tab columns. The grid is a rule, so it survives
    resize, hide/show, and theme switches with zero
    re-application — these tests prove that on the real
    window, by counting grid-colored pixels in window grabs.
    """

    DARK_GRID = (0x2E, 0x2E, 0x36)
    LIGHT_GRID = (0xD8, 0xD8, 0xD2)

    @classmethod
    def setUpClass(cls):
        # The tiles are PNGs, so they ship in the release zip
        # and not in the git push — a fresh clone has no tiles,
        # and the whole class skips there instead of failing
        # (same pattern as test_installer's icon skip).
        from pixel_forge.gui_shared import (_GRID_TILE_DARK,
                                            _GRID_TILE_LIGHT)
        if not _GRID_TILE_DARK or not _GRID_TILE_LIGHT:
            raise unittest.SkipTest(
                "grid tiles not present (tiles ship in the "
                "release zip, not the git push)")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import DARK_STYLESHEET, MainWindow
        self.app.setStyleSheet(DARK_STYLESHEET)
        self.lib_dir = tempfile.mkdtemp()
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.show()
        self._settle()

    def tearDown(self):
        from pixel_forge.gui import DARK_STYLESHEET
        self.win.close()
        self.win.deleteLater()
        self.app.setStyleSheet(DARK_STYLESHEET)
        self.app.processEvents()
        shutil.rmtree(self.lib_dir, ignore_errors=True)

    def _settle(self):
        from PySide6.QtTest import QTest
        QTest.qWait(200)
        self.app.processEvents()

    def _grid_pixels(self, widget, color):
        """Grid-colored pixels in a widget's region of the
        window grab (sampled every 2 px). The widget must be
        visible — grab the window, never the viewport
        directly: a viewport grab under offscreen can come
        back blank (proven in the v0.81 probes)."""
        from PySide6.QtCore import QPoint
        top_left = widget.mapTo(self.win, QPoint(0, 0))
        img = self.win.grab().toImage()
        # A scroll column is taller than the window — clamp
        # the sample region to the grab, or pixelColor warns
        # for every out-of-range coordinate.
        x0, y0 = top_left.x() + 3, top_left.y() + 3
        x1 = min(top_left.x() + widget.width() - 3, img.width())
        y1 = min(top_left.y() + widget.height() - 3, img.height())
        found = 0
        for y in range(y0, y1, 2):
            for x in range(x0, x1, 2):
                pixel = img.pixelColor(x, y)
                if (pixel.red(), pixel.green(),
                        pixel.blue()) == color:
                    found += 1
        return found

    def _visible_column(self):
        """The current tab's column widget — the only one
        visible. Every tab column is named "column" by the
        shared _scrollable_column helper."""
        visible = [child for child in
                   self.win.findChildren(QWidget, "column")
                   if child.isVisibleTo(self.win)]
        self.assertEqual(len(visible), 1,
                         "%d columns visible, expected 1"
                         % len(visible))
        return visible[0]

    def _seed_library(self):
        """One sprite in the library, so the Library stack
        shows the list instead of the "No sprites yet" label
        — an empty library hides the list entirely, and the
        grid with it."""
        self.win.library.add_image(
            make_solid_image(32, 32, 255, 0, 0), "red")
        self.win._refresh_library()
        self._settle()

    def test_grid_renders_on_library_list(self):
        self.win.tabs.setCurrentIndex(4)
        self._seed_library()
        found = self._grid_pixels(
            self.win.library_list.viewport(), self.DARK_GRID)
        self.assertGreaterEqual(
            found, 200, "library list shows %d grid pixels" % found)

    def test_grid_renders_on_image_column(self):
        self.win.tabs.setCurrentIndex(0)
        self._settle()
        found = self._grid_pixels(
            self._visible_column(), self.DARK_GRID)
        self.assertGreaterEqual(
            found, 50, "image column shows %d grid pixels" % found)

    def test_grid_shows_through_video_sections(self):
        # The Video tab's sections are transparent, so the
        # column grid shows through as one continuous grid —
        # it must be visible INSIDE an expanded section body,
        # not just in the gaps between sections.
        self.win.tabs.setCurrentIndex(1)
        self._settle()
        frames = self.win.video_sections["frames"]
        self.assertTrue(frames.isExpanded())
        found = self._grid_pixels(frames._body, self.DARK_GRID)
        self.assertGreaterEqual(
            found, 30,
            "frames section body shows %d grid pixels" % found)

    def test_grid_survives_resize_hide_show_and_theme_switch(self):
        # The grid is a stylesheet rule, not applied state:
        # resize, hide/show, and a theme switch must not lose
        # it, and the light theme must show the LIGHT tile.
        self.win.tabs.setCurrentIndex(4)
        self._seed_library()
        viewport = self.win.library_list.viewport()
        found = self._grid_pixels(viewport, self.DARK_GRID)
        self.assertGreaterEqual(
            found, 200, "no dark grid at start: %d" % found)
        self.win.resize(900, 560)
        self._settle()
        found = self._grid_pixels(viewport, self.DARK_GRID)
        self.assertGreaterEqual(
            found, 200, "grid lost on resize: %d" % found)
        self.win.hide()
        self._settle()
        self.win.show()
        self._settle()
        found = self._grid_pixels(viewport, self.DARK_GRID)
        self.assertGreaterEqual(
            found, 200, "grid lost on hide/show: %d" % found)
        self.win._light_theme_action.trigger()
        self._settle()
        found = self._grid_pixels(viewport, self.LIGHT_GRID)
        self.assertGreaterEqual(
            found, 200, "no light grid after theme switch: %d"
            % found)
        self.win._light_theme_action.trigger()
        self._settle()
        found = self._grid_pixels(viewport, self.DARK_GRID)
        self.assertGreaterEqual(
            found, 200, "no dark grid after switching back: %d"
            % found)

    def test_grid_tile_urls_are_absolute_and_exist(self):
        # The v0.79 failure: a relative tile path never
        # resolves (Qt resolves url() against the working
        # directory). The evaluated stylesheets must carry
        # absolute paths to files that exist — no placeholder
        # left behind.
        import re
        from pixel_forge.gui import (DARK_STYLESHEET,
                                     LIGHT_STYLESHEET)
        for name, sheet in (("dark", DARK_STYLESHEET),
                            ("light", LIGHT_STYLESHEET)):
            self.assertNotIn("__GRID_TILE_", sheet,
                             "%s stylesheet still has the tile "
                             "placeholder" % name)
            urls = re.findall(r'url\("([^"]+)"\)', sheet)
            self.assertEqual(len(urls), 3,
                             "%s stylesheet has %d tile urls, "
                             "expected 3" % (name, len(urls)))
            for url in urls:
                self.assertTrue(os.path.isabs(url),
                                "%s tile url is relative: %s"
                                % (name, url))
                self.assertTrue(os.path.isfile(url),
                                "%s tile url is missing: %s"
                                % (name, url))


class ActivityTests(unittest.TestCase):
    """Task 90 (U16/U26): the status-bar activity indicator —
    a square accent dot, a short plain-words line, and a Cancel
    button where the operation can stop safely. Runner group
    g12."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.tmp = tempfile.mkdtemp()
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(1)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _video_clip(self):
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            self.skipTest("ffmpeg not installed")
        return make_test_video(os.path.join(self.tmp, "clip.mp4"))

    def test_indicator_hidden_at_startup(self):
        self.assertTrue(self.win._activity_dot.isHidden())
        self.assertTrue(self.win._activity_text.isHidden())
        self.assertTrue(self.win._activity_cancel.isHidden())
        self.assertEqual(self.win._activity_cancel.text(), "Cancel")
        self.assertTrue(self.win._activity_cancel.toolTip())

    def test_start_update_finish(self):
        calls = []
        self.win._activity_start("test", "Doing a test thing…",
                                 cancel=lambda: calls.append(1))
        self.app.processEvents()
        self.assertTrue(self.win._activity_dot.isVisible())
        self.assertTrue(self.win._activity_text.isVisible())
        self.assertEqual(self.win._activity_text.text(),
                         "Doing a test thing…")
        self.assertTrue(self.win._activity_cancel.isVisible())
        self.assertTrue(self.win._activity_cancel.isEnabled())
        self.win._activity_update("test",
                                  "Doing a test thing… 3 of 9")
        self.assertEqual(self.win._activity_text.text(),
                         "Doing a test thing… 3 of 9")
        # The Cancel button calls the operation's own cancel
        # slot once, then disables itself so a second press
        # cannot double-cancel.
        self.win._activity_cancel.click()
        self.assertEqual(calls, [1])
        self.assertFalse(self.win._activity_cancel.isEnabled())
        self.win._activity_cancel.click()
        self.assertEqual(calls, [1])
        self.win._activity_finish("test")
        self.assertTrue(self.win._activity_dot.isHidden())
        self.assertTrue(self.win._activity_text.isHidden())
        self.assertTrue(self.win._activity_cancel.isHidden())

    def test_newest_operation_shows_finish_reveals_older(self):
        self.win._activity_start("first", "First thing…")
        self.win._activity_start("second", "Second thing…")
        self.assertEqual(self.win._activity_text.text(),
                         "Second thing…")
        self.win._activity_finish("second")
        self.assertEqual(self.win._activity_text.text(),
                         "First thing…")
        self.assertTrue(self.win._activity_dot.isVisible())
        self.win._activity_finish("first")
        self.assertTrue(self.win._activity_dot.isHidden())

    def test_stale_update_and_finish_ignored(self):
        # An update or finish for an operation that is not
        # running (a superseded worker's late signal) must not
        # crash and must not touch the running operation.
        self.win._activity_start("live", "Live thing…")
        self.win._activity_update("gone", "Gone thing…")
        self.win._activity_finish("gone")
        self.assertEqual(self.win._activity_text.text(),
                         "Live thing…")
        self.assertTrue(self.win._activity_dot.isVisible())
        self.win._activity_finish("live")

    def test_no_cancel_button_for_uncancellable_op(self):
        self.win._activity_start("busy", "Busy thing…")
        self.app.processEvents()
        self.assertTrue(self.win._activity_dot.isVisible())
        self.assertTrue(self.win._activity_cancel.isHidden())
        self.win._activity_finish("busy")

    def test_message_area_borrowed_and_restored(self):
        # The indicator borrows the message area while work runs
        # — a stale message must not sit beside the live
        # indicator disagreeing with it — and gives it back
        # when the last operation finishes, so a quiet
        # operation leaves the status bar exactly as it was.
        self.win.statusBar().showMessage("Ready to work.")
        self.win._activity_start("first", "First thing…")
        self.assertEqual(self.win.statusBar().currentMessage(), "")
        # A second operation starting does not clobber the
        # saved message, and finishing it does not restore
        # early while the first is still running.
        self.win._activity_start("second", "Second thing…")
        self.win._activity_finish("second")
        self.assertEqual(self.win.statusBar().currentMessage(), "")
        self.win._activity_finish("first")
        self.assertEqual(self.win.statusBar().currentMessage(),
                         "Ready to work.")

    def test_video_run_shows_progress_counts(self):
        import re
        clip = self._video_clip()
        # 48 frames at 128 px: slow enough to observe under
        # load, fast enough to finish inside the timeout.
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(24))
        self.win.video_width_spin.setValue(128)
        self.win.video_height_spin.setValue(128)
        self.assertTrue(self.win.load_video(clip))
        seen = []
        deadline = time.monotonic() + 60
        progress = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self.win._activity_dot.isVisible():
                text = self.win._activity_text.text()
                seen.append(text)
                if re.search(r"Pixelizing frame \d+ of \d+", text):
                    progress = text
                    break
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                break
            time.sleep(0.02)
        self.assertIsNotNone(
            progress,
            "no determinate progress text seen; indicator texts "
            "were: %r" % seen[:20])
        self.assertTrue(self._wait_for_video(timeout=60))
        self.assertTrue(self.win._activity_dot.isHidden())
        self.assertTrue(self.win._activity_text.isHidden())
        self.assertIn(len(self.win.video_frames), (47, 48))

    def test_video_cancel_from_indicator_stops_run(self):
        import re
        clip = self._video_clip()
        self.assertTrue(self.win.load_video(clip))
        self.assertTrue(self._wait_for_video())
        frames_before = len(self.win.video_frames)
        self.assertIn(frames_before, (15, 16))
        # A slow reload: 48 frames at 128 px.
        self.win.video_fps_combo.setCurrentIndex(
            self.win.video_fps_combo.findData(24))
        self.win.video_width_spin.setValue(128)
        self.win.video_height_spin.setValue(128)
        deadline = time.monotonic() + 60
        clicked = False
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self.win._activity_dot.isVisible():
                match = re.search(
                    r"Pixelizing frame (\d+) of (\d+)",
                    self.win._activity_text.text())
                if match and int(match.group(1)) < int(match.group(2)) - 5:
                    # At least 5 frames still to go — the cancel
                    # lands mid-run, not on the last frame.
                    self.assertTrue(
                        self.win._activity_cancel.isVisible())
                    self.win._activity_cancel.click()
                    clicked = True
                    break
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                break
            time.sleep(0.02)
        self.assertTrue(clicked,
                        "the slow run never showed progress with "
                        "frames still to go")
        self.assertTrue(self._wait_for_video(timeout=60))
        self.assertIsNone(self.win.video_worker)
        self.assertTrue(self.win._activity_dot.isHidden())
        self.assertTrue(self.win._activity_text.isHidden())
        # The old frames are unchanged by a cancelled run.
        self.assertEqual(len(self.win.video_frames), frames_before)
        self.assertIn("cancelled",
                      self.win.statusBar().currentMessage().lower())

    def test_export_frames_shows_progress_mid_run(self):
        from PySide6.QtCore import QTimer
        clip = self._video_clip()
        self.assertTrue(self.win.load_video(clip))
        self.assertTrue(self._wait_for_video())
        out = os.path.join(self.tmp, "frames")
        seen = []

        def record():
            seen.append((self.win._activity_text.text(),
                         self.win._activity_dot.isVisible()))

        # Fires inside the export's own processEvents pass —
        # the timer cannot fire before the call starts, because
        # nothing pumps the loop in between.
        QTimer.singleShot(0, record)
        self.assertTrue(self.win.export_frames(out))
        self.assertTrue(
            any("Exporting frames" in text and visible
                for text, visible in seen),
            "indicator never showed during the export; saw %r"
            % seen)
        self.assertTrue(self.win._activity_dot.isHidden())
        self.assertTrue(self.win._activity_text.isHidden())
        self.assertEqual(len(os.listdir(out)),
                         len(self.win.video_frames))

    def test_tiny_export_leaves_indicator_hidden(self):
        clip = self._video_clip()
        self.assertTrue(self.win.load_video(clip))
        self.assertTrue(self._wait_for_video())
        # Keep one frame: the export is instant, and the
        # indicator must not stick around afterwards.
        self.win.video_keep = (
            [True] + [False] * (len(self.win.video_frames) - 1))
        out = os.path.join(self.tmp, "one")
        self.assertTrue(self.win.export_frames(out))
        self.assertEqual(os.listdir(out), ["frame_001.png"])
        self.assertTrue(self.win._activity_dot.isHidden())
        self.assertTrue(self.win._activity_text.isHidden())
        self.assertTrue(self.win._activity_cancel.isHidden())


class CompareHoverTests(unittest.TestCase):
    """Task 91 (U16/U34): hold to compare + hover to preview.
    A visible "Hold to see the original" button on the Image
    and Video tabs shows the original while held and restores
    the result on release; hovering a preset or palette in its
    picker previews its cached variant without applying it,
    and the hover never renders. Runner group g14."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.tmp = tempfile.mkdtemp()
        self.win = MainWindow()
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _load_image(self):
        src = write_image(make_gradient_image(96, 96),
                          os.path.join(self.tmp, "photo.png"))
        self.assertTrue(self.win.load_image(src))
        self.app.processEvents()
        return src

    def _load_video(self):
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            self.skipTest("ffmpeg not installed")
        clip = make_test_video(os.path.join(self.tmp, "clip.mp4"),
                               duration=1, fps=8, width=80,
                               height=60)
        self.win.tabs.setCurrentIndex(1)
        self.assertTrue(self.win.load_video(clip))
        self.assertTrue(self._wait_for_video())
        return clip

    def _hover(self, combo, row):
        """Drive one real hover: the popup view's entered
        signal, the way the mouse delivers it."""
        index = combo.view().model().index(row, 0)
        combo.view().entered.emit(index)
        self.app.processEvents()

    # --------------------------------------------------- the hint

    def test_compare_buttons_are_the_hint(self):
        # The button text says what holding it does — the
        # gesture can be found, not guessed. Plain words, no
        # em-dash, a tooltip on each, quiet until there is
        # something to compare.
        for button in (self.win.compare_btn,
                       self.win.video_compare_btn):
            self.assertEqual(button.text(),
                             "Hold to see the original")
            self.assertNotIn("\u2014", button.text())
            self.assertNotIn("\u2014", button.toolTip())
            self.assertTrue(button.toolTip())
            self.assertFalse(button.isEnabled())
        self._load_image()
        self.assertTrue(self.win.compare_btn.isEnabled())

    # --------------------------------------------------- image hold

    def test_image_hold_shows_original_release_restores(self):
        self._load_image()
        result = self.win.after_label.pixmap().toImage()
        self.win.compare_btn.pressed.emit()
        self.app.processEvents()
        held = self.win.after_label.pixmap().toImage()
        self.assertNotEqual(held, result,
                            "holding still shows the result")
        expected = self.win._compare_pixmap_for("image")
        self.assertIsNotNone(expected)
        self.assertEqual(held, expected.toImage(),
                         "holding shows something else than the "
                         "original at the result size")
        self.win.compare_btn.released.emit()
        self.app.processEvents()
        self.assertEqual(self.win.after_label.pixmap().toImage(),
                         result, "release did not restore the result")

    def test_image_hold_matches_result_size(self):
        # The comparison only reads when the two line up: the
        # held original is the result's displayed size.
        self._load_image()
        result = self.win.after_label.pixmap()
        self.win.compare_btn.pressed.emit()
        self.app.processEvents()
        held = self.win.after_label.pixmap()
        self.assertEqual((held.width(), held.height()),
                         (result.width(), result.height()))
        self.win.compare_btn.released.emit()

    def test_canvas_is_dead_while_held(self):
        # The canvas shows the original while held — a click
        # there would paint blind onto pixels the user cannot
        # see, so the canvas ignores it entirely.
        from PySide6.QtTest import QTest
        self._load_image()
        self.win._on_editor_tool("pencil")
        with mock.patch.object(self.win, "_canvas_press") as press:
            pos = self.win.after_label.pos_for_pixel(0, 0)
            QTest.mousePress(self.win.after_label,
                             Qt.MouseButton.LeftButton, pos=pos)
            self.assertEqual(press.call_count, 1)
            self.win.compare_btn.pressed.emit()
            self.app.processEvents()
            QTest.mousePress(self.win.after_label,
                             Qt.MouseButton.LeftButton, pos=pos)
            self.assertEqual(press.call_count, 1,
                             "a canvas press landed while the "
                             "original was showing")
            self.win.compare_btn.released.emit()
            self.app.processEvents()
            QTest.mousePress(self.win.after_label,
                             Qt.MouseButton.LeftButton, pos=pos)
            self.assertEqual(press.call_count, 2)

    # --------------------------------------------------- video hold

    def test_video_hold_shows_original_release_restores(self):
        self._load_video()
        self.assertTrue(self.win.video_compare_btn.isEnabled())
        result = self.win.video_preview_label.pixmap().toImage()
        self.win.video_compare_btn.pressed.emit()
        self.app.processEvents()
        held = self.win.video_preview_label.pixmap().toImage()
        self.assertNotEqual(held, result,
                            "holding still shows the pixelized frame")
        expected = self.win._compare_pixmap_for("video")
        self.assertIsNotNone(expected)
        self.assertEqual(held, expected.toImage(),
                         "holding shows something else than the "
                         "extracted frame")
        self.win.video_compare_btn.released.emit()
        self.app.processEvents()
        self.assertEqual(
            self.win.video_preview_label.pixmap().toImage(),
            result, "release did not restore the pixelized frame")

    def test_video_compare_disabled_without_raw_frames(self):
        # Split-sheet frames are finished pixel art — there is
        # no raw set to compare against, so the button stays
        # quiet for them.
        frame = make_solid_image(32, 32, 200, 30, 30)
        self.win._load_split_frames([frame], [125], [], "sheet")
        self.app.processEvents()
        self.assertTrue(self.win.video_frames)
        self.assertFalse(self.win.video_compare_btn.isEnabled())

    def test_video_hold_stops_playback(self):
        self._load_video()
        self.win.video_play_btn.click()
        self.app.processEvents()
        self.assertTrue(self.win.video_timer.isActive())
        self.win.video_compare_btn.pressed.emit()
        self.app.processEvents()
        self.assertFalse(self.win.video_timer.isActive(),
                         "playback kept running under the hold")
        self.win.video_compare_btn.released.emit()

    # --------------------------------------------------- hover

    def test_hover_palette_previews_without_applying(self):
        self._load_image()
        self.win._render_hover_variants_now()
        result = self.win.after_label.pixmap().toImage()
        before = self.win.result_image
        row = self.win.palette_combo.findData("gameboy")
        self.assertGreaterEqual(row, 0)
        self._hover(self.win.palette_combo, row)
        hovered = self.win.after_label.pixmap().toImage()
        self.assertNotEqual(hovered, result,
                            "hovering the palette changed nothing")
        # Nothing applied: the picker, the controls, and the
        # result are all exactly as they were.
        self.assertEqual(self.win.palette_combo.currentData(),
                         "image")
        self.assertEqual(self.win.result_image, before)
        self.win._end_hover_preview("image")
        self.app.processEvents()
        self.assertEqual(self.win.after_label.pixmap().toImage(),
                         result, "leaving did not restore the result")

    def test_hover_preset_previews_without_applying(self):
        self._load_image()
        self.win._render_hover_variants_now()
        result = self.win.after_label.pixmap().toImage()
        row = self.win.preset_combo.findData("builtin:Game Boy 32")
        self.assertGreaterEqual(row, 0)
        self._hover(self.win.preset_combo, row)
        self.assertNotEqual(
            self.win.after_label.pixmap().toImage(), result,
            "hovering the preset changed nothing")
        self.assertEqual(self.win.preset_combo.currentData(),
                         None)
        self.assertEqual(self.win.width_spin.value(), 32)
        self.assertEqual(self.win.colors_spin.value(), 16)
        self.win._end_hover_preview("image")
        self.app.processEvents()
        self.assertEqual(self.win.after_label.pixmap().toImage(),
                         result)

    def test_hover_triggers_no_render(self):
        # The rule: a hover never renders — it only displays
        # what the prefetch already made. Count pixelize calls
        # across two hovers (palette + preset).
        import pixel_forge.gui as gui_mod
        self._load_image()
        self.win._render_hover_variants_now()
        # The cache is warm: stop the prefetch timer and flush
        # the event loop, so the only pixelize calls the
        # counter can see are the hovers' own.
        self.win._hover_prefetch_timer.stop()
        self.app.processEvents()
        calls = []
        original = gui_mod.pixelize_image

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with mock.patch.object(gui_mod, "pixelize_image", counting):
            palette_row = self.win.palette_combo.findData("gameboy")
            self._hover(self.win.palette_combo, palette_row)
            preset_row = self.win.preset_combo.findData(
                "builtin:Game Boy 32")
            self._hover(self.win.preset_combo, preset_row)
            self.win._end_hover_preview("image")
        self.assertEqual(calls, [],
                         "hovering rendered %d time(s)" % len(calls))

    def test_hover_with_cold_cache_shows_nothing(self):
        # A variant the prefetch has not made yet shows nothing
        # — the preview stays on the current result rather than
        # stalling on a render.
        self._load_image()
        self.win._invalidate_hover_cache("image")
        result = self.win.after_label.pixmap().toImage()
        row = self.win.palette_combo.findData("gameboy")
        self._hover(self.win.palette_combo, row)
        self.assertEqual(self.win.after_label.pixmap().toImage(),
                         result)
        self.assertIsNone(self.win._hovering)

    def test_hover_ends_when_popup_closes_or_leaves(self):
        # Both ways out of a popup — closing it (Hide) and the
        # mouse leaving it (Leave) — put the result back.
        from PySide6.QtCore import QEvent
        self._load_image()
        self.win._render_hover_variants_now()
        result = self.win.after_label.pixmap().toImage()
        row = self.win.palette_combo.findData("gameboy")
        view = self.win.palette_combo.view()
        self._hover(self.win.palette_combo, row)
        self.assertIsNotNone(self.win._hovering)
        self.win.eventFilter(view, QEvent(QEvent.Type.Hide))
        self.app.processEvents()
        self.assertIsNone(self.win._hovering)
        self.assertEqual(self.win.after_label.pixmap().toImage(),
                         result)
        self._hover(self.win.palette_combo, row)
        self.assertIsNotNone(self.win._hovering)
        self.win.eventFilter(view, QEvent(QEvent.Type.Leave))
        self.app.processEvents()
        self.assertIsNone(self.win._hovering)
        self.assertEqual(self.win.after_label.pixmap().toImage(),
                         result)

    def test_click_applies_after_hover(self):
        # Hover previews, click applies: picking the hovered
        # palette re-pixelizes to exactly the previewed
        # variant.
        self._load_image()
        self.win._render_hover_variants_now()
        settings = self.win._hover_settings("image", "palette",
                                            "gameboy")
        source = self.win._hover_source("image")
        variant = self.win._hover_cache[("image", source[0],
                                         settings)]
        row = self.win.palette_combo.findData("gameboy")
        self._hover(self.win.palette_combo, row)
        self.win.palette_combo.setCurrentIndex(row)
        self.app.processEvents()
        self.assertEqual(self.win.palette_combo.currentData(),
                         "gameboy")
        self.assertEqual(self.win.result_image, variant,
                         "applying the palette did not land on "
                         "the previewed variant")

    def test_prefetch_fills_cache_at_idle(self):
        # Without any manual drain, the idle timer renders the
        # variants — a few event-loop pumps fill the cache.
        self._load_image()
        deadline = time.monotonic() + 10
        settings = self.win._hover_settings("image", "palette",
                                            "gameboy")
        source = self.win._hover_source("image")
        key = ("image", source[0], settings)
        while time.monotonic() < deadline:
            self.app.processEvents()
            if key in self.win._hover_cache:
                break
            time.sleep(0.05)
        self.assertIn(key, self.win._hover_cache,
                      "the idle prefetch never rendered the "
                      "gameboy variant")

    def test_control_change_invalidates_cache(self):
        # A control change moves the settings, so the variants
        # rendered under the old settings are gone.
        self._load_image()
        self.win._render_hover_variants_now()
        settings = self.win._hover_settings("image", "palette",
                                            "image")
        source = self.win._hover_source("image")
        old_key = ("image", source[0], settings)
        self.assertIn(old_key, self.win._hover_cache)
        self.win.colors_spin.setValue(8)
        self.app.processEvents()
        self.assertNotIn(old_key, self.win._hover_cache,
                         "a variant from the old color count "
                         "survived the control change")

    # --------------------------------------------------- video hover

    def test_video_hover_palette_previews_without_applying(self):
        self._load_video()
        self.win._render_hover_variants_now()
        result = self.win.video_preview_label.pixmap().toImage()
        frames_before = list(self.win.video_frames)
        row = self.win.video_palette_combo.findData("gameboy")
        self.assertGreaterEqual(row, 0)
        self._hover(self.win.video_palette_combo, row)
        self.assertNotEqual(
            self.win.video_preview_label.pixmap().toImage(), result,
            "hovering the palette changed nothing")
        self.assertEqual(
            self.win.video_palette_combo.currentData(), "image")
        self.assertEqual(self.win.video_frames, frames_before)
        self.win._end_hover_preview("video")
        self.app.processEvents()
        self.assertEqual(
            self.win.video_preview_label.pixmap().toImage(), result)

    def test_video_hover_triggers_no_render(self):
        import pixel_forge.gui as gui_mod
        self._load_video()
        self.win._render_hover_variants_now()
        # The cache is warm: stop the prefetch timer and flush
        # the event loop, so the only pixelize calls the
        # counter can see are the hovers' own.
        self.win._hover_prefetch_timer.stop()
        self.app.processEvents()
        calls = []
        original = gui_mod.pixelize_image

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with mock.patch.object(gui_mod, "pixelize_image", counting):
            row = self.win.video_palette_combo.findData("gameboy")
            self._hover(self.win.video_palette_combo, row)
            self.win._end_hover_preview("video")
        self.assertEqual(calls, [],
                         "hovering rendered %d time(s)" % len(calls))


class HoverScrubTests(unittest.TestCase):
    """Task 92 (U16/U34): hover-scrub the frame strip.
    Hovering a strip thumbnail previews that frame in the
    Animation preview without loading it — the current
    frame, the counter, and the scrubber stay where they
    were — and moving off puts the current frame back.
    Thumbnails are built once at strip load, so a hover
    never rebuilds one. Double-click loads at the hovered
    frame. Runner group g15."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.tmp = tempfile.mkdtemp()
        self.win = MainWindow()
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer.isActive()):
                return True
            time.sleep(0.05)
        return False

    def _load_video(self, duration=2, fps=8):
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            self.skipTest("ffmpeg not installed")
        clip = make_test_video(os.path.join(self.tmp, "clip.mp4"),
                               duration=duration, fps=fps, width=80,
                               height=60)
        self.win.tabs.setCurrentIndex(1)
        self.assertTrue(self.win.load_video(clip))
        self.assertTrue(self._wait_for_video())
        self.assertGreaterEqual(len(self.win.video_frames), 8)
        return clip

    def _preview_image(self):
        return self.win.video_preview_label.pixmap().toImage()

    def _expected_image(self, index):
        from pixel_forge.gui_shared import checkerboard_pixmap
        frame = self.win.video_frames[index]
        return checkerboard_pixmap(
            frame, self.win.video_zoom_slider.value(),
            smooth=False).toImage()

    def _strip_item(self, index):
        # The strip is in natural order after a fresh load —
        # row == frame index.
        item = self.win.video_strip.item(index)
        self.assertEqual(
            item.data(Qt.ItemDataRole.UserRole), index)
        return item

    # --------------------------------------------------- the hint

    def test_hint_line_and_tooltip(self):
        # The gesture can be found, not guessed: a plain-words
        # line over the strip and a tooltip that names the
        # hover and the double-click. No em-dash, no "(s)".
        hint = self.win.video_scrub_hint
        self.assertEqual(
            hint.text(),
            "Hover a frame to peek at it. Double-click to jump "
            "to it.")
        self.assertEqual(hint.objectName(), "explanation")
        self.assertTrue(hint.wordWrap())
        self.assertNotIn("\u2014", hint.text())
        self.assertNotIn("(s)", hint.text())
        tooltip = self.win.video_strip.toolTip()
        self.assertIn("hover one to peek", tooltip)
        self.assertIn("double-click to jump", tooltip)
        # The hint explains a gesture, not a field — it is
        # not a registered field explanation.
        self.assertNotIn(self.win.video_scrub_hint,
                         self.win.field_explanations.values())

    # --------------------------------------------------- the peek

    def test_hover_peeks_without_loading(self):
        self._load_video()
        current = self.win.video_current
        counter = self.win.video_counter_label.text()
        scrubber = self.win.video_scrub_slider.value()
        duration = self.win.video_duration_spin.value()
        target = current + 5
        self.win._on_strip_hover(self._strip_item(target))
        self.app.processEvents()
        self.assertEqual(self.win._video_scrub_index, target)
        self.assertEqual(self._preview_image(),
                         self._expected_image(target),
                         "the preview does not show the hovered "
                         "frame")
        # Nothing loaded: the current frame and every control
        # that names it are exactly as they were.
        self.assertEqual(self.win.video_current, current)
        self.assertEqual(self.win.video_counter_label.text(),
                         counter)
        self.assertEqual(self.win.video_scrub_slider.value(),
                         scrubber)
        self.assertEqual(self.win.video_duration_spin.value(),
                         duration)

    def test_hover_moves_between_frames(self):
        self._load_video()
        current = self.win.video_current
        self.win._on_strip_hover(self._strip_item(current + 2))
        self.app.processEvents()
        self.assertEqual(self._preview_image(),
                         self._expected_image(current + 2))
        self.win._on_strip_hover(self._strip_item(current + 7))
        self.app.processEvents()
        self.assertEqual(self._preview_image(),
                         self._expected_image(current + 7))
        self.assertEqual(self.win.video_current, current)

    def test_leave_restores_current_frame(self):
        self._load_video()
        before = self._preview_image()
        self.win._on_strip_hover(self._strip_item(5))
        self.app.processEvents()
        self.assertNotEqual(self._preview_image(), before)
        self.win._end_strip_scrub()
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index)
        self.assertEqual(self._preview_image(), before,
                         "leaving did not restore the current "
                         "frame")

    def test_hover_same_frame_twice_is_one_refresh(self):
        # Moving within one thumbnail re-fires the hover, but
        # the preview only refreshes when the frame changes.
        self._load_video()
        with mock.patch.object(
                self.win, "_refresh_video_preview",
                wraps=self.win._refresh_video_preview) as refresh:
            item = self._strip_item(4)
            self.win._on_strip_hover(item)
            self.win._on_strip_hover(item)
            self.win._on_strip_hover(item)
        self.assertEqual(refresh.call_count, 1)

    # --------------------------------------------------- no rebuild

    def test_hover_triggers_no_thumbnail_rebuild(self):
        # The rule: thumbnails are built once at strip load —
        # a hover never rebuilds one and never re-pixelizes.
        import pixel_forge.gui as gui_mod
        import pixel_forge.gui_video as video_gui_mod
        self._load_video()
        # The v0.84 idle prefetch renders variants on timer
        # ticks — drain it and stop it first, so the only
        # pixelize calls the counter can see are the hovers'
        # own (the same setup as the v0.84 no-render test).
        self.win._render_hover_variants_now()
        self.win._hover_prefetch_timer.stop()
        self.app.processEvents()
        calls = []
        original_thumb = video_gui_mod.thumb_pixmap
        original_pixelize = gui_mod.pixelize_image

        def counting_thumb(*args, **kwargs):
            calls.append("thumb")
            return original_thumb(*args, **kwargs)

        def counting_pixelize(*args, **kwargs):
            calls.append("pixelize")
            return original_pixelize(*args, **kwargs)

        with mock.patch.object(video_gui_mod, "thumb_pixmap",
                               counting_thumb), \
            mock.patch.object(gui_mod, "pixelize_image",
                              counting_pixelize), \
            mock.patch.object(self.win, "_rebuild_strip",
                              wraps=self.win._rebuild_strip) \
                as rebuild:
            for index in (1, 3, 6, 9, 2):
                self.win._on_strip_hover(
                    self._strip_item(index))
                self.app.processEvents()
            self.win._end_strip_scrub()
        self.assertEqual(calls, [],
                         "hovering rebuilt: %s" % calls)
        self.assertEqual(rebuild.call_count, 0,
                         "hovering rebuilt the strip")

    # --------------------------------------------------- double-click

    def test_double_click_loads_hovered_frame(self):
        self._load_video()
        current = self.win.video_current
        target = current + 5
        self.win._on_strip_hover(self._strip_item(target))
        self.app.processEvents()
        self.assertEqual(self.win.video_current, current)
        self.win.video_strip.itemDoubleClicked.emit(
            self._strip_item(target))
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index)
        self.assertEqual(self.win.video_current, target)
        self.assertEqual(self._preview_image(),
                         self._expected_image(target))
        self.assertEqual(
            self.win.video_counter_label.text(),
            "Frame %d of %d (%d kept)"
            % (target + 1, len(self.win.video_frames),
               len(self.win._kept_indices())))

    # --------------------------------------------------- playback

    def test_hover_does_not_start_playback(self):
        self._load_video()
        self.win._on_strip_hover(self._strip_item(5))
        self.app.processEvents()
        self.assertFalse(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_play_btn.text(), "Play")

    def test_hover_while_playing_does_nothing(self):
        self._load_video()
        self.win.video_play_btn.click()
        self.app.processEvents()
        self.assertTrue(self.win.video_timer.isActive())
        self.win._on_strip_hover(self._strip_item(5))
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index,
                          "a hover peeked during playback")
        self.win.video_play_btn.click()
        self.app.processEvents()

    def test_play_clears_scrub(self):
        self._load_video()
        self.win._on_strip_hover(self._strip_item(5))
        self.app.processEvents()
        self.assertIsNotNone(self.win._video_scrub_index)
        self.win.video_play_btn.click()
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index)
        self.assertTrue(self.win.video_timer.isActive())
        self.win.video_play_btn.click()
        self.app.processEvents()

    def test_rebuild_clears_scrub(self):
        # New frames make a peek stale — the strip rebuild ends
        # it rather than pointing at a different frame.
        self._load_video()
        self.win._on_strip_hover(self._strip_item(5))
        self.app.processEvents()
        self.assertIsNotNone(self.win._video_scrub_index)
        self.win._rebuild_strip()
        self.assertIsNone(self.win._video_scrub_index)

    # --------------------------------------------------- the wiring

    def test_event_filter_drives_scrub(self):
        # The real path: a MouseMove over a thumbnail and a
        # Leave off the strip, through the window's event
        # filter — the way the mouse delivers them.
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent
        # A 1 s clip: 8 frames, one strip row, so the space
        # below the row is empty strip space.
        self._load_video(duration=1)
        viewport = self.win.video_strip.viewport()
        item = self._strip_item(6)
        rect = self.win.video_strip.visualItemRect(item)
        move = QMouseEvent(QEvent.Type.MouseMove, rect.center(),
                           Qt.MouseButton.NoButton,
                           Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)
        self.assertFalse(self.win.eventFilter(viewport, move))
        self.app.processEvents()
        self.assertEqual(self.win._video_scrub_index, 6)
        self.assertEqual(self._preview_image(),
                         self._expected_image(6))
        # Empty strip space ends the scrub — scan for a point
        # no item covers (the row layout depends on the window
        # width, so no fixed corner is safe).
        empty = None
        for y in range(4, viewport.height(), 10):
            for x in range(4, viewport.width(), 10):
                point = QPoint(x, y)
                if self.win.video_strip.itemAt(point) is None:
                    empty = point
                    break
            if empty is not None:
                break
        self.assertIsNotNone(
            empty, "the strip leaves no empty space to move to")
        move_empty = QMouseEvent(
            QEvent.Type.MouseMove, empty, Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(self.win.eventFilter(viewport, move_empty))
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index)
        self.assertEqual(self._preview_image(),
                         self._expected_image(
                             self.win.video_current))

    def test_event_filter_leave_ends_scrub(self):
        from PySide6.QtCore import QEvent
        self._load_video()
        viewport = self.win.video_strip.viewport()
        self.win._on_strip_hover(self._strip_item(4))
        self.app.processEvents()
        self.assertIsNotNone(self.win._video_scrub_index)
        self.assertFalse(
            self.win.eventFilter(viewport,
                                 QEvent(QEvent.Type.Leave)))
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index)

    def test_drag_does_not_scrub(self):
        # A button held means a reorder drag, not a peek — the
        # hover is ignored until the button is up.
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        self._load_video()
        viewport = self.win.video_strip.viewport()
        item = self._strip_item(6)
        rect = self.win.video_strip.visualItemRect(item)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton,
                         pos=rect.center())
        move = QMouseEvent(QEvent.Type.MouseMove, rect.center(),
                           Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
        self.win.eventFilter(viewport, move)
        self.app.processEvents()
        self.assertIsNone(self.win._video_scrub_index,
                          "a drag peeked at a frame")
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton,
                           pos=rect.center())


if __name__ == "__main__":
    unittest.main()
