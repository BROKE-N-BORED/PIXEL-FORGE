"""Text stamping for sprites (task 47) — Qt-free, stdlib-only.

A name above a character, a number on a door, a shop sign:
text stamped onto a sprite, one line at a time, in the
sprite's own pixels.

Two font paths share one blit:

* The built-in pixel font — a 5 x 7 bitmap font covering
  printable ASCII (space through ~). It IS pixel art by
  construction: no antialiasing, no mush, every glyph drawn
  from whole pixels. Scale 1 puts a 5 x 7 letter on the
  sprite; scale 2-4 doubles, triples, and quadruples each
  font pixel. This is the default, and the only font the
  command line offers.
* A system font, rendered by the GUI (Qt's QFont, with
  antialiasing off and the alpha thresholded to a hard mask)
  and blitted through stamp_image — the same blit, so a
  system-font stamp is exactly as crisp as a pixel-font one.

The stamp paints opaque color over whatever was there:
text must read, so it never blends. Pixels outside the
sprite are clipped, never an error — a stamp half off the
edge lands the half that fits. A stamp that lands nothing
paints 0 pixels and the caller says so plainly instead of
recording an empty undo step.

Characters the pixel font does not know (anything outside
printable ASCII) stamp as a blank the width of a space, and
unsupported_characters names them so the GUI can say so.
Control characters — a line break included — are refused:
stamp one line at a time.
"""

from .image import Image, argb
from .pixelize import PixelForgeError

GLYPH_WIDTH = 5
GLYPH_HEIGHT = 7
GLYPH_ADVANCE = 6  # glyph width + 1 px of spacing
MIN_SCALE = 1
MAX_SCALE = 4

# One glyph per printable ASCII character (space through ~),
# 7 rows of 5 columns, "#" inked, "." empty. Capitals fill
# all 7 rows; lowercase letters sit on rows 2-6 with
# descenders (g, j, p, q, y) reaching row 6.
_GLYPHS = {
    " ": (".....",
          ".....",
          ".....",
          ".....",
          ".....",
          ".....",
          "....."),
    "!": ("..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          ".....",
          "..#.."),
    '"': (".#.#.",
          ".#.#.",
          ".#.#.",
          ".....",
          ".....",
          ".....",
          "....."),
    "#": (".#.#.",
          ".#.#.",
          "#####",
          ".#.#.",
          "#####",
          ".#.#.",
          ".#.#."),
    "$": ("..#..",
          ".####",
          "#.#.#",
          "#.###",
          "..#.#",
          ".####",
          "..#.."),
    "%": ("##..#",
          "##.#.",
          "...#.",
          "..#..",
          ".#...",
          "#.##.",
          "#.##."),
    "&": (".##..",
          "#..#.",
          "#.#..",
          ".#...",
          "#.#.#",
          "#..#.",
          ".##.#"),
    "'": ("..#..",
          "..#..",
          ".#...",
          ".....",
          ".....",
          ".....",
          "....."),
    "(": ("...#.",
          "..#..",
          ".#...",
          ".#...",
          ".#...",
          "..#..",
          "...#."),
    ")": (".#...",
          "..#..",
          "...#.",
          "...#.",
          "...#.",
          "..#..",
          ".#..."),
    "*": (".....",
          ".#.#.",
          "..#..",
          "#####",
          "..#..",
          ".#.#.",
          "....."),
    "+": (".....",
          "..#..",
          "..#..",
          "#####",
          "..#..",
          "..#..",
          "....."),
    ",": (".....",
          ".....",
          ".....",
          ".....",
          ".##..",
          ".##..",
          ".#..."),
    "-": (".....",
          ".....",
          ".....",
          "#####",
          ".....",
          ".....",
          "....."),
    ".": (".....",
          ".....",
          ".....",
          ".....",
          ".....",
          ".##..",
          ".##.."),
    "/": ("....#",
          "....#",
          "...#.",
          "..#..",
          ".#...",
          "#....",
          "#...."),
    "0": (".###.",
          "#...#",
          "#..##",
          "#.#.#",
          "##..#",
          "#...#",
          ".###."),
    "1": ("..#..",
          ".##..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          ".###."),
    "2": (".###.",
          "#...#",
          "....#",
          "...#.",
          "..#..",
          ".#...",
          "#####"),
    "3": ("####.",
          "....#",
          "....#",
          ".###.",
          "....#",
          "#...#",
          ".###."),
    "4": ("...#.",
          "..##.",
          ".#.#.",
          "#..#.",
          "#####",
          "...#.",
          "...#."),
    "5": ("#####",
          "#....",
          "####.",
          "....#",
          "....#",
          "#...#",
          ".###."),
    "6": (".###.",
          "#...#",
          "#....",
          "####.",
          "#...#",
          "#...#",
          ".###."),
    "7": ("#####",
          "....#",
          "...#.",
          "..#..",
          "..#..",
          "..#..",
          "..#.."),
    "8": (".###.",
          "#...#",
          "#...#",
          ".###.",
          "#...#",
          "#...#",
          ".###."),
    "9": (".###.",
          "#...#",
          "#...#",
          ".####",
          "....#",
          "#...#",
          ".###."),
    ":": (".....",
          ".##..",
          ".##..",
          ".....",
          ".##..",
          ".##..",
          "....."),
    ";": (".....",
          ".##..",
          ".##..",
          ".....",
          ".##..",
          ".##..",
          ".#..."),
    "<": ("...#.",
          "..#..",
          ".#...",
          "#....",
          ".#...",
          "..#..",
          "...#."),
    "=": (".....",
          ".....",
          "#####",
          ".....",
          "#####",
          ".....",
          "....."),
    ">": (".#...",
          "..#..",
          "...#.",
          "....#",
          "...#.",
          "..#..",
          ".#..."),
    "?": (".###.",
          "#...#",
          "....#",
          "...#.",
          "..#..",
          ".....",
          "..#.."),
    "@": (".###.",
          "#...#",
          "#.###",
          "#.#.#",
          "#.##.",
          "#....",
          ".###."),
    "A": (".###.",
          "#...#",
          "#...#",
          "#####",
          "#...#",
          "#...#",
          "#...#"),
    "B": ("####.",
          "#...#",
          "#...#",
          "####.",
          "#...#",
          "#...#",
          "####."),
    "C": (".###.",
          "#...#",
          "#....",
          "#....",
          "#....",
          "#...#",
          ".###."),
    "D": ("####.",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          "####."),
    "E": ("#####",
          "#....",
          "#....",
          "####.",
          "#....",
          "#....",
          "#####"),
    "F": ("#####",
          "#....",
          "#....",
          "####.",
          "#....",
          "#....",
          "#...."),
    "G": (".###.",
          "#...#",
          "#....",
          "#.###",
          "#...#",
          "#...#",
          ".###."),
    "H": ("#...#",
          "#...#",
          "#...#",
          "#####",
          "#...#",
          "#...#",
          "#...#"),
    "I": (".###.",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          ".###."),
    "J": ("..###",
          "....#",
          "....#",
          "....#",
          "#...#",
          "#...#",
          ".###."),
    "K": ("#...#",
          "#..#.",
          "#.#..",
          "##...",
          "#.#..",
          "#..#.",
          "#...#"),
    "L": ("#....",
          "#....",
          "#....",
          "#....",
          "#....",
          "#....",
          "#####"),
    "M": ("#...#",
          "##.##",
          "#.#.#",
          "#.#.#",
          "#...#",
          "#...#",
          "#...#"),
    "N": ("#...#",
          "##..#",
          "#.#.#",
          "#..##",
          "#...#",
          "#...#",
          "#...#"),
    "O": (".###.",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          ".###."),
    "P": ("####.",
          "#...#",
          "#...#",
          "####.",
          "#....",
          "#....",
          "#...."),
    "Q": (".###.",
          "#...#",
          "#...#",
          "#...#",
          "#.#.#",
          "#..#.",
          ".##.#"),
    "R": ("####.",
          "#...#",
          "#...#",
          "####.",
          "#.#..",
          "#..#.",
          "#...#"),
    "S": (".####",
          "#....",
          "#....",
          ".###.",
          "....#",
          "#...#",
          ".###."),
    "T": ("#####",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#.."),
    "U": ("#...#",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          ".###."),
    "V": ("#...#",
          "#...#",
          "#...#",
          "#...#",
          "#...#",
          ".#.#.",
          "..#.."),
    "W": ("#...#",
          "#...#",
          "#...#",
          "#.#.#",
          "#.#.#",
          "##.##",
          "#...#"),
    "X": ("#...#",
          "#...#",
          ".#.#.",
          "..#..",
          ".#.#.",
          "#...#",
          "#...#"),
    "Y": ("#...#",
          "#...#",
          ".#.#.",
          "..#..",
          "..#..",
          "..#..",
          "..#.."),
    "Z": ("#####",
          "....#",
          "...#.",
          "..#..",
          ".#...",
          "#....",
          "#####"),
    "[": (".###.",
          ".#...",
          ".#...",
          ".#...",
          ".#...",
          ".#...",
          ".###."),
    "\\": ("#....",
          "#....",
          ".#...",
          "..#..",
          "...#.",
          "....#",
          "....#"),
    "]": (".###.",
          "...#.",
          "...#.",
          "...#.",
          "...#.",
          "...#.",
          ".###."),
    "^": ("..#..",
          ".#.#.",
          ".....",
          ".....",
          ".....",
          ".....",
          "....."),
    "_": (".....",
          ".....",
          ".....",
          ".....",
          ".....",
          ".....",
          "#####"),
    "`": ("..#..",
          "..#..",
          ".#...",
          ".....",
          ".....",
          ".....",
          "....."),
    "a": (".....",
          ".....",
          ".####",
          "....#",
          ".####",
          "#...#",
          ".####"),
    "b": ("#....",
          "#....",
          "####.",
          "#...#",
          "#...#",
          "#...#",
          "####."),
    "c": (".....",
          ".....",
          ".###.",
          "#...#",
          "#....",
          "#...#",
          ".###."),
    "d": ("....#",
          "....#",
          ".####",
          "#...#",
          "#...#",
          "#...#",
          ".####"),
    "e": (".....",
          ".....",
          ".###.",
          "#...#",
          "#####",
          "#....",
          ".###."),
    "f": ("..##.",
          ".#..#",
          ".#...",
          "####.",
          ".#...",
          ".#...",
          ".#..."),
    "g": (".....",
          ".####",
          "#...#",
          "#...#",
          ".####",
          "....#",
          ".###."),
    "h": ("#....",
          "#....",
          "####.",
          "#...#",
          "#...#",
          "#...#",
          "#...#"),
    "i": ("..#..",
          ".....",
          ".##..",
          "..#..",
          "..#..",
          "..#..",
          ".###."),
    "j": ("...#.",
          ".....",
          "..##.",
          "...#.",
          "...#.",
          "...#.",
          ".##.."),
    "k": ("#....",
          "#....",
          "#..#.",
          "#.#..",
          "##...",
          "#.#..",
          "#..#."),
    "l": (".##..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          ".###."),
    "m": (".....",
          ".....",
          "##.#.",
          "#.#.#",
          "#.#.#",
          "#.#.#",
          "#.#.#"),
    "n": (".....",
          ".....",
          "####.",
          "#...#",
          "#...#",
          "#...#",
          "#...#"),
    "o": (".....",
          ".....",
          ".###.",
          "#...#",
          "#...#",
          "#...#",
          ".###."),
    "p": (".....",
          ".....",
          "####.",
          "#...#",
          "#...#",
          "####.",
          "#...."),
    "q": (".....",
          ".....",
          ".####",
          "#...#",
          "#...#",
          ".####",
          "....#"),
    "r": (".....",
          ".....",
          "#.##.",
          "##..#",
          "#....",
          "#....",
          "#...."),
    "s": (".....",
          ".....",
          ".####",
          "#....",
          ".###.",
          "....#",
          "####."),
    "t": (".#...",
          ".#...",
          "####.",
          ".#...",
          ".#...",
          ".#...",
          "..##."),
    "u": (".....",
          ".....",
          "#...#",
          "#...#",
          "#...#",
          "#..##",
          ".##.#"),
    "v": (".....",
          ".....",
          "#...#",
          "#...#",
          "#...#",
          ".#.#.",
          "..#.."),
    "w": (".....",
          ".....",
          "#...#",
          "#.#.#",
          "#.#.#",
          "#.#.#",
          ".#.#."),
    "x": (".....",
          ".....",
          "#...#",
          ".#.#.",
          "..#..",
          ".#.#.",
          "#...#"),
    "y": (".....",
          ".....",
          "#...#",
          "#...#",
          ".####",
          "....#",
          "####."),
    "z": (".....",
          ".....",
          "#####",
          "...#.",
          "..#..",
          ".#...",
          "#####"),
    "{": ("..##.",
          "..#..",
          "..#..",
          ".###.",
          "..#..",
          "..#..",
          "..##."),
    "|": ("..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#..",
          "..#.."),
    "}": (".##..",
          "..#..",
          "..#..",
          ".###.",
          "..#..",
          "..#..",
          ".##.."),
    "~": (".....",
          ".....",
          ".##.#",
          "#.##.",
          ".....",
          ".....",
          "....."),
}

_BLANK = _GLYPHS[" "]


def glyph_for(char):
    """The 7 rows of one glyph, or the blank glyph when the font
    does not know the character."""
    return _GLYPHS.get(char, _BLANK)


def unsupported_characters(text):
    """The characters in text the pixel font cannot draw, in
    first-appearance order — so the GUI can name them instead
    of stamping blanks silently."""
    seen = []
    for char in text:
        if char not in _GLYPHS and char not in seen:
            seen.append(char)
    return seen


def _check_text(text):
    """One line of text, or a plain refusal. Control
    characters — a line break included — cannot be stamped."""
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise PixelForgeError(
            "cannot stamp a line break or control character: "
            "stamp one line of text at a time")


def _check_scale(scale):
    """A whole-number scale from 1 to 4, or a plain refusal."""
    if isinstance(scale, bool) or not isinstance(scale, int):
        raise PixelForgeError(
            "scale must be a whole number from %d to %d"
            % (MIN_SCALE, MAX_SCALE))
    if scale < MIN_SCALE or scale > MAX_SCALE:
        raise PixelForgeError(
            "scale must be a whole number from %d to %d (got %r)"
            % (MIN_SCALE, MAX_SCALE, scale))


def measure_text(text, scale=1):
    """The size of the stamped text in pixels: (width, height).
    Width is one advance per character minus the trailing
    spacing; height is the 7 px glyph height. An empty text is
    0 wide and one glyph tall."""
    _check_text(text)
    _check_scale(scale)
    if not text:
        return (0, GLYPH_HEIGHT * scale)
    width = (len(text) * GLYPH_ADVANCE - 1) * scale
    return (width, GLYPH_HEIGHT * scale)


def render_text(text, scale=1):
    """The text as a mask Image: white opaque pixels where the
    ink goes, transparent everywhere else. The color is applied
    at stamp time, so one render serves any color."""
    _check_text(text)
    _check_scale(scale)
    width, height = measure_text(text, scale)
    mask = Image(width, height)
    ink = argb(255, 255, 255)
    for index, char in enumerate(text):
        glyph = glyph_for(char)
        base_x = index * GLYPH_ADVANCE * scale
        for row in range(GLYPH_HEIGHT):
            line = glyph[row]
            for col in range(GLYPH_WIDTH):
                if line[col] != "#":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        mask.setPixel(base_x + col * scale + dx,
                                      row * scale + dy, ink)
    return mask


def stamp_image(image, mask, x, y, color):
    """Blit a mask onto an image at (x, y), in color: every
    mask pixel with any opacity becomes the color, opaque.
    The mask is clipped to the image — a stamp half off the
    edge lands the half that fits. Returns the number of
    pixels painted (0 when the mask misses the image
    entirely). The image is changed in place."""
    if mask.isNull() or image.isNull():
        return 0
    left = max(0, x)
    top = max(0, y)
    right = min(image.width(), x + mask.width())
    bottom = min(image.height(), y + mask.height())
    if left >= right or top >= bottom:
        return 0
    painted = 0
    for row in range(top, bottom):
        for col in range(left, right):
            if (mask.pixel(col - x, row - y) >> 24) == 0:
                continue
            image.setPixel(col, row, color)
            painted += 1
    return painted


def stamp_text(image, text, x, y, color, scale=1):
    """Stamp one line of text onto an image in the pixel font,
    at (x, y), in color (a 0xAARRGGBB int). Returns the number
    of pixels painted. Empty text is refused — there is
    nothing to stamp."""
    if not text:
        raise PixelForgeError("nothing to stamp: type some text")
    mask = render_text(text, scale)
    return stamp_image(image, mask, x, y, color)


def parse_hex_color(text):
    """A typed hex color ("#rrggbb" or "rrggbb") as an (r, g, b)
    tuple, or None when it is not one."""
    text = text.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16),
                int(text[4:6], 16))
    except ValueError:
        return None
