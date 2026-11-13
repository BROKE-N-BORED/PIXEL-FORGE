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

    def test_menu_bar_has_file_and_help_menus(self):
        menus = self._menus()
        self.assertEqual(sorted(menus), ["Edit", "File", "Help"])
        file_items = [(a.text(), a.shortcut().toString())
                      for a in menus["File"].actions()]
        # Task 29 adds the Recent files submenu after Export; it
        # has no shortcut, and it is disabled while the session
        # is empty (this window's library folder is fresh).
        self.assertEqual(file_items, [("Import", "Ctrl+O"),
                                      ("Export", "Ctrl+S"),
                                      ("Recent files", "")])
        self.assertFalse(menus["File"].actions()[2].isEnabled())
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
        from pixel_forge.imagegen import MAX_PROMPT_LEN
        self.assertEqual(self.win.prompt_counter_label.text(),
                         "0 / 1000")
        self.win.prompt_edit.setText("hello")
        self.assertEqual(self.win.prompt_counter_label.text(),
                         "5 / 1000")
        self.assertEqual(self.win.prompt_counter_label.styleSheet(),
                         "")
        self.win.prompt_edit.setText("x" * (MAX_PROMPT_LEN + 1))
        self.assertEqual(self.win.prompt_counter_label.text(),
                         "1001 / 1000")
        self.assertIn("#ffb066",
                      self.win.prompt_counter_label.styleSheet())

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
                # Button -> inner widget -> viewport -> scroll
                # area: the viewport is the width that matters.
                scroll = (save.parentWidget().parentWidget()
                          .parentWidget())
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


if __name__ == "__main__":
    unittest.main()
