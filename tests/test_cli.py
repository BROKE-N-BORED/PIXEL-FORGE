"""CLI tests — in-process plus one real subprocess smoke test."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import (ensure_app, make_gradient_image, stub_env,  # noqa: E402
                     write_image, write_stub_engine)
from PySide6.QtGui import QImage  # noqa: E402
from pixel_forge import __version__  # noqa: E402
from pixel_forge.cli import main  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()
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
        image = QImage(out_path)
        self.assertEqual((image.width(), image.height()), (32, 32))

    def test_convert_default_output_name(self):
        code, out, err = self._run(["convert", self.src, "--size", "16"])
        self.assertEqual(code, 0, err)
        expected = os.path.join(self.tmp, "photo-pixel.png")
        self.assertTrue(os.path.isfile(expected))
        image = QImage(expected)
        self.assertEqual((image.width(), image.height()), (16, 16))

    def test_convert_width_by_height_size(self):
        out_path = os.path.join(self.tmp, "wide.png")
        code, _out, err = self._run(["convert", self.src,
                                     "--size", "64x48",
                                     "--out", out_path])
        self.assertEqual(code, 0, err)
        image = QImage(out_path)
        self.assertEqual((image.width(), image.height()), (64, 48))

    def test_convert_keep_aspect(self):
        out_path = os.path.join(self.tmp, "aspect.png")
        code, _out, err = self._run(["convert", self.src, "--size", "32",
                                     "--keep-aspect", "--out", out_path])
        self.assertEqual(code, 0, err)
        image = QImage(out_path)
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

    def test_prompt_happy_path(self):
        out_dir = os.path.join(self.tmp, "knight-out")
        with stub_env(self.stub, self.model):
            code, out, err = self._run(
                ["prompt", "a knight", "--out", out_dir,
                 "--size", "32", "--colors", "16"])
        self.assertEqual(code, 0, err)
        self.assertIn("3 candidate(s)", out)
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
            image = QImage(pixel)
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
        ensure_app()
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
        image = QImage(out_png)
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
        image = QImage(expected_png)
        # Auto grid for 6 frames: 3 columns, 2 rows, no padding.
        self.assertEqual((image.width(), image.height()), (96, 64))

    def test_sheet_meta_defaults_to_out_stem(self):
        out_png = os.path.join(self.tmp, "custom-name.png")
        code, _out, err = self._run(["sheet", self.frames,
                                     "--out", out_png])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "custom-name.json")))

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
        ensure_app()
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

    def test_library_add_blank_name_fails_cleanly(self):
        code, _out, err = self._run(
            ["library", "add", self.src, "--name", "   "])
        self.assertEqual(code, 1)
        self.assertIn("give the sprite a name", err)
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


class CodeCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()
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
        image = QImage(out_path)
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


class SkinCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()
        cls.tmp = tempfile.mkdtemp()
        from helpers import make_solid_image
        from PySide6.QtGui import qRgba
        skin = make_solid_image(64, 64, 10, 20, 30)
        # A red head front, so the extracted part is checkable.
        for y in range(8, 16):
            for x in range(8, 16):
                skin.setPixel(x, y, qRgba(255, 0, 0, 255))
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
        self.assertIn("12 part(s) + figure", out)
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "head.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(out_dir, "figure.png")))
        head = QImage(os.path.join(out_dir, "head.png"))
        self.assertEqual((head.width(), head.height()), (8, 8))
        self.assertEqual(head.pixelColor(0, 0).red(), 255)

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


if __name__ == "__main__":
    unittest.main()
