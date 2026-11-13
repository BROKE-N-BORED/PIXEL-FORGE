"""Session tests — task 29.

Qt-free: the session module is stdlib-only, so these run on
bare python.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.session import (  # noqa: E402
    MAX_RECENT, SESSION_FILENAME, empty_session, load_session,
    record_recent, save_session, session_path, set_last_code,
    set_last_image, set_last_video)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_missing_file_reads_empty_not_corrupt(self):
        data, corrupt = load_session(self.dir)
        self.assertEqual(data, empty_session())
        self.assertFalse(corrupt)

    def test_save_and_load_round_trip(self):
        data = empty_session()
        set_last_image(data, "/tmp/knight.png")
        set_last_video(data, "/tmp/walk.mp4")
        set_last_code(data, ["/tmp/sprites.py", "/tmp/more"])
        record_recent(data, "/tmp/knight.png", "image")
        record_recent(data, "/tmp/walk.mp4", "video")
        save_session(self.dir, data)
        loaded, corrupt = load_session(self.dir)
        self.assertFalse(corrupt)
        self.assertEqual(loaded, data)
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, SESSION_FILENAME)))

    def test_save_creates_the_folder(self):
        nested = os.path.join(self.dir, "deep", "library")
        save_session(nested, empty_session())
        self.assertTrue(os.path.isfile(
            os.path.join(nested, SESSION_FILENAME)))

    def test_corrupt_json_reads_empty_and_corrupt(self):
        path = session_path(self.dir)
        path.write_text("{not json", encoding="utf-8")
        data, corrupt = load_session(self.dir)
        self.assertEqual(data, empty_session())
        self.assertTrue(corrupt)
        # The bad file is left alone — never deleted, never
        # "repaired" on load.
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "{not json")

    def test_wrong_shape_reads_empty_and_corrupt(self):
        path = session_path(self.dir)
        for bad in ({"format": "something-else", "version": 1,
                     "last_image": None, "last_video": None,
                     "last_code": [], "recent": []},
                    {"format": "pixel-forge-session", "version": 1,
                     "last_image": 42, "last_video": None,
                     "last_code": [], "recent": []},
                    {"format": "pixel-forge-session", "version": 1,
                     "last_image": None, "last_video": None,
                     "last_code": "not a list", "recent": []},
                    {"format": "pixel-forge-session", "version": 1,
                     "last_image": None, "last_video": None,
                     "last_code": [], "recent": "not a list"},
                    ["not", "a", "dict"]):
            path.write_text(json.dumps(bad), encoding="utf-8")
            data, corrupt = load_session(self.dir)
            self.assertEqual(data, empty_session(),
                             "bad shape loaded: %r" % (bad,))
            self.assertTrue(corrupt, "bad shape not corrupt: %r"
                            % (bad,))

    def test_bad_recent_entries_are_dropped_good_ones_kept(self):
        path = session_path(self.dir)
        data = empty_session()
        data["recent"] = [
            {"path": "/tmp/good.png", "kind": "image"},
            {"path": "", "kind": "image"},
            {"path": "/tmp/no-kind.png"},
            {"path": "/tmp/bad-kind.png", "kind": "audio"},
            {"kind": "image"},
            "not a dict",
            {"path": "/tmp/also-good.mp4", "kind": "video"},
        ]
        data["last_code"] = ["/tmp/ok.py", "", 7, "/tmp/also-ok.py"]
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded, corrupt = load_session(self.dir)
        self.assertFalse(corrupt)
        self.assertEqual(loaded["recent"], [
            {"path": "/tmp/good.png", "kind": "image"},
            {"path": "/tmp/also-good.mp4", "kind": "video"}])
        self.assertEqual(loaded["last_code"],
                         ["/tmp/ok.py", "/tmp/also-ok.py"])

    def test_record_recent_is_most_recent_first(self):
        data = empty_session()
        record_recent(data, "/tmp/a.png", "image")
        record_recent(data, "/tmp/b.mp4", "video")
        record_recent(data, "/tmp/c.py", "code")
        self.assertEqual([e["path"] for e in data["recent"]],
                         ["/tmp/c.py", "/tmp/b.mp4", "/tmp/a.png"])

    def test_record_recent_dedupes_and_moves_to_top(self):
        data = empty_session()
        record_recent(data, "/tmp/a.png", "image")
        record_recent(data, "/tmp/b.mp4", "video")
        record_recent(data, "/tmp/a.png", "image")
        self.assertEqual([e["path"] for e in data["recent"]],
                         ["/tmp/a.png", "/tmp/b.mp4"])
        # A re-load under a different kind wins: the same file
        # loaded as a game asset after an image import is a skin
        # now, not two entries.
        record_recent(data, "/tmp/a.png", "skin")
        self.assertEqual(data["recent"],
                         [{"path": "/tmp/a.png", "kind": "skin"},
                          {"path": "/tmp/b.mp4", "kind": "video"}])

    def test_record_recent_caps_at_ten(self):
        data = empty_session()
        for i in range(MAX_RECENT + 4):
            record_recent(data, "/tmp/file-%02d.png" % i, "image")
        self.assertEqual(len(data["recent"]), MAX_RECENT)
        self.assertEqual(data["recent"][0]["path"],
                         "/tmp/file-13.png")
        self.assertEqual(data["recent"][-1]["path"],
                         "/tmp/file-04.png")

    def test_record_recent_ignores_blank_paths_and_bad_kinds(self):
        data = empty_session()
        record_recent(data, "", "image")
        record_recent(data, None, "image")
        record_recent(data, "/tmp/a.png", "audio")
        record_recent(data, "/tmp/a.png", "")
        self.assertEqual(data["recent"], [])

    def test_set_last_fields(self):
        data = empty_session()
        set_last_image(data, "/tmp/a.png")
        set_last_video(data, "/tmp/b.mp4")
        set_last_code(data, ["/tmp/c.py", "/tmp/folder"])
        self.assertEqual(data["last_image"], "/tmp/a.png")
        self.assertEqual(data["last_video"], "/tmp/b.mp4")
        self.assertEqual(data["last_code"],
                         ["/tmp/c.py", "/tmp/folder"])


if __name__ == "__main__":
    unittest.main()
