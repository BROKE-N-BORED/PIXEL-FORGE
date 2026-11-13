"""Video tests — ffmpeg extraction, pixelized frames, frames CLI.

Test videos are generated with ffmpeg itself (testsrc: moving
shapes), so the suite tests against real videos, not fixtures.
Skipped when ffmpeg is not installed.

Qt-free (task 64): runs without PySide6.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import make_test_video  # noqa: E402
from pixel_forge import video as video_mod  # noqa: E402
from pixel_forge.cli import main as cli_main  # noqa: E402
from pixel_forge.pixelize import PixelForgeError, load_image  # noqa: E402

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

HAS_FFMPEG = video_mod.find_ffmpeg() is not None


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
            image = load_image(str(path))
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

    def test_sorted_frames_stray_png_last(self):
        # B9: cover.png used to sort as 0 and become frame 0.
        paths = ["frame_001.png", "cover.png", "frame_003.png",
                 "frame_002.png"]
        ordered = [os.path.basename(p) for p in
                   video_mod.sorted_frames(paths)]
        self.assertEqual(ordered, ["frame_001.png", "frame_002.png",
                                   "frame_003.png", "cover.png"])

    def test_sorted_frames_number_anywhere_counts(self):
        # A number anywhere in the name makes it numbered.
        paths = ["frame_010.png", "walk2.png", "frame_002.png",
                 "cover.png"]
        ordered = [os.path.basename(p) for p in
                   video_mod.sorted_frames(paths)]
        self.assertEqual(ordered, ["frame_002.png", "walk2.png",
                                   "frame_010.png", "cover.png"])

    def test_sorted_frames_all_unnumbered_alphabetical(self):
        paths = ["zebra.png", "cover.png", "apple.png"]
        ordered = [os.path.basename(p) for p in
                   video_mod.sorted_frames(paths)]
        self.assertEqual(ordered, ["apple.png", "cover.png",
                                   "zebra.png"])

    def test_sorted_frames_mixed_numbers_numeric(self):
        paths = ["walk1.png", "walk10.png", "walk2.png"]
        ordered = [os.path.basename(p) for p in
                   video_mod.sorted_frames(paths)]
        self.assertEqual(ordered, ["walk1.png", "walk2.png",
                                   "walk10.png"])

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
            image = load_image(str(path))
            self.assertEqual((image.width(), image.height()), (32, 32))

    def test_video_to_frames_end_to_end(self):
        out = os.path.join(self.tmp, "e2e")
        written = video_mod.video_to_frames(
            self.clip, out, fps=8, width=16, palette="gameboy")
        self.assertIn(len(written), (15, 16))
        for path in written:
            image = load_image(str(path))
            self.assertEqual((image.width(), image.height()), (16, 16))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class FramesCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        image = load_image(os.path.join(out_dir, files[0]))
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


class VideoGuardrailTests(unittest.TestCase):
    """Task 25: the frame-count estimate (pure math, no ffmpeg
    needed) and the confirm threshold."""

    def test_gif_is_a_video_extension(self):
        self.assertIn(".gif", video_mod.VIDEO_EXTENSIONS)

    def test_estimate_duration_times_fps(self):
        self.assertEqual(
            video_mod.estimate_frame_count(30, 8), 240)

    def test_estimate_every_nth_divides(self):
        self.assertEqual(
            video_mod.estimate_frame_count(30, 24, every=2), 360)

    def test_estimate_honors_start_and_end(self):
        self.assertEqual(
            video_mod.estimate_frame_count(30, 8, start=5, end=10),
            40)

    def test_estimate_end_clamps_to_duration(self):
        self.assertEqual(
            video_mod.estimate_frame_count(30, 8, start=25, end=99),
            40)

    def test_estimate_zero_span_is_zero(self):
        self.assertEqual(
            video_mod.estimate_frame_count(30, 8, start=40), 0)

    def test_estimate_rounds_up(self):
        # An estimate that under-counts would let a huge run
        # through unchallenged.
        self.assertEqual(
            video_mod.estimate_frame_count(0.1, 8), 1)

    def test_estimate_unknown_is_none(self):
        self.assertIsNone(video_mod.estimate_frame_count(None, 8))
        self.assertIsNone(video_mod.estimate_frame_count(30, None))

    def test_confirm_threshold_is_500(self):
        self.assertEqual(video_mod.FRAME_COUNT_CONFIRM, 500)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class VideoProbeTests(unittest.TestCase):
    """Task 25: ffprobe duration + GIF extraction (real ffmpeg)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.clip = make_test_video(os.path.join(cls.tmp, "clip.mp4"))

    def test_probe_duration_reads_a_real_clip(self):
        duration = video_mod.probe_duration(self.clip)
        self.assertIsNotNone(duration)
        self.assertAlmostEqual(duration, 2.0, delta=0.1)

    def test_probe_duration_missing_file_is_none(self):
        self.assertIsNone(video_mod.probe_duration(
            os.path.join(self.tmp, "nope.mp4")))

    def test_probe_duration_garbage_is_none(self):
        junk = os.path.join(self.tmp, "junk.mp4")
        with open(junk, "wb") as handle:
            handle.write(b"not a video at all")
        self.assertIsNone(video_mod.probe_duration(junk))

    def test_gif_extracts_frames(self):
        gif = os.path.join(self.tmp, "clip.gif")
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-i", self.clip, "-vf", "fps=8,scale=64:48",
                        gif], check=True, timeout=60)
        out = os.path.join(self.tmp, "gif-frames")
        frames = video_mod.extract_frames(gif, out, fps=8)
        self.assertIn(len(frames), (15, 16))


class OnionBlendTests(unittest.TestCase):
    """Task 34: onion skinning is a pure blend — no ffmpeg, no Qt."""

    def _frame(self, values):
        from pixel_forge.image import Image
        image = Image(len(values), 1)
        for index, value in enumerate(values):
            image.setPixel(index, 0, value)
        return image

    def test_opaque_frames_mix_60_40(self):
        # Video frames are opaque, so a plain "over" composite
        # would hide the ghost entirely — the colors mix instead.
        out = video_mod.onion_blend(
            self._frame([0xFFFF0000]),
            self._frame([0xFF0000FF]))
        # R = 255 * 153 // 255 = 153, B = 255 * 102 // 255 = 102.
        self.assertEqual(out.pixel(0, 0), 0xFF990066)

    def test_transparent_current_shows_ghost_at_40_percent(self):
        out = video_mod.onion_blend(
            self._frame([0x00000000]),
            self._frame([0xFF0000FF]))
        # 255 * 102 // 255 = 102 alpha.
        self.assertEqual(out.pixel(0, 0), 0x660000FF)

    def test_mostly_opaque_current_mixes(self):
        out = video_mod.onion_blend(
            self._frame([0xC0FF0000]),
            self._frame([0xFF0000FF]))
        value = out.pixel(0, 0)
        alpha = (value >> 24) & 0xFF
        # 192 + 102 * 63 // 255 = 217.
        self.assertEqual(alpha, 217)
        # Mostly red (the current frame), some blue (the ghost).
        self.assertGreater((value >> 16) & 0xFF,
                           value & 0xFF)
        self.assertGreater(value & 0xFF, 0)

    def test_nearly_transparent_pixel_no_crash(self):
        # Alpha 1 rounds both weights to 0, but the pixel still
        # has a trace of alpha — black at that alpha, never a
        # division by zero.
        out = video_mod.onion_blend(
            self._frame([0x01FF0000]),
            self._frame([0x00000000]))
        self.assertEqual(out.pixel(0, 0), 0x01000000)

    def test_inputs_untouched(self):
        current = self._frame([0x80FF0000])
        previous = self._frame([0xFF0000FF])
        video_mod.onion_blend(current, previous)
        self.assertEqual(current.pixel(0, 0), 0x80FF0000)
        self.assertEqual(previous.pixel(0, 0), 0xFF0000FF)

    def test_mismatched_sizes_fail_plainly(self):
        from pixel_forge.image import Image
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.onion_blend(Image(4, 4), Image(8, 4))
        self.assertIn("same size", str(ctx.exception))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class GifExportTests(unittest.TestCase):
    """Task 34: GIF export through real ffmpeg — frame count,
    per-frame delays, total duration, and transparency."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _frames(self, colors):
        from pixel_forge.image import Image
        frames = []
        for color in colors:
            image = Image(16, 16)
            image.fill(color)
            frames.append(image)
        return frames

    def _frame_times(self, gif):
        """Per-frame (pts_time, duration) from ffprobe."""
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_frames",
             "-of", "csv=p=0", gif],
            capture_output=True, text=True, timeout=60)
        times = []
        for line in proc.stdout.splitlines():
            # CSV fields: media_type, stream_index, key_frame,
            # pkt_pts, pkt_pts_time, pkt_dts, ...
            # pkt_duration_time is field 10.
            fields = line.split(",")
            times.append((float(fields[4]), float(fields[10])))
        return times

    def test_gif_frame_count_and_delays(self):
        frames = self._frames([0xFFFF0000, 0xFF00FF00, 0xFF0000FF])
        gif = os.path.join(self.tmp, "anim.gif")
        video_mod.write_gif(frames, [125, 500, 250], gif)
        self.assertTrue(os.path.isfile(gif))
        times = self._frame_times(gif)
        # The last frame is written twice (the concat demuxer
        # drops the last file's duration otherwise), so a 3-frame
        # animation has 4 frames in the file.
        self.assertEqual(len(times), 4)
        # GIF delays are hundredths of a second, so 125 ms lands
        # as 0.12 and 500 ms as 0.52 (ffmpeg rounds up).
        self.assertAlmostEqual(times[0][1], 0.12, delta=0.02)
        self.assertAlmostEqual(times[1][1], 0.52, delta=0.02)
        # The repeated last frame pair shows for the true
        # duration: 0.21 + 0.04 = 0.25.
        self.assertAlmostEqual(times[2][1] + times[3][1], 0.25,
                               delta=0.02)
        # Total duration matches the sum of the durations.
        total = times[-1][0] + times[-1][1]
        self.assertAlmostEqual(total, 0.875, delta=0.03)

    def test_gif_loops_forever(self):
        frames = self._frames([0xFFFF0000, 0xFF00FF00])
        gif = os.path.join(self.tmp, "loop.gif")
        video_mod.write_gif(frames, [125, 125], gif)
        with open(gif, "rb") as handle:
            data = handle.read()
        # The NETSCAPE2.0 application extension carries the loop
        # count as two little-endian bytes after the 0x03 0x01
        # sub-block header; 0 means loop forever. ffprobe does
        # not expose it, so read the bytes directly.
        marker = data.find(b"NETSCAPE2.0")
        self.assertNotEqual(marker, -1)
        count = data[marker + 13] | (data[marker + 14] << 8)
        self.assertEqual(count, 0)

    def test_gif_preserves_transparency(self):
        # One red pixel in a transparent frame: the hardest case
        # for the GIF encoder — its default picture offsetting
        # crops a thin frame to a 1x1 transparent block and the
        # pixel is lost (write_gif turns offsetting off).
        from pixel_forge.image import Image
        frame = Image(16, 16)
        frame.fill(0x00000000)
        frame.setPixel(0, 0, 0xFFFF0000)
        gif = os.path.join(self.tmp, "clear.gif")
        video_mod.write_gif([frame], [125], gif)
        # Decode frame 0 back to PNG with ffmpeg (load_image
        # reads only PNG without PySide6), then check the pixels.
        png = os.path.join(self.tmp, "clear-frame0.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", gif,
                        "-frames:v", "1", png],
                       check=True, timeout=60)
        image = load_image(png)
        self.assertEqual(image.pixel(0, 0), 0xFFFF0000)
        self.assertEqual((image.pixel(8, 8) >> 24) & 0xFF, 0)

    def test_write_gif_refuses_without_ffmpeg(self):
        frames = self._frames([0xFFFF0000])
        with mock.patch.object(video_mod, "find_ffmpeg",
                               return_value=None):
            with self.assertRaises(PixelForgeError) as ctx:
                video_mod.write_gif(
                    frames, [125], os.path.join(self.tmp, "x.gif"))
        self.assertIn("sudo apt install ffmpeg", str(ctx.exception))

    def test_write_gif_validation(self):
        frames = self._frames([0xFFFF0000, 0xFF00FF00])
        gif = os.path.join(self.tmp, "x.gif")
        with self.assertRaises(PixelForgeError):
            video_mod.write_gif([], [], gif)
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.write_gif(frames, [125], gif)
        self.assertIn("2 frames but 1 durations", str(ctx.exception))
        for bad in ([0, 125], [-5, 125], ["125", 125], [True, 125]):
            with self.assertRaises(PixelForgeError, msg=bad):
                video_mod.write_gif(frames, bad, gif)
        mixed = self._frames([0xFFFF0000])
        from pixel_forge.image import Image
        big = Image(32, 32)
        big.fill(0xFF00FF00)
        mixed.append(big)
        with self.assertRaises(PixelForgeError) as ctx:
            video_mod.write_gif(mixed, [125, 125], gif)
        self.assertIn("frame 2 is 32 x 32", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
