"""Pixel Forge command line.

    pixel-forge convert INPUT --size 32 --colors 16 --palette pico8 --out OUT.png
    pixel-forge frames VIDEO --fps 8 --out DIR
    pixel-forge prompt "a knight" --out DIR
    pixel-forge sheet DIR --out sheet.png --meta sheet.json
    pixel-forge library list [--tag TAG]
    pixel-forge library add FILE --name NAME [--tags a,b]
    pixel-forge presets list
    pixel-forge gui
    pixel-forge --version

--size takes a square size (32) or WIDTHxHEIGHT (64x48). With
--keep-aspect, --size is the box the image is fitted into. convert's
--out defaults to <input-stem>-pixel.png next to the input; frames'
--out defaults to <video-stem>-frames next to the video; prompt's
--out defaults to <prompt-slug>-prompt in the current folder.
frames needs ffmpeg installed (sudo apt install ffmpeg). prompt
needs the image engine and model (see pixel-forge prompt --help).
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .imagegen import (DEFAULT_NEGATIVE, prompt_to_sprites, slugify)
from .library import Library, normalize_tags
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError,
                       pixelize_file)
from .presets import PresetStore
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pixel-forge",
        description="Turn images, videos, and prompts into pixel art "
        "and sprites.")
    parser.add_argument("--version", action="version",
                        version="pixel-forge %s" % __version__)
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    convert = sub.add_parser(
        "convert", help="convert one image into a pixel sprite")
    convert.add_argument("input", help="image to convert (PNG, JPG, "
                         "BMP, WebP, GIF, and more)")
    _add_pixelize_args(convert)
    convert.add_argument("--out", default=None,
                         help="output PNG path (default: "
                         "<input-stem>-pixel.png next to the input)")

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
    _add_pixelize_args(prompt)
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

    library = sub.add_parser(
        "library", help="manage the saved sprite library "
        "(~/pixel-forge-library/, or PIXELFORGE_LIBRARY)")
    lib_sub = library.add_subparsers(dest="library_command",
                                     metavar="command")
    lib_sub.required = True
    lib_list = lib_sub.add_parser(
        "list", help="list saved sprites")
    lib_list.add_argument("--tag", default=None,
                          help="only show sprites with this tag")
    lib_add = lib_sub.add_parser(
        "add", help="add an image file to the library")
    lib_add.add_argument("file", help="image file to add")
    lib_add.add_argument("--name", required=True,
                         help="name for the sprite in the library")
    lib_add.add_argument("--tags", default="",
                         help="comma-separated tags")

    presets = sub.add_parser(
        "presets", help="list pixelize presets (built-in and saved)")
    pre_sub = presets.add_subparsers(dest="presets_command",
                                     metavar="command")
    pre_sub.required = True
    pre_sub.add_parser("list", help="list all presets")

    sub.add_parser("gui", help="open the Pixel Forge window")
    return parser


def cmd_convert(args):
    width, height = _validate_pixelize_args(args)
    out = args.out
    if out is None:
        src = Path(args.input)
        out = str(src.with_name(src.stem + "-pixel.png"))
    width_out, height_out, colors = pixelize_file(
        args.input, out, width=width, height=height,
        keep_aspect=args.keep_aspect, palette=args.palette,
        max_colors=args.colors, dither=args.dither)
    print("%s: %d x %d, %d colors" % (out, width_out, height_out, colors))
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
    from PySide6.QtGui import QImage
    first = QImage(str(written[0]))
    print("%d frames, %d x %d, in %s"
          % (len(written), first.width(), first.height(), out))
    return 0


def cmd_prompt(args):
    width, height = _validate_pixelize_args(args)
    if args.count < 1:
        raise PixelForgeError("count must be at least 1")
    out = args.out
    if out is None:
        out = slugify(args.prompt) + "-prompt"

    def _progress(done, total, record):
        status = record["status"]
        line = "candidate %d of %d: %s (%s)" % (
            done, total, record["file"], status)
        if status == "failed" and record.get("error"):
            line += " — %s" % record["error"]
        print(line)

    batch, bdir = prompt_to_sprites(
        args.prompt, out, negative=args.negative, count=args.count,
        width=width, height=height, keep_aspect=args.keep_aspect,
        palette=args.palette, max_colors=args.colors,
        dither=args.dither, progress=_progress)
    landed = [c for c in batch["candidates"]
              if c["status"] == "pending"]
    failed = len(batch["candidates"]) - len(landed)
    print("%d candidate(s) + pixelized versions in %s"
          % (len(landed), bdir))
    if failed:
        print("%d candidate(s) failed — see batch.json in %s"
              % (failed, bdir))
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
    print("%d frames, %d x %d grid, %d x %d sheet, %s fps — "
          "wrote %s and %s"
          % (len(frames), metadata["columns"], metadata["rows"],
             metadata["sheet_width"], metadata["sheet_height"],
             args.fps, out, meta))
    return 0


def cmd_library_list(args):
    lib = Library()
    entries = lib.search(tag=args.tag)
    if not entries:
        if args.tag:
            print("No sprites with tag %r in the library (%s)"
                  % (args.tag, lib.directory))
        else:
            print("Library is empty — %s" % lib.directory)
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
        print("%s — %s%s%s" % (
            entry["name"], what,
            (" — tags: %s" % ", ".join(tags)) if tags else "",
            " — %s" % entry["file"]))
    return 0


def cmd_library_add(args):
    lib = Library()
    entry = lib.add_file(args.file, args.name,
                         tags=normalize_tags(args.tags),
                         source="file")
    print("Saved %s to the library (%s)"
          % (entry["name"], lib.path_for(entry)))
    return 0


def cmd_presets_list(_args):
    store = PresetStore()
    for name, settings, builtin in store.all_presets():
        print("%s%s — %d x %d, %s, %d colors, dither %s, "
              "keep aspect %s" % (
                  name, " (built-in)" if builtin else "",
                  settings["width"], settings["height"],
                  settings["palette"], settings["max_colors"],
                  "on" if settings["dither"] else "off",
                  "on" if settings["keep_aspect"] else "off"))
    return 0


def cmd_gui(_args):
    from .gui import main as gui_main
    return gui_main()


def main(argv=None):
    # QImage needs a QCoreApplication instance so Qt finds its image
    # format plugins (notably JPEG) even in a headless process.
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv if argv is None else
                               ["pixel-forge"] + list(argv or []))
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
        if args.command == "presets":
            if args.presets_command == "list":
                return cmd_presets_list(args)
        if args.command == "gui":
            return cmd_gui(args)
    except PixelForgeError as exc:
        print("pixel-forge: %s" % exc, file=sys.stderr)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
