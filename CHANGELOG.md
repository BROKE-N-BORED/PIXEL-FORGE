# Pixel Forge changelog

## 0.27 — 2026-11-13

`compute_target_size` now guards zero (and negative) source
dimensions — task-list item #15 (bug B11, latent). With
keep-aspect on, the fit divides by the source width and height,
so a 0 x 0 source raised ZeroDivisionError; it is now a plain
PixelForgeError naming the source size ("cannot size a 0 x 0
image"). The guard runs for both aspect modes. No normal input
reaches it — `load_image` rejects unreadable files and the PNG
format forbids 0-size images — so this is a guard, not a
behavior change; normal keep-aspect results are unchanged.
Checked while in the code: `texture_target_size` has no
division of its own (it returns early for images within the
cap, and its only division path goes through
`compute_target_size`), and no other size helper divides by a
source dimension.

## 0.26 — 2026-11-13

`load_metadata` now validates every frame entry at load time —
task-list item #14 (bug B10). Bad metadata used to pass here and
fail later and confusingly: a null frame raised TypeError, string
coordinates passed validation and crashed in `extract_frame`, and
out-of-bounds rects silently cut blank frames. Every rejection is
now a plain PixelForgeError naming the file, the frame (index and
name), and what's wrong with it.

- Each frame must be an object with `name` (text), `index`,
  `x`, `y`, `w`, `h` (whole numbers — a bool is not one) and,
  when present, `duration_ms` (a whole number of 0 or more).
  The sheet and frame sizes must be whole numbers of at least 1.
- Rects must lie inside the metadata's own declared sheet size:
  no negative origin, no zero-size frame, no rect ending past
  the sheet's edge. The PNG is never loaded — this stays a
  JSON-only read. A valid sheet loads exactly as before.
- Callers checked: no product code loads sheet metadata from
  disk — the GUI preview dialog builds its metadata in memory
  from the frames, the Library tab previews a sheet from its
  PNG only, the `sheet` CLI only writes metadata, and the
  library's meta copy rewrites the "sheet" field without
  interpreting frames. Nothing else needed changing.

- Tests: fourteen new B10 tests — null and non-dict frames,
  string coords, missing field and missing name, bool coord,
  out-of-bounds rect, negative origin, zero size, string
  duration, duration optional, frames not a list, string
  sheet size, and a valid hand-written sheet loading unchanged.

## 0.25 — 2026-11-13

A stray PNG in a frames folder no longer becomes frame 0 —
task-list item #13 (bug B9). `cover.png` used to sort as 0
(no number in the name) and land ahead of every numbered
frame. Frame ordering is now: names carrying a number sort
first, by that number (frame_2 before frame_10 — a number
anywhere in the name counts, so walk2.png and cover2.png are
numbered too); names with no number are kept but sort after
all numbered ones, alphabetically among themselves; a folder
with no numbered files at all keeps plain alphabetical order.

- `video.sorted_frames` is the one ordering site — the fix
  covers every path that reads frames from a folder: the
  `sheet` CLI (via `spritesheet.load_frame_dir`) and video
  extraction (ffmpeg-written frame_%03d.png files are all
  numbered, so extraction order is unchanged).
- `pixelize_frame_files` takes an already-ordered list and
  writes frame_001.png, frame_002.png, ... in that order —
  no re-sort, nothing to fix there. The GUI video and sheet
  paths use the same ordered lists, so they inherit the fix.
- Tests: seven new B9 tests — stray PNG last, a number
  anywhere counts, all-unnumbered stays alphabetical, mixed
  numbers sort numerically, frame_2 before frame_10, and
  the CLI `sheet` path end-to-end (meta frame names in
  order).

## 0.24 — 2026-11-13

The three Video tab fps controls stop overwriting each other —
task-list item #12 (bug B8). Every reload used to copy the
extraction fps into the Preview and Sheet fps controls, silently
stomping whatever the user had set there. Each control now keeps
its own value across reloads, re-pixelizes, and new imports, and
the labels say what each one is for: "Extract fps", "Preview
fps", "Sheet fps".

- GUI: `_on_video_run_done` no longer writes the run's fps into
  the preview/sheet spins. Extraction reads Extract fps,
  playback reads Preview fps (the play timer interval), and
  sheet export reads Sheet fps (the metadata fps).
- The CLI needed no change: `frames --fps` drives extraction
  only and `sheet --fps` is recorded in the metadata only —
  each command takes its own flag and neither overwrites a
  stored value.
- Tests: the old `test_sheet_fps_follows_extraction_fps`, which
  pinned the overwrite, is replaced by six B8 tests — the
  controls keep their values across a reload and a new import,
  the labels read exactly "Extract fps" / "Preview fps" /
  "Sheet fps", playback uses Preview fps, and sheet metadata
  uses Sheet fps.

## 0.23 — 2026-11-13

The Video tab's start/end range resets on a new import — task-list
item #11 (bug B7). The range used to persist across video loads, so
loading a shorter clip after a longer one could yield "no frames
extracted" with no visible reason (the old range sat beyond the new
clip's length). The range now clears to the whole clip on every new
import; the other Video settings stay put.

- GUI: `load_video` clears the start/end edits before the worker
  run starts, so the new import is never filtered by the old
  range. Empty edits are the fresh-tab default (full range). All
  import paths go through `load_video` — Import button,
  drag-and-drop, and a video file passed on the command line.
- The reset covers the range only. fps, size, palette, colors,
  dither, every-Nth, and the preset picker are the user's working
  choices and are untouched. The other per-video state already
  reset per import (keep/drop choices, playback, preview, raw
  frames), so no further fix was needed there.

## 0.22 — 2026-11-13

Re-Generate asks before discarding a batch — task-list item #10
(bug B6). Pressing Generate on the Prompt tab used to silently
destroy the previous batch, keeper included; now a batch with
candidates prompts first, and declining leaves everything
untouched.

- GUI: `_on_prompt_generate` asks "Generate a new batch? The
  current N candidate(s)[, including your keeper] will be
  discarded." (Yes/No, default No) whenever candidates exist,
  after the prompt/engine/model checks and before the old batch
  is cleaned up. Declining returns without touching the batch —
  candidates, keeper, pixel preview, and batch.json all stay.
  With no candidates, Generate runs straight away, no dialog.
- CLI: no change needed — `pixel-forge prompt` already writes
  each run to a fresh timestamped directory (`new_batch` adds a
  numeric suffix when the name exists), so it never overwrites
  an existing batch.

## 0.21 — 2026-11-13

User presets are fail-soft now — task-list item #61 (repo-audit
Top 5 #5). One invalid entry in presets.json used to drop EVERY
user preset (the load raised on the first bad one, and the pickers
fell back to built-ins only); now the bad preset is skipped, the
good ones load, and the skip is warned about where the user can
see it.

- `PresetStore._load` validates each user preset on its own and
  collects the failures in a new `skipped_presets()` list of
  (name, reason) pairs, same shape as the library's skipped
  entries. A wholly unparseable presets.json still raises, exactly
  like the library's unparseable index — only per-entry failures
  go fail-soft.
- `_save` preserves skipped entries verbatim, in their original
  positions — saving a good preset never deletes or rewrites the
  bad entry. Saving over a skipped preset's name replaces it with
  the good settings (the user fixed it).
- CLI: `pixel-forge presets list` prints one `skipped preset
  'Name': <reason>` line per bad preset on stderr and still lists
  the good ones.
- GUI: a warning label under the preset picker on the Image, Video,
  and Prompt tabs names each skipped preset and why — the old
  status-bar-only message was easy to miss, and the pickers
  silently dropping to built-ins-only looked like the presets had
  vanished. No modal dialog: a bad preset is not worth
  interrupting the user. The label hides when there is nothing to
  warn about.

## 0.20 — 2026-11-13

Folder code import is per-file now — task-list item #65 (repo-audit
Top 5 #4). One bad file in a folder used to abort the whole import
(exit 1, good assets never listed); now the good files load and each
bad file is reported with its name and reason.

- `codeinput.parse_paths` catches a per-file failure, records it in
  a new `"skipped"` list (`{"file", "reason"}`), and keeps going. A
  file that parses but defines no assets is a success, not a skip.
  When EVERY file fails it still raises: a single bad file raises
  its own error unchanged (so `pixel-forge code BROKEN.py` reads
  exactly as before), and several bad files are all named in the
  one message.
- CLI: `pixel-forge code FOLDER --list` prints one `skipped <path>:
  <reason>` line per bad file on stderr and exits 0 when at least
  one file imported; an all-bad folder exits 1 with the readable
  message. No tracebacks either way.
- GUI: the Code tab's skipped-reasons box now lists failed files
  (name + reason) alongside the found-but-not-rendered notes, and
  the status bar counts the skipped files. Loading a game asset or
  a clean folder clears the old skipped lines — no stale state.
- Fixed in passing: a Python syntax error with no line number
  (a binary file's "cannot contain null bytes") used to report
  "(line None)" — the line is left out when there is none.

## 0.19 — 2026-11-12

The pixelize core no longer needs Qt — task-list item #64 (the
real fix for repo-audit Top 5 #3, where v0.18 was only the
minimum). The CLI and the core suite now run on a bare system
python with no PySide6 at all; the GUI keeps Qt for display only.

- New `pixel_forge/image.py`: the core works on a plain pixel
  buffer (`Image` — width, height, RGBA bytes) instead of
  `QImage`. Its nearest-neighbour scaling reproduces Qt's
  `FastTransformation` sampling exactly — the mapping was probed
  against `QImage.scaled` on 4,400+ size pairs plus the boundary
  cases where a naive formula is off by one (100 -> 3 samples
  16, 49, 83 — the middle one is 49, not 50).
- New `pixel_forge/pngio.py`: a minimal pure-Python PNG codec on
  stdlib zlib, written for this release (the stdlib has no PNG
  codec). The decoder handles every color type, bit depths
  1/2/4/8/16, tRNS, all five filters, and Adam7 interlace; the
  writer emits 8-bit RGBA PNGs. New `pixel_forge/qtimage.py`
  adapts between buffer Images and QImages at the GUI's display
  boundary.
- `pixelize.py`, `spritesheet.py`, `skins.py`, `codeinput.py`,
  `library.py`, `video.py`, and `cli.py` no longer import
  PySide6. The CLI bootstrap is gone from `cli.main()` —
  `load_image` creates the QCoreApplication lazily, only when
  it takes the Qt decode path.
- `load_image` prefers Qt when PySide6 is importable, and falls
  back to the stdlib codec when it is not. That is a deliberate
  performance choice, measured: Qt's libpng decodes a 1920x1080
  frame in ~0.03 s against ~3.3 s for the pure-Python codec
  (Paeth unfiltering is ~2 MB/s in CPython — no further stdlib
  vectorization exists). Stdlib-always would have frozen the
  Image tab ~25–30 s on a 4000x3000 PNG import and stretched a
  2 s video clip import to ~50 s. The two decoders are pinned
  pixel-identical by the Qt cross-check tests in
  `tests/test_pngio.py`.
- JPG (and BMP, WebP, GIF, ...) has no stdlib decoder, so
  without Qt only PNG loads: a non-PNG input fails with a plain
  message naming the file and the fix ("Install PySide6 to load
  JPG, BMP and other formats"), never a traceback. With PySide6
  present, the Qt loader stays available for every format
  exactly as before — GUI behavior is unchanged.
- Pixel-identical before/after is proven, not assumed: 137
  reference outputs were recorded from the v0.18 Qt core
  (pixelize across sizes/palettes/dither, downscale and upscale
  pairs, sheet pack + cut-back-out, both skin layouts, texture
  pixelize, code rendering) and all 137 match from the new core
  on bare python AND under Qt. The comparison is now a
  permanent test (`tests/test_reference_outputs.py`), along
  with the downscale formula pins.
- The cross-check caught a real codec bug during the build:
  16-bit samples were reduced by keeping the high byte, but
  libpng (and Qt) scale with rounding — `(v * 255 + 32895) >>
  16`, which disagrees with truncation on a quarter of all
  values (0x00FF scales to 1, 0xFF00 to 254). pngio now scales
  via a 64 KiB lookup table.
- The suite: 466 tests with PySide6 (was 428 — new
  `test_pngio.py` with the independent-encoder codec tests and
  the Qt cross-check, plus `test_reference_outputs.py`). On bare
  python: Ran 358, OK, skipped=39 (was Ran 70, skipped=42) —
  the core tests now RUN instead of skipping; the 39 skips are
  all genuinely Qt-only (the GUI tests, the installer's widget
  tests, the cross-check, and the JPG/GIF load tests).
  `tests/test_skip_guards.py` now pins only `test_gui` as
  guarded.

## 0.18 — 2026-11-12

The test suite runs on a bare system python — task-list item
#63 (repo-audit Top 5 #3, minimum level). Ten of the twelve test
modules errored on import without PySide6, so the documented test
command failed outright (10 errors) on any machine without Qt
installed.

- Each Qt-dependent test module now guards its Qt imports at
  module level: when PySide6 is what is missing, the module
  raises `unittest.SkipTest` and discovery reports it as one
  clean skip instead of an import error. Only a missing PySide6
  skips — the guard checks the failed import's name, so a
  genuinely broken import still fails loudly instead of hiding
  behind the skip.
- The pure tests still run on bare python: the palette tests,
  the installer script tests, and the new guard tests all run
  and pass with no PySide6 at all (bare run: 70 tests, OK,
  skipped=42 — 10 module skips plus the 32 installer tests
  that need Qt).
- `test_gui.py`'s old per-class `HAS_QT` decorators are gone:
  one of its eight classes never had the decorator, so the
  module skipped nothing and errored on import anyway. The
  module-level guard covers every class and cannot be missed.
- New `tests/test_skip_guards.py` pins the behavior: it imports
  each guarded module in a subprocess with PySide6 blocked and
  asserts it skips instead of erroring.
- This is the minimum level, not the real fix: the pixelize
  core is still Qt-only, so even the CLI needs PySide6.
  Decoupling the core is task #64 (v0.19).

## 0.17 — 2026-11-12

The image engine pin is enforced and the model download is
SHA-256-checked — the core of task-list item #31 (repo-audit
Top 5 #2). `ENGINE_RELEASE` was defined in `imagegen.py` and
referenced NOWHERE: both installers cloned stable-diffusion.cpp
master HEAD, so the engine was whatever master happened to be
that day, and the 2.1 GB model download had no checksum at all.

- Both installers check the engine out at the pinned release
  (`master-1086-465adf3`, the tag the live generation was proven
  against). The GUI installer builds the clone command from
  `imagegen.engine_clone_command`, which reads the constant —
  there is no duplicated literal to drift. `install.sh` carries
  the literal tag (bash cannot import Python) and a test asserts
  it equals `ENGINE_RELEASE`, so the two can never drift
  silently. A failed checkout fails the step loudly, naming the
  release and the git exit code, and cmake never runs after it.
- Both installers download the model with curl resume (`-C -`),
  so an interrupted 2.1 GB download picks up where it stopped
  instead of restarting. After the download lands, its SHA-256
  is checked against the pinned `MODEL_SHA256` (recomputed
  2026-11-12 from the verified copy:
  `06420607…00e15`). On a mismatch the bad file is DELETED and
  the step fails loudly with the expected vs actual hash — a
  partial or corrupt download is never accepted as the model.
  A failed download leaves any partial file in place, so the
  next run resumes it.
- The rest of task 31 (disk-space pre-check, cheap steps first,
  per-step progress, Cancel mid-step, apt output on failure,
  version display, uninstall entry) is NOT in this version — it
  is task 76, a separate version.

## 0.16 — 2026-11-12

A spritesheet saved to the library no longer carries stale
metadata — task-list item #62 (repo-audit Top 5 #1). `add_sheet`
copied the sheet's metadata JSON verbatim, so the library copy's
"sheet" field still named the ORIGINAL PNG (e.g.
"walk-sheet.png") — a file that does not exist in the library
folder. The GUI path was always stale too: the temp sheet is
always called sheet.png.

- `add_sheet` now rewrites the library copy's "sheet" field to
  the PNG's name inside the library, so the JSON names a file
  that exists next to it. The rewrite happens on the library
  copy only — the original meta file stays byte-identical — and
  every other field in the JSON is preserved.
- The rewritten copy is written atomically (temp file + rename),
  like `write_sheet`. A meta that is not a JSON object is copied
  verbatim, as before — it has no "sheet" field to go stale.
- Checked the read path: the Library tab shows and loads a sheet
  from the entry's own PNG (`path_for`), never from
  meta["sheet"], so no fallback was needed for entries saved
  before this fix.

## 0.15 — 2026-11-12

A corrupt `batch.json` no longer crashes out of a GUI slot —
task-list item #9 (review bug B5). `load_batch` read the Prompt
tab's batch record with no error handling at all: a truncated
file raised a raw `JSONDecodeError`, and valid JSON with the
wrong shape sailed through the load and then raised `TypeError`
or `KeyError` in `set_keeper` — out of `_pick_prompt_keeper`
(which catches only `PixelForgeError`) or `_on_prompt_cancelled`
(which caught nothing).

- `load_batch` now raises `PixelForgeError` with a plain
  message naming the file and the problem — "prompt batch is
  unreadable: .../batch.json (Expecting ',' delimiter...)" for
  bad JSON, "prompt batch has the wrong shape: ... (no
  candidate list)" and friends for valid JSON with missing or
  wrong fields. The corrupt file is left alone: never deleted,
  never "repaired".
- `_on_prompt_cancelled` catches the error and shows it in the
  normal warning dialog; the keeper slot already caught
  `PixelForgeError`, so a corrupt batch there is now a plain
  status-bar message. Either way the window stays alive and
  usable.
- `save_batch` is atomic: the full content goes to a temp file
  in the same directory, is flushed + fsynced, then
  `os.replace` moves it over `batch.json`. A crash or failure
  mid-save leaves the previous `batch.json` intact — never a
  half-written file — and the temp file is cleaned up.
- Same-class audit: the spritesheet metadata write
  (`write_sheet`) also went straight to its final path, so it
  is atomic now too. The library index already raised
  `PixelForgeError` on an unparseable file and already saved
  atomically (unchanged); presets.json's first-invalid-preset
  problem is task 61, untouched here.

## 0.14 — 2026-11-12

The ffmpeg failure message reads like English now — task-list
item #8 (review bug B3). When ffmpeg could not read a video,
the error dialog was supposed to show the last 3 lines of
ffmpeg's output, but the code joined the lines into one string
and then sliced `[-3:]` off the string — the last 3
characters — and joined those with "; ", so the dialog read
`ffmpeg could not read not-a-video.mp4 — p; u; t`.

- The slice now applies to the list of lines before joining,
  so the dialog shows the last 3 non-blank lines of ffmpeg's
  output, in order, each on its own line.
- Fewer than 3 lines of output show in full; empty output
  leaves no detail at all — just "ffmpeg could not read X",
  no meaningless fragment.
- Same-class audit: the image-engine failure path
  (`imagegen.py`) already slices the list of lines, and the
  installer streams output without trimming, so no other
  instance of this defect exists.

## 0.13 — 2026-11-12

The empty Library tab is no longer a void — task-list item #7
(review bug B1). The preview pane was supposed to read "Select a
sprite to preview it" when nothing was selected, but clearing the
selection set the text and then set a null pixmap, and a pixmap —
even a null one — wipes the label's text, so the pane showed
blank.

- The clear-selection order is fixed (pixmap cleared first,
  text set after), so the preview pane reads "Select a sprite
  to preview it" whenever nothing is selected.
- The grid area now shows a centered "No sprites yet" label
  while the library is empty. It disappears as soon as sprites
  exist and comes back when the last sprite is deleted. An
  unreadable library still shows the error in the count label
  instead — the label never claims an unreadable library is
  empty.

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
