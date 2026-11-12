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
from .palettes import PALETTE_LABELS
from .pixelize import MAX_COLORS, MIN_COLORS, PixelForgeError

PRESETS_FORMAT = "pixel-forge-presets"
PRESETS_VERSION = 1
PRESETS_NAME = "presets.json"
MAX_PRESET_SIZE = 512

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
}


def validate_settings(settings):
    """Check a settings dict and return a clean copy with exactly
    the six known keys. Raises PixelForgeError on anything out
    of range, so a hand-edited presets.json fails loudly instead
    of silently applying nonsense."""
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
    if settings["palette"] not in PALETTE_LABELS:
        raise PixelForgeError(
            "preset palette must be one of: %s"
            % ", ".join(sorted(PALETTE_LABELS)))
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
    return {"width": width, "height": height,
            "keep_aspect": settings["keep_aspect"],
            "palette": settings["palette"],
            "max_colors": colors,
            "dither": settings["dither"]}


class PresetStore:
    """Built-ins (in code) + user presets (presets.json in the
    library folder)."""

    def __init__(self, directory=None):
        self.directory = (Path(directory) if directory is not None
                          else default_library_dir())
        self._user = None

    @property
    def presets_path(self):
        return self.directory / PRESETS_NAME

    def _load(self):
        if self._user is not None:
            return
        path = self.presets_path
        if not path.exists():
            self._user = {}
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
        clean = {}
        for name, settings in stored.items():
            try:
                clean[name] = validate_settings(settings)
            except PixelForgeError as exc:
                raise PixelForgeError(
                    "preset %r is invalid: %s" % (name, exc))
        self._user = clean

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.presets_path.with_suffix(".json.tmp")
        payload = {"format": PRESETS_FORMAT,
                   "version": PRESETS_VERSION,
                   "presets": self._user}
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, self.presets_path)

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
