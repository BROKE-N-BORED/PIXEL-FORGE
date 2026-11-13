# Pixel Forge

Turn images, videos, and prompts into pixel art and sprites — a
standalone Linux program. Import a picture, pick a size and a
palette, watch the before and after side by side, export a
transparent PNG ready to drop into a game. Import a video and turn
its frames into a run of pixel sprites, then pack them into a
spritesheet with metadata a game can play. Or describe a sprite and
generate it with a local image model, then pick the best of 3
candidates and pixelize it. Or feed it source code and rebuild the
pixel art the code defines, and load a Minecraft skin and split it
into its parts.

Everything runs locally on your machine — no accounts, no paid APIs,
no network at run time. Video import needs ffmpeg
(`sudo apt install ffmpeg`) — the installer offers to install it for
you, and image import works without it. Prompt import needs the
image engine and its model (about 2.1 GB) — the installer offers
those too, and everything else works without them.

## Install

Unzip the release, then double-click **Install Pixel Forge**. No
terminal needed — it sets up a private Python environment, puts Pixel
Forge in your app menu, and adds the `pixel-forge` and
`pixel-forge-gui` commands.

Terminal fallback, if you prefer it:

```sh
bash install.sh
```

To also install the image engine for prompt support (builds
stable-diffusion.cpp from source and downloads the 2.1 GB model —
a one-time job that takes a while):

```sh
bash install.sh --with-image-engine
```

The engine is a source build because the prebuilt binary needs a
newer system library than Mint ships. Building it needs git, cmake,
and a C++ compiler — if any are missing, the installer stops and
prints the exact `sudo apt install` line. The model is shared with
the Design Shell: if it is already in `~/design-shell-models/`, no
second download happens. When the engine is rebuilt, the old one
is moved to `/opt/sd.cpp.bak` first, never wiped — a failed build
leaves the working engine in place.

Both installers check for internet before anything that downloads
(the packages come from apt and pip, and the engine build and
model download need it too). An offline run stops with a plain
"you need internet" message instead of failing step by step. At
the end, the installer verifies the install (the venv python
runs, `pixel-forge --version` runs, the GUI imports) and prints
a summary of what worked and what did not, with the plain fix
for each failure. The venv package is detected from the system
Python's own version (`python3.<minor>-venv`), never hardcoded,
and a failed icon render is reported in the log and the summary
(the install continues and the menu entry is still created)
instead of leaving a silently icon-less menu entry.

To remove it later:

```sh
bash uninstall.sh
```

That removes the venv, the `pixel-forge` / `pixel-forge-gui`
commands, the menu entry, and the icons — and keeps your sprite
library, your image models, the image engine, and the install
folder (`~/.local/share/pixel-forge/`). To remove everything:

```sh
bash uninstall.sh --purge
```

`--purge` also removes the install folder and the image engine
(`/opt/sd.cpp`), and asks about the things that hold your data —
your sprite library (`~/pixel-forge-library/`) and your image
models (`~/design-shell-models/`, shared with the Design Shell,
and `~/pixel-forge-models/`) — one by one, default No, so
nothing of yours is deleted without asking. Without a terminal
it keeps them and prints the way to remove them by hand.

## Use

**Window:** open Pixel Forge from the menu, or run `pixel-forge-gui`.

- **Image tab:** import an image (or drop one on the window), adjust
  the size, palette, and dithering, and press Export PNG. Conversion
  is live — every change re-pixelizes at once. The pixel preview is
  also a canvas: pick a tool above it (Pencil, Eraser, Fill,
  Eyedropper, Line, Rectangle) and a color from the strip of swatches
  — one per color the sprite actually uses — and click or drag on
  the preview to fix pixels in place. Rectangles are outline only.
  Every edit is one undo step (Ctrl+Z / Ctrl+Y), the info line shows
  "(edited)" while the result carries edits, and Export PNG and Save
  to library write the edited sprite. A control change, a preset, or
  a new image load re-pixelizes from the source, so each one asks
  first before throwing the edits away (default No). A sprite with
  no opaque pixels has no colors to draw with — the strip stays
  empty until a result with colors loads. The result is a stack of
  layers: a fresh pixelize makes one base layer named "Background",
  and the Layers panel adds transparent layers on top ("Layer 2",
  "Layer 3", …), deletes them, moves them up and down, and toggles
  each one's visibility with a checkbox; double-click a name to
  rename it. The preview shows the layers composited bottom to top,
  a hidden layer contributing nothing, and Export PNG and Save to
  library write that composite — there is no layered file format,
  the library stores flat PNGs. The editor paints on the ACTIVE
  layer (click a layer to make it active); the eyedropper picks
  from the composite, and the color strip shows the composite's
  colors. The base layer is the active layer on load — it is the
  only layer, so this is the working layer, not a selection.
  Add, delete, and reorder are each one undo step; visibility
  toggles and renames are not undoable (view settings, like
  zoom). Deleting a layer asks first only when it has any
  non-transparent pixel, and the re-pixelize guard asks whenever
  the stack carries any edit — drawn pixels, an added layer, a
  reordered stack, or a deleted layer.
- **Video tab:** import a video or a GIF (or drop one on the window),
  pick how many frames per second to keep, then keep or drop frames
  in the strip, watch the animation play, and press Export frames to
  save the kept frames as numbered PNGs. Changing the size, palette,
  or dithering re-pixelizes every frame at once; changing the fps or
  time range re-extracts the frames. The tab shows how many frames
  the current settings would extract, and asks first when that
  number is over 500 — a long clip at a high fps would otherwise
  write tens of thousands of PNGs. Dropped frames show dimmed in
  the strip; Ctrl+click and Shift+click select several at once
  (Keep selected / Drop selected act on the selection), and frames
  drag to reorder — the export, the sheet, and the playback all
  follow the strip order. Every frame has its own duration in
  milliseconds: a new import starts each frame at 1000 / Sheet
  fps (125 ms at 8 fps), and the Frame duration spin box sets
  the current frame's duration (10–5000 ms) — a hold frame is
  just a long duration. Changing the Preview fps or the Sheet
  fps resets every frame to the new speed — either fps is the
  base speed, and the spin fine-tunes single frames on top of
  it. Durations follow their frames through
  reorder and keep/drop, and land in the sheet JSON and in GIF
  export. The scrubber under the preview jumps to any kept
  frame (dragging it pauses playback), and Onion skin ghosts
  the previous frame behind the current one at about 40% so
  the motion between frames is visible — preview only, exports
  use the plain frames. Animation tags name frame ranges in
  the kept order (walk/idle/attack sets in one sheet): Add a
  tag with a name and from/to frames, Play tag plays just that
  range (looping within it when Loop is on), Delete removes
  it. Tags are positions, so a reorder moves frames under a
  tag and a keep/drop shortens or drops tags that run out of
  frames; a new extraction clears them. The Spritesheet section
  packs the kept frames into one sheet PNG + a metadata JSON:
  pick the columns (Auto makes a square-ish grid), the padding
  between frames, and the playback fps, then Preview spritesheet
  to see the sheet and play the animation from it — at the
  frames' own durations — before you export, or Export
  spritesheet to write the pair straight away. Split spritesheet
  goes the other way: pick a sheet PNG and it comes back as
  frames in the strip — with its metadata JSON (durations and
  tags included) when one sits next to the sheet, or as a grid
  you describe with the frame size or the column and row counts,
  with a live frame count before you split. Split frames are
  finished pixel art, so the size and palette controls do not
  re-pixelize them. The Packing
  combo switches the packer: Grid is the default (same-size
  frames in rows), MaxRects packs mixed sizes tight into an
  atlas, with Trim (cut transparent borders), Atlas padding,
  and Extrude (repeat edge pixels into the padding, so the
  atlas does not bleed when scaled) — see "Atlas packing"
  below. Export GIF
  writes the kept frames (or the selected tag's range) as an
  animated GIF with the per-frame durations, looping forever;
  it needs ffmpeg, the same as video import. Export for engine
  writes the kept frames in a game engine's format instead —
  Aseprite JSON, TexturePacker JSON, Godot, Unity, GameMaker,
  Phaser, or Pixi (see "Engine-ready export" below).
- **Prompt tab:** type what you want (and what to avoid), press
  Generate, and 3 candidates appear in the strip — each takes a
  few minutes on CPU, and the window stays usable while they
  generate (the progress bar shows the elapsed time). Click a
  candidate to keep it, adjust the size, palette, and dithering,
  and press Export PNG. The status line shows whether the image
  engine and model are installed; if either is missing, Generate
  says plainly what to install instead of faking anything. If the
  model found is not the pinned LCM file, Generate asks before
  using it, and if less than 3 GB of memory is free it asks
  before starting a ~2.3 GB generation — both default to No, and
  declining leaves the tab untouched. A candidate that fails to
  pixelize is skipped with its reason recorded in batch.json
  (and named in the tab's info line), instead of aborting the
  whole batch. Generating again asks first, and the previous
  batch stays in History — past batches are listed under the
  Generation section and click to reopen, with the keeper's seed
  shown under the candidate preview. Regenerate one re-rolls a single candidate;
  More like this one generates a new batch seeded from the keeper
  (keeper seed +1, +2, +3). The Generation section sets the steps,
  CFG scale, and generated image size (LCM defaults: 4 steps, cfg
  1.5, 512 px); the Negative prompt has presets (Default, No text
  or watermark, Clean background, No people) and the Prompt history
  combo offers past prompts back. Batches live in
  `~/pixel-forge-library/batches/` and the history in
  `prompt-history.json` in the same folder.
- **Code tab:** import a `.py`, `.js`, or `.ts` file (or a whole
  folder of them) and the pixel art the code defines is rebuilt as
  real sprites — pixel maps and palettes, parsed statically and
  never executed, so a file cannot run anything when you import
  it. Click an asset to preview it, Export PNG, or Save to
  library; Export all writes every asset in the list to a
  folder, one PNG per asset. Anything the code defines that cannot be rendered
  (art built by loops or function calls, draw-call specs) is
  listed under "Found but not rendered" with the reason. Import
  game asset loads a Minecraft skin (64x64 or 64x32) and splits
  it into its parts — head, body, arms, legs, overlays — plus the
  combined figure; Export parts writes one PNG per part. Any
  other image pixelizes as a texture instead, and says so.
  A texture bigger than 512 px on its longest side is
  downscaled to fit that cap first (the info line says so),
  and loads on a worker thread, so a big photo never freezes
  the window. A texture gets Size, Palette, Max colors, and
  Dithering controls (a fresh import starts at native size,
  256 colors, no dithering), and a skin-sized image that is
  not really a skin can be treated as a texture instead with
  the "Treat as texture" button — "Treat as skin" goes back
  to the split view.
- **Library tab:** every sprite you save from the other tabs, with
  tags and search. Save to library on the Image tab (current
  result), the Prompt tab (pixelized keeper), or the Code tab
  (selected asset), or Save sheet to library on the Video tab
  (the whole spritesheet PNG + JSON pair as one entry) — each
  save asks for a name and optional tags.
  In the Library tab, search by name or tag, filter by tag, kind,
  source, or collection, and sort by saved order, name, date, or
  size. Click a sprite to preview it big, Load into Image tab to
  rework it (a sheet asks which frame to load), Rename it, Delete
  it (the file goes too), Export a copy elsewhere, or Edit tags
  to change its tags and collection. Sheets show a "Sheet" badge
  in the grid; entries whose file was deleted outside the app
  show "Missing", and Repair missing drops those stale entries
  after asking. Find duplicates lists sprites with identical
  files and removes the extras, keeping the first of each group.
  Export library backs the whole library up to a folder, and
  Import library brings one back (entries already in the library
  are skipped, never overwritten).
- **Tileset tab:** build a Wang 16 or blob 47 autotile set from
  one or two base tiles, and export it for Tiled or Godot 4.
  Pick a mode (Wang 16 / Blob 47 — nothing is picked by
  default), choose the base tiles (one for blob, two same-size
  tiles for Wang — the second picker only shows in Wang mode),
  and press Generate tileset. The set shows in a grid preview
  with a count line ("16 tiles, 32 x 32 each"), a zoom slider
  with Fit, and export buttons: Export PNG for the tileset
  image alone, Export for Tiled (PNG + a .tsx tileset + a demo
  .tmx map), Export for Godot (PNG + a .tres TileSet), and Save
  to library for the tileset PNG. A mode change or a new base
  tile clears the generated set — it was built from the old
  ones. Dropping a single image on the window with the Tileset
  tab current loads it as base tile A. The bitmask conventions,
  the quadrant construction, and what each export contains are
  in the "Tileset / autotile tools" section below.
- **Presets:** the Image, Video, and Prompt tabs each have a
  Preset picker. A preset is size + palette + max colors +
  dithering + keep-aspect in one pick — Game Boy 32, PICO-8 64,
  and Detailed 64 ship built in, and Save current as preset
  stores whatever the controls are set to right now under a name
  you choose. Changing a control by hand drops the picker back
  to Custom. Delete preset removes a saved preset (built-ins
  cannot be deleted).
- **Undo:** the Edit menu has Undo (Ctrl+Z) and Redo (Ctrl+Y
  and Ctrl+Shift+Z), each named after the step it will do.
  Five kinds of change are undoable: library deletes, preset
  deletes, keep/drop choices on the Video strip, control
  changes on the Image, Video, and Prompt tabs (size, palette,
  colors, dithering, and the preset selection — applying a
  preset is one step), and pixel edits on the Image tab's
  result (one drag, fill, line, or rectangle per step). A library delete moves the sprite's
  files to a `trash/` folder in the library folder instead of
  removing them, so undo brings the entry and its files back;
  the delete becomes permanent when the undo entry leaves the
  stack (it holds 50 entries) or the window closes. Undo is
  per-window-session only — nothing is kept across restarts,
  and the Video tab's fps and sheet controls are not undone.
  The command line has no undo: `library remove` still deletes
  permanently.
- **Shortcuts:** Enter in the prompt field generates; Ctrl+O
  imports for the current tab and Ctrl+S exports from it
  (nothing happens on a tab with nothing to import or export);
  Ctrl+1..5 switch tabs in order. The File menu mirrors Import
  and Export, and the Help menu has About, What the Code tab
  detects, and Library location (with an Open folder button).
  Every control has a one-line tooltip — hover to see what it
  does.
- **Polish:** preview zoom fits the pane when something loads,
  with a Fit button beside every zoom slider; the status bar
  says what each tab is for when you switch to it; error
  dialogs say what failed in their title; the prompt field has
  a character counter ("N / 1000"); exports default to a
  filename next to the source file, or the last-used export
  folder when there is no source; and the window title shows
  the version.

**Command line:**

```sh
pixel-forge convert photo.png --size 32 --colors 16 --palette pico8 --out sprite.png
pixel-forge convert photo.png --size 64x48 --keep-aspect --no-dither
pixel-forge convert photos/ --size 32 --out sprites/
pixel-forge frames clip.mp4 --fps 8 --out frames/
pixel-forge frames clip.mp4 --fps 12 --every 2 --start 1.5 --end 10 --size 48
pixel-forge prompt "a knight with a red shield" --out knight/
pixel-forge prompt "a slime" --count 3 --size 32 --colors 16 --palette pico8
pixel-forge sheet walk-frames/ --out walk-sheet.png --meta walk-sheet.json
pixel-forge sheet walk-frames/ --cols 4 --padding 2 --fps 12
pixel-forge split-sheet walk-sheet.png --out walk-frames/
pixel-forge split-sheet bare-grid.png --grid 32x32 --out frames/
pixel-forge split-sheet bare-grid.png --cols 4 --rows 3 --out frames/
pixel-forge library list
pixel-forge library list --tag hero
pixel-forge library add sprite.png --name "Red Knight" --tags hero,red
pixel-forge library remove "Red Knight"
pixel-forge library rename "Red Knight" "Sir Knight"
pixel-forge library export ~/pixel-forge-backup/
pixel-forge library import ~/pixel-forge-backup/
pixel-forge presets list
pixel-forge presets save "My Knight Look" --size 32 --palette pico8 --colors 16
pixel-forge presets delete "My Knight Look"
pixel-forge presets export "My Knight Look" --out knight-look.json
pixel-forge presets import knight-look.json
pixel-forge code game.py --list
pixel-forge code game.py --asset HERO --out hero.png
pixel-forge code game.py --all --out game-sprites/
pixel-forge code src/ --list
pixel-forge skin steve.png --out steve-parts/
pixel-forge tileset --wang grass.png water.png --out grass-tileset/
pixel-forge tileset --blob grass.png --out grass-tileset/ --export tmx --map-size 12
pixel-forge --help
```

Two flags work on every command, before or after it, for
scripting:

- `--json` — machine-readable JSON instead of human lines.
  The list commands emit arrays of objects with the same
  fields the human output shows (`library list` gives the
  stored entries, including each sprite's `id`; `presets
  list` gives name, whether it is built-in, and settings;
  `code --list` gives the assets and the found-but-not-
  rendered notes). The other commands emit small result
  objects, e.g. `{"converted": [...], "failed": [...]}` for
  a folder convert.
- `--quiet` — no info lines on stdout. Errors still print on
  stderr and exit codes are unchanged. `--json` output is
  never suppressed by `--quiet`.

Batch rules: a folder convert and `code --all` report a bad
file or asset on stderr and keep going; they exit 1 only
when nothing converted or rendered at all. `library remove`
takes a name, or an id when several sprites share a name
(`library list --json` shows the ids). `presets save`
refuses when the name already exists unless `--force` is
given. The CLI never prints a raw traceback — an unexpected
error prints one plain line on stderr with exit 1.

Options for `prompt` only:

- `--negative` — things the picture should avoid (default: blurry,
  deformed, distorted, low quality, watermark).
- `--count` — how many candidates to generate (default 3).
- `--steps` — diffusion steps per candidate (default 4, the LCM pin).
- `--cfg` — cfg scale, how closely the picture follows the prompt
  (default 1.5, the LCM pin).
- `--gen-size` — generated image size in pixels, square (default 512).
  `--size` on this command is the pixelized output size, not this.
- `--out` — output folder for the candidates, their pixelized
  versions (`cand-01-pixel.png`, ...), and `batch.json`, which
  records the prompt, negative, seeds, steps, cfg, and size so a
  keeper is reproducible (default: `<prompt-slug>-prompt` in the
  current folder).

Options for `convert` (and the pixelize half of `frames` and
`prompt`):

- `--size` — output size: a number (`32`) or `WIDTHxHEIGHT` (`64x48`).
  Presets in the window: 16, 32, 48, 64, 128, plus custom sizes.
- `--keep-aspect` — fit inside the size box instead of stretching to it.
- `--colors` — max colors, 2–256 (default 16).
- `--palette` — `image` (built from the picture), `pico8` (16 colors),
  `gameboy` (4 shades), `grayscale` (16 shades), `resurrect64`
  (64 colors), `endesga32` (32), `sweetie16` (16), or the name
  of a palette you saved (default: `image`).
- `--dither` / `--no-dither` — ordered (Bayer) dithering (default on).
- `--out` — output PNG (default: `<input-stem>-pixel.png` next to the
  input).

## Code input: what is detected

The Code tab and `pixel-forge code` read source code statically —
nothing is ever executed, so importing a file cannot run anything.
What they find, all as literal data in the file:

- **Pixel maps** — a variable holding rows of pixels: int rows
  (`[[0,1,1,0],...]`, where 0 is transparent and 1 is the first
  color of a palette from the same file, or PICO-8 colors by
  default), hex
  rows (`[["#ff0000", None],...]`), RGB rows (`[[(255,0,0)],...]`),
  and character rows (`["....",".XX.",...]`) with a dict in the
  same file mapping each character to a color (`.` and space are
  always transparent). Maps are capped at 512 x 512 and rows must
  all be the same length.
- **Palettes** — a flat list of colors (hex strings, RGB tuples,
  named colors like `red` or `gold`) or a dict mapping names, ints,
  or characters to colors. Rendered as a strip, one block per color.
- **Draw-call specs** — lists of dicts with rectangle keys (`x`,
  `y`, `w`, `h`, `color`) are recognized and reported as not
  rendered: they describe drawing steps, not pixels.

Not detected: anything computed (function calls, loops, arithmetic,
comprehensions), sprites loaded from image files at run time, and
sprites drawn through a game engine's API or Three.js / WebGL
geometry. Python detection looks at assignments anywhere in the
file; JS/TS detection is a textual scan (a regex literal containing
`//` may confuse its comment stripping).

Transparency is preserved: transparent pixels stay transparent and
never enter the palette; other pixels keep their original alpha.

Options for `frames` only:

- `--fps` — frames per second to keep (default 8).
- `--every` — keep every Nth frame (default 1).
- `--start` / `--end` — time range: seconds (`12.5`) or `MM:SS`
  (`1:30`).
- `--out` — output folder for `frame_001.png`, ... (default:
  `<video-stem>-frames` next to the video).

Options for `sheet` only:

- `--out` — output sheet PNG (default:
  `<folder-stem>-sheet.png` next to the folder).
- `--meta` — output metadata JSON (default: the `--out` name with
  `.json`).
- `--pack` — packer: `grid` (same-size frames in a grid, the
  default) or `maxrects` (mixed sizes, packed tight — see
  "Atlas packing" below).
- `--cols` — columns in the grid, 0 = automatic square-ish grid
  (default 0; grid only).
- `--padding` — gap between frames in pixels (default 0 for
  grid, 1 for maxrects; 0-64 under maxrects).
- `--trim` — cut transparent borders off each frame before
  packing (maxrects only).
- `--extrude` — repeat each sprite's edge pixels outward by N
  pixels into the padding (maxrects only, 0-16, never more
  than the padding).
- `--max-size` — largest side of one atlas sheet in pixels
  (maxrects only, 64-4096, default 1024).
- `--multipack` — spill to more sheets (`stem_0.png`,
  `stem_1.png`, ...) when the sprites do not fit in one sheet
  (maxrects only).
- `--pivot` — pivot for every frame, X,Y between 0 and 1
  (maxrects only, default 0.5,0.5).
- `--slice` — 9-slice borders for every frame, L,T,R,B in
  pixels (maxrects only).
- `--fps` — playback fps recorded in the metadata (default 8).

Under the grid packer all frames in a folder must be the same
size; a folder that mixes sizes (or holds no PNGs) fails with a
plain message. Under maxrects the frames may be any size — that
is what the packer is for.

## Spritesheet metadata format

Every spritesheet ships as a PNG + a JSON file with the same stem.
The JSON is Pixel Forge's own format (`pixel-forge-spritesheet`,
version 1) — a game reads it, cuts the frames out of the PNG, and
plays them at the given durations. Nothing to guess:

```json
{
  "format": "pixel-forge-spritesheet",
  "version": 1,
  "sheet": "walk-sheet.png",
  "sheet_width": 96,
  "sheet_height": 64,
  "frame_width": 32,
  "frame_height": 32,
  "columns": 3,
  "rows": 2,
  "padding": 0,
  "fps": 8,
  "frames": [
    {"name": "frame_001.png", "index": 0, "x": 0, "y": 0,
     "w": 32, "h": 32, "duration_ms": 125},
    {"name": "frame_002.png", "index": 1, "x": 32, "y": 0,
     "w": 32, "h": 32, "duration_ms": 500}
  ],
  "tags": [
    {"name": "walk", "from": 0, "to": 1}
  ]
}
```

Rules the format keeps: frames run left to right, top to bottom, in
playback order; `x` and `y` are the frame's top-left corner in the
PNG; padding is the gap *between* frames only, so frame 0 always
starts at (0, 0); every frame's `duration_ms` is its own duration
in milliseconds — 1000 / fps rounded to the nearest millisecond
when every frame shares the sheet fps, or the per-frame duration
set on the Video tab when they differ; the sheet background, the
padding gaps, and any unused cells in a partial last row are all
transparent. `tags` is optional and only present when the sheet
has animation tags: each tag is a name plus `from`/`to`, 0-based
positions into the frames list in playback order, naming a set
(walk/idle/attack) inside the one sheet. Names are unique, and
`from` never comes after `to`. Older metadata without `tags`
loads unchanged. A sheet + JSON pair loads back into the Video
tab with Split spritesheet (or `pixel-forge split-sheet` on
the command line) — the frames come back pixel-identical with
their durations and tags. A sheet with no metadata splits as a
grid instead: give the frame size or the column and row counts,
plus spacing and margin when the sheet has them, and the split
refuses plainly when the numbers do not divide the sheet
evenly.

## Atlas packing

The other packer: MaxRects bin-packing (Best Short Side Fit)
for sprites of different sizes, in the Video tab's Spritesheet
section (Packing combo) and on the command line
(`pixel-forge sheet DIR --pack maxrects`). It packs sprites
largest first into a sheet that grows to fit, up to
`--max-size` (default 1024), and crops the sheet to the
sprites it holds. Sprites are never rotated — rotating pixel
art changes its look, so a sprite that fits only sideways does
not fit. A sprite bigger than the cap refuses plainly naming
the sprite and the cap.

- **Trim** cuts each sprite to the bounding box of its
  non-transparent pixels and records the cut insets, so a game
  can put the sprite back where it was. A fully transparent
  sprite trims to a 1 x 1 transparent tile and keeps its
  placement — the frame count never changes silently.
- **Padding** is a ring kept around every sprite (default 1 px,
  the atlas convention): the gap between two sprites is twice
  the padding, and the sheet edge carries one padding ring.
- **Extrude** repeats each sprite's edge pixels outward into
  its own padding ring (default 0), the standard fix for
  bleeding when the atlas is scaled. It can never reach another
  sprite — extrude larger than the padding is refused plainly.
- **Multipack** spills to more sheets (`stem_0.png`,
  `stem_1.png`, ...) when the sprites do not fit in one sheet
  at the cap; every sprite is placed exactly once, on exactly
  one sheet. Without it the packer refuses plainly naming the
  count that did not fit and the cap.
- **Dedupe** packs byte-identical sprites once (on the trimmed
  pixels when trim is on); the other names become aliases of
  the same rect, and the export reports the count. An aliased
  frame's duration is the first occurrence's duration — the
  atlas stores one rect per unique sprite, so duplicate frames
  share the first one's timing.
- **Pivots** (default center 0.5, 0.5) and optional **9-slice**
  borders ride in the metadata, per sprite. The packer records
  9-slice borders; it does not render 9-slice.

The metadata format is new — `pixel-forge-atlas`, version 1 —
and does not reuse the spritesheet format:

```json
{
  "format": "pixel-forge-atlas",
  "version": 1,
  "packer": "maxrects-bssf",
  "max_size": 1024,
  "padding": 1,
  "extrude": 0,
  "trim": false,
  "deduped": 1,
  "fps": 8,
  "sheets": [
    {"file": "walk-atlas.png", "width": 96, "height": 64}
  ],
  "frames": [
    {"name": "frame_001.png", "index": 0, "sheet": 0,
     "x": 1, "y": 1, "w": 32, "h": 32,
     "source_w": 32, "source_h": 32,
     "trimmed": {"left": 0, "top": 0, "right": 0, "bottom": 0},
     "pivot": [0.5, 0.5], "slice": null,
     "aliases": ["frame_003.png"], "duration_ms": 125}
  ]
}
```

Rules the format keeps: frames run in input order, so animation
frames stay in playback order; `x` and `y` are the sprite's
top-left corner in its sheet (the `sheet` index says which PNG);
`w` and `h` are the packed size — the trimmed size when trim is
on, the source size otherwise — and `source_w`/`source_h` plus
the `trimmed` insets say where the sprite sat in its source;
`pivot` is the anchor point as fractions of the sprite (0.5, 0.5
is the center); `slice` is the 9-slice borders `[left, top,
right, bottom]` in pixels, or null; `aliases` names the sprites
that share this rect. Durations and animation tags ride along
from the Video tab, like the grid format. The atlas is an export
format — there is no atlas loader, and the library still stores
grid sheets.

## Engine-ready export

The Video tab's Export for engine button (and
`pixel-forge sheet DIR --format <preset>` on the command line)
writes the kept frames in a game engine's format instead of
Pixel Forge's own. The export carries the frames, their
per-frame durations, and the animation tags — except where the
format has no place for them, which is stated for each preset
below, never hidden. The command line reads a folder of PNGs,
which carries no durations or tags, so every frame there gets
the uniform fps duration.

- **Aseprite JSON** — sheet PNG + JSON in Aseprite's hash
  format: frames keyed by filename, each with its rect and its
  own duration in ms; meta with the PNG name, sheet size, and
  frameTags. Durations and tags are both carried.
- **TexturePacker JSON** — sheet PNG + JSON in TexturePacker's
  format: the same frame entries without durations, meta with
  a smartupdate content hash of the PNG. The format has no
  place for frame durations or animation tags, so they are
  dropped.
- **Godot** — sheet PNG + a JSON atlas in Aseprite's shape
  (frames, durations, frameTags). Set the texture Filter to
  Nearest on import, or the pixels blur. Godot has no
  built-in JSON-atlas import — the atlas is for script-driven
  use, and the .aseprite file from the Unity preset is what
  Godot's Aseprite Wizard reads.
- **Unity (.aseprite)** — a minimal .aseprite file plus the
  sheet PNG. The file follows the public .aseprite spec: one
  layer, one compressed cel per frame, a palette chunk, and a
  tags chunk carrying the animation tags. Unity's 2D Aseprite
  Importer reads it. Frame counts, sizes, and durations live
  in 16-bit fields, so anything over 65535 fails with a plain
  error instead of wrapping silently.
- **GameMaker strip** — one horizontal strip named `_stripN`,
  all frames in one row, the frame count in the name. No JSON,
  no durations, no tags; GameMaker reads the strip from the
  name.
- **Phaser JSON** and **Pixi JSON** — TexturePacker JSON, which
  Phaser's atlas loader and Pixi's Spritesheet both read, with
  the meta app naming the engine. Same drops as TexturePacker:
  no durations, no tags.

Verification: the exports are checked structurally against the
public specs — the .aseprite file is parsed back by a minimal
reader in the test suite (magic number, frame count, durations,
chunk types, tags, palette, and cel pixels round-tripping to
the source frames). No Aseprite binary, Godot, or Unity runs
in the build sandbox, so opening the exports in the real
engines is a real-PC check, not something proven here.

## Tileset / autotile tools

The Tileset tab (and `pixel-forge tileset` on the command line)
builds an autotile set — the set of tiles a map editor needs to
paint terrain that connects to itself. Two kinds:

- **Wang 16** — from two same-size base tiles: terrain A and
  terrain B. The 16 tiles cover every combination of the 4
  edges, each edge showing A or B. Tile index = the 4-bit edge
  mask: bit 0 = north, 1 = east, 2 = south, 3 = west, a set bit
  = terrain B. Tile 0 is all A, tile 15 is all B. Each tile is
  built quadrant by quadrant from the two base tiles: a quadrant
  whose two meeting edges agree takes that terrain's quadrant;
  a quadrant whose edges differ gets a diagonal split — half A,
  half B along the corner diagonal. The base tiles must be the
  same size; mismatched sizes refuse plainly.
- **Blob 47** — from one base tile: the terrain, drawn against
  transparency. The 47 tiles cover every combination of 4 edge
  neighbors + 4 corner neighbors, minus the impossible cases —
  a corner neighbor with neither adjacent edge neighbor can
  never show. Neighbor bits run clockwise from north: bit 0 =
  N, 1 = NE, 2 = E, 3 = SE, 4 = S, 5 = SW, 6 = W, 7 = NW. The
  47 valid masks, sorted ascending, are the tile indices: mask
  0 is tile 0, mask 255 is tile 46. Each quadrant shows the base
  tile's quadrant when its corner's neighbors are present, is
  cut with a diagonal when the corner neighbor is missing but
  both edges are present, shows the half next to the edge when
  exactly one edge is present, and is transparent when no
  adjacent neighbor is present. The all-neighbors tile (mask
  255) is the base tile unchanged; the no-neighbors tile (mask
  0) is fully transparent — so an isolated painted cell shows
  nothing, and you need at least two adjacent cells to see
  terrain. This follows the task's construction, which differs
  from the classic sub-blob one (where a no-edge quadrant is an
  outer curve and an isolated cell renders as a diamond); if
  the rounded variant is wanted later it is a small follow-up.

The tileset PNG packs the tiles in a grid (4x4 for Wang, 7x7
for blob at the default auto columns) with no padding, in tile
index order, top-left to bottom-right.

**Exports** (all written next to each other, named from the
first base tile — a Wang set from `grass.png` + `water.png`
exports as `grass.png`, `grass.tsx`, `grass-demo.tmx`,
`grass.tres`):

- **PNG** — the tileset image alone.
- **Tiled (.tsx)** — a tileset file referencing the PNG, with
  tilewidth, tileheight, tilecount, columns, and a Wang set:
  two terrains (Terrain A / Terrain B) for Wang, one (Terrain)
  for blob, plus one wangtile per tile carrying its Wang ID in
  Tiled's clockwise order (top edge, top-right corner, right
  edge, bottom-right corner, bottom edge, bottom-left corner,
  left edge, top-left corner). The blob mask bits are that same
  clockwise order, so a blob tile's Wang ID is its mask. A Wang
  tile's corner takes the next edge clockwise, since a
  diagonal-split corner is half A half B and Tiled wants one
  color per corner.
- **Tiled demo map (.tmx)** — a minimal valid map (default
  10x10, one tile layer, CSV layer data) referencing the .tsx,
  showing the set in use. In blob mode a terrain rectangle sits
  in the middle of an empty map, each cell's tile picked from
  its 8 neighbors (out-of-bounds counts as absent); in Wang
  mode the whole map is filled — terrain A background with a
  terrain B rectangle — each cell's tile picked from its 4 edge
  neighbors' terrains. The demo map is named `<stem>-demo.tmx`
  so it is clear it is a demo, not a real map.
- **Godot 4 (.tres)** — a TileSet resource in Godot 4's text
  format: an ext_resource for the PNG (`res://<png-name>` —
  put the .tres and the PNG in the same folder in the Godot
  project), a TileSetAtlasSource with the tile size and one
  tile per atlas cell, and a terrain set in
  match-corners-and-sides mode with the terrains plus per-tile
  terrain bits matching each tile's mask. A blob tile's center
  terrain is the terrain, except the mask-0 tile (no terrain);
  a Wang tile's center terrain is the majority terrain of its
  pixels, a tie going to terrain A. No uids — Godot assigns
  one on import.

**Command line:** `pixel-forge tileset --wang A.png B.png
--out DIR` or `pixel-forge tileset --blob TILE.png --out DIR`,
plus `--export {png,tsx,tmx,tres,all}` (default all — the PNG
always comes along, and the .tmx brings its .tsx) and
`--map-size N` (default 10, the demo .tmx size). `--wang` and
`--blob` together is an error. `--json` reports the mode, the
written files, the tile count, and the export kind.

Verification: the exports are checked structurally — the .tsx
and .tmx parse back through xml.etree in the tests (tilecount,
columns, wangset, 16 or 47 wangtiles with the right IDs; map
size, tileset ref, layer CSV decoding to the expected GIDs),
and the .tres passes text checks (resource type, texture path,
tile size, terrain set, per-tile terrain bits matching the
masks) plus a bracket-balance check. No Tiled or Godot runs in
the build sandbox, so opening the exports in the real editors
is a real-PC check, not something proven here.

## Palettes

Every palette picker — Image, Video, Prompt, and the Code tab's
texture section — lists the same palettes: "From image" (built
from the picture by median-cut), then the built-ins, then your
saved palettes after a separator. The built-ins are PICO-8 (16
colors), Game Boy (4 shades), Grayscale (16 shades), and three
popular palettes bundled from Lospec: Resurrect 64 (64 colors),
Endesga 32 (32), Sweetie 16 (16).

**Import.** "Palettes..." on the Image tab, next to the palette
picker, opens the palette editor; its Import button reads five
formats:

- `.hex` — Lospec: one hex color per line, with or without a
  leading `#` (blank lines skipped).
- `.gpl` — GIMP: header lines (`GIMP Palette`, `Name:`, ...),
  then `R G B [name]` lines.
- `.pal` — JASC-PAL: `JASC-PAL` + version + count lines, then
  `R G B` lines.
- `.ase` — Adobe Swatch Exchange, binary: RGB entries are
  imported; CMYK, LAB, and GRAY entries are skipped, and a
  file with no RGB colors refuses plainly.
- `.txt` — Lospec text: one `RRGGBB` per line, optionally
  followed by a name.

A malformed file refuses with a plain message naming the file
and what was wrong, instead of silently dropping colors. A
palette needs at least 1 color and at most 256 (the pixelizer's
max). Duplicate colors in a file are kept as-is — the file is
the authority.

**Editor.** The dialog lists every palette with a swatch strip
each, opens with no palette picked, and from it you can create
a palette (from empty or from an existing palette), edit a
saved palette (add a color via the color picker, remove a
color, move a color up or down, edit a hex value by typing
it), rename it, delete it (Yes/No, default No), export it to
`.hex` or `.gpl`, and duplicate it. Built-ins are read-only —
duplicate one to edit it. Saved palettes live in
`palettes.json` in the library folder, like presets; one bad
entry is skipped with a warning instead of dropping them all,
and a name that collides with a built-in refuses plainly.

**Extract palette.** "Extract palette..." on the Image tab
runs the median-cut at 16 colors on the current image and
opens the editor's create flow pre-filled with those colors
and a suggested name (the image's stem + " palette"). It works
from the loaded image only — disabled with no image.

**Perceptual matching.** When a pixel is matched to its
nearest palette color, the distance is the redmean
approximation, not plain Euclidean distance: with
rmean = (r1+r2)/2, red is weighted by (2 + rmean/256), green
by 4, and blue by (2 + (255-rmean)/256) — so a bright green
maps to a light gray rather than a mid gray, which is the
closer match in brightness. Pixelize output may shift slightly
from v0.48 where two palette entries were near-tied; that is
the point of the change, and every moved case was verified by
hand to be the perceptually closer color.

The CLI mirrors the editor: `pixel-forge palettes list`,
`import FILE [--name NAME]`, `export NAME --out FILE` (.hex or
.gpl by extension), `delete NAME` (refuses a built-in), and
`extract IMAGE [--colors N] [--name NAME]` (median-cut N
colors, default 16) — all with `--json`/`--quiet` like the
other commands. `convert`, `frames`, `prompt`, and
`presets save` accept any palette by name, built-in or saved.

## Library folder and preset format

The library is plain files in `~/pixel-forge-library/` (set
`PIXELFORGE_LIBRARY` to put it somewhere else). Nothing is created
there until the first save:

```
~/pixel-forge-library/
  library.json     the index
  presets.json     your saved presets
  palettes.json    your saved palettes
  session.json     recent files + the last-loaded image, video, code
  knight.png       one sprite entry
  walk-sheet.png   one spritesheet entry (the PNG half)
  walk-sheet.json  the spritesheet's metadata (the JSON half)
  trash/           deleted sprites, while their delete is
                   still undoable — emptied when the undo
                   entries leave the stack or the window closes
```

`library.json` is Pixel Forge's own format
(`pixel-forge-library`, version 1):

```json
{
  "format": "pixel-forge-library",
  "version": 1,
  "entries": [
    {"id": "knight", "name": "Knight", "file": "knight.png",
     "kind": "sprite", "tags": ["hero", "red"],
     "source": "image", "width": 32, "height": 32,
     "palette": "pico8", "created": "2026-11-12T16:40:00+00:00"},
    {"id": "walk-sheet", "name": "Walk cycle",
     "file": "walk-sheet.png", "kind": "sheet",
     "tags": ["walk"], "source": "video",
     "width": 134, "height": 134, "frames": 14,
     "meta": "walk-sheet.json", "created": "2026-11-12T16:45:00+00:00"}
  ]
}
```

File names come from the entry name, slugified, with `-2`, `-3`,
... on collision; renaming an entry renames the display name only,
so the file on disk stays put. Deleting an entry removes its files
too (a sheet's JSON goes with its PNG).

An entry missing a field the program reads (`id`, `name`,
`file`, `kind`, `width`, `height`, `source`, `created`) or with
a field of the wrong type is skipped when the library loads —
the app starts with the good entries, the Library tab says how
many were skipped, and the bad entries stay in `library.json`
untouched, so fixing them by hand brings them back. A whole
index file that is not parseable JSON still fails loudly.

`presets.json` holds your saved presets
(`pixel-forge-presets`, version 1) — built-ins are not stored
there, they ship in the program:

```json
{
  "format": "pixel-forge-presets",
  "version": 1,
  "presets": {
    "My Knight Look": {"width": 32, "height": 32,
      "keep_aspect": false, "palette": "pico8",
      "max_colors": 16, "dither": true}
  }
}
```

A preset needs all six keys: `width` and `height` (1–512),
`keep_aspect` (true/false), `palette` (a built-in key —
`image`, `pico8`, `gameboy`, `grayscale`, `resurrect64`,
`endesga32`, `sweetie16` — or the name of a saved palette),
(true/false). A hand-edited file with a bad preset fails loudly
instead of applying nonsense. A preset may also carry five
optional video fields — `extract_fps` (2, 4, 8, 12, or 24),
`preview_fps` and `sheet_fps` (1–30), `sheet_cols` (automatic,
or 2, 3, 4, 5, 6, 8, 10, 12, 16), `sheet_padding` (0–32) — which
only the Video tab uses; the other tabs ignore them, and a
preset without one leaves that control alone. Saving a preset
from the Video tab captures them; saving from the Image or
Prompt tab does not.

Eight presets ship built in: "Game Boy 32", "PICO-8 64",
"Detailed 64", "Tiny 16", "Large 128", "Tall 32x64",
"Wide 64x32", and "Game Boy 64". Any preset — built-in or saved
— can be exported to one JSON file from the Export button under
the preset picker (or `pixel-forge presets export NAME --out
FILE`) and imported back with the Import button (or `pixel-forge
presets import FILE`). The file carries a format marker
(`pixel-forge-preset`, version 1), the name, and the settings.
Importing a name that matches a built-in saves it as a user
preset shown as "(custom)"; importing a name that already exists
as a user preset asks before replacing it in the window, and
refuses in the CLI until the old one is deleted.

`session.json` holds the recent-files list and the last-loaded
image, video, and code paths (`pixel-forge-session`, version 1):

```json
{
  "format": "pixel-forge-session",
  "version": 1,
  "last_image": "/home/user/pictures/knight.png",
  "last_video": "/home/user/clips/walk.mp4",
  "last_code": ["/home/user/code/sprites.py"],
  "recent": [
    {"path": "/home/user/code/sprites.py", "kind": "code"},
    {"path": "/home/user/clips/walk.mp4", "kind": "video"},
    {"path": "/home/user/pictures/knight.png", "kind": "image"}
  ]
}
```

Every successful load records its file — imports, drops, and
library loads alike; a failed load records nothing. The File menu
has a "Recent files" submenu listing the last ten, most recent
first, each named with its kind; picking one loads it into its
tab exactly as if it had just been imported. A file that no
longer exists is dropped from the list when picked, with a plain
note in the status bar. On startup the window restores the
last-loaded image, video, and code into their tabs — each only
when its file still exists, a missing one skipped with a
status-bar note at most, never a dialog. The Image tab stays
current; the restored video and code are waiting on their own
tabs. A file given on the command line wins over the session: no
restore happens then. A corrupt session file reads as empty and
warns once in the status bar; the file itself is left alone.

## What v0.30 does and does not do

v0.30 is Items 1–6 of the plan: image to pixel sprite core, video to
sprite frames, prompt-to-sprite with a local image model,
spritesheets + animation export, a sprite library with presets,
and code + game-asset input — plus the window-fit fix (every tab's
control column scrolls, so the window fits a 768 px screen), the
texture-import fix (a big photo as a game asset caps at
512 px and loads on a worker thread instead of freezing the
window), the Video tab fix (extraction + pixelize run on a
worker thread with debounced controls, so dragging a slider no
longer locks the window up), the library validation fix (a
malformed entry in library.json is skipped with a warning
instead of bricking startup, and stays in the file so nothing
is lost), the installer fix (a failed packages step no
longer writes a menu launcher pointing at a broken venv, and
the Launch button only enables once the venv GUI actually
imports), and the nothing-selected-by-default fix (the ffmpeg
option is unticked by default in the GUI installer and
defaults to No in the terminal installer — ffmpeg only
installs when the user chooses it), and the empty-library
fix (the Library tab shows a centered "No sprites yet"
label while the library is empty, and the preview pane
reads "Select a sprite to preview it" when nothing is
selected instead of showing blank), and the ffmpeg
failure-message fix (when ffmpeg cannot read a video, the
error dialog now shows the last 3 lines of ffmpeg's own
output, each on its own line, instead of the garbled
"p; u; t" the old character slice produced), and the corrupt
batch fix (a damaged batch.json now gives a plain error
naming the file instead of a traceback out of the Prompt
tab, and batch saves are atomic so a crash mid-save can't
leave a half-written file), and the sheet-metadata fix (a
spritesheet saved to the library now carries a metadata JSON
whose "sheet" field names the PNG as it sits in the library,
instead of the original file name — which did not exist in
the library folder), and the stdlib-core fix (the pixelize
core no longer needs Qt: the CLI and the core suite run on a
bare system python with no PySide6, on a pure-Python PNG
codec written for this release — only PNG loads without Qt,
and a JPG input fails with a plain message naming the fix
instead of a traceback), and the folder code-import fix (one
bad file in a folder no longer aborts the whole import: the
good files still load, and each bad file is reported with its
name and reason — in the CLI on stderr, in the Code tab's
skipped-reasons box). The MIMIK merge is the one remaining planned item —
none of that is in this build.

## Requirements

Linux, Python 3.10 or newer, PySide6 for the GUI and for JPG/BMP/
WebP/GIF input (the installer handles it — the CLI works without
it for PNG input), ffmpeg for video import (the installer offers
it; image import works without it), and the image engine + model
for prompt import (the installer offers those too; everything
else works without them). Runs fine on a CPU-only machine with
8 GB of RAM — generation uses about 2.3 GB while it runs.

## Tests

```sh
python3 -m unittest discover -s tests
```

(The Qt GUI tests need a display or `QT_QPA_PLATFORM=offscreen`.)
On a bare system python with no PySide6 the suite still runs:
the core tests run and pass (the CLI and the pixelizer need no
Qt), and only the genuinely Qt-only tests skip — the GUI tests,
the installer's widget tests, and the JPG/GIF load tests.
