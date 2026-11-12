# Pixel Forge changelog

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
