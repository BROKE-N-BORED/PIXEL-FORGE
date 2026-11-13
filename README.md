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
second download happens.

To remove it later:

```sh
bash uninstall.sh
```

## Use

**Window:** open Pixel Forge from the menu, or run `pixel-forge-gui`.

- **Image tab:** import an image (or drop one on the window), adjust
  the size, palette, and dithering, and press Export PNG. Conversion
  is live — every change re-pixelizes at once.
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
  follow the strip order. The Spritesheet section packs the kept
  frames into one sheet PNG + a metadata JSON: pick the columns
  (Auto makes a square-ish grid), the padding between frames, and
  the playback fps, then Preview spritesheet to see the sheet and
  play the animation from it before you export, or Export
  spritesheet to write the pair straight away.
- **Prompt tab:** type what you want (and what to avoid), press
  Generate, and 3 candidates appear in the strip — each takes a
  few minutes on CPU, and the window stays usable while they
  generate (the progress bar shows the elapsed time). Click a
  candidate to keep it, adjust the size, palette, and dithering,
  and press Export PNG. The status line shows whether the image
  engine and model are installed; if either is missing, Generate
  says plainly what to install instead of faking anything.
  Generating again asks first, and the previous batch stays in
  History — past batches are listed under the Generation section
  and click to reopen, with the keeper's seed shown under the
  candidate preview. Regenerate one re-rolls a single candidate;
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
  library. Anything the code defines that cannot be rendered
  (art built by loops or function calls, draw-call specs) is
  listed under "Found but not rendered" with the reason. Import
  game asset loads a Minecraft skin (64x64 or 64x32) and splits
  it into its parts — head, body, arms, legs, overlays — plus the
  combined figure; Export parts writes one PNG per part. Any
  other image pixelizes as a texture instead, and says so.
  A texture bigger than 512 px on its longest side is
  downscaled to fit that cap first (the info line says so),
  and loads on a worker thread, so a big photo never freezes
  the window.
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
- **Presets:** the Image, Video, and Prompt tabs each have a
  Preset picker. A preset is size + palette + max colors +
  dithering + keep-aspect in one pick — Game Boy 32, PICO-8 64,
  and Detailed 64 ship built in, and Save current as preset
  stores whatever the controls are set to right now under a name
  you choose. Changing a control by hand drops the picker back
  to Custom. Delete preset removes a saved preset (built-ins
  cannot be deleted).
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
pixel-forge frames clip.mp4 --fps 8 --out frames/
pixel-forge frames clip.mp4 --fps 12 --every 2 --start 1.5 --end 10 --size 48
pixel-forge prompt "a knight with a red shield" --out knight/
pixel-forge prompt "a slime" --count 3 --size 32 --colors 16 --palette pico8
pixel-forge sheet walk-frames/ --out walk-sheet.png --meta walk-sheet.json
pixel-forge sheet walk-frames/ --cols 4 --padding 2 --fps 12
pixel-forge library list
pixel-forge library list --tag hero
pixel-forge library add sprite.png --name "Red Knight" --tags hero,red
pixel-forge library export ~/pixel-forge-backup/
pixel-forge library import ~/pixel-forge-backup/
pixel-forge presets list
pixel-forge presets export "My Knight Look" --out knight-look.json
pixel-forge presets import knight-look.json
pixel-forge code game.py --list
pixel-forge code game.py --asset HERO --out hero.png
pixel-forge code src/ --list
pixel-forge skin steve.png --out steve-parts/
pixel-forge --help
```

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
  `gameboy` (4 shades), `grayscale` (default: `image`).
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
- `--cols` — columns in the grid, 0 = automatic square-ish grid
  (default 0).
- `--padding` — gap between frames in pixels (default 0).
- `--fps` — playback fps recorded in the metadata (default 8).

All frames in a folder must be the same size; a folder that mixes
sizes (or holds no PNGs) fails with a plain message.

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
     "w": 32, "h": 32, "duration_ms": 125}
  ]
}
```

Rules the format keeps: frames run left to right, top to bottom, in
playback order; `x` and `y` are the frame's top-left corner in the
PNG; padding is the gap *between* frames only, so frame 0 always
starts at (0, 0); every frame's `duration_ms` is 1000 / fps rounded
to the nearest millisecond; the sheet background, the padding gaps,
and any unused cells in a partial last row are all transparent.

## Library folder and preset format

The library is plain files in `~/pixel-forge-library/` (set
`PIXELFORGE_LIBRARY` to put it somewhere else). Nothing is created
there until the first save:

```
~/pixel-forge-library/
  library.json     the index
  presets.json     your saved presets
  knight.png       one sprite entry
  walk-sheet.png   one spritesheet entry (the PNG half)
  walk-sheet.json  the spritesheet's metadata (the JSON half)
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
`keep_aspect` (true/false), `palette` (`image`, `pico8`,
`gameboy`, `grayscale`), `max_colors` (2–256), `dither`
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
