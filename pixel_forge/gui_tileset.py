"""Tileset tab: Wang and blob autotile sets..

One of the MainWindow mixins (task 58).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)
from .gui_shared import gui_module, supported_filter
from pathlib import Path
from .pixelize import PixelForgeError, plural
from .spritesheet import pack_frames
from .tileset import (BLOB_SHAPES, DEFAULT_BLOB_SHAPE, MODE_NAMES, SHAPE_NAMES, TILESET_MODES, blob_tiles, export_tileset, wang_tiles)


class TilesetTabMixin:
    """Tileset tab: Wang and blob autotile sets.

    One of the MainWindow mixins (task 58): the methods
    moved here verbatim from gui.py. MainWindow
    inherits this class; self is the window.
    """


    # ========================================================= tileset tab

    def _build_tileset_tab(self):
        root = QWidget()
        self.tileset_tab = root
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ------------------------------------------------ controls
        controls = QWidget()
        form = QVBoxLayout(controls)
        form.setContentsMargins(0, 0, 0, 0)

        form.addWidget(self._section("Mode"))
        self.tileset_mode_combo = QComboBox()
        self.tileset_mode_combo.setToolTip(
            "Wang 16 builds 16 tiles from two terrains; Blob 47 "
            "builds 47 tiles from one terrain on transparency")
        for key in TILESET_MODES:
            self.tileset_mode_combo.addItem(MODE_NAMES[key], key)
        # Nothing selected by default — Generate stays disabled
        # until a mode is picked (and the base tiles chosen).
        self.tileset_mode_combo.setCurrentIndex(-1)
        self.tileset_mode_combo.currentIndexChanged.connect(
            self._on_tileset_mode_changed)
        form.addWidget(self.tileset_mode_combo)

        # Blob shape (task 82; blob mode only): how a blob
        # quadrant is drawn. Square is the current look —
        # straight cuts, an isolated cell shows nothing.
        # Rounded is the classic sub-blob look — outer curves,
        # an isolated cell is a rounded diamond and the corners
        # are rounded. Nothing is picked until the user picks
        # one; until then the square construction is what the
        # app already does. The whole section hides as a unit
        # unless blob mode is picked.
        self.tileset_shape_section = QWidget()
        shape_form = QVBoxLayout(self.tileset_shape_section)
        shape_form.setContentsMargins(0, 0, 0, 0)
        shape_form.addWidget(self._section("Blob shape"))
        self.tileset_shape_combo = QComboBox()
        self.tileset_shape_combo.setToolTip(
            "How a blob quadrant is drawn: Square is the current "
            "look, straight cuts. Rounded is the classic "
            "sub-blob look, outer curves.")
        for key in BLOB_SHAPES:
            self.tileset_shape_combo.addItem(SHAPE_NAMES[key], key)
        self.tileset_shape_combo.setCurrentIndex(-1)
        self.tileset_shape_combo.currentIndexChanged.connect(
            self._on_tileset_shape_changed)
        shape_form.addWidget(self.tileset_shape_combo)
        shape_note = QLabel("Square is the current look. "
                            "Rounded makes a lone cell a rounded "
                            "diamond and rounds the corners.")
        shape_note.setWordWrap(True)
        shape_form.addWidget(shape_note)
        self.tileset_shape_section.setVisible(False)
        form.addWidget(self.tileset_shape_section)

        form.addWidget(self._section("Base tiles"))
        self.tileset_pick_a_btn = QPushButton("Choose base tile")
        self.tileset_pick_a_btn.setToolTip(
            "Pick the base tile — the terrain for a blob set, "
            "terrain A for a Wang set")
        self.tileset_pick_a_btn.clicked.connect(
            self._on_tileset_pick_a)
        form.addWidget(self.tileset_pick_a_btn)
        a_row = QHBoxLayout()
        self.tileset_a_preview = QLabel()
        self.tileset_a_preview.setFixedSize(64, 64)
        self.tileset_a_preview.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        a_row.addWidget(self.tileset_a_preview)
        self.tileset_a_name = QLabel("No base tile chosen")
        self.tileset_a_name.setWordWrap(True)
        a_row.addWidget(self.tileset_a_name, 1)
        form.addLayout(a_row)

        # The second base tile only exists in Wang mode — the
        # whole row hides as a unit in blob mode.
        self.tileset_b_section = QWidget()
        b_form = QVBoxLayout(self.tileset_b_section)
        b_form.setContentsMargins(0, 0, 0, 0)
        self.tileset_pick_b_btn = QPushButton("Choose terrain B")
        self.tileset_pick_b_btn.setToolTip(
            "Pick the second base tile — terrain B for a Wang set")
        self.tileset_pick_b_btn.clicked.connect(
            self._on_tileset_pick_b)
        b_form.addWidget(self.tileset_pick_b_btn)
        b_row = QHBoxLayout()
        self.tileset_b_preview = QLabel()
        self.tileset_b_preview.setFixedSize(64, 64)
        self.tileset_b_preview.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        b_row.addWidget(self.tileset_b_preview)
        self.tileset_b_name = QLabel("No terrain B chosen")
        self.tileset_b_name.setWordWrap(True)
        b_row.addWidget(self.tileset_b_name, 1)
        b_form.addLayout(b_row)
        self.tileset_b_section.setVisible(False)
        form.addWidget(self.tileset_b_section)

        self.tileset_generate_btn = QPushButton("Generate tileset")
        self.tileset_generate_btn.setObjectName("export")
        self.tileset_generate_btn.setToolTip(
            "Build the tileset from the base tiles")
        self.tileset_generate_btn.setEnabled(False)
        self.tileset_generate_btn.clicked.connect(
            self._on_tileset_generate)
        form.addWidget(self.tileset_generate_btn)

        self.tileset_count_label = QLabel("No tileset yet")
        self.tileset_count_label.setWordWrap(True)
        form.addWidget(self.tileset_count_label)

        form.addWidget(self._section("Zoom"))
        zoom_row = QHBoxLayout()
        self.tileset_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.tileset_zoom_slider.setRange(1, 16)
        self.tileset_zoom_slider.setValue(4)
        self.tileset_zoom_slider.setToolTip("Zoom the tileset preview")
        self.tileset_zoom_slider.valueChanged.connect(
            self._on_tileset_zoom_changed)
        zoom_row.addWidget(self.tileset_zoom_slider)
        self.tileset_zoom_label = QLabel("4x")
        self.tileset_zoom_label.setFixedWidth(32)
        zoom_row.addWidget(self.tileset_zoom_label)
        self.tileset_zoom_fit_btn = QPushButton("Fit")
        self.tileset_zoom_fit_btn.setToolTip(
            "Zoom to fit the preview pane")
        self.tileset_zoom_fit_btn.clicked.connect(
            self._on_fit_tileset_zoom)
        zoom_row.addWidget(self.tileset_zoom_fit_btn)
        form.addLayout(zoom_row)

        form.addWidget(self._section("Export"))
        self.tileset_export_png_btn = QPushButton("Export PNG")
        self.tileset_export_png_btn.setObjectName("export")
        self.tileset_export_png_btn.setToolTip(
            "Export the tileset as one PNG image")
        self.tileset_export_png_btn.setEnabled(False)
        self.tileset_export_png_btn.clicked.connect(
            self._on_tileset_export_png)
        form.addWidget(self.tileset_export_png_btn)

        self.tileset_export_tiled_btn = QPushButton("Export for Tiled")
        self.tileset_export_tiled_btn.setObjectName("export")
        self.tileset_export_tiled_btn.setToolTip(
            "Export the tileset PNG, a Tiled tileset, and a demo map")
        self.tileset_export_tiled_btn.setEnabled(False)
        self.tileset_export_tiled_btn.clicked.connect(
            self._on_tileset_export_tiled)
        form.addWidget(self.tileset_export_tiled_btn)

        self.tileset_export_godot_btn = QPushButton("Export for Godot")
        self.tileset_export_godot_btn.setObjectName("export")
        self.tileset_export_godot_btn.setToolTip(
            "Export the tileset PNG and a Godot 4 TileSet")
        self.tileset_export_godot_btn.setEnabled(False)
        self.tileset_export_godot_btn.clicked.connect(
            self._on_tileset_export_godot)
        form.addWidget(self.tileset_export_godot_btn)

        self.tileset_save_library_btn = QPushButton("Save to library")
        self.tileset_save_library_btn.setToolTip(
            "Save the tileset PNG to the library")
        self.tileset_save_library_btn.setEnabled(False)
        self.tileset_save_library_btn.clicked.connect(
            self._on_save_tileset_to_library)
        form.addWidget(self.tileset_save_library_btn)

        main_layout.addWidget(
            self._scrollable_column(controls))

        # ------------------------------------------------ preview
        preview_pane = QWidget()
        preview_layout = QVBoxLayout(preview_pane)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self._section("Tileset"))
        self.tileset_preview_label = QLabel(
            "Pick a mode and choose base tiles, then press Generate.")
        self.tileset_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.tileset_preview_label.setWordWrap(True)
        self.tileset_preview_scroll = QScrollArea()
        self.tileset_preview_scroll.setWidgetResizable(True)
        self.tileset_preview_scroll.setWidget(self.tileset_preview_label)
        preview_layout.addWidget(self.tileset_preview_scroll, 1)
        main_layout.addWidget(preview_pane, 1)
        return root


    # ------------------------------------------------------------ input

    def _on_tileset_mode_changed(self, _index):
        """A mode was picked (or unpicked): show or hide the
        second base tile and the blob shape section, rename the
        first picker, and clear the generated set — it was built
        under the old mode."""
        self.tileset_mode = self.tileset_mode_combo.currentData()
        is_wang = self.tileset_mode == "wang"
        self.tileset_b_section.setVisible(is_wang)
        self.tileset_shape_section.setVisible(
            self.tileset_mode == "blob")
        self.tileset_pick_a_btn.setText(
            "Choose terrain A" if is_wang else "Choose base tile")
        self._clear_tileset()
        self._refresh_tileset_buttons()
        self._mark_autosave_dirty()


    def _on_tileset_shape_changed(self, _index):
        """A blob shape was picked (or unpicked): record it and
        clear the generated set — it was built under the old
        shape. Nothing picked means the square construction,
        which is what the app already does."""
        self.tileset_shape = self.tileset_shape_combo.currentData()
        self._clear_tileset()
        self._refresh_tileset_buttons()
        self._mark_autosave_dirty()


    def _tileset_shape(self):
        """The blob shape to generate with: the picked shape, or
        the square default when nothing is picked."""
        return self.tileset_shape or DEFAULT_BLOB_SHAPE


    def _clear_tileset(self):
        """Drop the generated set (the mode or a base tile
        changed) and put the preview back to its empty text."""
        self.tileset_tiles = None
        self.tileset_sheet = None
        self.tileset_columns = None
        self.tileset_preview_label.setPixmap(QPixmap())
        self.tileset_preview_label.setText(
            "Pick a mode and choose base tiles, then press Generate.")
        self.tileset_count_label.setText("No tileset yet")


    def _refresh_tileset_buttons(self):
        """Generate needs a mode and its base tiles; the exports
        and Save to library need a generated set."""
        have_mode = self.tileset_mode is not None
        have_a = self.tileset_base_a is not None
        have_b = self.tileset_base_b is not None
        ready = have_mode and have_a and (
            self.tileset_mode != "wang" or have_b)
        self.tileset_generate_btn.setEnabled(ready)
        have_set = self.tileset_tiles is not None
        self.tileset_export_png_btn.setEnabled(have_set)
        self.tileset_export_tiled_btn.setEnabled(have_set)
        self.tileset_export_godot_btn.setEnabled(have_set)
        self.tileset_save_library_btn.setEnabled(have_set)


    def _show_tileset_base(self, which):
        """The small preview + file name for base tile A or B."""
        if which == "a":
            image = self.tileset_base_a
            path = self.tileset_base_a_path
            preview = self.tileset_a_preview
            name = self.tileset_a_name
            empty = "No base tile chosen"
        else:
            image = self.tileset_base_b
            path = self.tileset_base_b_path
            preview = self.tileset_b_preview
            name = self.tileset_b_name
            empty = "No terrain B chosen"
        if image is None:
            preview.clear()
            name.setText(empty)
            return
        # The preview is 64 px on a side; a bigger tile scales
        # down to fit, a smaller one scales up, nearest neighbor
        # so the pixels stay crisp.
        scale = 64 / max(image.width(), image.height())
        preview.setPixmap(gui_module.checkerboard_pixmap(image, scale,
                                              smooth=False))
        name.setText("%s (%d x %d)"
                     % (Path(path).name, image.width(),
                        image.height()))


    def load_tileset_base(self, path, which="a"):
        """Load an image as base tile A (or B). Returns True on
        success; on failure shows a warning and returns False,
        leaving the old base tile in place."""
        try:
            image = gui_module.load_image(path)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not load base tile", str(exc))
            return False
        if which == "a":
            self.tileset_base_a = image
            self.tileset_base_a_path = str(path)
        else:
            self.tileset_base_b = image
            self.tileset_base_b_path = str(path)
        self._show_tileset_base(which)
        self._clear_tileset()
        self._refresh_tileset_buttons()
        if which == "b":
            role = "terrain B"
        elif self.tileset_mode == "wang":
            role = "terrain A"
        else:
            role = "base tile"
        self.statusBar().showMessage("Loaded %s as %s"
                                     % (Path(path).name, role))
        self._mark_autosave_dirty()
        return True


    def _on_tileset_pick_a(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose base tile", "", supported_filter())
        if path:
            self.load_tileset_base(path, "a")


    def _on_tileset_pick_b(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose terrain B", "", supported_filter())
        if path:
            self.load_tileset_base(path, "b")


    def _on_tileset_generate(self):
        """Build the set from the base tiles and show it packed."""
        if self.tileset_mode is None or self.tileset_base_a is None:
            return
        try:
            if self.tileset_mode == "wang":
                if self.tileset_base_b is None:
                    return
                tiles = wang_tiles(self.tileset_base_a,
                                   self.tileset_base_b)
            else:
                tiles = blob_tiles(self.tileset_base_a,
                                   shape=self._tileset_shape())
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not generate tileset", str(exc))
            return
        sheet, layout = pack_frames(tiles)
        self.tileset_tiles = tiles
        self.tileset_sheet = sheet
        self.tileset_columns = layout["columns"]
        tile = tiles[0]
        self.tileset_count_label.setText(
            "%s, %d x %d each"
            % (plural(len(tiles), "tile"), tile.width(),
               tile.height()))
        self._on_fit_tileset_zoom()
        self._refresh_tileset_pixmap()
        self._refresh_tileset_buttons()
        self.statusBar().showMessage(
            "Generated %s" % plural(len(tiles), "tile"))


    # ---------------------------------------------------------- preview

    def _refresh_tileset_pixmap(self):
        if self.tileset_sheet is None:
            return
        pixmap = gui_module.checkerboard_pixmap(
            self.tileset_sheet, self.tileset_zoom_slider.value(),
            smooth=False)
        self.tileset_preview_label.setPixmap(pixmap)


    def _on_tileset_zoom_changed(self, value):
        self.tileset_zoom_label.setText("%dx" % value)
        self._refresh_tileset_pixmap()


    def _on_fit_tileset_zoom(self):
        """Fit the tileset preview to its pane (task 22)."""
        self._fit_zoom(self.tileset_zoom_slider,
                       self.tileset_preview_scroll,
                       self.tileset_sheet)


    # ----------------------------------------------------------- export

    def export_tileset_png(self, path):
        """Write the generated tileset sheet to path as PNG.
        Returns True on success; on failure shows a message box
        and returns False."""
        if self.tileset_sheet is None:
            return False
        if not str(path).lower().endswith(".png"):
            path = "%s.png" % path
        if not self.tileset_sheet.save(str(path), "PNG"):
            gui_module.warn_dialog(self, "Could not export PNG",
                        "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
        return True


    def _on_tileset_export_png(self):
        if self.tileset_sheet is None:
            return
        # Next to the first base tile, else the last-used export
        # folder (task 22).
        default = self._export_default(
            "%s-tileset.png" % Path(self.tileset_base_a_path).stem,
            self.tileset_base_a_path)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export tileset PNG", default, "PNG image (*.png)")
        if path and self.export_tileset_png(path):
            self._remember_export_dir(Path(path).parent)


    def _tileset_export_stem(self):
        """The file stem for a tileset export: the first base
        tile's name, so a Wang set from grass + water exports as
        grass.png / grass.tsx / grass-demo.tmx / grass.tres."""
        return Path(self.tileset_base_a_path).stem


    def _on_tileset_export_tiled(self):
        """Write the PNG + .tsx + demo .tmx into one folder."""
        if self.tileset_tiles is None:
            return
        start_dir = str(Path(self.tileset_base_a_path).parent)
        folder = QFileDialog.getExistingDirectory(
            self, "Export for Tiled to folder", start_dir)
        if not folder:
            return
        try:
            files = export_tileset(
                self.tileset_mode, self.tileset_tiles, folder,
                self._tileset_export_stem(), export="tmx")
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export for Tiled", str(exc))
            return
        self._remember_export_dir(folder)
        self.statusBar().showMessage(
            "Exported %s" % ", ".join(p.name for p in files))


    def _on_tileset_export_godot(self):
        """Write the PNG + .tres into one folder."""
        if self.tileset_tiles is None:
            return
        start_dir = str(Path(self.tileset_base_a_path).parent)
        folder = QFileDialog.getExistingDirectory(
            self, "Export for Godot to folder", start_dir)
        if not folder:
            return
        try:
            files = export_tileset(
                self.tileset_mode, self.tileset_tiles, folder,
                self._tileset_export_stem(), export="tres")
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export for Godot", str(exc))
            return
        self._remember_export_dir(folder)
        self.statusBar().showMessage(
            "Exported %s" % ", ".join(p.name for p in files))


    def save_tileset_to_library(self, name, tags):
        """Save the generated tileset PNG to the library under
        name (the SaveToLibraryDialog saver). Returns the entry;
        raises PixelForgeError when there is nothing to save."""
        if self.tileset_sheet is None:
            raise PixelForgeError("nothing to save to the library")
        # No "tileset" source exists (task 79 owns adding
        # sources) — the sheet is saved as a plain file sprite.
        entry = self.library.add_image(
            self.tileset_sheet, name, tags, source="file")
        self._refresh_library()
        return entry


    def _on_save_tileset_to_library(self):
        if self.tileset_sheet is None:
            return
        default = "%s-tileset" % self._tileset_export_stem()
        self._save_to_library_via_dialog(
            default, self.save_tileset_to_library)
