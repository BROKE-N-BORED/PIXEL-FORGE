"""Code input — reproduce pixel-art assets FROM source code, statically.

Feed Pixel Forge a .py / .js / .ts file (or a folder of them) and it
rebuilds the pixel art the code defines — pixel maps, palettes — as
real sprites. The code is NEVER executed, evaled, execed, or
imported: Python files are read with ast.parse and only literal
values are taken (ast.literal_eval), and JS/TS files are scanned
with a small literal parser (no JS engine is available here). A
file that defines its art by running code (loops, function calls,
draw calls) cannot be rendered this way — those assets land in the
"found but not rendered" list with a plain reason, never faked.

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
  Draw-call specs — lists of dicts with rectangle keys (x, y, w,
  h, color/fill) are recognized and reported as not rendered:
  they describe drawing steps, not pixels.

What is NOT detected: anything computed (function calls, loops,
arithmetic, comprehensions, f-strings with expressions), sprites
loaded from image files at run time, sprites drawn by a game
engine's API, and Three.js / canvas / WebGL geometry. Python
detection looks at assignments anywhere in the file (module level,
inside functions and classes); JS/TS detection is a textual scan,
so it finds assignments anywhere too, but comment stripping is
approximate — a regex literal containing "//" may confuse it.
Maps are capped at 512 x 512; rows must all be the same length.
"""

import ast
import re
from pathlib import Path

from .image import Image, argb
from .palettes import PICO8
from .pixelize import PixelForgeError

CODE_EXTENSIONS = {".py", ".js", ".ts"}
MAX_MAP_WIDTH = 512
MAX_MAP_HEIGHT = 512
PALETTE_BLOCK = 8  # pixels per color in a rendered palette strip

# A few common named colors, so ["red", "gold"] works as a palette.
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

def parse_color(value):
    """A color value to an (r, g, b, a) tuple, or None when the
    value is not a color. Accepts "#rrggbb", "#rgb", "#rrggbbaa",
    "rrggbb", "0xRRGGBB", named colors, and (r, g, b) / (r, g, b, a)
    tuples or lists of ints. Ints are read as 0xRRGGBB — but only
    in palette position (see interpret); in a map, ints are
    palette indices, never colors. None is transparent."""
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
    a Python file, first occurrence per name wins. Only literal
    values are taken — ast.literal_eval raises on anything
    computed, and those stay None. Never executes anything."""
    tree = ast.parse(source)
    found = {}
    order = []
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
_JS_ASSIGN_RE = re.compile(
    r"(?:(?:export\s+)?(?:const|let|var)\s+)?"
    r"([A-Za-z_$][\w$]*)\s*(?::\s*[^=;]+?)?=\s*(?![=>])")


def _js_assignments(source):
    """(name, value-or-None, is_literal) for every assignment a
    textual scan finds in a JS/TS file, first occurrence per name
    wins. Comments are stripped first; each value is parsed with
    the literal parser, so anything computed stays non-literal."""
    clean = _strip_js_comments(source)
    found = {}
    order = []
    for match in _JS_ASSIGN_RE.finditer(clean):
        name = match.group(1)
        if name in found:
            continue
        before = clean[:match.start()].rstrip()
        if before and before[-1] in ".=!<>":
            continue  # obj.prop = ... or a == b, not an assignment
        try:
            value, _end = _JsLiteralParser(clean, match.end()).parse()
            is_literal = True
        except _JsParseError:
            value, is_literal = None, False
        found[name] = (value, is_literal)
        order.append(name)
    return [(name, found[name][0], found[name][1]) for name in order]


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


def parse_source(source, path, language):
    """Parse source text of one file. language is "py" or "js".
    Returns {"assets": [...], "not_rendered": [...]} — see
    parse_file. Raises PixelForgeError on a Python syntax error;
    JS has no syntax check (the scan just finds what it finds)."""
    if language == "py":
        try:
            assignments = _python_assignments(source)
        except SyntaxError as exc:
            raise PixelForgeError(
                "could not parse %s as Python: %s (line %s)"
                % (Path(path).name, exc.msg, exc.lineno))
    else:
        assignments = _js_assignments(source)
    palettes, int_palettes, legends = _collect_palettes(assignments)
    assets = []
    not_rendered = []
    for name, value, is_literal in assignments:
        if not is_literal:
            if looks_like_asset_name(name):
                not_rendered.append({
                    "name": name, "source_file": str(path),
                    "reason": "computed at run time — static "
                    "parsing cannot render it (code is never "
                    "executed)"})
            continue
        if value is None:
            continue
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
            continue
        if detail is not None:
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": detail})
            continue
        colors = classify_palette(value)
        if colors is not None:
            image = render_palette_strip(colors)
            assets.append({
                "name": name, "kind": "palette",
                "source_file": str(path),
                "width": image.width(), "height": image.height(),
                "colors": len(colors),
                "image": image,
                "detail": "%d colors" % len(colors)})
            continue
        if _is_draw_spec(value):
            not_rendered.append({
                "name": name, "source_file": str(path),
                "reason": "draw-call spec (rectangles to draw), "
                "not a pixel map — run the code to see it"})
    return {"assets": assets, "not_rendered": not_rendered}


def parse_file(path):
    """Parse one .py / .js / .ts file. Returns {"assets": [...],
    "not_rendered": [...], "files": [path]}. Raises
    PixelForgeError on a missing file, a wrong extension, or a
    Python syntax error."""
    p = Path(path)
    if not p.is_file():
        raise PixelForgeError("not found: %s" % path)
    ext = p.suffix.lower()
    if ext not in CODE_EXTENSIONS:
        raise PixelForgeError(
            "not a code file: %s (need .py, .js, or .ts)" % path)
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PixelForgeError("could not read %s: %s" % (path, exc))
    language = "py" if ext == ".py" else "js"
    result = parse_source(source, p, language)
    result["files"] = [str(p)]
    return result


def parse_paths(paths):
    """Parse files and/or folders (a folder contributes its
    .py/.js/.ts files, non-recursive, sorted by name). Returns
    the merged result; raises PixelForgeError when nothing
    parseable was given."""
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
            "no .py, .js, or .ts files in %s"
            % ", ".join(str(p) for p in paths))
    merged = {"assets": [], "not_rendered": [], "files": []}
    for path in files:
        result = parse_file(path)
        merged["assets"].extend(result["assets"])
        merged["not_rendered"].extend(result["not_rendered"])
        merged["files"].extend(result["files"])
    return merged
