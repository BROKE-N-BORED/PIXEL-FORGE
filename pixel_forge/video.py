"""Video to sprite frames — extract with ffmpeg, pixelize with the
Item 1 core.

ffmpeg is an external tool, not a Python dependency: frame extraction
shells out to the ffmpeg CLI. If it is missing, every entry point
refuses LOUDLY with the exact fix (sudo apt install ffmpeg) instead
of faking anything. Pixel Forge still works for images without it.

Pipeline: ffmpeg extracts raw frames to a folder, then each frame
goes through pixelize_image with the same size/palette/dither
controls as a still image. The pixelizer is reused, never duplicated.
Raw frames live on disk (a temp dir for the GUI, a caller-chosen dir
for the low-level calls) so RAM stays light even for long videos.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .pixelize import PixelForgeError, load_image, pixelize_image

FFMPEG_ENV = "PIXEL_FORGE_FFMPEG"
FFMPEG_FIX = "sudo apt install ffmpeg"
FPS_PRESETS = [2, 4, 8, 12, 24]
DEFAULT_FPS = 8
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v",
                    ".ogv", ".flv", ".wmv", ".mpg", ".mpeg", ".3gp"}

TIME_RE = re.compile(
    r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


def find_ffmpeg():
    """Path to the ffmpeg binary, or None when it is not installed.

    PIXEL_FORGE_FFMPEG overrides the search when it points at an
    executable file (for machines where ffmpeg lives outside PATH).
    """
    override = os.environ.get(FFMPEG_ENV)
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        return None
    return shutil.which("ffmpeg")


def require_ffmpeg():
    """Path to ffmpeg, or a loud refusal with the exact fix."""
    path = find_ffmpeg()
    if path is None:
        raise PixelForgeError(
            "ffmpeg is not installed — video import needs it.\n"
            "Fix: %s\n"
            "Then try the video again. Image import works without it."
            % FFMPEG_FIX)
    return path


def parse_time(text):
    """Validate a start/end time ("12.5", "1:30", "1:02:30.5").

    Returns the value in seconds. Raises PixelForgeError on anything
    ffmpeg would not understand, so the user gets a readable message
    instead of ffmpeg's stderr.
    """
    match = TIME_RE.match(str(text).strip())
    if not match:
        raise PixelForgeError(
            "bad time %r (use seconds like 12.5, or MM:SS like 1:30)"
            % text)
    first, second, seconds = match.groups()
    total = float(seconds)
    if second is not None:
        # HH:MM:SS — both optional groups matched.
        total += int(first) * 3600 + int(second) * 60
    elif first is not None:
        # MM:SS — only the first optional group matched.
        total += int(first) * 60
    return total


def _frame_number(path):
    """Numeric order for frame_NNN.png names (plain sorted() breaks
    past frame 999: "frame_1000" sorts before "frame_999")."""
    stem = Path(path).stem
    suffix = stem.rsplit("_", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return 0


def sorted_frames(paths):
    """Frame paths in playback order."""
    return sorted(paths, key=_frame_number)


def build_filter(fps, every):
    """The -vf filtergraph for the frame selection, or None to take
    every frame at the source rate."""
    parts = []
    if fps is not None:
        parts.append("fps=fps=%s" % fps)
    if every > 1:
        parts.append("select='not(mod(n\\,%d))'" % every)
    if not parts:
        return None
    return ",".join(parts)


def extract_frames(video_path, out_dir, fps=DEFAULT_FPS, every=1,
                   start=None, end=None):
    """Extract frames from a video with ffmpeg.

    Returns the extracted frame paths in playback order. fps picks
    that many frames per second (None = the source rate); every keeps
    every Nth frame of those; start/end trim the range (seconds or
    MM:SS). Raises PixelForgeError with a readable message on any
    failure — missing ffmpeg, missing file, unreadable video, or an
    empty result.
    """
    ffmpeg = require_ffmpeg()
    video = Path(video_path)
    if not video.is_file():
        raise PixelForgeError("not found: %s" % video_path)
    if fps is not None and fps <= 0:
        raise PixelForgeError("fps must be greater than 0")
    if every < 1:
        raise PixelForgeError("--every must be at least 1")
    start_s = parse_time(start) if start is not None else None
    end_s = parse_time(end) if end is not None else None
    if (start_s is not None and end_s is not None
            and end_s <= start_s):
        raise PixelForgeError("end time must be after start time")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pattern = str(out / "frame_%03d.png")

    cmd = [ffmpeg, "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start_s)]
    if end is not None:
        cmd += ["-to", str(end_s)]
    cmd += ["-i", str(video)]
    video_filter = build_filter(fps, every)
    if video_filter is not None:
        cmd += ["-vf", video_filter]
    cmd += ["-vsync", "0", pattern]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=1800)
    except subprocess.TimeoutExpired:
        raise PixelForgeError(
            "ffmpeg took too long on %s — try a shorter range or a "
            "lower fps" % video.name)
    except OSError as exc:
        raise PixelForgeError("could not run ffmpeg: %s" % exc)
    if proc.returncode != 0:
        # Slice the LIST of lines, not the joined string: taking
        # [-3:] of the joined string gave the last 3 characters,
        # which the old "; ".join then garbled into "p; u; t"
        # (review B3). The last 3 non-blank lines, each on its
        # own line; empty output leaves no detail at all.
        lines = [line for line in (proc.stderr or "").splitlines()
                 if line.strip()]
        tail = lines[-3:]
        detail = ("\n" + "\n".join(tail)) if tail else ""
        raise PixelForgeError(
            "ffmpeg could not read %s%s" % (video.name, detail))
    frames = sorted_frames(out.glob("frame_*.png"))
    if not frames:
        raise PixelForgeError(
            "no frames extracted from %s (empty video, or the time "
            "range has no frames)" % video.name)
    return frames


def pixelize_frame_files(frame_paths, out_dir, width=32, height=None,
                         keep_aspect=False, palette="image",
                         max_colors=16, dither=True):
    """Pixelize extracted frames into numbered PNGs.

    Writes frame_001.png, frame_002.png, ... into out_dir (created if
    needed) and returns the output paths in order. Every frame goes
    through the Item 1 core — same size, palette, and dither.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for index, frame_path in enumerate(frame_paths, start=1):
        image = load_image(frame_path)
        result = pixelize_image(image, width=width, height=height,
                                keep_aspect=keep_aspect,
                                palette=palette,
                                max_colors=max_colors, dither=dither)
        dest = out / ("frame_%03d.png" % index)
        if not result.save(str(dest), "PNG"):
            raise PixelForgeError("could not write: %s" % dest)
        written.append(dest)
    return written


def video_to_frames(video_path, out_dir, fps=DEFAULT_FPS, every=1,
                    start=None, end=None, width=32, height=None,
                    keep_aspect=False, palette="image", max_colors=16,
                    dither=True):
    """Full pipeline: video in, numbered pixelized PNGs out.

    Raw frames go to a temp dir that is deleted afterwards, so only
    the finished sprites remain. Returns the output paths in order.
    """
    tmp = tempfile.mkdtemp(prefix="pixel-forge-frames-")
    try:
        raw = extract_frames(video_path, tmp, fps=fps, every=every,
                             start=start, end=end)
        return pixelize_frame_files(
            raw, out_dir, width=width, height=height,
            keep_aspect=keep_aspect, palette=palette,
            max_colors=max_colors, dither=dither)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
