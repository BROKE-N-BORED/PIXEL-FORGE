"""Prompt-to-sprite tests — engine/model lookup, batches, generation.

Generation runs against a fake sd-cli (tests/helpers.py: a Python
script that answers the real sd-cli flags and writes a seed-colored
PNG), because no engine or model exists in this sandbox. Live
generation against the real LCM model is NOT proven here — it needs
Joshua's PC, where the model pull is the long pole.

Qt-free (task 64): runs without PySide6.
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import stub_env, write_stub_engine  # noqa: E402
from pixel_forge import imagegen  # noqa: E402
from pixel_forge.imagegen import (DEFAULT_NEGATIVE,  # noqa: E402
                                  ENGINE_RELEASE, ENGINE_REPO,
                                  GenerationCancelled, MODEL_FILENAME,
                                  MODEL_SHA256, MODEL_URL,
                                  build_command, check_model_file,
                                  engine_clone_command,
                                  engine_missing_text, engine_status,
                                  file_sha256, find_engine, find_model,
                                  generate, load_batch,
                                  model_download_command,
                                  model_missing_text, new_batch,
                                  pixelize_candidates, prompt_to_sprites,
                                  save_batch, set_keeper, slugify,
                                  validate_prompt)
from pixel_forge.pixelize import PixelForgeError, load_image  # noqa: E402

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()


class ImageGenBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.stub = write_stub_engine(cls.tmp)
        cls.model = os.path.join(cls.tmp, "dummy.gguf")
        Path(cls.model).write_bytes(b"not a real model")

    def setUp(self):
        self.work = tempfile.mkdtemp(dir=self.tmp)

    def _batch(self, prompt="a knight", **kwargs):
        _batch, bdir = new_batch(self.work, prompt, **kwargs)
        return bdir


class FindTests(ImageGenBase):
    def test_find_engine_env_override(self):
        with stub_env(self.stub, self.model):
            self.assertEqual(find_engine(), Path(self.stub))

    def test_find_engine_env_bogus_falls_through(self):
        if imagegen.shutil.which("sd-cli") or imagegen.shutil.which("sd"):
            self.skipTest("a real sd-cli is installed on this machine")
        # PATH is an empty dir (the stub itself lives in cls.tmp,
        # so PATH must NOT point there).
        with mock.patch.dict(os.environ,
                             {"PIXELFORGE_SD_CLI": "/nonexistent/sd-cli",
                              "PATH": self.work}, clear=False):
            self.assertIsNone(find_engine())

    def test_find_engine_path_search(self):
        bindir = os.path.join(self.work, "bin")
        os.mkdir(bindir)
        write_stub_engine(bindir)
        env = {"PATH": bindir}
        env.pop("PIXELFORGE_SD_CLI", None)
        with mock.patch.dict(os.environ, env, clear=True):
            # HOME must survive: find_engine checks home candidates.
            os.environ["HOME"] = self.tmp
            self.assertEqual(find_engine(),
                             Path(bindir) / "sd-cli")

    def test_find_engine_checks_build_bin(self):
        # B15: imagegen checked ~/sd.cpp/build/sd-cli, a path the
        # build never produces — the real binary lives in
        # build/bin, the path install.sh already used.
        home = Path(self.work) / "home"
        bindir = home / "sd.cpp" / "build" / "bin"
        bindir.mkdir(parents=True)
        write_stub_engine(str(bindir))
        env = {"PATH": self.work, "HOME": str(home)}
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ.pop("PIXELFORGE_SD_CLI", None)
            self.assertEqual(find_engine(), bindir / "sd-cli")

    def test_engine_checked_lists_build_bin(self):
        # The checked-places list must agree with find_engine.
        with mock.patch.dict(os.environ, {"HOME": self.tmp},
                             clear=False):
            status = engine_status()
        checked = status["engine_checked"]
        self.assertIn(str(Path(self.tmp) / "sd.cpp" / "build"
                          / "bin" / "sd-cli"), checked)
        self.assertNotIn(str(Path(self.tmp) / "sd.cpp" / "build"
                             / "sd-cli"), checked)

    def test_find_model_env_override(self):
        with stub_env(self.stub, self.model):
            self.assertEqual(find_model(), Path(self.model))

    def test_find_model_shared_home_first(self):
        shared = Path(self.work) / "shared"
        own = Path(self.work) / "own"
        shared.mkdir()
        own.mkdir()
        (shared / MODEL_FILENAME).write_bytes(b"shared")
        (own / MODEL_FILENAME).write_bytes(b"own")
        with mock.patch.object(imagegen, "model_homes",
                               return_value=[shared, own]):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["HOME"] = self.tmp
                self.assertEqual(find_model(),
                                 shared / MODEL_FILENAME)
        (shared / MODEL_FILENAME).unlink()
        with mock.patch.object(imagegen, "model_homes",
                               return_value=[shared, own]):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["HOME"] = self.tmp
                self.assertEqual(find_model(), own / MODEL_FILENAME)

    def test_find_model_any_gguf_fallback(self):
        home_dir = Path(self.work) / "home"
        home_dir.mkdir()
        other = home_dir / "some-other-lcm.gguf"
        other.write_bytes(b"gguf")
        with mock.patch.object(imagegen, "model_homes",
                               return_value=[home_dir]):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["HOME"] = self.tmp
                self.assertEqual(find_model(), other)

    def test_find_model_missing_returns_none(self):
        with mock.patch.object(imagegen, "model_homes",
                               return_value=[Path(self.work) / "nope"]):
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ["HOME"] = self.tmp
                self.assertIsNone(find_model())

    def test_engine_status_reports_paths(self):
        with stub_env(self.stub, self.model):
            status = engine_status()
        self.assertEqual(status["engine"], Path(self.stub))
        self.assertEqual(status["model"], Path(self.model))
        self.assertTrue(status["engine_checked"])
        self.assertTrue(status["model_checked"])

    def test_engine_status_reports_missing(self):
        with mock.patch.object(imagegen, "find_engine",
                               return_value=None), \
                mock.patch.object(imagegen, "find_model",
                                  return_value=None):
            status = engine_status()
        self.assertIsNone(status["engine"])
        self.assertIsNone(status["model"])
        self.assertIn("/opt/sd.cpp/bin/sd-cli",
                      status["engine_checked"])

    def test_missing_texts_name_the_fix(self):
        self.assertIn("--with-image-engine", engine_missing_text())
        self.assertIn("nothing was generated", engine_missing_text())
        self.assertIn(MODEL_FILENAME, model_missing_text())
        self.assertIn("2.1 GB", model_missing_text())

    def test_model_missing_text_names_both_homes(self):
        # B13: the GUI status line once named only
        # ~/pixel-forge-models/, so a user following it
        # downloaded a second 2.1 GB copy instead of using the
        # shared home find_model checks first. This message
        # already named both — pin it, shared home first.
        text = model_missing_text()
        self.assertIn("~/design-shell-models/", text)
        self.assertIn("~/pixel-forge-models/", text)
        self.assertLess(text.index("~/design-shell-models/"),
                        text.index("~/pixel-forge-models/"),
                        "shared home first — find_model checks it first")


class PromptTests(ImageGenBase):
    def test_validate_prompt_strips_and_accepts(self):
        self.assertEqual(validate_prompt("  a knight  "), "a knight")

    def test_validate_prompt_empty_refuses(self):
        for bad in ("", "   ", None):
            with self.assertRaises(PixelForgeError) as ctx:
                validate_prompt(bad)
            self.assertIn("type a prompt first", str(ctx.exception))

    def test_validate_prompt_too_long_refuses(self):
        with self.assertRaises(PixelForgeError) as ctx:
            validate_prompt("x" * 1001)
        self.assertIn("keep it under", str(ctx.exception))

    def test_slugify(self):
        self.assertEqual(slugify("A Knight With A Red Shield!"),
                         "a-knight-with-a-red-shield")
        self.assertEqual(slugify("!!!"), "image")
        self.assertLessEqual(len(slugify("word " * 30)), 40)


class BatchTests(ImageGenBase):
    def test_new_batch_creates_dir_and_record(self):
        _batch, bdir = new_batch(self.work, "a knight")
        record = load_batch(bdir)
        self.assertEqual(record["prompt"], "a knight")
        self.assertEqual(record["negative"], DEFAULT_NEGATIVE)
        self.assertEqual(record["count_per_round"], 3)
        self.assertEqual(record["size"], 512)
        self.assertEqual(record["steps"], 4)
        self.assertEqual(record["candidates"], [])
        self.assertIsNone(record["keeper"])
        self.assertTrue((bdir / "batch.json").is_file())

    def test_new_batch_unique_dirs(self):
        _b1, d1 = new_batch(self.work, "a knight")
        _b2, d2 = new_batch(self.work, "a knight")
        self.assertNotEqual(d1, d2)
        self.assertTrue(d1.is_dir() and d2.is_dir())

    def test_new_batch_empty_prompt_refuses(self):
        with self.assertRaises(PixelForgeError):
            new_batch(self.work, "   ")

    def test_load_batch_missing_raises(self):
        with self.assertRaises(PixelForgeError):
            load_batch(self.work)

    def test_set_keeper_marks_and_records(self):
        bdir = self._batch()
        batch = load_batch(bdir)
        batch["candidates"] = [
            {"file": "cand-01.png", "seed": 1, "status": "pending"},
            {"file": "cand-02.png", "seed": 2, "status": "pending"},
        ]
        from pixel_forge.imagegen import save_batch
        save_batch(bdir, batch)
        batch = set_keeper(bdir, "cand-02.png")
        self.assertEqual(batch["keeper"], "cand-02.png")
        kept = [c for c in batch["candidates"]
                if c["status"] == "kept"]
        self.assertEqual([c["file"] for c in kept], ["cand-02.png"])
        # Picking another keeper un-keeps the first.
        batch = set_keeper(bdir, "cand-01.png")
        self.assertEqual(batch["keeper"], "cand-01.png")
        self.assertEqual(batch["candidates"][1]["status"], "pending")

    def test_set_keeper_unknown_or_failed_refuses(self):
        bdir = self._batch()
        batch = load_batch(bdir)
        batch["candidates"] = [
            {"file": "cand-01.png", "seed": 1, "status": "failed",
             "error": "boom"},
        ]
        from pixel_forge.imagegen import save_batch
        save_batch(bdir, batch)
        with self.assertRaises(PixelForgeError):
            set_keeper(bdir, "cand-99.png")
        with self.assertRaises(PixelForgeError):
            set_keeper(bdir, "cand-01.png")


class CorruptBatchTests(ImageGenBase):
    """Review B5: a corrupt batch.json must raise PixelForgeError
    with a plain message — never a raw json error, TypeError, or
    KeyError out of a GUI slot — and save_batch must be atomic."""

    def test_load_batch_corrupt_json_raises_plain_error(self):
        bdir = self._batch()
        path = Path(bdir) / "batch.json"
        good = path.read_bytes()
        truncated = good[:len(good) // 2]
        path.write_bytes(truncated)
        with self.assertRaises(PixelForgeError) as ctx:
            load_batch(bdir)
        self.assertIn("batch.json", str(ctx.exception))
        self.assertIn("unreadable", str(ctx.exception))
        # The corrupt file is left alone, never "repaired".
        self.assertEqual(path.read_bytes(), truncated)

    def test_load_batch_wrong_shape_raises_plain_error(self):
        cases = {
            "top-level list": "[1, 2, 3]",
            "no prompt": json.dumps({"candidates": []}),
            "no candidates": json.dumps({"prompt": "a knight"}),
            "candidates not a list": json.dumps(
                {"prompt": "a knight", "candidates": {}}),
            "candidate missing file": json.dumps({
                "prompt": "a knight",
                "candidates": [{"seed": 1, "status": "pending"}]}),
            "candidate missing seed": json.dumps({
                "prompt": "a knight",
                "candidates": [{"file": "cand-01.png",
                                "status": "pending"}]}),
            "candidate missing status": json.dumps({
                "prompt": "a knight",
                "candidates": [{"file": "cand-01.png", "seed": 1}]}),
            "candidate not an object": json.dumps(
                {"prompt": "a knight", "candidates": ["cand-01.png"]}),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                bdir = self._batch()
                path = Path(bdir) / "batch.json"
                path.write_text(text)
                with self.assertRaises(PixelForgeError) as ctx:
                    load_batch(bdir)
                self.assertIn("batch.json", str(ctx.exception))
                self.assertIn("wrong shape", str(ctx.exception))
                self.assertEqual(path.read_text(), text)

    def test_save_batch_write_failure_keeps_previous(self):
        bdir = self._batch()
        batch = load_batch(bdir)
        path = Path(bdir) / "batch.json"
        before = path.read_bytes()
        batch["candidates"] = [
            {"file": "cand-01.png", "seed": 1, "status": "pending"}]
        with mock.patch("builtins.open",
                        side_effect=OSError("disk full")):
            with self.assertRaises(PixelForgeError) as ctx:
                save_batch(bdir, batch)
        self.assertIn("could not write", str(ctx.exception))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((Path(bdir) / "batch.json.tmp").exists())

    def test_save_batch_replace_failure_keeps_previous(self):
        bdir = self._batch()
        batch = load_batch(bdir)
        path = Path(bdir) / "batch.json"
        before = path.read_bytes()
        batch["candidates"] = [
            {"file": "cand-01.png", "seed": 1, "status": "pending"}]
        with mock.patch("os.replace",
                        side_effect=OSError("no space")):
            with self.assertRaises(PixelForgeError) as ctx:
                save_batch(bdir, batch)
        self.assertIn("could not write", str(ctx.exception))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((Path(bdir) / "batch.json.tmp").exists())

    def test_save_then_load_round_trips(self):
        bdir = self._batch()
        batch = load_batch(bdir)
        batch["candidates"] = [
            {"file": "cand-01.png", "seed": 7, "status": "kept"},
            {"file": "cand-02.png", "seed": 8, "status": "failed",
             "error": "boom"},
        ]
        batch["keeper"] = "cand-01.png"
        save_batch(bdir, batch)
        self.assertFalse((Path(bdir) / "batch.json.tmp").exists())
        self.assertEqual(load_batch(bdir), batch)
        self.assertEqual((Path(bdir) / "batch.json").read_text(),
                         json.dumps(batch, indent=2) + "\n")


class CommandTests(ImageGenBase):
    def test_build_command_pins(self):
        cmd = build_command("/opt/sd.cpp/bin/sd-cli", "/m/model.gguf",
                            "a knight", "blurry", 42, 512,
                            "/out/cand-01.png")
        self.assertEqual(cmd[0], "/opt/sd.cpp/bin/sd-cli")
        self.assertEqual(cmd[cmd.index("-m") + 1], "/m/model.gguf")
        self.assertEqual(cmd[cmd.index("-p") + 1], "a knight")
        self.assertEqual(cmd[cmd.index("-n") + 1], "blurry")
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")
        self.assertEqual(cmd[cmd.index("--cfg-scale") + 1], "1.5")
        self.assertEqual(cmd[cmd.index("--sampling-method") + 1], "lcm")
        self.assertEqual(cmd[cmd.index("-W") + 1], "512")
        self.assertEqual(cmd[cmd.index("-H") + 1], "512")
        self.assertEqual(cmd[cmd.index("-s") + 1], "42")
        self.assertEqual(cmd[cmd.index("-o") + 1], "/out/cand-01.png")


class InstallPinTests(ImageGenBase):
    """Task 31 (core): the engine pin and the model hash are the
    one source of truth in imagegen.py — the installers use them,
    they are not defined-and-forgotten. The hash tests use a SMALL
    file with a patched MODEL_SHA256; no test downloads 2.1 GB."""

    def test_engine_clone_command_pins_release(self):
        cmd = engine_clone_command("/tmp/sd")
        self.assertEqual(cmd[0], "git")
        self.assertEqual(cmd[1], "clone")
        self.assertEqual(cmd[cmd.index("--branch") + 1], ENGINE_RELEASE)
        self.assertIn("--recursive", cmd)
        self.assertEqual(cmd[cmd.index("--depth") + 1], "1")
        self.assertIn(ENGINE_REPO, cmd)
        self.assertEqual(cmd[-1], "/tmp/sd")

    def test_clone_command_uses_the_constant(self):
        # The bug being fixed was a defined-but-unreferenced pin:
        # change the constant and the command must follow it, or
        # the command carries a duplicated literal.
        with mock.patch.object(imagegen, "ENGINE_RELEASE",
                               "sentinel-release-1"):
            cmd = engine_clone_command("/tmp/sd")
        self.assertIn("sentinel-release-1", cmd)
        self.assertNotIn(ENGINE_RELEASE, cmd)

    def test_model_download_command_resumes(self):
        cmd = model_download_command("/m/model.gguf")
        self.assertEqual(cmd[0], "curl")
        self.assertIn("-C", cmd)
        self.assertEqual(cmd[cmd.index("-C") + 1], "-")
        self.assertIn(MODEL_URL, cmd)
        self.assertEqual(cmd[cmd.index("-o") + 1], "/m/model.gguf")

    def test_file_sha256_matches_hashlib(self):
        path = os.path.join(self.work, "small.bin")
        data = b"pixel-forge pin test"
        Path(path).write_bytes(data)
        self.assertEqual(file_sha256(path),
                         hashlib.sha256(data).hexdigest())

    def test_check_model_file_accepts_matching_hash(self):
        path = os.path.join(self.work, "small.gguf")
        data = b"a small stand-in for the model"
        Path(path).write_bytes(data)
        with mock.patch.object(
                imagegen, "MODEL_SHA256",
                hashlib.sha256(data).hexdigest()):
            check_model_file(path)  # must not raise

    def test_check_model_file_rejects_wrong_hash(self):
        path = os.path.join(self.work, "small.gguf")
        data = b"a small stand-in for the model"
        Path(path).write_bytes(data)
        actual = hashlib.sha256(data).hexdigest()
        wrong = "0" * 64
        with mock.patch.object(imagegen, "MODEL_SHA256", wrong):
            with self.assertRaises(PixelForgeError) as caught:
                check_model_file(path)
        message = str(caught.exception)
        self.assertIn(wrong, message)
        self.assertIn(actual, message)
        self.assertIn(str(path), message)


class GenerateTests(ImageGenBase):
    def test_generate_three_candidates(self):
        bdir = self._batch()
        with stub_env(self.stub, self.model):
            batch = generate(bdir)
        self.assertEqual(len(batch["candidates"]), 3)
        seeds = [c["seed"] for c in batch["candidates"]]
        self.assertEqual(len(set(seeds)), 3)
        for index, record in enumerate(batch["candidates"], start=1):
            self.assertEqual(record["file"], "cand-%02d.png" % index)
            self.assertEqual(record["status"], "pending")
            path = bdir / record["file"]
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)
        self.assertEqual(batch["engine"], self.stub)
        self.assertEqual(batch["model"], "dummy.gguf")
        # The batch on disk matches — a keeper stays reproducible.
        self.assertEqual(load_batch(bdir)["candidates"],
                         batch["candidates"])

    def test_generate_command_received_by_stub(self):
        bdir = self._batch()
        log = os.path.join(self.work, "argv.log")
        with stub_env(self.stub, self.model, {"STUB_LOG": log}):
            generate(bdir)
        lines = Path(log).read_text().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("--steps 4", line)
            self.assertIn("--cfg-scale 1.5", line)
            self.assertIn("--sampling-method lcm", line)
            self.assertIn("-W 512 -H 512", line)
            self.assertIn("-m %s" % self.model, line)
            self.assertIn("-p a knight", line)
        seeds = [line.split("-s ")[1].split(" ")[0] for line in lines]
        self.assertEqual(len(set(seeds)), 3)

    def test_generate_progress_callback(self):
        bdir = self._batch()
        calls = []
        with stub_env(self.stub, self.model):
            generate(bdir, progress=lambda d, t, r:
                     calls.append((d, t, r["file"])))
        self.assertEqual(calls, [(1, 3, "cand-01.png"),
                                 (2, 3, "cand-02.png"),
                                 (3, 3, "cand-03.png")])

    def test_generate_missing_engine_refuses(self):
        bdir = self._batch()
        with mock.patch.object(imagegen, "find_engine",
                               return_value=None):
            with self.assertRaises(PixelForgeError) as ctx:
                generate(bdir)
        self.assertIn("--with-image-engine", str(ctx.exception))
        self.assertEqual(load_batch(bdir)["candidates"], [])

    def test_generate_missing_model_refuses(self):
        bdir = self._batch()
        with stub_env(self.stub, self.model):
            with mock.patch.object(imagegen, "find_model",
                                   return_value=None):
                with self.assertRaises(PixelForgeError) as ctx:
                    generate(bdir)
        self.assertIn(MODEL_FILENAME, str(ctx.exception))
        self.assertEqual(load_batch(bdir)["candidates"], [])

    def test_generate_failed_candidate_recorded(self):
        bdir = self._batch()
        with stub_env(self.stub, self.model, {"STUB_FAIL": "1"}):
            batch = generate(bdir)
        self.assertEqual(len(batch["candidates"]), 3)
        self.assertTrue(all(c["status"] == "failed"
                            for c in batch["candidates"]))
        self.assertIn("failing on purpose",
                      batch["candidates"][0]["error"])
        self.assertFalse((bdir / "cand-01.png").exists())

    def test_generate_cancel_before_start(self):
        bdir = self._batch()
        event = threading.Event()
        event.set()
        with stub_env(self.stub, self.model):
            with self.assertRaises(GenerationCancelled):
                generate(bdir, cancel_event=event)
        batch = load_batch(bdir)
        self.assertTrue(batch["cancelled"])
        self.assertEqual(batch["candidates"], [])

    def test_generate_cancel_mid_run(self):
        bdir = self._batch()
        event = threading.Event()
        timer = threading.Timer(1.6, event.set)
        with stub_env(self.stub, self.model, {"STUB_DELAY": "1"}):
            timer.start()
            try:
                with self.assertRaises(GenerationCancelled):
                    generate(bdir, cancel_event=event)
            finally:
                timer.cancel()
        batch = load_batch(bdir)
        self.assertTrue(batch["cancelled"])
        landed = [c for c in batch["candidates"]
                  if c["status"] == "pending"]
        self.assertEqual(len(landed), 1)

    def test_generate_timeout_marks_failed(self):
        bdir = self._batch()
        with stub_env(self.stub, self.model, {"STUB_DELAY": "3"}):
            batch = generate(bdir, count=1, timeout_s=1)
        self.assertEqual(batch["candidates"][0]["status"], "failed")
        self.assertIn("timed out", batch["candidates"][0]["error"])

    def test_pixelize_candidates(self):
        bdir = self._batch()
        with stub_env(self.stub, self.model):
            generate(bdir)
        pairs = pixelize_candidates(bdir, width=32, max_colors=16)
        self.assertEqual(len(pairs), 3)
        for _cand, pixel in pairs:
            path = bdir / pixel
            self.assertTrue(path.is_file())
            image = load_image(str(path))
            self.assertEqual((image.width(), image.height()), (32, 32))
        batch = load_batch(bdir)
        self.assertTrue(all("pixel_file" in c
                            for c in batch["candidates"]))

    def test_pixelize_candidates_skips_failed(self):
        bdir = self._batch()
        with stub_env(self.stub, self.model, {"STUB_FAIL": "1"}):
            generate(bdir)
        pairs = pixelize_candidates(bdir)
        self.assertEqual(pairs, [])

    def test_prompt_to_sprites_end_to_end(self):
        out_dir = os.path.join(self.work, "out")
        with stub_env(self.stub, self.model):
            batch, bdir = prompt_to_sprites("a knight", out_dir,
                                            width=32)
        self.assertEqual(len(batch["candidates"]), 3)
        self.assertEqual(batch["prompt"], "a knight")
        for index in (1, 2, 3):
            self.assertTrue(
                (bdir / ("cand-%02d.png" % index)).is_file())
            self.assertTrue(
                (bdir / ("cand-%02d-pixel.png" % index)).is_file())
        self.assertTrue((bdir / "batch.json").is_file())

    def test_prompt_to_sprites_all_failed_raises(self):
        out_dir = os.path.join(self.work, "out-fail")
        with stub_env(self.stub, self.model, {"STUB_FAIL": "1"}):
            with self.assertRaises(PixelForgeError) as ctx:
                prompt_to_sprites("a knight", out_dir)
        self.assertIn("all 3 candidates failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
