"""Spritesheets + animation export — pack pixel frames into one PNG
plus a metadata JSON a game can play without guessing.

Layout: frames go left to right, top to bottom, in a grid. Columns
are picked by the caller or auto (square-ish grid). Padding is the
gap BETWEEN frames only — the sheet has no outer border, so frame 0
always starts at (0, 0). The sheet background is transparent, and so
are the padding gaps and any unused cells in a partial last row.

The metadata format is Pixel Forge's own, documented in the README:
sheet size, frame size, fps, and a frames array in playback order
with each frame's x, y, w, h, duration_ms, and source name. A game
reads the JSON, cuts the frames out of the PNG, and plays them at
the given durations — nothing to guess.

Frames must all be the same size (they come out of the Item 1
pixelizer that way); a folder that mixes sizes fails cleanly with a
readable message. No AI, no network — pure packing.
"""

import json
import math
from pathlib import Path

from PySide6.QtGui import QImage, QPainter

from .pixelize import PixelForgeError, load_image
from .video import sorted_frames

FORMAT_NAME = "pixel-forge-spritesheet"
FORMAT_VERSION = 1


def auto_columns(count):
    """A square-ish grid: as many columns as the square root,
    rounded up (1 frame -> 1, 4 -> 2, 5 -> 3, 16 -> 4)."""
    if count < 1:
        raise PixelForgeError("need at least one frame to pack")
    return max(1, math.ceil(math.sqrt(count)))


def layout_frames(count, frame_w, frame_h, cols=None, padding=0):
    """Grid geometry for count frames of frame_w x frame_h.

    Returns a dict with columns, rows, sheet_width, sheet_height,
    and positions — the (x, y) of each frame in order. cols=None
    picks auto_columns; padding is the gap between frames only.
    Raises PixelForgeError on nonsense (no frames, cols < 1,
    negative padding, frame smaller than 1 px).
    """
    if count < 1:
        raise PixelForgeError("need at least one frame to pack")
    if frame_w < 1 or frame_h < 1:
        raise PixelForgeError(
            "frame size must be at least 1 pixel in each direction")
    if cols is None:
        cols = auto_columns(count)
    if cols < 1:
        raise PixelForgeError("columns must be at least 1")
    if padding < 0:
        raise PixelForgeError("padding cannot be negative")
    rows = math.ceil(count / cols)
    sheet_w = cols * frame_w + (cols - 1) * padding
    sheet_h = rows * frame_h + (rows - 1) * padding
    positions = []
    for index in range(count):
        col, row = index % cols, index // cols
        positions.append((col * (frame_w + padding),
                          row * (frame_h + padding)))
    return {"columns": cols, "rows": rows,
            "sheet_width": sheet_w, "sheet_height": sheet_h,
            "padding": padding, "positions": positions}


def check_uniform(frames):
    """All frames the same size, or a readable error naming the
    odd one out. frames is a list of (name, QImage)."""
    if not frames:
        raise PixelForgeError("no frames to pack")
    first_name, first = frames[0]
    size = (first.width(), first.height())
    for name, image in frames[1:]:
        other = (image.width(), image.height())
        if other != size:
            raise PixelForgeError(
                "frame %s is %d x %d but frame %s is %d x %d — all "
                "frames in a sheet must be the same size"
                % (name, other[0], other[1], first_name,
                   size[0], size[1]))
    return size


def pack_frames(images, cols=None, padding=0):
    """Pack QImages into one transparent sheet.

    Returns (sheet QImage, layout dict). All images must be the
    same size. Empty cells in a partial last row stay transparent.
    """
    if not images:
        raise PixelForgeError("no frames to pack")
    check_uniform([(str(i), img) for i, img in enumerate(images)])
    frame_w, frame_h = images[0].width(), images[0].height()
    layout = layout_frames(len(images), frame_w, frame_h,
                           cols=cols, padding=padding)
    sheet = QImage(layout["sheet_width"], layout["sheet_height"],
                   QImage.Format.Format_ARGB32)
    sheet.fill(0)  # transparent background, gaps, and empty cells
    painter = QPainter(sheet)
    try:
        for image, (x, y) in zip(images, layout["positions"]):
            painter.drawImage(x, y, image)
    finally:
        painter.end()
    return sheet, layout


def duration_for_fps(fps):
    """Milliseconds per frame at fps (8 fps -> 125 ms)."""
    if fps is None or fps <= 0:
        raise PixelForgeError("fps must be greater than 0")
    return max(1, int(round(1000 / fps)))


def build_metadata(sheet_name, frame_names, frame_w, frame_h,
                   layout, fps):
    """The metadata dict for a packed sheet.

    sheet_name is the PNG file's name ("" when the sheet has not
    been saved yet, as in the preview dialog — the name is filled
    in at export). frame_names are the source names in playback
    order. Every frame carries the same duration from fps.
    """
    duration = duration_for_fps(fps)
    frames = []
    for index, (name, (x, y)) in enumerate(
            zip(frame_names, layout["positions"])):
        frames.append({"name": name, "index": index,
                       "x": x, "y": y, "w": frame_w, "h": frame_h,
                       "duration_ms": duration})
    return {"format": FORMAT_NAME, "version": FORMAT_VERSION,
            "sheet": sheet_name,
            "sheet_width": layout["sheet_width"],
            "sheet_height": layout["sheet_height"],
            "frame_width": frame_w, "frame_height": frame_h,
            "columns": layout["columns"], "rows": layout["rows"],
            "padding": layout["padding"],
            "fps": fps, "frames": frames}


def load_frame_dir(directory):
    """Load the PNG frames in a folder, in playback order.

    Returns a list of (name, QImage). frame_NNN.png names sort
    numerically (frame_1000 after frame_999); other names sort
    alphabetically. Non-PNG files are ignored. Raises
    PixelForgeError when the folder is missing, holds no PNGs, or
    holds a file that is not a readable image.
    """
    folder = Path(directory)
    if not folder.is_dir():
        raise PixelForgeError("not found: %s" % directory)
    paths = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() == ".png"]
    if not paths:
        raise PixelForgeError(
            "no PNG frames in %s (put frame images in the folder "
            "first)" % directory)
    ordered = sorted_frames(sorted(paths, key=lambda p: p.name.lower()))
    frames = []
    for path in ordered:
        frames.append((path.name, load_image(path)))
    return frames


def write_sheet(images, names, out_png, out_json, cols=None,
                padding=0, fps=8):
    """Pack images, save the sheet PNG + metadata JSON.

    Parent folders are created as needed. The JSON's "sheet" field
    is the PNG file's name, so the pair stays linked when moved
    together. Returns the metadata dict. Raises PixelForgeError
    with a readable message on any failure.
    """
    if len(images) != len(names):
        raise PixelForgeError(
            "internal error: %d images but %d names"
            % (len(images), len(names)))
    # Check with the real names FIRST so a mixed-size error names
    # the odd file (pack_frames re-checks by index only, and would
    # report "frame 1" instead of the file the user has to fix).
    check_uniform(list(zip(names, images)))
    sheet, layout = pack_frames(images, cols=cols, padding=padding)
    frame_w, frame_h = images[0].width(), images[0].height()
    png_path = Path(out_png)
    json_path = Path(out_json)
    metadata = build_metadata(png_path.name, names,
                              frame_w, frame_h, layout, fps)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(png_path), "PNG"):
        raise PixelForgeError("could not write: %s" % png_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(json_path, "w") as handle:
            json.dump(metadata, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise PixelForgeError("could not write %s: %s"
                              % (json_path, exc))
    return metadata


def load_metadata(path):
    """Read + validate a spritesheet JSON. Raises PixelForgeError
    on a missing file, bad JSON, or a missing/wrong field — never a
    traceback, never a half-loaded dict."""
    meta_path = Path(path)
    if not meta_path.is_file():
        raise PixelForgeError("not found: %s" % path)
    try:
        with open(meta_path) as handle:
            metadata = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PixelForgeError(
            "could not read %s as metadata: %s" % (path, exc))
    if not isinstance(metadata, dict):
        raise PixelForgeError(
            "%s is not a spritesheet file (expected a JSON object)"
            % path)
    if metadata.get("format") != FORMAT_NAME:
        raise PixelForgeError(
            "%s is not a Pixel Forge spritesheet (format is %r)"
            % (path, metadata.get("format")))
    if metadata.get("version") != FORMAT_VERSION:
        raise PixelForgeError(
            "%s is spritesheet version %r, this build reads version "
            "%d" % (path, metadata.get("version"), FORMAT_VERSION))
    frames = metadata.get("frames")
    if not frames:
        raise PixelForgeError("%s has no frames" % path)
    for index, frame in enumerate(frames):
        for key in ("x", "y", "w", "h"):
            if key not in frame:
                raise PixelForgeError(
                    "%s frame %d is missing %r" % (path, index, key))
    for key in ("sheet_width", "sheet_height",
                "frame_width", "frame_height"):
        if key not in metadata:
            raise PixelForgeError(
                "%s is missing %r" % (path, key))
    return metadata


def extract_frame(sheet, metadata, index):
    """Cut one frame out of a sheet by its metadata entry.

    Returns a QImage copy of that frame's rectangle. Raises
    PixelForgeError when the index is out of range.
    """
    frames = metadata["frames"]
    if index < 0 or index >= len(frames):
        raise PixelForgeError(
            "frame %d is out of range (sheet has %d frames)"
            % (index, len(frames)))
    frame = frames[index]
    return sheet.copy(frame["x"], frame["y"],
                      frame["w"], frame["h"])


def frames_from_sheet(sheet, metadata):
    """Every frame of a sheet, in playback order — the playback
    path: pack once, then play from the sheet, not the sources."""
    return [extract_frame(sheet, metadata, i)
            for i in range(len(metadata["frames"]))]
