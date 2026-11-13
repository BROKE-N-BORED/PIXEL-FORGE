"""Pixel editing operations — stdlib-only.

The raster work behind the Image tab's pixel editor (task 33):
flood fill, line and rectangle drawing, the distinct colors of a
result, and the eyedropper read. Pure functions over the core
buffer Image — no Qt here, so the suite runs them on bare python
and the GUI just wires them to mouse events. Colors are
0xAARRGGBB ints, the same values Image.pixel() returns.
"""


def distinct_colors(image):
    """The colors the image actually uses, as 0xAARRGGBB ints, in
    first-appearance order (row-major scan). Fully transparent
    pixels are not a color — the eraser paints transparency, and
    the editor's color strip shows drawing colors only."""
    seen = set()
    colors = []
    data = image.data
    for offset in range(0, len(data), 4):
        if data[offset + 3] == 0:
            continue
        value = ((data[offset + 3]) << 24) | (data[offset] << 16) | \
            (data[offset + 1] << 8) | data[offset + 2]
        if value not in seen:
            seen.add(value)
            colors.append(value)
    return colors


def pick_color(image, x, y):
    """The pixel at (x, y) as a 0xAARRGGBB int, or None when the
    point is outside the image."""
    if 0 <= x < image.width() and 0 <= y < image.height():
        return image.pixel(x, y)
    return None


def flood_fill(image, x, y, color):
    """Fill the connected region of the color at (x, y) with
    color. Returns how many pixels changed — 0 when the point is
    outside the image, or when the region already is that color
    (a no-op, not an undo step). Iterative, so a whole-image
    fill never blows the stack."""
    width, height = image.width(), image.height()
    if not (0 <= x < width and 0 <= y < height):
        return 0
    target = image.pixel(x, y)
    if target == color:
        return 0
    filled = 0
    stack = [(x, y)]
    while stack:
        px, py = stack.pop()
        if not (0 <= px < width and 0 <= py < height):
            continue
        if image.pixel(px, py) != target:
            continue
        image.setPixel(px, py, color)
        filled += 1
        stack.append((px + 1, py))
        stack.append((px - 1, py))
        stack.append((px, py + 1))
        stack.append((px, py - 1))
    return filled


def line_points(x0, y0, x1, y1):
    """The pixels of the line from (x0, y0) to (x1, y1),
    Bresenham — both endpoints included, no gaps on any slope."""
    points = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            return points
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def rect_points(x0, y0, x1, y1):
    """The pixels of the rectangle outline with corners (x0, y0)
    and (x1, y1) — outline only, in either drag direction. A
    one-pixel-wide rectangle is a line; a 1x1 rectangle is one
    pixel."""
    left, right = (x0, x1) if x0 <= x1 else (x1, x0)
    top, bottom = (y0, y1) if y0 <= y1 else (y1, y0)
    points = []
    for x in range(left, right + 1):
        points.append((x, top))
        if bottom != top:
            points.append((x, bottom))
    for y in range(top + 1, bottom):
        points.append((left, y))
        if right != left:
            points.append((right, y))
    return points


def draw_line(image, x0, y0, x1, y1, color):
    """Draw the line on the image, clipped to its edges."""
    width, height = image.width(), image.height()
    for x, y in line_points(x0, y0, x1, y1):
        if 0 <= x < width and 0 <= y < height:
            image.setPixel(x, y, color)


def draw_rect_outline(image, x0, y0, x1, y1, color):
    """Draw the rectangle outline on the image, clipped to its
    edges."""
    width, height = image.width(), image.height()
    for x, y in rect_points(x0, y0, x1, y1):
        if 0 <= x < width and 0 <= y < height:
            image.setPixel(x, y, color)
