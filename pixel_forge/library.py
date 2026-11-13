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

Entries are validated when the index loads: an entry missing a
field the window or the CLI reads (or with a field of the wrong
type) is skipped, not fatal — the app starts with the good
entries, the skipped ones are reported (see skipped_entries),
and they stay in library.json verbatim, so a later save never
deletes data the load could not use. A whole index file that is
not parseable JSON still fails loudly instead.

Nothing is created on disk until the first save — opening the
window or listing an empty library never touches the home folder.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .pixelize import PixelForgeError, load_image

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


def validate_entry(entry):
    """Why an index entry is unusable, or None when it is fine.
    Every field the window and the CLI read with [] is required
    (id, name, file, kind, width, height, source, created);
    optional fields are checked for type when present. A bad
    entry is skipped at load, never fatal — see Library._load."""
    if not isinstance(entry, dict):
        return "not an object"
    for key in ("id", "name", "file"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            return "missing %r" % key
    if entry.get("kind") not in KINDS:
        return "missing or unknown kind %r" % (entry.get("kind"),)
    for key in ("width", "height"):
        value = entry.get(key)
        if isinstance(value, bool) or not isinstance(value, int) \
                or value < 1:
            return "%r must be a whole number of 1 or more" % key
    if entry.get("source") not in SOURCES:
        return "missing or unknown source %r" % (entry.get("source"),)
    created = entry.get("created")
    if not isinstance(created, str) or not created.strip():
        return "missing 'created'"
    tags = entry.get("tags")
    if tags is not None and (not isinstance(tags, list) or not all(
            isinstance(tag, str) for tag in tags)):
        return "'tags' must be a list of names"
    if entry.get("palette") is not None and \
            not isinstance(entry["palette"], str):
        return "'palette' must be a name"
    frames = entry.get("frames")
    if frames is not None and (isinstance(frames, bool)
                               or not isinstance(frames, int)
                               or frames < 0):
        return "'frames' must be a whole number of 0 or more"
    if entry.get("meta") is not None and \
            not isinstance(entry["meta"], str):
        return "'meta' must be a file name"
    return None


class Library:
    """One library folder. The index loads lazily and is written
    back (atomically, via a temp file + rename) on every change."""

    def __init__(self, directory=None):
        self.directory = (Path(directory) if directory is not None
                          else default_library_dir())
        self._entries = None
        # The index exactly as loaded (valid + skipped, in order),
        # so a save can keep skipped entries verbatim; and one
        # reason string per skipped entry, in index order.
        self._raw_entries = None
        self._skipped = None

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
            self._raw_entries = []
            self._skipped = []
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
        # A bad entry is skipped, not fatal — one malformed entry
        # used to brick startup with an uncaught KeyError. The
        # skipped entries stay in the file (see _save); their
        # reasons are reported through skipped_entries().
        self._raw_entries = entries
        self._entries = []
        self._skipped = []
        for index, entry in enumerate(entries):
            reason = validate_entry(entry)
            if reason is None:
                self._entries.append(entry)
            else:
                self._skipped.append(
                    "entry %d: %s" % (index + 1, reason))

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        # Skipped entries stay in the file, verbatim and in their
        # original positions — a save never deletes data the load
        # could not use. Valid entries keep their positions too
        # (renames included); new entries go at the end.
        by_id = {entry["id"]: entry for entry in self._entries}
        seen = set()
        out = []
        for raw in self._raw_entries or []:
            if validate_entry(raw) is not None:
                out.append(raw)
                continue
            current = by_id.get(raw.get("id"))
            if current is not None:
                out.append(current)
                seen.add(current["id"])
        for entry in self._entries:
            if entry["id"] not in seen:
                out.append(entry)
        payload = {"format": LIBRARY_FORMAT,
                   "version": LIBRARY_VERSION,
                   "entries": out}
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, self.index_path)
        self._raw_entries = out
        self._skipped = [
            "entry %d: %s" % (index + 1, validate_entry(entry))
            for index, entry in enumerate(out)
            if validate_entry(entry) is not None]

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

    def skipped_entries(self):
        """Why each skipped index entry was skipped, in index
        order ("entry 3: missing 'name'"). Skipped entries stay
        in library.json — they are never deleted or rewritten."""
        self._load()
        return list(self._skipped)

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
        """Save an Image as a sprite entry. Returns the entry."""
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
        image = load_image(src)
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
        image = load_image(png)
        self._load()
        stem = self._unique_stem(name)
        png_name = stem + ".png"
        meta_name = stem + ".json"
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(png, self.directory / png_name)
            self._copy_sheet_meta(meta, self.directory / meta_name,
                                  png_name)
        except OSError as exc:
            raise PixelForgeError("could not copy sheet: %s" % exc)
        return self._record(name, tags, source, "sheet", png_name,
                            image.width(), image.height(),
                            frames=frames, meta=meta_name)

    def _copy_sheet_meta(self, src, dest, png_name):
        """Copy a sheet's metadata JSON into the library, with its
        "sheet" field rewritten to the PNG's name inside the
        library. The original names the SOURCE png — a file that
        does not exist in the library folder (and the GUI's temp
        sheet is always sheet.png, so the GUI path was always
        stale). The original file is never touched. A meta that
        is not a JSON object is copied verbatim, as before — it
        has no "sheet" field to go stale. The rewrite is written
        atomically (temp file + rename), like write_sheet."""
        try:
            with open(src) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            shutil.copyfile(src, dest)
            return
        if not isinstance(data, dict):
            shutil.copyfile(src, dest)
            return
        data["sheet"] = png_name
        tmp = dest.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            os.replace(tmp, dest)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

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
