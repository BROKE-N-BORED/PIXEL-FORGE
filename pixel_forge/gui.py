"""Pixel Forge window — native Qt GUI, dark theme.

Six tabs. Image: import a picture, pick a size and a palette, watch
the before and after side by side, export a transparent PNG.
Conversion is live: every control change re-pixelizes at once.
Video: import a clip, extract frames with ffmpeg, keep or drop frames
in the strip, watch the animation play, export the kept frames as
numbered PNGs — or pack them into a spritesheet PNG + metadata JSON
a game can play. Frames pixelize through the same Item 1 core.
Prompt: describe a sprite, generate 3 candidates with the local
image engine, click one to keep it, pixelize it with the same
controls, export a transparent PNG. Generation runs in a worker
thread — a minutes-long CPU run never freezes the window.
Code: import a .py / .js / .ts / .json / .cs / .gd / .lua file
(or a folder of them) and the pixel art the code defines — pixel
maps, palettes, single colors, sprite frames, image references —
is rebuilt as real sprites, parsed statically and never executed.
Or import a
game asset: a Minecraft skin splits into its parts, anything else
pixelizes as a texture. Export PNG and Save to library work like
the other tabs.
Library: every sprite saved from the other tabs, with tags and
search — click one to preview it, load it back into the Image tab,
rename it, delete it, or export a copy.
Tileset: build a Wang 16 or blob 47 autotile set from one or two
base tiles, and export it as a PNG, for Tiled (.tsx + demo .tmx),
or for Godot 4 (.tres).
Each of the Image, Video, and Prompt tabs also has a preset picker:
one pick sets size, palette, colors, dithering, and aspect together,
and the current controls can be saved as a named preset.

No bare "&" in any visible label text anywhere in this file — Qt eats
a bare & as a mnemonic marker and renders "_" in its place while the
underlying text() still returns "&", so a string check alone would
stay green on a wrong label. Write "and" instead.
Compatibility shim (task 58): gui.py was split
into per-tab modules. Everything the old module
exposed is re-exported here, so
`from pixel_forge.gui import ...` and
`pixel_forge.gui.<name>` keep working unchanged.
"""

import sys

from .gui_shared import *
from .gui_shared import _CardLift, _ControlChangeCommand, _IMAGE_EXTENSIONS, _KeepDropCommand, _LayerStackCommand, _LibraryDeleteCommand, _Motion, _MotionEffect, _PieMenu, _PixelCanvas, _PixelEditCommand, _PresetDeleteCommand, _parse_hex_color, _render_system_font
from .gui_core import MainWindow, main


if __name__ == "__main__":
    sys.exit(main())
