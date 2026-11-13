"""Palette files + user palettes (task 39).

Lospec-style palette files come in five formats, all parsed here
(stdlib only):

    .hex  one hex color per line, with or without a leading '#',
          CRLF or LF, blank lines skipped
    .gpl  GIMP palette — "GIMP Palette" header, Name:/Columns:
          lines, '#' comments, then "R G B [name]" lines
    .pal  JASC-PAL — "JASC-PAL" + version + count lines, then
          "R G B" lines
    .ase  Adobe Swatch Exchange, binary — "ASEF" signature,
          group blocks and color entries with a UTF-16BE name
          and a model ("RGB " + 3 big-endian floats 0..1);
          CMYK/LAB/GRAY entries are ignored
    .txt  Lospec text — one RRGGBB per line, optionally followed
          by a name after a space or comma

Every parser refuses plainly on a malformed file — naming the
file and what was wrong — instead of silently dropping colors.
A palette with 0 colors refuses, and one with more than 256
(the pixelizer's max) refuses naming the count. Duplicate colors
are kept as-is: the file is the authority, dedupe is the editor's
job, not the importer's.

User palettes follow PresetStore's shape: built-ins live in code
(palettes.NAMED_PALETTES) and user palettes live in palettes.json
inside the library folder, so they travel with the library. A
user palette is {name, colors: ["#rrggbb", ...]}. Names are unique
case-insensitively across built-ins and user palettes: saving
over an existing user name replaces it, saving over a built-in
name (key or label) refuses plainly. One bad entry in palettes.json
is skipped with a warning naming it — the v0.21 rule — and a
wholly unparseable file still raises.

    ~/pixel-forge-library/palettes.json
    {
      "format": "pixel-forge-palettes",
      "version": 1,
      "palettes": {
        "My Sunset": {"colors": ["#2e222f", "#3e3546"]}
      }
    }
"""

import json
import os
import struct
from pathlib import Path

from .library import default_library_dir
from .palettes import NAMED_PALETTES, PALETTE_LABELS, median_cut
from .pixelize import MAX_COLORS, PixelForgeError, opaque_pixels

PALETTES_FORMAT = "pixel-forge-palettes"
PALETTES_VERSION = 1
PALETTES_NAME = "palettes.json"

IMPORT_EXTENSIONS = (".hex", ".gpl", ".pal", ".ase", ".txt")
EXPORT_EXTENSIONS = (".hex", ".gpl")


def _hex_to_rgb(text):
    """A 6-digit hex string (no '#') as an (r, g, b) tuple, or
    None when it is not one."""
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16),
                int(text[4:6], 16))
    except ValueError:
        return None


def _rgb_to_hex(rgb):
    """An (r, g, b) tuple as "#rrggbb"."""
    return "#%02x%02x%02x" % rgb


def _check_colors(colors, source):
    """The shared size rule for imported palettes: at least one
    color, at most the pixelizer's max. Raises PixelForgeError
    naming the file and the count."""
    if not colors:
        raise PixelForgeError(
            "palette is empty — no colors found in %s" % source)
    if len(colors) > MAX_COLORS:
        raise PixelForgeError(
            "palette has %d colors, more than the %d the pixelizer "
            "allows: %s" % (len(colors), MAX_COLORS, source))


def _parse_hex_line(line, source, lineno):
    """One .hex/.txt color token as (r, g, b) — with or without
    a leading '#'. Raises PixelForgeError naming the line."""
    rgb = _hex_to_rgb(line.lstrip("#"))
    if rgb is None:
        raise PixelForgeError(
            "%s line %d is not a color: %r" % (source, lineno, line))
    return rgb


def parse_hex(text, source):
    """Lospec .hex — one hex color per line. Returns
    (None, colors): the format carries no name, so the caller
    uses the file stem."""
    colors = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        colors.append(_parse_hex_line(line, source, lineno))
    return None, colors


def parse_txt(text, source):
    """Lospec .txt — one RRGGBB per line, optionally followed by
    a name after a space or comma. The per-color name is not
    kept: a palette is bare colors, names live on the palette."""
    colors = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        # The color is the first token; the rest is the name.
        token = line.replace(",", " ", 1).split(None, 1)[0]
        colors.append(_parse_hex_line(token, source, lineno))
    return None, colors


def _parse_rgb_triplet(parts, source, lineno):
    """Three decimal ints 0..255 from a split line, or a
    PixelForgeError naming the line."""
    if len(parts) < 3:
        raise PixelForgeError(
            "%s line %d is not a color: %r"
            % (source, lineno, " ".join(parts)))
    try:
        rgb = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        raise PixelForgeError(
            "%s line %d is not a color: %r"
            % (source, lineno, " ".join(parts)))
    if not all(0 <= c <= 255 for c in rgb):
        raise PixelForgeError(
            "%s line %d has a channel outside 0..255: %r"
            % (source, lineno, " ".join(parts)))
    return rgb


def parse_gpl(text, source):
    """GIMP .gpl — header lines, then "R G B [name]" lines.
    Returns (name, colors); the name comes from the "Name:"
    header line when present, else None."""
    lines = text.splitlines()
    # The first non-blank line must be the GIMP header — without
    # it, this is not a GIMP palette.
    first = next((line.strip() for line in lines if line.strip()),
                 None)
    if first != "GIMP Palette":
        raise PixelForgeError(
            "%s is not a GIMP palette (no 'GIMP Palette' header)"
            % source)
    name = None
    colors = []
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "GIMP Palette":
            continue
        if line.startswith("Name:"):
            name = line[len("Name:"):].strip() or None
            continue
        if line.startswith("Columns:"):
            continue
        colors.append(_parse_rgb_triplet(
            line.split(), source, lineno))
    return name, colors


def parse_pal(text, source):
    """JASC-PAL — "JASC-PAL" + version + count lines, then
    "R G B" lines. Returns (None, colors): the format carries
    no name."""
    lines = [line.strip() for line in text.splitlines()
             if line.strip()]
    if not lines or lines[0] != "JASC-PAL":
        raise PixelForgeError(
            "%s is not a JASC palette (no 'JASC-PAL' header)"
            % source)
    if len(lines) < 3:
        raise PixelForgeError(
            "%s is a truncated JASC palette (no version or count "
            "line)" % source)
    try:
        count = int(lines[2])
    except ValueError:
        raise PixelForgeError(
            "%s line 3 is not a color count: %r" % (source, lines[2]))
    if count < 0:
        raise PixelForgeError(
            "%s has a negative color count: %d" % (source, count))
    data = lines[3:]
    if len(data) != count:
        raise PixelForgeError(
            "%s says it has %d colors but has %d color lines"
            % (source, count, len(data)))
    colors = []
    for i, line in enumerate(data):
        # The line number in the original file: the three header
        # lines plus the lines before this one. Blank lines were
        # stripped above, so this is the data-line number, which
        # is what a user counts when they look at the file.
        colors.append(_parse_rgb_triplet(
            line.split(), source, i + 4))
    return None, colors


def _read_ase_string(data, offset, source):
    """A UTF-16BE ASE string at offset: a uint16 length (in code
    units, including the NUL terminator) followed by the bytes.
    Returns (text, next offset)."""
    if offset + 2 > len(data):
        raise PixelForgeError("%s is truncated" % source)
    (length,) = struct.unpack_from(">H", data, offset)
    offset += 2
    end = offset + length * 2
    if end > len(data):
        raise PixelForgeError("%s is truncated" % source)
    raw = data[offset:end]
    offset = end
    try:
        text = raw.decode("utf-16-be").rstrip("\x00")
    except UnicodeDecodeError:
        raise PixelForgeError(
            "%s has a name that is not valid UTF-16" % source)
    return text, offset


def parse_ase(data, source):
    """Adobe Swatch Exchange (.ase), binary. Returns
    (name, colors): the name is the first group name when the
    file has one, else None. Non-RGB entries are skipped; a
    file with no RGB entries refuses plainly."""
    if len(data) < 12 or data[:4] != b"ASEF":
        raise PixelForgeError(
            "%s is not an Adobe Swatch Exchange file (no 'ASEF' "
            "signature)" % source)
    (_major, _minor, block_count) = struct.unpack_from(">HHI", data, 4)
    offset = 12
    name = None
    colors = []
    skipped = 0
    for _ in range(block_count):
        if offset + 6 > len(data):
            raise PixelForgeError("%s is truncated" % source)
        block_type, length = struct.unpack_from(">HI", data, offset)
        offset += 6
        end = offset + length
        if end > len(data):
            raise PixelForgeError("%s is truncated" % source)
        if block_type == 0xC001:  # group start
            group_name, _ = _read_ase_string(data, offset, source)
            if name is None and group_name:
                name = group_name
        elif block_type == 0xC002:  # group end — no payload
            pass
        elif block_type == 0x0001:  # color entry
            _color_name, pos = _read_ase_string(data, offset, source)
            if pos + 4 > end:
                raise PixelForgeError("%s is truncated" % source)
            model = data[pos:pos + 4]
            pos += 4
            if model == b"RGB ":
                if pos + 12 > end:
                    raise PixelForgeError("%s is truncated" % source)
                floats = struct.unpack_from(">fff", data, pos)
                rgb = []
                for value in floats:
                    # Clamp out-of-range floats (and NaN) instead
                    # of failing the whole file on one bad entry.
                    if value != value or value < 0.0:
                        value = 0.0
                    elif value > 1.0:
                        value = 1.0
                    rgb.append(int(round(value * 255)))
                colors.append(tuple(rgb))
            else:
                skipped += 1
        # Unknown block types are skipped by length — a newer
        # writer's blocks must not break an older reader.
        offset = end
    if not colors:
        if skipped:
            raise PixelForgeError(
                "%s has no RGB colors (%d non-RGB colors skipped)"
                % (source, skipped))
        raise PixelForgeError(
            "%s has no RGB colors" % source)
    return name, colors


# Text parsers take (text, source); the binary parser takes
# (bytes, source). read_palette_file dispatches on that.
_TEXT_PARSERS = {
    ".hex": parse_hex,
    ".txt": parse_txt,
    ".gpl": parse_gpl,
    ".pal": parse_pal,
}


def read_palette_file(path):
    """Parse a palette file into (name, colors). The name comes
    from the file when the format carries one (.gpl Name:,
    .ase group name), else from the file stem. Raises
    PixelForgeError naming the file and the problem on anything
    malformed, empty, or over 256 colors."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in IMPORT_EXTENSIONS:
        raise PixelForgeError(
            "unknown palette format: %s (use .hex, .gpl, .pal, "
            ".ase, or .txt)" % suffix)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PixelForgeError(
            "cannot read palette file: %s (%s)" % (path, exc))
    if suffix == ".ase":
        name, colors = parse_ase(raw, str(path))
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PixelForgeError(
                "%s is not valid text: %s" % (path, exc))
        name, colors = _TEXT_PARSERS[suffix](text, str(path))
    _check_colors(colors, str(path))
    if not name:
        name = path.stem
    return name, colors


def write_hex(path, name, colors):
    """Write a Lospec .hex file — one bare RRGGBB per line.
    Atomically (temp file + fsync + replace). The name is not
    stored: the format carries no name."""
    _check_colors(colors, str(path))
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = "".join("%02x%02x%02x\n" % rgb for rgb in colors)
    with open(tmp, "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_gpl(path, name, colors):
    """Write a GIMP .gpl file — header + "R G B" lines.
    Atomically (temp file + fsync + replace)."""
    _check_colors(colors, str(path))
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    lines = ["GIMP Palette",
             "Name: %s" % name,
             "Columns: 8",
             "#"]
    lines.extend("%d %d %d" % rgb for rgb in colors)
    with open(tmp, "w") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_palette_file(path, name, colors):
    """Write a palette to .hex or .gpl by extension. Raises
    PixelForgeError on any other extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".hex":
        write_hex(path, name, colors)
    elif suffix == ".gpl":
        write_gpl(path, name, colors)
    else:
        raise PixelForgeError(
            "cannot export a palette as %s (use .hex or .gpl)"
            % suffix)


def extract_palette_from_image(image, max_colors):
    """The median-cut palette of an image's opaque pixels — the
    same code "From image" uses (resolve_palette), with the
    palette itself returned instead of a pixelized image. A
    fully transparent image gives one black entry, the same
    fallback resolve_palette uses."""
    if max_colors < 1:
        raise PixelForgeError(
            "colors must be at least 1 (got %r)" % (max_colors,))
    if max_colors > MAX_COLORS:
        raise PixelForgeError(
            "colors must be at most %d (got %r)"
            % (MAX_COLORS, max_colors))
    colors = opaque_pixels(image)
    if not colors:
        return [(0, 0, 0)]
    return median_cut(colors, max_colors)


def validate_palette_name(name):
    """A palette name, stripped — raises PixelForgeError on a
    blank one."""
    if not isinstance(name, str) or not name.strip():
        raise PixelForgeError("give the palette a name")
    return name.strip()


def validate_palette_colors(colors):
    """A color list as clean (r, g, b) tuples — raises
    PixelForgeError on anything out of shape, empty, or over
    the pixelizer's max. Accepts "#rrggbb" strings (the JSON
    form) as well as tuples."""
    if not isinstance(colors, (list, tuple)):
        raise PixelForgeError("palette colors must be a list")
    clean = []
    for entry in colors:
        if isinstance(entry, str):
            rgb = _hex_to_rgb(entry.lstrip("#"))
            if rgb is None:
                raise PixelForgeError(
                    "palette color %r is not a hex color" % (entry,))
            clean.append(rgb)
            continue
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise PixelForgeError(
                "palette color %r is not an (r, g, b) triple"
                % (entry,))
        if not all(isinstance(c, int) and not isinstance(c, bool)
                   and 0 <= c <= 255 for c in entry):
            raise PixelForgeError(
                "palette color %r has a channel outside 0..255"
                % (entry,))
        clean.append(tuple(entry))
    _check_colors(clean, "palette")
    return clean


def _builtin_name_taken(name):
    """Whether a user palette name collides (case-insensitively)
    with a built-in palette's key or label, or with "image" /
    "From image" — the name a combo would confuse it with."""
    folded = name.casefold()
    taken = [key.casefold() for key in NAMED_PALETTES]
    taken.extend(label.casefold() for label in PALETTE_LABELS.values())
    taken.append("image")
    return folded in taken


class PaletteStore:
    """Built-ins (in code) + user palettes (palettes.json in the
    library folder)."""

    def __init__(self, directory=None):
        self.directory = (Path(directory) if directory is not None
                          else default_library_dir())
        self._user = None
        # The palettes dict exactly as loaded (valid + skipped,
        # in file order), so a save can keep skipped entries
        # verbatim; and one (name, reason) pair per skipped
        # palette, in file order. Same shape as PresetStore.
        self._raw_palettes = None
        self._skipped = None

    @property
    def palettes_path(self):
        return self.directory / PALETTES_NAME

    def _load(self):
        if self._user is not None:
            return
        path = self.palettes_path
        if not path.exists():
            self._user = {}
            self._raw_palettes = {}
            self._skipped = []
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise PixelForgeError(
                "palettes file is unreadable: %s (%s)" % (path, exc))
        if not isinstance(data, dict) or \
                data.get("format") != PALETTES_FORMAT:
            raise PixelForgeError(
                "not a Pixel Forge palettes file: %s" % path)
        stored = data.get("palettes", {})
        if not isinstance(stored, dict):
            raise PixelForgeError(
                "palettes file is malformed: %s" % path)
        # A bad palette is skipped, not fatal — one invalid entry
        # used to drop every user preset (v0.21), and the same
        # rule applies here. The skipped entries stay in the file
        # (see _save); their reasons are reported through
        # skipped_palettes(). An entry whose name matches a
        # built-in is skipped too: the combo could not tell the
        # two apart.
        self._raw_palettes = stored
        self._user = {}
        self._skipped = []
        for name, entry in stored.items():
            try:
                if _builtin_name_taken(name):
                    raise PixelForgeError(
                        "name matches a built-in palette")
                if not isinstance(entry, dict) or "colors" not in entry:
                    raise PixelForgeError(
                        "palette entry has no colors")
                self._user[name] = validate_palette_colors(
                    entry["colors"])
            except PixelForgeError as exc:
                self._skipped.append((name, str(exc)))

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.palettes_path.with_suffix(".json.tmp")
        # Skipped entries stay in the file, verbatim and in their
        # original positions — a save never deletes data the load
        # could not use. A name that is now valid (the user saved
        # over a skipped palette) takes the current colors at the
        # skipped entry's position; a deleted name drops out;
        # brand-new palettes go at the end.
        out = {}
        for name, raw in (self._raw_palettes or {}).items():
            if name in self._user:
                out[name] = {"colors": [_rgb_to_hex(rgb) for rgb in
                                        self._user[name]]}
                continue
            try:
                if _builtin_name_taken(name):
                    raise PixelForgeError("name matches a built-in")
                if not isinstance(raw, dict) or "colors" not in raw:
                    raise PixelForgeError("no colors")
                validate_palette_colors(raw["colors"])
            except PixelForgeError:
                out[name] = raw
        for name, colors in self._user.items():
            if name not in out:
                out[name] = {"colors": [_rgb_to_hex(rgb) for rgb in
                                        colors]}
        payload = {"format": PALETTES_FORMAT,
                   "version": PALETTES_VERSION,
                   "palettes": out}
        with open(tmp, "w") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.palettes_path)
        self._raw_palettes = out
        self._skipped = []
        for name, entry in out.items():
            try:
                if _builtin_name_taken(name):
                    raise PixelForgeError(
                        "name matches a built-in palette")
                if not isinstance(entry, dict) or "colors" not in entry:
                    raise PixelForgeError("palette entry has no colors")
                validate_palette_colors(entry["colors"])
            except PixelForgeError as exc:
                self._skipped.append((name, str(exc)))

    def skipped_palettes(self):
        """Why each skipped user palette was skipped, in file
        order — a list of (name, reason) pairs. Skipped palettes
        stay in palettes.json — they are never deleted or
        rewritten."""
        self._load()
        return list(self._skipped)

    def user_palettes(self):
        """Name -> [(r, g, b)] for user palettes only (copies)."""
        self._load()
        return {name: list(colors)
                for name, colors in self._user.items()}

    def all_palettes(self):
        """Every palette as (name, colors, builtin) — built-ins
        first in their shipped order, then user palettes sorted
        by name. A built-in's name is its key ("pico8"); the
        display label lives in PALETTE_LABELS."""
        self._load()
        found = [(key, list(colors), True)
                 for key, colors in NAMED_PALETTES.items()]
        found.extend((name, list(colors), False)
                     for name, colors in
                     sorted(self._user.items(), key=lambda kv: kv[0]))
        return found

    def get(self, name, builtin=False):
        """One palette's colors, or None. builtin picks the
        built-in slot (name is the key); otherwise the user slot
        (exact name)."""
        self._load()
        if builtin:
            colors = NAMED_PALETTES.get(name)
        else:
            colors = self._user.get(name)
        return list(colors) if colors is not None else None

    def find(self, name):
        """A user palette's stored name — exact match first,
        then case-insensitive — or None. The CLI uses this so
        "sunset" finds a palette saved as "Sunset"."""
        self._load()
        if name in self._user:
            return name
        folded = name.casefold()
        for stored in self._user:
            if stored.casefold() == folded:
                return stored
        return None

    def lookup(self, name):
        """(colors, builtin) for any palette by name: a built-in
        key first, then a user palette (case-insensitive via
        find). Returns None when nothing matches. "image" is not
        a palette and returns None."""
        self._load()
        if name in NAMED_PALETTES:
            return list(NAMED_PALETTES[name]), True
        stored = self.find(name)
        if stored is not None:
            return list(self._user[stored]), False
        return None

    def save(self, name, colors):
        """Save (or replace) a user palette. A name matching a
        built-in key or label refuses plainly; a name matching
        an existing user palette case-insensitively replaces it
        under the new spelling. Returns the stored colors."""
        name = validate_palette_name(name)
        clean = validate_palette_colors(colors)
        if _builtin_name_taken(name):
            raise PixelForgeError(
                "%r is a built-in palette name — pick another name"
                % name)
        self._load()
        # Case-insensitive uniqueness: replace the old entry
        # under its old spelling, keeping the new spelling.
        for stored in list(self._user):
            if stored.casefold() == name.casefold() \
                    and stored != name:
                del self._user[stored]
        self._user[name] = clean
        self._save()
        return list(clean)

    def rename(self, old, new):
        """Rename a user palette in one save — the old name
        drops out and the new one takes its colors. A built-in
        name as the new name refuses, as does a name another
        user palette already has."""
        new = validate_palette_name(new)
        if _builtin_name_taken(new):
            raise PixelForgeError(
                "%r is a built-in palette name — pick another name"
                % new)
        self._load()
        if old not in self._user:
            if old in NAMED_PALETTES:
                raise PixelForgeError(
                    "built-in palette %r cannot be renamed" % old)
            raise PixelForgeError("no palette named %r" % old)
        if new != old:
            for stored in self._user:
                if stored != old \
                        and stored.casefold() == new.casefold():
                    raise PixelForgeError(
                        "a palette named %r already exists" % new)
        colors = self._user.pop(old)
        self._user[new] = colors
        self._save()
        return list(colors)

    def delete(self, name):
        """Delete a user palette. Built-ins cannot be deleted."""
        self._load()
        if name not in self._user:
            if name in NAMED_PALETTES:
                raise PixelForgeError(
                    "built-in palette %r cannot be deleted" % name)
            raise PixelForgeError("no palette named %r" % name)
        del self._user[name]
        self._save()

    def import_file(self, path, name=None, overwrite=False):
        """Read a palette file into the user slot and return its
        name. The name comes from --name, the file (when the
        format carries one), or the file stem. A name that
        already exists as a user palette refuses plainly unless
        overwrite is True; a built-in name refuses through save.
        The file is validated before anything is checked, so a
        bad file never touches the store either way."""
        file_name, colors = read_palette_file(path)
        name = (validate_palette_name(name) if name is not None
                else file_name)
        self._load()
        existing = self.find(name)
        if existing is not None and not overwrite:
            raise PixelForgeError(
                "a palette named %r already exists — delete it "
                "first" % existing)
        self.save(name, colors)
        return name

    def export_palette(self, name, path, builtin=False):
        """Write one palette (built-in or user) to a .hex or
        .gpl file by extension. Raises PixelForgeError when no
        palette with that name exists in the chosen slot."""
        colors = self.get(name, builtin=builtin)
        if colors is None:
            raise PixelForgeError("no palette named %r" % name)
        label = PALETTE_LABELS.get(name, name) if builtin else name
        write_palette_file(path, label, colors)
