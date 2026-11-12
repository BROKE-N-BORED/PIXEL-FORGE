"""Sprite library — saved sprites on disk, plain files, no database.

The library lives in ~/pixel-forge-library/ (PIXELFORGE_LIBRARY
overrides it, same pattern as the engine/model env vars) — a
visible folder he can browse in Nemo, same pattern as
~/pixel-forge-models/. Layout:

    ~/pixel-forge-library/
      library.json     the index (format, version, entries)
      presets.json     user presets (see presets.py)
      knight.png       one sprite entry
      walk-sheet.png   one spritesheet entry (the PNG half)
      walk-sheet.json  the spritesheet's metadata (the JSON half)

An index entry records the display name, the file, the kind
("sprite" or "sheet"), tags, where it came from ("image", "video",
"prompt", "code", "skin", or "file" for a CLI add), size, palette
(sprites), and frame count + metadata file (sheets), plus the date
it was saved.
Files are named from the entry name (slugified, with -2, -3, ...
on collision); renaming an entry renames the display name only,
so the file on disk stays put.

Nothing is created on disk until the first save — opening the
window or listing an empty library never touches the home folder.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtGui import QImage

from .pixelize import PixelForgeError

LIBRARY_ENV = "PIXELFORGE_LIBRARY"
LIBRARY_FORMAT = "pixel-forge-library"
LIBRARY_VERSION = 1
INDEX_NAME = "library.json"

KINDS = ("sprite", "sheet")
SOURCES = ("image", "video", "prompt", "code", "skin", "file")


def default_library_dir():
    """Where the library lives: PIXELFORGE_LIBRARY, or
    ~/pixel-forge-library/."""
    override = os.environ.get(LIBRARY_ENV)
    if override:
        return Path(override)
    return Path.home() / "pixel-forge-library"


def slugify(text):
    """A filename-safe slug from a display name ("Red Knight" ->
    "red-knight"). Same shape as imagegen.slugify, kept local so
    the library does not depend on the image-gen module."""
    words = [w.lower() for w in text.replace("_", " ").split()
             if any(c.isalnum() for c in w)]
    cleaned = ["".join(c for c in w if c.isalnum()) for w in words]
    return "-".join(w for w in cleaned if w)[:40] or "sprite"


def normalize_tags(tags):
    """Tags as a clean list: split on commas when given a string,
    stripped, empties dropped, duplicates dropped in order."""
    if isinstance(tags, str):
        tags = tags.split(",")
    clean = []
    for tag in tags:
        tag = str(tag).strip()
        if tag and tag not in clean:
            clean.append(tag)
    return clean


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Library:
    """One library folder. The index loads lazily and is written
    back (atomically, via a temp file + rename) on every change."""

    def __init__(self, directory=None):
        self.directory = (Path(directory) if directory is not None
                          else default_library_dir())
        self._entries = None

    @property
    def index_path(self):
        return self.directory / INDEX_NAME

    # ------------------------------------------------------------ index

    def _load(self):
        if self._entries is not None:
            return
        path = self.index_path
        if not path.exists():
            self._entries = []
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise PixelForgeError(
                "library index is unreadable: %s (%s)" % (path, exc))
        if not isinstance(data, dict) or \
                data.get("format") != LIBRARY_FORMAT:
            raise PixelForgeError(
                "not a Pixel Forge library: %s" % path)
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            raise PixelForgeError(
                "library index is malformed: %s" % path)
        self._entries = entries

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        payload = {"format": LIBRARY_FORMAT,
                   "version": LIBRARY_VERSION,
                   "entries": self._entries}
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, self.index_path)

    def _unique_stem(self, name):
        """A file stem no entry (and no stray file) already uses."""
        self._load()
        taken = {Path(e["file"]).stem for e in self._entries}
        base = slugify(name)
        stem = base
        number = 2
        while stem in taken or \
                (self.directory / (stem + ".png")).exists():
            stem = "%s-%d" % (base, number)
            number += 1
        return stem

    # ------------------------------------------------------------ reads

    def entries(self):
        """All entries, in saved order (copies — edit via rename)."""
        self._load()
        return [dict(entry) for entry in self._entries]

    def find(self, entry_id):
        """One entry by id, or None."""
        self._load()
        for entry in self._entries:
            if entry["id"] == entry_id:
                return dict(entry)
        return None

    def search(self, text=None, tag=None):
        """Entries matching a text search (name or tags, substring,
        case-insensitive) and/or an exact tag. Both filters apply
        together; no filters returns everything."""
        self._load()
        needle = text.strip().lower() if text else None
        want_tag = tag.strip().lower() if tag else None
        found = []
        for entry in self._entries:
            if want_tag is not None and want_tag not in \
                    [t.lower() for t in entry.get("tags", [])]:
                continue
            if needle is not None:
                hay = [entry["name"].lower()] + \
                    [t.lower() for t in entry.get("tags", [])]
                if not any(needle in h for h in hay):
                    continue
            found.append(dict(entry))
        return found

    def all_tags(self):
        """Every tag in use, sorted, case-insensitively."""
        self._load()
        tags = set()
        for entry in self._entries:
            tags.update(entry.get("tags", []))
        return sorted(tags, key=str.lower)

    def path_for(self, entry):
        """The PNG on disk for an entry."""
        return self.directory / entry["file"]

    # ----------------------------------------------------------- writes

    def _record(self, name, tags, source, kind, filename, width,
                height, palette=None, frames=None, meta=None):
        if not str(name).strip():
            raise PixelForgeError("give the sprite a name")
        if source not in SOURCES:
            raise PixelForgeError("unknown source: %r" % source)
        entry = {"id": Path(filename).stem,
                 "name": str(name).strip(),
                 "file": filename,
                 "kind": kind,
                 "tags": normalize_tags(tags),
                 "source": source,
                 "width": width,
                 "height": height,
                 "created": _stamp()}
        if palette is not None:
            entry["palette"] = palette
        if frames is not None:
            entry["frames"] = frames
        if meta is not None:
            entry["meta"] = meta
        self._entries.append(entry)
        self._save()
        return dict(entry)

    def add_image(self, image, name, tags=(), source="image",
                  palette=None):
        """Save a QImage as a sprite entry. Returns the entry."""
        if image is None or image.isNull():
            raise PixelForgeError("nothing to save to the library")
        self._load()
        stem = self._unique_stem(name)
        filename = stem + ".png"
        self.directory.mkdir(parents=True, exist_ok=True)
        if not image.save(str(self.directory / filename), "PNG"):
            raise PixelForgeError(
                "could not write: %s" % filename)
        return self._record(name, tags, source, "sprite", filename,
                            image.width(), image.height(),
                            palette=palette)

    def add_file(self, path, name, tags=(), source="file"):
        """Add an existing image file (PNG, JPG, ...). It is
        re-saved as a PNG in the library. Returns the entry."""
        src = Path(path)
        if not src.is_file():
            raise PixelForgeError("no such file: %s" % path)
        image = QImage(str(src))
        if image.isNull():
            raise PixelForgeError("not a readable image: %s" % path)
        return self.add_image(image, name, tags, source=source)

    def add_sheet(self, png_path, meta_path, name, tags=(),
                  source="video", frames=0):
        """Add a spritesheet PNG + metadata JSON pair as one
        entry. Both files are copied into the library. Returns
        the entry."""
        png = Path(png_path)
        meta = Path(meta_path)
        if not png.is_file():
            raise PixelForgeError("no such file: %s" % png_path)
        if not meta.is_file():
            raise PixelForgeError(
                "spritesheet metadata missing: %s" % meta_path)
        image = QImage(str(png))
        if image.isNull():
            raise PixelForgeError("not a readable image: %s" % png)
        self._load()
        stem = self._unique_stem(name)
        png_name = stem + ".png"
        meta_name = stem + ".json"
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(png, self.directory / png_name)
            shutil.copyfile(meta, self.directory / meta_name)
        except OSError as exc:
            raise PixelForgeError("could not copy sheet: %s" % exc)
        return self._record(name, tags, source, "sheet", png_name,
                            image.width(), image.height(),
                            frames=frames, meta=meta_name)

    def rename(self, entry_id, new_name):
        """Change an entry's display name (the file stays put).
        Returns the updated entry."""
        if not str(new_name).strip():
            raise PixelForgeError("give the sprite a name")
        self._load()
        for entry in self._entries:
            if entry["id"] == entry_id:
                entry["name"] = str(new_name).strip()
                self._save()
                return dict(entry)
        raise PixelForgeError(
            "no library entry with id %r" % entry_id)

    def remove(self, entry_id):
        """Delete an entry and its files (the sheet JSON too).
        Missing files are tolerated — the index entry is what
        counts."""
        self._load()
        for index, entry in enumerate(self._entries):
            if entry["id"] != entry_id:
                continue
            for key in ("file", "meta"):
                if entry.get(key):
                    try:
                        (self.directory / entry[key]).unlink()
                    except OSError:
                        pass
            del self._entries[index]
            self._save()
            return
        raise PixelForgeError(
            "no library entry with id %r" % entry_id)
