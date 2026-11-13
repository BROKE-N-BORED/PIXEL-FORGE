"""CLI tests — in-process plus one real subprocess smoke test.

Qt-free (task 64): the CLI runs on bare python, so these tests
run without PySide6."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge import __version__  # noqa: E402

from helpers import (make_gradient_image, stub_env,  # noqa: E402
                     write_image, write_stub_engine)
from pixel_forge.cli import main  # noqa: E402
from pixel_forge.library import LIBRARY_FORMAT  # noqa: E402
from pixel_forge.pixelize import load_image  # noqa: E402

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

ROOT = os.path.join(os.path.dirname(__file__), "..")


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))
        cls.stub = write_stub_engine(cls.tmp)
        cls.model = os.path.join(cls.tmp, "dummy.gguf")
        with open(cls.model, "wb") as f:
            f.write(b"not a real model")

    def _run(self, argv):
        """Run main(argv), capturing stdout/stderr and the exit code."""
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_version(self):
        code, out, _err = self._run(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "pixel-forge %s" % __version__)

    def test_help(self):
        code, out, _err = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("convert", out)
        self.assertIn("pixel-forge", out)

    def test_convert_help(self):
        code, out, _err = self._run(["convert", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--size", out)
        self.assertIn("--palette", out)
        self.assertIn("--dither", out)

    def test_convert_happy_path(self):
        out_path = os.path.join(self.tmp, "sprite.png")
        code, out, err = self._run(["convert", self.src, "--size", "32",
                                    "--colors", "16", "--palette", "pico8",
                                    "--out", out_path])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(out_path))
        self.assertIn("32 x 32", out)
        image = load_image(out_path)
        self.assertEqual((image.width(), image.height()), (32, 32))

    def test_convert_default_output_name(self):
        code, out, err = self._run(["convert", self.src, "--size", "16"])
        self.assertEqual(code, 0, err)
        expected = os.path.join(self.tmp, "photo-pixel.png")
        self.assertTrue(os.path.isfile(expected))
        image = load_image(expected)
        self.assertEqual((image.width(), image.height()), (16, 16))

    def test_convert_width_by_height_size(self):
        out_path = os.path.join(self.tmp, "wide.png")
        code, _out, err = self._run(["convert", self.src,
                                     "--size", "64x48",
                                     "--out", out_path])
        self.assertEqual(code, 0, err)
        image = load_image(out_path)
        self.assertEqual((image.width(), image.height()), (64, 48))

    def test_convert_keep_aspect(self):
        out_path = os.path.join(self.tmp, "aspect.png")
        code, _out, err = self._run(["convert", self.src, "--size", "32",
                                     "--keep-aspect", "--out", out_path])
        self.assertEqual(code, 0, err)
        image = load_image(out_path)
        # Source is 200 x 100, so it fits into 32 x 32 as 32 x 16.
        self.assertEqual((image.width(), image.height()), (32, 16))

    def test_convert_no_dither(self):
        out_path = os.path.join(self.tmp, "nodither.png")
        code, _out, err = self._run(["convert", self.src, "--size", "16",
                                     "--no-dither", "--out", out_path])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(out_path))

    def test_convert_missing_input_fails_cleanly(self):
        code, _out, err = self._run(
            ["convert", os.path.join(self.tmp, "missing.png")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def test_convert_bad_size_fails_cleanly(self):
        code, _out, err = self._run(["convert", self.src,
                                     "--size", "banana"])
        self.assertEqual(code, 1)
        self.assertIn("bad --size", err)
        self.assertNotIn("Traceback", err)

    def test_convert_bad_colors_fails_cleanly(self):
        code, _out, err = self._run(["convert", self.src, "--colors", "1"])
        self.assertEqual(code, 1)
        self.assertIn("colors must be between", err)
        self.assertNotIn("Traceback", err)

    def test_convert_bad_palette_is_argparse_error(self):
        code, _out, err = self._run(["convert", self.src,
                                     "--palette", "neon"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_no_command_prints_help_with_error(self):
        code, _out, err = self._run([])
        self.assertEqual(code, 2)
        self.assertIn("usage", err)

    def test_subprocess_entry_point(self):
        proc = subprocess.run(
            [sys.executable, "-m", "pixel_forge.cli", "--version"],
            cwd=os.path.abspath(ROOT), capture_output=True, text=True,
            timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(),
                         "pixel-forge %s" % __version__)

    def test_prompt_help(self):
        code, out, _err = self._run(["prompt", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--count", out)
        self.assertIn("--negative", out)
        self.assertIn("--size", out)
        self.assertIn("--palette", out)
        # Task 20: generation settings. --size is already the
        # pixelize size, so the generated size is --gen-size.
        self.assertIn("--steps", out)
        self.assertIn("--cfg", out)
        self.assertIn("--gen-size", out)

    def test_prompt_generation_settings_reach_the_engine(self):
        out_dir = os.path.join(self.tmp, "settings-out")
        log = os.path.join(self.tmp, "settings-argv.log")
        with stub_env(self.stub, self.model, {"STUB_LOG": log}):
            code, _out, err = self._run(
                ["prompt", "a knight", "--out", out_dir,
                 "--steps", "8", "--cfg", "2.5", "--gen-size", "256",
                 "--size", "32"])
        self.assertEqual(code, 0, err)
        lines = open(log).read().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("--steps 8", line)
            self.assertIn("--cfg-scale 2.5", line)
            self.assertIn("-W 256 -H 256", line)
        batches = [d for d in os.listdir(out_dir)
                   if os.path.isdir(os.path.join(out_dir, d))]
        batch = json.load(open(os.path.join(
            out_dir, batches[0], "batch.json")))
        self.assertEqual(batch["steps"], 8)
        self.assertEqual(batch["cfg"], 2.5)
        self.assertEqual(batch["size"], 256)

    def test_prompt_bad_generation_settings_fail_cleanly(self):
        cases = (["--steps", "0"], ["--cfg", "0"],
                 ["--gen-size", "0"])
        for flag in cases:
            code, _out, err = self._run(
                ["prompt", "a knight"] + flag)
            self.assertEqual(code, 1, flag)
            self.assertNotIn("Traceback", err)

    def test_prompt_happy_path(self):
        out_dir = os.path.join(self.tmp, "knight-out")
        with stub_env(self.stub, self.model):
            code, out, err = self._run(
                ["prompt", "a knight", "--out", out_dir,
                 "--size", "32", "--colors", "16"])
        self.assertEqual(code, 0, err)
        self.assertIn("3 candidates", out)
        # One batch folder under the out dir.
        batches = [d for d in os.listdir(out_dir)
                   if os.path.isdir(os.path.join(out_dir, d))]
        self.assertEqual(len(batches), 1)
        bdir = os.path.join(out_dir, batches[0])
        for index in (1, 2, 3):
            self.assertTrue(os.path.isfile(
                os.path.join(bdir, "cand-%02d.png" % index)))
            pixel = os.path.join(bdir,
                                 "cand-%02d-pixel.png" % index)
            self.assertTrue(os.path.isfile(pixel))
            image = load_image(pixel)
            self.assertEqual((image.width(), image.height()),
                             (32, 32))
        self.assertTrue(os.path.isfile(
            os.path.join(bdir, "batch.json")))

    def test_prompt_default_output_name(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with stub_env(self.stub, self.model):
                code, _out, err = self._run(["prompt", "a slime"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.isdir(
                os.path.join(self.tmp, "a-slime-prompt")))
        finally:
            os.chdir(cwd)

    def test_prompt_names_the_model_on_stderr(self):
        # B15: find_model picks up ANY .gguf, so a wrong model was
        # used without a word. The CLI names the model it uses on
        # stderr before generating — and flags it when it is not
        # the pinned LCM file (the stub model here is dummy.gguf).
        out_dir = os.path.join(self.tmp, "model-named-out")
        with stub_env(self.stub, self.model):
            code, _out, err = self._run(
                ["prompt", "a knight", "--out", out_dir,
                 "--size", "32", "--colors", "16"])
        self.assertEqual(code, 0, err)
        self.assertIn("using model:", err)
        self.assertIn("dummy.gguf", err)
        self.assertIn("not the pinned LCM file", err)

    def test_prompt_warns_on_low_ram_and_proceeds(self):
        # Task 27: the CLI warns on stderr when memory is short
        # for a ~2.3 GB generation — and proceeds, because a
        # non-interactive command does not stop to ask.
        from unittest import mock
        from pixel_forge import cli as cli_mod
        out_dir = os.path.join(self.tmp, "low-ram-out")
        with stub_env(self.stub, self.model), \
                mock.patch.object(cli_mod, "available_ram_bytes",
                                  return_value=1 * 1024 ** 3):
            code, _out, err = self._run(
                ["prompt", "a knight", "--out", out_dir,
                 "--size", "32", "--colors", "16"])
        self.assertEqual(code, 0, err)
        self.assertIn("only 1.0 GB of memory is free", err)
        self.assertIn("2.1 GB", err)

    def test_prompt_stays_silent_on_plenty_of_ram(self):
        # Plenty of memory: no RAM warning at all.
        from unittest import mock
        from pixel_forge import cli as cli_mod
        out_dir = os.path.join(self.tmp, "plenty-ram-out")
        with stub_env(self.stub, self.model), \
                mock.patch.object(cli_mod, "available_ram_bytes",
                                  return_value=8 * 1024 ** 3):
            code, _out, err = self._run(
                ["prompt", "a knight", "--out", out_dir,
                 "--size", "32", "--colors", "16"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("memory is free", err)

    def test_prompt_reports_pixelize_failures_on_stderr(self):
        # Task 27: a candidate that failed to pixelize gets one
        # plain stderr line naming it — the batch record carries
        # the reason, the CLI prints it.
        from unittest import mock
        from pathlib import Path
        from pixel_forge import cli as cli_mod
        bdir = Path(self.tmp) / "pixel-fail-out"
        batch = {"candidates": [
            {"file": "cand-01.png", "status": "pending",
             "pixel_file": "cand-01-pixel.png"},
            {"file": "cand-02.png", "status": "pending",
             "pixel_error": "could not load cand-02.png"},
            {"file": "cand-03.png", "status": "pending",
             "pixel_file": "cand-03-pixel.png"},
        ]}
        with stub_env(self.stub, self.model), \
                mock.patch.object(cli_mod, "prompt_to_sprites",
                                  return_value=(batch, bdir)):
            code, _out, err = self._run(
                ["prompt", "a knight", "--out", str(bdir)])
        self.assertEqual(code, 0, err)
        self.assertIn("could not pixelize cand-02.png", err)
        self.assertIn("could not load cand-02.png", err)
        self.assertNotIn("cand-01.png", err)

    def test_prompt_missing_engine_fails_cleanly(self):
        from unittest import mock
        from pixel_forge import imagegen as imagegen_mod
        out_dir = os.path.join(self.tmp, "no-engine-out")
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None):
            code, _out, err = self._run(
                ["prompt", "a knight", "--out", out_dir])
        self.assertEqual(code, 1)
        self.assertIn("--with-image-engine", err)
        self.assertNotIn("Traceback", err)

    def test_prompt_empty_fails_cleanly(self):
        code, _out, err = self._run(["prompt", "   "])
        self.assertEqual(code, 1)
        self.assertIn("type a prompt first", err)
        self.assertNotIn("Traceback", err)

    def test_prompt_bad_count_fails_cleanly(self):
        code, _out, err = self._run(["prompt", "a knight",
                                     "--count", "0"])
        self.assertEqual(code, 1)
        self.assertIn("count must be at least 1", err)
        self.assertNotIn("Traceback", err)


class SheetCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        from helpers import make_solid_image
        cls.frames = os.path.join(cls.tmp, "walk-frames")
        os.makedirs(cls.frames)
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                  (255, 255, 0), (255, 0, 255), (0, 255, 255)]
        for index, color in enumerate(colors, start=1):
            write_image(make_solid_image(32, 32, *color),
                        os.path.join(cls.frames,
                                     "frame_%03d.png" % index))
        cls.empty = os.path.join(cls.tmp, "empty-frames")
        os.makedirs(cls.empty)
        cls.mixed = os.path.join(cls.tmp, "mixed-frames")
        os.makedirs(cls.mixed)
        write_image(make_solid_image(32, 32, 1, 2, 3),
                    os.path.join(cls.mixed, "frame_001.png"))
        write_image(make_solid_image(16, 16, 1, 2, 3),
                    os.path.join(cls.mixed, "frame_002.png"))
        # B9: a stray cover.png plus a name with a number in it.
        cls.stray = os.path.join(cls.tmp, "stray-frames")
        os.makedirs(cls.stray)
        stray = [("frame_001.png", (255, 0, 0)),
                 ("frame_002.png", (0, 255, 0)),
                 ("frame_010.png", (0, 0, 255)),
                 ("cover.png", (255, 255, 0)),
                 ("walk2.png", (255, 0, 255))]
        for name, color in stray:
            write_image(make_solid_image(32, 32, *color),
                        os.path.join(cls.stray, name))

    def _run(self, argv):
        """Run main(argv), capturing stdout/stderr and the exit code."""
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_sheet_help(self):
        code, out, _err = self._run(["sheet", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--cols", out)
        self.assertIn("--padding", out)
        self.assertIn("--fps", out)
        self.assertIn("--meta", out)

    def test_sheet_happy_path(self):
        import json
        out_png = os.path.join(self.tmp, "walk-sheet.png")
        out_json = os.path.join(self.tmp, "walk-sheet.json")
        code, out, err = self._run(["sheet", self.frames,
                                    "--out", out_png,
                                    "--meta", out_json,
                                    "--cols", "3", "--padding", "2",
                                    "--fps", "12"])
        self.assertEqual(code, 0, err)
        self.assertIn("6 frames", out)
        self.assertIn("3 x 2 grid", out)
        self.assertTrue(os.path.isfile(out_png))
        self.assertTrue(os.path.isfile(out_json))
        image = load_image(out_png)
        self.assertEqual((image.width(), image.height()), (100, 66))
        with open(out_json) as handle:
            meta = json.load(handle)
        self.assertEqual(meta["format"], "pixel-forge-spritesheet")
        self.assertEqual(meta["sheet"], "walk-sheet.png")
        self.assertEqual((meta["columns"], meta["rows"]), (3, 2))
        self.assertEqual(meta["padding"], 2)
        self.assertEqual(meta["fps"], 12)
        self.assertEqual(len(meta["frames"]), 6)
        self.assertEqual([f["duration_ms"] for f in meta["frames"]],
                         [83] * 6)
        self.assertEqual(meta["frames"][0]["name"], "frame_001.png")

    def test_sheet_defaults(self):
        code, out, err = self._run(["sheet", self.frames])
        self.assertEqual(code, 0, err)
        # Defaults: <folder-stem>-sheet.png next to the folder, and
        # the JSON beside it with the same stem.
        expected_png = os.path.join(self.tmp, "walk-frames-sheet.png")
        expected_json = os.path.join(self.tmp,
                                     "walk-frames-sheet.json")
        self.assertTrue(os.path.isfile(expected_png))
        self.assertTrue(os.path.isfile(expected_json))
        image = load_image(expected_png)
        # Auto grid for 6 frames: 3 columns, 2 rows, no padding.
        self.assertEqual((image.width(), image.height()), (96, 64))

    def test_sheet_meta_defaults_to_out_stem(self):
        out_png = os.path.join(self.tmp, "custom-name.png")
        code, _out, err = self._run(["sheet", self.frames,
                                     "--out", out_png])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "custom-name.json")))

    def test_sheet_stray_png_last_in_meta(self):
        # B9 end-to-end: numbered frames first in numeric order,
        # the stray cover.png last — never frame 0.
        import json
        out_png = os.path.join(self.tmp, "stray-sheet.png")
        out_json = os.path.join(self.tmp, "stray-sheet.json")
        code, _out, err = self._run(["sheet", self.stray,
                                     "--out", out_png,
                                     "--meta", out_json])
        self.assertEqual(code, 0, err)
        with open(out_json) as handle:
            meta = json.load(handle)
        self.assertEqual([f["name"] for f in meta["frames"]],
                         ["frame_001.png", "frame_002.png",
                          "walk2.png", "frame_010.png", "cover.png"])

    def test_sheet_missing_dir_fails_cleanly(self):
        code, _out, err = self._run(
            ["sheet", os.path.join(self.tmp, "nope")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def test_sheet_empty_dir_fails_cleanly(self):
        code, _out, err = self._run(["sheet", self.empty])
        self.assertEqual(code, 1)
        self.assertIn("no PNG frames", err)
        self.assertNotIn("Traceback", err)

    def test_sheet_mixed_sizes_fail_cleanly(self):
        code, _out, err = self._run(["sheet", self.mixed])
        self.assertEqual(code, 1)
        self.assertIn("same size", err)
        self.assertNotIn("Traceback", err)

    def test_sheet_bad_flags_fail_cleanly(self):
        for argv, needle in (
                (["--cols", "-1"], "--cols"),
                (["--padding", "-1"], "--padding"),
                (["--fps", "0"], "fps")):
            code, _out, err = self._run(["sheet", self.frames] + argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(needle, err, argv)
            self.assertNotIn("Traceback", err, argv)


class LibraryCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))

    def setUp(self):
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self._saved = os.environ.get("PIXELFORGE_LIBRARY")
        os.environ["PIXELFORGE_LIBRARY"] = self.lib_dir

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("PIXELFORGE_LIBRARY", None)
        else:
            os.environ["PIXELFORGE_LIBRARY"] = self._saved

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_library_list_empty(self):
        code, out, _err = self._run(["library", "list"])
        self.assertEqual(code, 0)
        self.assertIn("Library is empty", out)
        self.assertIn(self.lib_dir, out)
        self.assertFalse(os.path.exists(self.lib_dir))

    def test_library_add_and_list(self):
        code, out, err = self._run(
            ["library", "add", self.src, "--name", "Red Knight",
             "--tags", "hero,red"])
        self.assertEqual(code, 0, err)
        self.assertIn("Saved Red Knight", out)
        self.assertTrue(os.path.isfile(
            os.path.join(self.lib_dir, "red-knight.png")))
        code, out, _err = self._run(["library", "list"])
        self.assertEqual(code, 0)
        self.assertIn("Red Knight", out)
        self.assertIn("hero, red", out)
        self.assertIn("red-knight.png", out)

    def test_library_list_tag_filter(self):
        self._run(["library", "add", self.src, "--name", "Knight",
                   "--tags", "hero"])
        self._run(["library", "add", self.src, "--name", "Slime",
                   "--tags", "enemy"])
        code, out, _err = self._run(["library", "list",
                                     "--tag", "enemy"])
        self.assertEqual(code, 0)
        self.assertIn("Slime", out)
        self.assertNotIn("Knight", out)
        code, out, _err = self._run(["library", "list",
                                     "--tag", "nobody"])
        self.assertEqual(code, 0)
        self.assertIn("No sprites with tag", out)

    def test_library_add_missing_file_fails_cleanly(self):
        code, _out, err = self._run(
            ["library", "add", os.path.join(self.tmp, "nope.png"),
             "--name", "Nope"])
        self.assertEqual(code, 1)
        self.assertIn("no such file", err)
        self.assertNotIn("Traceback", err)

    def _write_mixed_index(self):
        os.makedirs(self.lib_dir, exist_ok=True)
        good = {"id": "knight", "name": "Knight",
                "file": "knight.png", "kind": "sprite",
                "tags": ["hero"], "source": "image",
                "width": 32, "height": 32,
                "created": "2026-11-12T00:00:00+00:00"}
        no_name = dict(good, id="ghost", file="ghost.png")
        del no_name["name"]
        path = os.path.join(self.lib_dir, "library.json")
        with open(path, "w") as f:
            json.dump({"format": LIBRARY_FORMAT, "version": 1,
                       "entries": [good, no_name]}, f)
        return path, no_name

    def test_library_list_skips_malformed_entries(self):
        # v0.10: a malformed entry is skipped with a warning,
        # not fatal — and left in the file, not deleted.
        path, _no_name = self._write_mixed_index()
        before = open(path, "rb").read()
        code, out, err = self._run(["library", "list"])
        self.assertEqual(code, 0, err)
        self.assertIn("Knight", out)
        self.assertIn("skipped 1 malformed entry", err)
        self.assertEqual(open(path, "rb").read(), before)

    def test_library_add_preserves_malformed_entries(self):
        # v0.10: adding a sprite re-saves the index — the bad
        # entry must survive the save verbatim.
        path, no_name = self._write_mixed_index()
        code, out, err = self._run(
            ["library", "add", self.src, "--name", "Newcomer"])
        self.assertEqual(code, 0, err)
        self.assertIn("Saved Newcomer", out)
        self.assertIn("skipped 1 malformed entry", err)
        with open(path) as f:
            entries = json.load(f)["entries"]
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[1], no_name)
        self.assertEqual(entries[2]["name"], "Newcomer")

    def test_library_add_blank_name_fails_cleanly(self):
        code, _out, err = self._run(
            ["library", "add", self.src, "--name", "   "])
        self.assertEqual(code, 1)
        self.assertIn("give the sprite a name", err)
        self.assertNotIn("Traceback", err)

    def test_library_export_import_round_trip(self):
        self._run(["library", "add", self.src, "--name", "Knight",
                   "--tags", "hero"])
        backup = os.path.join(tempfile.mkdtemp(), "backup")
        code, out, _err = self._run(["library", "export", backup])
        self.assertEqual(code, 0)
        self.assertIn("Exported 1 sprite", out)
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "knight.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "library.json")))
        # Import into a fresh library, then import again: the
        # second run skips, never duplicates.
        fresh = os.path.join(tempfile.mkdtemp(), "lib2")
        os.environ["PIXELFORGE_LIBRARY"] = fresh
        try:
            code, out, _err = self._run(
                ["library", "import", backup])
            self.assertEqual(code, 0)
            self.assertIn("added 1 sprite", out)
            code, out, _err = self._run(
                ["library", "import", backup])
            self.assertEqual(code, 0)
            self.assertIn("added 0 sprites", out)
            self.assertIn("skipped 1 sprite already here", out)
            code, out, _err = self._run(["library", "list"])
            self.assertEqual(code, 0)
            self.assertIn("Knight", out)
        finally:
            os.environ["PIXELFORGE_LIBRARY"] = self.lib_dir

    def test_library_import_bad_folder_fails_cleanly(self):
        code, _out, err = self._run(
            ["library", "import",
             os.path.join(self.tmp, "no-such-folder")])
        self.assertEqual(code, 1)
        self.assertIn("no library in", err)
        self.assertNotIn("Traceback", err)

    def test_presets_list_shows_builtins(self):
        code, out, _err = self._run(["presets", "list"])
        self.assertEqual(code, 0)
        self.assertIn("Game Boy 32 (built-in)", out)
        self.assertIn("32 x 32, gameboy, 4 colors", out)
        self.assertIn("PICO-8 64 (built-in)", out)
        self.assertIn("Detailed 64 (built-in)", out)

    def test_presets_list_shows_saved_preset(self):
        from pixel_forge.presets import PresetStore
        store = PresetStore(self.lib_dir)
        store.save("My Look", {"width": 48, "height": 48,
                               "keep_aspect": True, "palette": "pico8",
                               "max_colors": 8, "dither": False})
        code, out, _err = self._run(["presets", "list"])
        self.assertEqual(code, 0)
        self.assertIn("My Look — 48 x 48, pico8, 8 colors", out)
        self.assertNotIn("My Look (built-in)", out)

    def test_presets_list_warns_about_skipped_preset(self):
        # Task 61: one bad entry must not hide the good presets —
        # they list, and the bad one warns on stderr, name + reason.
        os.makedirs(self.lib_dir, exist_ok=True)
        good = {"width": 48, "height": 48, "keep_aspect": True,
                "palette": "pico8", "max_colors": 8, "dither": False}
        with open(os.path.join(self.lib_dir, "presets.json"),
                  "w") as handle:
            json.dump({"format": "pixel-forge-presets", "version": 1,
                       "presets": {"Good One": good,
                                   "Broken": dict(good, width=0),
                                   "Good Two": dict(good, width=16)}},
                      handle)
        code, out, err = self._run(["presets", "list"])
        self.assertEqual(code, 0)
        self.assertIn("Good One — 48 x 48, pico8, 8 colors", out)
        self.assertIn("Good Two — 16 x 48, pico8, 8 colors", out)
        self.assertNotIn("Broken", out)
        self.assertIn("pixel-forge: skipped preset 'Broken'", err)
        self.assertIn("size", err)
        self.assertNotIn("Traceback", err)

    def test_presets_list_shows_video_fields(self):
        from pixel_forge.presets import PresetStore
        store = PresetStore(self.lib_dir)
        store.save("Video Look",
                   {"width": 32, "height": 32, "keep_aspect": False,
                    "palette": "pico8", "max_colors": 16,
                    "dither": True, "extract_fps": 4,
                    "preview_fps": 12, "sheet_fps": 8,
                    "sheet_cols": 4, "sheet_padding": 2})
        code, out, _err = self._run(["presets", "list"])
        self.assertEqual(code, 0)
        self.assertIn("Video Look — 32 x 32, pico8, 16 colors", out)
        self.assertIn("extract 4 fps", out)
        self.assertIn("preview 12 fps", out)
        self.assertIn("sheet 8 fps", out)
        self.assertIn("sheet columns 4", out)
        self.assertIn("sheet padding 2 px", out)

    def test_presets_export_writes_shareable_file(self):
        out_file = os.path.join(tempfile.mkdtemp(), "shared.json")
        code, out, _err = self._run(
            ["presets", "export", "Tiny 16", "--out", out_file])
        self.assertEqual(code, 0)
        self.assertIn("Exported preset Tiny 16", out)
        with open(out_file) as handle:
            data = json.load(handle)
        self.assertEqual(data["format"], "pixel-forge-preset")
        self.assertEqual(data["name"], "Tiny 16")
        self.assertEqual(data["settings"]["width"], 16)

    def test_presets_export_unknown_name_fails_plainly(self):
        out_file = os.path.join(tempfile.mkdtemp(), "shared.json")
        code, _out, err = self._run(
            ["presets", "export", "No Such Preset",
             "--out", out_file])
        self.assertEqual(code, 1)
        self.assertIn("no preset named 'No Such Preset'", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(out_file))

    def test_presets_export_import_round_trip(self):
        from pixel_forge.presets import PresetStore
        store = PresetStore(self.lib_dir)
        store.save("Video Look",
                   {"width": 32, "height": 32, "keep_aspect": False,
                    "palette": "pico8", "max_colors": 16,
                    "dither": True, "extract_fps": 4,
                    "sheet_cols": None})
        shared = os.path.join(tempfile.mkdtemp(), "shared.json")
        code, _out, _err = self._run(
            ["presets", "export", "Video Look", "--out", shared])
        self.assertEqual(code, 0)
        # Import into a fresh library: the preset arrives whole.
        fresh = os.path.join(tempfile.mkdtemp(), "fresh-lib")
        os.environ["PIXELFORGE_LIBRARY"] = fresh
        code, out, _err = self._run(["presets", "import", shared])
        self.assertEqual(code, 0)
        self.assertIn("Imported preset Video Look", out)
        code, out, _err = self._run(["presets", "list"])
        self.assertIn("Video Look — 32 x 32, pico8, 16 colors", out)
        self.assertIn("extract 4 fps", out)
        self.assertIn("sheet columns auto", out)

    def test_presets_import_bad_file_fails_plainly(self):
        bad = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(bad, "w") as handle:
            handle.write("{not json")
        code, _out, err = self._run(["presets", "import", bad])
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)
        self.assertNotIn("Traceback", err)
        code, out, _err = self._run(["presets", "list"])
        self.assertNotIn("Bad", out)

    def test_presets_import_clash_refuses(self):
        from pixel_forge.presets import PresetStore
        store = PresetStore(self.lib_dir)
        store.save("Mine", {"width": 32, "height": 32,
                            "keep_aspect": False, "palette": "pico8",
                            "max_colors": 16, "dither": True})
        shared = os.path.join(tempfile.mkdtemp(), "shared.json")
        with open(shared, "w") as handle:
            json.dump({"format": "pixel-forge-preset", "version": 1,
                       "name": "Mine",
                       "settings": {"width": 16, "height": 16,
                                    "keep_aspect": False,
                                    "palette": "pico8",
                                    "max_colors": 16,
                                    "dither": True}}, handle)
        code, _out, err = self._run(["presets", "import", shared])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertIn("delete it first", err)
        self.assertNotIn("Traceback", err)
        # The old preset survives the refusal.
        self.assertEqual(store.get("Mine")["width"], 32)


class CodeCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n"
                         "DRAW = [{'x': 0, 'y': 0, 'w': 4, 'h': 4,"
                         " 'color': '#ff0000'}]\n")
        cls.empty = os.path.join(cls.tmp, "empty.py")
        with open(cls.empty, "w") as handle:
            handle.write("# no sprites here\n")

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_code_list(self):
        code, out, err = self._run(["code", self.game, "--list"])
        self.assertEqual(code, 0, err)
        self.assertIn("HERO — map, 2 x 2", out)
        self.assertIn("PALETTE — palette, 2 colors", out)
        self.assertIn("DRAW — found but not rendered", out)
        self.assertIn("draw-call", out)

    def test_code_render_asset(self):
        out_path = os.path.join(self.tmp, "hero.png")
        code, out, err = self._run(["code", self.game,
                                    "--asset", "HERO",
                                    "--out", out_path])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(out_path))
        self.assertIn("HERO: 2 x 2", out)
        image = load_image(out_path)
        self.assertEqual((image.width(), image.height()), (2, 2))

    def test_code_missing_asset_fails_cleanly(self):
        code, _out, err = self._run(["code", self.game,
                                     "--asset", "NOPE"])
        self.assertEqual(code, 1)
        self.assertIn("no asset named 'NOPE'", err)
        self.assertIn("HERO", err)
        self.assertNotIn("Traceback", err)

    def test_code_no_asset_flag_fails_cleanly(self):
        code, _out, err = self._run(["code", self.game])
        self.assertEqual(code, 1)
        self.assertIn("--asset", err)
        self.assertNotIn("Traceback", err)

    def test_code_no_assets_list_reports_empty(self):
        code, out, err = self._run(["code", self.empty, "--list"])
        self.assertEqual(code, 0, err)
        self.assertIn("No pixel-art assets found", out)

    def test_code_no_assets_render_fails_cleanly(self):
        code, _out, err = self._run(["code", self.empty,
                                     "--asset", "HERO"])
        self.assertEqual(code, 1)
        self.assertIn("no pixel-art assets found", err)
        self.assertNotIn("Traceback", err)

    def test_code_missing_file_fails_cleanly(self):
        code, _out, err = self._run(
            ["code", os.path.join(self.tmp, "nope.py"), "--list"])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def _mixed_folder(self, name):
        folder = os.path.join(self.tmp, name)
        os.mkdir(folder)
        with open(os.path.join(folder, "a.py"), "w") as handle:
            handle.write("HERO = [[0, 1], [1, 0]]\n")
        with open(os.path.join(folder, "b.js"), "w") as handle:
            handle.write("const GEM = [[1, 2], [2, 1]];\n")
        with open(os.path.join(folder, "broken.py"), "w") as handle:
            handle.write("def broken(:\n")
        with open(os.path.join(folder, "junk.py"), "wb") as handle:
            handle.write(b"\x00\x01\x02\xff\xfe binary junk")
        return folder

    def test_code_folder_list_skips_bad_file(self):
        # Task 65: a mixed folder lists the good assets, reports
        # the bad files on stderr, and still exits 0.
        folder = self._mixed_folder("mixed")
        code, out, err = self._run(["code", folder, "--list"])
        self.assertEqual(code, 0, err)
        self.assertIn("HERO — map, 2 x 2", out)
        self.assertIn("GEM — map, 2 x 2", out)
        self.assertIn("skipped", err)
        self.assertIn("broken.py", err)
        self.assertIn("junk.py", err)
        self.assertIn("could not parse", err)
        self.assertNotIn("Traceback", err)

    def test_code_folder_all_bad_fails_cleanly(self):
        folder = os.path.join(self.tmp, "allbad")
        os.mkdir(folder)
        with open(os.path.join(folder, "a.py"), "w") as handle:
            handle.write("def a(:\n")
        with open(os.path.join(folder, "b.py"), "w") as handle:
            handle.write("x = \n")
        code, _out, err = self._run(["code", folder, "--list"])
        self.assertEqual(code, 1)
        self.assertIn("could not parse any of 2 files", err)
        self.assertIn("a.py", err)
        self.assertIn("b.py", err)
        self.assertNotIn("Traceback", err)

    def test_code_single_bad_file_fails_cleanly(self):
        path = os.path.join(self.tmp, "solo.py")
        with open(path, "w") as handle:
            handle.write("def broken(:\n")
        code, _out, err = self._run(["code", path, "--list"])
        self.assertEqual(code, 1)
        self.assertIn("could not parse solo.py as Python", err)
        self.assertNotIn("Traceback", err)


class SkinCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        from helpers import make_solid_image
        from pixel_forge.image import argb
        skin = make_solid_image(64, 64, 10, 20, 30)
        # A red head front, so the extracted part is checkable.
        for y in range(8, 16):
            for x in range(8, 16):
                skin.setPixel(x, y, argb(255, 0, 0, 255))
        cls.skin = write_image(skin, os.path.join(cls.tmp,
                                                  "steve.png"))
        cls.texture = write_image(
            make_solid_image(16, 16, 1, 2, 3),
            os.path.join(cls.tmp, "grass.png"))

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_skin_extract(self):
        out_dir = os.path.join(self.tmp, "parts")
        code, out, err = self._run(["skin", self.skin,
                                    "--out", out_dir])
        self.assertEqual(code, 0, err)
        self.assertIn("12 parts + figure", out)
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "head.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "figure.png")))
        head = load_image(os.path.join(out_dir, "head.png"))
        self.assertEqual((head.width(), head.height()), (8, 8))
        self.assertEqual((head.pixel(0, 0) >> 16) & 0xFF, 255)

    def test_skin_default_out_dir(self):
        code, _out, err = self._run(["skin", self.skin])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(
            os.path.join(self.tmp, "steve-skin-parts")))

    def test_skin_not_a_skin_fails_cleanly(self):
        code, _out, err = self._run(["skin", self.texture])
        self.assertEqual(code, 1)
        self.assertIn("not a Minecraft skin", err)
        self.assertIn("16 x 16", err)
        self.assertNotIn("Traceback", err)

    def test_skin_missing_file_fails_cleanly(self):
        code, _out, err = self._run(
            ["skin", os.path.join(self.tmp, "nope.png")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)


class BatchProcessingCliTests(unittest.TestCase):
    """Task 28: folder convert, code --all, library remove/rename,
    presets save/delete, --json / --quiet, and no raw tracebacks."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = write_image(make_gradient_image(),
                              os.path.join(cls.tmp, "photo.png"))
        cls.game = os.path.join(cls.tmp, "game.py")
        with open(cls.game, "w") as handle:
            handle.write('PALETTE = ["#ff0000", "#00ff00"]\n'
                         "HERO = [[0, 1], [1, 0]]\n"
                         "GEM = [[1, 0], [0, 1]]\n")

    def setUp(self):
        self.lib_dir = os.path.join(tempfile.mkdtemp(), "lib")
        self._saved = os.environ.get("PIXELFORGE_LIBRARY")
        os.environ["PIXELFORGE_LIBRARY"] = self.lib_dir
        self.work = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self._cwd)
        if self._saved is None:
            os.environ.pop("PIXELFORGE_LIBRARY", None)
        else:
            os.environ["PIXELFORGE_LIBRARY"] = self._saved

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def _add(self, name):
        code, _out, err = self._run(
            ["library", "add", self.src, "--name", name])
        self.assertEqual(code, 0, err)

    def _convert_folder(self, name="shots"):
        folder = os.path.join(self.work, name)
        os.mkdir(folder)
        write_image(make_gradient_image(),
                    os.path.join(folder, "a.png"))
        write_image(make_gradient_image(),
                    os.path.join(folder, "b.png"))
        # A bad file in the middle: a .png that is not a PNG.
        with open(os.path.join(folder, "broken.png"), "wb") as f:
            f.write(b"not a png at all")
        # A non-image file: ignored, never reported.
        with open(os.path.join(folder, "notes.txt"), "w") as f:
            f.write("not an image\n")
        return folder

    # ------------------------------------------------ folder convert

    def test_convert_folder_bad_file_skipped(self):
        folder = self._convert_folder()
        code, out, err = self._run(["convert", folder])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(folder, "a-pixel.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(folder, "b-pixel.png")))
        self.assertFalse(os.path.isfile(
            os.path.join(folder, "broken-pixel.png")))
        self.assertIn("skipped broken.png", err)
        self.assertIn("a-pixel.png", out)
        self.assertIn("b-pixel.png", out)
        self.assertNotIn("notes", err)

    def test_convert_folder_out_dir(self):
        folder = self._convert_folder()
        out_dir = os.path.join(self.work, "sprites")
        code, _out, err = self._run(
            ["convert", folder, "--out", out_dir])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "a-pixel.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "b-pixel.png")))
        self.assertFalse(os.path.isfile(
            os.path.join(folder, "a-pixel.png")))

    def test_convert_folder_no_images_fails_cleanly(self):
        folder = os.path.join(self.work, "empty")
        os.mkdir(folder)
        with open(os.path.join(folder, "notes.txt"), "w") as f:
            f.write("nothing to see\n")
        code, _out, err = self._run(["convert", folder])
        self.assertEqual(code, 1)
        self.assertIn("no images", err)
        self.assertIn("empty", err)
        self.assertNotIn("Traceback", err)

    def test_convert_folder_all_bad_fails_cleanly(self):
        folder = os.path.join(self.work, "allbad")
        os.mkdir(folder)
        with open(os.path.join(folder, "broken.png"), "wb") as f:
            f.write(b"not a png at all")
        code, _out, err = self._run(["convert", folder])
        self.assertEqual(code, 1)
        self.assertIn("could not convert anything", err)
        self.assertIn("skipped broken.png", err)
        self.assertNotIn("Traceback", err)

    # ----------------------------------------------------- code --all

    def test_code_all_renders_every_asset(self):
        code, out, err = self._run(
            ["code", self.game, "--all"])
        self.assertEqual(code, 0, err)
        out_dir = os.path.join(self.work, "game-sprites")
        for name in ("palette.png", "hero.png", "gem.png"):
            self.assertTrue(os.path.isfile(
                os.path.join(out_dir, name)), name)
        self.assertIn("HERO", out)
        self.assertIn("GEM", out)
        hero = load_image(os.path.join(out_dir, "hero.png"))
        self.assertEqual((hero.width(), hero.height()), (2, 2))

    def test_code_all_out_dir(self):
        out_dir = os.path.join(self.work, "all")
        code, _out, err = self._run(
            ["code", self.game, "--all", "--out", out_dir])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "hero.png")))

    def test_code_all_with_asset_rejected(self):
        code, _out, err = self._run(
            ["code", self.game, "--all", "--asset", "HERO"])
        self.assertEqual(code, 2)
        self.assertIn("not allowed with", err)

    def test_code_all_no_assets_fails_cleanly(self):
        empty = os.path.join(self.work, "empty.py")
        with open(empty, "w") as handle:
            handle.write("# no sprites here\n")
        code, _out, err = self._run(["code", empty, "--all"])
        self.assertEqual(code, 1)
        self.assertIn("no pixel-art assets found", err)
        self.assertNotIn("Traceback", err)

    # --------------------------------------------- library remove/rename

    def test_library_remove_by_name(self):
        self._add("Knight")
        self._add("Slime")
        code, out, err = self._run(["library", "remove", "Knight"])
        self.assertEqual(code, 0, err)
        self.assertIn("Removed Knight", out)
        self.assertFalse(os.path.isfile(
            os.path.join(self.lib_dir, "knight.png")))
        code, out, _err = self._run(["library", "list"])
        self.assertNotIn("Knight", out)
        self.assertIn("Slime", out)

    def test_library_remove_missing_fails_cleanly(self):
        self._add("Knight")
        code, _out, err = self._run(["library", "remove", "Nope"])
        self.assertEqual(code, 1)
        self.assertIn("no sprite named 'Nope'", err)
        self.assertNotIn("Traceback", err)

    def test_library_remove_ambiguous_name_refuses(self):
        self._add("Twin")
        self._add("Twin")
        code, _out, err = self._run(["library", "remove", "Twin"])
        self.assertEqual(code, 1)
        self.assertIn("2 sprites are named 'Twin'", err)
        self.assertIn("use an id instead", err)
        self.assertNotIn("Traceback", err)

    def test_library_remove_by_id(self):
        self._add("Twin")
        self._add("Twin")
        code, out, err = self._run(["library", "list", "--json"])
        self.assertEqual(code, 0, err)
        first_id = json.loads(out)[0]["id"]
        code, out, err = self._run(["library", "remove", first_id])
        self.assertEqual(code, 0, err)
        self.assertIn("Removed Twin", out)
        code, out, _err = self._run(["library", "list", "--json"])
        self.assertEqual(len(json.loads(out)), 1)

    def test_library_rename(self):
        self._add("Knight")
        code, out, err = self._run(
            ["library", "rename", "Knight", "Sir Knight"])
        self.assertEqual(code, 0, err)
        self.assertIn("Renamed Knight to Sir Knight", out)
        code, out, _err = self._run(["library", "list", "--json"])
        self.assertEqual([entry["name"] for entry in json.loads(out)],
                         ["Sir Knight"])

    def test_library_rename_missing_fails_cleanly(self):
        code, _out, err = self._run(
            ["library", "rename", "Nope", "New"])
        self.assertEqual(code, 1)
        self.assertIn("no sprite named 'Nope'", err)
        self.assertNotIn("Traceback", err)

    def test_library_rename_to_existing_fails_cleanly(self):
        self._add("Knight")
        self._add("Slime")
        code, _out, err = self._run(
            ["library", "rename", "Knight", "Slime"])
        self.assertEqual(code, 1)
        self.assertIn("a sprite named 'Slime' already exists", err)
        self.assertNotIn("Traceback", err)

    # ----------------------------------------------- presets save/delete

    def test_presets_save_and_list(self):
        code, out, err = self._run(
            ["presets", "save", "My Look", "--size", "48",
             "--palette", "pico8", "--colors", "8"])
        self.assertEqual(code, 0, err)
        self.assertIn("Saved preset My Look", out)
        code, out, _err = self._run(["presets", "list"])
        self.assertIn("My Look — 48 x 48, pico8, 8 colors", out)

    def test_presets_save_existing_refuses_without_force(self):
        self._run(["presets", "save", "My Look", "--size", "48"])
        code, _out, err = self._run(
            ["presets", "save", "My Look", "--size", "32"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertIn("--force", err)
        self.assertNotIn("Traceback", err)

    def test_presets_save_existing_force_replaces(self):
        self._run(["presets", "save", "My Look", "--size", "48"])
        code, _out, err = self._run(
            ["presets", "save", "My Look", "--size", "32",
             "--force"])
        self.assertEqual(code, 0, err)
        code, out, _err = self._run(["presets", "list"])
        self.assertIn("My Look — 32 x 32", out)

    def test_presets_save_builtin_name_refuses_without_force(self):
        code, _out, err = self._run(
            ["presets", "save", "Game Boy 32", "--size", "48"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_presets_delete(self):
        self._run(["presets", "save", "My Look", "--size", "48"])
        code, out, err = self._run(["presets", "delete", "My Look"])
        self.assertEqual(code, 0, err)
        self.assertIn("Deleted preset My Look", out)
        code, out, _err = self._run(["presets", "list"])
        self.assertNotIn("My Look", out)

    def test_presets_delete_missing_fails_cleanly(self):
        code, _out, err = self._run(["presets", "delete", "Nope"])
        self.assertEqual(code, 1)
        self.assertIn("no preset named 'Nope'", err)
        self.assertNotIn("Traceback", err)

    def test_presets_delete_builtin_fails_cleanly(self):
        code, _out, err = self._run(
            ["presets", "delete", "Game Boy 32"])
        self.assertEqual(code, 1)
        self.assertIn("cannot be deleted", err)
        self.assertNotIn("Traceback", err)

    # ------------------------------------------------- --json / --quiet

    def test_json_library_list_parses(self):
        self._add("Knight")
        code, out, err = self._run(["library", "list", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        entry = data[0]
        self.assertEqual(entry["name"], "Knight")
        self.assertEqual(entry["kind"], "sprite")
        for field in ("id", "file", "width", "height",
                      "source", "created"):
            self.assertIn(field, entry)

    def test_json_library_list_empty_is_empty_array(self):
        code, out, err = self._run(["library", "list", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [])

    def test_json_flag_before_command(self):
        self._add("Knight")
        code, out, err = self._run(["--json", "library", "list"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data[0]["name"], "Knight")

    def test_json_presets_list_parses(self):
        code, out, err = self._run(["presets", "list", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        names = [item["name"] for item in data]
        self.assertIn("Game Boy 32", names)
        builtin = [item for item in data
                   if item["name"] == "Game Boy 32"][0]
        self.assertTrue(builtin["builtin"])
        for field in ("width", "height", "keep_aspect",
                      "palette", "max_colors", "dither"):
            self.assertIn(field, builtin["settings"])

    def test_json_code_list_parses(self):
        code, out, err = self._run(
            ["code", self.game, "--list", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        names = [item["name"] for item in data["assets"]]
        self.assertIn("HERO", names)
        self.assertIn("PALETTE", names)
        hero = [item for item in data["assets"]
                if item["name"] == "HERO"][0]
        self.assertEqual(hero["kind"], "map")
        self.assertEqual(hero["width"], 2)
        self.assertIn("not_rendered", data)

    def test_json_convert_parses(self):
        code, out, err = self._run(
            ["convert", self.src, "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["output"].endswith("-pixel.png"))
        self.assertEqual(data["width"], 32)
        self.assertEqual(data["height"], 32)
        self.assertIn("colors", data)

    def test_quiet_suppresses_info_lines(self):
        code, out, err = self._run(
            ["convert", self.src, "--quiet"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "photo-pixel.png")))

    def test_quiet_keeps_errors(self):
        code, out, err = self._run(
            ["library", "remove", "Nope", "--quiet"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no sprite named 'Nope'", err)

    def test_json_and_quiet_together(self):
        self._add("Knight")
        code, out, err = self._run(
            ["library", "list", "--json", "--quiet"])
        self.assertEqual(code, 0, err)
        # The JSON is the machine output — --quiet does not
        # suppress it.
        self.assertEqual(json.loads(out)[0]["name"], "Knight")

    # -------------------------------------------------- no raw tracebacks

    def test_unexpected_error_prints_plain_line(self):
        import pixel_forge.cli as cli
        real = cli.pixelize_file
        try:
            def boom(*_args, **_kwargs):
                raise ValueError("a test-only failure")
            cli.pixelize_file = boom
            code, _out, err = self._run(["convert", self.src])
        finally:
            cli.pixelize_file = real
        self.assertEqual(code, 1)
        self.assertIn("pixel-forge: a test-only failure", err)
        self.assertNotIn("Traceback", err)

    def test_keyboard_interrupt_exits_quietly(self):
        import pixel_forge.cli as cli
        real = cli.pixelize_file
        try:
            def boom(*_args, **_kwargs):
                raise KeyboardInterrupt()
            cli.pixelize_file = boom
            code, out, err = self._run(["convert", self.src])
        finally:
            cli.pixelize_file = real
        self.assertEqual(code, 130)
        self.assertEqual(out, "")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
