"""Tests for the live generation verification harness (task 70).

Qt-free: the harness runs on bare python, so these tests run
without PySide6. No test here runs a real generation — the
stub engine (tests/helpers.py) stands in for sd-cli, and the
one real run is the proof script's job.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import stub_env, write_stub_engine  # noqa: E402
from pixel_forge import selftest  # noqa: E402
from pixel_forge.imagegen import (MODEL_FILENAME,  # noqa: E402
                                  MODEL_SIZE_BYTES,
                                  engine_missing_text,
                                  model_missing_text)
from pixel_forge.selftest import (SELF_TEST_PROMPT, SELF_TEST_SEED,
                                  SELF_TEST_STEPS, format_duration,
                                  run_self_test, self_test_home)


class FormatDurationTests(unittest.TestCase):
    def test_sub_second(self):
        self.assertEqual(format_duration(0), "0.0 s")
        self.assertEqual(format_duration(0.4), "0.4 s")

    def test_seconds(self):
        self.assertEqual(format_duration(9.9), "9.9 s")
        self.assertEqual(format_duration(12), "12 s")
        self.assertEqual(format_duration(59), "59 s")

    def test_minutes(self):
        self.assertEqual(format_duration(60), "1 min 0 s")
        self.assertEqual(format_duration(192), "3 min 12 s")
        self.assertEqual(format_duration(390), "6 min 30 s")

    def test_negative_is_zero(self):
        self.assertEqual(format_duration(-5), "0.0 s")


class SelfTestHomeTests(unittest.TestCase):
    def test_home_is_under_the_installer_home(self):
        home = self_test_home()
        self.assertEqual(
            home, Path.home() / ".local" / "share" / "pixel-forge"
            / "self-test")


class RunSelfTestTests(unittest.TestCase):
    """The harness end to end against the stub engine."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.engine = write_stub_engine(cls.tmp)
        cls.model = os.path.join(cls.tmp, "dummy.gguf")
        with open(cls.model, "wb") as f:
            f.write(b"not a real model")

    def _run(self, out_dir=None, extra=None):
        messages = []

        def progress(stage, message):
            messages.append((stage, message))

        with stub_env(self.engine, self.model, extra=extra):
            report = run_self_test(out_dir=out_dir,
                                   progress=progress)
        return report, messages

    def test_success_reports_ok_with_files_and_timings(self):
        out = os.path.join(self.tmp, "ok")
        report, messages = self._run(out_dir=out)
        self.assertEqual(report["state"], "ok")
        self.assertIsNone(report["reason"])
        self.assertEqual(report["prompt"], SELF_TEST_PROMPT)
        self.assertEqual(report["seed"], SELF_TEST_SEED)
        self.assertEqual(report["steps"], SELF_TEST_STEPS)
        self.assertEqual(report["candidate"], "cand-01.png")
        self.assertTrue(report["candidate_bytes"] > 0)
        self.assertEqual(report["pixel_file"], "cand-01-pixel.png")
        self.assertTrue(report["pixel_bytes"] > 0)
        batch_dir = Path(report["batch_dir"])
        self.assertTrue((batch_dir / "cand-01.png").is_file())
        self.assertTrue(
            (batch_dir / "cand-01-pixel.png").is_file())
        self.assertTrue((batch_dir / "batch.json").is_file())
        self.assertEqual(batch_dir.parent, Path(out))
        timings = report["timings"]
        for key in ("checks", "generation", "pixelize", "total"):
            self.assertGreaterEqual(timings[key], 0)
        self.assertGreaterEqual(timings["total"],
                                timings["generation"])
        stages = [stage for stage, _ in messages]
        self.assertIn("checks", stages)
        self.assertIn("generate", stages)
        self.assertIn("pixelize", stages)

    def test_batch_records_the_fixed_seed_and_prompt(self):
        out = os.path.join(self.tmp, "seed")
        report, _ = self._run(out_dir=out)
        batch = json.loads(
            Path(report["batch_dir"], "batch.json").read_text())
        self.assertEqual(batch["prompt"], SELF_TEST_PROMPT)
        self.assertEqual(batch["steps"], SELF_TEST_STEPS)
        self.assertEqual(batch["candidates"][0]["seed"],
                         SELF_TEST_SEED)

    def test_report_is_json_serializable(self):
        out = os.path.join(self.tmp, "json")
        report, _ = self._run(out_dir=out)
        text = json.dumps(report)
        self.assertEqual(json.loads(text)["state"], "ok")

    def test_renamed_model_is_used_with_a_note(self):
        out = os.path.join(self.tmp, "renamed")
        report, messages = self._run(out_dir=out)
        self.assertEqual(report["state"], "ok")
        self.assertFalse(report["checks"]["model_is_pinned"])
        self.assertTrue(any(
            "not the pinned LCM file" in text
            for _, text in messages))

    def test_pinned_model_at_exact_size_passes(self):
        out = os.path.join(self.tmp, "pinned")
        pinned = os.path.join(self.tmp, MODEL_FILENAME)
        with open(pinned, "wb") as handle:
            handle.seek(MODEL_SIZE_BYTES - 1)
            handle.write(b"\0")
        with stub_env(self.engine, pinned):
            report = run_self_test(out_dir=out)
        self.assertEqual(report["state"], "ok")
        self.assertTrue(report["checks"]["model_is_pinned"])
        self.assertFalse(report["checks"]["model_partial"])
        self.assertEqual(report["checks"]["model_size"],
                         MODEL_SIZE_BYTES)

    def test_partial_model_is_a_plain_failure(self):
        out = os.path.join(self.tmp, "partial")
        partial = os.path.join(self.tmp, "partial-dir",
                               MODEL_FILENAME)
        os.makedirs(os.path.dirname(partial), exist_ok=True)
        with open(partial, "wb") as handle:
            handle.write(b"x" * (1024 * 1024))
        with stub_env(self.engine, partial):
            report = run_self_test(out_dir=out)
        self.assertEqual(report["state"], "failed")
        self.assertTrue(report["checks"]["model_partial"])
        self.assertIn("stopped partway", report["reason"])
        self.assertIn("1 MB", report["reason"])
        self.assertIn("2.0 GB", report["reason"])
        self.assertIsNone(report["batch_dir"])

    def test_missing_engine_is_a_plain_failure(self):
        out = os.path.join(self.tmp, "no-engine")
        with stub_env(self.engine, self.model), mock.patch.object(
                selftest, "find_engine", return_value=None):
            report = run_self_test(out_dir=out)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["reason"], engine_missing_text())
        self.assertIsNone(report["checks"]["engine"])

    def test_missing_model_is_a_plain_failure(self):
        out = os.path.join(self.tmp, "no-model")
        with stub_env(self.engine, self.model), mock.patch.object(
                selftest, "find_model", return_value=None):
            report = run_self_test(out_dir=out)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["reason"], model_missing_text())
        self.assertIsNone(report["checks"]["model"])

    def test_engine_failure_carries_the_engine_error(self):
        out = os.path.join(self.tmp, "fail")
        report, _ = self._run(out_dir=out,
                              extra={"STUB_FAIL": "1"})
        self.assertEqual(report["state"], "failed")
        self.assertIn("the generation failed", report["reason"])
        self.assertIn("stub engine: failing on purpose",
                      report["reason"])
        self.assertIsNotNone(report["batch_dir"])

    def test_pixelize_failure_is_a_plain_failure(self):
        out = os.path.join(self.tmp, "pixfail")
        with stub_env(self.engine, self.model), mock.patch.object(
                selftest, "pixelize_candidates",
                return_value=([], [("cand-01.png", "boom")])):
            report = run_self_test(out_dir=out)
        self.assertEqual(report["state"], "failed")
        self.assertIn("could not pixelize cand-01.png",
                      report["reason"])
        self.assertIn("boom", report["reason"])

    def test_low_memory_warns_but_does_not_refuse(self):
        out = os.path.join(self.tmp, "lowram")
        with stub_env(self.engine, self.model), mock.patch.object(
                selftest, "available_ram_bytes",
                return_value=1024 * 1024 * 1024):
            messages = []

            def progress(stage, message):
                messages.append((stage, message))

            report = run_self_test(out_dir=out,
                                   progress=progress)
        self.assertEqual(report["state"], "ok")
        self.assertIsNotNone(report["checks"]["ram_warning"])
        self.assertIn("1.0 GB", report["checks"]["ram_warning"])
        self.assertTrue(any("warning:" in text
                            for _, text in messages))

    def test_default_out_dir_is_the_self_test_home(self):
        home = os.path.join(self.tmp, "home")
        with stub_env(self.engine, self.model), mock.patch.object(
                selftest, "self_test_home",
                return_value=Path(home)):
            report = run_self_test()
        self.assertEqual(report["state"], "ok")
        self.assertEqual(Path(report["batch_dir"]).parent,
                         Path(home))


if __name__ == "__main__":
    unittest.main()
