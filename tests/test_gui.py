"""Qt GUI smoke tests — launch the window offscreen, drive it.

Run with the Qt test env (see AGENTS.md):
  LD_LIBRARY_PATH=~/workspace/qt-libs/root/usr/lib/x86_64-linux-gnu \
  QT_QPA_PLATFORM=offscreen \
  ~/workspace/forge-v2-testenv/bin/python -m unittest tests.test_gui
Skipped when PySide6 is not importable.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import (make_gradient_image, make_solid_image,  # noqa: E402
                     make_test_video, stub_env, write_image,
                     write_stub_engine)

try:
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, \
        QFileDialog, QInputDialog, QLabel, QMessageBox, QPushButton, \
        QTabWidget, QWidget
    HAS_QT = True
except ImportError:
    HAS_QT = False


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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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

    def test_video_tab_exists_with_controls(self):
        tabs = [self.win.tabs.tabText(i)
                for i in range(self.win.tabs.count())]
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Library"])
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
        item = self.win.video_strip.item(0)
        icon = item.icon()
        self.assertFalse(icon.isNull())
        pixmap = icon.pixmap(64, 64)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole), 0)

    def test_keep_drop_changes_export(self):
        from PySide6.QtCore import Qt
        self.win.load_video(self.clip)
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
        self.win.video_strip.item(1).setCheckState(
            Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.win.video_current = 0
        self.win._advance_video_frame()
        self.assertEqual(self.win.video_current, 2)

    def test_playback_without_loop_stops_at_end(self):
        self.win.load_video(self.clip)
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
        self.win._on_play_pause()
        self.assertTrue(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_play_btn.text(), "Pause")
        self.win._on_play_pause()
        self.assertFalse(self.win.video_timer.isActive())
        self.assertEqual(self.win.video_play_btn.text(), "Play")

    def test_size_change_repixelizes_frames(self):
        self.win.load_video(self.clip)
        index = self.win.video_size_combo.findData(16)
        self.win.video_size_combo.setCurrentIndex(index)
        self.app.processEvents()
        for frame in self.win.video_frames:
            self.assertEqual((frame.width(), frame.height()), (16, 16))
        # Keep/drop choices survive a re-pixelize.
        self.assertTrue(all(self.win.video_keep))

    def test_fps_change_reextracts(self):
        self.win.load_video(self.clip)
        self.assertIn(len(self.win.video_frames), (15, 16))
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.app.processEvents()
        self.assertIn(len(self.win.video_frames), (7, 8))

    def test_clicking_strip_frame_shows_it(self):
        self.win.load_video(self.clip)
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
        self.win.show()
        self.app.processEvents()
        shot = os.path.join(self.tmp, "pixel-forge-video.png")
        self.win.grab().save(shot)
        self.assertTrue(os.path.exists(shot))
        self.assertGreater(os.path.getsize(shot), 1000)


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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
        self.app.processEvents()
        self.assertTrue(self.win.sheet_preview_btn.isEnabled())
        self.assertTrue(self.win.sheet_export_btn.isEnabled())

    def test_drop_all_disables_sheet_buttons(self):
        self.win.load_video(self.clip)
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
        self.assertEqual(self.win.sheet_fps_spin.value(), 8)
        index = self.win.video_fps_combo.findData(4)
        self.win.video_fps_combo.setCurrentIndex(index)
        self.app.processEvents()
        self.assertEqual(self.win.sheet_fps_spin.value(), 4)

    def test_preview_dialog_shows_packed_sheet(self):
        import math
        self.win.load_video(self.clip)
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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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
        self.assertEqual(tabs, ["Image", "Video", "Prompt", "Library"])
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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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
        self.win.tabs.setCurrentIndex(3)

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
        self.assertEqual(tabs, ["Image", "Video", "Prompt",
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

    def test_no_bare_ampersand_on_library_tab(self):
        for text in visible_texts(self.win):
            self.assertNotIn("&", text, "bare & in visible text: %r"
                             % text)

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
            second.tabs.setCurrentIndex(3)
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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
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

    def test_save_sheet_button_follows_kept_frames(self):
        self.assertFalse(self.win.sheet_library_btn.isEnabled())
        self.assertTrue(self.win.load_video(self.clip))
        self.assertTrue(self.win.sheet_library_btn.isEnabled())
        self.win._on_drop_all()
        self.assertFalse(self.win.sheet_library_btn.isEnabled())
        self.win._on_keep_all()
        self.assertTrue(self.win.sheet_library_btn.isEnabled())

    def test_save_sheet_to_library(self):
        self.assertTrue(self.win.load_video(self.clip))
        entry = self.win.save_sheet_to_library("Walk cycle",
                                               ["walk"])
        self.assertEqual(entry["kind"], "sheet")
        self.assertEqual(entry["frames"], 16)
        self.assertEqual(entry["source"], "video")
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "walk-cycle.png")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.lib_dir, "walk-cycle.json")))
        self.win.tabs.setCurrentIndex(3)
        self.assertEqual(self.win.library_list.count(), 1)
        item = self.win.library_list.item(0)
        self.win.library_list.setCurrentItem(item)
        self.win._on_library_item_clicked(item)
        self.assertIn("spritesheet, 16 frames",
                      self.win.library_info_label.text())


if __name__ == "__main__":
    unittest.main()
