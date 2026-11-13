"""Pixel Forge command line.

    pixel-forge convert INPUT --size 32 --colors 16 --palette pico8 --out OUT.png
    pixel-forge frames VIDEO --fps 8 --out DIR
    pixel-forge prompt "a knight" --out DIR
    pixel-forge sheet DIR --out sheet.png --meta sheet.json
    pixel-forge library list [--tag TAG]
    pixel-forge library add FILE --name NAME [--tags a,b]
    pixel-forge library export DIR
    pixel-forge library import DIR
    pixel-forge presets list
    pixel-forge code FILE --list
    pixel-forge code FILE --asset NAME --out OUT.png
    pixel-forge skin FILE --out DIR
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pixel-forge",
        description="Turn images, videos, prompts, and code into "
        "pixel art and sprites.")
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
    lib_export = lib_sub.add_parser(
        "export", help="copy the whole library (sprites + "
        "index) to a folder as a backup")
    lib_export.add_argument("dir", help="folder to write the "
                            "backup into")
    lib_import = lib_sub.add_parser(
        "import", help="add sprites from a library backup "
        "folder (entries already in the library are skipped)")
    lib_import.add_argument("dir", help="backup folder to read")

    presets = sub.add_parser(
        "presets", help="manage pixelize presets (built-in and "
        "saved): list, export, import")
    pre_sub = presets.add_subparsers(dest="presets_command",
                                     metavar="command")
    pre_sub.required = True
    pre_sub.add_parser("list", help="list all presets")
    pre_export = pre_sub.add_parser(
        "export", help="export a preset to a JSON file to share")
    pre_export.add_argument("name", help="preset name (built-in "
                            "or saved — a saved preset wins when "
                            "both exist)")
    pre_export.add_argument("--out", required=True,
                            help="JSON file to write")
    pre_import = pre_sub.add_parser(
        "import", help="import a preset from a shared JSON file")
    pre_import.add_argument("file", help="preset JSON file to "
                            "import")

    code = sub.add_parser(
        "code", help="reproduce pixel-art assets from source code "
        "(.py, .js, .ts — parsed statically, never executed)")
    code.add_argument("file", help="code file or folder of code "
                      "files to parse")
    code.add_argument("--list", action="store_true",
                      help="list the assets found, instead of "
                      "rendering one")
    code.add_argument("--asset", default=None,
                      help="name of the asset to render (see "
                      "--list)")
    code.add_argument("--out", default=None,
                      help="output PNG path (default: "
                      "<asset>-sprite.png in the current folder)")

    skin = sub.add_parser(
        "skin", help="extract a Minecraft skin's parts (64x64 or "
        "64x32 PNG) into one PNG per part plus the figure")
    skin.add_argument("file", help="skin PNG to extract")
    skin.add_argument("--out", default=None,
                      help="output folder (default: "
                      "<skin-stem>-skin-parts next to the file)")

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
    first = load_image(written[0])
    print("%d frames, %d x %d, in %s"
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
    print("%s + pixelized versions in %s"
          % (plural(len(landed), "candidate"), bdir))
    if failed:
        print("%s failed — see batch.json in %s"
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
    print("%d frames, %d x %d grid, %d x %d sheet, %s fps — "
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
    if not entries:
        if args.tag:
            print("No sprites with tag %r in the library (%s)"
                  % (args.tag, lib.directory))
        elif lib.skipped_entries():
            print("Library has no usable sprites — %s"
                  % lib.directory)
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
    _warn_skipped(lib)
    return 0


def cmd_library_export(args):
    lib = Library()
    count = lib.export_to(args.dir)
    print("Exported %s to %s" % (plural(count, "sprite"),
                                 args.dir))
    _warn_skipped(lib)
    return 0


def cmd_library_import(args):
    lib = Library()
    report = lib.import_from(args.dir)
    parts = ["added %s" % plural(len(report["added"]), "sprite")]
    if report["skipped"]:
        parts.append("skipped %s already here"
                     % plural(len(report["skipped"]), "sprite"))
    if report["missing"]:
        parts.append("%s had no file"
                     % plural(len(report["missing"]), "sprite"))
    print("Imported from %s — %s" % (args.dir, ", ".join(parts)))
    _warn_skipped(lib)
    return 0


def cmd_presets_list(_args):
    store = PresetStore()
    for name, settings, builtin in store.all_presets():
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
        print(line)
    # Files that failed to parse are reported, never swallowed:
    # one line each, name + reason, on stderr — the good presets
    # still listed above.
    for name, reason in store.skipped_presets():
        print("pixel-forge: skipped preset %r: %s"
              % (name, reason), file=sys.stderr)
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
    print("Exported preset %s to %s" % (args.name, args.out))
    return 0


def cmd_presets_import(args):
    store = PresetStore()
    # A name clash refuses plainly — no --force; delete the old
    # preset first. (The window asks Yes/No instead.)
    name = store.import_preset(args.file)
    print("Imported preset %s" % name)
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
        if not assets:
            print("No pixel-art assets found in %s "
                  "(parsed %s)"
                  % (args.file,
                     plural(len(result["files"]), "file")))
        for asset in assets:
            print(_describe_asset(asset))
        for note in result["not_rendered"]:
            print("%s — found but not rendered: %s"
                  % (note["name"], note["reason"]))
        return 0
    if args.asset is None:
        raise PixelForgeError(
            "pick an asset with --asset NAME, or list them with "
            "--list")
    for asset in assets:
        if asset["name"] == args.asset:
            out = args.out
            if out is None:
                out = "%s-sprite.png" % slugify(asset["name"])
            if not str(out).lower().endswith(".png"):
                out = "%s.png" % out
            if not asset["image"].save(str(out), "PNG"):
                raise PixelForgeError("could not write: %s" % out)
            print("%s: %d x %d, %d colors — wrote %s"
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


def cmd_skin(args):
    out = args.out
    if out is None:
        src = Path(args.file)
        out = str(src.with_name(src.stem + "-skin-parts"))
    written = extract_skin(args.file, out)
    parts = [p for p in written if p.name != "figure.png"]
    print("%s + figure in %s"
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
            if args.library_command == "export":
                return cmd_library_export(args)
            if args.library_command == "import":
                return cmd_library_import(args)
        if args.command == "presets":
            if args.presets_command == "list":
                return cmd_presets_list(args)
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
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
