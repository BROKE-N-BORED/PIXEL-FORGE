# Pixel Forge changelog

## 0.12 — 2026-11-12

Nothing selected by default, ever — task-list item #6. The
ffmpeg option used to be pre-selected in both installers, so
an install went ahead and installed ffmpeg (sudo + download)
unless the user noticed and opted out.

- The GUI installer's "Install ffmpeg for video support"
  checkbox is now unticked by default. Ticking it installs
  ffmpeg exactly as before; the image-engine checkbox was
  already unticked and stays that way.
- The terminal installer's ffmpeg prompt now reads `[y/N]`
  and an empty answer means no — an explicit yes still
  installs ffmpeg exactly as before.
- The same default is fixed one layer down: a bare
  `InstallWorker` or `InstallContext` no longer installs
  ffmpeg either, so no caller can trigger the install
  without the user's choice. The window passes the checkbox
  state explicitly, so GUI behavior is unchanged apart from
  the default.
- The rest of the install surface was audited for the same
  violation and is clean: the image engine is opt-in in both
  installers (`--with-image-engine` / unticked checkbox),
  the uninstaller's `--purge` is explicit, and `run.sh`
  offers no options at all.

## 0.11 — 2026-11-12

A failed install no longer leaves a dangling launcher and an
enabled Launch button — task-list item #5, weakness W5 of the
v0.6 internal review. The file step used to run regardless of
whether the packages step failed, so a broken install still
wrote a menu launcher pointing at a venv that could not start,
and the Launch button trusted the launcher's existence instead
of checking the GUI actually imports.

- The file step is now gated on the packages step: when
  packages fails, the launcher, .desktop file, and menu entry
  are not written at all, and the step list shows the file
  step as "skipped" with the reason in the log. The installer
  ends in a clear failure state naming the failed step.
- Launch is now gated on a real check: the installer runs the
  venv's own Python with `import pixel_forge.gui` (from a
  fresh empty directory, so the import cannot pass from the
  installer's own folder) and enables Launch only when that
  import succeeds. When it fails, Launch stays disabled and
  the installer says why. A previous install that still works
  keeps Launch available even when a re-run fails partway.
- No other installer behavior changed: per-step results,
  warn-never-refuse on optional steps, the PySide6 core
  verification, and re-runnability are all as before.

## 0.10 — 2026-11-12

One malformed library entry no longer bricks startup — task-list
item #4, weakness W4 of the v0.6 internal review. A single entry
in library.json missing a field (say `name` or `file`) used to
crash the app at startup with an uncaught KeyError.

- Index entries are validated when the library loads: entries
  missing a field the window or the CLI reads (or with a field
  of the wrong type) are skipped, not fatal. The app starts with
  the good entries intact.
- The skip is warned about, never silent: the Library tab's
  count label says how many malformed entries were skipped, the
  status bar says so once at startup, and the `library list` /
  `library add` CLI commands print a warning to stderr.
- Skipped entries stay in library.json verbatim, in their
  original positions — a later save (a new sprite, a rename, a
  remove) never deletes data the load could not use. Nothing
  is lost; fix the entries by hand and they load again.
- A whole index file that is not parseable JSON still fails
  loudly, as before — this is about bad entries inside a
  readable index. No other changes in this release.

## 0.9 — 2026-11-12

Video tab freeze fixed — task-list item #3, weakness W3 of
the v0.6 internal review. Every Video tab control change used
to re-run ffmpeg extraction + the pixelize pass synchronously
on the UI thread (~9.8 s per extract-setting change and 2.9–6.3 s
per pixelize change on a 30 s clip), so dragging a slider locked
the window up on every tick.

- Extraction + pixelize now run on a worker thread (the
  PromptWorker / TextureWorker pattern), so the window stays
  responsive while a run works; the status bar says
  "Extracting frames…" or "Pixelizing…" until it lands. Import
  is async too — `load_video` returns at once and the frames
  land when the run finishes.
- Control changes debounce 300 ms: rapid changes run once,
  with the settled settings, instead of once per tick. A run
  in flight when a new change lands is superseded cleanly —
  its results are dropped by a stale guard, never stacked.
- The results are identical to v0.8: same frames, same pixels,
  same export names, same keep/drop behaviour, same loud
  refusal when ffmpeg is missing (that check stays
  synchronous, so it still fails fast). No other changes in
  this release.

## 0.8 — 2026-11-12

Texture-import freeze fixed — task-list item #2, weakness W2 of
the v0.6 internal review. A big photo imported as a game asset
used to freeze the window for minutes (21.8 s for 1024x1024,
33 s for 1920x1080, 208.8 s for 4000x3000, at ~950 MB RAM):
`pixelize_texture` ran the core at native size with 256 colors,
synchronously on the UI thread.

- Textures now cap at 512 px on the longest side (the Image tab's
  max preset), downscaled keeping the aspect ratio before the
  core runs. A 4000x3000 photo now imports in under a second.
  Textures at or under the cap are pixel-for-pixel unchanged.
- The texture path loads on a worker thread (the PromptWorker
  pattern), so the window stays responsive while it works; the
  status bar says "Loading texture…" until it lands.
- The Code tab's info line says when a texture was downscaled
  ("downscaled from 4000 x 3000 to 512 x 384") — never silently
  resized. The skin path (64x64/64x32 parts + figure) is
  untouched. No other changes in this release.

## 0.7 — 2026-11-12

Window fit — the #1 finding of the v0.6 internal review (W1).

- Every tab's control column now lives in a vertical scroll area,
  so the window is no longer forced taller than the screen: it
  opens at 1020x640 with a minimum height of ~305 px (was 872),
  and a resize to 800x600 takes effect on every tab. The Video
  column alone needed 817 px, so its Export buttons sat off-screen
  on a 1366x768 display. Every control is still in the same place
  — the tall columns just scroll. No other changes in this
  release.

## 0.6 — 2026-11-12

Item 6 of the plan: code + game-asset input.

- Code tab: import a .py / .js / .ts file (or a folder of them)
  and the pixel art the code defines is rebuilt as real sprites.
  Parsed statically — Python with ast.parse + literal_eval, JS/TS
  with a small literal parser — and NEVER executed, evaled, or
  imported, so a file cannot run anything when you import it.
  Detected: pixel maps (int rows with a palette from the file or
  PICO-8 colors by default, hex rows, RGB rows, character rows
  with a legend dict), and palettes (flat color lists or dicts of
  colors). Each asset shows its variable name, kind, size, and a
  preview; Export PNG and Save to library work like the other
  tabs. Anything found but not renderable (art computed at run
  time, draw-call specs, character rows with no legend, ragged
  rows) is listed with a plain reason — nothing is faked. The
  README documents exactly what is and is not detected.
- Game assets: Import game asset in the Code tab loads a
  Minecraft skin (64x64 new format or 64x32 classic, detected by
  size) and splits it into its parts — head, hat, body, jacket,
  arms, sleeves, legs, pants — plus the combined 16x32 figure
  with overlays composited. Classic skins get mirrored left limbs.
  Export parts writes one PNG per part plus figure.png. Any other
  image pixelizes as a texture through the Item 1 core at its
  native size, with a note saying so.
- CLI: `pixel-forge code FILE --list` lists the assets found,
  `pixel-forge code FILE --asset NAME --out PNG` renders one,
  `pixel-forge skin FILE --out DIR` extracts a skin's parts.
  No assets found, unknown asset, or not a skin all fail with a
  readable message and exit 1, no traceback.
- Library sources now include "code" and "skin" for saves from
  the Code tab.
- Fixed in place (verification catch): int maps indexed a palette
  from the file 0-based (value 1 was the SECOND color) while the
  PICO-8 default path was 1-based (value 1 the first) — one
  function, two conventions. Worse, a map using every color of
  its palette failed the coverage check and silently fell back
  to PICO-8 colors. File palettes are now 1-based like the
  default: 0 transparent, 1 the first color, and a full-range
  map keeps its palette.
- Fixed in place (verification catch): the Code tab's info
  label wrapped its text but the layout gave it too little
  height, so the text overflowed into the controls below — the
  same defect as the Prompt status label in v0.3, fixed the
  same way (minimum height from the label's own
  heightForWidth). The "Found but not rendered" list lives in
  a scroll box instead: sizing it to its full text overflowed
  the column and squeezed the Zoom row and info label out of
  view entirely. Also "1 colors" now reads "1 color" in the
  asset info line.

## 0.5 — 2026-11-12

Item 5 of the plan: library + presets.

- Library: save sprites from the Image tab (current result),
  the Prompt tab (pixelized keeper), or the Video tab (a
  spritesheet PNG + JSON pair, saved as one entry). Each save
  asks for a name + optional tags. Storage is plain files in
  ~/pixel-forge-library/ — one PNG per entry plus a library.json
  index (name, tags, source, size, palette, date) — so the
  folder is browsable in Nemo and nothing is locked in a
  database. PIXELFORGE_LIBRARY overrides the folder. Nothing is
  created on disk until the first save.
- Library tab: grid of thumbnails with a search box and a tag
  filter, click one to preview it big with its details, Load
  into Image tab reopens a sprite for rework, Rename, Delete
  (with a confirm — the file is removed too), Export a copy
  (a sheet exports its PNG + JSON pair). The grid re-reads the
  index whenever the tab is opened, so a CLI add shows up too.
- Presets: one picker on each of the Image, Video, and Prompt
  tabs. A preset is size + palette + max colors + dither +
  keep-aspect — one pick sets all five controls at once, and
  changing a control by hand drops the picker back to Custom.
  Save current as preset stores the current controls under a
  name; Delete preset removes a saved one (with a confirm).
  Three built-ins ship read-only: Game Boy 32 (32x32, gameboy,
  4 colors, no dither), PICO-8 64 (64x64, pico8, 16 colors),
  Detailed 64 (64x64, from-image palette, 16 colors). User
  presets live in presets.json in the library folder, so they
  travel with it; a user preset may share a built-in's name —
  it gets its own slot (shown as "Name (custom)") and never
  overwrites the built-in.
- CLI: `pixel-forge library list [--tag X]`,
  `pixel-forge library add FILE --name X [--tags a,b]`,
  `pixel-forge presets list`. Bad input fails with a plain
  message and exit 1, no traceback.
- Fixed in place (verification catch, pre-existing since v0.1):
  a named palette at fewer than its full colors used to be cut
  by index, which could throw away the exact colors the image
  needed — a green-and-red picture on PICO-8 at 4 colors came
  out a single flat grey. The palette now keeps the entries the
  image actually uses most.
- Fixed in place (verification catch, pre-existing since v0.1):
  dithering was Floyd-Steinberg error diffusion, whose feedback
  loop runs away on tiny palettes — flat red on Game Boy came
  out 95% lightest shade, worse than no dithering at all.
  Dithering is now ordered (Bayer) thresholds: a stable
  crosshatch pattern that cannot drift, and the classic
  pixel-art look besides.

## 0.4 — 2026-11-12

Item 4 of the plan: spritesheets + animation export.

- Pack the Video tab's kept frames into one spritesheet PNG +
  a metadata JSON (`pixel-forge-spritesheet`, version 1 — sheet
  size, frame size, fps, and a frames array with x, y, w, h,
  duration_ms, and source name, documented in the README). A
  game reads the JSON, cuts the frames out of the PNG, and plays
  them — nothing to guess.
- Layout: left to right, top to bottom; columns are Auto
  (square-ish grid) or a fixed count; padding is the gap between
  frames only, so frame 0 always starts at (0, 0); the sheet
  background, padding gaps, and unused cells in a partial last
  row are all transparent.
- Video tab Spritesheet section: columns, padding, and sheet fps
  controls (sheet fps follows the extraction fps), Preview
  spritesheet opens a dialog showing the whole sheet and playing
  the animation cut back out of it, Export spritesheet writes
  the PNG + JSON pair. Both buttons follow the kept frames —
  drop all and they disable, like Export frames.
- CLI: `pixel-forge sheet DIR --out sheet.png --meta sheet.json
  [--cols N] [--padding N] [--fps N]` packs a folder of
  same-size PNGs. Empty folder, missing folder, or mixed frame
  sizes fail with a plain message and exit 1, no traceback.
- Fixed in place (verification catch): a mixed-size sheet error
  used to name frames by index ("frame 1") instead of by file —
  it now names the odd file ("b.png is 16 x 16"), so the user
  knows exactly which frame to fix.
- Fixed in place: the Prompt tab status label could be 5 px
  short of its wrapped text in the sandbox (63 px given, 68
  needed) because its height was computed with a QTextDocument
  whose wrapping differs from the label's. The height now comes
  from the label's own heightForWidth at 230 px.

## 0.3 — 2026-11-12

Item 3 of the plan: prompt to sprite, with a local image model.

- Prompt tab in the window: prompt + negative prompt, Generate
  makes 3 candidates (different seeds), click one in the strip to
  keep it, pixelize it with the same size/palette/dither controls
  as the Image tab, export a transparent PNG. Generation runs in
  a worker thread with progress and Cancel — a minutes-long CPU
  run never freezes the window.
- Engine: stable-diffusion.cpp + LCM_Dreamshaper_v7-f16.gguf
  (2,135 MB, 4-step LCM, 512x512, cfg 1.5), ~2.3 GB RAM while
  generating. The model home is shared with the Design Shell
  (~/design-shell-models/ first), so a model already pulled for
  the shell is reused — no second download. PIXELFORGE_SD_CLI
  and PIXELFORGE_SD_MODEL override the search.
- Missing engine or model = a loud refusal with the exact fix,
  nothing faked. A status line on the Prompt tab shows the engine
  and model state (found/missing + paths).
- Every generation writes batch.json next to the candidates
  (prompt, negative, seeds, engine, model, keeper), so a keeper
  is reproducible.
- CLI: `pixel-forge prompt "a knight" --out DIR` with `--count`,
  `--negative`, and the same `--size`/`--colors`/`--palette`/
  `--dither` flags as convert — 3 candidates + pixelized versions
  in the folder.
- Installer: `bash install.sh --with-image-engine` builds the
  engine from source (the prebuilt binary needs glibc 2.43; Mint
  ships 2.39) and downloads the model; missing git/cmake/g++
  stops with the plain apt line. The GUI installer offers the
  same as an optional checkbox (off by default — it is a long
  build plus a 2.1 GB download).
- Live generation is NOT proven in this build's sandbox: no
  engine or model was present there, so verification used a stub
  engine plus full missing-engine/missing-model tests. The first
  real generation on Joshua's PC (after the model pull) is the
  proof that still matters.

## 0.2 — 2026-11-12

Item 2 of the plan: video to sprite frames.

- Frame extraction with the ffmpeg CLI: mp4/webm/mkv/mov/avi and
  whatever else ffmpeg reads. fps selector (2/4/8/12/24), keep every
  Nth frame, optional start/end time range. If ffmpeg is missing,
  video import refuses loudly with the exact fix
  (`sudo apt install ffmpeg`) — image import still works.
- Every kept frame pixelizes through the Item 1 core with the same
  size/palette/dither controls. No duplicated pixelizer.
- Video tab in the window: import video, frame strip with thumbnails
  and keep/drop checkboxes (plus Keep all / Drop all), animation
  preview with play/pause, playback fps, and loop, export of the
  kept frames as numbered PNGs (frame_001.png, ...). No spritesheet
  packing — that is Item 4.
- CLI: `pixel-forge frames VIDEO --fps 8 --out DIR` with `--every`,
  `--start`, `--end`, and the same `--size`/`--colors`/`--palette`/
  `--dither` flags as convert.
- Installer offers ffmpeg as an optional step (checkbox, on by
  default). A failed ffmpeg install is a warning, never a failure.

## 0.1 — 2026-11-12

First release. Item 1 of the plan: image to pixel sprite core, no AI.

- Core pixelizer (Qt-only, no Pillow): load PNG/JPG/BMP/WebP/GIF
  (first frame) and whatever else QImage reads, downscale first with
  nearest-neighbour sampling, then quantize in pure Python.
- Sizes: presets 16, 32, 48, 64, 128, custom width/height, keep-aspect
  fit.
- Palettes: max colors 2–256, Floyd-Steinberg dithering on/off,
  built-in PICO-8 (16), Game Boy (4), grayscale (16), plus
  from-image median-cut.
- Transparency preserved: alpha-0 pixels stay transparent and are
  excluded from the palette; other pixels keep their original alpha.
- Native Qt GUI (dark theme): import button and drag-and-drop, size /
  palette / dither controls, before/after preview side by side with
  zoom (1x–16x) and a checkerboard behind transparent areas, live
  re-pixelizing, Export PNG.
- CLI: `pixel-forge convert` with `--size`, `--colors`, `--palette`,
  `--dither` / `--no-dither`, `--out`, plus `--help` and `--version`.
- Release packaging: pixel-flame icon (SVG + PNGs), Qt GUI
  double-click installer with launch button, `install.sh` fallback,
  menu launcher, uninstaller.
- 69 tests green (core, palettes, CLI, GUI offscreen, installer
  dry-run).
