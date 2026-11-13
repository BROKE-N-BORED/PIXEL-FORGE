"""Qt GUI smoke tests — launch the window offscreen, drive it.

Run with the Qt test env (see AGENTS.md):
  LD_LIBRARY_PATH=~/workspace/qt-libs/root/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen \
  ~/workspace/forge-v2-testenv/bin/python -m unittest tests.test_gui
Skipped when PySide6 is not importable.
"""
import json
import os
import shutil
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
        self.win.load_video(self.long_clip)
        self.assertTrue(self._wait_for_video())
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.win.video_play_fps_spin.setValue(2)
        self.win.sheet_fps_spin.setValue(12)
        self.assertTrue(self._wait_for_video())
        self.win.load_video(self.clip)
        self.assertTrue(self._wait_for_video())
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
        # Batches and history live in the library folder now
        # (task 20), so generation must not write into the real
        # ~/pixel-forge-library/ during the suite.
        self.lib_dir = tempfile.mkdtemp()
        from pixel_forge.gui import MainWindow
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
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
        self.assertIn("3 candidate(s)", text)
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
        self.assertIn("3 candidate(s)", text)
        self.assertNotIn("keeper", text)

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
        cls.model = os.path.join(cls.tmp, "dummy.gguf")
        with open(cls.model, "wb") as f:
            f.write(b"not a real model")

    def setUp(self):
        self._env = stub_env(self.stub, self.model)
        self._env.__enter__()
        self.lib_dir = tempfile.mkdtemp()
        from pixel_forge.gui import MainWindow
        self.win = MainWindow(library_dir=self.lib_dir)
        self.win.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
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
    Ctrl+S routing, Ctrl+1..5 tab switching, Enter-to-generate,
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

    def test_menu_bar_has_file_and_help_menus(self):
        menus = self._menus()
        self.assertEqual(sorted(menus), ["File", "Help"])
        file_items = [(a.text(), a.shortcut().toString())
                      for a in menus["File"].actions()]
        self.assertEqual(file_items, [("Import", "Ctrl+O"),
                                      ("Export", "Ctrl+S")])
        help_items = [a.text()
                      for a in menus["Help"].actions()]
        self.assertEqual(help_items,
                         ["About Pixel Forge",
                          "What the Code tab detects",
                          "Library location"])

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
            self._menus()["Help"].actions()[0].trigger()
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
            self._menus()["Help"].actions()[1].trigger()
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
            self._menus()["Help"].actions()[2].trigger()
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


if __name__ == "__main__":
    unittest.main()
