"""Prompt history tests — task 20.

Qt-free: the history module is stdlib-only, so these run on
bare python.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.history import (  # noqa: E402
    HISTORY_FILENAME, MAX_BATCHES, MAX_PROMPTS, batches_dir,
    empty_history, history_path, load_history, record_batch,
    record_prompt, save_history)


def _entry(prompt="a knight", directory="/tmp/batches/a-knight-123"):
    return {"prompt": prompt, "negative": "blurry",
            "seeds": [11, 22, 33], "keeper": "cand-01.png",
            "created": "2026-11-13 01:50:00", "dir": directory,
            "steps": 4, "cfg": 1.5, "size": 512}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_missing_file_reads_empty_not_corrupt(self):
        data, corrupt = load_history(self.dir)
        self.assertEqual(data, empty_history())
        self.assertFalse(corrupt)

    def test_save_and_load_round_trip(self):
        data = empty_history()
        record_batch(data, _entry())
        record_prompt(data, "a knight")
        save_history(self.dir, data)
        loaded, corrupt = load_history(self.dir)
        self.assertFalse(corrupt)
        self.assertEqual(loaded, data)
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, HISTORY_FILENAME)))

    def test_save_creates_the_folder(self):
        nested = os.path.join(self.dir, "deep", "library")
        save_history(nested, empty_history())
        self.assertTrue(os.path.isfile(
            os.path.join(nested, HISTORY_FILENAME)))

    def test_corrupt_json_reads_empty_and_corrupt(self):
        path = history_path(self.dir)
        path.write_text("{not json", encoding="utf-8")
        data, corrupt = load_history(self.dir)
        self.assertEqual(data, empty_history())
        self.assertTrue(corrupt)
        # The bad file is left alone — never deleted, never
        # "repaired" on load.
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "{not json")

    def test_wrong_shape_reads_empty_and_corrupt(self):
        path = history_path(self.dir)
        path.write_text(json.dumps({"format": "something else",
                                    "batches": [], "prompts": []}))
        data, corrupt = load_history(self.dir)
        self.assertEqual(data, empty_history())
        self.assertTrue(corrupt)

    def test_one_bad_batch_entry_drops_only_itself(self):
        data = empty_history()
        data["batches"] = [_entry(), {"prompt": "no dir here"},
                           "not a dict", _entry(prompt="a slime")]
        save_history(self.dir, data)
        loaded, corrupt = load_history(self.dir)
        self.assertFalse(corrupt)
        self.assertEqual([e["prompt"] for e in loaded["batches"]],
                         ["a knight", "a slime"])

    def test_record_batch_most_recent_first_and_capped(self):
        data = empty_history()
        for i in range(MAX_BATCHES + 5):
            record_batch(data, _entry(prompt="batch %d" % i))
        self.assertEqual(len(data["batches"]), MAX_BATCHES)
        self.assertEqual(data["batches"][0]["prompt"],
                         "batch %d" % (MAX_BATCHES + 4))
        self.assertEqual(data["batches"][-1]["prompt"], "batch 5")

    def test_record_prompt_dedups_most_recent_first_and_capped(self):
        data = empty_history()
        record_prompt(data, "a knight")
        record_prompt(data, "a slime")
        record_prompt(data, "a knight")
        self.assertEqual(data["prompts"], ["a knight", "a slime"])
        for i in range(MAX_PROMPTS + 5):
            record_prompt(data, "prompt %d" % i)
        self.assertEqual(len(data["prompts"]), MAX_PROMPTS)
        self.assertEqual(data["prompts"][0],
                         "prompt %d" % (MAX_PROMPTS + 4))

    def test_record_prompt_skips_blanks(self):
        data = empty_history()
        record_prompt(data, "   ")
        record_prompt(data, "")
        self.assertEqual(data["prompts"], [])

    def test_batches_dir_is_under_the_library_folder(self):
        from pathlib import Path
        self.assertEqual(batches_dir(self.dir),
                         Path(self.dir) / "batches")


if __name__ == "__main__":
    unittest.main()
