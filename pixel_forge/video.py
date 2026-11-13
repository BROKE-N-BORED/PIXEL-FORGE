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

import math
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
# Above this many estimated frames, the Video tab asks before
# extracting (task 25): a long clip at 24 fps would otherwise
# write tens of thousands of PNGs with no warning.
FRAME_COUNT_CONFIRM = 500
# The GIF muxer gives the LAST frame a fixed 40 ms delay (1/25 s)
# — it computes every delay from the gap to the NEXT frame, and
# the last frame has no next frame. GIF export (task 34) works
# around it by repeating the last frame and shaving this much off
# its own duration, so the repeated pair shows for exactly the
# last frame's duration (see write_gif).
GIF_LAST_FRAME_DELAY_MS = 40
# Onion skinning (task 34): the previous frame ghosts behind the
# current one at this alpha (102 of 255, about 40%).
ONION_GHOST_ALPHA = 102
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v",
                    ".ogv", ".flv", ".wmv", ".mpg", ".mpeg", ".3gp",
                    ".gif"}

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


def find_ffprobe():
    """Path to the ffprobe binary, or None when it is not
    installed. ffprobe ships with ffmpeg, so it is usually on
    PATH next to it; when ffmpeg came from PIXEL_FORGE_FFMPEG,
    look for ffprobe beside it too."""
    path = shutil.which("ffprobe")
    if path:
        return path
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    return None


def probe_duration(video_path):
    """Length of a video in seconds, or None when it cannot be
    probed (no ffprobe, an unreadable file, no duration in the
    metadata). Never raises — a missing estimate is not a
    failure, it just means no frame-count estimate (task 25)."""
    ffprobe = find_ffprobe()
    if ffprobe is None:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        duration = float((proc.stdout or "").strip())
    except ValueError:
        return None
    if duration <= 0:
        return None
    return duration


def estimate_frame_count(duration, fps, every=1, start=None, end=None):
    """Roughly how many frames extraction would produce: the
    seconds in range times fps, divided by every-Nth, rounded
    UP — an estimate that under-counts would let a huge run
    through unchallenged. None when the count cannot be
    estimated (no duration, or fps None = the source rate,
    which this function does not know). start/end are seconds
    as floats — callers parse the user's text first."""
    if duration is None or fps is None or fps <= 0 or every < 1:
        return None
    span = duration - (start or 0.0)
    if end is not None:
        span = min(span, end - (start or 0.0))
    if span <= 0:
        return 0
    return math.ceil(span * fps / every)


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


def _frame_sort_key(path):
    """Playback order for frame files.

    A name counts as numbered when a number appears anywhere in
    it (frame_002.png, walk2.png, cover2.png) — numbered names
    sort first, by that number (frame_2 before frame_10; plain
    sorted() breaks past frame 999, putting "frame_1000" before
    "frame_999"). Names with no number sort AFTER all numbered
    ones, alphabetically among themselves — a stray cover.png
    used to sort as 0 and become frame 0 (review B9). A folder
    with no numbered files at all keeps plain alphabetical
    order. Ties on the number break alphabetically.
    """
    name = Path(path).name.lower()
    match = re.search(r"\d+", Path(path).stem)
    if match is not None:
        return (0, int(match.group()), name)
    return (1, 0, name)


def sorted_frames(paths):
    """Frame paths in playback order (see _frame_sort_key)."""
    return sorted(paths, key=_frame_sort_key)


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

def onion_blend(current, previous, ghost_alpha=ONION_GHOST_ALPHA):
    """The current frame with the previous one ghosted through it
    (task 34): onion skinning for the animation preview. The
    previous frame shows at about 40% strength (ghost_alpha) —
    as a 40%-alpha ghost where the current frame is transparent,
    and mixed 60/40 into the colors where the current frame is
    opaque. A plain Porter-Duff "over" would be useless here:
    video frames are opaque, so the current frame would win
    outright and the ghost would never show. The output keeps
    the current frame's coverage (opaque stays opaque); only the
    colors carry the ghost. Returns a new Image; neither input
    is touched. Both frames must be the same size — a mismatched
    pair is a plain error, never a crash.
    """
    from .image import Image

    if (current.width() != previous.width()
            or current.height() != previous.height()):
        raise PixelForgeError(
            "onion skin needs two frames of the same size (%d x %d "
            "and %d x %d)" % (current.width(), current.height(),
                             previous.width(), previous.height()))
    out = Image(current.width(), current.height())
    src = current.data
    ghost = previous.data
    dst = out.data
    for offset in range(0, len(src), 4):
        ca = src[offset + 3]
        # The ghost's strength, and the current frame's own
        # weight after the ghost's share is taken out.
        ga = ghost[offset + 3] * ghost_alpha // 255
        weight_cur = ca * (255 - ghost_alpha) // 255
        weight_ghost = ga
        total = weight_cur + weight_ghost
        # Coverage is still Porter-Duff "over": the current
        # frame's alpha is preserved, the ghost only adds
        # coverage where the current frame has none.
        alpha = ca + ga * (255 - ca) // 255
        if alpha == 0:
            dst[offset:offset + 4] = b"\x00\x00\x00\x00"
            continue
        if total == 0:
            # A nearly-transparent current pixel over a
            # transparent ghost: both weights round to 0, but the
            # pixel still has a trace of alpha — black at that
            # alpha, never a division by zero.
            dst[offset:offset + 3] = b"\x00\x00\x00"
            dst[offset + 3] = alpha
            continue
        dst[offset] = (src[offset] * weight_cur
                       + ghost[offset] * weight_ghost) // total
        dst[offset + 1] = (src[offset + 1] * weight_cur
                           + ghost[offset + 1] * weight_ghost) // total
        dst[offset + 2] = (src[offset + 2] * weight_cur
                           + ghost[offset + 2] * weight_ghost) // total
        dst[offset + 3] = alpha
    return out


def write_gif(images, durations, out_path, loop=0):
    """Write an animated GIF from pixelized frames (task 34).

    images are buffer Images, all the same size; durations are
    milliseconds per frame, one per image, each at least 1. The
    GIF loops forever when loop is 0 (the default), or the given
    number of times. Returns the output path as a string.

    Built on ffmpeg's concat demuxer, which takes per-file
    durations — the only ffmpeg route that honours varied timing
    (a plain image input is uniform fps). Two quirks are handled
    here, so callers never see them: the concat demuxer drops the
    LAST file's duration unless the file is repeated, and the GIF
    muxer gives a frame with no successor a fixed 40 ms delay — so
    the last frame is written twice, with GIF_LAST_FRAME_DELAY_MS
    shaved off its own duration, and the repeated pair shows for
    exactly that duration. (A last frame shorter than 40 ms keeps
    its full duration and shows up to 40 ms longer — the shave
    never goes below 10 ms.) The palette is built from the frames
    themselves (palettegen, one slot reserved for transparency),
    so the GIF keeps the frames' own colors instead of ffmpeg's
    fixed palette. The encoder's picture offsetting is turned
    OFF (-gifflags -offsetting): by default it crops each frame
    to its changed bounding box, and that crop misfires on thin
    content — a single-pixel highlight or a one-pixel-tall line
    is cropped away entirely (verified 2026-11-13: a frame with
    8 red pixels in a row encoded as a 1x1 transparent block).
    Every frame is written whole instead.

    Raises PixelForgeError with a readable message on any failure
    — missing ffmpeg (with the exact fix), no frames, mismatched
    durations, mixed frame sizes, or ffmpeg failing (its error
    tail, last 3 lines, like extract_frames).
    """
    ffmpeg = require_ffmpeg()
    if not images:
        raise PixelForgeError("no frames to export as a GIF")
    if len(images) != len(durations):
        raise PixelForgeError(
            "internal error: %d frames but %d durations"
            % (len(images), len(durations)))
    for duration in durations:
        if (not isinstance(duration, int)
                or isinstance(duration, bool) or duration < 1):
            raise PixelForgeError(
                "frame durations must be whole numbers of at least "
                "1 ms (got %r)" % (duration,))
    first_w, first_h = images[0].width(), images[0].height()
    for index, image in enumerate(images[1:], start=1):
        if image.width() != first_w or image.height() != first_h:
            raise PixelForgeError(
                "frame %d is %d x %d but frame 1 is %d x %d — a GIF "
                "needs every frame the same size"
                % (index + 1, image.width(), image.height(),
                   first_w, first_h))
    out = Path(out_path)
    tmp = tempfile.mkdtemp(prefix="pixel-forge-gif-")
    try:
        paths = []
        for index, image in enumerate(images):
            path = Path(tmp) / ("frame_%03d.png" % (index + 1))
            if not image.save(str(path), "PNG"):
                raise PixelForgeError(
                    "could not write: %s" % path)
            paths.append(path)
        # The concat list: one duration line after each file. The
        # last file is repeated so its duration is honoured (see
        # the docstring), with the muxer's fixed last-frame delay
        # shaved off so the pair lands on the true duration.
        lines = []
        for index, path in enumerate(paths):
            lines.append("file '%s'" % path)
            duration = durations[index]
            if index == len(paths) - 1:
                duration = max(10, duration
                               - GIF_LAST_FRAME_DELAY_MS)
            lines.append("duration %.3f" % (duration / 1000))
        lines.append("file '%s'" % paths[-1])
        list_path = Path(tmp) / "list.txt"
        list_path.write_text("\n".join(lines) + "\n")
        cmd = [ffmpeg, "-y", "-v", "error", "-f", "concat",
               "-safe", "0", "-i", str(list_path),
               "-vf", "split[s0][s1];[s0]palettegen=max_colors=255:"
               "reserve_transparent=1[p];[s1][p]paletteuse",
               "-gifflags", "-offsetting",
               "-loop", str(loop), str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=1800)
        except subprocess.TimeoutExpired:
            raise PixelForgeError(
                "ffmpeg took too long writing %s — try fewer frames"
                % out.name)
        except OSError as exc:
            raise PixelForgeError("could not run ffmpeg: %s" % exc)
        if proc.returncode != 0:
            # Slice the LIST of lines, not the joined string
            # (review B3 — see extract_frames).
            lines = [line for line in (proc.stderr or "").splitlines()
                     if line.strip()]
            tail = lines[-3:]
            detail = ("\n" + "\n".join(tail)) if tail else ""
            raise PixelForgeError(
                "ffmpeg could not write %s" % (out.name, detail))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(out)
