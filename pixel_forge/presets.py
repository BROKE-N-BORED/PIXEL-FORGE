"""Presets — named bundles of pixelize settings, saved as JSON.

A preset is size + palette + max colors + dither + keep-aspect —
everything the Image, Video, and Prompt tabs share — so one pick
sets a whole look at once instead of five controls one by one.
MIMIK-style: behavior programmed as a reusable preset, no AI
involved.

Built-ins ship in code and are read-only. User presets live in
presets.json inside the library folder, so they travel with the
library. A user preset may share a built-in's name — it gets its
own slot and never overwrites the built-in; the two are told apart
by the builtin flag, and the window shows the user one as
"Name (custom)".

A preset may also carry video fields — extract_fps, preview_fps,
sheet_fps, sheet_cols, sheet_padding — which only the Video tab
uses; the other tabs ignore them, and a preset without them
leaves the Video tab's controls alone. All five are optional and
validated like the rest: a bad video field skips the preset with
a reason, the same fail-soft rule as any other bad field.

A preset can be shared as one JSON file:

    {"format": "pixel-forge-preset", "version": 1,
     "name": "My Knight Look",
     "settings": {"width": 32, ...}}

write_preset_export writes one, read_preset_export reads and
validates one. Importing a name that matches a built-in shadows
it as a user preset; importing a name that already exists as a
user preset needs overwrite=True.

    ~/pixel-forge-library/presets.json
    {
      "format": "pixel-forge-presets",
      "version": 1,
      "presets": {
        "My Knight Look": {"width": 32, "height": 32,
          "keep_aspect": false, "palette": "pico8",
          "max_colors": 16, "dither": true}
      }
    }
"""

import json
import os
from pathlib import Path

from .library import default_library_dir
from .pixelize import MAX_COLORS, MIN_COLORS, PixelForgeError
from .video import FPS_PRESETS

PRESETS_FORMAT = "pixel-forge-presets"
PRESETS_VERSION = 1
PRESETS_NAME = "presets.json"
MAX_PRESET_SIZE = 512

# One preset shared as a file — the format marker + version the
# export file carries, so an import can tell a preset file from
# any other JSON.
PRESET_EXPORT_FORMAT = "pixel-forge-preset"
PRESET_EXPORT_VERSION = 1

# The video fields' ranges, matching the Video tab's controls:
# preview/sheet fps are spin boxes (1..30), extract fps is the
# extraction combo (FPS_PRESETS), sheet columns is the columns
# combo (Auto, or one of these), padding is a spin box (0..32).
VIDEO_FPS_MIN = 1
VIDEO_FPS_MAX = 30
SHEET_COLS_VALUES = (2, 3, 4, 5, 6, 8, 10, 12, 16)
SHEET_PADDING_MAX = 32
VIDEO_FIELDS = ("extract_fps", "preview_fps", "sheet_fps",
                "sheet_cols", "sheet_padding")

BUILTIN_PRESETS = {
    "Game Boy 32": {"width": 32, "height": 32, "keep_aspect": False,
                    "palette": "gameboy", "max_colors": 4,
                    "dither": False},
    "PICO-8 64": {"width": 64, "height": 64, "keep_aspect": False,
                  "palette": "pico8", "max_colors": 16,
                  "dither": True},
    "Detailed 64": {"width": 64, "height": 64, "keep_aspect": False,
                    "palette": "image", "max_colors": 16,
                    "dither": True},
    "Tiny 16": {"width": 16, "height": 16, "keep_aspect": False,
                "palette": "pico8", "max_colors": 16,
                "dither": True},
    "Large 128": {"width": 128, "height": 128, "keep_aspect": False,
                  "palette": "image", "max_colors": 16,
                  "dither": True},
    "Tall 32x64": {"width": 32, "height": 64, "keep_aspect": False,
                   "palette": "pico8", "max_colors": 16,
                   "dither": True},
    "Wide 64x32": {"width": 64, "height": 32, "keep_aspect": False,
                   "palette": "pico8", "max_colors": 16,
                   "dither": True},
    "Game Boy 64": {"width": 64, "height": 64, "keep_aspect": False,
                    "palette": "gameboy", "max_colors": 4,
                    "dither": False},
}


def _is_whole(value):
    """A whole number — a bool is not one (True is int 1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_video_field(field, value):
    """One optional video field, checked against its control's
    range. Raises PixelForgeError naming the field and the
    range, so a bad video field skips the preset like any
    other bad field."""
    if field == "extract_fps":
        if not _is_whole(value) or value not in FPS_PRESETS:
            raise PixelForgeError(
                "preset extract fps must be one of: %s"
                % ", ".join(str(fps) for fps in FPS_PRESETS))
    elif field in ("preview_fps", "sheet_fps"):
        if not _is_whole(value) or value < VIDEO_FPS_MIN \
                or value > VIDEO_FPS_MAX:
            raise PixelForgeError(
                "preset %s must be a whole number from %d to %d"
                % (field.replace("_", " "), VIDEO_FPS_MIN,
                   VIDEO_FPS_MAX))
    elif field == "sheet_cols":
        if value is not None:
            if not _is_whole(value) or value not in SHEET_COLS_VALUES:
                raise PixelForgeError(
                    "preset sheet columns must be automatic or "
                    "one of: %s"
                    % ", ".join(str(c) for c in SHEET_COLS_VALUES))
    elif field == "sheet_padding":
        if not _is_whole(value) or value < 0 \
                or value > SHEET_PADDING_MAX:
            raise PixelForgeError(
                "preset sheet padding must be a whole number "
                "from 0 to %d" % SHEET_PADDING_MAX)


def validate_settings(settings):
    """Check a settings dict and return a clean copy with exactly
    the six known keys, plus any of the five optional video
    fields that were present and valid. Raises PixelForgeError
    on anything out of range, so a hand-edited presets.json
    fails loudly instead of silently applying nonsense."""
    if not isinstance(settings, dict):
        raise PixelForgeError("preset settings must be an object")
    for key in ("width", "height", "keep_aspect", "palette",
                "max_colors", "dither"):
        if key not in settings:
            raise PixelForgeError(
                "preset is missing %r" % key)
    width, height = settings["width"], settings["height"]
    if not isinstance(width, int) or not isinstance(height, int) \
            or width < 1 or height < 1 \
            or width > MAX_PRESET_SIZE or height > MAX_PRESET_SIZE:
        raise PixelForgeError(
            "preset size must be whole numbers from 1 to %d"
            % MAX_PRESET_SIZE)
    if not isinstance(settings["palette"], str) \
            or not settings["palette"].strip():
        raise PixelForgeError(
            "preset palette must be a palette name")
    # Any non-empty string is a valid palette name here — a
    # preset may name a user palette (task 39), and this module
    # does not know which user palettes exist. Existence is
    # checked where it is used: the CLI validates at save time,
    # and the window falls back to "From image" with a note when
    # a preset names a palette that is gone.
    colors = settings["max_colors"]
    if not isinstance(colors, int) or colors < MIN_COLORS \
            or colors > MAX_COLORS:
        raise PixelForgeError(
            "preset colors must be between %d and %d"
            % (MIN_COLORS, MAX_COLORS))
    if not isinstance(settings["dither"], bool) or \
            not isinstance(settings["keep_aspect"], bool):
        raise PixelForgeError(
            "preset dither and keep_aspect must be true or false")
    clean = {"width": width, "height": height,
             "keep_aspect": settings["keep_aspect"],
             "palette": settings["palette"],
             "max_colors": colors,
             "dither": settings["dither"]}
    # The video fields are optional: a preset without one leaves
    # that Video tab control alone. A preset WITH a bad one is
    # skipped with a reason, like any other bad preset.
    for field in VIDEO_FIELDS:
        if field in settings:
            _validate_video_field(field, settings[field])
            clean[field] = settings[field]
    return clean


def preset_export_payload(name, settings):
    """The shareable file's contents as a dict — format marker,
    name, and validated settings."""
    if not str(name).strip():
        raise PixelForgeError("give the preset a name")
    return {"format": PRESET_EXPORT_FORMAT,
            "version": PRESET_EXPORT_VERSION,
            "name": str(name).strip(),
            "settings": validate_settings(settings)}


def write_preset_export(path, name, settings):
    """Write one preset to a shareable JSON file, atomically
    (temp file + replace, so a crash mid-write never leaves a
    half-written file behind)."""
    payload = preset_export_payload(name, settings)
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def read_preset_export(path):
    """Read a shared preset file back — (name, settings).
    Raises PixelForgeError naming the problem on anything that
    is not a valid preset file, and never touches the store."""
    try:
        data = json.loads(Path(path).read_text())
    except OSError as exc:
        raise PixelForgeError(
            "cannot read preset file: %s (%s)" % (path, exc))
    except ValueError as exc:
        raise PixelForgeError(
            "preset file is not valid JSON: %s (%s)" % (path, exc))
    if not isinstance(data, dict) or \
            data.get("format") != PRESET_EXPORT_FORMAT:
        raise PixelForgeError(
            "not a Pixel Forge preset file: %s" % path)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PixelForgeError(
            "preset file has no name: %s" % path)
    try:
        settings = validate_settings(data.get("settings"))
    except PixelForgeError as exc:
        raise PixelForgeError(
            "preset file %r has bad settings: %s" % (path, exc))
    return name.strip(), settings


class PresetStore:
    """Built-ins (in code) + user presets (presets.json in the
    library folder)."""

    def __init__(self, directory=None):
        self.directory = (Path(directory) if directory is not None
                          else default_library_dir())
        self._user = None
        # The presets dict exactly as loaded (valid + skipped, in
        # file order), so a save can keep skipped entries verbatim;
        # and one (name, reason) pair per skipped preset, in file
        # order. Same shape as the library's skipped entries.
        self._raw_presets = None
        self._skipped = None

    @property
    def presets_path(self):
        return self.directory / PRESETS_NAME

    def _load(self):
        if self._user is not None:
            return
        path = self.presets_path
        if not path.exists():
            self._user = {}
            self._raw_presets = {}
            self._skipped = []
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise PixelForgeError(
                "presets file is unreadable: %s (%s)" % (path, exc))
        if not isinstance(data, dict) or \
                data.get("format") != PRESETS_FORMAT:
            raise PixelForgeError(
                "not a Pixel Forge presets file: %s" % path)
        stored = data.get("presets", {})
        if not isinstance(stored, dict):
            raise PixelForgeError(
                "presets file is malformed: %s" % path)
        # A bad preset is skipped, not fatal — one invalid entry
        # used to drop every user preset. The skipped entries
        # stay in the file (see _save); their reasons are
        # reported through skipped_presets().
        self._raw_presets = stored
        self._user = {}
        self._skipped = []
        for name, settings in stored.items():
            try:
                self._user[name] = validate_settings(settings)
            except PixelForgeError as exc:
                self._skipped.append((name, str(exc)))

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.presets_path.with_suffix(".json.tmp")
        # Skipped entries stay in the file, verbatim and in their
        # original positions — a save never deletes data the load
        # could not use. A name that is now valid (the user saved
        # over a skipped preset) takes the current settings at
        # the skipped entry's position; a deleted name drops out;
        # brand-new presets go at the end.
        out = {}
        for name, raw in (self._raw_presets or {}).items():
            if name in self._user:
                out[name] = self._user[name]
                continue
            try:
                validate_settings(raw)
            except PixelForgeError:
                out[name] = raw
        for name, settings in self._user.items():
            if name not in out:
                out[name] = settings
        payload = {"format": PRESETS_FORMAT,
                   "version": PRESETS_VERSION,
                   "presets": out}
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, self.presets_path)
        self._raw_presets = out
        self._skipped = []
        for name, settings in out.items():
            try:
                validate_settings(settings)
            except PixelForgeError as exc:
                self._skipped.append((name, str(exc)))

    def skipped_presets(self):
        """Why each skipped user preset was skipped, in file
        order — a list of (name, reason) pairs, e.g. ("Broken",
        "preset size must be whole numbers from 1 to 512").
        Skipped presets stay in presets.json — they are never
        deleted or rewritten."""
        self._load()
        return list(self._skipped)

    def user_presets(self):
        """Name -> settings for user presets only (copies)."""
        self._load()
        return {name: dict(settings)
                for name, settings in self._user.items()}

    def all_presets(self):
        """Every preset as (name, settings, builtin) — built-ins
        first in their shipped order, then user presets sorted
        by name. A user preset sharing a built-in's name appears
        twice, once per slot."""
        self._load()
        found = [(name, dict(settings), True)
                 for name, settings in BUILTIN_PRESETS.items()]
        found.extend((name, dict(settings), False)
                     for name, settings in
                     sorted(self._user.items(), key=lambda kv: kv[0]))
        return found

    def get(self, name, builtin=False):
        """One preset's settings, or None. builtin picks the slot
        when a name exists in both."""
        self._load()
        if builtin:
            settings = BUILTIN_PRESETS.get(name)
        else:
            settings = self._user.get(name)
        return dict(settings) if settings is not None else None

    def save(self, name, settings):
        """Save (or overwrite) a user preset. A name that matches
        a built-in saves into the user slot — the built-in is
        never touched. Returns the stored settings."""
        if not str(name).strip():
            raise PixelForgeError("give the preset a name")
        clean = validate_settings(settings)
        self._load()
        self._user[str(name).strip()] = clean
        self._save()
        return dict(clean)

    def delete(self, name):
        """Delete a user preset. Built-ins cannot be deleted —
        deleting a built-in's name only works when a user preset
        with that name exists, and removes the user one."""
        self._load()
        if name not in self._user:
            if name in BUILTIN_PRESETS:
                raise PixelForgeError(
                    "built-in preset %r cannot be deleted" % name)
            raise PixelForgeError("no preset named %r" % name)
        del self._user[name]
        self._save()

    def export_preset(self, name, path, builtin=False):
        """Write one preset (built-in or user) to a shareable
        JSON file. Raises PixelForgeError when no preset with
        that name exists in the chosen slot."""
        settings = self.get(name, builtin=builtin)
        if settings is None:
            raise PixelForgeError("no preset named %r" % name)
        write_preset_export(path, name, settings)

    def import_preset(self, path, overwrite=False):
        """Read a shared preset file into the user slot and
        return its name. A name matching a built-in shadows it
        as "(custom)" — the built-in is never touched. A name
        that already exists as a user preset refuses plainly
        unless overwrite is True; the file is validated before
        anything is checked, so a bad file never touches the
        store either way."""
        name, settings = read_preset_export(path)
        self._load()
        if name in self._user and not overwrite:
            raise PixelForgeError(
                "a preset named %r already exists — delete it "
                "first" % name)
        self.save(name, settings)
        return name
