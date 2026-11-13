"""Layer compositing — stdlib-only.

The Image tab's layer stack (task 35): a result is a stack of
layers, each one an Image with a name and a visible flag, and
the preview, the export, and the library save all show the
stack composited bottom to top with Porter-Duff "over". Pure
functions over the core buffer Image — no Qt here, so the suite
runs them on bare python and the GUI just wires them to the
layer list.
"""

from .image import Image
from .pixelize import PixelForgeError


class Layer:
    """One layer of an Image tab result: a name, an Image the
    same size as the result, and a visible flag. Hidden layers
    contribute nothing to the composite. The GUI tracks which
    layer is active by object identity — a reorder moves the
    object in the list, never a copy of it, so an undo entry
    holding a layer still points at the same layer after a
    reorder."""

    def __init__(self, name, image, visible=True):
        self.name = name
        self.image = image
        self.visible = visible

    def __repr__(self):
        return "Layer(%r, %s, visible=%s)" % (
            self.name, self.image, self.visible)


def blend_over(base, over):
    """One pixel of Porter-Duff "over": the over color drawn
    on the base color, both 0xAARRGGBB ints. An opaque over
    pixel wins outright; a transparent one leaves the base
    alone; a half-transparent one mixes by its alpha. Two
    transparent pixels stay transparent."""
    over_a = (over >> 24) & 0xFF
    base_a = (base >> 24) & 0xFF
    weight_base = base_a * (255 - over_a) // 255
    alpha = over_a + weight_base
    if alpha == 0:
        return 0
    red = (((over >> 16) & 0xFF) * over_a
           + ((base >> 16) & 0xFF) * weight_base) // alpha
    green = (((over >> 8) & 0xFF) * over_a
             + ((base >> 8) & 0xFF) * weight_base) // alpha
    blue = ((over & 0xFF) * over_a
            + (base & 0xFF) * weight_base) // alpha
    return (alpha << 24) | (red << 16) | (green << 8) | blue


def composite_layers(layers):
    """Composite a layer stack bottom to top: layers[0] is the
    bottom, the last layer is the top. Hidden layers are
    skipped. Returns a new Image; no layer is touched. An
    empty stack composites to a transparent 0 x 0 image, and
    layers of different sizes are a plain error, never a
    crash."""
    if not layers:
        return Image(0, 0)
    width = layers[0].image.width()
    height = layers[0].image.height()
    for layer in layers[1:]:
        if (layer.image.width() != width
                or layer.image.height() != height):
            raise PixelForgeError(
                "layers must all be the same size (%d x %d and "
                "%d x %d)" % (width, height,
                             layer.image.width(),
                             layer.image.height()))
    out = Image(width, height)
    dst = out.data
    for layer in layers:
        if not layer.visible:
            continue
        src = layer.image.data
        for offset in range(0, len(src), 4):
            over_a = src[offset + 3]
            if over_a == 0:
                continue
            if over_a == 255:
                dst[offset:offset + 4] = src[offset:offset + 4]
                continue
            base_a = dst[offset + 3]
            weight_base = base_a * (255 - over_a) // 255
            alpha = over_a + weight_base
            dst[offset] = (src[offset] * over_a
                           + dst[offset] * weight_base) // alpha
            dst[offset + 1] = (src[offset + 1] * over_a
                               + dst[offset + 1] * weight_base) // alpha
            dst[offset + 2] = (src[offset + 2] * over_a
                               + dst[offset + 2] * weight_base) // alpha
            dst[offset + 3] = alpha
    return out


def has_content(image):
    """True when any pixel of the image is not fully
    transparent — the delete-layer confirm asks only for a
    layer with content; an empty layer deletes silently."""
    data = image.data
    for offset in range(3, len(data), 4):
        if data[offset]:
            return True
    return False
