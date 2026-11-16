"""The Pixel Forge window itself: MainWindow
(the composition point — it inherits the six
tab mixins) plus main(). One of the gui.py
split (task 58).
"""

from . import __version__, procedural
from PySide6.QtCore import (QRect, QSettings, QTimer, QUrl, Qt)
from PySide6.QtGui import (QAction, QDesktopServices, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QStatusBar, QTabWidget, QTextEdit, QVBoxLayout)
from .autosave import (autosave_home, crashed, discard_recovery, empty_recovery, load_recovery, load_recovery_image, mark_clean_exit, mark_running, recovery_image_path, recovery_path, save_recovery_image)
from .codeinput import CODE_EXTENSIONS
from .gui_code import CodeTabMixin
from .gui_image import ImageTabMixin
from .gui_library import LibraryTabMixin
from .gui_prompt import PromptTabMixin
from .gui_shared import (AUTOSAVE_DEBOUNCE_MS, PaletteEditorDialog, SIZE_PRESETS, TEXTURE_DEBOUNCE_MS, THEMES, UpdateCheckWorker, VIDEO_DEBOUNCE_MS, _ControlChangeCommand, _PresetDeleteCommand, clamp_geometry, gui_module)
from .gui_tileset import TilesetTabMixin
from .gui_video import VideoTabMixin
from .imagegen import load_batch
from .layers import Layer
from .library import Library
from .palette_io import PaletteStore, extract_palette_from_image
from .palettes import NAMED_PALETTES, PALETTE_LABELS
from pathlib import Path
from .pixelize import PixelForgeError
from .presets import (BUILTIN_PRESETS, PresetStore, read_preset_export, validate_settings)
from .session import (load_session, record_recent, save_session, set_last_code, set_last_image, set_last_video)
from .undo import UndoStack
from .video import VIDEO_EXTENSIONS
import os
import subprocess
import sys


class MainWindow(ImageTabMixin, VideoTabMixin,
                   PromptTabMixin, CodeTabMixin,
                   LibraryTabMixin, TilesetTabMixin,
                   QMainWindow):
    def __init__(self, library_dir=None, restore=True, settings=None,
                 autosave_dir=None):
        super().__init__()
        self.setWindowTitle("Pixel Forge %s" % __version__)
        # Geometry persistence (task 66): settings is a test hook
        # like library_dir — main() passes the real QSettings, and
        # a window built without one behaves exactly as before
        # (1020x640, nothing saved).
        self._settings = settings
        # Autosave flags (task 55) live here, before the tabs are
        # built: control signals fire during construction, and
        # the dirty-marking hooks read these flags. The crash
        # check and the timer come at the end of __init__.
        self._autosave_ready = False
        self._autosave_broken = False
        self._autosave_dirty = False
        self._autosave_last = None
        self._autosave_offered = False
        self._autosave_pending_video = None
        self._autosave_video_restored = False
        self._autosave_crashed = False
        self._autosave_recovery = None
        self._autosave_corrupt = False
        self._restore_geometry()
        # Theme (task 67): the saved choice, or dark on first
        # launch. Applied before the widgets are built, so they
        # are styled at creation. Only a window with settings
        # applies its theme here — main() always passes them,
        # and a window built without them must not restyle the
        # app behind a harness that sets its own stylesheet.
        self._theme = self._load_theme()
        if settings is not None:
            self._apply_stylesheet()
        self.setAcceptDrops(True)
        # Library + presets. library_dir is a test hook — the real
        # window uses ~/pixel-forge-library/ (or PIXELFORGE_LIBRARY).
        # Nothing is created on disk until the first save.
        # restore is a second hook: main() turns it off when a file
        # was given on the command line (that file wins over the
        # session), and tests turn it off to drive restore by hand.
        self.library = Library(library_dir)
        self.preset_store = PresetStore(self.library.directory)
        self.palette_store = PaletteStore(self.library.directory)
        self._applying_preset = False
        # Session (task 29): recent files + the last-loaded image,
        # video, and code, from session.json in the library folder.
        # Loaded at the end of __init__; restored once, when the
        # window is first shown (see showEvent).
        self._session = None
        self._session_corrupt = False
        self._session_restored = not restore
        # Undo (task 30): a per-window stack, capped at 50. An
        # undo/redo must not record itself, so every recording
        # hook checks _undo_suppress first; _tab_settings holds
        # each generation tab's last-known control state, and a
        # control change records the diff against it. Both are
        # set up at the end of __init__ — the control-change
        # signals fire during tab construction, before the
        # snapshot exists, and _record_control_change is a no-op
        # until then.
        self._undo_stack = UndoStack()
        self._undo_suppress = False
        self._tab_settings = None
        # The malformed-entries warning shows in the status bar
        # once per window; the Library tab's count label carries
        # it permanently (see _refresh_library).
        self._library_skip_warned = False
        # Library thumbnails, built once per entry and reused
        # across refreshes (task 24): keyed by entry id + the
        # file's size and mtime, so a changed file rebuilds but
        # typing in the search box does not. _thumb_builds counts
        # cache misses — the suite pins the "N refreshes, 1 build
        # per entry" rule against it.
        self._thumb_cache = {}
        self._thumb_builds = 0
        # Duplicate groups, cached against a fingerprint of the
        # library (entry ids + file sizes/mtimes) so the info
        # label does not re-hash every file on each refresh.
        self._duplicate_groups_cache = None
        self._duplicate_fingerprint = None
        self.source_image = None
        self.source_path = None
        # Original-pane pixmap cache (task 60): _refresh_pixmaps
        # rebuilds the Original pane on every zoom drag even
        # though the source image and its fit never change during
        # one. (source, fit, pixmap) — the cached source is held
        # so its identity stays meaningful; the source is
        # replaced, never mutated in place, so identity + fit is
        # a complete key.
        self._before_pixmap_cache = None
        # Background removal (task 41) and pixel-grid snap
        # (task 42): the worker running each job, or None. Like
        # the prompt worker, both are stopped in closeEvent.
        self.bgremove_worker = None
        self.gridsnap_worker = None
        # Normal map (task 43): the worker running a generation,
        # the generated map, and whether the result pane is
        # showing it instead of the sprite. The map belongs to
        # the result it was made from — any pixel change clears
        # it (see _refresh_result).
        self.normalmap_worker = None
        self.normal_map_image = None
        self._showing_normal_map = False
        # Update check (task 48): the worker running a check,
        # or None. The startup check runs once, after the window
        # is shown (see showEvent) — _update_check_started
        # keeps it to one run per launch.
        self.update_worker = None
        self._update_check_started = False
        # Procedural generation (task 44): the kind and seed
        # that made the loaded sprite, or None when the sprite
        # came from a file or was changed by another tool. The
        # seed label shows them so the sprite can be remade.
        self._generated_kind = None
        self._generated_seed = None
        self.result_image = None
        self._updating_size = False
        # Pixel editor (task 33): the result's edit state, the
        # chosen tool and color, and the drag in progress.
        # _result_original is the freshly pixelized result — the
        # edited flag is whether the live result still equals
        # it, recomputed after every undo/redo, so undoing all
        # edits clears the flag again. No tool and no color are
        # selected until the user picks them.
        self._result_edited = False
        self._result_original = None
        self._editor_tool = None
        self._editor_color = None
        self._editor_drag = None
        self._editor_swatch_by_color = {}
        # Layer stack (task 35): bottom first, index 0 is the
        # base. result_image is the cached composite — every
        # consumer (preview, export, library save, canvas,
        # color strip) reads the composite and never the stack
        # directly. _active_layer is the layer the editor
        # paints on; on load it is the base, the only layer,
        # which is the working layer, not a selection.
        self._layers = []
        self._active_layer = None

        # Video tab state. Raw frames live on disk in a temp dir;
        # only the small pixelized frames are held in memory.
        self.video_path = None
        self.video_raw_dir = None
        self.video_raw_frames = []
        self.video_frames = []
        self.video_keep = []
        # Strip order: frame indices in the order the strip shows
        # them. A drag-reorder rewrites this, and the export and
        # the sheet follow it (task 25) — _kept_indices reads it,
        # never plain range().
        self.video_order = []
        # Per-frame durations in milliseconds (task 34), indexed
        # by ORIGINAL frame number — parallel to video_frames and
        # video_keep. Indexing by frame, not by kept position,
        # means a duration follows its frame through a reorder or
        # a keep/drop with no bookkeeping at all. A new import
        # starts every frame at 1000 / Sheet fps; a re-pixelize
        # with the same frame count keeps them.
        self.video_durations = []
        # Animation tags (task 34): {"name", "from", "to"} dicts,
        # "from" and "to" 0-based positions in the KEPT order —
        # a tag is a range of positions in the current animation,
        # so a reorder moves frames under it and a keep/drop
        # clamps it (see _clamp_video_tags). A new extraction
        # clears them: the frames are entirely new.
        self.video_tags = []
        # The tag currently playing, or None for the whole kept
        # animation. Set only by "Play tag"; the main Play button
        # clears it and plays everything.
        self.video_play_tag = None
        # The clip's length in seconds, probed with ffprobe at
        # import (None = unknown — no estimate, no confirm).
        self.video_duration = None
        self.video_current = 0
        self._updating_video_size = False
        self.video_timer = QTimer(self)
        self.video_timer.timeout.connect(self._advance_video_frame)
        # Extraction + pixelize run on a worker thread (see
        # VideoWorker); control changes debounce so a dragged
        # slider runs once, with the settled settings.
        self.video_worker = None
        self._video_orphans = set()
        # A multi-file drop's summary, kept so the video run's
        # completion message does not erase it (review B15).
        self._drop_summary = None
        self._video_pending_reload = False
        self._video_debounce_timer = QTimer(self)
        self._video_debounce_timer.setSingleShot(True)
        self._video_debounce_timer.setInterval(VIDEO_DEBOUNCE_MS)
        self._video_debounce_timer.timeout.connect(
            self._start_scheduled_video_run)

        # Prompt tab state. Candidates live on disk in a batch dir
        # under the library folder (with batch.json); only the
        # keeper is held in memory. The history (past batches +
        # past prompts) loads from the library folder and is
        # written back on every generation.
        self.prompt_batch_dir = None
        self.prompt_worker = None
        self.prompt_candidates = []
        self.prompt_keeper = None
        self.prompt_keeper_image = None
        self.prompt_result_image = None
        # Prompt Original-pane pixmap cache (task 60): same
        # shape as _before_pixmap_cache, for the keeper image.
        self._prompt_before_pixmap_cache = None
        self._updating_prompt_size = False
        self._prompt_history = None
        self._prompt_history_corrupt = False
        # Elapsed time while generating: the timer ticks once a
        # second and refreshes the progress bar's text.
        self._prompt_started = None
        self._prompt_elapsed_timer = QTimer(self)
        self._prompt_elapsed_timer.setInterval(1000)
        self._prompt_elapsed_timer.timeout.connect(
            self._on_prompt_elapsed_tick)

        # Code tab state. Assets are small rendered buffer Images
        # held in memory; a skin also keeps its source path so its parts
        # can be exported to a folder. A texture loads on a worker
        # thread (see TextureWorker) so a big photo never freezes
        # the window.
        self.code_assets = []
        self.code_not_rendered = []
        self.code_skipped = []
        # The paths the current Code tab import was given, in
        # order (task 55: the autosave restores from these — the
        # session's last_code only covers code imports, not
        # skins and textures, which load through load_game_asset).
        self.code_paths = None
        self.code_source = None
        # The first source file of the current import — code_source
        # is a joined display string, so exports default next to
        # this one instead (task 22).
        self.code_source_first = None
        self.code_kind = None  # "code", "skin", or "texture"
        self.code_current = None
        self.code_image = None
        self.code_skin_path = None
        self.code_skin_layout = None
        self.texture_worker = None
        # Texture rendering state (task 26): the source image a
        # texture (or an overridden skin) re-pixelizes from, and
        # the skin override flag — per loaded asset, reset on
        # every new import. The debounce timer waits for the
        # texture controls to settle before re-rendering.
        self.code_texture_image = None
        self.code_texture_path = None
        self.code_treat_as_texture = False
        self._texture_debounce_timer = QTimer(self)
        self._texture_debounce_timer.setSingleShot(True)
        self._texture_debounce_timer.setInterval(TEXTURE_DEBOUNCE_MS)
        self._texture_debounce_timer.timeout.connect(
            self._on_texture_debounce)

        # Tileset tab state (task 37): the picked mode ("wang" /
        # "blob", None until one is picked), the base tiles, and
        # the generated set. A new mode or a new base tile clears
        # the generated set — it was built from the old ones.
        # Task 82: the picked blob shape ("square" / "rounded",
        # None until one is picked — until then the square
        # construction is what the app already does, so the
        # effective shape is "square"). A new shape clears the
        # generated set too.
        self.tileset_mode = None
        self.tileset_shape = None
        self.tileset_base_a = None
        self.tileset_base_a_path = None
        self.tileset_base_b = None
        self.tileset_base_b_path = None
        self.tileset_tiles = None
        self.tileset_sheet = None
        self.tileset_columns = None

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_image_tab(), "Image")
        self.tabs.addTab(self._build_video_tab(), "Video")
        self.tabs.addTab(self._build_prompt_tab(), "Prompt")
        self.tabs.addTab(self._build_code_tab(), "Code")
        self.tabs.addTab(self._build_library_tab(), "Library")
        self.tabs.addTab(self._build_tileset_tab(), "Tileset")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_menu_bar()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready — import an image to begin.")
        self._refresh_preset_combos()
        self._refresh_library()
        self._refresh_prompt_status()
        self._load_prompt_history()
        self._load_session()
        # Autosave (task 55): crash recovery for in-progress
        # work. The crash check runs first — a marker from a
        # previous run means it crashed — then this run writes
        # its own marker. Nothing is written until the recovery
        # question is settled in showEvent: an early write would
        # overwrite the recovery file with an empty state.
        self._autosave_dir = (Path(autosave_dir)
                              if autosave_dir is not None
                              else autosave_home())
        self._autosave_crashed = crashed(self._autosave_dir)
        if self._autosave_crashed:
            (self._autosave_recovery,
             self._autosave_corrupt) = load_recovery(
                 self._autosave_dir)
        try:
            mark_running(self._autosave_dir)
        except OSError:
            self._autosave_broken = True
            self.statusBar().showMessage(
                "Autosave is off. The recovery folder could not "
                "be written")
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._autosave_write)
        # Undo snapshots (task 30): the tabs exist now, so the
        # first control change has a "before" to record against.
        self._sync_tab_settings()
        self._refresh_undo_actions()


    # ================================================ menus, shortcuts, help

    def _build_menu_bar(self):
        """File + Edit + View + Help menus and the keyboard
        shortcuts (task 21; Edit is task 30; View is task 67).

        Import/Export are QActions with their shortcuts, so the
        menu shows the keys and the actions work from anywhere in
        the window. Tab switching is plain QShortcuts — there is
        no menu entry for it. Both use the default WindowShortcut
        context, so they do not fire while a modal dialog is open:
        the dialog is the active window then, not this one (pinned
        by a test — do not \"fix\" it by switching contexts).
        """
        file_menu = self.menuBar().addMenu("File")
        import_action = QAction("Import", self)
        import_action.setShortcut(QKeySequence("Ctrl+O"))
        import_action.triggered.connect(self._on_shortcut_import)
        file_menu.addAction(import_action)
        export_action = QAction("Export", self)
        export_action.setShortcut(QKeySequence("Ctrl+S"))
        export_action.triggered.connect(self._on_shortcut_export)
        file_menu.addAction(export_action)
        self._import_action = import_action
        self._export_action = export_action
        # Recent files (task 29): the submenu lists what was
        # loaded before, most recent first. It rebuilds when it
        # opens (aboutToShow) and whenever the session changes,
        # so it is never stale — a disabled menu never emits
        # aboutToShow, so the enabled state cannot be fixed only
        # at open time.
        recent_menu = file_menu.addMenu("Recent files")
        recent_menu.aboutToShow.connect(self._refresh_recent_menu)
        recent_menu.menuAction().setToolTip(
            "Open a file you loaded before")
        self._recent_menu = recent_menu

        # Undo/Redo (task 30): the action text names what the
        # step will do ("Undo Delete knight.png") and is rebuilt
        # by _refresh_undo_actions after every stack change.
        # Redo answers to both Ctrl+Y and Ctrl+Shift+Z.
        edit_menu = self.menuBar().addMenu("Edit")
        undo_action = QAction("Undo", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.setToolTip("Undo the last change")
        undo_action.triggered.connect(self._on_undo)
        edit_menu.addAction(undo_action)
        redo_action = QAction("Redo", self)
        redo_action.setShortcuts([QKeySequence("Ctrl+Y"),
                                  QKeySequence("Ctrl+Shift+Z")])
        redo_action.setToolTip("Redo the last undone change")
        redo_action.triggered.connect(self._on_redo)
        edit_menu.addAction(redo_action)
        self._undo_action = undo_action
        self._redo_action = redo_action

        # Light theme (task 67): a checkable action that mirrors
        # the current theme — checked means light is on. It is a
        # reflection of state, not a pre-selection.
        view_menu = self.menuBar().addMenu("View")
        light_action = QAction("Light theme", self)
        light_action.setCheckable(True)
        light_action.setChecked(self._theme == "light")
        light_action.setToolTip("Switch between the light and "
                                "dark themes")
        light_action.triggered.connect(self._on_toggle_theme)
        view_menu.addAction(light_action)
        self._light_theme_action = light_action

        help_menu = self.menuBar().addMenu("Help")
        about_action = QAction("About Pixel Forge", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        detects_action = QAction("What the Code tab detects", self)
        detects_action.triggered.connect(self._show_code_detects)
        help_menu.addAction(detects_action)
        library_action = QAction("Library location", self)
        library_action.triggered.connect(self._show_library_location)
        help_menu.addAction(library_action)
        # Update check (task 48): a manual check that works
        # whether or not the automatic one is on — the user
        # asking is not phoning home — and the opt-in itself, a
        # checkable action that mirrors the saved setting. Off
        # by default: nothing is checked, and no request is
        # made, until the user turns it on.
        help_menu.addSeparator()
        check_action = QAction("Check for updates", self)
        check_action.setToolTip("Ask GitHub whether a newer "
                                "Pixel Forge is out")
        check_action.triggered.connect(self._on_check_updates)
        help_menu.addAction(check_action)
        auto_action = QAction("Check for updates on startup", self)
        auto_action.setCheckable(True)
        auto_action.setChecked(self._load_update_check())
        auto_action.setToolTip("Check for a newer version each "
                               "time Pixel Forge starts")
        auto_action.triggered.connect(self._on_toggle_update_check)
        help_menu.addAction(auto_action)
        self._check_updates_action = check_action
        self._auto_update_action = auto_action

        # Ctrl+1..6 switch tabs in their current order (Image,
        # Video, Prompt, Code, Library, Tileset). Kept on self: a QShortcut
        # with no other owner is garbage-collected with its parent
        # — these are parented to the window, so they live as long
        # as it does, but the list keeps the intent explicit.
        self._tab_shortcuts = []
        for index in range(self.tabs.count()):
            shortcut = QShortcut(
                QKeySequence("Ctrl+%d" % (index + 1)), self)
            shortcut.activated.connect(
                lambda _checked=False, i=index:
                self.tabs.setCurrentIndex(i))
            self._tab_shortcuts.append(shortcut)


    # ------------------------------------------------------------ undo
    def _refresh_undo_actions(self):
        """The Edit menu's Undo/Redo entries: enabled only when
        the stack has something to do, and named after the step
        ("Undo Delete knight.png"). A name with & in it is
        doubled, or Qt eats the & as a mnemonic marker (the same
        rule as the Recent files submenu)."""
        stack = self._undo_stack
        undo_text = stack.undo_text
        if undo_text is None:
            self._undo_action.setText("Undo")
            self._undo_action.setEnabled(False)
        else:
            self._undo_action.setText(
                "Undo %s" % undo_text.replace("&", "&&"))
            self._undo_action.setEnabled(True)
        redo_text = stack.redo_text
        if redo_text is None:
            self._redo_action.setText("Redo")
            self._redo_action.setEnabled(False)
        else:
            self._redo_action.setText(
                "Redo %s" % redo_text.replace("&", "&&"))
            self._redo_action.setEnabled(True)


    def _push_undo(self, command):
        """Record a change that already happened, and refresh
        the Edit menu. Every recording site goes through here,
        so the menu is never stale."""
        self._undo_stack.push(command)
        self._refresh_undo_actions()


    def _on_undo(self):
        if self._undo_stack.can_undo:
            text = self._undo_stack.undo_text
            self._undo_suppress = True
            try:
                self._undo_stack.undo()
            finally:
                self._undo_suppress = False
            self._refresh_undo_actions()
            self.statusBar().showMessage("Undid %s" % text)


    def _on_redo(self):
        if self._undo_stack.can_redo:
            text = self._undo_stack.redo_text
            self._undo_suppress = True
            try:
                self._undo_stack.redo()
            finally:
                self._undo_suppress = False
            self._refresh_undo_actions()
            self.statusBar().showMessage("Redid %s" % text)


    # --------------------------------------------- control-change undo
    def _read_control_state(self, tab):
        """One tab's undoable control state: the six pixelize
        fields plus the picker selection. The Video tab's fps
        and sheet controls are NOT in here — they have their own
        handlers outside the control-change funnel, and undoing
        an extraction is out of scope (see the README)."""
        settings = self._read_tab_settings(tab)
        state = {key: settings[key] for key in
                 ("width", "height", "keep_aspect", "palette",
                  "max_colors", "dither")}
        state["preset"] = self._tab_controls(tab)[
            "preset_combo"].currentData()
        return state


    def _sync_tab_settings(self):
        """Re-read every tab's control state as the new
        "before" — called after a picker rebuild or a programmatic
        picker change, so a preset save/delete never leaves a
        phantom picker-only undo step behind."""
        self._tab_settings = {
            tab: self._read_control_state(tab)
            for tab in ("image", "video", "prompt")}


    def _record_control_change(self, tab):
        """A control on this tab changed — if the state differs
        from the snapshot, push one undo step and move the
        snapshot on. A no-op while the window is still being
        built (no snapshot yet), while an undo/redo is applying,
        and while a preset is being applied (the preset handler
        records the whole application as one step itself)."""
        if self._undo_suppress or self._tab_settings is None:
            return
        current = self._read_control_state(tab)
        before = self._tab_settings[tab]
        if current == before:
            return
        self._tab_settings[tab] = current
        self._push_undo(_ControlChangeCommand(
            self, tab, before, current,
            self._control_change_text(before, current)))
        self._mark_autosave_dirty()


    def _control_change_text(self, before, after):
        """A short name for a control change, for the Edit menu.
        A preset pick is named after the preset; a single-field
        change after the field; several fields at once is just
        "Change controls"."""
        if (after.get("preset") is not None
                and after["preset"] != before.get("preset")):
            return "Apply preset %s" % after["preset"].split(
                ":", 1)[1]
        labels = {"width": "width", "height": "height",
                  "keep_aspect": "aspect", "palette": "palette",
                  "max_colors": "colors", "dither": "dither"}
        changed = [labels[key] for key in labels
                   if before.get(key) != after.get(key)]
        if len(changed) == 1:
            return "Change %s" % changed[0]
        return "Change controls"


    def _apply_control_state(self, tab, state):
        """Set a tab's controls from a recorded state (undo/redo).
        The preset apply fires the normal control signals, so the
        preview re-pixelizes through the usual path — recording
        is suppressed, and the picker is set without applying
        (the settings are already in the state)."""
        self._undo_suppress = True
        self._applying_preset = True
        try:
            self._apply_settings_to_tab(tab, state)
            self._select_preset(tab, state.get("preset"))
        finally:
            self._applying_preset = False
            self._undo_suppress = False
        self._tab_settings[tab] = dict(state)


    # -------------------------------------------------- keep/drop undo
    def _apply_keep(self, kept):
        """Set the Video strip's kept set from a recorded list of
        booleans (undo/redo): the check states, the dimming, the
        buttons, the counter, and the preview all come back.

        A list that does not match the current frame count names
        frames that no longer exist — a new import or a split
        replaced them — so it is ignored instead of applied:
        applying it indexed out of range and crashed the undo
        (found while landing split frames, task 40)."""
        if len(kept) != len(self.video_frames):
            return
        self.video_keep = list(kept)
        self.video_strip.blockSignals(True)
        try:
            for row in range(self.video_strip.count()):
                item = self.video_strip.item(row)
                index = item.data(Qt.ItemDataRole.UserRole)
                if index is None:
                    continue
                keep = self.video_keep[index]
                item.setCheckState(
                    Qt.CheckState.Checked if keep
                    else Qt.CheckState.Unchecked)
                self._set_strip_item_dimmed(item, not keep)
        finally:
            self.video_strip.blockSignals(False)
        self._refresh_after_keep_change()


    # ------------------------------------------------ library trash
    def _trash_dir_for(self, entry_id):
        """Where a deleted entry's files wait while the delete
        is still undoable: trash/<id>/ inside the library
        folder. Export and import iterate the index, never the
        directory, so the trash folder is invisible to them."""
        return self.library.directory / "trash" / entry_id


    def _on_shortcut_import(self):
        """Ctrl+O: import for the current tab. The Prompt tab has
        nothing to import (silent no-op); the Library tab has no
        import of its own, so it takes the user to the Image tab
        and opens its import dialog."""
        index = self.tabs.currentIndex()
        if index == 0:
            self._on_import()
        elif index == 1:
            self._on_video_import()
        elif index == 3:
            self._on_import_code()
        elif index == 4:
            self.tabs.setCurrentIndex(0)
            self._on_import()
        elif index == 5:
            self._on_tileset_pick_a()

        # Index 2 (Prompt): nothing to import — do nothing.

    def _on_shortcut_export(self):
        """Ctrl+S: export for the current tab. Every handler below
        is already a silent no-op when its tab has nothing to
        export (no result, no kept frames, no keeper, no asset,
        no library selection), so this never pops a dialog or
        crashes on an empty tab."""
        index = self.tabs.currentIndex()
        if index == 0:
            self._on_export()
        elif index == 1:
            self._on_export_frames()
        elif index == 2:
            self._on_export_prompt_keeper()
        elif index == 3:
            self._on_export_code_asset()
        elif index == 4:
            self._on_library_export()
        elif index == 5:
            self._on_tileset_export_png()


    def _about_text(self):
        """About box text. The version comes from __version__, so
        it can never drift from the shipped version."""
        return ("Pixel Forge %s\n\n"
                "Turn images, videos, prompts, and source code "
                "into reusable pixel art and sprites — all on "
                "your own machine, no account, no cloud."
                % __version__)


    def _show_about(self):
        QMessageBox.about(self, "About Pixel Forge",
                          self._about_text())


    def _code_detects_text(self):
        """What the Code tab finds — every claim here is grounded
        in codeinput.py and skins.py. Keep it in sync with them:
        the test pins the key phrases, not the exact text."""
        return (
            "What the Code tab detects\n"
            "\n"
            "Source code (.py, .js, .ts, .json, .cs, .gd, .lua) "
            "is read statically — it is never run, imported, or "
            "executed. Only literal values in the code are "
            "rendered. Items appear in the order they occur in "
            "the file.\n"
            "\n"
            "Pixel maps — a variable holding rows of pixels:\n"
            "- int rows: numbers, where 0 is transparent and the "
            "rest are palette colors (a palette from the same "
            "file when one covers the values, otherwise the "
            "built-in PICO-8 colors in order)\n"
            "- hex rows: color strings like \"#ff0000\" (empty, "
            "None, and \"transparent\" are transparent)\n"
            "- RGB rows: (red, green, blue) or "
            "(red, green, blue, alpha) tuples\n"
            "- char rows: strings of characters, with a dict in "
            "the same file mapping each character to a color "
            "(\".\" and space are always transparent)\n"
            "\n"
            "Palettes — a flat list of colors, or a dict mapping "
            "names, numbers, or characters to colors. Rendered "
            "as a strip, one block per color. Colors can be hex "
            "strings, RGB tuples, named colors, or 0xAARRGGBB "
            "ints (alpha first).\n"
            "\n"
            "Single colors: one color on its own (a Color in "
            "C# or GDScript, a {1, 0, 0} table in Lua, a tuple "
            "or a \"#ff0000\" string) is shown as a small swatch.\n"
            "\n"
            "JSON sprites: a .json file describing a sprite: "
            "Aseprite frame exports (frames plus the sheet image "
            "in meta), a pixel list with a palette, a flat pixel "
            "list with width and height, or a plain list of rows "
            "or colors.\n"
            "\n"
            "Image references: when code names an image file "
            "next to it (load, preload, newImage, "
            "Resources.Load, or a plain \"hero.png\" string), "
            "the image is loaded and shown like any other asset. "
            "A missing file is listed with the reason, and "
            "absolute paths are never followed.\n"
            "\n"
            "Rectangles: a rect or frame region (Rect, Rect2, "
            "newQuad, or an {x, y, w, h} dict) is listed as "
            "found but not rendered: it is a frame region, not "
            "pixels.\n"
            "\n"
            "Game assets — a 64x64 or 64x32 PNG is treated as a "
            "Minecraft skin and split into its parts (head, "
            "body, arms, legs, and overlays; the 64x32 classic "
            "layout mirrors the left limbs). Any other image "
            "pixelizes as a texture at its native size.\n"
            "\n"
            "Limits:\n"
            "- maps are capped at 512 x 512; rows must all be "
            "the same length\n"
            "- anything computed — function calls, loops, "
            "arithmetic — is reported as found but not "
            "rendered, never faked\n"
            "- anything drawn by a game engine cannot be detected\n"
            "- skins are detected by size only: a non-skin "
            "64x64 image would be misread as a skin\n"
            "- code files over 5 MB are refused before parsing, "
            "with the size named\n")


    def _show_code_detects(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("What the Code tab detects")
        dialog.resize(560, 460)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self._code_detects_text())
        layout.addWidget(text)
        close_btn = QPushButton("Close")
        close_btn.setToolTip("Close this dialog")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()


    def _library_location_text(self):
        """The library-location message — the actual folder,
        honoring PIXELFORGE_LIBRARY (via the Library object)."""
        return ("Sprites are stored in:\n%s"
                % self.library.directory)


    def _open_library_folder(self):
        """Open the library folder in the file manager."""
        subprocess.Popen(["xdg-open", str(self.library.directory)])


    def _show_library_location(self):
        box = QMessageBox(self)
        box.setWindowTitle("Library location")
        box.setText(self._library_location_text())
        open_btn = box.addButton(
            "Open folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_library_folder()


    @staticmethod
    def _section(text):
        label = QLabel(text)
        label.setObjectName("heading")
        return label


    @staticmethod
    def _scrollable_column(controls):
        """Wrap a tab's control column in a vertical scroll area.

        v0.7: the columns are taller than the 768 px target screen
        (the Video column alone needs 817 px), and a bare column
        forced the whole window taller than the screen, putting
        the Export buttons out of reach. Inside a scroll area the
        window can shrink to ~700 px or less and every control
        stays reachable by scrolling. The scroll area is fixed at
        268 px wide — 250 px of controls plus room for the
        vertical scrollbar and the frame — so the column keeps
        its v0.6 width; the inner widget stretches to the
        viewport width, so it renders a few px wider than v0.6
        when no scrollbar is showing. Horizontal scrolling is
        off: the column never scrolls sideways.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(controls)
        scroll.setFixedWidth(268)
        return scroll


    def _build_preset_section(self, form, tab):
        """The preset picker + Save/Delete buttons for one tab.

        tab is "image", "video", or "prompt" — the widgets live
        under that tab's attribute prefix ("" / "video_" /
        "prompt_"). The three pickers share one preset set; each
        one applies to its own tab's controls.
        """
        prefix = {"image": "", "video": "video_",
                  "prompt": "prompt_"}[tab]
        form.addWidget(self._section("Preset"))
        combo = QComboBox()
        combo.setToolTip(
            "Apply a saved set of size, palette, colors, and "
            "dithering")
        combo.currentIndexChanged.connect(
            lambda _i, t=tab: self._on_library_preset(t))
        form.addWidget(combo)
        setattr(self, prefix + "preset_combo", combo)
        # A skipped user preset warns HERE, in the open — the old
        # status-bar-only message was easy to miss, and the
        # pickers silently dropping to built-ins-only looked like
        # the user's presets had vanished. Hidden when there is
        # nothing to warn about; updated by _refresh_preset_combos.
        warning = QLabel()
        # Task 67: the warning color lives in the stylesheets
        # (QLabel#warning), one color per theme — the inline
        # #ffb066 would vanish on the light background.
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        warning.setVisible(False)
        form.addWidget(warning)
        setattr(self, prefix + "preset_warning_label", warning)
        button_row = QHBoxLayout()
        # Slimmer horizontal padding than the stylesheet's 14 px:
        # at full padding these two buttons measure 156 + 107 px,
        # and the row (269 px with spacing) is wider than the
        # column's 252 px viewport whenever the scrollbar shows —
        # so the whole column overflowed and the right edge of
        # every widget clipped (the counter read "0 / 10", the
        # status lines broke mid-word). At 6 px the row is 237 px
        # and everything fits. Found live in the v0.33 polish
        # pass: every earlier screenshot ran without the dark
        # stylesheet, where the default padding is smaller and
        # the row fit.
        save_btn = QPushButton("Save current as preset")
        save_btn.setStyleSheet(
            "padding-left: 6px; padding-right: 6px;")
        save_btn.setToolTip(
            "Save the current controls as a named preset")
        save_btn.clicked.connect(
            lambda _c, t=tab: self._on_save_preset(t))
        button_row.addWidget(save_btn)
        setattr(self, prefix + "preset_save_btn", save_btn)
        delete_btn = QPushButton("Delete preset")
        delete_btn.setStyleSheet(
            "padding-left: 6px; padding-right: 6px;")
        delete_btn.setToolTip("Delete the selected preset")
        delete_btn.setEnabled(False)
        delete_btn.clicked.connect(
            lambda _c, t=tab: self._on_delete_preset(t))
        button_row.addWidget(delete_btn)
        setattr(self, prefix + "preset_delete_btn", delete_btn)
        form.addLayout(button_row)
        # A second row for sharing: Export writes the picked
        # preset to a JSON file, Import reads one back. Same
        # slim padding — the row fits the 252 px viewport.
        share_row = QHBoxLayout()
        export_btn = QPushButton("Export preset")
        export_btn.setStyleSheet(
            "padding-left: 6px; padding-right: 6px;")
        export_btn.setToolTip(
            "Save the selected preset as a JSON file to share")
        export_btn.setEnabled(False)
        export_btn.clicked.connect(
            lambda _c, t=tab: self._on_export_preset(t))
        share_row.addWidget(export_btn)
        setattr(self, prefix + "preset_export_btn", export_btn)
        import_btn = QPushButton("Import preset")
        import_btn.setStyleSheet(
            "padding-left: 6px; padding-right: 6px;")
        import_btn.setToolTip(
            "Load a preset from a shared JSON file")
        import_btn.clicked.connect(
            lambda _c, t=tab: self._on_import_preset(t))
        share_row.addWidget(import_btn)
        setattr(self, prefix + "preset_import_btn", import_btn)
        form.addLayout(share_row)


    # ================================================ session (task 29)

    def _load_session(self):
        """Load the session (recent files + last-loaded image,
        video, and code) from the library folder. A corrupt file
        reads as empty and warns once in the status bar — startup
        never breaks on it, and the file is left alone (not
        deleted, not "repaired")."""
        (self._session,
         self._session_corrupt) = load_session(self.library.directory)
        self._refresh_recent_menu()
        if self._session_corrupt:
            self.statusBar().showMessage(
                "The session file is unreadable — starting with an "
                "empty session (the file was left alone)")


    def _save_session(self):
        """Write the session back. A failure shows in the status
        bar and never breaks the load it rode along with."""
        if self._session is None:
            return
        try:
            save_session(self.library.directory, self._session)
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))


    def _record_session(self, path, kind):
        """A file loaded successfully (task 29): it goes to the
        top of the recent list, and an image or video also
        becomes the one restored on the next startup. A game
        asset records as kind "skin" — it loads through
        load_game_asset either way, skin or texture."""
        if self._session is None:
            return
        record_recent(self._session, str(path), kind)
        if kind == "image":
            set_last_image(self._session, str(path))
        elif kind == "video":
            set_last_video(self._session, str(path))
        self._save_session()
        self._refresh_recent_menu()
        self._mark_autosave_dirty()


    def _record_code_session(self, paths, files):
        """A code import loaded successfully (task 29): the paths
        load_code was given become the ones restored on the next
        startup, and every file actually parsed goes to the
        recent list — a folder import records its files, not the
        folder."""
        if self._session is None:
            return
        set_last_code(self._session, [str(p) for p in paths])
        for found in files:
            record_recent(self._session, str(found), "code")
        self._save_session()
        self._refresh_recent_menu()
        self._mark_autosave_dirty()


    def _refresh_recent_menu(self):
        """Rebuild the Recent files submenu from the session —
        most recent first, each entry named with its kind. Called
        when the menu opens and whenever the session changes, so
        the list and the enabled state are never stale. A "&" in
        a file name is doubled: Qt reads a bare & in menu text as
        a mnemonic marker and would eat it."""
        menu = self._recent_menu
        menu.clear()
        recent = self._session["recent"] if self._session else []
        menu.setEnabled(bool(recent))
        for entry in recent:
            text = "%s — %s" % (Path(entry["path"]).name.replace(
                "&", "&&"), entry["kind"])
            action = QAction(text, self)
            action.setData((entry["path"], entry["kind"]))
            action.triggered.connect(self._on_recent_file)
            menu.addAction(action)


    def _on_recent_file(self, _checked=False):
        """Open a recent file into its tab, exactly as if it had
        just been imported. A file that no longer exists is
        pruned from the list with one plain status-bar note — no
        modal, no crash."""
        action = self.sender()
        if action is None:
            return
        path, kind = action.data()
        if not Path(path).exists():
            self._session["recent"] = [
                e for e in self._session["recent"]
                if e["path"] != path]
            self._save_session()
            self._refresh_recent_menu()
            self.statusBar().showMessage(
                "No longer there: %s" % Path(path).name)
            return
        if kind == "image":
            self.tabs.setCurrentIndex(0)
            self.load_image(path)
        elif kind == "video":
            self.tabs.setCurrentIndex(1)
            self.load_video(path)
        elif kind == "code":
            self.tabs.setCurrentIndex(3)
            self.load_code([path])
        elif kind == "skin":
            self.tabs.setCurrentIndex(3)
            self.load_game_asset(path)


    def restore_session(self):
        """Restore the last-loaded image, video, and code into
        their tabs (task 29). Each loads only when its file still
        exists — a missing file is skipped with one status-bar
        note at most, never a modal. The loads go through the
        normal import paths, so the video restores on its worker
        thread (the v0.9 stale guard covers a user who starts
        something else mid-restore) and nothing switches tabs:
        the Image tab stays current, and the restored video and
        code are waiting on their own tabs. A clip over the
        frame-count threshold still asks the extraction question
        — the guardrail does not know the clip was restored."""
        if self._session is None:
            return
        skipped = []
        image = self._session.get("last_image")
        if image:
            if Path(image).is_file():
                self.load_image(image)
            else:
                skipped.append(Path(image).name)
        video = self._session.get("last_video")
        if video:
            if Path(video).is_file():
                self.load_video(video)
            else:
                skipped.append(Path(video).name)
        last_code = self._session.get("last_code") or []
        existing = [p for p in last_code if Path(p).exists()]
        if existing:
            self.load_code(existing)
        elif last_code:
            skipped.extend(Path(p).name for p in last_code)
        if skipped:
            self.statusBar().showMessage(
                "Could not restore %s — no longer there"
                % ", ".join(skipped))


    # --------------------------------------------------------- autosave
    # Task 55: crash recovery for in-progress work. The state is
    # references + settings (see pixel_forge/autosave.py for the
    # payload per tab); only an edited Image result carries
    # pixels. Writes are debounced, atomic, and skipped when
    # nothing changed — a quiet window costs one comparison per
    # flush, never a disk write.

    def _autosave_state(self):
        """The current work as a recovery state (see
        pixel_forge/autosave.py). Cheap: paths, control values,
        and small lists — no pixel reads."""
        from datetime import datetime, timezone

        state = empty_recovery()
        state["saved_at"] = datetime.now(timezone.utc).isoformat()
        state["current_tab"] = self.tabs.currentIndex()
        generated = None
        if (self._generated_kind is not None
                and self.source_image is not None):
            # The size is the sprite's own size — the tab's
            # width spin may have moved on since generation.
            generated = {"kind": self._generated_kind,
                         "seed": self._generated_seed,
                         "size": self.source_image.width()}
        state["image"] = {
            "source": self.source_path,
            "generated": generated,
            "settings": self._read_tab_settings("image"),
            "preset": self._tab_controls("image")[
                "preset_combo"].currentData(),
            "edited": bool(self._result_edited),
        }
        state["video"] = {
            "source": self.video_path,
            "settings": self._read_tab_settings("video"),
            "preset": self._tab_controls("video")[
                "preset_combo"].currentData(),
            "start": self.video_start_edit.text(),
            "end": self.video_end_edit.text(),
            "every": self.video_every_spin.value(),
            "count": len(self.video_frames),
            "keep": list(self.video_keep),
            "order": list(self.video_order),
            "durations": list(self.video_durations),
            "tags": [dict(tag) for tag in self.video_tags],
        }
        state["prompt"] = {
            "batch_dir": (str(self.prompt_batch_dir)
                          if self.prompt_batch_dir is not None
                          else None),
            "prompt": self.prompt_edit.text(),
            "negative": self.prompt_negative_edit.text(),
            "steps": self.prompt_steps_spin.value(),
            "cfg": self.prompt_cfg_spin.value(),
            "size": self.prompt_gen_size_spin.value(),
            "settings": self._read_tab_settings("prompt"),
            "preset": self._tab_controls("prompt")[
                "preset_combo"].currentData(),
        }
        state["code"] = {
            "paths": (list(self.code_paths)
                      if self.code_paths else []),
            "kind": self.code_kind,
            "current": self.code_current,
        }
        state["tileset"] = {
            "mode": self.tileset_mode,
            "shape": self.tileset_shape,
            "base_a": self.tileset_base_a_path,
            "base_b": self.tileset_base_b_path,
        }
        return state


    def _mark_autosave_dirty(self):
        """The work changed — restart the debounce timer. A
        no-op until the recovery question is settled, and while
        autosave is broken."""
        if not self._autosave_ready or self._autosave_broken:
            return
        self._autosave_dirty = True
        self._autosave_timer.start()


    def _autosave_write_now(self):
        """Flush the autosave at once (tab switch, long
        operation) — the change check still applies, so a quiet
        tab costs one comparison."""
        if not self._autosave_ready or self._autosave_broken:
            return
        self._autosave_dirty = True
        self._autosave_timer.stop()
        self._autosave_write()


    def _autosave_write(self):
        """Write the recovery state when the work changed. The
        saved_at timestamp is not part of the comparison — it
        moves on every write, so comparing it would defeat the
        check. A missing recovery file also counts as changed:
        another window may have discarded it, and the next write
        must put the state back."""
        if (not self._autosave_ready or self._autosave_broken
                or not self._autosave_dirty):
            return
        state = self._autosave_state()
        comparable = {key: value for key, value in state.items()
                      if key != "saved_at"}
        last = self._autosave_last
        last_comparable = None if last is None else {
            key: value for key, value in last.items()
            if key != "saved_at"}
        if (comparable == last_comparable
                and recovery_path(self._autosave_dir).is_file()):
            self._autosave_dirty = False
            return
        try:
            gui_module.save_recovery(self._autosave_dir, state)
            if (state["image"]["edited"]
                    and self.result_image is not None):
                save_recovery_image(self._autosave_dir,
                                    self.result_image)
            else:
                # No unsaved edits — drop a stale recovery image
                # so an old edited result is never restored by
                # mistake.
                recovery_image_path(
                    self._autosave_dir).unlink(missing_ok=True)
        except (PixelForgeError, OSError, ValueError) as exc:
            self.statusBar().showMessage(
                "Autosave could not write: %s" % exc)
            return
        self._autosave_last = state
        self._autosave_dirty = False


    def _offer_recovery(self):
        """Offer the crashed run's work back, once. Returns True
        when a recovery was accepted and restored — the caller
        then skips the normal session restore. Anything else
        returns False and the session restore runs as usual."""
        if not self._autosave_crashed:
            return False
        if self._autosave_corrupt:
            self.statusBar().showMessage(
                "Pixel Forge closed unexpectedly last time, and "
                "the recovery file could not be read, so this "
                "is a fresh start")
            return False
        state = self._autosave_recovery
        if state is None or not self._recovery_has_work(state):
            return False
        if not self._ask_recovery(self._recovery_summary(state)):
            # Declined: the user said to start fresh, so the
            # recovery file goes — it is an explicit discard.
            discard_recovery(self._autosave_dir)
            return False
        self._restore_recovery(state)
        return True


    def _recovery_has_work(self, state):
        """True when the recovered state holds anything worth
        offering — a crash with nothing open starts fresh
        silently, with no dialog."""
        image = state.get("image") or {}
        video = state.get("video") or {}
        prompt = state.get("prompt") or {}
        code = state.get("code") or {}
        tileset = state.get("tileset") or {}
        return bool(
            image.get("source") or image.get("generated")
            or video.get("source")
            or prompt.get("batch_dir")
            or (prompt.get("prompt") or "").strip()
            or code.get("paths")
            or tileset.get("base_a") or tileset.get("base_b"))


    def _recovery_summary(self, state):
        """One plain line per tab with work in it, for the
        recovery dialog — file names, not paths."""
        lines = []
        image = state.get("image") or {}
        if image.get("source"):
            line = "Image: %s" % Path(image["source"]).name
            if image.get("edited"):
                line += " (with edits not saved yet)"
            lines.append(line)
        elif image.get("generated"):
            lines.append("Image: a generated sprite")
        video = state.get("video") or {}
        if video.get("source"):
            lines.append("Video: %s" % Path(video["source"]).name)
        prompt = state.get("prompt") or {}
        if prompt.get("batch_dir"):
            text = (prompt.get("prompt") or "").strip()
            if text:
                lines.append("Prompt: %s" % text)
            else:
                lines.append("Prompt: a generated batch")
        elif (prompt.get("prompt") or "").strip():
            lines.append("Prompt: %s"
                         % (prompt["prompt"] or "").strip())
        code = state.get("code") or {}
        if code.get("paths"):
            lines.append("Code: %s"
                         % Path(code["paths"][0]).name)
        tileset = state.get("tileset") or {}
        if tileset.get("base_a") or tileset.get("base_b"):
            lines.append("Tileset: base tiles picked")
        return "\n".join(lines)


    def _ask_recovery(self, summary):
        """The recovery dialog. "Bring back" is the default —
        starting fresh discards the recovery file, so it is the
        destructive choice and never the default (U23)."""
        box = QMessageBox(self)
        box.setWindowTitle("Bring back your work?")
        box.setText("Pixel Forge closed unexpectedly last time. "
                    "Bring back the work you had open?")
        box.setInformativeText(summary)
        bring = box.addButton(
            "Bring back", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(
            "Start fresh", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(bring)
        box.exec()
        return box.clickedButton() is bring


    def _apply_recovery_settings(self, tab, tab_state):
        """Apply a recovered tab's pixelize settings + preset.
        A stored settings dict that fails validation is skipped
        — the tab keeps its defaults instead of applying
        nonsense from a hand-edited file. Goes through the undo
        path's apply, so the restore itself records no undo step
        and the next real change records against the restored
        state."""
        if not isinstance(tab_state, dict):
            return
        settings = tab_state.get("settings")
        if isinstance(settings, dict):
            try:
                clean = validate_settings(settings)
            except PixelForgeError:
                clean = None
            if clean is not None:
                clean["preset"] = tab_state.get("preset")
                self._apply_control_state(tab, clean)


    def _restore_recovery(self, state):
        """Bring the crashed run's work back, tab by tab. A file
        that no longer exists is skipped and named at the end —
        one missing file never blocks the rest. The recovery
        file is NOT deleted here: restoring must never destroy
        the only copy (a second crash during restore would lose
        it). It is replaced by the next autosave write, and
        discarded on a clean exit."""
        skipped = []
        self._restore_recovery_image(state.get("image"), skipped)
        self._restore_recovery_video(state.get("video"), skipped)
        self._restore_recovery_prompt(state.get("prompt"), skipped)
        self._restore_recovery_code(state.get("code"), skipped)
        self._restore_recovery_tileset(state.get("tileset"),
                                       skipped)
        tab = state.get("current_tab")
        if (isinstance(tab, int) and not isinstance(tab, bool)
                and 0 <= tab < self.tabs.count()):
            self.tabs.setCurrentIndex(tab)
        # The restored state IS the recovery file's content, so
        # the next autosave write is correctly skipped as
        # unchanged.
        self._autosave_last = state
        if skipped:
            self.statusBar().showMessage(
                "Brought back your work from last time. Could "
                "not bring back %s (no longer there)"
                % ", ".join(skipped))
        else:
            self.statusBar().showMessage(
                "Brought back your work from last time.")


    def _restore_recovery_image(self, image_state, skipped):
        if not isinstance(image_state, dict):
            return
        self._apply_recovery_settings("image", image_state)
        source = image_state.get("source")
        if source:
            if not Path(source).is_file():
                skipped.append(Path(source).name)
                return
            if not self.load_image(source):
                skipped.append(Path(source).name)
                return
        else:
            generated = image_state.get("generated")
            if isinstance(generated, dict):
                self._restore_generated_sprite(generated)
                if self.source_image is None:
                    skipped.append("the generated sprite")
        if image_state.get("edited"):
            recovered = load_recovery_image(self._autosave_dir)
            if recovered is None and self.result_image is not None:
                # The edits were recorded but their pixels are
                # gone — say so instead of pretending.
                name = (Path(source).name if source
                        else "the generated sprite")
                skipped.append("the edits to %s" % name)
            if recovered is not None and self.result_image \
                    is not None:
                # The edited pixels come back as the base layer;
                # the fresh pixelize result stays the original,
                # so _recompute_edited still derives the flag
                # from the stack (recovered != original). The
                # layer structure is not recovered — only the
                # composited result.
                self._layers = [Layer("Background", recovered,
                                      True)]
                self._active_layer = self._layers[0]
                self._refresh_result()
                self._refresh_layer_list()
                self._update_editor_enabled()


    def _restore_generated_sprite(self, generated):
        """Regenerate a procedural sprite from its recorded kind
        + seed + size — the same sprite, exactly (task 44)."""
        kind = generated.get("kind")
        seed = generated.get("seed")
        size = generated.get("size")
        if (kind not in procedural.KINDS
                or not isinstance(seed, int)
                or not isinstance(size, int)
                or not 8 <= size <= 512):
            return
        try:
            sprite = procedural.generate(kind, seed, size=size)
        except PixelForgeError:
            return
        self.source_image = sprite
        self.source_path = None
        self._generated_kind = kind
        self._generated_seed = seed
        self.procedural_seed_label.setText(
            "Seed %d, %s. Type the seed in to make this "
            "sprite again." % (seed, procedural.KIND_LABELS[kind]))
        self.update_preview()
        self._on_fit_image_zoom()


    def _restore_recovery_video(self, video_state, skipped):
        if not isinstance(video_state, dict):
            return
        self._apply_recovery_settings("video", video_state)
        start = video_state.get("start")
        if isinstance(start, str):
            self.video_start_edit.setText(start)
        end = video_state.get("end")
        if isinstance(end, str):
            self.video_end_edit.setText(end)
        every = video_state.get("every")
        if (isinstance(every, int) and not isinstance(every, bool)
                and 1 <= every <= 60):
            self.video_every_spin.setValue(every)
        source = video_state.get("source")
        if not source:
            return
        if not Path(source).is_file():
            skipped.append(Path(source).name)
            return
        # The range belongs to THIS clip (the same file), so the
        # fresh-import clear is skipped — otherwise the
        # re-extraction would cover the whole clip and the
        # keep/drop set would name the wrong frames.
        if not self.load_video(source, keep_range=True):
            return
        # The frames land asynchronously; the keep/drop set is
        # applied in _on_video_run_done, once the frame count is
        # known.
        self._autosave_pending_video = {
            "source": str(source),
            "count": video_state.get("count"),
            "keep": video_state.get("keep"),
            "order": video_state.get("order"),
            "durations": video_state.get("durations"),
            "tags": video_state.get("tags"),
        }


    def _apply_pending_video_restore(self):
        """Apply a crash-recovery keep/drop set to the frames
        that just landed. The set names frames by position, so
        it applies only when the re-extraction yielded the same
        video and the same frame count — anything else drops
        the set instead of mislabeling frames."""
        pending = self._autosave_pending_video
        self._autosave_pending_video = None
        if pending is None:
            return
        if pending.get("source") != self.video_path:
            return
        count = pending.get("count")
        keep = pending.get("keep")
        order = pending.get("order")
        durations = pending.get("durations")
        tags = pending.get("tags")
        if (not isinstance(count, int)
                or isinstance(count, bool)
                or count != len(self.video_frames)
                or not isinstance(keep, list)
                or len(keep) != count
                or not isinstance(order, list)
                or len(order) != count
                or not all(isinstance(item, int)
                           and not isinstance(item, bool)
                           for item in order)
                or sorted(order) != list(range(count))
                or not isinstance(durations, list)
                or len(durations) != count
                or not all(isinstance(item, int)
                           and not isinstance(item, bool)
                           for item in durations)
                or not isinstance(tags, list)):
            return
        self.video_keep = [bool(item) for item in keep]
        self.video_order = [int(item) for item in order]
        self.video_durations = [int(item) for item in durations]
        self.video_tags = [dict(item) for item in tags
                           if isinstance(item, dict)]
        self._refresh_video_tag_list()
        self._autosave_video_restored = True


    def _restore_recovery_prompt(self, prompt_state, skipped):
        if not isinstance(prompt_state, dict):
            return
        self._apply_recovery_settings("prompt", prompt_state)
        bdir = prompt_state.get("batch_dir")
        if bdir:
            if not Path(bdir).is_dir():
                skipped.append(Path(bdir).name)
            else:
                try:
                    batch = load_batch(bdir)
                except PixelForgeError:
                    skipped.append(Path(bdir).name)
                    batch = None
                if batch is not None:
                    landed = [c for c in batch["candidates"]
                              if c["status"]
                              in ("pending", "kept")]
                    if landed:
                        self._load_prompt_batch(batch, Path(bdir),
                                                landed)
                    else:
                        skipped.append(Path(bdir).name)
        # The edit boxes may hold a newer prompt than the batch
        # (typed after the last generation), so their text wins.
        if isinstance(prompt_state.get("prompt"), str):
            self.prompt_edit.setText(prompt_state["prompt"])
        if isinstance(prompt_state.get("negative"), str):
            self.prompt_negative_edit.setText(
                prompt_state["negative"])
        steps = prompt_state.get("steps")
        if (isinstance(steps, int) and not isinstance(steps, bool)
                and self.prompt_steps_spin.minimum()
                <= steps <= self.prompt_steps_spin.maximum()):
            self.prompt_steps_spin.setValue(steps)
        cfg = prompt_state.get("cfg")
        if (isinstance(cfg, (int, float))
                and not isinstance(cfg, bool)
                and self.prompt_cfg_spin.minimum()
                <= cfg <= self.prompt_cfg_spin.maximum()):
            self.prompt_cfg_spin.setValue(cfg)
        size = prompt_state.get("size")
        if (isinstance(size, int) and not isinstance(size, bool)
                and self.prompt_gen_size_spin.minimum()
                <= size <= self.prompt_gen_size_spin.maximum()):
            self.prompt_gen_size_spin.setValue(size)


    def _restore_recovery_code(self, code_state, skipped):
        if not isinstance(code_state, dict):
            return
        paths = code_state.get("paths") or []
        existing = [p for p in paths
                    if isinstance(p, str) and Path(p).exists()]
        if not existing:
            if paths:
                skipped.extend(Path(p).name for p in paths
                               if isinstance(p, str))
            return
        kind = code_state.get("kind")
        if kind in ("skin", "texture"):
            if not self.load_game_asset(existing[0]):
                return
        else:
            if not self.load_code(existing):
                return
        current = code_state.get("current")
        if (isinstance(current, int)
                and not isinstance(current, bool)
                and 0 <= current < len(self.code_assets)):
            self._show_code_asset(current)


    def _restore_recovery_tileset(self, tileset_state, skipped):
        if not isinstance(tileset_state, dict):
            return
        mode = tileset_state.get("mode")
        if mode in ("wang", "blob"):
            index = self.tileset_mode_combo.findData(mode)
            if index >= 0:
                self.tileset_mode_combo.setCurrentIndex(index)
        shape = tileset_state.get("shape")
        if shape in ("square", "rounded"):
            index = self.tileset_shape_combo.findData(shape)
            if index >= 0:
                self.tileset_shape_combo.setCurrentIndex(index)
        base_a = tileset_state.get("base_a")
        if base_a:
            if Path(base_a).is_file():
                self.load_tileset_base(base_a, "a")
            else:
                skipped.append(Path(base_a).name)
        base_b = tileset_state.get("base_b")
        if base_b:
            if Path(base_b).is_file():
                self.load_tileset_base(base_b, "b")
            else:
                skipped.append(Path(base_b).name)
        if (self.tileset_mode is not None
                and self.tileset_base_a is not None):
            self._on_tileset_generate()


    # =========================================================== presets

    def _tab_controls(self, tab):
        """The pixelize controls of one tab, by name. tab is
        "image", "video", or "prompt"."""
        prefix = {"image": "", "video": "video_",
                  "prompt": "prompt_"}[tab]
        return {
            "size_combo": getattr(self, prefix + "size_combo"),
            "width": getattr(self, prefix + "width_spin"),
            "height": getattr(self, prefix + "height_spin"),
            "aspect": getattr(self, prefix + "aspect_check"),
            "palette": getattr(self, prefix + "palette_combo"),
            "colors": getattr(self, prefix + "colors_spin"),
            "dither": getattr(self, prefix + "dither_check"),
            "preset_combo": getattr(self, prefix + "preset_combo"),
            "preset_warning": getattr(
                self, prefix + "preset_warning_label"),
            "delete_btn": getattr(self, prefix + "preset_delete_btn"),
            "export_btn": getattr(self, prefix + "preset_export_btn"),
        }


    def _read_tab_settings(self, tab):
        controls = self._tab_controls(tab)
        settings = {"width": controls["width"].value(),
                    "height": controls["height"].value(),
                    "keep_aspect": controls["aspect"].isChecked(),
                    "palette": controls["palette"].currentData(),
                    "max_colors": controls["colors"].value(),
                    "dither": controls["dither"].isChecked()}
        # The Video tab's save captures the video fps + sheet
        # settings too, so a saved video preset re-applies the
        # whole look; the other tabs have no such controls and
        # save without them.
        if tab == "video":
            settings["extract_fps"] = \
                self.video_fps_combo.currentData()
            settings["preview_fps"] = \
                self.video_play_fps_spin.value()
            settings["sheet_fps"] = self.sheet_fps_spin.value()
            settings["sheet_cols"] = \
                self.sheet_cols_combo.currentData()
            settings["sheet_padding"] = \
                self.sheet_padding_spin.value()
        return settings


    def _apply_settings_to_tab(self, tab, settings):
        """Set a tab's controls from a preset. The live preview
        re-pixelizes through the normal control signals; the
        preset combo is not reset to Custom mid-apply."""
        controls = self._tab_controls(tab)
        self._applying_preset = True
        try:
            width, height = settings["width"], settings["height"]
            if width == height and width in SIZE_PRESETS:
                index = controls["size_combo"].findData(width)
                if index >= 0:
                    controls["size_combo"].setCurrentIndex(index)
            else:
                index = controls["size_combo"].findData(None)
                if index >= 0:
                    controls["size_combo"].blockSignals(True)
                    controls["size_combo"].setCurrentIndex(index)
                    controls["size_combo"].blockSignals(False)
                controls["width"].setValue(width)
                controls["height"].setValue(height)
            controls["aspect"].setChecked(settings["keep_aspect"])
            index = controls["palette"].findData(settings["palette"])
            if index >= 0:
                controls["palette"].setCurrentIndex(index)
            else:
                # The preset names a palette that is gone (a
                # user palette deleted since the preset was
                # saved) — fall back to "From image" with a note,
                # never crash and never leave the picker pointing
                # at nothing.
                index = controls["palette"].findData("image")
                if index >= 0:
                    controls["palette"].setCurrentIndex(index)
                self.statusBar().showMessage(
                    "Preset palette %r is gone — using From image"
                    % settings["palette"])
            controls["colors"].setValue(settings["max_colors"])
            controls["dither"].setChecked(settings["dither"])
            if tab == "video":
                # The video fields are optional: a preset
                # carrying one sets that control, a preset
                # without it leaves the control alone — never
                # reset to a default. The other tabs have no
                # such controls and ignore the fields entirely.
                # Every value here is in its control's range
                # (validate_settings checked it), so findData
                # always lands; the guard is belt-and-braces.
                if "extract_fps" in settings:
                    index = self.video_fps_combo.findData(
                        settings["extract_fps"])
                    if index >= 0:
                        self.video_fps_combo.setCurrentIndex(index)
                if "preview_fps" in settings:
                    self.video_play_fps_spin.setValue(
                        settings["preview_fps"])
                if "sheet_fps" in settings:
                    self.sheet_fps_spin.setValue(
                        settings["sheet_fps"])
                if "sheet_cols" in settings:
                    index = self.sheet_cols_combo.findData(
                        settings["sheet_cols"])
                    if index >= 0:
                        self.sheet_cols_combo.setCurrentIndex(index)
                if "sheet_padding" in settings:
                    self.sheet_padding_spin.setValue(
                        settings["sheet_padding"])
        finally:
            self._applying_preset = False


    def _mark_preset_custom(self, tab):
        """A control changed by hand — the tab no longer matches
        its selected preset, so the picker goes back to Custom.
        Suppressed while a preset is being applied, and a no-op
        while the tab is still being built (its picker does not
        exist yet — the size combo fires control-changed during
        construction)."""
        if self._applying_preset:
            return
        prefix = {"image": "", "video": "video_",
                  "prompt": "prompt_"}[tab]
        combo = getattr(self, prefix + "preset_combo", None)
        if combo is None:
            return
        if combo.currentIndex() != 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        delete_btn = getattr(self, prefix + "preset_delete_btn", None)
        if delete_btn is not None:
            delete_btn.setEnabled(False)
        export_btn = getattr(self, prefix + "preset_export_btn", None)
        if export_btn is not None:
            export_btn.setEnabled(False)


    # --------------------------------------- shared tab controls (task 58)
    # The Image, Video, and Prompt tabs share one size-control
    # layout and one control-change shape, so their handlers are
    # one implementation each, not three copies. The tab
    # handlers (_on_preset, _on_video_preset, _on_prompt_preset
    # and friends) are thin delegates that pass their tab key.
    # The export handlers stay per-tab: their work genuinely
    # differs (a PNG, a frame set, a keeper).

    def _size_controls(self, tab):
        """(size combo, width spin, height spin, updating-flag
        name) for the Image, Video, or Prompt tab."""
        prefix = {"image": "", "video": "video_",
                  "prompt": "prompt_"}[tab]
        return (getattr(self, prefix + "size_combo"),
                getattr(self, prefix + "width_spin"),
                getattr(self, prefix + "height_spin"),
                "_updating_" + prefix + "size")

    def _apply_size_preset(self, tab):
        """A size preset was picked on the Image, Video, or
        Prompt tab: set both spins to the preset size, then
        re-pixelize. The updating flag stops the spins'
        valueChanged signals from pointing the combo back at
        Custom mid-apply."""
        combo, width_spin, height_spin, flag = self._size_controls(tab)
        preset = combo.currentData()
        if preset is None:
            return
        setattr(self, flag, True)
        try:
            width_spin.setValue(preset)
            height_spin.setValue(preset)
        finally:
            setattr(self, flag, False)
        self._on_tab_control_changed(tab)

    def _reset_size_to_custom(self, tab):
        """A width or height was typed by hand on the Image,
        Video, or Prompt tab: the size combo goes back to
        Custom, then the tab re-pixelizes."""
        combo, _width, _height, flag = self._size_controls(tab)
        if getattr(self, flag):
            return
        index = combo.findData(None)
        if index >= 0 and combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self._on_tab_control_changed(tab)

    def _on_tab_control_changed(self, tab):
        """A pixelize control changed on the Image, Video, or
        Prompt tab: the tab's preset picker goes back to
        Custom, the change is recorded for undo, and the tab
        re-renders. The Image tab asks first when the
        re-pixelize would throw pixel edits away (task 33);
        the Video and Prompt tabs have no pixel edits to lose.
        Declining puts the Image controls back where they
        were, with signals blocked so nothing re-pixelizes and
        no undo step is recorded."""
        if tab == "image":
            # A control change re-pixelizes from the source,
            # which throws the pixel edits away — ask first
            # (task 33). Skipped while an undo/redo is
            # applying (the stack order means no edits can
            # exist then — any edit made after the change must
            # be undone first) and while a preset is being
            # applied (the preset handler asks itself, once,
            # before the apply starts).
            if self._result_edited and not self._undo_suppress:
                if not self._confirm_discard_edits(
                        "Re-pixelize?",
                        "Re-pixelize and lose your edits?"):
                    self._restore_image_controls()
                    return
        self._mark_preset_custom(tab)
        self._record_control_change(tab)
        if tab == "image":
            if self.source_image is not None:
                self.update_preview()
        elif tab == "video":
            if self.video_raw_frames:
                self._schedule_video_run(reload=False)
        elif tab == "prompt":
            if self.prompt_keeper_image is not None:
                self._update_prompt_preview()

    def _on_zoom_changed_for(self, tab, value):
        """A zoom slider moved on the Image, Video, or Prompt
        tab: show the zoom, then redraw the preview if there
        is one."""
        prefix = {"image": "", "video": "video_",
                  "prompt": "prompt_"}[tab]
        getattr(self, prefix + "zoom_label").setText("%dx" % value)
        if tab == "image":
            if self.source_image is not None:
                self._refresh_pixmaps()
        elif tab == "video":
            if self.video_frames:
                self._refresh_video_preview()
        elif tab == "prompt":
            if self.prompt_keeper_image is not None:
                self._refresh_prompt_pixmaps()


    def _mark_video_preset_custom(self, field):
        """A video control changed by hand — if the picked preset
        carries that field, the tab no longer matches it, so the
        picker goes back to Custom. A preset WITHOUT the field
        is unaffected (its look does not cover that control), so
        the picker stays — changing fps never invalidated a
        shared-only preset, and still does not. Suppressed while
        a preset is being applied, and a no-op while the tab is
        still being built (its picker does not exist yet — the
        fps combo fires during construction). A corrupt presets
        file never breaks a control change: the marking is
        cosmetic, so a store error just skips it."""
        if self._applying_preset:
            return
        combo = getattr(self, "video_preset_combo", None)
        if combo is None:
            return
        data = combo.currentData()
        if data is None:
            return
        kind, name = data.split(":", 1)
        try:
            settings = self.preset_store.get(
                name, builtin=(kind == "builtin"))
        except PixelForgeError:
            return
        if settings is not None and field in settings:
            self._mark_preset_custom("video")
            # The picker moved without a control signal — sync
            # the undo snapshot so the next change does not
            # record a phantom picker-only step (task 30).
            if self._tab_settings is not None:
                self._sync_tab_settings()


    def _palette_combos(self):
        """The four palette pickers, in tab order."""
        return (self.palette_combo, self.video_palette_combo,
                self.prompt_palette_combo,
                self.code_texture_palette_combo)


    def _palette_label(self, key):
        """The display label for a palette key — a built-in label,
        or the user palette's name as-is."""
        return PALETTE_LABELS.get(key, key)


    def _fill_palette_combo(self, combo, user=None):
        """Rebuild one palette picker: "From image" first, the
        built-ins in shipped order, a separator, then the saved
        palettes sorted by name. Keeps the picker's current
        palette when it still exists. A wholly unreadable
        palettes file leaves the picker with the built-ins only
        (the refresh reports it; the build sites stay silent so
        a bad file never kills the window)."""
        if user is None:
            try:
                user = self.palette_store.user_palettes()
            except PixelForgeError:
                user = {}
        current = combo.currentData()
        combo.blockSignals(True)
        try:
            combo.clear()
            for key in ("image",) + tuple(NAMED_PALETTES):
                combo.addItem(PALETTE_LABELS[key], key)
            if user:
                combo.insertSeparator(combo.count())
                for name in sorted(user):
                    combo.addItem(name, name)
            if current is not None:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)


    def _refresh_palette_combos(self):
        """Rebuild all four palette pickers after a palette
        change. A picker whose palette is gone (deleted while
        picked) falls back to "From image" with a note in the
        status bar — it never crashes and never keeps pointing
        at a palette that no longer exists."""
        try:
            user = self.palette_store.user_palettes()
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))
            user = {}
        for combo in self._palette_combos():
            current = combo.currentData()
            self._fill_palette_combo(combo, user)
            if current is not None and current != "image" \
                    and combo.findData(current) < 0:
                index = combo.findData("image")
                if index >= 0:
                    combo.setCurrentIndex(index)
                self.statusBar().showMessage(
                    "Palette %r is gone — using From image" % current)


    def _open_palette_editor(self, prefill=None, prefill_name=None):
        """Open the palette editor dialog (task 39). prefill is
        an optional (colors, name) pair from Extract palette —
        the dialog opens in its create flow with those colors.
        The four palette pickers refresh when anything changed.
        """
        dialog = PaletteEditorDialog(self, self.palette_store,
                                     prefill_colors=prefill,
                                     prefill_name=prefill_name)
        dialog.exec()
        if dialog.changed:
            self._refresh_palette_combos()


    def _extract_palette(self):
        """Extract a palette from the loaded image (median-cut,
        16 colors — the same code "From image" uses) and open
        the editor's create flow with it, named after the
        image."""
        if self.source_image is None:
            return
        try:
            colors = extract_palette_from_image(self.source_image,
                                                16)
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))
            return
        stem = Path(self.source_path).stem \
            if self.source_path else "Image"
        self._open_palette_editor(prefill=colors,
                                  prefill_name="%s palette" % stem)


    def _refresh_preset_combos(self):
        """Rebuild all three preset pickers from the store,
        keeping each tab's selection when the preset still
        exists. A skipped user preset shows in the warning label
        under each picker; only a wholly unreadable presets file
        shows in the status bar and leaves the pickers with
        Custom only."""
        try:
            presets = self.preset_store.all_presets()
            skipped = self.preset_store.skipped_presets()
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))
            presets = []
            skipped = []
        for tab in ("image", "video", "prompt"):
            controls = self._tab_controls(tab)
            warning = controls["preset_warning"]
            if skipped:
                warning.setText("\n".join(
                    "Preset %r skipped: %s" % (name, reason)
                    for name, reason in skipped))
                warning.setVisible(True)
            else:
                warning.setVisible(False)
            combo = controls["preset_combo"]
            current = combo.currentData()
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem("Custom", None)
                for name, _settings, builtin in presets:
                    label = name
                    if not builtin and name in BUILTIN_PRESETS:
                        label = "%s (custom)" % name
                    combo.addItem(label, "%s:%s"
                                  % ("builtin" if builtin else "user",
                                     name))
                index = 0
                if current is not None:
                    for i in range(combo.count()):
                        if combo.itemData(i) == current:
                            index = i
                            break
                combo.setCurrentIndex(index)
            finally:
                combo.blockSignals(False)
            self._update_preset_buttons(tab)
        # A rebuild can move a picker (a deleted preset drops
        # its tab to Custom) without any control signal — sync
        # the undo snapshot so the next change does not record
        # a phantom picker-only step (task 30).
        if self._tab_settings is not None:
            self._sync_tab_settings()


    def _update_preset_buttons(self, tab):
        """Delete is only for a selected USER preset — Custom and
        built-ins keep it disabled. Export is for any selected
        preset, built-in or user — Custom keeps it disabled."""
        controls = self._tab_controls(tab)
        data = controls["preset_combo"].currentData()
        controls["delete_btn"].setEnabled(
            data is not None and data.startswith("user:"))
        controls["export_btn"].setEnabled(data is not None)


    def _select_preset(self, tab, data):
        """Set a tab's picker to the preset with this data key
        ("builtin:Name" or "user:Name"), without applying it."""
        combo = self._tab_controls(tab)["preset_combo"]
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)
                break
        self._update_preset_buttons(tab)
        # The picker moved without a control signal — sync the
        # undo snapshot so the next change does not record a
        # phantom picker-only step (task 30).
        if self._tab_settings is not None:
            self._sync_tab_settings()


    def _on_library_preset(self, tab):
        """A preset was picked — apply it to this tab."""
        controls = self._tab_controls(tab)
        data = controls["preset_combo"].currentData()
        self._update_preset_buttons(tab)
        if data is None:
            return
        kind, name = data.split(":", 1)
        try:
            settings = self.preset_store.get(
                name, builtin=(kind == "builtin"))
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not apply preset", str(exc))
            return
        if settings is None:
            return
        # A preset re-pixelizes, which throws the pixel edits
        # away — ask once, here, before the apply starts (the
        # per-control guard cannot run mid-apply: recording is
        # suppressed until it lands). Declining puts the picker
        # back where it was.
        if tab == "image" and self._result_edited:
            if not self._confirm_discard_edits(
                    "Apply preset?",
                    "Apply this preset and lose your edits?"):
                before = None
                if self._tab_settings is not None:
                    before = self._tab_settings["image"]["preset"]
                self._select_preset(tab, before)
                return
        # One undo step for the whole application: the apply
        # fires a control-changed signal per control, so
        # recording is suppressed until it lands, then the diff
        # against the snapshot is recorded once (task 30).
        self._undo_suppress = True
        try:
            self._apply_settings_to_tab(tab, settings)
        finally:
            self._undo_suppress = False
        self._record_control_change(tab)
        self.statusBar().showMessage("Applied preset %s" % name)


    def _on_save_preset(self, tab):
        name, ok = QInputDialog.getText(
            self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        try:
            self.preset_store.save(name.strip(),
                                   self._read_tab_settings(tab))
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not save preset", str(exc))
            return
        self._refresh_preset_combos()
        self._select_preset(tab, "user:%s" % name.strip())
        self.statusBar().showMessage(
            "Saved preset %s" % name.strip())


    def _on_delete_preset(self, tab):
        controls = self._tab_controls(tab)
        data = controls["preset_combo"].currentData()
        if data is None or not data.startswith("user:"):
            return
        name = data.split(":", 1)[1]
        answer = QMessageBox.question(
            self, "Delete preset",
            "Delete the preset %s?" % name,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Undoable (task 30): the settings are captured before
        # the delete, so undo saves them straight back.
        try:
            settings = self.preset_store.get(name)
            self.preset_store.delete(name)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not delete preset", str(exc))
            return
        self._refresh_preset_combos()
        if settings is not None:
            self._push_undo(_PresetDeleteCommand(
                self, name, settings, tab))
        self.statusBar().showMessage("Deleted preset %s" % name)


    def _on_export_preset(self, tab):
        """Write the picked preset — built-in or user — to a
        JSON file to share. Disabled with no preset picked."""
        controls = self._tab_controls(tab)
        data = controls["preset_combo"].currentData()
        if data is None:
            return
        kind, name = data.split(":", 1)
        default = self._export_default("%s.json" % name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export preset", default,
            "Pixel Forge preset (*.json)")
        if not path:
            return
        try:
            self.preset_store.export_preset(
                name, path, builtin=(kind == "builtin"))
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export preset", str(exc))
            return
        self._remember_export_dir(Path(path).parent)
        self.statusBar().showMessage(
            "Exported preset %s to %s" % (name, path))


    def _on_import_preset(self, tab):
        """Read a shared preset file into the user presets. A
        bad file refuses plainly and touches nothing. A name
        matching a built-in shadows it as "(custom)"; a name
        matching an existing user preset asks Yes/No (default
        No) before replacing it."""
        last = self._last_export_dir() or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import preset", last,
            "Pixel Forge preset (*.json)")
        if not path:
            return
        try:
            name, _settings = read_preset_export(path)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import preset", str(exc))
            return
        if self.preset_store.get(name) is not None:
            answer = QMessageBox.question(
                self, "Replace preset",
                "A preset named %s already exists. Replace it?"
                % name,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.preset_store.import_preset(path, overwrite=True)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import preset", str(exc))
            return
        self._refresh_preset_combos()
        self._select_preset(tab, "user:%s" % name)
        self.statusBar().showMessage("Imported preset %s" % name)


    def _on_tab_changed(self, index):
        # Autosave (task 55): leaving a tab writes the work down
        # at once, so a crash on the next tab never loses it.
        self._autosave_write_now()
        # The library can change outside this tab (a save from the
        # Image tab, a CLI add) — re-read it whenever the tab is
        # opened, so the grid is never stale.
        warned = False
        if self.tabs.tabText(index) == "Library":
            warned = self._refresh_library()
        # And the status bar says what this tab is for (task 22):
        # one plain line per tab, what it shows and what to do next.
        # But never over the malformed-entry warning — while bad
        # entries exist, the warning matters more than the hint.
        hints = {
            "Image": "Import an image to turn it into a pixel sprite.",
            "Video": "Import a video to turn its frames into sprites.",
            "Prompt": "Describe a sprite and press Generate.",
            "Code": ("Import code or a game asset to see the sprites "
                     "it defines."),
            "Library": "Your saved sprites — click one to preview it.",
            "Tileset": ("Build a Wang or blob tileset from base "
                        "tiles, then export it for Tiled or Godot."),
        }
        hint = hints.get(self.tabs.tabText(index))
        if hint is not None and not warned:
            self.statusBar().showMessage(hint)


    # --------------------------------------------------------- cleanup

    def showEvent(self, event):
        # Session restore (task 29) runs once, after the window
        # is shown — the video restore starts a worker, and the
        # window must be up first so it never blocks startup.
        super().showEvent(event)
        if not self._session_restored:
            self._session_restored = True
            # Autosave (task 55): a crash leaves a marker + a
            # recovery file, and the work is offered back. A
            # restore replaces the session restore (it covers
            # the same files and more); anything else falls
            # through to the normal restore.
            if not self._offer_recovery():
                self.restore_session()
        # Autosave writes start only now, once the recovery
        # question is settled — an earlier write would have
        # overwritten the recovery file with an empty state.
        if not self._autosave_offered:
            self._autosave_offered = True
            self._autosave_ready = True
        # Update check (task 48): once per launch, after the
        # window is shown, and only when the user opted in —
        # off by default, so a fresh install makes no request.
        if not self._update_check_started:
            self._update_check_started = True
            if self._load_update_check():
                self._start_update_check(manual=False)


    def _restore_geometry(self):
        """Open where the window was last closed (task 66), or
        1020x640 on first launch. A saved rectangle is clamped to
        the current screen, so a changed monitor setup never
        strands the window off-screen. The default is never
        clamped — first launch looks exactly like it always did."""
        settings = self._settings
        if settings is None:
            self.resize(1020, 640)
            return
        values = [settings.value("window/%s" % key, type=int)
                  for key in ("x", "y", "width", "height")]
        if any(value is None for value in values):
            self.resize(1020, 640)
            return
        x, y, width, height = values
        if width < 1 or height < 1:
            self.resize(1020, 640)
            return
        screen = self.screen() or QApplication.primaryScreen()
        rect = QRect(x, y, width, height)
        if screen is not None:
            rect = clamp_geometry(rect, screen.availableGeometry())
        self.setGeometry(rect)


    def _load_theme(self):
        """The saved theme (task 67): "light" only when the
        settings say so — anything else (missing, anything
        else) is dark, the first-launch look."""
        if self._settings is None:
            return "dark"
        return "light" if self._settings.value("theme") == "light" \
            else "dark"


    def _apply_stylesheet(self):
        """Apply the current theme to the whole app (task 67)."""
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(THEMES[self._theme])


    def _on_toggle_theme(self, checked):
        """The View menu's Light theme action (task 67): switch
        now, remember the choice for the next launch. A window
        without settings still switches the live theme — it
        just has nowhere to save the choice."""
        self._theme = "light" if checked else "dark"
        self._apply_stylesheet()
        if self._settings is not None:
            self._settings.setValue("theme", self._theme)
            self._settings.sync()


    def _load_update_check(self):
        """The saved update-check choice (task 48): True only
        when the settings say so — missing means off, the
        first-launch look. Off is the default, so a fresh
        install makes no network request at all."""
        if self._settings is None:
            return False
        return bool(self._settings.value(
            "check_updates_on_startup", type=bool))


    def _on_toggle_update_check(self, checked):
        """The Help menu's Check for updates on startup action
        (task 48): remember the choice for the next launch. A
        window without settings has nowhere to save it — the
        checkable action still reflects the click, it just does
        not persist. Turning it on does not check now; the
        check runs on the next launch, or by hand."""
        if self._settings is not None:
            self._settings.setValue("check_updates_on_startup",
                                    bool(checked))
            self._settings.sync()


    def _start_update_check(self, manual):
        """Start one update check in a worker, unless one is
        already running — two checks at once would only double
        the request. Returns False when a check is already
        running, True when one started."""
        if self.update_worker is not None \
                and self.update_worker.isRunning():
            return False
        worker = UpdateCheckWorker(__version__, manual, self)
        worker.done.connect(self._on_update_check_done)
        self.update_worker = worker
        worker.start()
        return True


    def _on_check_updates(self):
        """The Help menu's Check for updates action (task 48):
        a manual check, which works whether or not the
        automatic one is on — the user asking is not phoning
        home."""
        if not self._start_update_check(manual=True):
            self.statusBar().showMessage(
                "Already checking for updates.")
            return
        self.statusBar().showMessage("Checking for updates...")


    def _on_update_check_done(self, result):
        """One check finished (task 48). A stale result — from
        a worker that is no longer the running one — is
        ignored. A newer release is told plainly, with the
        version and a way to open the release page, whether the
        check was manual or automatic: the automatic check only
        runs when the user opted in, so telling them is the
        feature working. Up to date is told only when the user
        asked — an automatic check that finds nothing stays
        quiet. A failed check is told only when the user asked
        (a warning with the reason); an automatic failure is a
        status-bar line at most, never a modal on startup."""
        if self.sender() is not self.update_worker:
            return
        self.update_worker = None
        state = result.get("state")
        manual = result.get("manual", False)
        if state == "newer":
            self._show_update_available(result)
            return
        if state == "current":
            if manual:
                QMessageBox.information(
                    self, "No updates",
                    "You have the newest version (%s)."
                    % __version__)
            return
        if manual:
            gui_module.warn_dialog(self, "Could not check for updates",
                        result.get("reason")
                        or "the update check failed")
        else:
            self.statusBar().showMessage(
                "Could not check for updates.")


    def _show_update_available(self, result):
        """A newer release is out (task 48): say so plainly,
        with the version, and offer the release page. The check
        never downloads or installs anything — the user
        downloads the new release themselves."""
        box = QMessageBox(self)
        box.setWindowTitle("A new release is on GitHub")
        box.setText("Pixel Forge %s is on GitHub. You have %s."
                    % (result["latest"], __version__))
        open_button = box.addButton(
            "Open the release page",
            QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(result["url"]))


    def _stop_update_worker(self):
        """Wait for a running update check to finish (task 48).
        The fetch is one HTTP call with an 8 second timeout, so
        the wait is bounded — at most the timeout plus a little.
        There is no cancel: the call cannot be interrupted
        mid-flight."""
        if self.update_worker is not None:
            if self.update_worker.isRunning():
                self.update_worker.wait(10000)
            self.update_worker = None


    def _save_geometry(self):
        """Remember size + position for the next launch (task 66).
        Nothing is saved when the window has no settings. The
        sync puts the values on disk at once, so they survive
        even if the process ends right after the close."""
        if self._settings is None:
            return
        rect = self.geometry()
        self._settings.setValue("window/x", rect.x())
        self._settings.setValue("window/y", rect.y())
        self._settings.setValue("window/width", rect.width())
        self._settings.setValue("window/height", rect.height())
        self._settings.sync()


    def closeEvent(self, event):
        self._save_geometry()
        self._stop_playback()
        self._stop_bgremove_worker()
        self._stop_gridsnap_worker()
        self._stop_normalmap_worker()
        self._stop_update_worker()
        self._stop_prompt_worker()
        self._stop_texture_worker()
        self._stop_video_worker()
        self._cleanup_video_raw()
        self._cleanup_prompt_batch()
        # Autosave (task 55): a clean exit leaves no marker and
        # no recovery file — the next launch starts fresh (the
        # session still restores the last files). A crash skips
        # all of this, which is exactly what the next launch
        # detects.
        self._autosave_timer.stop()
        try:
            mark_clean_exit(self._autosave_dir)
            discard_recovery(self._autosave_dir)
        except OSError:
            pass
        # Undo is per-window-session only (task 30): clearing
        # the stack discards every entry, which is what
        # permanently deletes the files of undone deletes.
        self._undo_stack.clear()
        event.accept()


def main(argv=None):
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else argv)
    # The names QSettings stores the window geometry (task 66)
    # and the theme (task 67) under:
    # ~/.config/BrokeNBored/PixelForge.conf. Set before the
    # settings object is built. The window applies its saved
    # theme itself in __init__ — main() no longer hardcodes
    # the dark stylesheet here.
    app.setOrganizationName("BrokeNBored")
    app.setApplicationName("PixelForge")
    icon_path = Path(__file__).resolve().parent.parent / "icons" / \
        "pixel-forge-256.png"
    if icon_path.is_file():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))
    args = list(sys.argv[1:] if argv is None else argv[1:])
    # A file on the command line wins over the session (task
    # 29): the user asked for this file, so no restore.
    window = MainWindow(restore=not (args and os.path.isfile(args[0])),
                        settings=QSettings())
    if args and os.path.isfile(args[0]):
        if Path(args[0]).suffix.lower() in VIDEO_EXTENSIONS:
            window.tabs.setCurrentIndex(1)
            window.load_video(args[0])
        elif Path(args[0]).suffix.lower() in CODE_EXTENSIONS:
            window.tabs.setCurrentIndex(3)
            window.load_code(args[0])
        else:
            window.load_image(args[0])
    window.show()
    return app.exec()