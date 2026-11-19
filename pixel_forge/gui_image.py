"""Image tab: import, pixelize, edit, export..

One of the MainWindow mixins (task 58).
"""

from . import bgremove, normalmap, procedural
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)
from .codeinput import CODE_EXTENSIONS
from .editor import (distinct_colors, draw_line, draw_rect_outline, flood_fill, line_points, pick_color, rect_points)
from .gui_shared import (BgRemoveWorker, EDITOR_TOOLS, GridSnapWorker, MAX_CUSTOM_SIZE, NormalMapWorker, ORIGINAL_FIT, SIZE_PRESETS, _LayerStackCommand, _PieMenu, _PixelCanvas, _PixelEditCommand, gui_module, image_extensions, supported_filter)
from .image import Image, argb
from .layers import Layer, composite_layers, has_content
from pathlib import Path
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError, count_opaque_colors, plural)
from .textstamp import stamp_image
from .video import VIDEO_EXTENSIONS
import json
import random


class ImageTabMixin:
    """Image tab: import, pixelize, edit, export.

    One of the MainWindow mixins (task 58): the methods
    moved here verbatim from gui.py. MainWindow
    inherits this class; self is the window.
    """


    # ============================================================ image tab

    def _build_image_tab(self):
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

        self.import_btn = QPushButton("Import image")
        self.import_btn.setToolTip(
            "Import an image to turn into a sprite")
        self.import_btn.clicked.connect(self._on_import)
        form.addWidget(self.import_btn)

        # Background removal (task 41): runs on this machine
        # through rembg's u2net model — the subject stays, the
        # rest goes transparent, and the result replaces the
        # loaded image. The button is available whenever an
        # image is loaded; a missing model or missing packages
        # says plainly what to install when it is pressed.
        form.addWidget(self._section("Background"))
        bgremove_row = QHBoxLayout()
        self.bgremove_btn = QPushButton("Remove background")
        self.bgremove_btn.setToolTip(
            "Remove the background from the loaded image, on "
            "this machine — the subject stays, the rest goes "
            "transparent")
        self.bgremove_btn.setEnabled(False)
        self.bgremove_btn.clicked.connect(
            self._on_remove_background)
        bgremove_row.addWidget(self.bgremove_btn)
        self.bgremove_cancel_btn = QPushButton("Cancel")
        self.bgremove_cancel_btn.setToolTip(
            "Stop the background removal — the image is left "
            "exactly as it was")
        self.bgremove_cancel_btn.setEnabled(False)
        self.bgremove_cancel_btn.clicked.connect(
            self._on_bgremove_cancel)
        bgremove_row.addWidget(self.bgremove_cancel_btn)
        bgremove_row.addStretch(1)
        form.addLayout(bgremove_row)

        # Pixel-grid snap (task 42): an AI image meant to be
        # pixel art comes back noisy — pixels off-grid, blended
        # edges, stray colors. The snap finds the image's
        # native pixel grid, takes the median color per cell,
        # and quantises to the current palette, and the result
        # replaces the loaded image. Opt-in only: the button is
        # available whenever an image is loaded, and a clean
        # sprite or a gridless image says so plainly and is
        # left alone.
        form.addWidget(self._section("Pixel grid"))
        gridsnap_row = QHBoxLayout()
        self.gridsnap_btn = QPushButton("Snap to pixel grid")
        self.gridsnap_btn.setToolTip(
            "Clean up an AI image that is meant to be pixel "
            "art. Finds its pixel grid, takes the middle color "
            "of each cell, and quantises to the current palette")
        self.gridsnap_btn.setEnabled(False)
        self.gridsnap_btn.clicked.connect(
            self._on_snap_to_grid)
        gridsnap_row.addWidget(self.gridsnap_btn)
        self.gridsnap_cancel_btn = QPushButton("Cancel")
        self.gridsnap_cancel_btn.setToolTip(
            "Stop the pixel grid snap. The image is left "
            "exactly as it was")
        self.gridsnap_cancel_btn.setEnabled(False)
        self.gridsnap_cancel_btn.clicked.connect(
            self._on_gridsnap_cancel)
        gridsnap_row.addWidget(self.gridsnap_cancel_btn)
        gridsnap_row.addStretch(1)
        form.addLayout(gridsnap_row)

        # Normal map (task 43): a normal map for 2D lighting,
        # made from the pixelized sprite's brightness — bright
        # reads as high, dark as low. Generation runs in a
        # worker (a 512 x 512 image takes about a second), the
        # map can be shown in place of the sprite, and it
        # exports as a PNG. The map belongs to the result it
        # was made from: any control change, edit, or new load
        # clears it, and the buttons say so by going quiet.
        form.addWidget(self._section("Normal map"))
        normalmap_row = QHBoxLayout()
        self.normalmap_btn = QPushButton("Generate normal map")
        self.normalmap_btn.setToolTip(
            "Make a normal map from the sprite for 2D lighting. "
            "Bright parts read as high, dark parts as low")
        self.normalmap_btn.setEnabled(False)
        self.normalmap_btn.clicked.connect(
            self._on_generate_normal_map)
        normalmap_row.addWidget(self.normalmap_btn)
        self.normalmap_cancel_btn = QPushButton("Cancel")
        self.normalmap_cancel_btn.setToolTip(
            "Stop generating the normal map. Any map already "
            "made is kept")
        self.normalmap_cancel_btn.setEnabled(False)
        self.normalmap_cancel_btn.clicked.connect(
            self._on_normalmap_cancel)
        normalmap_row.addWidget(self.normalmap_cancel_btn)
        normalmap_row.addStretch(1)
        form.addLayout(normalmap_row)
        normalmap_view_row = QHBoxLayout()
        self.normalmap_strength_spin = QDoubleSpinBox()
        self.normalmap_strength_spin.setRange(
            normalmap.MIN_STRENGTH, normalmap.MAX_STRENGTH)
        self.normalmap_strength_spin.setDecimals(2)
        self.normalmap_strength_spin.setSingleStep(0.25)
        self.normalmap_strength_spin.setValue(
            normalmap.DEFAULT_STRENGTH)
        self.normalmap_strength_spin.setPrefix("Strength ")
        self.normalmap_strength_spin.setToolTip(
            "How strong the lighting relief is. 1.00 is the "
            "true slope of the sprite, lower is flatter, "
            "higher is more dramatic")
        normalmap_view_row.addWidget(self.normalmap_strength_spin)
        self.normalmap_show_btn = QPushButton("Show normal map")
        self.normalmap_show_btn.setCheckable(True)
        self.normalmap_show_btn.setToolTip(
            "Show the normal map in the pixel preview instead "
            "of the sprite. Press again to go back")
        self.normalmap_show_btn.setEnabled(False)
        self.normalmap_show_btn.clicked.connect(
            self._on_show_normal_map)
        normalmap_view_row.addWidget(self.normalmap_show_btn)
        normalmap_view_row.addStretch(1)
        form.addLayout(normalmap_view_row)
        form.addWidget(self._explain(
            "normalmap_strength_spin",
            "How strong the bumps look. Higher is more "
            "dramatic."))
        self.normalmap_export_btn = QPushButton("Export normal map")
        self.normalmap_export_btn.setToolTip(
            "Save the normal map as a PNG next to the sprite")
        self.normalmap_export_btn.setEnabled(False)
        self.normalmap_export_btn.clicked.connect(
            self._on_export_normal_map)
        form.addWidget(self.normalmap_export_btn)

        # Procedural generation (task 44): make a sprite from
        # a number. The kind combo starts with nothing picked
        # (the standing rule), and Generate stays quiet until
        # a kind is picked. The seed spin takes any whole
        # number, Random picks a fresh one, and the size spin
        # sets the sprite's size. Generation takes
        # milliseconds, so it runs right here, no worker.
        form.addWidget(self._section("Generate"))
        procedural_row = QHBoxLayout()
        self.procedural_kind_combo = QComboBox()
        self.procedural_kind_combo.setToolTip(
            "What kind of sprite to make")
        for kind in procedural.KINDS:
            self.procedural_kind_combo.addItem(
                procedural.KIND_LABELS[kind], kind)
        self.procedural_kind_combo.setCurrentIndex(-1)
        self.procedural_kind_combo.setPlaceholderText("Pick a kind")
        self.procedural_kind_combo.currentIndexChanged.connect(
            self._on_procedural_kind_changed)
        procedural_row.addWidget(self.procedural_kind_combo)
        self.procedural_generate_btn = QPushButton("Generate")
        self.procedural_generate_btn.setToolTip(
            "Make a sprite from the seed and load it into the "
            "tab, ready to edit, export, or save to the library")
        self.procedural_generate_btn.setEnabled(False)
        self.procedural_generate_btn.clicked.connect(
            self._on_generate_procedural)
        procedural_row.addWidget(self.procedural_generate_btn)
        procedural_row.addStretch(1)
        form.addLayout(procedural_row)
        form.addWidget(self._explain(
            "procedural_kind_combo",
            "What kind of sprite to make."))
        procedural_seed_row = QHBoxLayout()
        self.procedural_seed_spin = QSpinBox()
        self.procedural_seed_spin.setRange(-1000000, 1000000)
        self.procedural_seed_spin.setValue(1)
        # Task 89: the spin's size hint is its widest value
        # ("Seed -1000000", 120 px), which pushed the whole
        # seed row to 293 px — past the column's 252 px
        # viewport, so the size spin's right edge clipped.
        # Capped at 76 px, the row (76 + 78 + 83 + spacing)
        # fits; a long seed scrolls inside the spin, the way
        # a long number does in any spin box.
        self.procedural_seed_spin.setMaximumWidth(76)
        self.procedural_seed_spin.setPrefix("Seed ")
        self.procedural_seed_spin.setToolTip(
            "The number the sprite is made from. The same kind "
            "and seed always make the same sprite")
        procedural_seed_row.addWidget(self.procedural_seed_spin)
        self.procedural_random_btn = QPushButton("Random")
        self.procedural_random_btn.setToolTip(
            "Pick a fresh seed at random")
        self.procedural_random_btn.clicked.connect(
            self._on_procedural_random)
        procedural_seed_row.addWidget(self.procedural_random_btn)
        self.procedural_size_spin = QSpinBox()
        self.procedural_size_spin.setRange(
            procedural.MIN_SIZE, procedural.MAX_SIZE)
        self.procedural_size_spin.setValue(
            procedural.DEFAULT_SIZE)
        self.procedural_size_spin.setPrefix("Size ")
        self.procedural_size_spin.setToolTip(
            "The sprite's size in pixels, width and height")
        procedural_seed_row.addWidget(self.procedural_size_spin)
        procedural_seed_row.addStretch(1)
        form.addLayout(procedural_seed_row)
        form.addWidget(self._explain(
            "procedural_seed_spin",
            "Same kind and seed always make the same sprite."))
        form.addWidget(self._explain(
            "procedural_size_spin",
            "The sprite's size in pixels, width and height."))
        self.procedural_seed_label = QLabel("")
        self.procedural_seed_label.setWordWrap(True)
        self.procedural_seed_label.setToolTip(
            "The kind and seed that made the loaded sprite")
        form.addWidget(self.procedural_seed_label)

        form.addWidget(self._section("Size"))
        self.size_combo = QComboBox()
        self.size_combo.setToolTip("Sprite size in pixels")
        for preset in SIZE_PRESETS:
            self.size_combo.addItem("%d x %d" % (preset, preset), preset)
        self.size_combo.addItem("Custom", None)
        self.size_combo.setCurrentIndex(1)  # 32 x 32
        self.size_combo.currentIndexChanged.connect(self._on_preset)
        form.addWidget(self.size_combo)
        form.addWidget(self._explain(
            "size_combo",
            "The size of the finished sprite, in pixels."))

        size_row = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.width_spin.setValue(32)
        self.width_spin.setPrefix("W ")
        self.width_spin.setToolTip("Sprite width in pixels")
        self.width_spin.valueChanged.connect(self._on_custom_size)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.height_spin.setValue(32)
        self.height_spin.setPrefix("H ")
        self.height_spin.setToolTip("Sprite height in pixels")
        self.height_spin.valueChanged.connect(self._on_custom_size)
        size_row.addWidget(self.width_spin)
        size_row.addWidget(self.height_spin)
        form.addLayout(size_row)
        form.addWidget(self._explain(
            "image_size_row",
            "Width and height in pixels. Editing one picks "
            "Custom."))

        self.aspect_check = QCheckBox("Keep aspect ratio")
        self.aspect_check.setToolTip(
            "Keep the original proportions when resizing")
        self.aspect_check.toggled.connect(self._on_control_changed)
        form.addWidget(self.aspect_check)
        form.addWidget(self._explain(
            "aspect_check",
            "Keeps the picture's shape, so nothing stretches."))

        form.addWidget(self._section("Palette"))
        self.palette_combo = QComboBox()
        self.palette_combo.setToolTip(
            "Colors the sprite may use")
        self._fill_palette_combo(self.palette_combo)
        self.palette_combo.currentIndexChanged.connect(
            self._on_control_changed)
        form.addWidget(self.palette_combo)
        form.addWidget(self._explain(
            "palette_combo",
            "The colors the sprite is made from."))

        palette_btn_row = QHBoxLayout()
        self.palettes_btn = QPushButton("Palettes…")
        self.palettes_btn.setToolTip(
            "Create, edit, import, and export palettes")
        self.palettes_btn.clicked.connect(
            lambda: self._open_palette_editor())
        palette_btn_row.addWidget(self.palettes_btn)
        self.extract_palette_btn = QPushButton("Extract palette…")
        self.extract_palette_btn.setToolTip(
            "Extract a palette from the loaded image and save it")
        self.extract_palette_btn.setEnabled(False)
        self.extract_palette_btn.clicked.connect(
            self._extract_palette)
        palette_btn_row.addWidget(self.extract_palette_btn)
        palette_btn_row.addStretch(1)
        form.addLayout(palette_btn_row)

        colors_row = QHBoxLayout()
        colors_row.addWidget(QLabel("Max colors"))
        self.colors_spin = QSpinBox()
        self.colors_spin.setRange(MIN_COLORS, MAX_COLORS)
        self.colors_spin.setValue(16)
        self.colors_spin.setToolTip(
            "Most colors the sprite may use")
        self.colors_spin.valueChanged.connect(self._on_control_changed)
        colors_row.addWidget(self.colors_spin)
        form.addLayout(colors_row)
        form.addWidget(self._explain(
            "colors_spin",
            "The most colors the sprite may use. Fewer is "
            "chunkier."))

        self.dither_check = QCheckBox("Dithering")
        self.dither_check.setToolTip("Ordered (Bayer) dithering")
        self.dither_check.setChecked(True)
        self.dither_check.toggled.connect(self._on_control_changed)
        form.addWidget(self.dither_check)
        form.addWidget(self._explain(
            "dither_check",
            "Fakes shades between colors with a checker "
            "pattern."))

        self._build_preset_section(form, "image")
        # Task 91: hovering a palette or a preset previews its
        # effect without applying it — the variants are
        # rendered ahead of time (see _schedule_hover_prefetch),
        # so the hover itself never renders.
        self._install_hover_preview(self.palette_combo, "image",
                                    "palette")
        self._install_hover_preview(self.preset_combo, "image",
                                    "preset")

        form.addWidget(self._section("Zoom"))
        zoom_row = QHBoxLayout()
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(1, 16)
        self.zoom_slider.setValue(8)
        self.zoom_slider.setToolTip("Zoom the pixel preview")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self.zoom_slider)
        self.zoom_label = QLabel("8x")
        self.zoom_label.setFixedWidth(32)
        zoom_row.addWidget(self.zoom_label)
        self.zoom_fit_btn = QPushButton("Fit")
        self.zoom_fit_btn.setToolTip("Zoom to fit the preview pane")
        self.zoom_fit_btn.clicked.connect(self._on_fit_image_zoom)
        zoom_row.addWidget(self.zoom_fit_btn)
        form.addLayout(zoom_row)
        form.addWidget(self._explain(
            "zoom_slider",
            "How big the preview looks. It does not change "
            "the sprite."))

        # Layer stack (task 35): the list shows the stack top
        # first — row 0 is the top layer. The check on each row
        # is the visibility toggle; double-click a name to
        # rename it. The active layer is the one the editor
        # paints on; on load it is the base, the only layer.
        form.addWidget(self._section("Layers"))
        self.layer_list = QListWidget()
        self.layer_list.setToolTip(
            "Layers, top first — click to make one active, "
            "double-click a name to rename it")
        self.layer_list.setMaximumHeight(96)
        self.layer_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked)
        self.layer_list.currentRowChanged.connect(
            self._on_layer_selected)
        self.layer_list.itemClicked.connect(
            self._on_layer_item_clicked)
        self.layer_list.itemChanged.connect(
            self._on_layer_item_changed)
        form.addWidget(self.layer_list)
        form.addWidget(self._explain(
            "layer_list",
            "Layers stack top first. Click one to paint on it."))
        # Two rows of two: four buttons in one row overflow
        # the 268 px column and push Move down off the edge.
        layer_row = QHBoxLayout()
        self.layer_add_btn = QPushButton("Add layer")
        self.layer_add_btn.setToolTip(
            "Add a transparent layer on top")
        self.layer_add_btn.clicked.connect(self._on_layer_add)
        layer_row.addWidget(self.layer_add_btn)
        self.layer_delete_btn = QPushButton("Delete layer")
        self.layer_delete_btn.setToolTip(
            "Delete the active layer")
        self.layer_delete_btn.clicked.connect(
            self._on_layer_delete)
        layer_row.addWidget(self.layer_delete_btn)
        form.addLayout(layer_row)
        layer_move_row = QHBoxLayout()
        self.layer_up_btn = QPushButton("Move up")
        self.layer_up_btn.setToolTip(
            "Move the active layer one step up the stack")
        self.layer_up_btn.clicked.connect(
            lambda: self._on_layer_move(1))
        layer_move_row.addWidget(self.layer_up_btn)
        self.layer_down_btn = QPushButton("Move down")
        self.layer_down_btn.setToolTip(
            "Move the active layer one step down the stack")
        self.layer_down_btn.clicked.connect(
            lambda: self._on_layer_move(-1))
        layer_move_row.addWidget(self.layer_down_btn)
        form.addLayout(layer_move_row)

        form.addStretch(1)
        self.info_label = QLabel("No image loaded")
        self.info_label.setWordWrap(True)
        form.addWidget(self.info_label)

        self.export_btn = QPushButton("Export PNG")
        self.export_btn.setObjectName("export")
        self.export_btn.setToolTip(
            "Export the pixel sprite as a transparent PNG")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)
        form.addWidget(self.export_btn)

        self.save_library_btn = QPushButton("Save to library")
        self.save_library_btn.setToolTip(
            "Save the pixel sprite to the library")
        self.save_library_btn.setEnabled(False)
        self.save_library_btn.clicked.connect(
            self._on_save_image_to_library)
        form.addWidget(self.save_library_btn)

        main_layout.addWidget(
            self._scrollable_column(controls))

        # ------------------------------------------------ previews
        previews = QSplitter(Qt.Orientation.Horizontal)

        before_pane = QWidget()
        before_layout = QVBoxLayout(before_pane)
        before_layout.setContentsMargins(0, 0, 0, 0)
        before_layout.addWidget(self._section("Original"))
        self.before_label = QLabel("Drop an image here or press "
                                   "Import image")
        self.before_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.before_label.setWordWrap(True)
        before_scroll = QScrollArea()
        before_scroll.setWidgetResizable(True)
        before_scroll.setWidget(self.before_label)
        before_layout.addWidget(before_scroll)
        previews.addWidget(before_pane)

        after_pane = QWidget()
        after_layout = QVBoxLayout(after_pane)
        after_layout.setContentsMargins(0, 0, 0, 0)
        after_layout.addWidget(self._section("Pixel art"))
        # Hold to compare (task 91): the button IS the hint —
        # its text says what holding it does, so the gesture
        # can be found instead of guessed. While held, the
        # canvas below shows the original in place of the
        # result; letting go brings the result back. Nothing
        # is selected or applied by holding.
        compare_row = QHBoxLayout()
        self.compare_btn = QPushButton("Hold to see the original")
        self.compare_btn.setToolTip(
            "Hold this button to see the original image in "
            "place of the pixel art. Let go to see the result "
            "again")
        self.compare_btn.setEnabled(False)
        self.compare_btn.pressed.connect(
            lambda: self._on_compare_hold_start("image"))
        self.compare_btn.released.connect(
            self._on_compare_hold_end)
        compare_row.addWidget(self.compare_btn)
        compare_row.addStretch(1)
        after_layout.addLayout(compare_row)
        # Pixel editor toolbar (task 33): the tools sit above
        # the canvas they work on. Nothing is selected by
        # default — the canvas does nothing on click until a
        # tool is chosen, and the pencil, fill, line, and
        # rectangle need a color picked first.
        tool_row = QHBoxLayout()
        self.editor_tool_group = QButtonGroup(self)
        self.editor_tool_group.setExclusive(True)
        self.editor_tool_buttons = {}
        # The tools come from EDITOR_TOOLS (gui_shared) — the
        # pie menu reads the same list, so the wheel and the
        # buttons can never disagree about the tools.
        for tool, label, tip in EDITOR_TOOLS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(
                lambda _c, t=tool: self._on_editor_tool(t))
            self.editor_tool_group.addButton(button)
            self.editor_tool_buttons[tool] = button
            tool_row.addWidget(button)
        tool_row.addStretch(1)
        after_layout.addLayout(tool_row)
        # The pie menu hint (task 93): one plain line under
        # the tool row, so the right-click gesture can be
        # found. It reuses the explanation style but is NOT
        # registered in field_explanations — that registry
        # pins an exact key set (the v0.85 strip hint does
        # the same).
        self.pie_hint = QLabel(
            "Right-click the canvas, or press Q, for the tool "
            "wheel. Number keys 1 to 7 pick a tool.")
        self.pie_hint.setObjectName("explanation")
        self.pie_hint.setWordWrap(True)
        after_layout.addWidget(self.pie_hint)
        # The color strip: one swatch per color the result
        # actually uses, in first-appearance order — the editor
        # draws with the result's own palette, not a color
        # wheel. Rebuilt by _refresh_editor_colors on every new
        # result; a horizontal scroll when the result uses many
        # colors.
        self.editor_color_scroll = QScrollArea()
        self.editor_color_scroll.setWidgetResizable(True)
        self.editor_color_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.editor_color_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor_color_scroll.setFixedHeight(32)
        self.editor_color_scroll.setToolTip(
            "Colors this sprite uses — click one to paint with it")
        color_row_widget = QWidget()
        self.editor_color_layout = QHBoxLayout(color_row_widget)
        self.editor_color_layout.setContentsMargins(2, 2, 2, 2)
        self.editor_color_layout.setSpacing(4)
        self.editor_color_layout.addStretch(1)
        self.editor_color_scroll.setWidget(color_row_widget)
        self.editor_color_group = QButtonGroup(self)
        self.editor_color_group.setExclusive(True)
        after_layout.addWidget(self.editor_color_scroll)
        self.after_label = _PixelCanvas(self)
        # Task 93: the canvas names the wheel gesture too, so a
        # hover over the canvas teaches it alongside the hint.
        self.after_label.setToolTip(
            "Right-click for the tool wheel, or press Q")
        self.after_scroll = QScrollArea()
        self.after_scroll.setWidgetResizable(True)
        self.after_scroll.setWidget(self.after_label)
        after_layout.addWidget(self.after_scroll)
        previews.addWidget(after_pane)
        self._update_editor_enabled()

        previews.setSizes([500, 500])
        main_layout.addWidget(previews, 1)
        return root


    # ---------------------------------------------------------- input

    def load_image(self, path):
        """Load an image from disk and refresh the previews.

        Returns True on success. On failure shows a message box and
        returns False — the window stays up either way.
        """
        # A new load replaces the result, which throws the pixel
        # edits away — ask first (task 33). Declining keeps the
        # current image and result.
        if self._result_edited and not self._confirm_discard_edits(
                "Load a new image?",
                "Load a new image and lose your edits?"):
            return False
        try:
            image = gui_module.load_image(path)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import image", str(exc))
            return False
        self.source_image = image
        self.source_path = str(path)
        self._clear_generated_seed()
        self.update_preview()
        # The preview zoom fits the pane on load (task 22) — the
        # old fixed 8x left a 16x16 sprite tiny and a 128x128 one
        # overflowing. A later control change does not re-fit.
        self._on_fit_image_zoom()
        self.statusBar().showMessage("Loaded %s (%d x %d)"
                                     % (Path(path).name, image.width(),
                                        image.height()))
        self._record_session(path, "image")
        return True


    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import image", "", supported_filter())
        if path:
            self.load_image(path)


    # -------------------------------------------- background removal

    def _bgremove_running(self):
        return (self.bgremove_worker is not None
                and self.bgremove_worker.isRunning())


    def _on_remove_background(self):
        """Remove the background from the loaded image (task 41).

        Missing packages or a missing model say plainly what to
        install (the Prompt tab's missing-engine pattern), never
        a traceback. The check runs against the interpreter the
        removal runs under, not the GUI's own imports — with
        the PIXELFORGE_BGREMOVE_PYTHON override set, the GUI
        venv may not have rembg while the removal python does.
        A run in progress ignores a second press. The result
        replaces the loaded image, which throws the pixel edits
        away — the same discard confirm as a new load guards
        that.
        """
        if self._bgremove_running() or self.source_image is None:
            return
        if not self._confirm_discard_edits(
                "Remove the background?",
                "Remove the background and lose your edits?"):
            return
        try:
            bgremove.require_child_ready()
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not remove background",
                        str(exc))
            return
        self.bgremove_worker = BgRemoveWorker(self.source_image,
                                              parent=self)
        self.bgremove_worker.done.connect(self._on_bgremove_done)
        self.bgremove_worker.failed.connect(
            self._on_bgremove_failed)
        self.bgremove_worker.cancelled.connect(
            self._on_bgremove_cancelled)
        self.bgremove_btn.setEnabled(False)
        self.bgremove_cancel_btn.setEnabled(True)
        self.import_btn.setEnabled(False)
        self.statusBar().showMessage("Removing background…")
        self._activity_start("bgremove", "Removing background…",
                             cancel=self._on_bgremove_cancel)
        self.bgremove_worker.start()


    def _on_bgremove_cancel(self):
        if self.bgremove_worker is not None:
            self.bgremove_worker.cancel()
            self.bgremove_cancel_btn.setEnabled(False)
            self.statusBar().showMessage(
                "Cancelling — stopping the removal…")


    def _bgremove_finished(self):
        """Buttons back after a run ends, whatever the outcome."""
        self._activity_finish("bgremove")
        self.bgremove_worker = None
        self.import_btn.setEnabled(True)
        self.bgremove_cancel_btn.setEnabled(False)
        if self.source_image is not None:
            self.bgremove_btn.setEnabled(True)


    def _on_bgremove_done(self, result):
        # Only the current worker counts (the Prompt tab's
        # sender guard), and only when the image it ran on is
        # still the loaded one — a new import mid-run (a drop
        # lands even with the button disabled) must not be
        # overwritten by the old image's result.
        if self.sender() is not self.bgremove_worker:
            return
        self._bgremove_finished()
        if result["source"] is not self.source_image:
            self.statusBar().showMessage(
                "Background removed for an image that is no "
                "longer loaded — the current image is unchanged")
            return
        self.source_image = result["image"]
        self._clear_generated_seed()
        self.update_preview()
        self.statusBar().showMessage("Background removed")
        # The finish moment (task 94), on the new result.
        self._motion.pulse(self.after_label)


    def _on_bgremove_failed(self, message):
        if self.sender() is not self.bgremove_worker:
            return
        self._bgremove_finished()
        gui_module.warn_dialog(self, "Could not remove background", message)
        self.statusBar().showMessage("Background removal failed")


    def _on_bgremove_cancelled(self):
        if self.sender() is not self.bgremove_worker:
            return
        self._bgremove_finished()
        self.statusBar().showMessage(
            "Cancelled — the image is unchanged")


    def _stop_bgremove_worker(self):
        if self.bgremove_worker is not None:
            if self.bgremove_worker.isRunning():
                self.bgremove_worker.cancel()
                self.bgremove_worker.wait(10000)
            self.bgremove_worker = None


    # -------------------------------------------- pixel-grid snap

    def _gridsnap_running(self):
        return (self.gridsnap_worker is not None
                and self.gridsnap_worker.isRunning())


    def _on_snap_to_grid(self):
        """Snap the loaded image to its pixel grid (task 42).

        Detection alone takes seconds on a large image, so the
        work runs in a worker — the window stays responsive and
        the snap can be cancelled. A run in progress ignores a
        second press. The result replaces the loaded image,
        which throws the pixel edits away — the same discard
        confirm as a new load guards that. A clean sprite and
        a gridless image are not failures: the worker says so
        and the image is left alone.
        """
        if self._gridsnap_running() or self.source_image is None:
            return
        if not self._confirm_discard_edits(
                "Snap to the pixel grid?",
                "Snap to the pixel grid and lose your edits?"):
            return
        self.gridsnap_worker = GridSnapWorker(
            self.source_image,
            self.palette_combo.currentData(),
            self.colors_spin.value(),
            self.palette_store.user_palettes(),
            parent=self)
        self.gridsnap_worker.done.connect(self._on_gridsnap_done)
        self.gridsnap_worker.failed.connect(
            self._on_gridsnap_failed)
        self.gridsnap_worker.cancelled.connect(
            self._on_gridsnap_cancelled)
        self.gridsnap_worker.no_grid.connect(
            self._on_gridsnap_no_grid)
        self.gridsnap_worker.already_clean.connect(
            self._on_gridsnap_already_clean)
        self.gridsnap_btn.setEnabled(False)
        self.gridsnap_cancel_btn.setEnabled(True)
        self.import_btn.setEnabled(False)
        self.statusBar().showMessage("Finding the pixel grid…")
        self._activity_start("gridsnap", "Finding the pixel grid…",
                             cancel=self._on_gridsnap_cancel)
        self.gridsnap_worker.start()


    def _on_gridsnap_cancel(self):
        if self.gridsnap_worker is not None:
            self.gridsnap_worker.cancel()
            self.gridsnap_cancel_btn.setEnabled(False)
            self.statusBar().showMessage(
                "Cancelling, stopping the snap…")


    def _gridsnap_finished(self):
        """Buttons back after a run ends, whatever the outcome."""
        self._activity_finish("gridsnap")
        self.gridsnap_worker = None
        self.import_btn.setEnabled(True)
        self.gridsnap_cancel_btn.setEnabled(False)
        if self.source_image is not None:
            self.gridsnap_btn.setEnabled(True)


    def _on_gridsnap_done(self, result):
        # Only the current worker counts (the Prompt tab's
        # sender guard), and only when the image it ran on is
        # still the loaded one — a new import mid-run (a drop
        # lands even with the button disabled) must not be
        # overwritten by the old image's result.
        if self.sender() is not self.gridsnap_worker:
            return
        self._gridsnap_finished()
        if result["source"] is not self.source_image:
            self.statusBar().showMessage(
                "Snapped to a grid for an image that is no "
                "longer loaded, so the current image is "
                "unchanged")
            return
        self.source_image = result["image"]
        self._clear_generated_seed()
        self.update_preview()
        self.statusBar().showMessage(
            "Snapped to a %g px grid (%d x %d sprite)"
            % (result["grid"][0], self.source_image.width(),
               self.source_image.height()))
        self._motion.pulse(self.after_label)


    def _on_gridsnap_failed(self, message):
        if self.sender() is not self.gridsnap_worker:
            return
        self._gridsnap_finished()
        gui_module.warn_dialog(self, "Could not snap to the pixel grid",
                    message)
        self.statusBar().showMessage("Pixel grid snap failed")


    def _on_gridsnap_cancelled(self):
        if self.sender() is not self.gridsnap_worker:
            return
        self._gridsnap_finished()
        self.statusBar().showMessage(
            "Cancelled, the image is unchanged")


    def _on_gridsnap_no_grid(self):
        if self.sender() is not self.gridsnap_worker:
            return
        self._gridsnap_finished()
        self.statusBar().showMessage(
            "Could not find a pixel grid in this image, so the "
            "image is unchanged")


    def _on_gridsnap_already_clean(self):
        if self.sender() is not self.gridsnap_worker:
            return
        self._gridsnap_finished()
        self.statusBar().showMessage(
            "This is already a clean pixel sprite, so the "
            "image is unchanged")


    def _stop_gridsnap_worker(self):
        if self.gridsnap_worker is not None:
            if self.gridsnap_worker.isRunning():
                self.gridsnap_worker.cancel()
                self.gridsnap_worker.wait(10000)
            self.gridsnap_worker = None


    def _normalmap_running(self):
        return (self.normalmap_worker is not None
                and self.normalmap_worker.isRunning())


    def _on_generate_normal_map(self):
        """Generate a normal map from the current result (task 43).

        A 512 x 512 image takes about a second, so the work runs
        in a worker — the window stays responsive and the run
        can be cancelled. A run in progress ignores a second
        press. Generation never touches the result, so no
        discard confirm is needed. When the map lands it is
        shown in place of the sprite; the toggle switches back.
        """
        if self._normalmap_running() or self.result_image is None:
            return
        self.normalmap_worker = NormalMapWorker(
            self.result_image,
            self.normalmap_strength_spin.value(),
            parent=self)
        self.normalmap_worker.done.connect(self._on_normalmap_done)
        self.normalmap_worker.failed.connect(
            self._on_normalmap_failed)
        self.normalmap_worker.cancelled.connect(
            self._on_normalmap_cancelled)
        self.normalmap_btn.setEnabled(False)
        self.normalmap_cancel_btn.setEnabled(True)
        self.normalmap_strength_spin.setEnabled(False)
        self.statusBar().showMessage("Generating the normal map…")
        self._activity_start("normalmap",
                             "Generating the normal map…",
                             cancel=self._on_normalmap_cancel)
        self.normalmap_worker.start()


    def _on_normalmap_cancel(self):
        if self.normalmap_worker is not None:
            self.normalmap_worker.cancel()
            self.normalmap_cancel_btn.setEnabled(False)
            self.statusBar().showMessage(
                "Cancelling, stopping the normal map…")


    def _normalmap_finished(self):
        """Buttons back after a run ends, whatever the outcome."""
        self._activity_finish("normalmap")
        self.normalmap_worker = None
        self.normalmap_cancel_btn.setEnabled(False)
        self.normalmap_strength_spin.setEnabled(True)
        if self.result_image is not None:
            self.normalmap_btn.setEnabled(True)


    def _on_normalmap_done(self, result):
        # Only the current worker counts, and only when the
        # result it ran on is still the current one — a control
        # change or an edit mid-run re-pixelizes, and the old
        # result's map must not be kept. Compared by VALUE, not
        # identity: _refresh_result rebuilds the composite
        # object on every call, so identity would call every
        # map stale.
        if self.sender() is not self.normalmap_worker:
            return
        self._normalmap_finished()
        if result["source"] != self.result_image:
            self.statusBar().showMessage(
                "Normal map generated for a result that changed, "
                "so it was not kept")
            return
        self.normal_map_image = result["image"]
        self.normalmap_show_btn.setEnabled(True)
        self.normalmap_export_btn.setEnabled(True)
        self._showing_normal_map = True
        self.normalmap_show_btn.setChecked(True)
        self.normalmap_show_btn.setText("Show sprite")
        self._refresh_pixmaps()
        self._update_editor_enabled()
        self.statusBar().showMessage(
            "Normal map generated, %d x %d"
            % (self.normal_map_image.width(),
               self.normal_map_image.height()))
        self._motion.pulse(self.after_label)


    def _on_normalmap_failed(self, message):
        if self.sender() is not self.normalmap_worker:
            return
        self._normalmap_finished()
        gui_module.warn_dialog(self, "Could not generate the normal map",
                    message)
        self.statusBar().showMessage("Normal map failed")


    def _on_normalmap_cancelled(self):
        if self.sender() is not self.normalmap_worker:
            return
        self._normalmap_finished()
        self.statusBar().showMessage(
            "Cancelled, the normal map is unchanged")


    def _stop_normalmap_worker(self):
        if self.normalmap_worker is not None:
            if self.normalmap_worker.isRunning():
                self.normalmap_worker.cancel()
                self.normalmap_worker.wait(10000)
            self.normalmap_worker = None


    def _on_show_normal_map(self, checked):
        """Show the generated normal map in the pixel preview
        instead of the sprite, or go back to the sprite. The
        editor tools go quiet while the map is showing: they
        paint on the sprite, not on the map."""
        self._showing_normal_map = bool(checked)
        self.normalmap_show_btn.setText(
            "Show sprite" if checked else "Show normal map")
        self._refresh_pixmaps()
        self._update_editor_enabled()
        self.statusBar().showMessage(
            "Showing the normal map" if checked
            else "Showing the sprite")


    def _classify_drop(self, path):
        """'video', 'code', 'image', or None for a dropped path."""
        suffix = Path(path).suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in CODE_EXTENSIONS:
            return "code"
        if suffix in image_extensions():
            return "image"
        return None


    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        # Accept only when at least one file is something Pixel
        # Forge can import — a .zip used to be accepted and then
        # warned about after the drop (review B15). No supported
        # file, no drop cursor, no warning box.
        if paths and any(self._classify_drop(p) for p in paths):
            event.acceptProposedAction()


    def dropEvent(self, event):
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        videos = [p for p in paths
                  if self._classify_drop(p) == "video"]
        code = [p for p in paths
                if self._classify_drop(p) == "code"]
        images = [p for p in paths
                  if self._classify_drop(p) == "image"]
        unsupported = [p for p in paths
                       if self._classify_drop(p) is None]
        # A multi-file drop used to import only the first file and
        # silently ignore the rest (review B15); task 28 imports
        # every supported file. Code files all go to the Code
        # tab's list. The Image and Video tabs hold one thing at
        # a time, so images load in turn and videos extract in
        # turn — each one named in the summary, the last one
        # showing when the drop is done (a newer video run
        # supersedes the one before it, per the v0.9 stale
        # guard). The last tab switched to stays visible.
        imported = []
        skipped = []
        video_started = False
        # A single image dropped on the Tileset tab loads as base
        # tile A (task 37) — the tab is where the image belongs.
        # A multi-image drop keeps the normal path (the images
        # load into the Image tab in turn).
        if (len(images) == 1 and not videos and not code
                and self.tabs.currentWidget() is self.tileset_tab):
            if self.load_tileset_base(images[0]):
                imported.append("%s (base tile)"
                                % Path(images[0]).name)
            images = []
        if images:
            self.tabs.setCurrentIndex(0)
            for path in images:
                if self.load_image(path):
                    imported.append("%s (image)"
                                    % Path(path).name)
        if videos:
            self.tabs.setCurrentIndex(1)
            for path in videos:
                if self.load_video(path):
                    imported.append("%s (video)"
                                    % Path(path).name)
                    video_started = True
        if code:
            self.tabs.setCurrentIndex(3)
            if self.load_code(code):
                imported.append(plural(len(code), "code file"))
        for path in unsupported:
            skipped.append("%s (unsupported file)" % Path(path).name)
        if not imported:
            # Nothing landed. A failed load already showed its own
            # warning; only the unsupported files need one here.
            if skipped:
                gui_module.warn_dialog(
                    self, "Nothing to import",
                    "nothing to import: %s" % "; ".join(skipped))
            return
        message = "Imported " + "; ".join(imported)
        if skipped:
            message += " — skipped: " + "; ".join(skipped)
        self.statusBar().showMessage(message)
        if video_started:
            # The dropped video lands on a worker thread, and its
            # completion message overwrites this summary after
            # this method returns — keep it so the completion
            # handler can put it back (review B15).
            self._drop_summary = message


    # -------------------------------------------------------- controls

    def _on_preset(self):
        self._apply_size_preset("image")


    def _on_custom_size(self):
        self._reset_size_to_custom("image")


    def _on_control_changed(self):
        self._on_tab_control_changed("image")


    def _confirm_discard_edits(self, title, message):
        """Ask whether to throw the pixel edits away. Returns
        True when there are no edits, or the user said Yes.
        Default No — declining keeps the edits and the result."""
        if not self._result_edited:
            return True
        answer = QMessageBox.question(
            self, title, message,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes


    def _restore_image_controls(self):
        """Put the Image tab's controls back to the undo
        snapshot — the values they had before the change the
        user just declined. Signals blocked throughout, so the
        restore itself triggers no control change, no
        re-pixelize, and no undo entry. The size combo follows
        width and height, as it does everywhere else."""
        if self._tab_settings is None:
            return
        state = self._tab_settings["image"]
        controls = (self.size_combo, self.width_spin,
                    self.height_spin, self.aspect_check,
                    self.palette_combo, self.colors_spin,
                    self.dither_check)
        for widget in controls:
            widget.blockSignals(True)
        try:
            self.width_spin.setValue(state["width"])
            self.height_spin.setValue(state["height"])
            self.aspect_check.setChecked(state["keep_aspect"])
            index = self.palette_combo.findData(state["palette"])
            if index >= 0:
                self.palette_combo.setCurrentIndex(index)
            self.colors_spin.setValue(state["max_colors"])
            self.dither_check.setChecked(state["dither"])
            width, height = state["width"], state["height"]
            if width == height and width in SIZE_PRESETS:
                index = self.size_combo.findData(width)
            else:
                index = self.size_combo.findData(None)
            if index >= 0:
                self.size_combo.setCurrentIndex(index)
        finally:
            for widget in controls:
                widget.blockSignals(False)


    def _on_zoom_changed(self, value):
        self._on_zoom_changed_for("image", value)


    def _fit_zoom(self, slider, scroll, image):
        """Set a zoom slider so the image fits its preview pane.

        The fit is the largest whole zoom that fits the scroll
        area's viewport, clamped to the slider's range — a 32x32
        sprite in a 400 px pane fits at 12x, a 512x512 one at 1x.
        Called on load only: a re-pixelize or a control change
        must not snap the user's chosen zoom back (task 22).
        """
        if image is None:
            return
        viewport = scroll.viewport()
        width = viewport.width()
        height = viewport.height()
        if width < 10 or height < 10:
            # No pane to fit to yet — the window has not been laid
            # out (an offscreen test, a hidden tab). The slider
            # keeps its value; Fit works once the pane has a size.
            return
        fit = min(width / image.width(), height / image.height())
        value = max(slider.minimum(),
                    min(slider.maximum(), int(fit)))
        slider.setValue(value)


    def _on_fit_image_zoom(self):
        """Fit the pixel preview to its pane (task 22)."""
        self._fit_zoom(self.zoom_slider, self.after_scroll,
                       self.result_image)


    def _on_fit_video_zoom(self):
        """Fit the frame preview to its pane (task 22)."""
        if not self.video_frames:
            return
        self._fit_zoom(self.video_zoom_slider,
                       self.video_preview_scroll,
                       self.video_frames[self.video_current])


    def _on_fit_prompt_zoom(self):
        """Fit the keeper's pixel preview to its pane (task 22)."""
        self._fit_zoom(self.prompt_zoom_slider,
                       self.prompt_pixel_scroll,
                       self.prompt_result_image)


    def _on_fit_code_zoom(self):
        """Fit the sprite preview to its pane (task 22)."""
        self._fit_zoom(self.code_zoom_slider,
                       self.code_preview_scroll,
                       self.code_image)


    # -------------------------------------------------------- preview

    def update_preview(self):
        """Re-run the pixelizer with the current controls."""
        if self.source_image is None:
            return
        # Extract works from the source image, not the result —
        # it is available whenever an image is loaded, even if
        # the pixelize below fails. Background removal too.
        self.extract_palette_btn.setEnabled(True)
        if not self._bgremove_running():
            self.bgremove_btn.setEnabled(True)
        if not self._gridsnap_running():
            self.gridsnap_btn.setEnabled(True)
        try:
            result = gui_module.pixelize_image(
                self.source_image,
                width=self.width_spin.value(),
                height=self.height_spin.value(),
                keep_aspect=self.aspect_check.isChecked(),
                palette=self.palette_combo.currentData(),
                max_colors=self.colors_spin.value(),
                dither=self.dither_check.isChecked(),
                palettes=self.palette_store.user_palettes())
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))
            return
        # A fresh pixelize replaces the whole stack (task 35)
        # with one base layer: this result is the new original,
        # no edits on it, no drag in progress, and the color
        # strip shows its colors. The guard at every entry
        # point asks before this runs when the stack carries
        # edits.
        self._layers = [Layer("Background", result, True)]
        self._active_layer = self._layers[0]
        self._result_original = result.copy()
        self._result_edited = False
        self._editor_drag = None
        self._refresh_result()
        self._refresh_layer_list()
        self._update_editor_enabled()
        self.export_btn.setEnabled(True)
        self.save_library_btn.setEnabled(True)
        self.compare_btn.setEnabled(True)
        # Task 91: a fresh pixelize means a fresh source or
        # fresh settings, so every cached hover variant is
        # stale — drop them and queue the new set to render at
        # idle.
        self._invalidate_hover_cache("image")
        self._schedule_hover_prefetch("image")
        if not self._normalmap_running():
            self.normalmap_btn.setEnabled(True)


    def _refresh_info_label(self):
        """The Image tab's info line: size and color count, plus
        "(edited)" while the result carries edits."""
        result = self.result_image
        if result is None:
            self.info_label.setText("No image loaded")
            return
        text = "%d x %d, %d colors" % (
            result.width(), result.height(),
            count_opaque_colors(result))
        if self._result_edited:
            text += " (edited)"
        self.info_label.setText(text)


    def _refresh_pixmaps(self):
        # The original always fits its pane whole — zoom is for
        # inspecting pixels in the result, not for cropping the
        # original down to one corner of sky.
        source = self.source_image
        fit = ORIGINAL_FIT / max(source.width(), source.height())
        # Task 60: the Original pane does not change on a zoom
        # drag (neither the source nor its fit moves), so a
        # cached pixmap is reused until the source or the fit
        # changes. The result pane below still rebuilds — its
        # scale is the zoom.
        cached = self._before_pixmap_cache
        if (cached is not None and cached[0] is source
                and cached[1] == fit):
            before = cached[2]
        else:
            before = gui_module.checkerboard_pixmap(source, fit, smooth=True)
            self._before_pixmap_cache = (source, fit, before)
        self.before_label.setPixmap(before)
        # Task 91: while the compare is held, the result pane
        # shows the original at the result's size — every
        # refresh (a zoom drag, a control change) keeps the
        # hold showing, and the release restores through here.
        if self._compare_held == "image":
            compare = self._compare_pixmap_for("image")
            if compare is not None:
                self.after_label.setPixmap(compare)
                return
        if self._showing_normal_map and \
                self.normal_map_image is not None:
            self._show_after_image(self.normal_map_image)
        elif self.result_image is not None:
            self._show_after_image(self.result_image)


    def _show_after_image(self, image):
        """Render one image into the pixel canvas at the current
        zoom — the live result, or a drag preview of it."""
        after = gui_module.checkerboard_pixmap(image, self.zoom_slider.value(),
                                    smooth=False)
        self.after_label.setPixmap(after)


    # --------------------------------------------------------- export

    def export_image(self, path):
        """Write the current result to path as PNG. Returns True on
        success; on failure shows a message box and returns False."""
        if self.result_image is None:
            return False
        if not str(path).lower().endswith(".png"):
            path = "%s.png" % path
        if not self.result_image.save(str(path), "PNG"):
            gui_module.warn_dialog(self, "Could not export PNG",
                                "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
        # The finish moment (task 94): the exported result
        # pops once. Decoration only: the file is written.
        self._motion.pulse(self.after_label)
        return True


    # ------------------------------------------- export filename defaults
    # Task 22: every export dialog defaults to a filename next to
    # the source file; when there is no source file, it defaults
    # to the last-used export folder. The folder is remembered in
    # a small JSON file in the library folder, read and written
    # fail-soft — a corrupt or unwritable file just means the
    # dialog falls back to the current directory.

    def _export_folders_path(self):
        """Where the last-used export folder is remembered."""
        return Path(self.library.directory) / "export-folders.json"


    def _last_export_dir(self):
        """The last folder an export wrote to, or None."""
        try:
            data = json.loads(
                self._export_folders_path().read_text(
                    encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        folder = data.get("last_export_dir")
        if not folder or not Path(folder).is_dir():
            return None
        return folder


    def _remember_export_dir(self, folder):
        """Record the folder an export just wrote to."""
        try:
            record = self._export_folders_path()
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(
                json.dumps({"last_export_dir": str(folder)}),
                encoding="utf-8")
        except OSError:
            pass


    def _export_default(self, filename, source_path=None):
        """The default path for an export dialog.

        Next to the source file when there is one; else the
        last-used export folder; else just the filename (the
        dialog opens in the current directory).
        """
        if source_path is not None:
            return str(Path(source_path).parent / filename)
        last = self._last_export_dir()
        if last is not None:
            return str(Path(last) / filename)
        return filename


    def _on_export(self):
        if self.result_image is None:
            return
        if self.source_path:
            src = Path(self.source_path)
            default = self._export_default(
                src.stem + "-pixel.png", self.source_path)
        else:
            default = self._export_default("sprite-pixel.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", default, "PNG image (*.png)")
        if path and self.export_image(path):
            self._remember_export_dir(Path(path).parent)


    def export_normal_map(self, path):
        """Write the generated normal map to path as PNG.
        Returns True on success; on failure shows a message box
        and returns False. Mirrors export_image."""
        if self.normal_map_image is None:
            return False
        if not str(path).lower().endswith(".png"):
            path = "%s.png" % path
        if not self.normal_map_image.save(str(path), "PNG"):
            gui_module.warn_dialog(self, "Could not export PNG",
                        "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
        self._motion.pulse(self.after_label)
        return True


    def _on_export_normal_map(self):
        if self.normal_map_image is None:
            return
        if self.source_path:
            src = Path(self.source_path)
            default = self._export_default(
                src.stem + "-normal.png", self.source_path)
        else:
            default = self._export_default("sprite-normal.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export normal map", default, "PNG image (*.png)")
        if path and self.export_normal_map(path):
            self._remember_export_dir(Path(path).parent)


    # --------------------------------------- procedural generation

    def _on_procedural_kind_changed(self):
        """Generate stays quiet until a kind is picked — the
        combo starts with nothing selected (the standing
        rule)."""
        self.procedural_generate_btn.setEnabled(
            self.procedural_kind_combo.currentData() is not None)


    def _on_procedural_random(self):
        """Pick a fresh seed at random (1 to 999999)."""
        self.procedural_seed_spin.setValue(random.randint(1, 999999))


    def _clear_generated_seed(self):
        """The loaded sprite no longer comes from a seed — a
        file was loaded, or another tool changed the source —
        so the seed label goes blank."""
        self._generated_kind = None
        self._generated_seed = None
        self.procedural_seed_label.setText("")


    def _procedural_what(self, kind, seed):
        """Plain words for what a generation made: the kind,
        plus the style or shape the seed picked."""
        if kind == "tree":
            return "%s tree" % procedural.tree_style_for_seed(seed)
        if kind == "item":
            return "a %s" % procedural.item_shape_for_seed(seed)
        return "a mirrored mask sprite"


    def _on_generate_procedural(self):
        """Make a sprite from the kind and seed and load it
        into the tab like an imported image (task 44).

        Generation takes milliseconds, so it runs right here,
        no worker. The sprite becomes the source image and the
        tab's size is set to match, so the result is the
        sprite itself. A new generation replaces the result,
        which throws the pixel edits away — the same discard
        confirm as a new load guards it.
        """
        kind = self.procedural_kind_combo.currentData()
        if kind is None:
            return
        if self._result_edited and not self._confirm_discard_edits(
                "Generate a new sprite?",
                "Generate a new sprite and lose your edits?"):
            return
        seed = self.procedural_seed_spin.value()
        size = self.procedural_size_spin.value()
        try:
            sprite = procedural.generate(kind, seed, size=size)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not generate the sprite",
                        str(exc))
            self.statusBar().showMessage("Generation failed")
            return
        self.source_image = sprite
        self.source_path = None
        # The tab's size follows the sprite, so the result is
        # the sprite itself, not a resample of it. Signals
        # blocked: one update_preview below does the refresh.
        self.width_spin.blockSignals(True)
        self.height_spin.blockSignals(True)
        self.width_spin.setValue(size)
        self.height_spin.setValue(size)
        self.width_spin.blockSignals(False)
        self.height_spin.blockSignals(False)
        index = self.size_combo.findData(size)
        if index < 0:
            index = self.size_combo.findData(None)
        self.size_combo.blockSignals(True)
        self.size_combo.setCurrentIndex(index)
        self.size_combo.blockSignals(False)
        self._generated_kind = kind
        self._generated_seed = seed
        self.procedural_seed_label.setText(
            "Seed %d, %s. Type the seed in to make this "
            "sprite again." % (seed, procedural.KIND_LABELS[kind]))
        self.update_preview()
        self._on_fit_image_zoom()
        self.statusBar().showMessage(
            "Generated %s, seed %d (%d x %d)"
            % (self._procedural_what(kind, seed), seed,
               sprite.width(), sprite.height()))
        # The finish moment (task 94), on the new sprite.
        self._motion.pulse(self.after_label)


    # ------------------------------------------------ pixel editor
    # Task 33: fix one wrong pixel without round-tripping to
    # another program. The canvas is the result preview itself;
    # the tools draw with the colors the result actually uses.
    # Every pixel-changing edit is one undo step, and the
    # re-pixelize guard (see _on_control_changed, _on_library_
    # preset, load_image) asks before a control change, a preset,
    # or a new load throws the edits away. Export PNG and Save
    # to library read result_image, so they carry the edits.

    def _update_editor_enabled(self):
        """The editor tools work only on a loaded result — and
        so do the layer controls. While the normal map is
        showing (task 43) the tools stay off: they paint on the
        sprite, and painting blind behind the map would be
        nonsense."""
        enabled = self.result_image is not None \
            and not self._showing_normal_map
        for button in self.editor_tool_buttons.values():
            button.setEnabled(enabled)
        self.editor_color_scroll.setEnabled(enabled)
        self._update_layer_buttons()


    def _refresh_editor_colors(self):
        """Rebuild the color strip from the current result's
        distinct colors, keeping the picked color when the
        result still uses it. An edit never adds a color — the
        tools draw with strip colors only — but erasing the last
        pixel of a color, or undoing an edit, can change the
        set."""
        kept = self._editor_color
        for button in self._editor_swatch_by_color.values():
            self.editor_color_group.removeButton(button)
            self.editor_color_layout.removeWidget(button)
            button.deleteLater()
        self._editor_swatch_by_color = {}
        self._editor_color = None
        if self.result_image is None:
            return
        for color in distinct_colors(self.result_image):
            button = QPushButton()
            button.setCheckable(True)
            button.setFixedSize(20, 20)
            hex_color = "#%06x" % (color & 0xFFFFFF)
            button.setToolTip("Paint with %s" % hex_color)
            # The swatch's own stylesheet carries its color and
            # its checked border: a plain background-color rule
            # here would lose to the theme's :checked rule when
            # the swatch is picked.
            button.setStyleSheet(
                "QPushButton { background-color: %s; "
                "border: 1px solid #55555e; border-radius: 2px; "
                "padding: 0px; } "
                "QPushButton:checked { border: 2px solid #e07020; }"
                % hex_color)
            button.clicked.connect(
                lambda _c, c=color: self._on_editor_color(c))
            self.editor_color_group.addButton(button)
            self.editor_color_layout.insertWidget(
                len(self._editor_swatch_by_color), button)
            self._editor_swatch_by_color[color] = button
        if kept is not None and kept in self._editor_swatch_by_color:
            self._editor_color = kept
            self._editor_swatch_by_color[kept].setChecked(True)


    def _on_editor_tool(self, tool):
        """A tool button was clicked — it is the active tool
        now. The button group keeps exactly one checked; the
        canvas reads _editor_tool on every click. The text tool
        opens a dialog on click instead of painting, so its
        hint says so."""
        self._editor_tool = tool
        # The morph (task 94): the picked button pops once as
        # it becomes the tool in hand. Every pick path lands
        # here (the buttons, the pie wheel, the number keys
        # all press the real button), so one hook covers all
        # three. The tools have no panels to morph into: the
        # pop is the morph.
        self._motion.pulse(self.editor_tool_buttons[tool],
                           peak=1.07)
        if tool == "text":
            self.statusBar().showMessage(
                "Text: click where the text goes")
            return
        self.statusBar().showMessage(
            "%s — click or drag on the pixel preview"
            % tool.capitalize())


    # ------------------------------------------- pie menu (task 93)

    def _open_pie_menu(self, global_pos):
        """Open the tool wheel centered on a global point —
        a right-click on the canvas, or the Q key. One wheel
        at a time, and none at all while the editor is off
        (no result, or the normal map showing): the tool
        buttons' enabled state is exactly that condition."""
        if self._pie_menu is not None:
            return
        if not self.editor_tool_buttons["pencil"].isEnabled():
            return
        self._pie_menu = _PieMenu(self, self._theme,
                                  self._editor_tool)
        self._pie_menu.show_at(global_pos)

    def _on_pie_pick(self, tool):
        """The wheel picked a tool — press its button, the
        same path as clicking it, so the button checks and
        the status line follows."""
        button = self.editor_tool_buttons[tool]
        if button.isEnabled():
            button.click()

    def _on_pie_closed(self):
        """The wheel closed, picked or not — drop the
        reference so the next right-click opens a fresh one."""
        self._pie_menu = None


    def _on_editor_color(self, color):
        """A swatch was clicked — it is the drawing color now."""
        self._editor_color = color


    def _refresh_result(self):
        """Recomposite the layer stack into result_image and
        bring the preview, info line, and color strip up to
        date. Every layer change goes through here."""
        old_result = self.result_image
        self.result_image = composite_layers(self._layers)
        # The normal map belongs to the result it was made from
        # (task 43): any pixel change clears it. A refresh that
        # leaves the pixels alone (a rename, a visibility flip
        # on an empty layer) keeps the map.
        if old_result is not None and \
                old_result != self.result_image:
            self._clear_normal_map()
        self._recompute_edited()
        self._refresh_pixmaps()
        self._refresh_info_label()
        self._refresh_editor_colors()
        self._mark_autosave_dirty()


    def _clear_normal_map(self):
        """Drop the generated normal map and go back to showing
        the sprite. Called when the result's pixels change, so
        the map can never describe a result it was not made
        from. Does not refresh the preview — the caller does."""
        self.normal_map_image = None
        self._showing_normal_map = False
        self.normalmap_show_btn.blockSignals(True)
        self.normalmap_show_btn.setChecked(False)
        self.normalmap_show_btn.setText("Show normal map")
        self.normalmap_show_btn.blockSignals(False)
        self.normalmap_show_btn.setEnabled(False)
        self.normalmap_export_btn.setEnabled(False)


    def _recompute_edited(self):
        """The edited flag, derived from the stack: True when
        the stack is not one base layer holding the freshly
        pixelized result — an added, deleted, or reordered
        layer, or drawn pixels on any layer. A bare rename
        does not count, and hiding the only layer does not
        count either: the pixels are untouched. Undoing every
        edit clears the flag again, like v0.43."""
        if self._result_original is None or not self._layers:
            self._result_edited = False
            return
        self._result_edited = (
            len(self._layers) != 1
            or self._layers[0].image != self._result_original)


    def _set_layer_image(self, layer, image):
        """Install a snapshot as one layer's image (undo/redo
        of a pixel edit). A copy — the entry's snapshot must
        never be the live image, or the next edit would rewrite
        history. When the layer is no longer in the stack, the
        edit died with it and this does nothing."""
        if not any(entry is layer for entry in self._layers):
            return
        layer.image = image.copy()
        self._refresh_result()


    def _commit_edit(self, layer, before, text):
        """An edit landed on one layer: one undo step, and the
        preview, info line, and color strip brought up to date.
        Called only when the layer image actually changed."""
        self._push_undo(_PixelEditCommand(
            self, layer, before, layer.image.copy(), text))
        self._refresh_result()


    def _paint_pixel(self, pixel, color):
        """Set one pixel of the active layer (a pencil/eraser
        stroke point)."""
        if self._active_layer is not None:
            self._active_layer.image.setPixel(
                pixel[0], pixel[1], color)


    def _editor_pick(self, pixel):
        """The eyedropper: the color under the cursor becomes
        the drawing color. It reads the COMPOSITED result —
        what you see is what you pick — and the picked color
        paints on the active layer. A transparent pixel has no
        color to pick — the eraser paints transparency."""
        color = pick_color(self.result_image, pixel[0], pixel[1])
        if color is None or (color >> 24) == 0:
            self.statusBar().showMessage(
                "That pixel is transparent — the eraser paints "
                "transparency")
            return
        self._editor_color = color
        button = self._editor_swatch_by_color.get(color)
        if button is not None:
            button.setChecked(True)
        self.statusBar().showMessage(
            "Picked #%06x" % (color & 0xFFFFFF))


    def _editor_fill(self, pixel):
        """The fill tool: flood fill from the clicked pixel,
        on the ACTIVE layer's connected region. A click on a
        pixel already the picked color fills nothing and
        records no undo step."""
        if self._editor_color is None:
            self.statusBar().showMessage(
                "Pick a color from the strip first")
            return
        layer = self._active_layer
        if layer is None:
            return
        before = layer.image.copy()
        if flood_fill(layer.image, pixel[0], pixel[1],
                      self._editor_color) == 0:
            return
        self._commit_edit(layer, before, "Fill")


    def _open_stamp_dialog(self, x, y):
        """The Text tool's click: open the stamp dialog at the
        clicked pixel and stamp onto the active layer when it
        accepts — one undo step, like every other edit. The
        stamp starts in the picked color when one is picked,
        black otherwise; the dialog's own picker can change it.
        A stamp that lands nothing records no undo step."""
        if self.result_image is None or self._active_layer is None:
            return
        initial = self._editor_color
        if initial is None:
            initial = argb(0, 0, 0)
        dialog = gui_module.StampTextDialog(self, self.result_image, x, y,
                                 initial)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        layer = self._active_layer
        before = layer.image.copy()
        painted = stamp_image(layer.image, dialog.mask, dialog.x,
                              dialog.y, dialog.color)
        if painted == 0:
            return
        self._commit_edit(layer, before, "Stamp text")
        self.statusBar().showMessage(
            "Stamped %r at %d, %d" % (dialog.text_value, dialog.x,
                                      dialog.y))


    def _canvas_press(self, pixel):
        """A click on the pixel canvas, as an image pixel (or
        None when the click missed the image). No tool, no
        color, no active layer, or no result — nothing happens."""
        if (pixel is None or self.result_image is None
                or self._active_layer is None):
            return
        tool = self._editor_tool
        if tool is None:
            return
        if tool == "eyedropper":
            self._editor_pick(pixel)
            return
        if tool == "fill":
            self._editor_fill(pixel)
            return
        if tool == "text":
            self._open_stamp_dialog(pixel[0], pixel[1])
            return
        if tool in ("pencil", "line", "rectangle"):
            if self._editor_color is None:
                self.statusBar().showMessage(
                    "Pick a color from the strip first")
                return
        # Pencil, eraser, line, and rectangle all start a drag:
        # the active layer and its before-image are snapshotted
        # now, and the drag commits one undo step on release.
        # The layer is pinned in the drag — switching layers
        # mid-drag cannot redirect a stroke. Pencil and eraser
        # paint the start pixel at once, so a click without a
        # drag still paints one pixel; line and rectangle only
        # preview until release. The preview re-composites
        # after every stroke point.
        self._editor_drag = {
            "tool": tool, "start": pixel, "last": pixel,
            "layer": self._active_layer,
            "before": self._active_layer.image.copy()}
        if tool == "pencil":
            self._paint_pixel(pixel, self._editor_color)
            self.result_image = composite_layers(self._layers)
            self._refresh_pixmaps()
        elif tool == "eraser":
            self._paint_pixel(pixel, 0)
            self.result_image = composite_layers(self._layers)
            self._refresh_pixmaps()


    def _canvas_move(self, pixel):
        """A drag across the canvas. Pencil and eraser paint
        as they go (interpolated, so a fast drag leaves no
        gaps); line and rectangle redraw their preview."""
        drag = self._editor_drag
        if drag is None or pixel is None:
            return
        tool = drag["tool"]
        if tool in ("pencil", "eraser"):
            if pixel == drag["last"]:
                return
            color = self._editor_color if tool == "pencil" else 0
            draw_line(drag["layer"].image,
                      drag["last"][0], drag["last"][1],
                      pixel[0], pixel[1], color)
            drag["last"] = pixel
            self.result_image = composite_layers(self._layers)
            self._refresh_pixmaps()
        elif tool in ("line", "rectangle"):
            if pixel == drag.get("current"):
                return
            drag["current"] = pixel
            self._show_shape_preview(drag)


    def _show_shape_preview(self, drag):
        """The line or rectangle preview while dragging: the
        current pixmap with the shape drawn on top, cell by
        cell. The result itself is untouched until release."""
        base = self.after_label.pixmap()
        image = self.result_image
        if base is None or image is None:
            return
        start = drag["start"]
        end = drag.get("current", start)
        if drag["tool"] == "line":
            points = line_points(start[0], start[1],
                                 end[0], end[1])
        else:
            points = rect_points(start[0], start[1],
                                 end[0], end[1])
        preview = QPixmap(base)
        painter = QPainter(preview)
        color = QColor.fromRgba(self._editor_color)
        cell_w = base.width() / image.width()
        cell_h = base.height() / image.height()
        for x, y in points:
            if 0 <= x < image.width() and 0 <= y < image.height():
                left = int(x * cell_w)
                top = int(y * cell_h)
                right = int((x + 1) * cell_w)
                bottom = int((y + 1) * cell_h)
                painter.fillRect(left, top,
                                 max(1, right - left),
                                 max(1, bottom - top), color)
        painter.end()
        self.after_label.setPixmap(preview)


    def _canvas_release(self, pixel):
        """The drag ends: commit one undo step when the image
        changed. Line and rectangle apply their shape now (the
        drag only previewed it); pencil and eraser painted live,
        so the commit is just the undo entry. A drag that
        changed nothing — a click that painted its own color,
        or a shape that landed on identical pixels — records no
        step, and the preview is restored to the true result."""
        drag = self._editor_drag
        self._editor_drag = None
        if drag is None:
            return
        tool = drag["tool"]
        layer = drag["layer"]
        if tool in ("line", "rectangle"):
            end = drag.get("current", drag["start"])
            if tool == "line":
                draw_line(layer.image,
                          drag["start"][0], drag["start"][1],
                          end[0], end[1], self._editor_color)
            else:
                draw_rect_outline(layer.image,
                                  drag["start"][0], drag["start"][1],
                                  end[0], end[1], self._editor_color)
        if layer.image == drag["before"]:
            self.result_image = composite_layers(self._layers)
            self._refresh_pixmaps()
            return
        self._commit_edit(layer, drag["before"],
                          {"pencil": "Draw", "eraser": "Erase",
                           "line": "Line",
                           "rectangle": "Rectangle"}[tool])


    # -------------------------------------------------------- layers

    def _layer_at_row(self, row):
        """The layer a list row shows: row 0 is the top layer,
        the last row is the base."""
        if 0 <= row < len(self._layers):
            return self._layers[len(self._layers) - 1 - row]
        return None


    def _refresh_layer_list(self):
        """Rebuild the layer list from the stack, top first,
        keeping the active layer current. Signals stay blocked
        while rebuilding — the check states and names are set
        here, not by the user."""
        self.layer_list.blockSignals(True)
        try:
            self.layer_list.clear()
            for layer in reversed(self._layers):
                item = QListWidgetItem(layer.name)
                item.setFlags(item.flags()
                              | Qt.ItemFlag.ItemIsUserCheckable
                              | Qt.ItemFlag.ItemIsEditable)
                item.setCheckState(
                    Qt.CheckState.Checked if layer.visible
                    else Qt.CheckState.Unchecked)
                self.layer_list.addItem(item)
            if self._active_layer is not None:
                index = self._layers.index(self._active_layer)
                self.layer_list.setCurrentRow(
                    len(self._layers) - 1 - index)
        finally:
            self.layer_list.blockSignals(False)


    def _update_layer_buttons(self):
        """Layer buttons follow the stack: Add works on any
        loaded result; Delete needs more than one layer, so
        the list never goes empty; Up and Down stop at the
        ends of the stack."""
        has_result = self.result_image is not None
        self.layer_list.setEnabled(has_result)
        self.layer_add_btn.setEnabled(has_result)
        self.layer_delete_btn.setEnabled(
            has_result and len(self._layers) > 1)
        if self._active_layer is None or not self._layers:
            self.layer_up_btn.setEnabled(False)
            self.layer_down_btn.setEnabled(False)
            return
        index = self._layers.index(self._active_layer)
        self.layer_up_btn.setEnabled(
            index < len(self._layers) - 1)
        self.layer_down_btn.setEnabled(index > 0)


    def _on_layer_selected(self, row):
        """A layer was selected in the list — it is the active
        layer now, the one the editor paints on."""
        layer = self._layer_at_row(row)
        if layer is not None:
            self._active_layer = layer
            self._update_layer_buttons()


    def _on_layer_item_clicked(self, item):
        """A click on a layer row. A click on the already
        current row fires no row change, so the click handler
        lands the selection itself — both paths end with the
        clicked layer active."""
        self._on_layer_selected(self.layer_list.row(item))


    def _on_layer_item_changed(self, item):
        """A layer row changed: a rename, or a visibility
        toggle. A rename is not undoable; a blank name puts
        the old name back. Visibility is a view setting, like
        zoom — not undoable either — and re-composites the
        preview at once."""
        layer = self._layer_at_row(self.layer_list.row(item))
        if layer is None:
            return
        if item.text() != layer.name:
            if not item.text().strip():
                self.layer_list.blockSignals(True)
                item.setText(layer.name)
                self.layer_list.blockSignals(False)
                return
            layer.name = item.text()
            return
        visible = (item.checkState() == Qt.CheckState.Checked)
        if visible != layer.visible:
            layer.visible = visible
            self._refresh_result()


    def _next_layer_name(self):
        """The next free default layer name: "Layer 2" is the
        first new layer, then "Layer 3", reusing a freed
        number when its layer was deleted."""
        used = {layer.name for layer in self._layers}
        number = 2
        while "Layer %d" % number in used:
            number += 1
        return "Layer %d" % number


    def _on_layer_add(self):
        """Add a transparent layer on top and make it active:
        one undo step."""
        if self.result_image is None:
            return
        before = list(self._layers)
        before_active = self._active_layer
        layer = Layer(self._next_layer_name(),
                      Image(self.result_image.width(),
                            self.result_image.height()),
                      True)
        self._layers.append(layer)
        self._active_layer = layer
        self._push_undo(_LayerStackCommand(
            self, before, self._layers, before_active, layer,
            "Add layer"))
        self._refresh_result()
        self._refresh_layer_list()
        self._update_layer_buttons()


    def _on_layer_delete(self):
        """Delete the active layer: one undo step. A layer
        with any non-transparent pixel asks first (default
        No); an empty layer deletes silently. The layer below
        becomes active, or the one above when the bottom layer
        goes. The Delete button disables on the last layer, so
        this never empties the list."""
        layer = self._active_layer
        if layer is None or len(self._layers) <= 1:
            return
        if has_content(layer.image):
            answer = QMessageBox.question(
                self, "Delete layer?",
                "Delete the layer \"%s\"? Its pixels will be "
                "lost." % layer.name,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        before = list(self._layers)
        index = self._layers.index(layer)
        self._layers.remove(layer)
        if index > 0:
            self._active_layer = self._layers[index - 1]
        else:
            self._active_layer = self._layers[0]
        self._push_undo(_LayerStackCommand(
            self, before, self._layers, layer,
            self._active_layer, "Delete layer"))
        self._refresh_result()
        self._refresh_layer_list()
        self._update_layer_buttons()


    def _on_layer_move(self, direction):
        """Move the active layer one step up (+1, toward the
        top) or down (-1, toward the base): one undo step."""
        layer = self._active_layer
        if layer is None:
            return
        index = self._layers.index(layer)
        target = index + direction
        if target < 0 or target >= len(self._layers):
            return
        before = list(self._layers)
        self._layers[index], self._layers[target] = (
            self._layers[target], self._layers[index])
        self._push_undo(_LayerStackCommand(
            self, before, self._layers, layer, layer,
            "Move layer"))
        self._refresh_result()
        self._refresh_layer_list()
        self._update_layer_buttons()


    def _install_layer_stack(self, layers, active):
        """Install a layer list and active layer (undo/redo of
        a stack change), then bring the preview, the list, and
        the buttons up to date."""
        self._layers = list(layers)
        self._active_layer = active
        self._refresh_result()
        self._refresh_layer_list()
        self._update_layer_buttons()
