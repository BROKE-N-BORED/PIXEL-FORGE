"""Code tab: rebuild the sprites code defines..

One of the MainWindow mixins (task 58).
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)
from .codeinput import parse_paths
from .gui_shared import (MAX_CUSTOM_SIZE, TEXTURE_DEBOUNCE_MS, TextureWorker, code_filter, gui_module, supported_filter, thumb_pixmap)
from .imagegen import slugify
from pathlib import Path
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError, count_opaque_colors, plural)
from .skins import (detect_skin, extract_skin, reassemble_skin_file, skin_figure, skin_parts, texture_target_size)


class CodeTabMixin:
    """Code tab: rebuild the sprites code defines.

    One of the MainWindow mixins (task 58): the methods
    moved here verbatim from gui.py. MainWindow
    inherits this class; self is the window.
    """


    # ============================================================== code tab

    def _build_code_tab(self):
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

        self.code_import_btn = QPushButton("Import code")
        self.code_import_btn.setToolTip(
            "Import source code files to find the pixel art they "
            "define")
        self.code_import_btn.clicked.connect(self._on_import_code)
        form.addWidget(self.code_import_btn)

        self.code_import_folder_btn = QPushButton("Import code folder")
        self.code_import_folder_btn.setToolTip(
            "Import a folder of source code files")
        self.code_import_folder_btn.clicked.connect(
            self._on_import_code_folder)
        form.addWidget(self.code_import_folder_btn)

        self.code_import_asset_btn = QPushButton("Import game asset")
        self.code_import_asset_btn.setToolTip(
            "Import a game asset — a Minecraft skin or a texture")
        self.code_import_asset_btn.clicked.connect(
            self._on_import_game_asset)
        form.addWidget(self.code_import_asset_btn)

        form.addWidget(self._section("Assets"))
        self.code_list = QListWidget()
        self.code_list.setToolTip(
            "Assets found in the imported code — click one to "
            "see it")
        self.code_list.setIconSize(QSize(32, 32))
        self.code_list.itemClicked.connect(
            self._on_code_item_clicked)
        form.addWidget(self.code_list, 1)

        self.code_skipped_heading = self._section(
            "Found but not rendered")
        form.addWidget(self.code_skipped_heading)
        self.code_skipped_label = QLabel(
            "Import code to see what it defines.")
        self.code_skipped_label.setWordWrap(True)
        # The reasons list can run long (one line per skipped
        # asset, each wrapping) — a scroll box caps what it costs
        # the column, so the Zoom row and info label below it are
        # never squeezed out. Sizing the label to its full text
        # instead overflowed the column and hid them entirely.
        # 110 px shows about five lines before scrolling (task 22:
        # 72 px showed barely two).
        skipped_scroll = QScrollArea()
        skipped_scroll.setWidgetResizable(True)
        skipped_scroll.setFixedHeight(110)
        skipped_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        skipped_scroll.setWidget(self.code_skipped_label)
        form.addWidget(skipped_scroll)

        # Skin-detection override (task 26): a 64x64 or 64x32
        # image is detected as a skin by size alone, so a
        # non-skin of exactly that size would be split into
        # parts. The button offers the other reading — and back
        # again. Visible only for skin-sized assets; the text
        # names the action it will take.
        self.code_treat_texture_btn = QPushButton("Treat as texture")
        self.code_treat_texture_btn.setToolTip(
            "Render this skin-sized image as a plain texture "
            "instead of splitting it into skin parts")
        self.code_treat_texture_btn.clicked.connect(
            self._on_treat_texture_toggled)
        self.code_treat_texture_btn.setVisible(False)
        form.addWidget(self.code_treat_texture_btn)

        # Texture controls (task 26): a texture used to render
        # at fixed native size with a 256-color from-image
        # palette and no dithering. The controls mirror the
        # Image tab's — same widgets, same ranges — and a change
        # re-renders through the worker. The whole section lives
        # in one container so it shows and hides as a unit; it
        # is visible only while a texture is showing.
        self.code_texture_section = QWidget()
        texture_form = QVBoxLayout(self.code_texture_section)
        texture_form.setContentsMargins(0, 0, 0, 0)
        texture_form.addWidget(self._section("Texture"))
        texture_size_row = QHBoxLayout()
        self.code_texture_width_spin = QSpinBox()
        self.code_texture_width_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.code_texture_width_spin.setPrefix("W ")
        self.code_texture_width_spin.setToolTip(
            "Texture width in pixels")
        self.code_texture_width_spin.valueChanged.connect(
            self._on_code_texture_control_changed)
        self.code_texture_height_spin = QSpinBox()
        self.code_texture_height_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.code_texture_height_spin.setPrefix("H ")
        self.code_texture_height_spin.setToolTip(
            "Texture height in pixels")
        self.code_texture_height_spin.valueChanged.connect(
            self._on_code_texture_control_changed)
        texture_size_row.addWidget(self.code_texture_width_spin)
        texture_size_row.addWidget(self.code_texture_height_spin)
        texture_form.addLayout(texture_size_row)
        self.code_texture_palette_combo = QComboBox()
        self.code_texture_palette_combo.setToolTip(
            "Colors the texture may use")
        self._fill_palette_combo(self.code_texture_palette_combo)
        self.code_texture_palette_combo.currentIndexChanged.connect(
            self._on_code_texture_control_changed)
        texture_form.addWidget(self.code_texture_palette_combo)
        texture_colors_row = QHBoxLayout()
        texture_colors_row.addWidget(QLabel("Max colors"))
        self.code_texture_colors_spin = QSpinBox()
        self.code_texture_colors_spin.setRange(MIN_COLORS, MAX_COLORS)
        self.code_texture_colors_spin.setValue(MAX_COLORS)
        self.code_texture_colors_spin.setToolTip(
            "Most colors the texture may use")
        self.code_texture_colors_spin.valueChanged.connect(
            self._on_code_texture_control_changed)
        texture_colors_row.addWidget(self.code_texture_colors_spin)
        texture_form.addLayout(texture_colors_row)
        self.code_texture_dither_check = QCheckBox("Dithering")
        self.code_texture_dither_check.setToolTip(
            "Ordered (Bayer) dithering")
        self.code_texture_dither_check.toggled.connect(
            self._on_code_texture_control_changed)
        texture_form.addWidget(self.code_texture_dither_check)
        self.code_texture_section.setVisible(False)
        form.addWidget(self.code_texture_section)

        form.addWidget(self._section("Zoom"))
        zoom_row = QHBoxLayout()
        self.code_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.code_zoom_slider.setRange(1, 16)
        self.code_zoom_slider.setValue(8)
        self.code_zoom_slider.setToolTip("Zoom the sprite preview")
        self.code_zoom_slider.valueChanged.connect(
            self._on_code_zoom_changed)
        zoom_row.addWidget(self.code_zoom_slider)
        self.code_zoom_label = QLabel("8x")
        self.code_zoom_label.setFixedWidth(32)
        zoom_row.addWidget(self.code_zoom_label)
        self.code_zoom_fit_btn = QPushButton("Fit")
        self.code_zoom_fit_btn.setToolTip(
            "Zoom to fit the preview pane")
        self.code_zoom_fit_btn.clicked.connect(
            self._on_fit_code_zoom)
        zoom_row.addWidget(self.code_zoom_fit_btn)
        form.addLayout(zoom_row)

        self.code_info_label = QLabel("No code loaded")
        self.code_info_label.setWordWrap(True)
        form.addWidget(self.code_info_label)

        self.code_export_btn = QPushButton("Export PNG")
        self.code_export_btn.setObjectName("export")
        self.code_export_btn.setToolTip(
            "Export the selected asset as a PNG")
        self.code_export_btn.setEnabled(False)
        self.code_export_btn.clicked.connect(
            self._on_export_code_asset)
        form.addWidget(self.code_export_btn)

        self.code_export_parts_btn = QPushButton("Export parts")
        # Disabled until a skin is loaded — and the tooltip says
        # so (task 22): a disabled button that only ever describes
        # what it would do leaves the user guessing why it won't.
        self.code_export_parts_btn.setToolTip(
            "Load a Minecraft skin to export its parts")
        self.code_export_parts_btn.setEnabled(False)
        self.code_export_parts_btn.clicked.connect(
            self._on_export_code_parts)
        form.addWidget(self.code_export_parts_btn)

        # Skin reassembly + 3D preview (task 45): the same
        # lifecycle as Export parts — enabled only while a skin
        # is loaded, with tooltips that say why when disabled.
        self.code_export_skin_btn = QPushButton("Export skin PNG")
        self.code_export_skin_btn.setToolTip(
            "Load a Minecraft skin to export it as one 64x64 PNG")
        self.code_export_skin_btn.setEnabled(False)
        self.code_export_skin_btn.clicked.connect(
            self._on_export_skin_png)
        form.addWidget(self.code_export_skin_btn)

        self.code_preview_skin_btn = QPushButton("3D preview")
        self.code_preview_skin_btn.setToolTip(
            "Load a Minecraft skin to see it on a 3D figure")
        self.code_preview_skin_btn.setEnabled(False)
        self.code_preview_skin_btn.clicked.connect(
            self._on_preview_skin)
        form.addWidget(self.code_preview_skin_btn)

        self.code_export_all_btn = QPushButton("Export all")
        self.code_export_all_btn.setObjectName("export")
        self.code_export_all_btn.setToolTip(
            "Export every asset in the list as PNG files")
        self.code_export_all_btn.setEnabled(False)
        self.code_export_all_btn.clicked.connect(
            self._on_export_code_all)
        form.addWidget(self.code_export_all_btn)

        self.code_save_library_btn = QPushButton("Save to library")
        self.code_save_library_btn.setToolTip(
            "Save the selected asset to the library")
        self.code_save_library_btn.setEnabled(False)
        self.code_save_library_btn.clicked.connect(
            self._on_save_code_to_library)
        form.addWidget(self.code_save_library_btn)

        main_layout.addWidget(
            self._scrollable_column(controls))

        # ------------------------------------------------ preview
        preview_pane = QWidget()
        preview_layout = QVBoxLayout(preview_pane)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self._section("Sprite"))
        self.code_preview_label = QLabel(
            "Import code or a game asset to see its sprites here")
        self.code_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.code_preview_label.setWordWrap(True)
        self.code_preview_scroll = QScrollArea()
        self.code_preview_scroll.setWidgetResizable(True)
        self.code_preview_scroll.setWidget(self.code_preview_label)
        preview_layout.addWidget(self.code_preview_scroll, 1)
        main_layout.addWidget(preview_pane, 1)
        self._fit_code_info_label()
        return root


    def _fit_code_info_label(self):
        """Size the Code tab's info label to its text.

        Same problem as the Prompt status label (v0.4): Qt does
        not reliably apply a word-wrapped QLabel's heightForWidth
        inside this fixed-width column, so the info text
        overflowed into the controls below it. The minimum height
        comes from the label's OWN heightForWidth at 230 px —
        slightly narrower than the 250 px column, so it wraps at
        least as much and the height is never short. Call it
        after any setText on the label. (The skipped-reasons
        label lives in a scroll box instead — its text can run
        long enough to overflow the column on its own.)
        """
        label = self.code_info_label
        label.setMinimumHeight(label.heightForWidth(230) + 2)
        label.updateGeometry()


    # ------------------------------------------------------------ input

    def _set_parts_button(self, enabled, tooltip):
        """The Export parts button's state and its tooltip together.

        The tooltip explains the CURRENT state: while the button
        is disabled it says why (nothing loaded yet, or the import
        is not a skin), so the user is never left guessing. This
        Qt build shows tooltips on disabled widgets too (verified
        live in v0.33), so the reason is visible exactly when the
        button cannot be used.
        """
        self.code_export_parts_btn.setEnabled(enabled)
        self.code_export_parts_btn.setToolTip(tooltip)


    def _set_skin_buttons(self, enabled, export_tip, preview_tip):
        """The Export skin PNG and 3D preview buttons' state and
        tooltips together — same pattern as _set_parts_button:
        while disabled the tooltips say why, so the user is
        never left guessing."""
        self.code_export_skin_btn.setEnabled(enabled)
        self.code_export_skin_btn.setToolTip(export_tip)
        self.code_preview_skin_btn.setEnabled(enabled)
        self.code_preview_skin_btn.setToolTip(preview_tip)


    def load_code(self, paths):
        """Parse code files or folders and list the assets
        found. Returns True on success. On failure shows a message
        box and returns False — the window stays up either way."""
        if isinstance(paths, (str, Path)):
            paths = [paths]
        self._stop_texture_worker()
        # A code import is not a texture and not a skin: the
        # override and the texture source go away with it.
        self.code_treat_as_texture = False
        self.code_texture_image = None
        self.code_texture_path = None
        try:
            result = parse_paths(paths)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import code", str(exc))
            return False
        if not result["assets"] and not result["not_rendered"]:
            gui_module.warn_dialog(
                self, "No pixel-art assets found",
                "no pixel-art assets found in %s — the code "
                "defines no pixel maps, palettes, colors, or "
                "image references Pixel Forge can read (see the "
                "README for what is detected)"
                % ", ".join(Path(p).name for p in paths))
            return False
        self.code_assets = result["assets"]
        self.code_not_rendered = result["not_rendered"]
        self.code_skipped = result["skipped"]
        self.code_paths = [str(p) for p in paths]
        self.code_source = ", ".join(str(p) for p in paths)
        self.code_source_first = str(paths[0]) if paths else None
        self.code_kind = "code"
        self.code_skin_path = None
        self.code_skin_layout = None
        self._set_parts_button(
            False, "Only Minecraft skins have parts to export")
        self._set_skin_buttons(
            False,
            "Only Minecraft skins can be exported as one PNG",
            "Only Minecraft skins have a 3D preview")
        self._rebuild_code_list()
        self._update_code_texture_controls()
        message = ("%s from %s"
                   % (plural(len(result["assets"]), "asset"),
                      plural(len(result["files"]), "file")))
        if result["skipped"]:
            message += (", %s skipped"
                        % plural(len(result["skipped"]), "file"))
        self.statusBar().showMessage(message)
        self._record_code_session(paths, result["files"])
        return True


    def load_game_asset(self, path):
        """Load a game asset: a Minecraft skin splits into its
        parts (plus the combined figure) right away; anything else
        pixelizes as a texture on a worker thread (see
        TextureWorker), so a big photo never freezes the window.
        A skin-sized image can be treated as a texture instead
        with the override button (task 26). Returns True when
        the load started (or finished, for a skin); False when
        the file cannot be read at all."""
        try:
            image = gui_module.load_image(path)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import game asset", str(exc))
            return False
        layout = detect_skin(image)
        self.code_paths = [str(path)]
        self.code_source = str(path)
        self.code_source_first = str(path)
        self.code_not_rendered = []
        self.code_skipped = []
        self._stop_texture_worker()
        # A new import resets the override (task 26): whatever
        # the last asset was treated as says nothing about this
        # one. The source image stays in memory so the texture
        # controls can re-pixelize from it without re-reading
        # the file.
        self.code_treat_as_texture = False
        self.code_texture_image = image
        self.code_texture_path = str(path)
        if layout is not None:
            ok = self._show_skin(image, path, layout)
            if ok:
                self._record_session(path, "skin")
            return ok
        # Texture: pixelize off the GUI thread with the controls
        # reset to this texture's defaults (native size, 256
        # colors, no dithering) — the first render is exactly
        # what an untouched texture always was.
        self.code_skin_path = None
        self.code_skin_layout = None
        self._reset_code_texture_controls(image)
        self._start_texture_worker()
        self._record_session(path, "skin")
        return True


    def _show_skin(self, image, path, layout):
        """Render a skin-sized image as a skin: its parts plus
        the combined figure. Returns True on success, False
        when the split fails. Shared by load_game_asset and the
        override toggle back from a texture."""
        try:
            parts = skin_parts(image)
            figure = skin_figure(image)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not split the skin", str(exc))
            return False
        self.code_kind = "skin"
        self.code_skin_path = str(path)
        self.code_skin_layout = layout
        self.code_assets = []
        for name, part in parts.items():
            self.code_assets.append({
                "name": name, "kind": "part",
                "source_file": str(path),
                "width": part.width(), "height": part.height(),
                "colors": count_opaque_colors(part),
                "image": part,
                "detail": "front view, %s skin" % layout})
        self.code_assets.append({
            "name": "figure", "kind": "figure",
            "source_file": str(path),
            "width": figure.width(), "height": figure.height(),
            "colors": count_opaque_colors(figure),
            "image": figure,
            "detail": "combined figure, overlays included"})
        self._set_parts_button(
            True, "Export the skin's parts as separate PNGs")
        self._set_skin_buttons(
            True, "Export the skin as one 64x64 PNG",
            "See the skin on a 3D figure")
        self._rebuild_code_list()
        self._update_code_texture_controls()
        self.statusBar().showMessage(
            "Minecraft skin (%s): %d parts"
            % (layout, len(parts)))
        return True


    def _on_texture_done(self, result):
        # See _on_prompt_progress: only the current worker counts
        # — a texture load superseded by a newer import is ignored.
        if self.sender() is not self.texture_worker:
            return
        self.texture_worker = None
        self._activity_finish("texture")
        texture = result["image"]
        self.code_kind = "texture"
        # An overridden skin keeps its skin path and layout —
        # they are what the toggle back needs, and what keeps
        # the override button visible. A plain texture has
        # neither.
        if self.code_treat_as_texture:
            self._set_parts_button(
                False,
                "This image is being treated as a texture — "
                "switch back to a skin to export its parts")
            self._set_skin_buttons(
                False,
                "This image is being treated as a texture. "
                "Switch back to a skin to export the skin PNG",
                "This image is being treated as a texture. "
                "Switch back to a skin to see the 3D preview")
        else:
            self.code_skin_path = None
            self.code_skin_layout = None
            self._set_parts_button(
                False, "Only Minecraft skins have parts to export")
            self._set_skin_buttons(
                False,
                "Only Minecraft skins can be exported as one PNG",
                "Only Minecraft skins have a 3D preview")
        src_w, src_h = (result["source_width"],
                        result["source_height"])
        # Capped means the 512 px cap shrank the source — not
        # the texture controls asking for a smaller size, which
        # the settings suffix below already names.
        cap_w, cap_h = texture_target_size(self.code_texture_image)
        capped = (cap_w, cap_h) != (src_w, src_h)
        if self.code_treat_as_texture:
            detail = ("%d x %d skin-sized image — treated as a "
                      "texture" % (src_w, src_h))
        elif capped:
            detail = ("downscaled from %d x %d to %d x %d — "
                      "pixelized as a texture"
                      % (src_w, src_h, texture.width(),
                         texture.height()))
        else:
            detail = ("not a Minecraft skin (%d x %d) — "
                      "pixelized as a texture" % (src_w, src_h))
        detail += self._texture_settings_suffix(result)
        self.code_assets = [{
            "name": Path(result["path"]).stem, "kind": "texture",
            "source_file": result["path"],
            "width": texture.width(), "height": texture.height(),
            "colors": result["colors"],
            "image": texture,
            "detail": detail}]
        self._rebuild_code_list()
        self._update_code_texture_controls()
        if self.code_treat_as_texture:
            self.statusBar().showMessage(
                "Skin-sized image treated as a texture")
        elif capped:
            self.statusBar().showMessage(
                "Not a skin — pixelized as a texture "
                "(downscaled from %d x %d)" % (src_w, src_h))
        else:
            self.statusBar().showMessage(
                "Not a skin — pixelized as a texture")


    def _texture_settings_suffix(self, result):
        """What the texture controls did to this render, in the
        detail line's words — only the settings that differ
        from a fresh import's defaults, so an untouched texture
        keeps its plain detail line. The settings are the ones
        the worker ran with (they ride back in the result), not
        the controls as they stand when the render lands."""
        settings = result.get("settings", {})
        if not settings:
            return ""
        cap_w, cap_h = texture_target_size(self.code_texture_image)
        parts = []
        if ((settings.get("width"), settings.get("height"))
                != (cap_w, cap_h)):
            parts.append("%d x %d" % (settings["width"],
                                     settings["height"]))
        palette = settings.get("palette", "image")
        if palette != "image":
            parts.append(self._palette_label(palette))
        if settings.get("max_colors", MAX_COLORS) != MAX_COLORS:
            parts.append("%d colors" % settings["max_colors"])
        if settings.get("dither"):
            parts.append("dithered")
        if not parts:
            return ""
        return ", " + ", ".join(parts)


    def _on_texture_failed(self, message):
        # See _on_prompt_progress: only the current worker counts.
        if self.sender() is not self.texture_worker:
            return
        self.texture_worker = None
        self._activity_finish("texture")
        gui_module.warn_dialog(self, "Texture import failed", message)
        self.statusBar().showMessage("Texture load failed")


    def _stop_texture_worker(self):
        if self.texture_worker is not None:
            if self.texture_worker.isRunning():
                self.texture_worker.wait(10000)
            self.texture_worker = None


    def _texture_settings(self):
        """The texture controls as a pixelize_texture call."""
        return {
            "width": self.code_texture_width_spin.value(),
            "height": self.code_texture_height_spin.value(),
            "palette": self.code_texture_palette_combo.currentData(),
            "max_colors": self.code_texture_colors_spin.value(),
            "dither": self.code_texture_dither_check.isChecked(),
            "palettes": self.palette_store.user_palettes(),
        }


    def _start_texture_worker(self):
        """Pixelize the current texture source on a worker thread
        with the controls' settings snapshotted at start."""
        self._stop_texture_worker()
        self.texture_worker = TextureWorker(
            self.code_texture_image, self.code_texture_path,
            settings=self._texture_settings(), parent=self)
        self.texture_worker.done.connect(self._on_texture_done)
        self.texture_worker.failed.connect(self._on_texture_failed)
        self.statusBar().showMessage("Loading texture…")
        # No Cancel: the render is one call that cannot be
        # interrupted mid-flight, and a newer run supersedes —
        # _stop_texture_worker waits for the old one first.
        self._activity_start("texture", "Loading texture…")
        self.texture_worker.start()


    def _reset_code_texture_controls(self, image):
        """A fresh texture's defaults: its native size (capped
        at 512), the from-image palette, 256 colors, no
        dithering — the render an untouched texture always had.
        Signals blocked, so the reset itself starts no worker."""
        target_w, target_h = texture_target_size(image)
        self.code_texture_width_spin.blockSignals(True)
        self.code_texture_height_spin.blockSignals(True)
        self.code_texture_palette_combo.blockSignals(True)
        self.code_texture_colors_spin.blockSignals(True)
        self.code_texture_dither_check.blockSignals(True)
        try:
            self.code_texture_width_spin.setValue(target_w)
            self.code_texture_height_spin.setValue(target_h)
            self.code_texture_palette_combo.setCurrentIndex(0)
            self.code_texture_colors_spin.setValue(MAX_COLORS)
            self.code_texture_dither_check.setChecked(False)
        finally:
            self.code_texture_width_spin.blockSignals(False)
            self.code_texture_height_spin.blockSignals(False)
            self.code_texture_palette_combo.blockSignals(False)
            self.code_texture_colors_spin.blockSignals(False)
            self.code_texture_dither_check.blockSignals(False)


    def _on_code_texture_control_changed(self):
        """A texture control moved — wait for the controls to
        settle, then re-render. Nothing to do while no texture
        is showing."""
        if (self.code_kind != "texture"
                or self.code_texture_image is None):
            return
        self._texture_debounce_timer.start(TEXTURE_DEBOUNCE_MS)


    def _on_texture_debounce(self):
        if (self.code_kind == "texture"
                and self.code_texture_image is not None):
            self._start_texture_worker()


    def _update_code_texture_controls(self):
        """Show the texture controls and the override button for
        the asset on screen: the controls only while a texture
        is showing, the override button only for a skin-sized
        asset — and it stays visible while one is overridden,
        so the way back is always there. The button's text and
        tooltip name the action it will take."""
        skin_sized = self.code_skin_layout is not None
        self.code_treat_texture_btn.setVisible(skin_sized)
        if self.code_treat_as_texture:
            self.code_treat_texture_btn.setText("Treat as skin")
            self.code_treat_texture_btn.setToolTip(
                "Go back to splitting this image into skin parts")
        else:
            self.code_treat_texture_btn.setText("Treat as texture")
            self.code_treat_texture_btn.setToolTip(
                "Render this skin-sized image as a plain texture "
                "instead of splitting it into skin parts")
        self.code_texture_section.setVisible(
            self.code_kind == "texture")


    def _on_treat_texture_toggled(self):
        """The skin override (task 26): a skin-sized image
        re-renders as a plain texture, or back into skin parts.
        The source image is already in memory either way, so
        the toggle back needs no re-read."""
        if self.code_skin_layout is None:
            return
        if self.code_treat_as_texture:
            self.code_treat_as_texture = False
            self._stop_texture_worker()
            self._show_skin(self.code_texture_image,
                            self.code_texture_path,
                            self.code_skin_layout)
            return
        self.code_treat_as_texture = True
        self._set_parts_button(
            False,
            "This image is being treated as a texture — switch "
            "back to a skin to export its parts")
        self._set_skin_buttons(
            False,
            "This image is being treated as a texture. "
            "Switch back to a skin to export the skin PNG",
            "This image is being treated as a texture. "
            "Switch back to a skin to see the 3D preview")
        self._reset_code_texture_controls(self.code_texture_image)
        self._start_texture_worker()


    def _on_import_code(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import code", "", code_filter())
        if paths:
            self.load_code(paths)


    def _on_import_code_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Import code folder", "")
        if folder:
            self.load_code(folder)


    def _on_import_game_asset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import game asset", "", supported_filter())
        if path:
            self.load_game_asset(path)


    # ------------------------------------------------------------ list

    def _rebuild_code_list(self):
        """Rebuild the asset list and the not-rendered note from
        the current Code tab state; select the first asset."""
        self.code_list.blockSignals(True)
        try:
            self.code_list.clear()
            for index, asset in enumerate(self.code_assets):
                thumb = thumb_pixmap(asset["image"], 32)
                item = QListWidgetItem(
                    thumb,
                    "%s — %s, %d x %d" % (
                        asset["name"], asset["kind"],
                        asset["width"], asset["height"]))
                item.setData(Qt.ItemDataRole.UserRole, index)
                item.setToolTip(asset["detail"])
                self.code_list.addItem(item)
        finally:
            self.code_list.blockSignals(False)
        lines = ["%s: %s" % (note["name"], note["reason"])
                 for note in self.code_not_rendered]
        lines += ["%s: %s" % (Path(skip["file"]).name,
                              skip["reason"])
                  for skip in self.code_skipped]
        if lines:
            self.code_skipped_label.setText("\n".join(lines))
            # The heading carries the count, so a long list reads
            # as "3 skipped" at a glance (task 22).
            self.code_skipped_heading.setText(
                "Found but not rendered (%d skipped)" % len(lines))
        elif self.code_assets:
            self.code_skipped_label.setText(
                "Everything found was rendered.")
            self.code_skipped_heading.setText("Found but not rendered")
        else:
            self.code_skipped_label.setText(
                "Import code to see what it defines.")
            self.code_skipped_heading.setText("Found but not rendered")
        self.code_current = None
        self.code_image = None
        self.code_info_label.setText(
            plural(len(self.code_assets), "asset")
            if self.code_assets else "No code loaded")
        self._fit_code_info_label()
        self.code_preview_label.setPixmap(QPixmap())
        self.code_preview_label.setText(
            "Select an asset to preview it"
            if self.code_assets else
            "Import code or a game asset to see its sprites here")
        self.code_export_btn.setEnabled(False)
        self.code_export_all_btn.setEnabled(False)
        self.code_save_library_btn.setEnabled(False)
        if self.code_assets:
            self.code_list.setCurrentRow(0)
            self._show_code_asset(0)


    def _on_code_item_clicked(self, item):
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self._show_code_asset(index)


    def _show_code_asset(self, index):
        asset = self.code_assets[index]
        self.code_current = index
        self.code_image = asset["image"]
        # A new asset: fit the sprite preview to the pane
        # (task 22). The fit sets the slider, which refreshes
        # the pixmap at the fit zoom.
        self._on_fit_code_zoom()
        self._refresh_code_pixmap()
        colors = asset["colors"]
        count_text = "%d color%s" % (colors,
                                     "" if colors == 1 else "s")
        # A palette asset's detail IS the color count ("3 colors")
        # — showing it under a header that already says "3 colors"
        # reads as a stutter (review B15). Drop the duplicate line;
        # every other detail ("int rows, …") stays.
        detail = asset["detail"]
        lines = ["%s — %s, %d x %d, %s"
                 % (asset["name"], asset["kind"],
                    asset["width"], asset["height"], count_text)]
        if detail and detail != count_text \
                and detail != "%d colors" % colors:
            lines.append(detail)
        lines.append(Path(asset["source_file"]).name)
        self.code_info_label.setText("\n".join(lines))
        self._fit_code_info_label()
        self.code_export_btn.setEnabled(True)
        self.code_export_all_btn.setEnabled(True)
        self.code_save_library_btn.setEnabled(True)


    def _refresh_code_pixmap(self):
        if self.code_image is None:
            return
        pixmap = gui_module.checkerboard_pixmap(
            self.code_image, self.code_zoom_slider.value(),
            smooth=False)
        self.code_preview_label.setPixmap(pixmap)


    def _on_code_zoom_changed(self, value):
        self.code_zoom_label.setText("%dx" % value)
        self._refresh_code_pixmap()


    # --------------------------------------------------------- export

    def export_code_asset(self, path):
        """Write the selected Code tab asset to path as PNG.
        Returns True on success; on failure shows a message box
        and returns False."""
        if self.code_image is None:
            return False
        if not str(path).lower().endswith(".png"):
            path = "%s.png" % path
        if not self.code_image.save(str(path), "PNG"):
            gui_module.warn_dialog(self, "Could not export PNG",
                                "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
        return True


    def _on_export_code_asset(self):
        if self.code_image is None:
            return
        asset = self.code_assets[self.code_current]
        # Next to the first source file (task 22) — the old code
        # defaulted to the home folder even when the source was
        # somewhere else entirely.
        default = self._export_default(
            "%s.png" % asset["name"], self.code_source_first)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", default, "PNG image (*.png)")
        if path and self.export_code_asset(path):
            self._remember_export_dir(Path(path).parent)


    def _on_export_code_parts(self):
        if self.code_skin_path is None:
            return
        # Next to the skin file, else the last-used export folder
        # (task 22).
        start_dir = str(Path(self.code_skin_path).parent)
        folder = QFileDialog.getExistingDirectory(
            self, "Export skin parts to folder", start_dir)
        if not folder:
            return
        try:
            written = extract_skin(self.code_skin_path, folder)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export skin parts", str(exc))
            return
        self._remember_export_dir(folder)
        self.statusBar().showMessage(
            "Exported %s to %s"
            % (plural(len(written), "skin part"), folder))


    def _on_export_skin_png(self):
        """Export the loaded skin as one canonical 64x64 PNG
        (task 45): a classic 64x32 skin comes out with its left
        limbs mirrored in and its overlays left transparent."""
        if self.code_skin_path is None:
            return
        stem = Path(self.code_skin_path).stem
        default = self._export_default("%s-skin.png" % stem,
                                       self.code_skin_path)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export skin PNG", default, "PNG image (*.png)")
        if not path:
            return
        try:
            reassemble_skin_file(self.code_skin_path, path)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export the skin", str(exc))
            return
        self._remember_export_dir(Path(path).parent)
        self.statusBar().showMessage(
            "Exported the skin as a 64x64 PNG to %s" % path)


    def _on_preview_skin(self):
        """Open the 3D skin preview in its own window (task 45).
        The source image is already in memory from the load, so
        no re-read is needed."""
        if self.code_skin_path is None:
            return
        image = self.code_texture_image
        if image is None:
            try:
                image = gui_module.load_image(self.code_skin_path)
            except PixelForgeError as exc:
                gui_module.warn_dialog(self, "Could not open the 3D preview",
                            str(exc))
                return
        try:
            dialog = gui_module.SkinPreviewDialog(self, image)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not open the 3D preview",
                        str(exc))
            return
        dialog.exec()


    def export_code_assets(self, folder):
        """Write every Code tab asset to folder as PNG, one file
        per asset named after the asset (a second asset with the
        same name gets -2, -3, ...). Returns (written, failed):
        the count written and the list of asset names that failed
        to write. Shows nothing — the caller reports the result."""
        written, failed, used = 0, [], set()
        for asset in self.code_assets:
            name = slugify(asset["name"])
            stem = name
            number = 2
            while name in used:
                name = "%s-%d" % (stem, number)
                number += 1
            used.add(name)
            path = Path(folder) / (name + ".png")
            if asset["image"].save(str(path), "PNG"):
                written += 1
            else:
                failed.append(asset["name"])
        return written, failed


    def _on_export_code_all(self):
        if not self.code_assets:
            return
        # Next to the first source file, else the last-used
        # export folder (task 22).
        if self.code_source_first is not None:
            start_dir = str(Path(self.code_source_first).parent)
        else:
            start_dir = self._last_export_dir() or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Export all assets to folder", start_dir)
        if not folder:
            return
        written, failed = self.export_code_assets(folder)
        self._remember_export_dir(folder)
        if failed:
            gui_module.warn_dialog(self, "Some assets failed to export",
                        "%s exported, %s failed (%s)"
                        % (plural(written, "asset"),
                           plural(len(failed), "asset"),
                           ", ".join(failed)))
        self.statusBar().showMessage(
            "Exported %s to %s"
            % (plural(written, "asset"), folder))
