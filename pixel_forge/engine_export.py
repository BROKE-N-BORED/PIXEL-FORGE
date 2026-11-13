"""Engine-ready export — spritesheets in the formats game engines
read (task 36).

Pixel Forge's own sheet format is documented in the README, but a
game engine wants its own: Aseprite JSON, TexturePacker JSON
(which Phaser's atlas loader and Pixi's Spritesheet both read),
a JSON atlas for Godot, a .aseprite file for Unity's 2D Aseprite
Importer (and Godot's Aseprite Wizard), and a GameMaker strip.

Every writer here takes the same inputs — frames as Images,
names, durations, tags, fps, columns, padding — and returns the
files it wrote. The grid packing is spritesheet.pack_frames,
shared with the native writer; no second packer. The JSON
writers are shaped after the formats' own exports: Aseprite's
hash format (frames keyed by filename, per-frame duration,
frameTags), TexturePacker's format (same frame entries without
durations, meta with a content hash for smartupdate).

What each format drops is stated, never hidden: TexturePacker
JSON (and so Phaser and Pixi) has no place for per-frame
durations or animation tags, and a GameMaker strip is just
frames in a row. The .aseprite writer carries durations and
tags in the file itself.

The .aseprite writer follows the public spec
(aseprite/aseprite docs/ase-file-specs.md): a 128-byte header,
one frame per animation frame (frame header with the duration
in ms), a layer chunk, a zlib-compressed cel chunk per frame,
a palette chunk, and a tags chunk. It writes 32-bit RGBA only —
Pixel Forge frames are RGBA, so no palette mapping is needed.
The file is verified structurally by a minimal reader in the
test suite; no Aseprite binary, Godot, or Unity runs in this
sandbox, so opening the exports in the real engines is a
real-PC check, not something proven here. The README says so.

Stdlib-only: no Qt, no ffmpeg — the command line uses this
module directly, and the tests run on bare python.
"""

import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

from . import __version__
from .pixelize import PixelForgeError
from .spritesheet import (check_uniform, duration_for_fps, pack_frames,
                          validate_tags)

# The engine presets, in the order the dialog lists them.
ENGINE_FORMATS = ("aseprite", "texturepacker", "godot", "unity",
                  "gamemaker", "phaser", "pixi")

# Display name + the note the dialog shows under the combo: what
# the preset writes and what it drops. The note never claims a
# format carries what it does not.
FORMAT_INFO = {
    "aseprite": (
        "Aseprite JSON",
        "Aseprite's JSON format — frames, per-frame durations, "
        "and animation tags are all carried."),
    "texturepacker": (
        "TexturePacker JSON",
        "TexturePacker's JSON format — the format has no place "
        "for frame durations or animation tags, so they are "
        "dropped."),
    "godot": (
        "Godot",
        "A JSON atlas in Aseprite's shape — frames, durations, "
        "and tags carried. Set the texture Filter to Nearest on "
        "import. Godot has no built-in JSON-atlas import, so the "
        "atlas is for script-driven use — the .aseprite file "
        "from the Unity preset is what Godot's Aseprite Wizard "
        "reads."),
    "unity": (
        "Unity (.aseprite)",
        "A minimal .aseprite file plus the sheet PNG — frames, "
        "durations, and tags carried in the .aseprite file. "
        "Unity's 2D Aseprite Importer reads it."),
    "gamemaker": (
        "GameMaker strip",
        "One row of frames — GameMaker reads the frame count "
        "from the _stripN name. No JSON, no durations, no tags."),
    "phaser": (
        "Phaser JSON",
        "TexturePacker JSON, which Phaser's atlas loader reads "
        "— frame durations and animation tags are dropped, the "
        "format has no place for them."),
    "pixi": (
        "Pixi JSON",
        "TexturePacker JSON, which Pixi's Spritesheet reads — "
        "frame durations and animation tags are dropped, the "
        "format has no place for them."),
}

# .aseprite constants, from the public spec.
ASE_MAGIC = 0xA5E0
ASE_FRAME_MAGIC = 0xF1FA
ASE_DEPTH_RGBA = 32
ASE_CHUNK_LAYER = 0x2004
ASE_CHUNK_CEL = 0x2005
ASE_CHUNK_TAGS = 0x2018
ASE_CHUNK_PALETTE = 0x2019
ASE_MAX_FRAMES = 0xFFFF
ASE_MAX_DURATION_MS = 0xFFFF
ASE_MAX_DIMENSION = 0xFFFF
ASE_MAX_PALETTE = 256
ASE_LAYER_NAME = "Layer 1"


def format_files(key, stem, count):
    """The file names one preset writes, for display (the dialog's
    note and the status line). count is the frame count — only
    the GameMaker strip needs it."""
    if key == "gamemaker":
        return ["%s_strip%d.png" % (stem, count)]
    if key == "unity":
        return ["%s.aseprite" % stem, "%s.png" % stem]
    return ["%s.png" % stem, "%s.json" % stem]


def format_note(key):
    """The carries/drops note for one preset (see FORMAT_INFO)."""
    return FORMAT_INFO[key][1]


def _resolve_durations(durations, count, fps):
    """One millisecond count per frame: the given list, validated,
    or the uniform fps duration when none was given. The rules
    match spritesheet.build_metadata (whole numbers, at least
    1 ms) so a bad duration fails the same way everywhere."""
    if durations is None:
        return [duration_for_fps(fps)] * count
    if len(durations) != count:
        raise PixelForgeError(
            "internal error: %d frames but %d durations"
            % (count, len(durations)))
    for duration in durations:
        if (not isinstance(duration, int)
                or isinstance(duration, bool) or duration < 1):
            raise PixelForgeError(
                "frame durations must be whole numbers of at "
                "least 1 ms (got %r)" % (duration,))
    return list(durations)


def _write_json_atomic(path, data):
    """Write a JSON file atomically, like write_sheet: a crash
    mid-write must not leave a half-written file next to a
    good PNG."""
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = json_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, json_path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError("could not write %s: %s"
                              % (json_path, exc))


def _pack_png(images, out_png, cols, padding):
    """Pack the frames into a sheet PNG. Returns the layout dict
    (positions + sheet size) the JSON writers need."""
    sheet, layout = pack_frames(images, cols=cols, padding=padding)
    png_path = Path(out_png)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(png_path), "PNG"):
        raise PixelForgeError("could not write: %s" % png_path)
    return layout


def _frame_entry(x, y, frame_w, frame_h):
    """One frame entry in the shape Aseprite and TexturePacker
    share: the frame's rect in the sheet, unrotated, untrimmed,
    with the source size equal to the frame size (Pixel Forge
    never trims — every frame is the full size)."""
    return {"frame": {"x": x, "y": y, "w": frame_w, "h": frame_h},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0,
                                 "w": frame_w, "h": frame_h},
            "sourceSize": {"w": frame_w, "h": frame_h}}


def aseprite_json_data(names, layout, frame_w, frame_h, png_name,
                       durations, tags, app="Pixel Forge"):
    """The Aseprite JSON dict for a packed sheet: frames keyed by
    filename, each with its rect and its own duration in ms;
    meta with the PNG name, the sheet size, scale "1" (a string
    in Aseprite's export), and frameTags when tags exist. from
    and to are 0-based frame positions, the same as Pixel
    Forge's tags."""
    frames = {}
    for name, (x, y), duration in zip(names, layout["positions"],
                                      durations):
        entry = _frame_entry(x, y, frame_w, frame_h)
        entry["duration"] = duration
        frames[name] = entry
    meta = {"app": app, "version": __version__, "image": png_name,
            "format": "RGBA8888",
            "size": {"w": layout["sheet_width"],
                     "h": layout["sheet_height"]},
            "scale": "1"}
    if tags:
        meta["frameTags"] = [{"name": tag["name"],
                              "from": tag["from"], "to": tag["to"],
                              "direction": "forward"}
                             for tag in tags]
    return {"frames": frames, "meta": meta}


def texturepacker_json_data(names, layout, frame_w, frame_h,
                            png_name, png_hash, app="Pixel Forge"):
    """The TexturePacker JSON dict for a packed sheet: the same
    frame entries as Aseprite's, without durations (the format
    has none) and without tags. scale is a number here, not a
    string — that is how TexturePacker writes it. smartupdate
    is a content hash of the PNG, so a re-export of identical
    frames produces an identical file."""
    frames = {}
    for name, (x, y) in zip(names, layout["positions"]):
        frames[name] = _frame_entry(x, y, frame_w, frame_h)
    meta = {"app": app, "version": __version__, "image": png_name,
            "format": "RGBA8888",
            "size": {"w": layout["sheet_width"],
                     "h": layout["sheet_height"]},
            "scale": 1, "smartupdate": png_hash}
    return {"frames": frames, "meta": meta}


def write_aseprite_json(images, names, out_png, out_json, cols=None,
                        padding=0, fps=8, durations=None, tags=None,
                        app="Pixel Forge"):
    """Pack the frames and write the sheet PNG + an Aseprite JSON
    file (also used for the Godot preset, with the app name
    changed). Returns [png_path, json_path]. Durations and tags
    are carried; tags are validated before anything is written."""
    if len(images) != len(names):
        raise PixelForgeError(
            "internal error: %d images but %d names"
            % (len(images), len(names)))
    check_uniform(list(zip(names, images)))
    resolved = _resolve_durations(durations, len(images), fps)
    if tags:
        validate_tags(tags, len(images))
    layout = _pack_png(images, out_png, cols, padding)
    frame_w, frame_h = images[0].width(), images[0].height()
    data = aseprite_json_data(names, layout, frame_w, frame_h,
                              Path(out_png).name, resolved, tags, app)
    _write_json_atomic(out_json, data)
    return [Path(out_png), Path(out_json)]


def write_texturepacker_json(images, names, out_png, out_json,
                             cols=None, padding=0, fps=8,
                             app="Pixel Forge"):
    """Pack the frames and write the sheet PNG + a TexturePacker
    JSON file (also used for the Phaser and Pixi presets, with
    the app name changed). Returns [png_path, json_path]. No
    durations or tags are written — the format has no place for
    them. fps is accepted for a uniform call shape and unused."""
    if len(images) != len(names):
        raise PixelForgeError(
            "internal error: %d images but %d names"
            % (len(images), len(names)))
    check_uniform(list(zip(names, images)))
    layout = _pack_png(images, out_png, cols, padding)
    frame_w, frame_h = images[0].width(), images[0].height()
    with open(out_png, "rb") as handle:
        png_hash = hashlib.sha256(handle.read()).hexdigest()
    data = texturepacker_json_data(names, layout, frame_w, frame_h,
                                   Path(out_png).name, png_hash, app)
    _write_json_atomic(out_json, data)
    return [Path(out_png), Path(out_json)]


def write_gamemaker_strip(images, out_path):
    """Pack the frames into one horizontal row — a GameMaker
    strip. The _stripN name is the caller's job (see
    export_engine); this writes the pixels. Returns the path."""
    if not images:
        raise PixelForgeError("no frames to pack")
    check_uniform([(str(i), image)
                   for i, image in enumerate(images)])
    sheet, _layout = pack_frames(images, cols=len(images), padding=0)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(out), "PNG"):
        raise PixelForgeError("could not write: %s" % out)
    return out


# ------------------------------------------------------------ .aseprite
# Minimal writer from the public spec (aseprite/aseprite
# docs/ase-file-specs.md). All integers little-endian. One layer
# ("Layer 1"), one zlib-compressed cel per frame, a palette chunk
# with the distinct colors, and a tags chunk when tags exist.

def _ase_string(text):
    """A spec STRING: WORD byte length + UTF-8 bytes, no NUL."""
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise PixelForgeError(
            "name too long for a .aseprite file: %r" % text)
    return struct.pack("<H", len(raw)) + raw


def _ase_chunk(chunk_type, data):
    """A chunk: DWORD size (including the 6 header bytes), WORD
    type, then the data."""
    return struct.pack("<IH", len(data) + 6, chunk_type) + data


def _ase_layer_chunk(name, width, height):
    """Layer chunk (0x2004): visible + editable, normal type,
    top level, normal blend, fully opaque. Default width/height
    are ignored by readers but filled in anyway."""
    data = struct.pack("<HHHHHHB", 3, 0, 0, width, height, 0, 255)
    data += b"\x00\x00\x00"
    data += _ase_string(name)
    return _ase_chunk(ASE_CHUNK_LAYER, data)


def _ase_palette_chunk(colors):
    """Palette chunk (0x2019): the distinct colors, first entry
    to last, no names. For an RGBA sprite the palette is
    informational — the cels carry real RGBA pixels — but
    readers expect the chunk to exist."""
    data = struct.pack("<III", len(colors), 0, len(colors) - 1)
    data += b"\x00" * 8
    for red, green, blue, alpha in colors:
        data += struct.pack("<HBBBB", 0, red, green, blue, alpha)
    return _ase_chunk(ASE_CHUNK_PALETTE, data)


def _ase_tags_chunk(tags):
    """Tags chunk (0x2018): one entry per tag — from/to frames
    (0-based, the same as Pixel Forge's tags), forward
    direction, repeat 0 (unspecified). The RGB color field is
    deprecated in the spec, so it is written black."""
    data = struct.pack("<H", len(tags)) + b"\x00" * 8
    for tag in tags:
        data += struct.pack("<HHBH", tag["from"], tag["to"], 0, 0)
        data += b"\x00" * 6
        data += b"\x00\x00\x00"
        data += b"\x00"
        data += _ase_string(tag["name"])
    return _ase_chunk(ASE_CHUNK_TAGS, data)


def _ase_cel_chunk(image):
    """Cel chunk (0x2005), type 2 (compressed image): layer 0,
    at (0, 0), fully opaque, default z-index, the frame's RGBA
    pixels zlib-compressed. Image.data is already RGBA
    row-major, exactly the spec's PIXEL order."""
    data = struct.pack("<HhhBHh", 0, 0, 0, 255, 2, 0)
    data += b"\x00" * 5
    data += struct.pack("<HH", image.width(), image.height())
    data += zlib.compress(bytes(image.data))
    return _ase_chunk(ASE_CHUNK_CEL, data)


def _ase_frame(duration, chunks):
    """One frame: the 16-byte frame header (size, magic 0xF1FA,
    chunk count in the old field, duration in ms, new chunk
    count 0 meaning "use the old field") + the chunks."""
    body = b"".join(chunks)
    header = struct.pack("<IHHH2sI", 16 + len(body),
                         ASE_FRAME_MAGIC, len(chunks), duration,
                         b"\x00\x00", 0)
    return header + body


def _ase_header(file_size, frame_count, width, height, color_count):
    """The 128-byte file header: size, magic 0xA5E0, frame
    count, size, 32-bit RGBA depth, flags 1 (layer opacity is
    valid — the layer chunk writes 255), speed 0 (deprecated;
    durations live in the frame headers), pixel ratio 1:1, no
    grid."""
    return struct.pack(
        "<IHHHHHIHIIB3sHBBhhHH84s",
        file_size, ASE_MAGIC, frame_count, width, height,
        ASE_DEPTH_RGBA, 1, 0, 0, 0, 0, b"\x00\x00\x00",
        color_count, 0, 0, 0, 0, 0, 0, b"\x00" * 84)


def _distinct_colors(images):
    """Distinct (r, g, b, a) colors across all frames, in
    first-appearance order, capped at 256 — the palette chunk
    for the .aseprite file. An all-transparent animation still
    gets one entry (transparent black), so the palette chunk
    is never empty."""
    seen = []
    known = set()
    for image in images:
        data = image.data
        for offset in range(0, len(data), 4):
            color = (data[offset], data[offset + 1],
                     data[offset + 2], data[offset + 3])
            if color not in known:
                known.add(color)
                seen.append(color)
                if len(seen) >= ASE_MAX_PALETTE:
                    return seen
    return seen or [(0, 0, 0, 0)]


def write_aseprite_file(images, out_path, durations=None, fps=8,
                        tags=None):
    """Write a minimal .aseprite file: one layer, one compressed
    cel per frame, a palette chunk, and a tags chunk when tags
    exist. Returns the path. Frame counts, sizes, and durations
    live in WORD fields, so anything over 65535 fails with a
    plain error instead of wrapping silently."""
    if not images:
        raise PixelForgeError("no frames to write")
    if len(images) > ASE_MAX_FRAMES:
        raise PixelForgeError(
            "%d frames is too many for a .aseprite file (the "
            "limit is %d)" % (len(images), ASE_MAX_FRAMES))
    check_uniform([(str(i), image)
                   for i, image in enumerate(images)])
    width, height = images[0].width(), images[0].height()
    if width > ASE_MAX_DIMENSION or height > ASE_MAX_DIMENSION:
        raise PixelForgeError(
            "a %d x %d frame is too large for a .aseprite file "
            "(the limit is %d in each direction)"
            % (width, height, ASE_MAX_DIMENSION))
    resolved = _resolve_durations(durations, len(images), fps)
    for index, duration in enumerate(resolved):
        if duration > ASE_MAX_DURATION_MS:
            raise PixelForgeError(
                "frame %d lasts %d ms — .aseprite frames top "
                "out at %d ms" % (index + 1, duration,
                                 ASE_MAX_DURATION_MS))
    if tags:
        validate_tags(tags, len(images))
    colors = _distinct_colors(images)
    first_chunks = [_ase_layer_chunk(ASE_LAYER_NAME, width, height),
                    _ase_palette_chunk(colors)]
    if tags:
        first_chunks.append(_ase_tags_chunk(tags))
    first_chunks.append(_ase_cel_chunk(images[0]))
    frames = [_ase_frame(resolved[0], first_chunks)]
    for image, duration in zip(images[1:], resolved[1:]):
        frames.append(_ase_frame(duration, [_ase_cel_chunk(image)]))
    body = b"".join(frames)
    data = _ase_header(128 + len(body), len(images), width, height,
                       len(colors)) + body
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out.with_suffix(".aseprite.tmp")
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, out)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError("could not write %s: %s" % (out, exc))
    return out


def export_engine(format_key, images, names, out_dir, stem,
                  cols=None, padding=0, fps=8, durations=None,
                  tags=None, meta_path=None):
    """Write one preset's files into out_dir, named from stem.
    Returns the written paths, PNG first where a preset writes
    two files (the .aseprite file first for Unity — it is the
    file the engine opens). meta_path overrides the JSON path
    for the JSON presets (the CLI's --meta); it is an error to
    pass it for Unity or GameMaker, which write no JSON.
    Raises PixelForgeError on a bad format key, bad input, or
    a write failure."""
    if format_key not in ENGINE_FORMATS:
        raise PixelForgeError(
            "unknown engine format %r (choose from: %s)"
            % (format_key, ", ".join(ENGINE_FORMATS)))
    if not images:
        raise PixelForgeError("no frames to export")
    if len(images) != len(names):
        raise PixelForgeError(
            "internal error: %d images but %d names"
            % (len(images), len(names)))
    # Check with the real names FIRST so a mixed-size error names
    # the odd file (the writers re-check by index only).
    check_uniform(list(zip(names, images)))
    if meta_path is not None and format_key in ("unity",
                                                "gamemaker"):
        raise PixelForgeError(
            "--meta only applies to formats that write a JSON "
            "file")
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if format_key == "gamemaker":
        strip = folder / ("%s_strip%d.png" % (stem, len(images)))
        return [write_gamemaker_strip(images, str(strip))]
    if format_key == "unity":
        ase_path = folder / ("%s.aseprite" % stem)
        png_path = folder / ("%s.png" % stem)
        # The .aseprite file first: it validates durations and
        # tags before writing anything, so a bad duration leaves
        # no stray PNG behind.
        write_aseprite_file(images, str(ase_path),
                            durations=durations, fps=fps, tags=tags)
        _pack_png(images, png_path, cols, padding)
        return [ase_path, png_path]
    png_path = folder / ("%s.png" % stem)
    json_path = (Path(meta_path) if meta_path is not None
                 else folder / ("%s.json" % stem))
    if format_key == "aseprite":
        return write_aseprite_json(images, names, str(png_path),
                                   str(json_path), cols=cols,
                                   padding=padding, fps=fps,
                                   durations=durations, tags=tags)
    if format_key == "godot":
        return write_aseprite_json(images, names, str(png_path),
                                   str(json_path), cols=cols,
                                   padding=padding, fps=fps,
                                   durations=durations, tags=tags,
                                   app="Pixel Forge (Godot)")
    app = {"texturepacker": "Pixel Forge",
           "phaser": "Pixel Forge (Phaser)",
           "pixi": "Pixel Forge (Pixi)"}[format_key]
    return write_texturepacker_json(images, names, str(png_path),
                                    str(json_path), cols=cols,
                                    padding=padding, fps=fps, app=app)
