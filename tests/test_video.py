"""Video tests — ffmpeg extraction, pixelized frames, frames CLI.

Test videos are generated with ffmpeg itself (testsrc: moving
shapes), so the suite tests against real videos, not fixtures.
Skipped when ffmpeg is not installed.
"""
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import ensure_app, make_test_video  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from pixel_forge import video as video_mod  # noqa: E402
from pixel_forge.cli import main as cli_main  # noqa: E402
from pixel_forge.pixelize import PixelForgeError  # noqa: E402

HAS_FFMPEG = video_mod.find_ffmpeg() is not None


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()
        cls.tmp = tempfile.mkdtemp()
        # 2 seconds at 24 fps = 48 source frames.
        cls.clip = make_test_video(
            os.path.join(cls.tmp, "clip.mp4"))
        cls.webm = make_test_video(
            os.path.join(cls.tmp, "clip.webm"), fmt="webm")

    def test_find_ffmpeg_returns_a_path(self):
        path = video_mod.find_ffmpeg()
        self.assertTrue(path)
        self.assertTrue(os.path.isfile(path))

    def test_require_ffmpeg_missing_is_loud(self):
        with mock.patch.object(video_mod, "find_ffmpeg",
                               return_value=None):
            with self.assertRaises(PixelForgeError) as ctx:
                video_mod.require_ffmpeg()
        self.assertIn("sudo apt install ffmpeg", str(ctx.exception))

    def test_parse_time(self):
        self.assertEqual(video_mod.parse_time("12.5"), 12.5)
        self.assertEqual(video_mod.parse_time("1:30"), 90.0)
        self.assertEqual(video_mod.parse_time("1:02:30.5"), 3750.5)
        with self.assertRaises(PixelForgeError):
            video_mod.parse_time("banana")
        with self.assertRaises(PixelForgeError):
            video_mod.parse_time("1:2:3:4")

    def test_extract_frames_at_fps(self):
        out = os.path.join(self.tmp, "fps8")
        frames = video_mod.extract_frames(self.clip, out, fps=8)
        # 2 seconds at 8 fps: 16 frames (allow one of rounding).
        self.assertIn(len(frames), (15, 16))
        for path in frames:
            self.assertTrue(os.path.isfile(path))
            image = QImage(str(path))
            self.assertEqual((image.width(), image.height()),
                             (160, 120))

    def test_extract_frames_webm(self):
        out = os.path.join(self.tmp, "webm")
        frames = video_mod.extract_frames(self.webm, out, fps=8)
        self.assertIn(len(frames), (15, 16))

    def test_extract_every_nth(self):
        out = os.path.join(self.tmp, "every2")
        frames = video_mod.extract_frames(self.clip, out, fps=24,
                                          every=2)
        # 48 source frames, every 2nd one.
        self.assertIn(len(frames), (23, 24))

    def test_extract_time_range(self):
        out = os.path.join(self.tmp, "range")
        frames = video_mod.extract_frames(self.clip, out, fps=8,
                                          start="0.5", end="1.5")
        # One second at 8 fps.
        self.assertIn(len(frames), (7, 8))

    def test_extract_end_before_start_rejected(self):
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.extract_frames(self.clip,
                                     os.path.join(self.tmp, "bad"),
                                     start="1.5", end="0.5")
        self.assertIn("end time must be after start time",
                      str(ctx.exception))

    def test_extract_missing_file(self):
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.extract_frames(
                os.path.join(self.tmp, "missing.mp4"),
                os.path.join(self.tmp, "nope"))
        self.assertIn("not found", str(ctx.exception))

    def test_extract_unreadable_file(self):
        garbage = os.path.join(self.tmp, "garbage.mp4")
        with open(garbage, "w") as f:
            f.write("this is not a video")
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.extract_frames(
                garbage, os.path.join(self.tmp, "garbage-out"))
        self.assertIn("could not read", str(ctx.exception))

    def _failing_extract(self, stderr, out_name):
        """Run extract_frames against a stubbed failing ffmpeg."""
        garbage = os.path.join(self.tmp, "garbage.mp4")
        if not os.path.exists(garbage):
            with open(garbage, "w") as f:
                f.write("this is not a video")
        proc = mock.Mock(returncode=1, stderr=stderr, stdout="")
        with mock.patch.object(video_mod.subprocess, "run",
                               return_value=proc):
            with self.assertRaises(PixelForgeError) as ctx:
                video_mod.extract_frames(
                    garbage, os.path.join(self.tmp, out_name))
        return str(ctx.exception)

    def test_extract_failure_shows_last_three_lines(self):
        # Review B3: the old code sliced the last 3 characters
        # of the joined string, not the last 3 lines.
        message = self._failing_extract(
            "line one\nline two\nline three\nline four\nline five\n",
            "fail-three")
        self.assertEqual(
            message,
            "ffmpeg could not read garbage.mp4\n"
            "line three\nline four\nline five")

    def test_extract_failure_short_output_shows_in_full(self):
        message = self._failing_extract("only one line\n", "fail-one")
        self.assertEqual(
            message, "ffmpeg could not read garbage.mp4\nonly one line")
        message = self._failing_extract("first line\nsecond line\n",
                                        "fail-two")
        self.assertEqual(
            message,
            "ffmpeg could not read garbage.mp4\nfirst line\nsecond line")

    def test_extract_failure_empty_output_has_no_fragment(self):
        message = self._failing_extract("", "fail-empty")
        self.assertEqual(message, "ffmpeg could not read garbage.mp4")
        message = self._failing_extract("\n\n", "fail-blank")
        self.assertEqual(message, "ffmpeg could not read garbage.mp4")

    def test_extract_failure_message_is_not_garbled(self):
        # Real ffmpeg on a garbage file: no "p; u; t" style
        # semicolon-separated single characters, and every
        # detail line is real text, not a fragment.
        garbage = os.path.join(self.tmp, "garbage.mp4")
        with open(garbage, "w") as f:
            f.write("this is not a video")
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.extract_frames(
                garbage, os.path.join(self.tmp, "garbage-out2"))
        message = str(ctx.exception)
        self.assertNotRegex(
            message, r"[a-zA-Z]; [a-zA-Z]; [a-zA-Z]")
        lines = message.splitlines()
        self.assertGreaterEqual(len(lines), 2)
        for line in lines[1:]:
            self.assertGreater(len(line), 1, line)

    def test_extract_missing_ffmpeg_is_loud(self):
        with mock.patch.object(video_mod, "find_ffmpeg",
                               return_value=None):
            with self.assertRaises(PixelForgeError) as ctx:
                video_mod.extract_frames(
                    self.clip, os.path.join(self.tmp, "no-ffmpeg"))
        self.assertIn("sudo apt install ffmpeg", str(ctx.exception))

    def test_sorted_frames_past_999(self):
        paths = ["frame_%03d.png" % n for n in (1, 999, 1000, 2)]
        ordered = [os.path.basename(p) for p in
                   video_mod.sorted_frames(paths)]
        self.assertEqual(ordered, ["frame_001.png", "frame_002.png",
                                   "frame_999.png", "frame_1000.png"])

    def test_pixelize_frames_sizes_and_names(self):
        raw = os.path.join(self.tmp, "raw")
        frames = video_mod.extract_frames(self.clip, raw, fps=4)
        out = os.path.join(self.tmp, "pix")
        written = video_mod.pixelize_frame_files(
            frames, out, width=32, height=32, palette="pico8",
            max_colors=16)
        self.assertEqual(len(written), len(frames))
        self.assertEqual(os.path.basename(str(written[0])),
                         "frame_001.png")
        self.assertEqual(
            os.path.basename(str(written[-1])),
            "frame_%03d.png" % len(written))
        for path in written:
            image = QImage(str(path))
            self.assertEqual((image.width(), image.height()), (32, 32))

    def test_video_to_frames_end_to_end(self):
        out = os.path.join(self.tmp, "e2e")
        written = video_mod.video_to_frames(
            self.clip, out, fps=8, width=16, palette="gameboy")
        self.assertIn(len(written), (15, 16))
        for path in written:
            image = QImage(str(path))
            self.assertEqual((image.width(), image.height()), (16, 16))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class FramesCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()
        cls.tmp = tempfile.mkdtemp()
        cls.clip = make_test_video(
            os.path.join(cls.tmp, "clip.mp4"))

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = cli_main(argv)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_frames_help(self):
        code, out, _err = self._run(["frames", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--fps", out)
        self.assertIn("--every", out)
        self.assertIn("--start", out)
        self.assertIn("--size", out)
        self.assertIn("--palette", out)

    def test_frames_happy_path(self):
        out_dir = os.path.join(self.tmp, "frames-out")
        code, out, err = self._run(
            ["frames", self.clip, "--fps", "8", "--size", "32",
             "--palette", "pico8", "--out", out_dir])
        self.assertEqual(code, 0, err)
        files = sorted(os.listdir(out_dir))
        self.assertIn(len(files), (15, 16))
        self.assertEqual(files[0], "frame_001.png")
        self.assertIn("frames", out)
        self.assertIn("32 x 32", out)
        image = QImage(os.path.join(out_dir, files[0]))
        self.assertEqual((image.width(), image.height()), (32, 32))

    def test_frames_default_output_dir(self):
        code, _out, err = self._run(
            ["frames", self.clip, "--fps", "4", "--size", "16"])
        self.assertEqual(code, 0, err)
        expected = os.path.join(self.tmp, "clip-frames")
        self.assertTrue(os.path.isdir(expected))
        self.assertTrue(os.path.isfile(
            os.path.join(expected, "frame_001.png")))

    def test_frames_every_and_range(self):
        out_dir = os.path.join(self.tmp, "frames-range")
        code, _out, err = self._run(
            ["frames", self.clip, "--fps", "8", "--every", "2",
             "--start", "0.5", "--end", "1.5", "--size", "16",
             "--out", out_dir])
        self.assertEqual(code, 0, err)
        files = sorted(os.listdir(out_dir))
        self.assertIn(len(files), (3, 4))

    def test_frames_missing_input_fails_cleanly(self):
        code, _out, err = self._run(
            ["frames", os.path.join(self.tmp, "missing.mp4")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def test_frames_missing_ffmpeg_fails_cleanly(self):
        with mock.patch.object(video_mod, "find_ffmpeg",
                               return_value=None):
            code, _out, err = self._run(["frames", self.clip])
        self.assertEqual(code, 1)
        self.assertIn("sudo apt install ffmpeg", err)
        self.assertNotIn("Traceback", err)

    def test_frames_bad_fps_fails_cleanly(self):
        code, _out, err = self._run(
            ["frames", self.clip, "--fps", "0"])
        self.assertEqual(code, 1)
        self.assertIn("fps must be greater than 0", err)
        self.assertNotIn("Traceback", err)

    def test_frames_bad_every_fails_cleanly(self):
        code, _out, err = self._run(
            ["frames", self.clip, "--every", "0"])
        self.assertEqual(code, 1)
        self.assertIn("--every must be at least 1", err)
        self.assertNotIn("Traceback", err)

    def test_frames_bad_time_fails_cleanly(self):
        code, _out, err = self._run(
            ["frames", self.clip, "--start", "banana"])
        self.assertEqual(code, 1)
        self.assertIn("bad time", err)
        self.assertNotIn("Traceback", err)

    def test_frames_bad_size_fails_cleanly(self):
        code, _out, err = self._run(
            ["frames", self.clip, "--size", "banana"])
        self.assertEqual(code, 1)
        self.assertIn("bad --size", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
