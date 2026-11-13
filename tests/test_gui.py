"""Qt GUI smoke tests — launch the window offscreen, drive it.

Run with the Qt test env (see AGENTS.md):
  LD_LIBRARY_PATH=~/workspace/qt-libs/root/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen \
  ~/workspace/forge-v2-testenv/bin/python -m unittest tests.test_gui
Skipped when PySide6 is not importable.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from helpers import (make_gradient_image, make_noisy_image,  # noqa: E402
                         make_solid_image, make_test_video, stub_env,
                         write_image, write_stub_engine)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, \
        QFileDialog, QInputDialog, QLabel, QMessageBox, QPushButton, \
        QTabWidget, QWidget
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
        self.win.show()
        self.app.processEvents()
        self.assertTrue(self.win.isVisible())
        self.assertEqual(self.win.windowTitle(), "Pixel Forge")

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
                                    "grayscale"])
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
        # otherwise show just one corner of sky.)
        self.win.load_image(self.src)
        before = self.win.before_label.pixmap()
        self.assertLessEqual(max(before.width(), before.height()), 352)
        after = self.win.after_label.pixmap()
        self.assertEqual((after.width(), after.height()), (256, 256))

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
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code", "Library"])
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
        # returns at once with an "Extracting frames…" status.
        from PySide6.QtCore import QTimer
        fired = []
        self.assertTrue(self.win.load_video(self.clip))
        self.assertIn("Extracting frames",
                      self.win.statusBar().currentMessage())
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

    def test_sheet_fps_follows_extraction_fps(self):
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.sheet_fps_spin.value(), 8)
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.assertTrue(self._wait_for_video())
        self.assertEqual(self.win.sheet_fps_spin.value(), 4)

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
        cls.model = os.path.join(cls.tmp, "dummy.gguf")
        with open(cls.model, "wb") as f:
            f.write(b"not a real model")

    def setUp(self):
        self._env = stub_env(self.stub, self.model)
        self._env.__enter__()
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self._env.__exit__(None, None, None)

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
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Code", "Library"])
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
                                    "grayscale"])
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
        self.assertIn("type a prompt first", warn.call_args[0][2])
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
        # status-bar message instead of a traceback.
        from pixel_forge import imagegen as imagegen_mod
        work = tempfile.mkdtemp()
        _batch, bdir = imagegen_mod.new_batch(work, "a knight")
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
                                "Library"])
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
        self.assertIn("1 sprite(s)",
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
                                     "PICO-8 64", "Detailed 64"])
            data = [combo.itemData(i)
                    for i in range(combo.count())]
            self.assertEqual(data, [None, "builtin:Game Boy 32",
                                    "builtin:PICO-8 64",
                                    "builtin:Detailed 64"])
        self.assertFalse(self.win.preset_delete_btn.isEnabled())

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
             "Also Broken": dict(good, palette="sepia")})
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
                                     "PICO-8 64", "Detailed 64"])

    def test_no_warning_label_when_no_skips(self):
        for label in self._warning_labels():
            self.assertTrue(label.isHidden())
            self.assertEqual(label.text(), "")


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
                                "Library"])
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
        self.assertIn("1 file(s) skipped",
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
        # returns at once with a "Loading texture…" status.
        from PySide6.QtCore import QTimer
        fired = []
        self.assertTrue(self.win.load_game_asset(self.big_texture))
        self.assertIn("Loading texture",
                      self.win.statusBar().currentMessage())
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


if __name__ == "__main__":
    unittest.main()
