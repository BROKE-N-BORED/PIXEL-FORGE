"""Prompt tab: generate candidates, keep one, pixelize..

One of the MainWindow mixins (task 58).
"""

from PySide6.QtCore import QSize, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)
from .gui_shared import (MAX_CUSTOM_SIZE, ORIGINAL_FIT, PromptWorker, SIZE_PRESETS, STRIP_THUMB, _CardLift, gui_module, thumb_pixmap)
from .history import (batches_dir, load_history, record_batch, record_prompt, save_history)
from .imagegen import (DEFAULT_COUNT, DEFAULT_NEGATIVE, DEFAULT_SIZE, GENERATION_MIN_RAM_BYTES, LCM_CFG, LCM_STEPS, MAX_PROMPT_LEN, MODEL_FILENAME, MODEL_URL, NEGATIVE_PRESETS, engine_status, find_model, format_bytes, is_pinned_model, load_batch, new_batch, require_engine, require_model, set_keeper, validate_prompt)
from pathlib import Path
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError, count_opaque_colors, plural)
import time


class PromptTabMixin:
    """Prompt tab: generate candidates, keep one, pixelize.

    One of the MainWindow mixins (task 58): the methods
    moved here verbatim from gui.py. MainWindow
    inherits this class; self is the window.
    """


    # =========================================================== prompt tab

    def _build_prompt_tab(self):
        root = QWidget()
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ------------------------------------------------ controls
        # No fixed width here: the column lives in a scroll
        # area (see _scrollable_column), which sets the 268 px
        # width and lets the window shrink below the column's
        # height.
        controls = QWidget()
        form = QVBoxLayout(controls)
        form.setContentsMargins(0, 0, 0, 0)

        form.addWidget(self._section("Prompt"))
        self.prompt_edit = QLineEdit()
        self.prompt_edit.setPlaceholderText(
            "a knight with a red shield")
        self.prompt_edit.setToolTip(
            "Describe the sprite to generate")
        # Enter-to-generate (task 21): Enter in THIS field starts
        # a generation, the same as the Generate button. Other
        # fields keep their own Enter behavior — the negative
        # field, the search box, and the time fields do not
        # generate anything.
        self.prompt_edit.returnPressed.connect(
            self._on_prompt_generate)
        form.addWidget(self.prompt_edit)

        # Character counter (task 22): the prompt limit is 1000
        # (MAX_PROMPT_LEN, enforced by validate_prompt), and until
        # now the only sign of it was a refusal at Generate time.
        # Right-aligned, under the field, orange past the limit.
        self.prompt_counter_label = QLabel(
            "0 / %d" % MAX_PROMPT_LEN)
        self.prompt_counter_label.setAlignment(
            Qt.AlignmentFlag.AlignRight)
        self.prompt_counter_label.setToolTip(
            "Prompt length — the limit is %d characters"
            % MAX_PROMPT_LEN)
        self.prompt_edit.textChanged.connect(
            self._on_prompt_text_changed)
        form.addWidget(self.prompt_counter_label)

        # Past prompts, most recent first (task 20) — picking one
        # fills the prompt field. Nothing is selected: the combo
        # shows blank until the user picks.
        form.addWidget(QLabel("Prompt history"))
        self.prompt_history_combo = QComboBox()
        self.prompt_history_combo.setToolTip(
            "Past prompts — pick one to reuse it")
        self.prompt_history_combo.currentIndexChanged.connect(
            self._on_prompt_history_picked)
        form.addWidget(self.prompt_history_combo)

        form.addWidget(QLabel("Negative prompt"))
        self.prompt_negative_edit = QLineEdit()
        self.prompt_negative_edit.setText(DEFAULT_NEGATIVE)
        # Show the START of the default: a QLineEdit scrolls a long
        # setText to the end, so the field used to read "eformed,
        # distorted…" — like a typo (review B15). The prompt field
        # above has no pre-filled text (a placeholder instead), so
        # this is the only field that needs it.
        self.prompt_negative_edit.setCursorPosition(0)
        self.prompt_negative_edit.setToolTip(
            "Things the picture should avoid")
        form.addWidget(self.prompt_negative_edit)

        # Negative-prompt presets (task 20): picking one fills the
        # field; typing by hand reads as "Custom". "Default" is
        # selected because the field already holds the default
        # text — the combo mirrors the field, it does not choose
        # for the user.
        self.prompt_negative_preset_combo = QComboBox()
        self.prompt_negative_preset_combo.setToolTip(
            "Fill the negative prompt from a preset")
        for name in NEGATIVE_PRESETS:
            self.prompt_negative_preset_combo.addItem(name, name)
        self.prompt_negative_preset_combo.addItem("Custom", None)
        self.prompt_negative_preset_combo.currentIndexChanged.connect(
            self._on_negative_preset_picked)
        form.addWidget(self.prompt_negative_preset_combo)
        self.prompt_negative_edit.textChanged.connect(
            self._on_negative_text_changed)

        self.prompt_generate_btn = QPushButton("Generate")
        self.prompt_generate_btn.setObjectName("export")
        self.prompt_generate_btn.setToolTip(
            "Generate 3 sprite candidates from the prompt")
        self.prompt_generate_btn.clicked.connect(
            self._on_prompt_generate)
        form.addWidget(self.prompt_generate_btn)

        self.prompt_cancel_btn = QPushButton("Cancel")
        self.prompt_cancel_btn.setToolTip(
            "Stop the current generation")
        self.prompt_cancel_btn.setEnabled(False)
        self.prompt_cancel_btn.clicked.connect(self._on_prompt_cancel)
        form.addWidget(self.prompt_cancel_btn)

        # Iteration (task 20): re-roll one candidate, or a new
        # batch seeded from the keeper. Both start disabled —
        # there is nothing to re-roll or imitate yet.
        self.prompt_regen_one_btn = QPushButton("Regenerate one")
        self.prompt_regen_one_btn.setToolTip(
            "Re-roll the selected candidate with a fresh seed")
        self.prompt_regen_one_btn.setEnabled(False)
        self.prompt_regen_one_btn.clicked.connect(
            self._on_prompt_regenerate_one)
        form.addWidget(self.prompt_regen_one_btn)

        self.prompt_more_like_btn = QPushButton("More like this one")
        self.prompt_more_like_btn.setToolTip(
            "Generate a new batch seeded from the keeper")
        self.prompt_more_like_btn.setEnabled(False)
        self.prompt_more_like_btn.clicked.connect(
            self._on_prompt_more_like)
        form.addWidget(self.prompt_more_like_btn)

        self.prompt_progress = QProgressBar()
        self.prompt_progress.setRange(0, DEFAULT_COUNT)
        self.prompt_progress.setValue(0)
        self.prompt_progress.setFormat("%v of %m candidates")
        form.addWidget(self.prompt_progress)

        self.prompt_status_label = QLabel("Checking the image engine…")
        self.prompt_status_label.setWordWrap(True)
        form.addWidget(self.prompt_status_label)

        # The model download, one click away (task 22): a button
        # under the status line, visible only while no model is
        # found. A plain-text status line cannot carry a link, and
        # an HTML link would fight the label's heightForWidth
        # sizing — so the button is the link.
        self.prompt_download_btn = QPushButton("Open download page")
        self.prompt_download_btn.setToolTip(
            "Open the model download page in your browser")
        self.prompt_download_btn.clicked.connect(
            self._on_open_model_download)
        form.addWidget(self.prompt_download_btn)

        # Generation settings (task 20): the LCM pins are the
        # defaults — these let the user change them. This is the
        # GENERATED picture's size; the "Size" section below is
        # the pixelized sprite's size.
        form.addWidget(self._section("Generation"))
        steps_row = QHBoxLayout()
        steps_row.addWidget(QLabel("Steps"))
        self.prompt_steps_spin = QSpinBox()
        self.prompt_steps_spin.setRange(1, 50)
        self.prompt_steps_spin.setValue(LCM_STEPS)
        self.prompt_steps_spin.setToolTip(
            "Diffusion steps per candidate (LCM default: 4)")
        steps_row.addWidget(self.prompt_steps_spin)
        form.addLayout(steps_row)

        cfg_row = QHBoxLayout()
        cfg_row.addWidget(QLabel("CFG scale"))
        self.prompt_cfg_spin = QDoubleSpinBox()
        self.prompt_cfg_spin.setRange(0.1, 20.0)
        self.prompt_cfg_spin.setSingleStep(0.5)
        self.prompt_cfg_spin.setDecimals(1)
        self.prompt_cfg_spin.setValue(LCM_CFG)
        self.prompt_cfg_spin.setToolTip(
            "How closely the picture follows the prompt")
        cfg_row.addWidget(self.prompt_cfg_spin)
        form.addLayout(cfg_row)

        gen_size_row = QHBoxLayout()
        gen_size_row.addWidget(QLabel("Image size"))
        self.prompt_gen_size_spin = QSpinBox()
        self.prompt_gen_size_spin.setRange(64, 1024)
        self.prompt_gen_size_spin.setSingleStep(64)
        self.prompt_gen_size_spin.setValue(DEFAULT_SIZE)
        self.prompt_gen_size_spin.setSuffix(" px")
        self.prompt_gen_size_spin.setToolTip(
            "Generated image size, square (LCM default: 512)")
        gen_size_row.addWidget(self.prompt_gen_size_spin)
        form.addLayout(gen_size_row)

        # Past batches, most recent first (task 20) — click one
        # to reopen its candidates and keeper. The list is capped
        # at 120 px so the Size section below stays reachable.
        form.addWidget(self._section("History"))
        self.prompt_history_list = QListWidget()
        self.prompt_history_list.setFixedHeight(120)
        # Items wrap: "prompt — 3 candidates, timestamp" is
        # wider than the 230 px column, and a horizontal scrollbar
        # hid the timestamp — the part that tells two batches with
        # the same prompt apart.
        self.prompt_history_list.setWordWrap(True)
        self.prompt_history_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prompt_history_list.setToolTip(
            "Past generations — click one to reopen it")
        self.prompt_history_list.itemClicked.connect(
            self._on_prompt_history_clicked)
        form.addWidget(self.prompt_history_list)

        form.addWidget(self._section("Size"))
        self.prompt_size_combo = QComboBox()
        self.prompt_size_combo.setToolTip("Sprite size in pixels")
        for preset in SIZE_PRESETS:
            self.prompt_size_combo.addItem("%d x %d" % (preset, preset),
                                           preset)
        self.prompt_size_combo.addItem("Custom", None)
        self.prompt_size_combo.setCurrentIndex(1)  # 32 x 32
        self.prompt_size_combo.currentIndexChanged.connect(
            self._on_prompt_preset)
        form.addWidget(self.prompt_size_combo)

        prompt_size_row = QHBoxLayout()
        self.prompt_width_spin = QSpinBox()
        self.prompt_width_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.prompt_width_spin.setValue(32)
        self.prompt_width_spin.setPrefix("W ")
        self.prompt_width_spin.setToolTip("Sprite width in pixels")
        self.prompt_width_spin.valueChanged.connect(
            self._on_prompt_custom_size)
        self.prompt_height_spin = QSpinBox()
        self.prompt_height_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.prompt_height_spin.setValue(32)
        self.prompt_height_spin.setPrefix("H ")
        self.prompt_height_spin.setToolTip("Sprite height in pixels")
        self.prompt_height_spin.valueChanged.connect(
            self._on_prompt_custom_size)
        prompt_size_row.addWidget(self.prompt_width_spin)
        prompt_size_row.addWidget(self.prompt_height_spin)
        form.addLayout(prompt_size_row)

        self.prompt_aspect_check = QCheckBox("Keep aspect ratio")
        self.prompt_aspect_check.setToolTip(
            "Keep the original proportions when resizing")
        self.prompt_aspect_check.toggled.connect(
            self._on_prompt_control_changed)
        form.addWidget(self.prompt_aspect_check)

        form.addWidget(self._section("Palette"))
        self.prompt_palette_combo = QComboBox()
        self.prompt_palette_combo.setToolTip(
            "Colors the sprite may use")
        self._fill_palette_combo(self.prompt_palette_combo)
        self.prompt_palette_combo.currentIndexChanged.connect(
            self._on_prompt_control_changed)
        form.addWidget(self.prompt_palette_combo)

        prompt_colors_row = QHBoxLayout()
        prompt_colors_row.addWidget(QLabel("Max colors"))
        self.prompt_colors_spin = QSpinBox()
        self.prompt_colors_spin.setRange(MIN_COLORS, MAX_COLORS)
        self.prompt_colors_spin.setValue(16)
        self.prompt_colors_spin.setToolTip(
            "Most colors the sprite may use")
        self.prompt_colors_spin.valueChanged.connect(
            self._on_prompt_control_changed)
        prompt_colors_row.addWidget(self.prompt_colors_spin)
        form.addLayout(prompt_colors_row)

        self.prompt_dither_check = QCheckBox("Dithering")
        self.prompt_dither_check.setToolTip(
            "Ordered (Bayer) dithering")
        self.prompt_dither_check.setChecked(True)
        self.prompt_dither_check.toggled.connect(
            self._on_prompt_control_changed)
        form.addWidget(self.prompt_dither_check)

        self._build_preset_section(form, "prompt")

        form.addWidget(self._section("Zoom"))
        prompt_zoom_row = QHBoxLayout()
        self.prompt_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.prompt_zoom_slider.setRange(1, 16)
        self.prompt_zoom_slider.setValue(8)
        self.prompt_zoom_slider.setToolTip("Zoom the pixel preview")
        self.prompt_zoom_slider.valueChanged.connect(
            self._on_prompt_zoom_changed)
        prompt_zoom_row.addWidget(self.prompt_zoom_slider)
        self.prompt_zoom_label = QLabel("8x")
        self.prompt_zoom_label.setFixedWidth(32)
        prompt_zoom_row.addWidget(self.prompt_zoom_label)
        self.prompt_zoom_fit_btn = QPushButton("Fit")
        self.prompt_zoom_fit_btn.setToolTip(
            "Zoom to fit the preview pane")
        self.prompt_zoom_fit_btn.clicked.connect(
            self._on_fit_prompt_zoom)
        prompt_zoom_row.addWidget(self.prompt_zoom_fit_btn)
        form.addLayout(prompt_zoom_row)

        form.addStretch(1)
        self.prompt_info_label = QLabel("No candidates yet")
        self.prompt_info_label.setWordWrap(True)
        form.addWidget(self.prompt_info_label)

        self.prompt_export_btn = QPushButton("Export PNG")
        self.prompt_export_btn.setObjectName("export")
        self.prompt_export_btn.setToolTip(
            "Export the keeper's pixel sprite as a transparent PNG")
        self.prompt_export_btn.setEnabled(False)
        self.prompt_export_btn.clicked.connect(
            self._on_export_prompt_keeper)
        form.addWidget(self.prompt_export_btn)

        self.prompt_save_library_btn = QPushButton("Save to library")
        self.prompt_save_library_btn.setToolTip(
            "Save the keeper's pixel sprite to the library")
        self.prompt_save_library_btn.setEnabled(False)
        self.prompt_save_library_btn.clicked.connect(
            self._on_save_prompt_keeper_to_library)
        form.addWidget(self.prompt_save_library_btn)

        main_layout.addWidget(
            self._scrollable_column(controls))

        # ------------------------------------------- preview and strip
        right = QSplitter(Qt.Orientation.Vertical)

        previews = QSplitter(Qt.Orientation.Horizontal)
        keeper_pane = QWidget()
        keeper_layout = QVBoxLayout(keeper_pane)
        keeper_layout.setContentsMargins(0, 0, 0, 0)
        keeper_layout.addWidget(self._section("Candidate"))
        self.prompt_before_label = QLabel(
            "Describe a sprite and press Generate")
        self.prompt_before_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.prompt_before_label.setWordWrap(True)
        keeper_scroll = QScrollArea()
        keeper_scroll.setWidgetResizable(True)
        keeper_scroll.setWidget(self.prompt_before_label)
        keeper_layout.addWidget(keeper_scroll)
        # The keeper's seed (task 20): the number that reproduces
        # this exact picture. Hidden until there is a keeper.
        self.prompt_seed_label = QLabel("")
        self.prompt_seed_label.setVisible(False)
        keeper_layout.addWidget(self.prompt_seed_label)
        previews.addWidget(keeper_pane)

        pixel_pane = QWidget()
        pixel_layout = QVBoxLayout(pixel_pane)
        pixel_layout.setContentsMargins(0, 0, 0, 0)
        pixel_layout.addWidget(self._section("Pixel art"))
        self.prompt_after_label = QLabel(
            "Your pixel sprite appears here")
        self.prompt_after_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.prompt_after_label.setWordWrap(True)
        self.prompt_pixel_scroll = QScrollArea()
        self.prompt_pixel_scroll.setWidgetResizable(True)
        self.prompt_pixel_scroll.setWidget(self.prompt_after_label)
        pixel_layout.addWidget(self.prompt_pixel_scroll)
        previews.addWidget(pixel_pane)
        previews.setSizes([500, 500])
        right.addWidget(previews)

        strip_pane = QWidget()
        strip_layout = QVBoxLayout(strip_pane)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.addWidget(self._section(
            "Candidates — click one to keep it"))
        self.prompt_strip = QListWidget()
        self.prompt_strip.setToolTip(
            "Candidates — click one to keep it")
        self.prompt_strip.setIconSize(QSize(STRIP_THUMB, STRIP_THUMB))
        self.prompt_strip.setFlow(QListWidget.Flow.LeftToRight)
        self.prompt_strip.setWrapping(True)
        self.prompt_strip.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.prompt_strip.setMovement(QListWidget.Movement.Static)
        self.prompt_strip.itemClicked.connect(
            self._on_prompt_strip_clicked)
        # Card hover lift (task 94): the candidates are cards;
        # the hovered one rises 4 px. Painting only: clicks
        # land exactly as before.
        self.prompt_lift = _CardLift(self.prompt_strip)
        strip_layout.addWidget(self.prompt_strip, 1)
        right.addWidget(strip_pane)

        right.setSizes([380, 220])
        main_layout.addWidget(right, 1)
        return root


    # ------------------------------------------------------ status

    def _refresh_prompt_status(self):
        """The engine/model status line. Checked on startup and
        again on every Generate — installing the engine while the
        window is open is picked up without a restart."""
        status = engine_status()
        if status["engine"] is not None:
            engine_line = "Engine: found at %s" % status["engine"]
        else:
            engine_line = ("Engine: missing — tick Install the image "
                           "engine in the installer, or run "
                           "./install.sh --with-image-engine")
        if status["model"] is not None:
            model = status["model"]
            if model.name == MODEL_FILENAME:
                model_line = "Model: found at %s" % model
            else:
                # find_model picks up ANY .gguf in a model home —
                # a renamed or newer LCM build still works, but a
                # wrong model used to be used without a word
                # (review B15). Name the file and flag it when it
                # is not the pinned one.
                model_line = ("Model: found at %s — not the pinned "
                              "LCM file (%s)" % (model, MODEL_FILENAME))
        else:
            # Both model homes, shared home first: find_model checks
            # ~/design-shell-models/ first, so a user following a
            # line that named only ~/pixel-forge-models/ downloaded
            # a second 2.1 GB copy instead of using the shared one
            # (review B13). The label wraps and _fit_prompt_status_
            # label() sizes it from its own heightForWidth, so the
            # longer line needs no layout change.
            model_line = ("Model: missing — download "
                          "LCM_Dreamshaper_v7-f16.gguf (about 2.1 GB) "
                          "into ~/design-shell-models/ or "
                          "~/pixel-forge-models/")
        self.prompt_status_label.setText(
            "%s\n%s" % (engine_line, model_line))
        self._fit_prompt_status_label()
        # No model yet: offer the download page. The button hides
        # once a model is found — nothing to download then.
        self.prompt_download_btn.setVisible(
            status["model"] is None)


    def _fit_prompt_status_label(self):
        """Size the status label to its wrapped text.

        Qt does not reliably apply a word-wrapped QLabel's
        heightForWidth inside this fixed-width column (measured:
        38 px given, 51 px needed — the text overflowed into the
        Size section below). So the minimum height is set from the
        label's OWN heightForWidth at 230 px — slightly narrower
        than the 250 px column, so it wraps at least as much and
        the height is never short. (A QTextDocument at 242 px
        under-measured by 5 px in the sandbox — 63 given, 68
        needed — because its wrapping differs from the label's.)
        """
        label = self.prompt_status_label
        label.setMinimumHeight(label.heightForWidth(230) + 2)
        label.updateGeometry()


    # ------------------------------------------------------ generate

    def _prompt_generating(self):
        return (self.prompt_worker is not None
                and self.prompt_worker.isRunning())


    def _confirm_replace_batch(self):
        """The v0.22 Re-Generate confirm, shared by Generate and
        "More like this one": candidates in the tab mean there is
        something to lose from the tab, so confirm first — default
        No, declining leaves the batch untouched. The batch itself
        is no longer destroyed — it stays in History — so the
        dialog says replaced, not discarded. True when there is
        nothing to lose, or the user accepts."""
        if not self.prompt_candidates:
            return True
        count = len(self.prompt_candidates)
        if self.prompt_keeper is not None:
            lost = ("%s (including your keeper)"
                    % plural(count, "candidate"))
        else:
            lost = plural(count, "candidate")
        answer = QMessageBox.question(
            self, "Generate a new batch?",
            "Generate a new batch? The current %s will be replaced "
            "in this tab — the batch stays in History." % lost,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes


    def _reset_prompt_tab_for_run(self, bdir):
        """Fresh-batch tab state before a worker starts."""
        self.prompt_batch_dir = bdir
        self.prompt_candidates = []
        self.prompt_keeper = None
        self.prompt_keeper_image = None
        self.prompt_result_image = None
        self.prompt_strip.clear()
        self.prompt_export_btn.setEnabled(False)
        self.prompt_save_library_btn.setEnabled(False)
        self.prompt_info_label.setText("Generating…")
        self._update_prompt_seed_label()
        self.statusBar().showMessage(
            "Generating 3 candidates — this takes a few minutes on "
            "CPU. The window stays usable.")


    def _start_prompt_worker(self, batch_dir, mode="generate",
                             target=None, total=DEFAULT_COUNT):
        """Wire a PromptWorker up and start it, with the buttons
        and the elapsed-time clock in the running state."""
        self.prompt_progress.setRange(0, total)
        self.prompt_progress.setValue(0)
        self.prompt_progress.setFormat("Generating… %v of %m — 0:00")
        self.prompt_generate_btn.setEnabled(False)
        self.prompt_regen_one_btn.setEnabled(False)
        self.prompt_more_like_btn.setEnabled(False)
        self.prompt_cancel_btn.setEnabled(True)
        self._prompt_started = time.monotonic()
        self._prompt_elapsed_timer.start()
        self.prompt_worker = PromptWorker(
            batch_dir, parent=self, mode=mode, target=target)
        self.prompt_worker.progress.connect(
            self._on_prompt_progress)
        self.prompt_worker.done.connect(self._on_prompt_done)
        self.prompt_worker.failed.connect(self._on_prompt_failed)
        self.prompt_worker.cancelled.connect(
            self._on_prompt_cancelled)
        self.prompt_worker.start()
        self._activity_start(
            "prompt",
            "Generating 0 of %s" % plural(total, "candidate"),
            cancel=self._on_prompt_cancel)


    def _confirm_model_fallback(self, model):
        """The found model is not the pinned LCM file — ask before
        generating with it (task 27). True when the model IS the
        pinned file, or the user accepts the fallback. A missing
        model never reaches here (require_model raises first);
        None is treated as "nothing to confirm"."""
        if model is None or is_pinned_model(model):
            return True
        answer = QMessageBox.question(
            self, "Use a different model?",
            "The model found is %s, not the pinned LCM file (%s). "
            "It may still work, or it may generate badly. Generate "
            "with it anyway?" % (model.name, MODEL_FILENAME),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes


    def _confirm_low_ram(self):
        """The machine is short on memory for a ~2.3 GB generation
        (task 27) — ask before starting. True when memory is
        unknown (no warning possible) or plentiful, or the user
        accepts."""
        available = gui_module.available_ram_bytes()
        if (available is None
                or available >= GENERATION_MIN_RAM_BYTES):
            return True
        answer = QMessageBox.question(
            self, "Low memory?",
            "Only %s of memory is free, and the image model needs "
            "about 2.1 GB — generation may be slow or fail. "
            "Generate anyway?" % format_bytes(available),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes


    def _on_prompt_generate(self):
        if self._prompt_generating():
            return
        # Autosave (task 55): generation is a long operation, so
        # the work is written down before it starts.
        self._autosave_write_now()
        self._refresh_prompt_status()
        try:
            prompt = validate_prompt(self.prompt_edit.text())
            require_engine()
            require_model()
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not generate", str(exc))
            return
        # Both confirms come before the replace-batch confirm and
        # before anything is cleaned up or created, so declining
        # leaves the tab exactly as it was.
        if not self._confirm_model_fallback(find_model()):
            return
        if not self._confirm_low_ram():
            return
        if not self._confirm_replace_batch():
            return
        self._cleanup_prompt_batch()
        try:
            # Batches live in a stable home under the library
            # folder now (task 20), so the generation history can
            # point at them across sessions — they used to live in
            # a temp dir the next generation deleted.
            home = batches_dir(self.library.directory)
            home.mkdir(parents=True, exist_ok=True)
            _batch, bdir = new_batch(
                home, prompt,
                negative=self.prompt_negative_edit.text().strip()
                or DEFAULT_NEGATIVE,
                steps=self.prompt_steps_spin.value(),
                cfg=self.prompt_cfg_spin.value(),
                size=self.prompt_gen_size_spin.value())
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not generate", str(exc))
            return
        self._record_prompt_text(prompt)
        self._reset_prompt_tab_for_run(bdir)
        self._start_prompt_worker(bdir)


    def _on_prompt_cancel(self):
        if self.prompt_worker is not None:
            self.prompt_worker.cancel()
            self.prompt_cancel_btn.setEnabled(False)
            self.statusBar().showMessage(
                "Cancelling — stopping the engine…")


    def _prompt_elapsed_text(self):
        """Elapsed generation time as M:SS (0:00 before start)."""
        if self._prompt_started is None:
            return "0:00"
        seconds = int(time.monotonic() - self._prompt_started)
        return "%d:%02d" % (seconds // 60, seconds % 60)


    def _on_prompt_elapsed_tick(self):
        # Once a second while generating: the progress bar's text
        # carries the elapsed time, so a minutes-long CPU run
        # shows it is still working.
        if self._prompt_generating():
            # %v / %m are Qt placeholders — they must reach Qt
            # literally, so the elapsed time is concatenated, not
            # %-formatted (Python's % chokes on %v).
            self.prompt_progress.setFormat(
                "Generating… %v of %m — "
                + self._prompt_elapsed_text())


    def _on_prompt_progress(self, done, total, record):
        # A queued signal from a previous worker can arrive after
        # the batch was cleaned up or a new generation started —
        # only the current worker's signals count.
        if (self.sender() is not self.prompt_worker
                or self.prompt_batch_dir is None):
            return
        self.prompt_progress.setValue(done)
        self.prompt_progress.setFormat(
            "Generating… %v of %m — "
            + self._prompt_elapsed_text())
        self._activity_update(
            "prompt",
            "Generating %d of %s"
            % (done, plural(total, "candidate")))
        if self.sender().mode == "regenerate":
            # A re-roll replaces a candidate the strip already
            # shows — appending would duplicate it. The done
            # handler reloads the batch.
            return
        self.statusBar().showMessage(
            "Generated %d of %d candidates — %s"
            % (done, total, self._prompt_elapsed_text()))
        if record["status"] == "pending":
            self.prompt_candidates.append(record)
            self._rebuild_prompt_strip()


    def _on_prompt_done(self, batch):
        # See _on_prompt_progress: only the current worker counts.
        if (self.sender() is not self.prompt_worker
                or self.prompt_batch_dir is None):
            return
        self._activity_finish("prompt")
        self._prompt_elapsed_timer.stop()
        took = self._prompt_elapsed_text()
        self.prompt_progress.setFormat("%v of %m candidates")
        self.prompt_generate_btn.setEnabled(True)
        self.prompt_cancel_btn.setEnabled(False)
        if self.sender().mode == "regenerate":
            self._on_prompt_rerolled(batch, took,
                                     self.sender().target)
            return
        self.prompt_candidates = [
            c for c in batch["candidates"]
            if c["status"] in ("pending", "kept")]
        self.prompt_progress.setValue(len(batch["candidates"]))
        self._rebuild_prompt_strip()
        failed = [c for c in batch["candidates"]
                  if c["status"] == "failed"]
        if self.prompt_candidates:
            self._pick_prompt_keeper(self.prompt_candidates[0]["file"])
            message = ("%s ready — click one to keep it (took %s)"
                       % (plural(len(self.prompt_candidates),
                                 "candidate"), took))
            if failed:
                message += " (%d failed)" % len(failed)
            self.statusBar().showMessage(message)
            self._record_current_batch()
            # The finish moment (task 94): generation landed.
            # The all-failed path below warns instead and
            # never reaches this; cancel and failure have
            # their own handlers and never pulse.
            self._motion.pulse(self.prompt_after_label)
        else:
            self.prompt_info_label.setText("No candidates generated")
            detail = failed[0].get("error", "") if failed else ""
            gui_module.warn_dialog(
                self, "Generation failed",
                "all %d candidates failed%s"
                % (len(failed), (" — " + detail) if detail else ""))
            self.statusBar().showMessage("Generation failed")
        self._update_prompt_iter_buttons()


    def _on_prompt_rerolled(self, batch, took, target):
        """A re-roll landed: refresh the candidates from the batch
        and keep the keeper — re-picking it reloads the new
        picture when the keeper was the one re-rolled."""
        self.prompt_progress.setValue(1)
        self.prompt_candidates = [
            c for c in batch["candidates"]
            if c["status"] in ("pending", "kept")]
        self._rebuild_prompt_strip()
        if self.prompt_keeper is not None:
            self._pick_prompt_keeper(self.prompt_keeper)
        self.statusBar().showMessage(
            "Regenerated %s (took %s)" % (target or "candidate", took))
        self._update_prompt_iter_buttons()
        # The finish moment (task 94): the re-roll landed.
        self._motion.pulse(self.prompt_after_label)


    def _on_prompt_failed(self, message):
        # See _on_prompt_progress: only the current worker counts.
        if (self.sender() is not self.prompt_worker
                or self.prompt_batch_dir is None):
            return
        self._activity_finish("prompt")
        self._prompt_elapsed_timer.stop()
        self.prompt_progress.setFormat("%v of %m candidates")
        self.prompt_generate_btn.setEnabled(True)
        self.prompt_cancel_btn.setEnabled(False)
        gui_module.warn_dialog(self, "Generation failed", message)
        self.statusBar().showMessage("Generation failed")
        self._update_prompt_iter_buttons()


    def _on_prompt_cancelled(self):
        # See _on_prompt_progress: only the current worker counts.
        if (self.sender() is not self.prompt_worker
                or self.prompt_batch_dir is None):
            return
        self._activity_finish("prompt")
        self._prompt_elapsed_timer.stop()
        self.prompt_progress.setFormat("%v of %m candidates")
        self.prompt_generate_btn.setEnabled(True)
        self.prompt_cancel_btn.setEnabled(False)
        if self.prompt_batch_dir is not None:
            try:
                batch = load_batch(self.prompt_batch_dir)
            except PixelForgeError as exc:
                # A corrupt batch.json must not kill the slot: say
                # so plainly and leave the batch alone (review B5).
                # The buttons are already re-enabled above, so the
                # window stays usable.
                gui_module.warn_dialog(self, "Could not read the batch record", str(exc))
                self.statusBar().showMessage(
                    "Cancelled — the batch record could not be read")
                self._update_prompt_iter_buttons()
                return
            self.prompt_candidates = [
                c for c in batch["candidates"]
                if c["status"] in ("pending", "kept")]
            self._rebuild_prompt_strip()
            if self.prompt_candidates:
                # A cancelled re-roll keeps the old keeper; a
                # cancelled batch picks the first candidate.
                if (self.sender().mode == "regenerate"
                        and self.prompt_keeper is not None):
                    self._pick_prompt_keeper(self.prompt_keeper)
                else:
                    self._pick_prompt_keeper(
                        self.prompt_candidates[0]["file"])
                self._record_current_batch()
        self.statusBar().showMessage(
            "Cancelled — %s kept"
            % plural(len(self.prompt_candidates), "candidate"))
        self._update_prompt_iter_buttons()


    def _cleanup_prompt_batch(self):
        # Detach from the current batch: stop the worker and clear
        # the tab's state. The batch dir itself stays — batches
        # live in the library's batches folder now, and the
        # generation history points at them (task 20). Nothing is
        # deleted here; the v0.22 confirm guards the tab, not the
        # files.
        self._stop_prompt_worker()
        self._prompt_elapsed_timer.stop()
        self.prompt_batch_dir = None
        self.prompt_candidates = []
        self.prompt_keeper = None
        self.prompt_keeper_image = None
        self.prompt_result_image = None
        self.prompt_strip.clear()
        self._update_prompt_seed_label()


    def _stop_prompt_worker(self):
        if self.prompt_worker is not None:
            if self.prompt_worker.isRunning():
                self.prompt_worker.cancel()
                self.prompt_worker.wait(10000)
            self.prompt_worker = None


    # ------------------------------------------------------ iteration

    def _update_prompt_iter_buttons(self):
        """Regenerate-one needs candidates; More-like-this needs
        a keeper; neither runs while a generation is in flight."""
        generating = self._prompt_generating()
        self.prompt_regen_one_btn.setEnabled(
            bool(self.prompt_candidates) and not generating)
        self.prompt_more_like_btn.setEnabled(
            self.prompt_keeper is not None and not generating)


    def _update_prompt_seed_label(self):
        """Show the keeper's seed, or hide the label with no
        keeper. The seed is the number that reproduces this
        exact picture."""
        record = None
        if self.prompt_keeper is not None:
            record = next((c for c in self.prompt_candidates
                           if c["file"] == self.prompt_keeper), None)
        if record is None:
            self.prompt_seed_label.setText("")
            self.prompt_seed_label.setVisible(False)
            return
        self.prompt_seed_label.setText("Seed: %d" % record["seed"])
        self.prompt_seed_label.setVisible(True)


    def _on_prompt_regenerate_one(self):
        """Re-roll ONE candidate: the one selected in the strip,
        or the keeper when nothing is selected. The other
        candidates stay, and a keeper that was not re-rolled
        stays the keeper."""
        if self._prompt_generating() or self.prompt_batch_dir is None:
            return
        target = None
        current = self.prompt_strip.currentItem()
        if current is not None:
            target = current.data(Qt.ItemDataRole.UserRole)
        if target is None:
            target = self.prompt_keeper
        if target is None:
            return
        self.prompt_info_label.setText("Regenerating %s…" % target)
        self.statusBar().showMessage("Regenerating %s…" % target)
        self._start_prompt_worker(
            self.prompt_batch_dir, mode="regenerate", target=target,
            total=1)


    def _on_prompt_more_like(self):
        """A NEW batch seeded from the keeper: the same prompt and
        negative prompt, seeds keeper+1, +2, +3. Goes through the
        same Re-Generate confirm as Generate — the current batch
        leaves the tab (it stays in History)."""
        if self._prompt_generating() or self.prompt_batch_dir is None:
            return
        keeper_seed = None
        for record in self.prompt_candidates:
            if record["file"] == self.prompt_keeper:
                keeper_seed = record["seed"]
                break
        if keeper_seed is None:
            return
        try:
            batch = load_batch(self.prompt_batch_dir)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not read the batch", str(exc))
            return
        if not self._confirm_replace_batch():
            return
        self._cleanup_prompt_batch()
        try:
            home = batches_dir(self.library.directory)
            home.mkdir(parents=True, exist_ok=True)
            _batch, bdir = new_batch(
                home, batch["prompt"],
                negative=batch.get("negative", DEFAULT_NEGATIVE),
                steps=int(batch.get("steps", LCM_STEPS)),
                cfg=float(batch.get("cfg", LCM_CFG)),
                size=int(batch.get("size", DEFAULT_SIZE)),
                seeds=[keeper_seed + 1, keeper_seed + 2,
                       keeper_seed + 3])
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not generate", str(exc))
            return
        # The tab shows the batch it is generating from, not
        # whatever the user typed since.
        self.prompt_edit.setText(batch["prompt"])
        self.prompt_negative_edit.setText(
            batch.get("negative", DEFAULT_NEGATIVE))
        self.prompt_negative_edit.setCursorPosition(0)
        self.prompt_steps_spin.setValue(
            int(batch.get("steps", LCM_STEPS)))
        self.prompt_cfg_spin.setValue(
            float(batch.get("cfg", LCM_CFG)))
        self.prompt_gen_size_spin.setValue(
            int(batch.get("size", DEFAULT_SIZE)))
        self._record_prompt_text(batch["prompt"])
        self._reset_prompt_tab_for_run(bdir)
        self._start_prompt_worker(bdir)


    # -------------------------------------------------------- history

    def _load_prompt_history(self):
        """Load the prompt history from the library folder and
        fill the prompt-history combo and the batch list. A
        corrupt file reads as empty and warns once in the status
        bar — startup never breaks on it, and the file is left
        alone (not deleted, not "repaired")."""
        (self._prompt_history,
         self._prompt_history_corrupt) = load_history(
            self.library.directory)
        self._refresh_prompt_history_combo()
        self._refresh_prompt_history_list()
        if self._prompt_history_corrupt:
            self.statusBar().showMessage(
                "The prompt history file is unreadable — starting "
                "with an empty history (the file was left alone)")


    def _save_prompt_history(self):
        """Write the history back. A failure shows in the status
        bar and never breaks the generation it rode along with."""
        if self._prompt_history is None:
            return
        try:
            save_history(self.library.directory,
                         self._prompt_history)
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))


    def _record_prompt_text(self, prompt):
        if self._prompt_history is None:
            return
        record_prompt(self._prompt_history, prompt)
        self._save_prompt_history()
        self._refresh_prompt_history_combo()


    def _record_current_batch(self):
        """Record the current batch in the generation history —
        prompt, negative, seeds, keeper, timestamp, batch dir —
        and refresh the list. A batch with no usable candidates
        is not recorded."""
        if (self.prompt_batch_dir is None
                or self._prompt_history is None):
            return
        try:
            batch = load_batch(self.prompt_batch_dir)
        except PixelForgeError:
            return
        landed = [c for c in batch["candidates"]
                  if c["status"] in ("pending", "kept")]
        if not landed:
            return
        record_batch(self._prompt_history, {
            "prompt": batch["prompt"],
            "negative": batch.get("negative", ""),
            "seeds": [c["seed"] for c in landed],
            "keeper": batch.get("keeper"),
            "created": batch.get("created", ""),
            "dir": str(self.prompt_batch_dir),
            "steps": batch.get("steps", LCM_STEPS),
            "cfg": batch.get("cfg", LCM_CFG),
            "size": batch.get("size", DEFAULT_SIZE),
        })
        self._save_prompt_history()
        self._refresh_prompt_history_list()


    def _refresh_prompt_history_combo(self):
        combo = self.prompt_history_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            for prompt in self._prompt_history["prompts"]:
                combo.addItem(prompt, prompt)
            combo.setCurrentIndex(-1)
        finally:
            combo.blockSignals(False)


    def _on_prompt_history_picked(self, index):
        if index < 0:
            return
        text = self.prompt_history_combo.itemData(index)
        if text:
            self.prompt_edit.setText(text)
        combo = self.prompt_history_combo
        combo.blockSignals(True)
        combo.setCurrentIndex(-1)
        combo.blockSignals(False)


    def _refresh_prompt_history_list(self):
        self.prompt_history_list.blockSignals(True)
        try:
            self.prompt_history_list.clear()
            for index, entry in enumerate(
                    self._prompt_history["batches"]):
                label = "%s — %s, %s" % (
                    entry["prompt"],
                    plural(len(entry.get("seeds", [])), "candidate"),
                    entry.get("created", ""))
                if not Path(entry["dir"]).is_dir():
                    label += " (batch folder missing)"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.prompt_history_list.addItem(item)
        finally:
            self.prompt_history_list.blockSignals(False)


    def _on_prompt_history_clicked(self, item):
        """Reopen a past batch: its candidates and keeper come
        back into the tab, with the prompt, negative prompt, and
        generation settings it was made with. The current batch
        is already in History, so nothing is lost and no confirm
        is needed. A batch whose folder is gone — or whose
        record cannot be read — warns and leaves the tab alone."""
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        entries = self._prompt_history["batches"]
        if index >= len(entries):
            return
        entry = entries[index]
        bdir = Path(entry["dir"])
        if not bdir.is_dir():
            gui_module.warn_dialog(
                self, "Batch folder missing",
                "that batch's folder is gone: %s" % bdir)
            return
        if self._prompt_generating():
            self.statusBar().showMessage(
                "Wait for the current generation to finish, then "
                "reopen the batch.")
            return
        try:
            batch = load_batch(bdir)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not read the batch", str(exc))
            return
        landed = [c for c in batch["candidates"]
                  if c["status"] in ("pending", "kept")]
        if not landed:
            gui_module.warn_dialog(
                self, "No usable candidates",
                "that batch has no usable candidates left")
            return
        self._load_prompt_batch(batch, bdir, landed)
        self.statusBar().showMessage(
            "Reopened batch from History: %s" % batch["prompt"])


    def _load_prompt_batch(self, batch, bdir, landed):
        """Land a batch on the Prompt tab: its candidates and
        keeper come back, with the prompt, negative prompt, and
        generation settings it was made with. Shared by History
        reopen and crash recovery (task 55) — the keeper comes
        from batch.json, where set_keeper already persisted it.
        The caller guarantees landed is non-empty."""
        self._cleanup_prompt_batch()
        self.prompt_batch_dir = bdir
        self.prompt_candidates = landed
        self.prompt_edit.setText(batch["prompt"])
        self.prompt_negative_edit.setText(
            batch.get("negative", DEFAULT_NEGATIVE))
        self.prompt_negative_edit.setCursorPosition(0)
        self.prompt_steps_spin.setValue(
            int(batch.get("steps", LCM_STEPS)))
        self.prompt_cfg_spin.setValue(
            float(batch.get("cfg", LCM_CFG)))
        self.prompt_gen_size_spin.setValue(
            int(batch.get("size", DEFAULT_SIZE)))
        self._rebuild_prompt_strip()
        keeper = batch.get("keeper")
        if keeper is not None and any(c["file"] == keeper
                                      for c in landed):
            self._pick_prompt_keeper(keeper)
        else:
            self._pick_prompt_keeper(landed[0]["file"])
        self._update_prompt_iter_buttons()


    def _on_negative_preset_picked(self, index):
        name = self.prompt_negative_preset_combo.itemData(index)
        if name is None:
            return  # "Custom" — the field already holds the text
        text = NEGATIVE_PRESETS[name]
        if self.prompt_negative_edit.text() != text:
            self.prompt_negative_edit.setText(text)
            self.prompt_negative_edit.setCursorPosition(0)


    def _on_prompt_text_changed(self, text):
        """Keep the character counter beside the prompt field."""
        length = len(text)
        self.prompt_counter_label.setText(
            "%d / %d" % (length, MAX_PROMPT_LEN))
        # Past the limit the counter takes the warning color —
        # the same color as the preset warning under the pickers.
        # A dynamic property, not an inline style, so the color
        # comes from the theme's stylesheet (task 67): the dark
        # theme's orange would vanish on the light background.
        # The unpolish/polish re-evaluates the stylesheet for
        # the new property value.
        self.prompt_counter_label.setProperty(
            "overLimit", length > MAX_PROMPT_LEN)
        style = self.prompt_counter_label.style()
        style.unpolish(self.prompt_counter_label)
        style.polish(self.prompt_counter_label)
        self.prompt_counter_label.update()


    def _on_open_model_download(self):
        """Open the pinned model's download page in the browser."""
        if not QDesktopServices.openUrl(QUrl(MODEL_URL)):
            # No browser to hand the URL to — say where it is
            # instead of failing silently.
            self.statusBar().showMessage(
                "Could not open the browser — the model is at %s"
                % MODEL_URL)


    def _on_negative_text_changed(self, text):
        """Mirror the field in the preset combo: a preset's exact
        text selects it, anything else reads as Custom."""
        combo = self.prompt_negative_preset_combo
        match = next((name for name, preset in NEGATIVE_PRESETS.items()
                      if preset == text), None)
        index = combo.findText(match if match is not None
                               else "Custom")
        if index >= 0 and combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)


    # ------------------------------------------------------ candidates

    def _rebuild_prompt_strip(self):
        if self.prompt_batch_dir is None:
            return
        self.prompt_strip.blockSignals(True)
        try:
            self.prompt_strip.clear()
            for record in self.prompt_candidates:
                path = self.prompt_batch_dir / record["file"]
                try:
                    image = gui_module.load_image(path)
                except PixelForgeError:
                    # A damaged candidate still lists — a dark
                    # square, like an unreadable library image.
                    thumb = QPixmap(STRIP_THUMB, STRIP_THUMB)
                    thumb.fill(Qt.GlobalColor.darkGray)
                else:
                    thumb = thumb_pixmap(image, STRIP_THUMB)
                label = "Candidate %s" % record["file"][5:7]
                if record["file"] == self.prompt_keeper:
                    label += " (keeper)"
                item = QListWidgetItem(thumb, label)
                item.setData(Qt.ItemDataRole.UserRole, record["file"])
                self.prompt_strip.addItem(item)
        finally:
            self.prompt_strip.blockSignals(False)
        self._update_prompt_iter_buttons()


    def _on_prompt_strip_clicked(self, item):
        filename = item.data(Qt.ItemDataRole.UserRole)
        if filename is not None:
            self._pick_prompt_keeper(filename)


    def _pick_prompt_keeper(self, filename):
        if self.prompt_batch_dir is None:
            return
        try:
            # Task 80: load first, mark second. Marking the
            # keeper before the load meant a corrupt candidate
            # landed in batch.json as keeper while the tab kept
            # the good one — the batch and the tab disagreed.
            # A failed load now leaves batch.json untouched.
            image = gui_module.load_image(self.prompt_batch_dir / filename)
            set_keeper(self.prompt_batch_dir, filename)
        except PixelForgeError as exc:
            # A load failure used to live only in the status bar,
            # which the next message overwrites (task 27). The
            # info label names the candidate and the reason, and
            # stays until the next successful preview.
            self.prompt_info_label.setText(
                "Could not load %s — %s" % (filename, exc))
            self.statusBar().showMessage(str(exc))
            return
        self.prompt_keeper = filename
        self.prompt_keeper_image = image
        self._rebuild_prompt_strip()
        self._update_prompt_seed_label()
        self._update_prompt_preview()
        # A new keeper: fit its pixel preview to the pane
        # (task 22). A control change does not re-fit.
        self._on_fit_prompt_zoom()


    # -------------------------------------------------------- controls

    def _on_prompt_preset(self):
        self._apply_size_preset("prompt")


    def _on_prompt_custom_size(self):
        self._reset_size_to_custom("prompt")


    def _on_prompt_control_changed(self):
        self._on_tab_control_changed("prompt")


    def _on_prompt_zoom_changed(self, value):
        self._on_zoom_changed_for("prompt", value)


    # -------------------------------------------------------- preview

    def _update_prompt_preview(self):
        """Re-run the Item 1 pixelizer on the keeper with the
        current controls."""
        if self.prompt_keeper_image is None:
            return
        try:
            result = gui_module.pixelize_image(
                self.prompt_keeper_image,
                width=self.prompt_width_spin.value(),
                height=self.prompt_height_spin.value(),
                keep_aspect=self.prompt_aspect_check.isChecked(),
                palette=self.prompt_palette_combo.currentData(),
                max_colors=self.prompt_colors_spin.value(),
                dither=self.prompt_dither_check.isChecked(),
                palettes=self.palette_store.user_palettes())
        except PixelForgeError as exc:
            # Same as the keeper load above (task 27): the failure
            # goes in the info label, not just the status bar, so
            # it stays visible instead of being overwritten.
            self.prompt_info_label.setText(
                "Could not pixelize %s — %s"
                % (self.prompt_keeper, exc))
            self.statusBar().showMessage(str(exc))
            return
        self.prompt_result_image = result
        self._refresh_prompt_pixmaps()
        self.prompt_info_label.setText(
            "Keeper: %s — %d x %d, %d colors"
            % (self.prompt_keeper, result.width(), result.height(),
               count_opaque_colors(result)))
        self.prompt_export_btn.setEnabled(True)
        self.prompt_save_library_btn.setEnabled(True)


    def _refresh_prompt_pixmaps(self):
        source = self.prompt_keeper_image
        fit = ORIGINAL_FIT / max(source.width(), source.height())
        # Task 60: same cache as _refresh_pixmaps — the keeper
        # pane does not change on a prompt zoom drag.
        cached = self._prompt_before_pixmap_cache
        if (cached is not None and cached[0] is source
                and cached[1] == fit):
            before = cached[2]
        else:
            before = gui_module.checkerboard_pixmap(source, fit, smooth=True)
            self._prompt_before_pixmap_cache = (source, fit, before)
        self.prompt_before_label.setPixmap(before)
        if self.prompt_result_image is not None:
            after = gui_module.checkerboard_pixmap(self.prompt_result_image,
                                        self.prompt_zoom_slider.value(),
                                        smooth=False)
            self.prompt_after_label.setPixmap(after)


    # --------------------------------------------------------- export

    def export_prompt_keeper(self, path):
        """Write the pixelized keeper to path as PNG. Returns True
        on success; on failure shows a message box and returns
        False."""
        if self.prompt_result_image is None:
            return False
        if not str(path).lower().endswith(".png"):
            path = "%s.png" % path
        if not self.prompt_result_image.save(str(path), "PNG"):
            gui_module.warn_dialog(self, "Could not export PNG",
                                "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
        # The finish moment (task 94): the file is written.
        self._motion.pulse(self.prompt_after_label)
        return True


    def _on_export_prompt_keeper(self):
        if self.prompt_result_image is None:
            return
        # The batch folder is internal storage, not a user source
        # — so the keeper defaults to the last-used export folder
        # (task 22), never to the batch dir or the home folder.
        name = "%s-pixel.png" % (
            self.prompt_batch_dir.name
            if self.prompt_batch_dir is not None else "sprite")
        default = self._export_default(name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", default, "PNG image (*.png)")
        if path and self.export_prompt_keeper(path):
            self._remember_export_dir(Path(path).parent)
