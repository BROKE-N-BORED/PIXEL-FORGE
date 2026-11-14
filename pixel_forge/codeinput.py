"""Code input — reproduce pixel-art assets FROM source code, statically.

Feed Pixel Forge a .py / .js / .ts / .json / .cs / .gd / .lua file
(or a folder of them) and it rebuilds the pixel art the code
defines — pixel maps, palettes — as real sprites. The code is
NEVER executed, evaled, execed, or imported: Python files are read
with ast.parse and only literal values are taken (ast.literal_eval),
and the other languages are scanned with small literal parsers (no
JS/C#/Lua engine is available here). A file that defines its art by
running code (loops, function calls, draw calls) cannot be rendered
this way — those assets land in the "found but not rendered" list
with a plain reason, never faked.

What is detected (all of it literal data only):

  Pixel maps — a variable holding rows of pixels:
    * int rows: [[0,1,1,0],[1,0,0,1],...] — 0 is transparent, 1 is
      the first color of a palette from the same file when one
      covers the values used (an int-keyed dict, or a color list
      long enough), else the built-in default (PICO-8 colors in
      order, 1 is the first).
    * hex rows: [["#ff0000", None],["#00ff00", "#0000ff"]] — None,
      "" and "transparent" are transparent cells.
    * RGB rows: [[(255,0,0),(0,255,0)],...] — (r, g, b) or
      (r, g, b, a) tuples.
    * char rows: ["....",".XX.",".XX.","...."] — each string is one
      row; a dict in the same file must map every character to a
      color ("." and " " are always transparent). Without a legend
      that covers every character, the map is not rendered.
  Palettes — a variable holding colors: a flat list of hex strings
  / RGB tuples / named colors ("red", "gold", ...), or a dict
  mapping names, ints, or characters to colors. Rendered as a
  strip, one block per color.
  Single colors — one color value (a Color constructor in C# /
  GDScript, an RGB tuple, a Love2D color table) renders as a
  swatch.
  Image references — a string naming an image file (.png, .jpg,
  .gif, .webp, .bmp), on its own or inside a loader call
  (Resources.Load, load/preload, love.graphics.newImage), is
  resolved against the code file's folder and shown as an asset
  when the file is there. A missing file is a plain note, never
  a crash; an absolute path is never followed silently.
  Rects — one rectangle (a Rect / Rect2 / newQuad, or a dict with
  exactly x, y, w, h) is reported as a frame region, not pixels.
  Draw-call specs — lists of dicts with rectangle keys (x, y, w,
  h, color/fill) are recognized and reported as not rendered:
  they describe drawing steps, not pixels.
  JSON sprites — a .json file: an Aseprite export (frames + meta
  with its sheet image — each frame is cropped from the sheet),
  a frame list with an "image" sheet, a "pixels" / "data" / "grid"
  row list (optionally with a "palette"), a flat "pixels" list
  with "width" and "height", a "palette" color list, a top-level
  list of rows or colors, or named sprites (each top-level key a
  sprite). Invalid JSON is a plain refusal, never a traceback.

What is NOT detected: anything computed (function calls, loops,
arithmetic, comprehensions, f-strings with expressions), sprites
drawn by a game engine's API, and Three.js / canvas / WebGL
geometry. Python detection looks at assignments anywhere in the
file (module level, inside functions and classes); the other
languages are textual scans, so they find assignments anywhere
too, but comment stripping is approximate — a regex literal
containing "//" may confuse the JS one, and a C# verbatim string
may confuse the C# one. Maps are capped at 512 x 512; rows must
all be the same length. Code files over 5 MB are refused before
parsing, with the size named.
"""

import ast
import json
import re
from pathlib import Path

from .image import Image, argb
from .palettes import PICO8
from .pixelize import (PixelForgeError, count_opaque_colors,
                       load_image, plural)

CODE_EXTENSIONS = {".py", ".js", ".ts", ".json", ".cs", ".gd", ".lua"}
CODE_EXTENSIONS_TEXT = ".py, .js, .ts, .json, .cs, .gd, or .lua"
MAX_MAP_WIDTH = 512
MAX_MAP_HEIGHT = 512
PALETTE_BLOCK = 8  # pixels per color in a rendered palette strip
# A code file over this size is refused before parsing: 5 MB is
# well above the largest renderable map written out as text (a
# 512 x 512 map of hex strings is under 3 MB) and far below a
# file big enough to freeze the parse.
MAX_CODE_FILE_BYTES = 5 * 1024 * 1024
# Image extensions followed from code references (see
# _resolve_image_reference).
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# How far up the folder tree a Godot res:// reference is searched:
# res:// means the project root, which is above the script's
# folder by an unknown number of levels.
RES_UP_LEVELS = 6

# A few common named colors, so ["red", "gold"] works as a palette.
# Values are the standard CSS ones.
NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 128, 0), "lime": (0, 255, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255),
    "orange": (255, 165, 0), "purple": (128, 0, 128),
    "pink": (255, 192, 203), "brown": (165, 42, 42),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "gold": (255, 215, 0),
    "navy": (0, 0, 128), "teal": (0, 128, 128),
    "olive": (128, 128, 0), "maroon": (128, 0, 0),
    "skyblue": (135, 206, 235), "crimson": (220, 20, 60),
    "violet": (238, 130, 238), "turquoise": (64, 224, 208),
    "coral": (255, 127, 80), "khaki": (240, 230, 140),
    "indigo": (75, 0, 130),
}

TRANSPARENT_WORDS = {"", "transparent", "none", "clear"}

# Name tokens that suggest a variable holds sprite data. Used only
# for the "found but not rendered" list — a computed value with a
# sprite-ish name is worth reporting; other computed values are
# silently ignored.
SPRITE_TOKENS = {
    "sprite", "sprites", "pixel", "pixels", "tile", "tiles",
    "bitmap", "bitmaps", "map", "maps", "art", "frame", "frames",
    "anim", "glyph", "glyphs", "icon", "icons", "pattern",
    "patterns", "char", "chars", "image", "images",
}

DRAW_KEYS = {"x", "y", "w", "h", "width", "height", "color",
             "fill", "rect"}


def _split_name(name):
    """Split a variable name into lowercase tokens (snake_case and
    camelCase both)."""
    parts = re.split(r"[^A-Za-z0-9]+", name)
    tokens = []
    for part in parts:
        for token in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+"
                                r"|[0-9]+", part):
            tokens.append(token.lower())
    return tokens


def looks_like_asset_name(name):
    """True when a variable name suggests it holds sprite data."""
    return any(t in SPRITE_TOKENS for t in _split_name(name))


# ------------------------------------------------------------- colors

def _format_size(size):
    """A byte count as a short human size: "12 KB", "5 MB"."""
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / 1024 / 1024)
    if size >= 1024:
        return "%d KB" % round(size / 1024)
    return "%d bytes" % size


def parse_color(value):
    """A color value to an (r, g, b, a) tuple, or None when the
    value is not a color. Accepts "#rrggbb", "#rgb", "#rrggbbaa",
    "rrggbb", "0xRRGGBB", named colors, and (r, g, b) / (r, g, b, a)
    tuples or lists of ints. Ints are read as 0xRRGGBB up to
    0xFFFFFF and as 0xAARRGGBB (alpha first, the C#/Java
    convention) from 0x1000000 to 0xFFFFFFFF — but only in
    palette position (see interpret); in a map, ints are palette
    indices, never colors. An 8-digit hex STRING stays RRGGBBAA
    (the CSS convention for "#rrggbbaa"); only int literals use
    the alpha-first order. None is transparent."""
    if value is None:
        return (0, 0, 0, 0)
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in TRANSPARENT_WORDS:
            return (0, 0, 0, 0)
        if text.lower() in NAMED_COLORS:
            r, g, b = NAMED_COLORS[text.lower()]
            return (r, g, b, 255)
        hexpart = text
        if hexpart.startswith("#"):
            hexpart = hexpart[1:]
        elif hexpart.lower().startswith("0x"):
            hexpart = hexpart[2:]
        if re.fullmatch(r"[0-9a-fA-F]{3}", hexpart):
            r = int(hexpart[0] * 2, 16)
            g = int(hexpart[1] * 2, 16)
            b = int(hexpart[2] * 2, 16)
            return (r, g, b, 255)
        if re.fullmatch(r"[0-9a-fA-F]{6}", hexpart):
            return (int(hexpart[0:2], 16), int(hexpart[2:4], 16),
                    int(hexpart[4:6], 16), 255)
        if re.fullmatch(r"[0-9a-fA-F]{8}", hexpart):
            return (int(hexpart[0:2], 16), int(hexpart[2:4], 16),
                    int(hexpart[4:6], 16), int(hexpart[6:8], 16))
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return ((value >> 16) & 0xFF, (value >> 8) & 0xFF,
                    value & 0xFF, 255)
        if 0x1000000 <= value <= 0xFFFFFFFF:
            return ((value >> 16) & 0xFF, (value >> 8) & 0xFF,
                    value & 0xFF, (value >> 24) & 0xFF)
        return None
    if isinstance(value, (tuple, list)) and len(value) in (3, 4):
        if all(isinstance(c, int) and not isinstance(c, bool) and
               0 <= c <= 255 for c in value):
            if len(value) == 3:
                return (value[0], value[1], value[2], 255)
            return tuple(value)
    return None


def is_transparent_cell(value):
    """True for the values that mean "no pixel here" in a map."""
    if value is None:
        return True
    if isinstance(value, str) and \
            value.strip().lower() in TRANSPARENT_WORDS:
        return True
    return False


# ----------------------------------------------------------- rendering

def render_color_grid(grid):
    """A grid of (r, g, b, a) tuples / None into an Image — one
    pixel per cell, exact."""
    height = len(grid)
    width = len(grid[0]) if height else 0
    image = Image(width, height)
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            if color is None:
                continue
            r, g, b, a = color
            image.setPixel(x, y, argb(r, g, b, a))
    return image


def render_palette_strip(colors):
    """A palette as a strip image — one 8x8 block per color."""
    image = Image(len(colors) * PALETTE_BLOCK, PALETTE_BLOCK)
    for index, (r, g, b, a) in enumerate(colors):
        for y in range(PALETTE_BLOCK):
            for x in range(PALETTE_BLOCK):
                image.setPixel(index * PALETTE_BLOCK + x, y,
                               argb(r, g, b, a))
    return image


def _distinct_colors(grid):
    seen = set()
    for row in grid:
        for color in row:
            if color is not None and color[3] > 0:
                seen.add(color)
    return len(seen)


# -------------------------------------------------------- interpreting

def classify_palette(value):
    """A literal value as a color list, or None.

    A flat list of colors, or a dict whose values are all colors
    (keys may be names, ints, or characters). Dicts keep insertion
    order; a char-keyed dict is a legend AND a palette."""
    if isinstance(value, list) and value and \
            not any(isinstance(item, (list, tuple, dict))
                    for item in value):
        colors = []
        for item in value:
            color = parse_color(item)
            if color is None:
                return None
            colors.append(color)
        return colors
    if isinstance(value, dict) and value:
        colors = []
        for key, item in value.items():
            if not isinstance(key, (str, int)):
                return None
            color = parse_color(item)
            if color is None:
                return None
            colors.append(color)
        return colors
    return None


def _is_draw_spec(value):
    """True for a list of dicts describing rectangles to draw —
    drawing steps, not pixels."""
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict) or not item:
            return False
        keys = {str(k).lower() for k in item}
        if len(keys & DRAW_KEYS) < 2:
            return False
    return True


def _check_rows(value):
    """A nested list as (rows, width, height), or a reason string
    when it is not a renderable grid."""
    if not isinstance(value, list) or not value:
        return None, "not a list of rows"
    if not all(isinstance(row, (list, tuple, str)) for row in value):
        return None, "not a list of rows"
    widths = {len(row) for row in value}
    if len(widths) != 1:
        return None, "rows have different lengths"
    width = widths.pop()
    height = len(value)
    if width < 1 or height < 1:
        return None, "empty map"
    if width > MAX_MAP_WIDTH or height > MAX_MAP_HEIGHT:
        return None, "too big to render (%d x %d, limit %d x %d)" % (
            width, height, MAX_MAP_WIDTH, MAX_MAP_HEIGHT)
    # Rows stay as they are — string rows must stay strings so the
    # char-row branch can tell them from lists of cells.
    return list(value), None


def _interpret_int_map(rows, palettes, int_palettes):
    """Rows of ints. Returns (grid, detail) or (None, reason)."""
    values = set()
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or not isinstance(cell, int):
                return None, None  # not an int map at all
            values.add(cell)
    if any(v < 0 for v in values):
        return None, "negative palette index in map"
    nonzero = sorted(v for v in values if v != 0)
    # Prefer an int-keyed dict from the file that covers every
    # value, then a color list long enough, else the default.
    lookup = None
    detail = "int rows, default colors (0 is transparent)"
    for name, mapping in int_palettes:
        if all(v in mapping for v in nonzero):
            lookup = mapping
            detail = "int rows, palette from %s" % name
            break
    if lookup is None:
        for name, colors in palettes:
            # 1-based, like the default path below: 1 is the first
            # color, 0 is transparent. A 0-based lookup left the
            # first color unreachable and silently dropped the
            # palette when a map used every color in it.
            if colors and max(nonzero, default=0) <= len(colors):
                lookup = {i + 1: c for i, c in enumerate(colors)}
                detail = "int rows, palette from %s" % name
                break
    grid = []
    for row in rows:
        out = []
        for cell in row:
            if cell == 0:
                out.append(None)
            elif lookup is not None:
                out.append(lookup[cell])
            else:
                r, g, b = PICO8[(cell - 1) % len(PICO8)]
                out.append((r, g, b, 255))
        grid.append(out)
    return grid, detail


def _interpret_color_map(rows):
    """Rows of color values (hex strings, named colors, RGB
    tuples, None). Returns (grid, detail) or (None, None) when
    the cells are not colors."""
    grid = []
    for row in rows:
        out = []
        for cell in row:
            if is_transparent_cell(cell):
                out.append(None)
                continue
            color = parse_color(cell)
            if color is None:
                return None, None
            out.append(color)
        grid.append(out)
    return grid, "color rows"


def _interpret_char_map(rows, legends):
    """Rows of strings, one character per pixel. Returns
    (grid, detail) or (None, reason)."""
    if not all(isinstance(row, str) for row in rows):
        return None, None  # not a char map at all
    chars = set()
    for row in rows:
        chars.update(row)
    chars.discard(".")
    chars.discard(" ")
    for name, legend in legends:
        if chars <= set(legend):
            grid = []
            for row in rows:
                out = []
                for char in row:
                    if char in (".", " "):
                        out.append(None)
                    else:
                        out.append(legend[char])
                grid.append(out)
            return grid, "character rows, legend from %s" % name
    missing = sorted(chars)
    shown = ", ".join(repr(c) for c in missing[:5])
    if len(missing) > 5:
        shown += ", ..."
    return None, ("character rows, no color legend in the file "
                  "maps every character (missing: %s)" % shown)


def interpret_map(name, value, palettes, int_palettes, legends):
    """A literal value as a pixel map.

    Returns (grid, detail) when renderable, (None, reason) when
    it looks like a map but cannot be rendered, or (None, None)
    when it is not a map at all. palettes is a list of (name,
    colors) color lists, int_palettes a list of (name, {int:
    color}) dicts, legends a list of (name, {char: color}) dicts.
    """
    if not isinstance(value, list) or not value:
        return None, None
    rows, problem = _check_rows(value)
    if rows is None:
        if looks_like_asset_name(name):
            return None, problem
        return None, None
    # Char rows: a list of strings is rows of characters — unless
    # every string parses as a color, in which case it is a flat
    # color list (a palette), not rows at all.
    if all(isinstance(row, str) for row in rows):
        if all(parse_color(row) is not None for row in rows):
            return None, None
        return _interpret_char_map(rows, legends)
    grid, detail = _interpret_int_map(rows, palettes, int_palettes)
    if grid is not None:
        return grid, detail
    if detail is not None:
        return None, detail
    grid, detail = _interpret_color_map(rows)
    if grid is not None:
        return grid, detail
    return None, None


# ------------------------------------------------------------- python

def _python_assignments(source):
    """(name, value-or-None, is_literal) for every assignment in
    a Python file, in TRUE FILE ORDER, first occurrence per name
    wins. Only literal values are taken — ast.literal_eval raises
    on anything computed, and those stay None. Never executes
    anything. ast.walk is breadth-first, so the assignments are
    collected first and sorted by (line, column) before the
    first-wins dedup — otherwise an assignment inside a function
    at the top of the file sorts after a module-level assignment
    at the bottom."""
    tree = ast.parse(source)
    collected = []
    for node in ast.walk(tree):
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and \
                node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        names = []
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
        if not names:
            continue
        try:
            literal = ast.literal_eval(value)
            is_literal = True
        except (ValueError, TypeError, SyntaxError, MemoryError,
                RecursionError):
            literal = None
            is_literal = False
        collected.append((node.lineno, node.col_offset, names,
                          literal, is_literal))
    collected.sort(key=lambda item: (item[0], item[1]))
    found = {}
    order = []
    for _line, _col, names, literal, is_literal in collected:
        for name in names:
            if name not in found:
                found[name] = (literal, is_literal)
                order.append(name)
    return [(name, found[name][0], found[name][1]) for name in order]


# --------------------------------------------------------------- js/ts

class _JsParseError(Exception):
    pass


def _strip_js_comments(text):
    """Remove // and /* */ comments, respecting string literals
    (single, double, and backtick quotes, with backslash escapes).
    Approximate: a regex literal containing // will confuse it."""
    out = []
    i = 0
    quote = None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and \
                    not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(text))
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class _JsLiteralParser:
    """Parse one JS literal expression (arrays, objects, strings,
    numbers, true/false/null/undefined) starting at a position.
    Trailing commas are tolerated. Anything else — a function
    call, an identifier, an operator — raises _JsParseError."""

    def __init__(self, text, pos):
        self.text = text
        self.pos = pos

    def _skip_space(self):
        while self.pos < len(self.text) and \
                self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek(self):
        self._skip_space()
        if self.pos >= len(self.text):
            raise _JsParseError("unexpected end of file")
        return self.text[self.pos]

    def parse(self):
        value = self._value()
        return value, self.pos

    def _value(self):
        ch = self._peek()
        if ch == "[":
            return self._array()
        if ch == "{":
            return self._object()
        if ch in ("'", '"', "`"):
            return self._string()
        if ch == "-" or ch.isdigit():
            return self._number()
        for word, value in (("true", True), ("false", False),
                            ("null", None), ("undefined", None)):
            if self.text.startswith(word, self.pos):
                after = self.pos + len(word)
                if after >= len(self.text) or \
                        not (self.text[after].isalnum() or
                             self.text[after] in "_$"):
                    self.pos = after
                    return value
        raise _JsParseError(
            "not a literal at offset %d" % self.pos)

    def _array(self):
        self.pos += 1  # [
        items = []
        if self._peek() == "]":
            self.pos += 1
            return items
        while True:
            items.append(self._value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                if self._peek() == "]":
                    self.pos += 1
                    return items
                continue
            if ch == "]":
                self.pos += 1
                return items
            raise _JsParseError("expected , or ] in array")

    def _object(self):
        self.pos += 1  # {
        obj = {}
        if self._peek() == "}":
            self.pos += 1
            return obj
        while True:
            ch = self._peek()
            if ch in ("'", '"', "`"):
                key = self._string()
            elif ch.isalpha() or ch in "_$":
                start = self.pos
                while self.pos < len(self.text) and \
                        (self.text[self.pos].isalnum() or
                         self.text[self.pos] in "_$"):
                    self.pos += 1
                key = self.text[start:self.pos]
            elif ch == "-" or ch.isdigit():
                key = str(self._number())
            else:
                raise _JsParseError("bad object key")
            if self._peek() != ":":
                raise _JsParseError("expected : in object")
            self.pos += 1
            obj[key] = self._value()
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                if self._peek() == "}":
                    self.pos += 1
                    return obj
                continue
            if ch == "}":
                self.pos += 1
                return obj
            raise _JsParseError("expected , or } in object")

    def _string(self):
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < len(self.text):
                nxt = self.text[self.pos + 1]
                escapes = {"n": "\n", "t": "\t", "r": "\r",
                           "0": "\0", "\\": "\\", "'": "'",
                           '"': '"', "`": "`", "/": "/"}
                out.append(escapes.get(nxt, nxt))
                self.pos += 2
                continue
            if ch == quote:
                self.pos += 1
                return "".join(out)
            if ch == "\n" and quote != "`":
                raise _JsParseError("newline in string")
            out.append(ch)
            self.pos += 1
        raise _JsParseError("unterminated string")

    def _number(self):
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        if self.text.startswith(("0x", "0X"), self.pos):
            self.pos += 2
            while self.pos < len(self.text) and \
                    self.text[self.pos] in "0123456789abcdefABCDEF":
                self.pos += 1
            try:
                return int(self.text[start:self.pos], 16)
            except ValueError:
                raise _JsParseError("bad hex number")
        while self.pos < len(self.text) and \
                (self.text[self.pos].isdigit() or
                 self.text[self.pos] in ".eE+-"):
            self.pos += 1
        raw = self.text[start:self.pos]
        try:
            if "." in raw or "e" in raw.lower():
                value = float(raw)
                return int(value) if value.is_integer() else value
            return int(raw)
        except ValueError:
            raise _JsParseError("bad number %r" % raw)


# const/let/var NAME [: type] = ... — the type annotation is
# skipped (it cannot cross a ; or a =, so it cannot run away).
# The declaration keyword is optional, so bare NAME = ... works
# too; the lookbehind keeps object properties (obj.x = ...) and
# comparisons (a == b) out.
# The ": type" part must not span a newline: C# reuses this
# regex, and a class line like "class Hero : MonoBehaviour {"
# would otherwise swallow everything up to the next "=" and
# report the class name as the assignment. A type annotation
# never spans lines in JS/TS either.
_JS_ASSIGN_RE = re.compile(
    r"(?:(?:export\s+)?(?:const|let|var)\s+)?"
    r"([A-Za-z_$][\w$]*)\s*(?::\s*[^=;\n]+?)?=\s*(?![=>])")


class _LoaderCall(Exception):
    """A loader call in code — load/preload (GDScript),
    Resources.Load / AssetDatabase (C#), love.graphics.newImage
    (Lua). The detector routes the path to the image-reference
    channel instead of recording an assignment, so the image is
    followed and the variable is not also reported as computed.
    try_extensions is Unity only: Resources.Load takes a path
    WITHOUT an extension ("sprites/hero"), so the resolver tries
    each image extension in turn."""

    def __init__(self, path, try_extensions=False):
        super().__init__(path)
        self.path = path
        self.try_extensions = try_extensions


def _scan_assignments(source, assign_re, strip_comments, parse_value):
    """The shared textual assignment scan (JS/TS, C#, GDScript,
    Lua): strip comments, find NAME = value matches, parse each
    value with parse_value(clean, pos) -> (value, end). Returns
    ([(name, value-or-None, is_literal)], [image_refs]) — first
    occurrence per name wins, in file order (finditer order).
    A loader call (see _LoaderCall) becomes an image reference,
    not an assignment. Anything unparseable stays non-literal."""
    clean = strip_comments(source)
    found = {}
    order = []
    image_refs = []
    prev_end = 0
    last_char = ""
    for match in assign_re.finditer(clean):
        gap = clean[prev_end:match.start()].rstrip()
        if gap:
            last_char = gap[-1]
        prev_end = match.end()
        name = match.group(1)
        if name in found:
            continue
        # "." is obj.prop = ..., "=" / "!" / "<" a comparison —
        # not an assignment. (">" is NOT excluded: C# generics
        # put one before the name, as in List<Color> palette.)
        if last_char and last_char in ".=!<":
            continue
        try:
            value, _end = parse_value(clean, match.end())
            is_literal = True
        except _LoaderCall as call:
            # "after" is how many assignments came before it, so
            # the driver can slot the image into true file order
            # alongside the assignments.
            image_refs.append({"name": name, "path": call.path,
                               "try_extensions": call.try_extensions,
                               "after": len(order)})
            found[name] = (None, False)
            continue
        except _JsParseError:
            value, is_literal = None, False
        found[name] = (value, is_literal)
        order.append(name)
    return ([(name, found[name][0], found[name][1])
             for name in order],
            image_refs)


def _js_assignments(source):
    """(name, value-or-None, is_literal) for every assignment a
    textual scan finds in a JS/TS file, first occurrence per name
    wins. Comments are stripped first; each value is parsed with
    the literal parser, so anything computed stays non-literal.
    Returns (assignments, image_refs) — JS has no loader calls,
    so image_refs is always empty here; plain string assignments
    naming image files are picked up by the driver instead."""
    assignments, _refs = _scan_assignments(
        source, _JS_ASSIGN_RE, _strip_js_comments,
        lambda text, pos: _JsLiteralParser(text, pos).parse())
    return assignments, []


# ----------------------------------------------------------- c# (unity)

# Unity's Color static properties (Unity green is full green,
# unlike CSS green).
_CS_STATIC_COLORS = {
    "red": (255, 0, 0, 255), "green": (0, 255, 0, 255),
    "blue": (0, 0, 255, 255), "white": (255, 255, 255, 255),
    "black": (0, 0, 0, 255), "yellow": (255, 255, 0, 255),
    "cyan": (0, 255, 255, 255), "magenta": (255, 0, 255, 255),
    "gray": (128, 128, 128, 255), "grey": (128, 128, 128, 255),
    "clear": (0, 0, 0, 0),
}


def _color_from_floats(args):
    """Color(r, g, b[, a]) with 0-1 floats to an (r, g, b, a)
    tuple, or None when the args are not 3-4 numbers."""
    if len(args) not in (3, 4):
        return None
    if not all(isinstance(a, (int, float))
               and not isinstance(a, bool) for a in args):
        return None
    values = [max(0, min(255, round(a * 255))) for a in args]
    if len(values) == 3:
        values.append(255)
    return tuple(values)


def _color_from_bytes(args):
    """Color32(r, g, b[, a]) with 0-255 ints to an (r, g, b, a)
    tuple, or None when the args are not 3-4 byte values."""
    if len(args) not in (3, 4):
        return None
    if not all(isinstance(a, int) and not isinstance(a, bool)
               and 0 <= a <= 255 for a in args):
        return None
    values = list(args)
    if len(values) == 3:
        values.append(255)
    return tuple(values)


class _CSharpLiteralParser(_JsLiteralParser):
    """Parse one C# literal / initializer / constructor starting
    at a position. Braces are array initializers (nested lists),
    not dicts; new Color / Color32 / Rect constructors become
    tuples / rect dicts; Color.<static> colors map directly;
    Resources.Load / AssetDatabase calls raise _LoaderCall with
    the path. Anything else raises _JsParseError."""

    def _number(self):
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        if self.text.startswith(("0x", "0X"), self.pos):
            self.pos += 2
            while self.pos < len(self.text) and \
                    self.text[self.pos] in "0123456789abcdefABCDEF_":
                self.pos += 1
            try:
                return int(self.text[start:self.pos].replace("_", ""),
                           16)
            except ValueError:
                raise _JsParseError("bad hex number")
        if self.text.startswith(("0b", "0B"), self.pos):
            self.pos += 2
            while self.pos < len(self.text) and \
                    self.text[self.pos] in "01_":
                self.pos += 1
            try:
                return int(self.text[start:self.pos].replace("_", ""),
                           2)
            except ValueError:
                raise _JsParseError("bad binary number")
        while self.pos < len(self.text) and \
                (self.text[self.pos].isdigit() or
                 self.text[self.pos] in ".eE+-_"):
            self.pos += 1
        is_float = False
        if self.pos < len(self.text) and \
                self.text[self.pos] in "fF":
            is_float = True
            self.pos += 1
        elif self.pos < len(self.text) and \
                self.text[self.pos] in "uUlL":
            self.pos += 1
        raw = self.text[start:self.pos].replace("_", "")
        raw = raw.rstrip("fFuUlL")
        try:
            if is_float or "." in raw or "e" in raw.lower():
                return float(raw)
            return int(raw)
        except ValueError:
            raise _JsParseError("bad number %r" % raw)

    def _string(self):
        # @"..." is a verbatim string: "" is an escaped quote, no
        # backslash escapes. $"..." is interpolated: braces are
        # kept as-is (the value is a plain string to us).
        if self.text[self.pos] in "@$":
            self.pos += 1
            if self.pos < len(self.text) and \
                    self.text[self.pos] == "$":
                self.pos += 1
        if self.pos >= len(self.text) or self.text[self.pos] != '"':
            raise _JsParseError("expected string")
        verbatim = self.text[self.pos - 1] == "@" or \
            (self.pos >= 2 and self.text[self.pos - 2] == "@")
        self.pos += 1
        out = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if verbatim and ch == '"' and \
                    self.pos + 1 < len(self.text) and \
                    self.text[self.pos + 1] == '"':
                out.append('"')
                self.pos += 2
                continue
            if ch == '"':
                self.pos += 1
                return "".join(out)
            if ch == "\\" and not verbatim and \
                    self.pos + 1 < len(self.text):
                nxt = self.text[self.pos + 1]
                escapes = {"n": "\n", "t": "\t", "r": "\r",
                           "0": "\0", "\\": "\\", "'": "'",
                           '"': '"', "/": "/"}
                out.append(escapes.get(nxt, nxt))
                self.pos += 2
                continue
            out.append(ch)
            self.pos += 1
        raise _JsParseError("unterminated string")

    def _char(self):
        """A C# char literal ('x') as a one-character string."""
        self.pos += 1  # '
        if self.pos >= len(self.text):
            raise _JsParseError("unterminated char")
        ch = self.text[self.pos]
        if ch == "\\" and self.pos + 1 < len(self.text):
            escapes = {"n": "\n", "t": "\t", "r": "\r",
                       "0": "\0", "\\": "\\", "'": "'",
                       '"': '"'}
            ch = escapes.get(self.text[self.pos + 1],
                             self.text[self.pos + 1])
            self.pos += 2
        else:
            self.pos += 1
        if self.pos >= len(self.text) or self.text[self.pos] != "'":
            raise _JsParseError("bad char literal")
        self.pos += 1
        return ch

    def _initializer(self):
        """A { ... } array initializer as a list (nested braces
        nest lists). An object initializer ({ X = 1 }) raises —
        the name is not a value."""
        self.pos += 1  # {
        items = []
        if self._peek() == "}":
            self.pos += 1
            return items
        while True:
            items.append(self._value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                if self._peek() == "}":
                    self.pos += 1
                    return items
                continue
            if ch == "}":
                self.pos += 1
                return items
            raise _JsParseError("expected , or } in initializer")

    def _args(self):
        """A parenthesized argument list as values."""
        if self._peek() != "(":
            raise _JsParseError("expected (")
        self.pos += 1
        args = []
        if self._peek() == ")":
            self.pos += 1
            return args
        while True:
            args.append(self._value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                continue
            if ch == ")":
                self.pos += 1
                return args
            raise _JsParseError("expected , or ) in call")

    def _skip_type(self):
        """Skip a type name after `new`: identifiers, dots,
        generics <...>, and array ranks [...] — the value that
        follows is what matters."""
        self._skip_space()
        depth_angle = 0
        depth_square = 0
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "<":
                depth_angle += 1
            elif ch == ">":
                depth_angle -= 1
            elif ch == "[":
                depth_square += 1
            elif ch == "]":
                depth_square -= 1
            elif depth_angle == 0 and depth_square == 0 \
                    and not (ch.isalnum() or ch in "_."):
                break
            self.pos += 1

    def _value(self):
        ch = self._peek()
        if ch == "{":
            return self._initializer()
        if ch == "'":
            return self._char()
        if ch == "@" or ch == "$":
            return self._string()
        if ch.isalpha() or ch == "_":
            start = self.pos
            while self.pos < len(self.text) and \
                    (self.text[self.pos].isalnum() or
                     self.text[self.pos] in "_"):
                self.pos += 1
            name = self.text[start:self.pos]
            if name in ("true", "false", "null"):
                self.pos = start
                return super()._value()
            if name == "new":
                type_start = self.pos
                self._skip_type()
                typename = self.text[type_start:self.pos].strip()
                short = typename.split(".")[-1]
                # Strip any rank spec left on the name ([,] etc).
                short = short.split("[")[0]
                nxt = self._peek()
                if nxt == "(":
                    args = self._args()
                    if short == "Color":
                        color = _color_from_floats(args)
                        if color is None:
                            raise _JsParseError(
                                "Color needs 3-4 numbers")
                        return color
                    if short == "Color32":
                        color = _color_from_bytes(args)
                        if color is None:
                            raise _JsParseError(
                                "Color32 needs 3-4 bytes")
                        return color
                    if short == "Rect":
                        if len(args) == 4 and all(
                                isinstance(a, (int, float))
                                and not isinstance(a, bool)
                                for a in args):
                            return {"x": args[0], "y": args[1],
                                    "w": args[2], "h": args[3]}
                        raise _JsParseError(
                            "Rect needs 4 numbers")
                    raise _JsParseError(
                        "not a literal constructor: %s" % short)
                if nxt == "{":
                    # new int[,] { ... } / new Color[] { ... } —
                    # the type is skipped, the initializer is the
                    # value.
                    return self._initializer()
                raise _JsParseError(
                    "not a literal constructor: %s" % short)
            if name in ("Resources", "AssetDatabase"):
                # Resources.Load<Sprite>("path") /
                # AssetDatabase.LoadAssetAtPath<Texture2D>("path")
                rest = self.text[self.pos:self.pos + 40]
                if re.match(r"\s*\.\s*(?:Load|LoadAssetAtPath)"
                            r"\s*(?:<[^>]*>)?\s*\(", rest):
                    # Skip to the opening paren, then one string.
                    paren = self.text.index("(", self.pos)
                    self.pos = paren + 1
                    self._skip_space()
                    if self.pos < len(self.text) and \
                            self.text[self.pos] == '"':
                        path = self._string()
                        raise _LoaderCall(path,
                                          try_extensions=True)
                raise _JsParseError("not a literal: %s" % name)
            if name == "Color" and self._peek() == ".":
                self.pos += 1
                attr_start = self.pos
                while self.pos < len(self.text) and \
                        (self.text[self.pos].isalnum() or
                         self.text[self.pos] == "_"):
                    self.pos += 1
                attr = self.text[attr_start:self.pos].lower()
                if attr in _CS_STATIC_COLORS:
                    return _CS_STATIC_COLORS[attr]
                raise _JsParseError(
                    "unknown Color member: %s" % attr)
            raise _JsParseError("not a literal: %s" % name)
        return super()._value()


def _csharp_assignments(source):
    """(assignments, image_refs) for a C# file — see
    _scan_assignments. Comment stripping reuses the JS stripper
    (C# comments are the same // and /* */ shapes; a verbatim
    string containing // may confuse it, as documented)."""
    return _scan_assignments(
        source, _JS_ASSIGN_RE, _strip_js_comments,
        lambda text, pos: _CSharpLiteralParser(text, pos).parse())


# ------------------------------------------------------ gdscript (godot)

# GDScript's Color constants (uppercase names).
_GD_STATIC_COLORS = {
    "RED": (255, 0, 0, 255), "GREEN": (0, 255, 0, 255),
    "BLUE": (0, 0, 255, 255), "WHITE": (255, 255, 255, 255),
    "BLACK": (0, 0, 0, 255), "YELLOW": (255, 255, 0, 255),
    "CYAN": (0, 255, 255, 255), "MAGENTA": (255, 0, 255, 255),
    "GRAY": (128, 128, 128, 255),
    "TRANSPARENT": (0, 0, 0, 0),
}


def _strip_gd_comments(text):
    """Remove GDScript # comments, respecting string literals
    (single and double quotes, with backslash escapes)."""
    out = []
    i = 0
    quote = None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class _GdLiteralParser(_JsLiteralParser):
    """_JsLiteralParser plus Godot constructors: Color / Color8 /
    Color.html / Color.<CONSTANT>, Rect2 (both forms), Vector2,
    and load / preload (which raise _LoaderCall with the path)."""

    def _args(self):
        if self._peek() != "(":
            raise _JsParseError("expected (")
        self.pos += 1
        args = []
        if self._peek() == ")":
            self.pos += 1
            return args
        while True:
            args.append(self._value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                continue
            if ch == ")":
                self.pos += 1
                return args
            raise _JsParseError("expected , or ) in call")

    def _value(self):
        ch = self._peek()
        if ch.isalpha() or ch == "_":
            start = self.pos
            while self.pos < len(self.text) and \
                    (self.text[self.pos].isalnum() or
                     self.text[self.pos] in "_."):
                self.pos += 1
            name = self.text[start:self.pos]
            if name in ("true", "false", "null"):
                self.pos = start
                return super()._value()
            if name in ("load", "preload"):
                args = self._args()
                if len(args) == 1 and isinstance(args[0], str):
                    raise _LoaderCall(args[0])
                raise _JsParseError("load needs one path")
            if name == "Color":
                args = self._args()
                if len(args) == 1 and isinstance(args[0], str):
                    color = parse_color(args[0])
                    if color is None:
                        raise _JsParseError(
                            "Color needs a color string")
                    return color
                color = _color_from_floats(args)
                if color is None:
                    raise _JsParseError(
                        "Color needs 3-4 numbers or a string")
                return color
            if name == "Color8":
                args = self._args()
                color = _color_from_bytes(args)
                if color is None:
                    raise _JsParseError(
                        "Color8 needs 3-4 bytes")
                return color
            if name == "Color.html":
                args = self._args()
                if len(args) == 1 and isinstance(args[0], str):
                    color = parse_color(args[0])
                    if color is None:
                        raise _JsParseError(
                            "Color.html needs a color string")
                    return color
                raise _JsParseError("Color.html needs one string")
            if name.startswith("Color.") and \
                    name[6:] in _GD_STATIC_COLORS:
                return _GD_STATIC_COLORS[name[6:]]
            if name == "Rect2":
                args = self._args()
                if len(args) == 4 and all(
                        isinstance(a, (int, float))
                        and not isinstance(a, bool)
                        for a in args):
                    return {"x": args[0], "y": args[1],
                            "w": args[2], "h": args[3]}
                if len(args) == 2 and all(
                        isinstance(a, tuple) and len(a) == 2
                        for a in args):
                    return {"x": args[0][0], "y": args[0][1],
                            "w": args[1][0], "h": args[1][1]}
                raise _JsParseError("Rect2 needs 4 numbers or "
                                    "2 vectors")
            if name in ("Vector2", "Vector2i"):
                args = self._args()
                if len(args) == 2 and all(
                        isinstance(a, (int, float))
                        and not isinstance(a, bool)
                        for a in args):
                    return (args[0], args[1])
                raise _JsParseError("Vector2 needs 2 numbers")
            raise _JsParseError("not a literal: %s" % name)
        return super()._value()


# var/const NAME [: type] = ... or NAME := ... or NAME = ... —
# the @export / @onready prefix is skipped. The type annotation
# cannot cross a newline, so it cannot run away.
_GD_ASSIGN_RE = re.compile(
    r"(?:@\w+\s+)?(?:(?:var|const)\s+)?"
    r"([A-Za-z_][\w]*)\s*(?::\s*[^=\n]+?)?(?::=|=)\s*(?![=>])")


def _gdscript_assignments(source):
    """(assignments, image_refs) for a GDScript file — see
    _scan_assignments."""
    return _scan_assignments(
        source, _GD_ASSIGN_RE, _strip_gd_comments,
        lambda text, pos: _GdLiteralParser(text, pos).parse())


# ------------------------------------------------------------ lua (love2d)

def _strip_lua_comments(text):
    """Remove Lua -- comments and --[[ ]] block comments,
    respecting string literals ('...', "...", and [[...]] long
    strings). --[==[ ]] levels are not handled."""
    out = []
    i = 0
    quote = None
    long_string = False
    while i < len(text):
        if long_string:
            if text.startswith("]]", i):
                out.append("]]")
                i += 2
                long_string = False
                continue
            out.append(text[i])
            i += 1
            continue
        if quote is not None:
            out.append(text[i])
            if text[i] == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if text[i] == quote:
                quote = None
            i += 1
            continue
        if text.startswith("[[", i):
            long_string = True
            out.append("[[")
            i += 2
            continue
        if text[i] in ("'", '"'):
            quote = text[i]
            out.append(text[i])
            i += 1
            continue
        if text.startswith("--[[", i):
            end = text.find("]]", i + 4)
            i = len(text) if end < 0 else end + 2
            continue
        if text.startswith("--", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


class _LuaLiteralParser:
    """Parse one Lua literal starting at a position: tables
    ({...} with positional values, name = value pairs, or
    [key] = value pairs), strings ('...', "...", [[...]]),
    numbers (0x hex, exponents), true/false/nil, and the
    Love2D calls newImage (raises _LoaderCall) and newQuad
    (a rect dict). Anything else raises _JsParseError."""

    def __init__(self, text, pos):
        self.text = text
        self.pos = pos

    def _skip_space(self):
        while self.pos < len(self.text) and \
                self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek(self):
        self._skip_space()
        if self.pos >= len(self.text):
            raise _JsParseError("unexpected end of file")
        return self.text[self.pos]

    def parse(self):
        return self._value(), self.pos

    def _value(self):
        ch = self._peek()
        if ch == "{":
            return self._table()
        if ch in ("'", '"'):
            return self._string()
        if self.text.startswith("[[", self.pos):
            return self._long_string()
        if ch == "-" or ch.isdigit() or \
                (ch == "." and self.pos + 1 < len(self.text) and
                 self.text[self.pos + 1].isdigit()):
            return self._number()
        if ch.isalpha() or ch == "_":
            start = self.pos
            while self.pos < len(self.text) and \
                    (self.text[self.pos].isalnum() or
                     self.text[self.pos] in "_."):
                self.pos += 1
            name = self.text[start:self.pos]
            for word, value in (("true", True), ("false", False),
                                ("nil", None)):
                if name == word:
                    return value
            if name in ("love.graphics.newImage", "newImage"):
                args = self._args()
                if len(args) == 1 and isinstance(args[0], str):
                    raise _LoaderCall(args[0])
                raise _JsParseError("newImage needs one path")
            if name in ("love.graphics.newQuad", "newQuad"):
                args = self._args()
                if len(args) >= 4 and all(
                        isinstance(a, (int, float))
                        and not isinstance(a, bool)
                        for a in args[:4]):
                    return {"x": args[0], "y": args[1],
                            "w": args[2], "h": args[3]}
                raise _JsParseError("newQuad needs 4 numbers")
            raise _JsParseError("not a literal: %s" % name)
        raise _JsParseError(
            "not a literal at offset %d" % self.pos)

    def _args(self):
        if self._peek() != "(":
            raise _JsParseError("expected (")
        self.pos += 1
        args = []
        if self._peek() == ")":
            self.pos += 1
            return args
        while True:
            args.append(self._value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                continue
            if ch == ")":
                self.pos += 1
                return args
            raise _JsParseError("expected , or ) in call")

    def _table(self):
        self.pos += 1  # {
        items = []
        keyed = {}
        has_keyed = False
        if self._peek() == "}":
            self.pos += 1
            return items
        while True:
            # [key] = value, or name = value, or a plain value.
            save = self.pos
            key = None
            ch = self._peek()
            if ch == "[":
                self.pos += 1
                key = self._value()
                if self._peek() != "]":
                    raise _JsParseError("expected ] in table key")
                self.pos += 1
            elif ch.isalpha() or ch == "_":
                start = self.pos
                while self.pos < len(self.text) and \
                        (self.text[self.pos].isalnum() or
                         self.text[self.pos] == "_"):
                    self.pos += 1
                word = self.text[start:self.pos]
                if self._peek() == "=":
                    key = word
                else:
                    self.pos = save
            if key is not None:
                if self._peek() != "=":
                    raise _JsParseError("expected = in table")
                self.pos += 1
                keyed[key] = self._value()
                has_keyed = True
            else:
                items.append(self._value())
            ch = self._peek()
            if ch in (",", ";"):
                self.pos += 1
                if self._peek() == "}":
                    self.pos += 1
                    break
                continue
            if ch == "}":
                self.pos += 1
                break
            raise _JsParseError("expected , or } in table")
        if not has_keyed:
            return items
        # Mixed tables become dicts; positional items take
        # 1-based int keys, as Lua numbers them.
        merged = {i + 1: value for i, value in enumerate(items)}
        merged.update(keyed)
        return merged

    def _string(self):
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < len(self.text):
                nxt = self.text[self.pos + 1]
                escapes = {"n": "\n", "t": "\t", "r": "\r",
                           "0": "\0", "\\": "\\", "'": "'",
                           '"': '"'}
                out.append(escapes.get(nxt, nxt))
                self.pos += 2
                continue
            if ch == quote:
                self.pos += 1
                return "".join(out)
            if ch == "\n":
                raise _JsParseError("newline in string")
            out.append(ch)
            self.pos += 1
        raise _JsParseError("unterminated string")

    def _long_string(self):
        self.pos += 2  # [[
        end = self.text.find("]]", self.pos)
        if end < 0:
            raise _JsParseError("unterminated long string")
        value = self.text[self.pos:end]
        self.pos = end + 2
        return value

    def _number(self):
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        if self.text.startswith(("0x", "0X"), self.pos):
            self.pos += 2
            while self.pos < len(self.text) and \
                    self.text[self.pos] in "0123456789abcdefABCDEF":
                self.pos += 1
            try:
                return int(self.text[start:self.pos], 16)
            except ValueError:
                raise _JsParseError("bad hex number")
        while self.pos < len(self.text) and \
                (self.text[self.pos].isdigit() or
                 self.text[self.pos] in ".eE+-"):
            self.pos += 1
        raw = self.text[start:self.pos]
        try:
            if "." in raw or "e" in raw.lower():
                return float(raw)
            return int(raw)
        except ValueError:
            raise _JsParseError("bad number %r" % raw)


def _lua_float_colors(value, _top=True):
    """Love2D color tables, recursively: a list of 3-4 numbers,
    all in [0, 1], becomes an (r, g, b, a) tuple scaled to
    0-255. At the top level (the whole assignment value) any
    numbers count — a flat list cannot be a map, so {1, 0, 0}
    is a color even written without decimals, as Lua code
    usually writes it. Deeper down (rows of a nested table) at
    least one number must be a float: a row of palette indices
    like {1, 0, 1} must stay a row. A palette of int-only color
    tables therefore still reads as a map — the two shapes are
    textually identical in Lua, and the float is the only
    signal that tells them apart."""
    if isinstance(value, list):
        if len(value) in (3, 4) and value and \
                (_top or any(isinstance(c, float) for c in value)) \
                and all(isinstance(c, (int, float))
                        and not isinstance(c, bool) and 0 <= c <= 1
                        for c in value):
            scaled = [round(c * 255) for c in value]
            if len(scaled) == 3:
                scaled.append(255)
            return tuple(scaled)
        return [_lua_float_colors(item, False) for item in value]
    if isinstance(value, dict):
        return {key: _lua_float_colors(item, False)
                for key, item in value.items()}
    return value


# local NAME = ... or NAME = ... — multi-assign (local a, b = ...)
# only catches the last name, which is fine for sprite data.
_LUA_ASSIGN_RE = re.compile(
    r"(?:local\s+)?([A-Za-z_][\w]*)\s*=\s*(?![=>])")


def _lua_assignments(source):
    """(assignments, image_refs) for a Lua file — see
    _scan_assignments. Color tables are normalized by
    _lua_float_colors after the scan."""
    assignments, image_refs = _scan_assignments(
        source, _LUA_ASSIGN_RE, _strip_lua_comments,
        lambda text, pos: _LuaLiteralParser(text, pos).parse())
    normalized = []
    for name, value, is_literal in assignments:
        if is_literal:
            value = _lua_float_colors(value)
        normalized.append((name, value, is_literal))
    return normalized, image_refs


# -------------------------------------------------------------- driver

def _collect_palettes(assignments):
    """(color lists, int-keyed dicts, char-keyed dicts) from a
    file's literal assignments, in file order."""
    palettes = []
    int_palettes = []
    legends = []
    for name, value, is_literal in assignments:
        if not is_literal or value is None:
            continue
        if isinstance(value, dict) and value:
            # A rectangle is a frame region, never a palette:
            # without this a Rect dict would be collected as a
            # 4-color palette and an int map would render in
            # its colors ("palette from frame").
            if _is_rect_dict(value):
                continue
            keys = list(value.keys())
            colors = classify_palette(value)
            if colors is None:
                continue
            if all(isinstance(k, str) and len(k) == 1
                   for k in keys):
                legends.append((name, {
                    k: parse_color(v) for k, v in value.items()}))
            elif all(isinstance(k, int) and not isinstance(k, bool)
                     for k in keys):
                int_palettes.append((name, {
                    k: parse_color(v) for k, v in value.items()}))
            palettes.append((name, colors))
        elif isinstance(value, list) and value:
            colors = classify_palette(value)
            if colors is not None:
                palettes.append((name, colors))
    return palettes, int_palettes, legends


def _is_rect_dict(value):
    """True for a dict that is a rectangle, not a palette:
    exactly the keys x, y, w, h (or x, y, width, height), all
    numbers. A C# Rect, a GDScript Rect2, a Lua newQuad, or a
    plain dict written that way all land here."""
    if not isinstance(value, dict):
        return False
    keys = set(value.keys())
    if keys not in ({"x", "y", "w", "h"},
                    {"x", "y", "width", "height"}):
        return False
    return all(isinstance(item, (int, float))
               and not isinstance(item, bool)
               for item in value.values())


def _has_rect_keys(value):
    """True for a dict carrying a rectangle among other keys —
    a JSON frame like {"name": "idle", "x": 0, "y": 0, "w": 16,
    "h": 16}. Unlike _is_rect_dict, extra keys are allowed, so
    this is only used where a rectangle is already expected."""
    if not isinstance(value, dict):
        return False
    keys = set(value.keys())
    if not {"x", "y"} <= keys:
        return False
    if not ({"w", "h"} <= keys or {"width", "height"} <= keys):
        return False
    for key in ("x", "y", "w", "h", "width", "height"):
        if key in value and (
                not isinstance(value[key], (int, float))
                or isinstance(value[key], bool)):
            return False
    return True


def _fmt_num(value):
    """A rect number in plain words: 32, not 32.0."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rect_note(value):
    """The plain-words note for a rectangle: its size and
    position, and that it is a frame region, not pixels."""
    width = value.get("w", value.get("width"))
    height = value.get("h", value.get("height"))
    return ("rectangle %s x %s at (%s, %s), a frame region, "
            "not pixels" % (_fmt_num(width), _fmt_num(height),
                            _fmt_num(value.get("x")),
                            _fmt_num(value.get("y"))))


def _looks_like_image_path(value):
    """True for a string naming an image file — a path ending in
    an image extension (a query string or fragment after the
    extension does not stop it being one)."""
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    if not text:
        return False
    for sep in ("?", "#"):
        if sep in text:
            text = text.split(sep, 1)[0]
    return any(text.endswith(ext) for ext in IMAGE_EXTENSIONS)


def _is_absolute_ref(ref):
    """True for a path that points somewhere absolute: a leading
    slash or backslash, a home (~) path, or a Windows drive
    letter. Absolute references are never followed — the Code
    tab only follows paths next to the code file."""
    if ref.startswith(("/", "\\", "~")):
        return True
    return len(ref) >= 2 and ref[1] == ":" and ref[0].isalpha()


def _resolve_image_reference(ref, code_path, try_extensions=False):
    """Resolve an image reference to a file on disk.

    Returns (path, reason): path is a Path when the file exists,
    else None and reason names what went wrong in plain words.
    Relative paths resolve against the code file's folder.
    res:// paths (Godot) strip the prefix and search upward from
    the code file's folder, up to RES_UP_LEVELS, because res://
    means the project root. Absolute paths are never followed.
    With try_extensions (Unity Resources.Load, which takes a path
    WITHOUT an extension), each image extension is tried in
    turn. A ".." step is allowed: the reference is still relative
    to the code file, and only image files are ever loaded."""
    code_dir = Path(code_path).parent
    name = Path(code_path).name
    clean = ref.strip()
    for sep in ("?", "#"):
        if sep in clean:
            clean = clean.split(sep, 1)[0]
    if _is_absolute_ref(clean):
        return None, ("absolute path, not followed: %s (only "
                      "paths next to the code file are followed)"
                      % ref)
    candidates = []
    if clean.startswith("res://"):
        rel = clean[len("res://"):]
        folder = code_dir
        for _level in range(RES_UP_LEVELS + 1):
            candidates.append(folder / rel)
            if folder.parent == folder:
                break
            folder = folder.parent
    else:
        candidates.append(code_dir / clean)
    if try_extensions:
        extended = []
        for base in candidates:
            extended.append(base)
            for ext in sorted(IMAGE_EXTENSIONS):
                extended.append(base.with_suffix(ext))
        candidates = extended
    for candidate in candidates:
        if candidate.is_file():
            return candidate, None
    return None, ("image file not found: %s (looked next to %s)"
                  % (ref, name))


def _load_referenced_image(resolved, ref, code_path):
    """Load a resolved image reference. Returns (image, reason):
    the image on success, else None and a plain reason. The
    load_image error for a missing Qt build carries an em-dash;
    reasons reach the UI, so it is sanitised here."""
    try:
        return load_image(str(resolved)), None
    except PixelForgeError as exc:
        return None, ("could not read %s: %s"
                      % (ref, str(exc).replace("—", "-")))
    except (OSError, ValueError) as exc:
        return None, ("could not read %s: %s" % (ref, exc))


def _interpret_assignments(assignments, image_refs, path):
    """Turn literal assignments + image references into assets
    and not-rendered notes, in file order. image_refs carry an
    "after" count (how many assignments came before them), so
    loader calls slot into the same order as the assignments.
    Returns (assets, not_rendered). Shared by every language
    path and by JSON, so a color or a map reads the same no
    matter where it came from.

    Branch order per assignment: flat color-tuple list (a
    palette), map, single color, single rect, palette, draw
    spec, image reference. The rect branch sits before the
    palette branch — a rect dict would otherwise read as a
    nonsense 4-color palette."""
    palettes, int_palettes, legends = _collect_palettes(assignments)
    assets = []
    not_rendered = []
    refs_by_after = {}
    for ref in image_refs:
        refs_by_after.setdefault(ref.get("after", len(assignments)),
                                 []).append(ref)

    def _add_image_asset(name, ref, resolved):
        image, reason = _load_referenced_image(resolved, ref, path)
        if image is None:
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": reason})
            return
        assets.append({
            "name": name, "kind": "image",
            "source_file": str(path),
            "width": image.width(), "height": image.height(),
            "colors": count_opaque_colors(image),
            "image": image,
            "detail": "image: %s" % ref})

    def _add_image_ref(name, ref, try_extensions):
        resolved, reason = _resolve_image_reference(
            ref, path, try_extensions)
        if resolved is None:
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": reason})
            return
        _add_image_asset(name, ref, resolved)

    def _add_palette_asset(name, colors):
        image = render_palette_strip(colors)
        assets.append({
            "name": name, "kind": "palette",
            "source_file": str(path),
            "width": image.width(), "height": image.height(),
            "colors": len(colors),
            "image": image,
            "detail": "%d colors" % len(colors)})

    def _interpret_one(name, value, is_literal):
        if not is_literal:
            if looks_like_asset_name(name):
                not_rendered.append({
                    "name": name, "source_file": str(path),
                    "reason": "computed at run time — static "
                    "parsing cannot render it (code is never "
                    "executed)"})
            return
        if value is None:
            return
        # A flat list of color tuples is a palette, not rows: a
        # C# Color[], a GDScript color array, and a Lua palette
        # of float tables all parse to this shape, and reading
        # the tuples as int rows would render nonsense colors.
        # (A list of 2-tuples still reads as rows — a 2-tuple
        # is not a color — and a nested list is untouched, so
        # a color map of tuple cells still renders as a map.)
        if isinstance(value, list) and value and all(
                isinstance(item, tuple)
                and parse_color(item) is not None
                for item in value):
            _add_palette_asset(
                name, [parse_color(item) for item in value])
            return
        grid, detail = interpret_map(name, value, palettes,
                                     int_palettes, legends)
        if grid is not None:
            image = render_color_grid(grid)
            assets.append({
                "name": name, "kind": "map",
                "source_file": str(path),
                "width": image.width(), "height": image.height(),
                "colors": _distinct_colors(grid),
                "image": image, "detail": detail})
            return
        if detail is not None:
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": detail})
            return
        # A single color: a tuple of 3-4 ints (a Python tuple, a
        # C#/GDScript Color, a Lua float table) or a color string
        # ("#ff0000", "0x336699", "red"). Bare ints are NOT colors
        # here — a MAX_HEALTH = 100 must not become a swatch —
        # and a flat int list keeps the palette reading below. A
        # bare string only counts when it is written as a color,
        # so a word that merely happens to be three hex letters
        # ("abc") does not become a swatch.
        color = None
        if isinstance(value, tuple):
            color = parse_color(value)
        elif isinstance(value, str):
            text = value.strip()
            if text.startswith("#") \
                    or text.lower().startswith("0x") \
                    or text.lower() in NAMED_COLORS:
                color = parse_color(value)
        if color is not None:
            image = render_palette_strip([color])
            r, g, b, a = color
            if a < 255:
                detail = "rgba(%d, %d, %d, %d)" % (r, g, b, a)
            else:
                detail = "#%02x%02x%02x" % (r, g, b)
            assets.append({
                "name": name, "kind": "color",
                "source_file": str(path),
                "width": image.width(), "height": image.height(),
                "colors": 1, "image": image, "detail": detail})
            return
        # A rectangle: a frame region, not pixels.
        if _is_rect_dict(value):
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": _rect_note(value)})
            return
        colors = classify_palette(value)
        if colors is not None:
            _add_palette_asset(name, colors)
            return
        if _is_draw_spec(value):
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": "draw-call spec (rectangles to draw), "
                "not a pixel map — run the code to see it"})
            return
        # An image path in a string: follow it.
        if _looks_like_image_path(value):
            _add_image_ref(name, value, try_extensions=False)
            return

    for index in range(len(assignments) + 1):
        for ref in refs_by_after.get(index, []):
            _add_image_ref(ref["name"], ref["path"],
                           ref.get("try_extensions", False))
        if index < len(assignments):
            name, value, is_literal = assignments[index]
            _interpret_one(name, value, is_literal)
    return assets, not_rendered


def _json_digit_keys(value):
    """A JSON value with digit-string dict keys converted to int
    keys, recursively — JSON object keys are always strings, so
    an int palette {"0": "#ff0000"} needs this to read as an
    int-keyed palette instead of a char legend."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str):
                try:
                    key = int(key)
                except ValueError:
                    pass
            out[key] = _json_digit_keys(item)
        return out
    if isinstance(value, list):
        return [_json_digit_keys(item) for item in value]
    return value


def _json_frame_name(name):
    """An Aseprite frame name without the trailing ".aseprite"
    (frames are named "<sprite> <n>.aseprite" in the export)."""
    if name.endswith(".aseprite"):
        return name[:-len(".aseprite")]
    return name


def _json_frames(doc, path):
    """Frame assets from a JSON spritesheet description.

    Aseprite: {"frames": {"name": {"frame": {x, y, w, h}, ...}}
    or {"frames": [{"filename": ..., "frame": {...}}, ...]} with
    meta.image naming the sheet. Generic: {"frames": [...],
    "image": "sheet.png"}, where each frame is either a {"frame":
    {x, y, w, h}} wrapper or a bare {x, y, w, h} rect. Each frame
    is cropped from the sheet; a frame entirely outside the sheet
    is a note, not a crash. Returns (assets, not_rendered)."""
    assets = []
    not_rendered = []
    if not isinstance(doc, dict):
        return assets, not_rendered
    frames = doc.get("frames")
    if frames is None:
        return assets, not_rendered
    entries = []  # (name, rect dict)
    if isinstance(frames, dict):
        for name, data in frames.items():
            if isinstance(data, dict) and isinstance(
                    data.get("frame"), dict):
                entries.append((str(name), data["frame"]))
    elif isinstance(frames, list):
        for data in frames:
            if not isinstance(data, dict):
                continue
            rect = data.get("frame")
            if not isinstance(rect, dict):
                if _has_rect_keys(data):
                    rect = data
                else:
                    continue
            name = data.get("filename", data.get("name", "frame"))
            entries.append((str(name), rect))
    if not entries:
        return assets, not_rendered
    sheet_ref = None
    meta = doc.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("image"), str):
        sheet_ref = meta["image"]
    elif isinstance(doc.get("image"), str):
        sheet_ref = doc["image"]
    if sheet_ref is None:
        for name, _rect in entries:
            not_rendered.append({
                "name": _json_frame_name(name),
                "source_file": str(path),
                "reason": "frame with no sheet image to crop "
                "from (no image named in the file)"})
        return assets, not_rendered
    resolved, reason = _resolve_image_reference(sheet_ref, path)
    if resolved is None:
        for name, _rect in entries:
            not_rendered.append({
                "name": _json_frame_name(name),
                "source_file": str(path), "reason": reason})
        return assets, not_rendered
    sheet, load_reason = _load_referenced_image(resolved, sheet_ref,
                                                path)
    if sheet is None:
        for name, _rect in entries:
            not_rendered.append({
                "name": _json_frame_name(name),
                "source_file": str(path), "reason": load_reason})
        return assets, not_rendered
    sheet_name = resolved.name
    for name, rect in entries:
        try:
            x = int(rect.get("x", 0))
            y = int(rect.get("y", 0))
            width = int(rect.get("w", rect.get("width", 0)))
            height = int(rect.get("h", rect.get("height", 0)))
        except (TypeError, ValueError):
            not_rendered.append({
                "name": _json_frame_name(name),
                "source_file": str(path),
                "reason": "frame has a bad rectangle, not "
                "numbers for x, y, w, h"})
            continue
        # Clamp to the sheet; entirely outside is a note.
        x0, y0 = max(x, 0), max(y, 0)
        x1 = min(x + width, sheet.width())
        y1 = min(y + height, sheet.height())
        if x1 <= x0 or y1 <= y0 or width <= 0 or height <= 0:
            not_rendered.append({
                "name": _json_frame_name(name),
                "source_file": str(path),
                "reason": "frame outside the sheet image "
                "(%s is %d x %d)" % (sheet_name, sheet.width(),
                                     sheet.height())})
            continue
        frame = sheet.copy(x0, y0, x1 - x0, y1 - y0)
        assets.append({
            "name": _json_frame_name(name), "kind": "frame",
            "source_file": str(path),
            "width": frame.width(), "height": frame.height(),
            "colors": count_opaque_colors(frame),
            "image": frame,
            "detail": "frame from %s" % sheet_name})
    return assets, not_rendered


# Keys the per-key JSON scan skips: frames and the sheet are
# handled by _json_frames, pixels/palette/width/height by their
# own branches, and frameTags is Aseprite tag metadata, not
# something renderable.
_JSON_SKIP_KEYS = frozenset([
    "frames", "meta", "image", "pixels", "data", "grid",
    "palette", "width", "height", "frameTags"])


def _parse_json_document(doc, path):
    """A parsed JSON document into (assignments, image_refs,
    assets, not_rendered). Frames become assets directly (see
    _json_frames); the rest becomes assignments for the shared
    interpreter, so JSON maps and palettes read exactly like
    code ones. Shapes: a top-level list (rows of a map, or a
    flat palette); a "pixels"/"data"/"grid" row list, optionally
    a flat list plus "width"/"height" to reshape; a "palette"
    list; any other key holding a literal (a named sprite, a
    palette, a color); an "image" key with no frames is a plain
    image reference."""
    frame_assets, frame_notes = _json_frames(doc, path)
    assignments = []
    image_refs = []
    stem = Path(path).stem
    if isinstance(doc, list):
        assignments.append((stem, _json_digit_keys(doc), True))
        return assignments, image_refs, frame_assets, frame_notes
    if not isinstance(doc, dict):
        return assignments, image_refs, frame_assets, frame_notes
    rows = None
    for key in ("pixels", "data", "grid"):
        if isinstance(doc.get(key), list):
            rows = doc[key]
            break
    if rows is not None:
        # A flat list plus width/height is one long row list to
        # reshape; a list of lists is already rows.
        if rows and not isinstance(rows[0], (list, dict)) \
                and isinstance(doc.get("width"), int) \
                and not isinstance(doc.get("width"), bool) \
                and isinstance(doc.get("height"), int) \
                and not isinstance(doc.get("height"), bool):
            width = doc["width"]
            height = doc["height"]
            if width > 0 and height > 0:
                rows = [rows[y * width:(y + 1) * width]
                        for y in range(height)]
        assignments.append((stem, _json_digit_keys(rows), True))
    palette = doc.get("palette")
    if isinstance(palette, (list, dict)):
        assignments.append(("palette", _json_digit_keys(palette),
                            True))
    for key, value in doc.items():
        if key in _JSON_SKIP_KEYS:
            continue
        if isinstance(value, (list, dict, str, int, float, bool)) \
                or value is None:
            assignments.append((str(key), _json_digit_keys(value),
                                True))
    if not frame_assets and not frame_notes \
            and isinstance(doc.get("image"), str):
        image_refs.append({"name": stem, "path": doc["image"],
                           "try_extensions": False})
    return assignments, image_refs, frame_assets, frame_notes


def _parse_json_source(source, path):
    """Parse JSON text. Raises PixelForgeError on invalid JSON
    (with the line named); a RecursionError from a deeply-nested
    document is left for the funnel in parse_source to catch."""
    try:
        doc = json.loads(source)
    except json.JSONDecodeError as exc:
        raise PixelForgeError(
            "could not parse %s as JSON: %s (line %s)"
            % (Path(path).name, exc.msg, exc.lineno))
    assignments, image_refs, assets, not_rendered = \
        _parse_json_document(doc, path)
    more_assets, more_notes = _interpret_assignments(
        assignments, image_refs, path)
    assets.extend(more_assets)
    not_rendered.extend(more_notes)
    return {"assets": assets, "not_rendered": not_rendered}


# The textual detectors, by parse_source language. Python and
# JSON have their own paths (ast, json.loads).
_LANGUAGE_DETECTORS = {
    "js": _js_assignments,
    "cs": _csharp_assignments,
    "gd": _gdscript_assignments,
    "lua": _lua_assignments,
}


def parse_source(source, path, language):
    """Parse source text of one file. language is "py", "js",
    "cs", "gd", "lua", or "json". Returns {"assets": [...],
    "not_rendered": [...]}. Raises PixelForgeError on a Python
    syntax error or invalid JSON. A RecursionError from any
    language's parser — a deeply-nested hostile file — is caught
    here, at the one place every parse path funnels through, and
    reported plainly; it never reaches the GUI uncaught. The
    textual languages (JS/C#/GDScript/Lua) have no syntax check:
    the scan just finds what it finds."""
    try:
        if language == "json":
            return _parse_json_source(source, path)
        if language == "py":
            try:
                assignments = _python_assignments(source)
            except SyntaxError as exc:
                # Some syntax errors carry no line number (a
                # binary file's "source code string cannot
                # contain null bytes" has lineno None) — "(line
                # None)" is not a plain message, so leave the
                # line out then.
                if exc.lineno is None:
                    raise PixelForgeError(
                        "could not parse %s as Python: %s"
                        % (Path(path).name, exc.msg))
                raise PixelForgeError(
                    "could not parse %s as Python: %s (line %s)"
                    % (Path(path).name, exc.msg, exc.lineno))
            image_refs = []
        else:
            detector = _LANGUAGE_DETECTORS[language]
            assignments, image_refs = detector(source)
        assets, not_rendered = _interpret_assignments(
            assignments, image_refs, path)
        return {"assets": assets, "not_rendered": not_rendered}
    except RecursionError:
        raise PixelForgeError(
            "could not read %s: it is nested too deeply to parse"
            % Path(path).name)


# Extension to parse_source language. .ts is TypeScript: the JS
# detector handles it (the assignment regex skips ": type"
# annotations).
_LANGUAGE_BY_EXTENSION = {
    ".py": "py",
    ".js": "js",
    ".ts": "js",
    ".json": "json",
    ".cs": "cs",
    ".gd": "gd",
    ".lua": "lua",
}


def parse_file(path):
    """Parse one code file (.py, .js, .ts, .json, .cs, .gd,
    .lua). Returns {"assets": [...], "not_rendered": [...],
    "files": [path]}. Raises PixelForgeError on a missing file,
    a wrong extension, a file over the size limit, a Python
    syntax error, or invalid JSON. The size check runs BEFORE
    reading, so a huge file is refused with its size named and
    never freezes the parse."""
    p = Path(path)
    if not p.is_file():
        raise PixelForgeError("not found: %s" % path)
    ext = p.suffix.lower()
    if ext not in CODE_EXTENSIONS:
        raise PixelForgeError(
            "not a code file: %s (need %s)"
            % (path, CODE_EXTENSIONS_TEXT))
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise PixelForgeError("could not read %s: %s" % (path, exc))
    if size > MAX_CODE_FILE_BYTES:
        raise PixelForgeError(
            "too big to read: %s is %s (limit %s)"
            % (p.name, _format_size(size),
               _format_size(MAX_CODE_FILE_BYTES)))
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PixelForgeError("could not read %s: %s" % (path, exc))
    language = _LANGUAGE_BY_EXTENSION[ext]
    result = parse_source(source, p, language)
    result["files"] = [str(p)]
    result["skipped"] = []
    return result


def parse_paths(paths):
    """Parse files and/or folders (a folder contributes its code
    files, non-recursive, sorted by name). Returns
    the merged result, with a "skipped" list of {"file",
    "reason"} for files that failed to parse — one bad file
    never aborts the rest (a file that parses but defines no
    assets is a success, not a skip). Raises PixelForgeError
    when nothing parseable was given, when a path does not
    exist, or when EVERY file failed: a single bad file raises
    its own error unchanged, so `pixel-forge code BROKEN.py`
    reads exactly as before; several bad files are all named in
    the one message."""
    files = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in
                CODE_EXTENSIONS))
        elif p.is_file():
            files.append(p)
        else:
            raise PixelForgeError("not found: %s" % raw)
    if not files:
        raise PixelForgeError(
            "no %s files in %s"
            % (CODE_EXTENSIONS_TEXT,
               ", ".join(str(p) for p in paths)))
    merged = {"assets": [], "not_rendered": [], "files": [],
              "skipped": []}
    for path in files:
        try:
            result = parse_file(path)
        except PixelForgeError as exc:
            merged["skipped"].append({"file": str(path),
                                      "reason": str(exc)})
            continue
        merged["assets"].extend(result["assets"])
        merged["not_rendered"].extend(result["not_rendered"])
        merged["files"].extend(result["files"])
    if not merged["files"]:
        # Nothing parsed at all. One file: its own error, word
        # for word, so the single-file path is unchanged.
        if len(merged["skipped"]) == 1:
            raise PixelForgeError(merged["skipped"][0]["reason"])
        detail = "; ".join("%s: %s" % (skip["file"],
                                       skip["reason"])
                           for skip in merged["skipped"])
        raise PixelForgeError(
            "could not parse any of %s: %s"
            % (plural(len(files), "file"), detail))
    return merged
