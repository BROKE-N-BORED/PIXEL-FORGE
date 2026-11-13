"""Pixel Forge command line.

    pixel-forge convert INPUT --size 32 --colors 16 --palette pico8 --out OUT.png
    pixel-forge convert FOLDER [--out DIR]
    pixel-forge frames VIDEO --fps 8 --out DIR
    pixel-forge prompt "a knight" --out DIR
    pixel-forge sheet DIR --out sheet.png --meta sheet.json
    pixel-forge library list [--tag TAG]
    pixel-forge library add FILE --name NAME [--tags a,b]
    pixel-forge library remove NAME
    pixel-forge library rename OLD NEW
    pixel-forge library export DIR
    pixel-forge library import DIR
    pixel-forge presets list
    pixel-forge presets save NAME --size 32 --palette pico8 --colors 16
    pixel-forge presets delete NAME
    pixel-forge code FILE --list
    pixel-forge code FILE --asset NAME --out OUT.png
    pixel-forge code FILE --all [--out DIR]
    pixel-forge skin FILE --out DIR
    pixel-forge gui
    pixel-forge --version

Every command accepts --json (machine-readable output for
scripting) and --quiet (no info lines on stdout; errors still
print on stderr). Either flag may come before or after the
command: `pixel-forge --json library list` and
`pixel-forge library list --json` are the same.

--size takes a square size (32) or WIDTHxHEIGHT (64x48). With
--keep-aspect, --size is the box the image is fitted into. convert's
--out defaults to <input-stem>-pixel.png next to the input; frames'
--out defaults to <video-stem>-frames next to the video; prompt's
--out defaults to <prompt-slug>-prompt in the current folder.
frames needs ffmpeg installed (sudo apt install ffmpeg). prompt
needs the image engine and model (see pixel-forge prompt --help).
"""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .codeinput import parse_paths
from .imagegen import (DEFAULT_NEGATIVE, DEFAULT_SIZE, LCM_CFG,
                       LCM_STEPS, MODEL_FILENAME,
                       GENERATION_MIN_RAM_BYTES, available_ram_bytes,
                       find_engine, find_model, format_bytes,
                       prompt_to_sprites, slugify)
from .library import Library, normalize_tags
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError,
                       load_image, pixelize_file, plural)
from .presets import PresetStore
from .skins import extract_skin
from .spritesheet import load_frame_dir, write_sheet
from .video import DEFAULT_FPS, video_to_frames

PALETTE_CHOICES = ["image", "pico8", "gameboy", "grayscale"]


def parse_size(text):
    """Parse "32" or "64x48" into (width, height)."""
    text = text.strip().lower().replace("x", " ").replace(",", " ")
    parts = text.split()
    try:
        if len(parts) == 1:
            value = int(parts[0])
            return value, value
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except ValueError:
        pass
    raise PixelForgeError(
        "bad --size %r (use a number like 32, or WIDTHxHEIGHT like 64x48)"
        % text)


def _add_pixelize_args(parser):
    """The size/palette/dither flags — identical for convert and
    frames, since both run through the same Item 1 core."""
    parser.add_argument("--size", default="32",
                        help="output size: a number (32) or WIDTHxHEIGHT "
                        "(64x48). Default: 32")
    parser.add_argument("--keep-aspect", action="store_true",
                        help="fit inside the --size box instead of "
                        "stretching to it")
    parser.add_argument("--colors", type=int, default=16,
                        help="max colors, %d-%d. Default: 16"
                        % (MIN_COLORS, MAX_COLORS))
    parser.add_argument("--palette", choices=PALETTE_CHOICES,
                        default="image",
                        help="palette: image (from the picture), pico8, "
                        "gameboy, grayscale. Default: image")
    parser.add_argument("--dither", dest="dither", action="store_true",
                        default=True,
                        help="Ordered (Bayer) dithering (default)")
    parser.add_argument("--no-dither", dest="dither",
                        action="store_false",
                        help="turn dithering off")


def _validate_pixelize_args(args):
    """Shared checks: returns (width, height)."""
    width, height = parse_size(args.size)
    if args.colors < MIN_COLORS or args.colors > MAX_COLORS:
        raise PixelForgeError(
            "colors must be between %d and %d" % (MIN_COLORS, MAX_COLORS))
    return width, height


# Image extensions a folder convert looks for — the formats
# QImageReader usually supports (the window's supported_filter
# is built from the same list at run time). A file with one of
# these extensions that still fails to load is reported and
# skipped per file; anything else in the folder is ignored.
IMAGE_EXTENSIONS = {
    ".bmp", ".cur", ".gif", ".icns", ".ico", ".jpeg", ".jpg",
    ".pbm", ".pgm", ".png", ".ppm", ".svg", ".svgz", ".tga",
    ".tif", ".tiff", ".wbmp", ".webp", ".xbm", ".xpm"}


def _add_output_args(parser):
    """--json / --quiet on a subparser, so the flags work after
    the command too (`pixel-forge library list --json`). The
    defaults are SUPPRESS: a subparser default would overwrite
    a flag given before the command (`pixel-forge --json
    library list`), so these only set the attribute when the
    flag is actually present. The top-level parser defines
    them with default False, so the attributes always exist."""
    parser.add_argument(
        "--json", dest="json", action="store_true",
        default=argparse.SUPPRESS,
        help="machine-readable JSON output instead of human lines")
    parser.add_argument(
        "--quiet", dest="quiet", action="store_true",
        default=argparse.SUPPRESS,
        help="no info lines on stdout (errors still print)")


def _is_json(args):
    return bool(getattr(args, "json", False))


def _is_quiet(args):
    return bool(getattr(args, "quiet", False))


def _info(args, message):
    """An info line on stdout — suppressed by --quiet and by
    --json, which replaces the human output entirely."""
    if not _is_quiet(args) and not _is_json(args):
        print(message)


def _emit_json(args, payload):
    """The machine output for --json. Never suppressed by
    --quiet: when both flags are given, the JSON is the point."""
    if _is_json(args):
        print(json.dumps(payload, indent=2))


def _unique_stem(stem, used):
    """A stem not yet in `used` — the plain stem, or stem-2,
    stem-3, ... when two assets share a name. Adds the chosen
    stem to `used`."""
    name = stem
    number = 2
    while name in used:
        name = "%s-%d" % (stem, number)
        number += 1
    used.add(name)
    return name


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pixel-forge",
        description="Turn images, videos, prompts, and code into "
        "pixel art and sprites.")
    parser.add_argument("--version", action="version",
                        version="pixel-forge %s" % __version__)
    parser.add_argument(
        "--json", dest="json", action="store_true", default=False,
        help="machine-readable JSON output instead of human lines")
    parser.add_argument(
        "--quiet", dest="quiet", action="store_true", default=False,
        help="no info lines on stdout (errors still print)")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    convert = sub.add_parser(
        "convert", help="convert one image, or every image in a "
        "folder, into pixel sprites")
    convert.add_argument("input", help="image to convert (PNG, JPG, "
                         "BMP, WebP, GIF, and more), or a folder of "
                         "images to convert in turn")
    _add_pixelize_args(convert)
    _add_output_args(convert)
    convert.add_argument("--out", default=None,
                         help="output PNG path (default: "
                         "<input-stem>-pixel.png next to the input); "
                         "for a folder input, the folder to write "
                         "the sprites into (default: next to each "
                         "input)")

    frames = sub.add_parser(
        "frames", help="extract a video's frames as pixel sprites "
        "(needs ffmpeg)")
    frames.add_argument("video", help="video to read (mp4, webm, mkv, "
                        "mov, avi, and more — whatever ffmpeg reads)")
    frames.add_argument("--fps", type=float, default=DEFAULT_FPS,
                        help="frames per second to keep (default: %s)"
                        % DEFAULT_FPS)
    frames.add_argument("--every", type=int, default=1,
                        help="keep every Nth frame (default: 1)")
    frames.add_argument("--start", default=None,
                        help="start time: seconds (12.5) or MM:SS (1:30)")
    frames.add_argument("--end", default=None,
                        help="end time: seconds (12.5) or MM:SS (1:30)")
    _add_pixelize_args(frames)
    _add_output_args(frames)
    frames.add_argument("--out", default=None,
                        help="output folder for frame_001.png, ... "
                        "(default: <video-stem>-frames next to the "
                        "video)")

    prompt = sub.add_parser(
        "prompt", help="generate sprite candidates from a text prompt "
        "(needs the image engine and model)")
    prompt.add_argument("prompt", help="describe the sprite you want "
                        "(for example: a knight with a red shield)")
    prompt.add_argument("--negative", default=DEFAULT_NEGATIVE,
                        help="things the picture should avoid "
                        "(default: %(default)s)")
    prompt.add_argument("--count", type=int, default=3,
                        help="how many candidates to generate "
                        "(default: 3)")
    # Generation settings (task 20): the LCM pins are the
    # defaults, these let the user change them. --size is
    # already the pixelize output size on this command, so the
    # generated image size is --gen-size.
    prompt.add_argument("--steps", type=int, default=LCM_STEPS,
                        help="diffusion steps per candidate "
                        "(default: %d)" % LCM_STEPS)
    prompt.add_argument("--cfg", type=float, default=LCM_CFG,
                        help="cfg scale — how closely the picture "
                        "follows the prompt (default: %s)" % LCM_CFG)
    prompt.add_argument("--gen-size", type=int, default=DEFAULT_SIZE,
                        help="generated image size in pixels, square "
                        "(default: %d)" % DEFAULT_SIZE)
    _add_pixelize_args(prompt)
    _add_output_args(prompt)
    prompt.add_argument("--out", default=None,
                        help="output folder for the candidates, their "
                        "pixelized versions, and batch.json (default: "
                        "<prompt-slug>-prompt in the current folder)")

    sheet = sub.add_parser(
        "sheet", help="pack a folder of same-size PNG frames into "
        "a spritesheet PNG + metadata JSON")
    sheet.add_argument("dir", help="folder of PNG frames, all the "
                       "same size (frame_001.png, ...)")
    sheet.add_argument("--out", default=None,
                       help="output sheet PNG (default: "
                       "<folder-stem>-sheet.png next to the folder)")
    sheet.add_argument("--meta", default=None,
                       help="output metadata JSON (default: the "
                       "--out name with .json)")
    sheet.add_argument("--cols", type=int, default=0,
                       help="columns in the grid, 0 = automatic "
                       "square-ish grid (default: 0)")
    sheet.add_argument("--padding", type=int, default=0,
                       help="gap between frames in pixels "
                       "(default: 0)")
    sheet.add_argument("--fps", type=float, default=DEFAULT_FPS,
                       help="playback fps recorded in the metadata "
                       "(default: %s)" % DEFAULT_FPS)
    _add_output_args(sheet)

    library = sub.add_parser(
        "library", help="manage the saved sprite library "
        "(~/pixel-forge-library/, or PIXELFORGE_LIBRARY)")
    _add_output_args(library)
    lib_sub = library.add_subparsers(dest="library_command",
                                     metavar="command")
    lib_sub.required = True
    lib_list = lib_sub.add_parser(
        "list", help="list saved sprites")
    lib_list.add_argument("--tag", default=None,
                          help="only show sprites with this tag")
    _add_output_args(lib_list)
    lib_add = lib_sub.add_parser(
        "add", help="add an image file to the library")
    lib_add.add_argument("file", help="image file to add")
    lib_add.add_argument("--name", required=True,
                         help="name for the sprite in the library")
    lib_add.add_argument("--tags", default="",
                         help="comma-separated tags")
    _add_output_args(lib_add)
    lib_remove = lib_sub.add_parser(
        "remove", help="delete a sprite from the library, file "
        "and all (by name, or by id when several sprites share "
        "a name — see library list --json for ids)")
    lib_remove.add_argument("name", help="sprite name (or id) "
                            "to remove")
    _add_output_args(lib_remove)
    lib_rename = lib_sub.add_parser(
        "rename", help="rename a sprite in the library (the "
        "file stays put)")
    lib_rename.add_argument("old", help="current sprite name "
                            "(or id)")
    lib_rename.add_argument("new", help="new name — refused "
                            "when another sprite already has it")
    _add_output_args(lib_rename)
    lib_export = lib_sub.add_parser(
        "export", help="copy the whole library (sprites + "
        "index) to a folder as a backup")
    lib_export.add_argument("dir", help="folder to write the "
                            "backup into")
    _add_output_args(lib_export)
    lib_import = lib_sub.add_parser(
        "import", help="add sprites from a library backup "
        "folder (entries already in the library are skipped)")
    lib_import.add_argument("dir", help="backup folder to read")
    _add_output_args(lib_import)

    presets = sub.add_parser(
        "presets", help="manage pixelize presets (built-in and "
        "saved): list, save, delete, export, import")
    _add_output_args(presets)
    pre_sub = presets.add_subparsers(dest="presets_command",
                                     metavar="command")
    pre_sub.required = True
    pre_list = pre_sub.add_parser("list", help="list all presets")
    _add_output_args(pre_list)
    pre_save = pre_sub.add_parser(
        "save", help="save a preset from the pixelize flags")
    pre_save.add_argument("name", help="name for the preset")
    _add_pixelize_args(pre_save)
    _add_output_args(pre_save)
    pre_save.add_argument("--force", action="store_true",
                          help="replace a preset that already has "
                          "this name (otherwise saving over a name "
                          "is refused)")
    pre_delete = pre_sub.add_parser(
        "delete", help="delete a saved preset (built-ins cannot "
        "be deleted)")
    pre_delete.add_argument("name", help="preset name to delete")
    _add_output_args(pre_delete)
    pre_export = pre_sub.add_parser(
        "export", help="export a preset to a JSON file to share")
    pre_export.add_argument("name", help="preset name (built-in "
                            "or saved — a saved preset wins when "
                            "both exist)")
    pre_export.add_argument("--out", required=True,
                            help="JSON file to write")
    _add_output_args(pre_export)
    pre_import = pre_sub.add_parser(
        "import", help="import a preset from a shared JSON file")
    pre_import.add_argument("file", help="preset JSON file to "
                            "import")
    _add_output_args(pre_import)

    code = sub.add_parser(
        "code", help="reproduce pixel-art assets from source code "
        "(.py, .js, .ts — parsed statically, never executed)")
    code.add_argument("file", help="code file or folder of code "
                      "files to parse")
    code.add_argument("--list", action="store_true",
                      help="list the assets found, instead of "
                      "rendering one")
    which = code.add_mutually_exclusive_group()
    which.add_argument("--asset", default=None,
                       help="name of the asset to render (see "
                       "--list)")
    which.add_argument("--all", action="store_true",
                       help="render every asset in the file, one "
                       "PNG per asset (conflicts with --asset)")
    _add_output_args(code)
    code.add_argument("--out", default=None,
                      help="output PNG path for --asset (default: "
                      "<asset>-sprite.png in the current folder); "
                      "for --all, the folder to write the sprites "
                      "into (default: <file-stem>-sprites in the "
                      "current folder)")

    skin = sub.add_parser(
        "skin", help="extract a Minecraft skin's parts (64x64 or "
        "64x32 PNG) into one PNG per part plus the figure")
    skin.add_argument("file", help="skin PNG to extract")
    skin.add_argument("--out", default=None,
                      help="output folder (default: "
                      "<skin-stem>-skin-parts next to the file)")
    _add_output_args(skin)

    gui = sub.add_parser("gui", help="open the Pixel Forge window")
    _add_output_args(gui)
    return parser


def cmd_convert(args):
    width, height = _validate_pixelize_args(args)
    src = Path(args.input)
    if src.is_dir():
        return _cmd_convert_folder(args, src, width, height)
    out = args.out
    if out is None:
        out = str(src.with_name(src.stem + "-pixel.png"))
    width_out, height_out, colors = pixelize_file(
        args.input, out, width=width, height=height,
        keep_aspect=args.keep_aspect, palette=args.palette,
        max_colors=args.colors, dither=args.dither)
    _emit_json(args, {"output": str(out), "width": width_out,
                      "height": height_out, "colors": colors})
    _info(args, "%s: %d x %d, %d colors"
          % (out, width_out, height_out, colors))
    return 0


def _cmd_convert_folder(args, folder, width, height):
    """Convert every image in a folder (task 28): one bad file
    is reported on stderr and skipped, the rest still convert.
    Exit 1 only when nothing converted at all."""
    images = sorted(path for path in folder.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in IMAGE_EXTENSIONS)
    if not images:
        raise PixelForgeError("no images in %s" % folder)
    out_dir = None
    if args.out is not None:
        out_dir = Path(args.out)
        if out_dir.exists() and not out_dir.is_dir():
            raise PixelForgeError(
                "--out must be a folder when converting a folder: "
                "%s" % args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
    converted, failed = [], []
    for src in images:
        if out_dir is not None:
            out = out_dir / (src.stem + "-pixel.png")
        else:
            out = src.with_name(src.stem + "-pixel.png")
        try:
            width_out, height_out, colors = pixelize_file(
                str(src), str(out), width=width, height=height,
                keep_aspect=args.keep_aspect, palette=args.palette,
                max_colors=args.colors, dither=args.dither)
        except PixelForgeError as exc:
            failed.append({"file": src.name, "reason": str(exc)})
            print("pixel-forge: skipped %s: %s" % (src.name, exc),
                  file=sys.stderr)
            continue
        converted.append(str(out))
        _info(args, "%s: %d x %d, %d colors"
              % (out, width_out, height_out, colors))
    if not converted:
        raise PixelForgeError(
            "could not convert anything in %s" % folder)
    _emit_json(args, {"converted": converted, "failed": failed})
    return 0


def cmd_frames(args):
    width, height = _validate_pixelize_args(args)
    if args.fps <= 0:
        raise PixelForgeError("fps must be greater than 0")
    if args.every < 1:
        raise PixelForgeError("--every must be at least 1")
    out = args.out
    if out is None:
        src = Path(args.video)
        out = str(src.with_name(src.stem + "-frames"))
    written = video_to_frames(
        args.video, out, fps=args.fps, every=args.every,
        start=args.start, end=args.end, width=width, height=height,
        keep_aspect=args.keep_aspect, palette=args.palette,
        max_colors=args.colors, dither=args.dither)
    first = load_image(written[0])
    _emit_json(args, {"frames": len(written),
                      "width": first.width(),
                      "height": first.height(), "out": str(out)})
    _info(args, "%d frames, %d x %d, in %s"
          % (len(written), first.width(), first.height(), out))
    return 0


def cmd_prompt(args):
    width, height = _validate_pixelize_args(args)
    if args.count < 1:
        raise PixelForgeError("count must be at least 1")
    if args.steps < 1:
        raise PixelForgeError("steps must be at least 1")
    if args.cfg <= 0:
        raise PixelForgeError("cfg scale must be above 0")
    if args.gen_size < 1:
        raise PixelForgeError("gen-size must be at least 1")
    out = args.out
    if out is None:
        out = slugify(args.prompt) + "-prompt"

    def _progress(done, total, record):
        if _is_quiet(args) or _is_json(args):
            return
        status = record["status"]
        line = "candidate %d of %d: %s (%s)" % (
            done, total, record["file"], status)
        if status == "failed" and record.get("error"):
            line += " — %s" % record["error"]
        print(line)

    # Name the model file in use, on stderr before generating
    # (review B15): find_model picks up ANY .gguf in a model
    # home, so a wrong model was used without a word. Only when
    # both resolve — otherwise prompt_to_sprites raises the
    # proper missing-engine/model error below.
    engine = find_engine()
    model = find_model()
    if engine is not None and model is not None:
        if model.name == MODEL_FILENAME:
            print("using model: %s" % model, file=sys.stderr)
        else:
            print("using model: %s (not the pinned LCM file %s)"
                  % (model, MODEL_FILENAME), file=sys.stderr)

    # RAM pre-flight (task 27): the model needs about 2.1 GB in
    # memory. The CLI only warns — a non-interactive command does
    # not stop to ask — and proceeds. Unknown memory (None) stays
    # silent.
    available = available_ram_bytes()
    if (available is not None
            and available < GENERATION_MIN_RAM_BYTES):
        print("pixel-forge: warning: only %s of memory is free — "
              "the image model needs about 2.1 GB, so generation "
              "may be slow or fail"
              % format_bytes(available), file=sys.stderr)

    batch, bdir = prompt_to_sprites(
        args.prompt, out, negative=args.negative, count=args.count,
        width=width, height=height, keep_aspect=args.keep_aspect,
        palette=args.palette, max_colors=args.colors,
        dither=args.dither, steps=args.steps, cfg=args.cfg,
        size=args.gen_size, progress=_progress)
    landed = [c for c in batch["candidates"]
              if c["status"] == "pending"]
    failed = len(batch["candidates"]) - len(landed)
    _emit_json(args, {"batch": str(bdir),
                      "candidates": len(landed),
                      "failed": failed})
    _info(args, "%s + pixelized versions in %s"
          % (plural(len(landed), "candidate"), bdir))
    if failed:
        _info(args, "%s failed — see batch.json in %s"
              % (plural(failed, "candidate"), bdir))
    # Pixelize failures (task 27): pixelize_candidates skips a
    # bad candidate and records the reason in the batch — one
    # plain stderr line per skipped candidate, naming it.
    for record in batch["candidates"]:
        if record.get("pixel_error"):
            print("pixel-forge: could not pixelize %s — %s"
                  % (record["file"], record["pixel_error"]),
                  file=sys.stderr)
    return 0


def cmd_sheet(args):
    if args.cols < 0:
        raise PixelForgeError("--cols must be 0 (automatic) or more")
    if args.padding < 0:
        raise PixelForgeError("--padding cannot be negative")
    if args.fps <= 0:
        raise PixelForgeError("fps must be greater than 0")
    folder = Path(args.dir)
    out = args.out
    if out is None:
        out = str(folder.parent / (folder.name + "-sheet.png"))
    meta = args.meta
    if meta is None:
        meta = str(Path(out).with_suffix(".json"))
    frames = load_frame_dir(folder)
    names = [name for name, _ in frames]
    images = [image for _, image in frames]
    metadata = write_sheet(images, names, out, meta,
                           cols=args.cols or None,
                           padding=args.padding, fps=args.fps)
    _emit_json(args, {"sheet": str(out), "meta": str(meta),
                      "frames": len(frames),
                      "columns": metadata["columns"],
                      "rows": metadata["rows"],
                      "sheet_width": metadata["sheet_width"],
                      "sheet_height": metadata["sheet_height"],
                      "fps": args.fps})
    _info(args, "%d frames, %d x %d grid, %d x %d sheet, %s fps — "
          "wrote %s and %s"
          % (len(frames), metadata["columns"], metadata["rows"],
             metadata["sheet_width"], metadata["sheet_height"],
             args.fps, out, meta))
    return 0


def _warn_skipped(lib):
    """One stderr line when the index had malformed entries —
    the same warning the window shows in its status bar."""
    skipped = lib.skipped_entries()
    if not skipped:
        return
    print("Warning: skipped %s in %s — left in the file, "
          "not deleted"
          % ("1 malformed entry" if len(skipped) == 1 else
             "%d malformed entries" % len(skipped),
             lib.index_path), file=sys.stderr)


def cmd_library_list(args):
    lib = Library()
    entries = lib.search(tag=args.tag)
    _warn_skipped(lib)
    if _is_json(args):
        # The stored entry dicts are already plain JSON — id,
        # name, file, kind, size, source, tags, and the rest.
        print(json.dumps([dict(entry) for entry in entries],
                         indent=2))
        return 0
    if not entries:
        if args.tag:
            _info(args, "No sprites with tag %r in the library (%s)"
                  % (args.tag, lib.directory))
        elif lib.skipped_entries():
            _info(args, "Library has no usable sprites — %s"
                  % lib.directory)
        else:
            _info(args, "Library is empty — %s" % lib.directory)
        return 0
    for entry in entries:
        if entry["kind"] == "sheet":
            what = "%d x %d spritesheet, %d frames" % (
                entry["width"], entry["height"],
                entry.get("frames", 0))
        else:
            what = "%d x %d sprite" % (entry["width"],
                                       entry["height"])
        tags = entry.get("tags", [])
        _info(args, "%s — %s%s%s" % (
            entry["name"], what,
            (" — tags: %s" % ", ".join(tags)) if tags else "",
            " — %s" % entry["file"]))
    return 0


def cmd_library_add(args):
    lib = Library()
    entry = lib.add_file(args.file, args.name,
                         tags=normalize_tags(args.tags),
                         source="file")
    _emit_json(args, dict(entry))
    _info(args, "Saved %s to the library (%s)"
          % (entry["name"], lib.path_for(entry)))
    _warn_skipped(lib)
    return 0


def _resolve_library_entry(lib, ref):
    """Find a library entry by id, else by name. Names are not
    unique, so a name shared by several entries is refused
    with the ids as the way out — the caller names them."""
    entries = lib.entries()
    for entry in entries:
        if entry["id"] == ref:
            return entry
    named = [entry for entry in entries
             if entry["name"] == ref]
    if len(named) == 1:
        return named[0]
    if not named:
        raise PixelForgeError(
            "no sprite named %r in the library" % ref)
    raise PixelForgeError(
        "%d sprites are named %r — use an id instead "
        "(see library list --json for ids)"
        % (len(named), ref))


def cmd_library_remove(args):
    lib = Library()
    entry = _resolve_library_entry(lib, args.name)
    lib.remove(entry["id"])
    _emit_json(args, {"removed": {"id": entry["id"],
                                  "name": entry["name"]}})
    _info(args, "Removed %s from the library" % entry["name"])
    _warn_skipped(lib)
    return 0


def cmd_library_rename(args):
    lib = Library()
    entry = _resolve_library_entry(lib, args.old)
    for other in lib.entries():
        if other["id"] != entry["id"] \
                and other["name"] == args.new:
            raise PixelForgeError(
                "a sprite named %r already exists" % args.new)
    updated = lib.rename(entry["id"], args.new)
    _emit_json(args, dict(updated))
    _info(args, "Renamed %s to %s" % (entry["name"], args.new))
    _warn_skipped(lib)
    return 0


def cmd_library_export(args):
    lib = Library()
    count = lib.export_to(args.dir)
    _emit_json(args, {"exported": count, "dir": str(args.dir)})
    _info(args, "Exported %s to %s" % (plural(count, "sprite"),
                                       args.dir))
    _warn_skipped(lib)
    return 0


def cmd_library_import(args):
    lib = Library()
    report = lib.import_from(args.dir)
    _emit_json(args, report)
    parts = ["added %s" % plural(len(report["added"]), "sprite")]
    if report["skipped"]:
        parts.append("skipped %s already here"
                     % plural(len(report["skipped"]), "sprite"))
    if report["missing"]:
        parts.append("%s had no file"
                     % plural(len(report["missing"]), "sprite"))
    _info(args, "Imported from %s — %s"
          % (args.dir, ", ".join(parts)))
    _warn_skipped(lib)
    return 0


def cmd_presets_list(args):
    store = PresetStore()
    presets = store.all_presets()
    if _is_json(args):
        print(json.dumps(
            [{"name": name, "builtin": builtin,
              "settings": settings}
             for name, settings, builtin in presets], indent=2))
        for name, reason in store.skipped_presets():
            print("pixel-forge: skipped preset %r: %s"
                  % (name, reason), file=sys.stderr)
        return 0
    for name, settings, builtin in presets:
        line = ("%s%s — %d x %d, %s, %d colors, dither %s, "
                "keep aspect %s" % (
                    name, " (built-in)" if builtin else "",
                    settings["width"], settings["height"],
                    settings["palette"], settings["max_colors"],
                    "on" if settings["dither"] else "off",
                    "on" if settings["keep_aspect"] else "off"))
        video = []
        if "extract_fps" in settings:
            video.append("extract %d fps" % settings["extract_fps"])
        if "preview_fps" in settings:
            video.append("preview %d fps" % settings["preview_fps"])
        if "sheet_fps" in settings:
            video.append("sheet %d fps" % settings["sheet_fps"])
        if "sheet_cols" in settings:
            cols = settings["sheet_cols"]
            video.append("sheet columns %s"
                         % ("auto" if cols is None else cols))
        if "sheet_padding" in settings:
            video.append("sheet padding %d px"
                         % settings["sheet_padding"])
        if video:
            line += ", video: " + ", ".join(video)
        _info(args, line)
    # Files that failed to parse are reported, never swallowed:
    # one line each, name + reason, on stderr — the good presets
    # still listed above.
    for name, reason in store.skipped_presets():
        print("pixel-forge: skipped preset %r: %s"
              % (name, reason), file=sys.stderr)
    return 0


def cmd_presets_save(args):
    store = PresetStore()
    if store.get(args.name) is not None \
            or store.get(args.name, builtin=True) is not None:
        if not args.force:
            raise PixelForgeError(
                "a preset named %r already exists "
                "(use --force to replace it)" % args.name)
    width, height = _validate_pixelize_args(args)
    settings = {"width": width, "height": height,
                "keep_aspect": args.keep_aspect,
                "palette": args.palette,
                "max_colors": args.colors,
                "dither": args.dither}
    store.save(args.name, settings)
    _emit_json(args, {"name": args.name, "settings": settings})
    _info(args, "Saved preset %s — %d x %d, %s, %d colors, "
          "dither %s, keep aspect %s"
          % (args.name, width, height, args.palette,
             args.colors,
             "on" if args.dither else "off",
             "on" if args.keep_aspect else "off"))
    return 0


def cmd_presets_delete(args):
    store = PresetStore()
    store.delete(args.name)
    _emit_json(args, {"deleted": args.name})
    _info(args, "Deleted preset %s" % args.name)
    return 0


def cmd_presets_export(args):
    store = PresetStore()
    # A saved preset wins when a name exists in both slots —
    # the user one is the one they would mean.
    if store.get(args.name) is not None:
        builtin = False
    elif store.get(args.name, builtin=True) is not None:
        builtin = True
    else:
        raise PixelForgeError("no preset named %r" % args.name)
    store.export_preset(args.name, args.out, builtin=builtin)
    _emit_json(args, {"exported": args.name, "out": str(args.out)})
    _info(args, "Exported preset %s to %s" % (args.name, args.out))
    return 0


def cmd_presets_import(args):
    store = PresetStore()
    # A name clash refuses plainly — no --force; delete the old
    # preset first. (The window asks Yes/No instead.)
    name = store.import_preset(args.file)
    _emit_json(args, {"imported": name})
    _info(args, "Imported preset %s" % name)
    return 0


def _describe_asset(asset):
    if asset["kind"] == "map":
        return "%s — map, %d x %d, %d colors (%s)" % (
            asset["name"], asset["width"], asset["height"],
            asset["colors"], asset["detail"])
    return "%s — palette, %d colors" % (
        asset["name"], asset["colors"])


def cmd_code(args):
    result = parse_paths([args.file])
    assets = result["assets"]
    # Files that failed to parse are reported, never swallowed:
    # one line each, path + reason, on stderr — the exit code
    # stays 0 because the good files did import.
    for skip in result["skipped"]:
        print("pixel-forge: skipped %s: %s"
              % (skip["file"], skip["reason"]), file=sys.stderr)
    if args.list:
        if _is_json(args):
            # The asset's "image" is a buffer Image, not JSON —
            # the scalar fields are the same ones the human
            # output shows.
            print(json.dumps({
                "assets": [{
                    "name": asset["name"],
                    "kind": asset["kind"],
                    "width": asset["width"],
                    "height": asset["height"],
                    "colors": asset["colors"],
                    "detail": asset["detail"],
                    "source_file": asset["source_file"]}
                    for asset in assets],
                "not_rendered": result["not_rendered"]},
                indent=2))
            return 0
        if not assets:
            _info(args, "No pixel-art assets found in %s "
                  "(parsed %s)"
                  % (args.file,
                     plural(len(result["files"]), "file")))
        for asset in assets:
            _info(args, _describe_asset(asset))
        for note in result["not_rendered"]:
            _info(args, "%s — found but not rendered: %s"
                  % (note["name"], note["reason"]))
        return 0
    if args.all:
        return _cmd_code_all(args, result)
    if args.asset is None:
        raise PixelForgeError(
            "pick an asset with --asset NAME, render them all "
            "with --all, or list them with --list")
    for asset in assets:
        if asset["name"] == args.asset:
            out = args.out
            if out is None:
                out = "%s-sprite.png" % slugify(asset["name"])
            if not str(out).lower().endswith(".png"):
                out = "%s.png" % out
            if not asset["image"].save(str(out), "PNG"):
                raise PixelForgeError("could not write: %s" % out)
            _emit_json(args, {"asset": asset["name"],
                              "width": asset["width"],
                              "height": asset["height"],
                              "colors": asset["colors"],
                              "out": str(out)})
            _info(args, "%s: %d x %d, %d colors — wrote %s"
                  % (asset["name"], asset["width"],
                     asset["height"], asset["colors"], out))
            return 0
    if not assets:
        raise PixelForgeError(
            "no pixel-art assets found in %s (parsed %s) — "
            "nothing to render" % (args.file,
                                   plural(len(result["files"]),
                                          "file")))
    raise PixelForgeError(
        "no asset named %r in %s (found: %s)"
        % (args.asset, args.file,
           ", ".join(a["name"] for a in assets)))


def _cmd_code_all(args, result):
    """Render every asset in the file, one PNG per asset
    (task 28). One bad asset is reported on stderr and
    skipped, the rest still render; exit 1 only when nothing
    rendered at all."""
    assets = result["assets"]
    if not assets:
        raise PixelForgeError(
            "no pixel-art assets found in %s (parsed %s) — "
            "nothing to render" % (args.file,
                                   plural(len(result["files"]),
                                          "file")))
    if args.out is not None:
        out_dir = Path(args.out)
    else:
        out_dir = Path("%s-sprites" % Path(args.file).stem)
    if out_dir.exists() and not out_dir.is_dir():
        raise PixelForgeError(
            "--out must be a folder with --all: %s" % args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered, failed, used = [], [], set()
    for asset in assets:
        name = _unique_stem(slugify(asset["name"]), used)
        out = out_dir / (name + ".png")
        if not asset["image"].save(str(out), "PNG"):
            reason = "could not write: %s" % out
            failed.append({"asset": asset["name"],
                           "reason": reason})
            print("pixel-forge: skipped %s: %s"
                  % (asset["name"], reason), file=sys.stderr)
            continue
        rendered.append(str(out))
        _info(args, "%s: %d x %d, %d colors — wrote %s"
              % (asset["name"], asset["width"],
                 asset["height"], asset["colors"], out))
    if not rendered:
        raise PixelForgeError(
            "could not render anything in %s" % args.file)
    _emit_json(args, {"rendered": rendered, "failed": failed})
    return 0


def cmd_skin(args):
    out = args.out
    if out is None:
        src = Path(args.file)
        out = str(src.with_name(src.stem + "-skin-parts"))
    written = extract_skin(args.file, out)
    parts = [p for p in written if p.name != "figure.png"]
    _emit_json(args, {"parts": [str(p) for p in written],
                      "out": str(out)})
    _info(args, "%s + figure in %s"
          % (plural(len(parts), "part"), out))
    return 0


def cmd_gui(_args):
    from .gui import main as gui_main
    return gui_main()


def main(argv=None):
    # No Qt bootstrap here: the core runs without PySide6, and
    # load_image creates the QCoreApplication lazily, only when
    # it takes the Qt decode path (see pixelize.load_image).
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "convert":
            return cmd_convert(args)
        if args.command == "frames":
            return cmd_frames(args)
        if args.command == "prompt":
            return cmd_prompt(args)
        if args.command == "sheet":
            return cmd_sheet(args)
        if args.command == "library":
            if args.library_command == "list":
                return cmd_library_list(args)
            if args.library_command == "add":
                return cmd_library_add(args)
            if args.library_command == "remove":
                return cmd_library_remove(args)
            if args.library_command == "rename":
                return cmd_library_rename(args)
            if args.library_command == "export":
                return cmd_library_export(args)
            if args.library_command == "import":
                return cmd_library_import(args)
        if args.command == "presets":
            if args.presets_command == "list":
                return cmd_presets_list(args)
            if args.presets_command == "save":
                return cmd_presets_save(args)
            if args.presets_command == "delete":
                return cmd_presets_delete(args)
            if args.presets_command == "export":
                return cmd_presets_export(args)
            if args.presets_command == "import":
                return cmd_presets_import(args)
        if args.command == "code":
            return cmd_code(args)
        if args.command == "skin":
            return cmd_skin(args)
        if args.command == "gui":
            return cmd_gui(args)
    except PixelForgeError as exc:
        print("pixel-forge: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Ctrl+C exits quietly — no message, no traceback.
        return 130
    except Exception as exc:
        # Any other failure prints one plain line, never a
        # traceback: a script caller gets the message on stderr
        # and exit 1. (Some exceptions carry no message — the
        # class name is better than an empty line.)
        message = str(exc) or exc.__class__.__name__
        print("pixel-forge: %s" % message, file=sys.stderr)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
