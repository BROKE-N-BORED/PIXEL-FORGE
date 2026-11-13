"""Sheet splitter GUI tests (task 40) — the SheetSplitDialog's
states (nothing picked, metadata, grid, refusals) and landing
split frames in the Video tab, plus the stale keep/drop undo
guard and the GIF import end-to-end check.

Every test here needs Qt, so the module skips when PySide6 is
missing (same guard as test_gui.py).
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from helpers import (make_solid_image, make_test_video,  # noqa: E402
                         write_image)
    from PySide6.QtCore import Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402
except ImportError as exc:
    if exc.name and exc.name.startswith("PySide6"):
        raise unittest.SkipTest("PySide6 not available")
    raise

from pixel_forge.spritesheet import pack_frames, write_sheet  # noqa: E402

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def solid_frames(count, size=16):
    return [make_solid_image(size, size, *COLORS[i])
            for i in range(count)]


def visible_texts(widget):
    """Every user-visible string in a widget tree: button text,
    labels, placeholders, tooltips."""
    from PySide6.QtWidgets import QWidget
    texts = []
    for child in widget.findChildren(QWidget):
        for attr in ("text", "placeholderText", "toolTip"):
            getter = getattr(child, attr, None)
            if callable(getter):
                try:
                    value = getter()
                except (RuntimeError, TypeError):
                    continue
                if value:
                    texts.append(value)
    return texts


class DialogTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        cls.frames = solid_frames(4)
        cls.png = os.path.join(cls.tmp, "walk-sheet.png")
        cls.meta = os.path.join(cls.tmp, "walk-sheet.json")
        write_sheet(cls.frames, ["f1", "f2", "f3", "f4"],
                    cls.png, cls.meta, cols=2, fps=8,
                    durations=[100, 200, 300, 400],
                    tags=[{"name": "walk", "from": 0, "to": 3}])
        sheet, _layout = pack_frames(cls.frames, cols=2)
        cls.bare = os.path.join(cls.tmp, "bare.png")
        write_image(sheet, cls.bare)

    def setUp(self):
        from pixel_forge.gui import MainWindow, SheetSplitDialog
        self.win = MainWindow()
        self.dialog = SheetSplitDialog(self.win, 125)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()


class DialogOpensEmptyTests(DialogTestBase):
    def test_nothing_picked_by_default(self):
        dialog = self.dialog
        self.assertEqual(dialog.sheet_edit.text(), "")
        self.assertEqual(dialog.meta_edit.text(), "")
        self.assertIsNone(dialog.sheet)
        self.assertIsNone(dialog.metadata)
        self.assertFalse(dialog.split_btn.isEnabled())
        self.assertEqual(dialog.estimate_label.text(), "")
        for spin in (dialog.frame_w_spin, dialog.frame_h_spin,
                     dialog.cols_spin, dialog.rows_spin,
                     dialog.spacing_spin, dialog.margin_spin):
            self.assertEqual(spin.value(), 0)
            self.assertFalse(spin.isEnabled())

    def test_visible_strings_have_no_ampersand_or_parens(self):
        # The Qt mnemonic trap (a bare & in a label) and the
        # "(s)" phrasing rule, checked on the dialog's own
        # strings — the MainWindow polish sweep does not open
        # this dialog.
        for text in visible_texts(self.dialog):
            self.assertNotIn("&", text, text)
            self.assertNotIn("(s)", text, text)

    def test_every_control_has_a_tooltip(self):
        # Interactive controls (buttons, inputs, spins) — plain
        # labels and headings carry no tooltip, like the rest of
        # the window. A spin box's internal line edit is not a
        # control of its own — skip it.
        from PySide6.QtWidgets import QSpinBox, QWidget
        for child in self.dialog.findChildren(QWidget):
            if isinstance(child.parentWidget(), QSpinBox):
                continue
            getter = getattr(child, "toolTip", None)
            if callable(getter) and child.__class__.__name__ in (
                    "QPushButton", "QLineEdit", "QSpinBox"):
                self.assertTrue(getter(),
                                "%s has no tooltip"
                                % child.__class__.__name__)


class DialogMetadataTests(DialogTestBase):
    def test_sheet_pick_auto_fills_sibling_metadata(self):
        self.dialog._set_sheet(self.png)
        self.assertEqual(self.dialog.meta_edit.text(), self.meta)
        self.assertIn("Metadata: 4 frames, 16 x 16 each",
                      self.dialog.info_label.text())
        self.assertIn("tags: walk", self.dialog.info_label.text())
        self.assertTrue(self.dialog.split_btn.isEnabled())
        for spin in (self.dialog.frame_w_spin,
                     self.dialog.frame_h_spin,
                     self.dialog.cols_spin, self.dialog.rows_spin):
            self.assertFalse(spin.isEnabled())

    def test_split_returns_frames_durations_tags(self):
        self.dialog._set_sheet(self.png)
        self.dialog.split_btn.click()
        self.app.processEvents()
        self.assertEqual(self.dialog.result(), 1)  # Accepted
        self.assertEqual(len(self.dialog.frames), 4)
        for index in range(4):
            self.assertEqual(self.dialog.frames[index].data,
                             self.frames[index].data)
        self.assertEqual(self.dialog.durations,
                         [100, 200, 300, 400])
        self.assertEqual(self.dialog.tags,
                         [{"name": "walk", "from": 0, "to": 3}])
        self.assertEqual(self.dialog.source_name,
                         "walk-sheet.png")

    def test_clearing_metadata_switches_to_grid(self):
        self.dialog._set_sheet(self.png)
        self.assertTrue(self.dialog.split_btn.isEnabled())
        self.dialog.meta_clear_btn.click()
        self.app.processEvents()
        self.assertEqual(self.dialog.meta_edit.text(), "")
        self.assertIn("no metadata", self.dialog.info_label.text())
        self.assertFalse(self.dialog.split_btn.isEnabled())
        self.assertTrue(self.dialog.frame_w_spin.isEnabled())


class DialogGridTests(DialogTestBase):
    def test_bare_sheet_opens_grid_form(self):
        self.dialog._set_sheet(self.bare)
        self.assertEqual(self.dialog.meta_edit.text(), "")
        self.assertIn("32 x 32, no metadata",
                      self.dialog.info_label.text())
        self.assertTrue(self.dialog.frame_w_spin.isEnabled())
        self.assertFalse(self.dialog.split_btn.isEnabled())
        self.assertEqual(self.dialog.estimate_label.text(), "")

    def test_frame_size_gives_live_estimate(self):
        self.dialog._set_sheet(self.bare)
        self.dialog.frame_w_spin.setValue(16)
        self.dialog.frame_h_spin.setValue(16)
        self.app.processEvents()
        self.assertEqual(self.dialog.estimate_label.text(),
                         "4 frames (2 columns x 2 rows), "
                         "each 16 x 16")
        self.assertTrue(self.dialog.split_btn.isEnabled())

    def test_cols_rows_give_live_estimate(self):
        self.dialog._set_sheet(self.bare)
        self.dialog.cols_spin.setValue(2)
        self.dialog.rows_spin.setValue(2)
        self.app.processEvents()
        self.assertEqual(self.dialog.estimate_label.text(),
                         "4 frames (2 columns x 2 rows), "
                         "each 16 x 16")
        self.assertTrue(self.dialog.split_btn.isEnabled())

    def test_grid_split_uses_default_duration(self):
        self.dialog._set_sheet(self.bare)
        self.dialog.frame_w_spin.setValue(16)
        self.dialog.frame_h_spin.setValue(16)
        self.dialog.split_btn.click()
        self.app.processEvents()
        self.assertEqual(self.dialog.result(), 1)
        self.assertEqual(len(self.dialog.frames), 4)
        for index in range(4):
            self.assertEqual(self.dialog.frames[index].data,
                             self.frames[index].data)
        self.assertEqual(self.dialog.durations, [125, 125, 125, 125])
        self.assertEqual(self.dialog.tags, [])

    def test_bad_grid_names_the_problem(self):
        self.dialog._set_sheet(self.bare)
        self.dialog.frame_w_spin.setValue(15)
        self.dialog.frame_h_spin.setValue(16)
        self.app.processEvents()
        self.assertIn("do not divide it evenly",
                      self.dialog.estimate_label.text())
        self.assertFalse(self.dialog.split_btn.isEnabled())


class DialogRefusalTests(DialogTestBase):
    def test_missing_sheet_shows_error_no_modal(self):
        missing = os.path.join(self.tmp, "nope.png")
        self.dialog._set_sheet(missing)
        self.assertIn("not found", self.dialog.info_label.text())
        self.assertIsNone(self.dialog.sheet)
        self.assertFalse(self.dialog.split_btn.isEnabled())

    def test_bad_metadata_shows_error(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as handle:
            handle.write("{not json")
        self.dialog._set_sheet(self.bare)
        self.dialog.meta_edit.setText(bad)
        self.dialog._refresh()
        self.assertIn("could not read",
                      self.dialog.info_label.text())
        self.assertFalse(self.dialog.split_btn.isEnabled())

    def test_mismatched_pair_shows_error(self):
        # The walk metadata (32 x 32) against a 16 x 16 sheet.
        small = os.path.join(self.tmp, "small.png")
        write_image(make_solid_image(16, 16, 1, 2, 3), small)
        self.dialog._set_sheet(small)
        self.dialog.meta_edit.setText(self.meta)
        self.dialog._refresh()
        self.assertIn("not a pair", self.dialog.info_label.text())
        self.assertFalse(self.dialog.split_btn.isEnabled())


class LandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def test_split_button_exists_on_video_tab(self):
        self.assertEqual(self.win.video_split_btn.text(),
                         "Split spritesheet")
        self.assertTrue(self.win.video_split_btn.toolTip())

    def test_landing_fills_strip_durations_tags(self):
        frames = solid_frames(4)
        self.win._load_split_frames(
            frames, [100, 200, 300, 400],
            [{"name": "walk", "from": 0, "to": 3}], "walk-sheet.png")
        self.app.processEvents()
        self.assertEqual(len(self.win.video_frames), 4)
        self.assertEqual(self.win.video_strip.count(), 4)
        self.assertEqual(self.win.video_durations,
                         [100, 200, 300, 400])
        self.assertEqual(self.win.video_tags,
                         [{"name": "walk", "from": 0, "to": 3}])
        self.assertEqual(self.win.video_keep, [True] * 4)
        self.assertEqual(self.win.video_info_label.text(),
                         "4 frames, 16 x 16, 4 kept")
        self.assertTrue(self.win.sheet_export_btn.isEnabled())
        self.assertTrue(self.win.video_gif_btn.isEnabled())
        self.assertTrue(self.win.video_play_btn.isEnabled())
        self.assertIn("Split walk-sheet.png (4 frames)",
                      self.win.statusBar().currentMessage())
        # No video to re-extract and no raw frames: the
        # pixelize controls cannot disturb split frames.
        self.assertIsNone(self.win.video_path)
        self.assertEqual(self.win.video_raw_frames, [])

    def test_second_split_replaces_first(self):
        self.win._load_split_frames(
            solid_frames(4), [125] * 4, [], "first.png")
        self.win._load_split_frames(
            solid_frames(2), [100, 200], [], "second.png")
        self.assertEqual(len(self.win.video_frames), 2)
        self.assertEqual(self.win.video_strip.count(), 2)
        self.assertEqual(self.win.video_durations, [100, 200])
        self.assertEqual(self.win.video_keep, [True, True])

    def test_stale_keep_undo_is_ignored_not_a_crash(self):
        # Task 40 fix: a keep/drop undo recorded against frames
        # that a later import or split replaced used to index
        # out of range in _apply_keep.
        from pixel_forge.gui import _KeepDropCommand
        self.win._load_split_frames(
            solid_frames(4), [125] * 4, [], "first.png")
        self.win._push_undo(_KeepDropCommand(
            self.win, [True] * 4, [False, True, True, True],
            "Drop frame 1"))
        self.win._load_split_frames(
            solid_frames(2), [125] * 2, [], "second.png")
        self.win._undo_stack.undo()  # must not raise
        self.assertEqual(self.win.video_keep, [True, True])
        self.assertEqual(self.win.video_strip.count(), 2)

    def test_matching_keep_undo_still_applies(self):
        from pixel_forge.gui import _KeepDropCommand
        self.win._load_split_frames(
            solid_frames(4), [125] * 4, [], "first.png")
        self.win._push_undo(_KeepDropCommand(
            self.win, [True] * 4, [False, True, True, True],
            "Drop frame 1"))
        self.win._undo_stack.undo()
        self.assertEqual(self.win.video_keep, [True] * 4)
        self.win._undo_stack.redo()
        self.assertEqual(self.win.video_keep,
                         [False, True, True, True])


class GifImportTests(unittest.TestCase):
    """Task 40 check: a GIF imported through the Video tab lands
    its frames in the strip, playable and exportable — the v0.36
    path, verified end to end here. Durations follow the Sheet
    fps default, the same as every video import."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()
        from pixel_forge import video as video_mod
        if video_mod.find_ffmpeg() is None:
            raise unittest.SkipTest("ffmpeg not installed")
        clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))
        cls.gif = os.path.join(cls.tmp, "clip.gif")
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-i", clip, "-vf", "fps=8,scale=64:48",
                        cls.gif], check=True, timeout=60)

    def setUp(self):
        from pixel_forge.gui import MainWindow
        self.win = MainWindow()
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()

    def _wait_for_video(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (self.win.video_worker is None
                    and not self.win._video_debounce_timer
                    .isActive()):
                return True
            time.sleep(0.05)
        return False

    def test_gif_lands_frames_in_strip(self):
        self.assertTrue(self.win.load_video(self.gif))
        self.assertTrue(self._wait_for_video())
        self.assertIn(len(self.win.video_frames), (15, 16))
        self.assertEqual(self.win.video_strip.count(),
                         len(self.win.video_frames))
        self.assertEqual(len(self.win.video_durations),
                         len(self.win.video_frames))
        self.assertTrue(all(
            duration == self.win._video_default_duration()
            for duration in self.win.video_durations))
        self.assertTrue(self.win.sheet_export_btn.isEnabled())
        self.assertTrue(self.win.video_gif_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
