"""Tests for pixel_forge/autosave.py (task 55: crash recovery).

Stdlib-only, no Qt — these run in both suites. The GUI half
(the dialog, the restore, the timer) lives in
AutosaveGuiTests in test_gui.py.
"""

import json
import os
import tempfile
import unittest

from helpers import make_solid_image
from pixel_forge.autosave import (
    autosave_home, crashed, discard_recovery, empty_recovery,
    has_recovery, load_recovery, load_recovery_image,
    mark_clean_exit, mark_running, marker_path, recovery_image_path,
    recovery_path, save_recovery, save_recovery_image)

FOREIGN_PID = 999999999  # no such process: a crashed run's pid


def _sample_state():
    state = empty_recovery()
    state["saved_at"] = "2026-11-15T00:00:00+00:00"
    state["current_tab"] = 1
    state["image"] = {"source": "/tmp/photo.png",
                      "generated": None,
                      "settings": {"width": 48, "height": 48,
                                   "keep_aspect": True,
                                   "palette": "image",
                                   "max_colors": 16,
                                   "dither": True},
                      "preset": None, "edited": True}
    state["video"] = {"source": "/tmp/clip.mp4",
                      "settings": {"width": 32, "height": 32,
                                   "keep_aspect": True,
                                   "palette": "image",
                                   "max_colors": 16,
                                   "dither": False},
                      "preset": None, "start": "0:01",
                      "end": "0:02", "every": 2, "count": 3,
                      "keep": [True, False, True],
                      "order": [0, 2, 1],
                      "durations": [125, 125, 125], "tags": []}
    state["prompt"] = {"batch_dir": "/tmp/batches/stool-120000",
                       "prompt": "a wooden stool",
                       "negative": "", "steps": 4, "cfg": 1.0,
                       "size": 512,
                       "settings": {"width": 32, "height": 32,
                                    "keep_aspect": True,
                                    "palette": "image",
                                    "max_colors": 16,
                                    "dither": True},
                       "preset": None}
    state["code"] = {"paths": ["/tmp/game.py"], "kind": "code",
                     "current": 1}
    state["tileset"] = {"mode": "blob", "shape": "rounded",
                        "base_a": "/tmp/base.png",
                        "base_b": None}
    return state


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pf-autosave-")

    def test_no_marker_is_not_a_crash(self):
        self.assertFalse(crashed(self.tmp))

    def test_foreign_pid_marker_is_a_crash(self):
        mark_running(self.tmp, pid=FOREIGN_PID)
        self.assertTrue(crashed(self.tmp))

    def test_clean_exit_removes_the_marker(self):
        mark_running(self.tmp, pid=FOREIGN_PID)
        self.assertTrue(crashed(self.tmp))
        mark_clean_exit(self.tmp)
        self.assertFalse(crashed(self.tmp))
        self.assertFalse(marker_path(self.tmp).exists())

    def test_clean_exit_without_a_marker_is_fine(self):
        mark_clean_exit(self.tmp)  # must not raise
        self.assertFalse(crashed(self.tmp))

    def test_same_process_marker_is_not_a_crash(self):
        # A second window in this process writes the same pid —
        # it is a sibling, not a crash.
        mark_running(self.tmp)
        self.assertFalse(crashed(self.tmp))

    def test_unreadable_marker_counts_as_a_crash(self):
        # This process always writes valid JSON, so a broken
        # marker is from a previous run.
        path = marker_path(self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        self.assertTrue(crashed(self.tmp))

    def test_marker_names_the_pid(self):
        mark_running(self.tmp, pid=4242)
        data = json.loads(marker_path(self.tmp).read_text(
            encoding="utf-8"))
        self.assertEqual(data["pid"], 4242)
        self.assertIn("started", data)


class RecoveryFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pf-autosave-")

    def test_save_load_round_trip(self):
        state = _sample_state()
        save_recovery(self.tmp, state)
        loaded, corrupt = load_recovery(self.tmp)
        self.assertFalse(corrupt)
        self.assertEqual(loaded, state)

    def test_missing_file_is_empty_not_corrupt(self):
        state, corrupt = load_recovery(self.tmp)
        self.assertIsNone(state)
        self.assertFalse(corrupt)

    def test_corrupt_json_is_corrupt(self):
        path = recovery_path(self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        state, corrupt = load_recovery(self.tmp)
        self.assertIsNone(state)
        self.assertTrue(corrupt)

    def test_wrong_format_is_corrupt(self):
        save_recovery(self.tmp, {"format": "something-else",
                                 "version": 1})
        state, corrupt = load_recovery(self.tmp)
        self.assertIsNone(state)
        self.assertTrue(corrupt)

    def test_wrong_version_is_corrupt(self):
        state = _sample_state()
        state["version"] = 99
        save_recovery(self.tmp, state)
        _state, corrupt = load_recovery(self.tmp)
        self.assertTrue(corrupt)

    def test_bad_tab_shape_is_corrupt(self):
        state = _sample_state()
        state["image"] = "not a dict"
        save_recovery(self.tmp, state)
        _state, corrupt = load_recovery(self.tmp)
        self.assertTrue(corrupt)

    def test_load_never_raises_and_never_writes(self):
        path = recovery_path(self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        state, corrupt = load_recovery(self.tmp)
        self.assertIsNone(state)
        self.assertTrue(corrupt)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_has_recovery(self):
        self.assertFalse(has_recovery(self.tmp))
        save_recovery(self.tmp, _sample_state())
        self.assertTrue(has_recovery(self.tmp))

    def test_discard_removes_state_and_image(self):
        save_recovery(self.tmp, _sample_state())
        save_recovery_image(self.tmp,
                            make_solid_image(8, 8, 200, 0, 0))
        self.assertTrue(recovery_path(self.tmp).exists())
        self.assertTrue(recovery_image_path(self.tmp).exists())
        discard_recovery(self.tmp)
        self.assertFalse(recovery_path(self.tmp).exists())
        self.assertFalse(recovery_image_path(self.tmp).exists())
        discard_recovery(self.tmp)  # twice is fine

    def test_image_round_trip(self):
        image = make_solid_image(16, 16, 10, 200, 30)
        image.data[0:4] = bytes((200, 30, 10, 255))
        save_recovery_image(self.tmp, image)
        back = load_recovery_image(self.tmp)
        self.assertIsNotNone(back)
        self.assertEqual((back.width(), back.height()), (16, 16))
        self.assertEqual(bytes(back.data), bytes(image.data))

    def test_missing_image_reads_none(self):
        self.assertIsNone(load_recovery_image(self.tmp))

    def test_corrupt_image_reads_none(self):
        path = recovery_image_path(self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a png at all")
        self.assertIsNone(load_recovery_image(self.tmp))

    def test_autosave_home_shape(self):
        home = autosave_home()
        self.assertEqual(home.parts[-4:],
                         (".local", "share", "pixel-forge",
                          "autosave"))


if __name__ == "__main__":
    unittest.main()
