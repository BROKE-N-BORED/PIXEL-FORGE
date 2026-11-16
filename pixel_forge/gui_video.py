"""Video tab: extract frames, keep or drop, export..

One of the MainWindow mixins (task 58).
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)
from .atlas import DEFAULT_PADDING, write_atlas
from .engine_export import FORMAT_INFO
from .gui_shared import (EngineExportDialog, MAX_CUSTOM_SIZE, SIZE_PRESETS, STRIP_THUMB, SheetSplitDialog, SpritesheetDialog, TagDialog, VIDEO_DEBOUNCE_MS, VideoWorker, _KeepDropCommand, dimmed_pixmap, gui_module, thumb_pixmap, video_filter)
from pathlib import Path
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError, plural)
from .presets import SHEET_COLS_VALUES
from .spritesheet import duration_for_fps, validate_tags, write_sheet
from .video import (DEFAULT_FPS, FPS_PRESETS, FRAME_COUNT_CONFIRM, estimate_frame_count, onion_blend, parse_time, probe_duration, require_ffmpeg, write_gif)
import shutil


class VideoTabMixin:
    """Video tab: extract frames, keep or drop, export.

    One of the MainWindow mixins (task 58): the methods
    moved here verbatim from gui.py. MainWindow
    inherits this class; self is the window.
    """


    # ============================================================ video tab

    def _build_video_tab(self):
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

        self.video_import_btn = QPushButton("Import video")
        self.video_import_btn.setToolTip(
            "Import a video to turn its frames into sprites")
        self.video_import_btn.clicked.connect(self._on_video_import)
        form.addWidget(self.video_import_btn)

        self.video_split_btn = QPushButton("Split spritesheet")
        self.video_split_btn.setToolTip(
            "Split a spritesheet PNG back into frames — with "
            "its metadata, or as a grid")
        self.video_split_btn.clicked.connect(
            self._on_split_spritesheet)
        form.addWidget(self.video_split_btn)

        form.addWidget(self._section("Frames"))
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Extract fps"))
        self.video_fps_combo = QComboBox()
        self.video_fps_combo.setToolTip(
            "Frames extracted per second of video")
        for preset in FPS_PRESETS:
            self.video_fps_combo.addItem(str(preset), preset)
        self.video_fps_combo.setCurrentIndex(
            FPS_PRESETS.index(DEFAULT_FPS))
        self.video_fps_combo.currentIndexChanged.connect(
            self._on_video_extract_changed)
        self.video_fps_combo.currentIndexChanged.connect(
            lambda _i: self._mark_video_preset_custom(
                "extract_fps"))
        fps_row.addWidget(self.video_fps_combo)
        form.addLayout(fps_row)

        every_row = QHBoxLayout()
        every_row.addWidget(QLabel("Keep every Nth frame"))
        self.video_every_spin = QSpinBox()
        self.video_every_spin.setRange(1, 60)
        self.video_every_spin.setValue(1)
        self.video_every_spin.setToolTip("Keep one frame in every N")
        self.video_every_spin.valueChanged.connect(
            self._on_video_extract_changed)
        every_row.addWidget(self.video_every_spin)
        form.addLayout(every_row)

        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Start time"))
        self.video_start_edit = QLineEdit()
        self.video_start_edit.setPlaceholderText("0.0")
        self.video_start_edit.setToolTip(
            "Start extracting at this time, in seconds")
        self.video_start_edit.editingFinished.connect(
            self._on_video_extract_changed)
        start_row.addWidget(self.video_start_edit)
        form.addLayout(start_row)

        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("End time"))
        self.video_end_edit = QLineEdit()
        self.video_end_edit.setPlaceholderText("to the end")
        self.video_end_edit.setToolTip(
            "Stop extracting at this time, in seconds")
        self.video_end_edit.editingFinished.connect(
            self._on_video_extract_changed)
        end_row.addWidget(self.video_end_edit)
        form.addLayout(end_row)

        # Frame-count estimate (task 25): "about 240 frames" at
        # the current settings, blank until a video is loaded or
        # when the clip's length could not be probed.
        self.video_estimate_label = QLabel("")
        self.video_estimate_label.setToolTip(
            "Estimated frames to extract at the current settings")
        form.addWidget(self.video_estimate_label)

        form.addWidget(self._section("Size"))
        self.video_size_combo = QComboBox()
        self.video_size_combo.setToolTip("Frame size in pixels")
        for preset in SIZE_PRESETS:
            self.video_size_combo.addItem("%d x %d" % (preset, preset),
                                          preset)
        self.video_size_combo.addItem("Custom", None)
        self.video_size_combo.setCurrentIndex(1)  # 32 x 32
        self.video_size_combo.currentIndexChanged.connect(
            self._on_video_preset)
        form.addWidget(self.video_size_combo)

        video_size_row = QHBoxLayout()
        self.video_width_spin = QSpinBox()
        self.video_width_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.video_width_spin.setValue(32)
        self.video_width_spin.setPrefix("W ")
        self.video_width_spin.setToolTip("Frame width in pixels")
        self.video_width_spin.valueChanged.connect(
            self._on_video_custom_size)
        self.video_height_spin = QSpinBox()
        self.video_height_spin.setRange(1, MAX_CUSTOM_SIZE)
        self.video_height_spin.setValue(32)
        self.video_height_spin.setPrefix("H ")
        self.video_height_spin.setToolTip("Frame height in pixels")
        self.video_height_spin.valueChanged.connect(
            self._on_video_custom_size)
        video_size_row.addWidget(self.video_width_spin)
        video_size_row.addWidget(self.video_height_spin)
        form.addLayout(video_size_row)

        self.video_aspect_check = QCheckBox("Keep aspect ratio")
        self.video_aspect_check.setToolTip(
            "Keep the original proportions when resizing")
        self.video_aspect_check.toggled.connect(
            self._on_video_control_changed)
        form.addWidget(self.video_aspect_check)

        form.addWidget(self._section("Palette"))
        self.video_palette_combo = QComboBox()
        self.video_palette_combo.setToolTip(
            "Colors the frames may use")
        self._fill_palette_combo(self.video_palette_combo)
        self.video_palette_combo.currentIndexChanged.connect(
            self._on_video_control_changed)
        form.addWidget(self.video_palette_combo)

        video_colors_row = QHBoxLayout()
        video_colors_row.addWidget(QLabel("Max colors"))
        self.video_colors_spin = QSpinBox()
        self.video_colors_spin.setRange(MIN_COLORS, MAX_COLORS)
        self.video_colors_spin.setValue(16)
        self.video_colors_spin.setToolTip(
            "Most colors the frames may use")
        self.video_colors_spin.valueChanged.connect(
            self._on_video_control_changed)
        video_colors_row.addWidget(self.video_colors_spin)
        form.addLayout(video_colors_row)

        self.video_dither_check = QCheckBox("Dithering")
        self.video_dither_check.setToolTip(
            "Ordered (Bayer) dithering")
        self.video_dither_check.setChecked(True)
        self.video_dither_check.toggled.connect(
            self._on_video_control_changed)
        form.addWidget(self.video_dither_check)

        self._build_preset_section(form, "video")

        form.addWidget(self._section("Zoom"))
        video_zoom_row = QHBoxLayout()
        self.video_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.video_zoom_slider.setRange(1, 16)
        self.video_zoom_slider.setValue(8)
        self.video_zoom_slider.setToolTip("Zoom the frame preview")
        self.video_zoom_slider.valueChanged.connect(
            self._on_video_zoom_changed)
        video_zoom_row.addWidget(self.video_zoom_slider)
        self.video_zoom_label = QLabel("8x")
        self.video_zoom_label.setFixedWidth(32)
        video_zoom_row.addWidget(self.video_zoom_label)
        self.video_zoom_fit_btn = QPushButton("Fit")
        self.video_zoom_fit_btn.setToolTip(
            "Zoom to fit the preview pane")
        self.video_zoom_fit_btn.clicked.connect(
            self._on_fit_video_zoom)
        video_zoom_row.addWidget(self.video_zoom_fit_btn)
        form.addLayout(video_zoom_row)

        form.addWidget(self._section("Spritesheet"))
        sheet_cols_row = QHBoxLayout()
        sheet_cols_row.addWidget(QLabel("Columns"))
        self.sheet_cols_combo = QComboBox()
        self.sheet_cols_combo.setToolTip(
            "Columns in the packed spritesheet")
        self.sheet_cols_combo.addItem("Auto", None)
        for count in SHEET_COLS_VALUES:
            self.sheet_cols_combo.addItem(str(count), count)
        self.sheet_cols_combo.currentIndexChanged.connect(
            lambda _i: self._mark_video_preset_custom("sheet_cols"))
        sheet_cols_row.addWidget(self.sheet_cols_combo)
        form.addLayout(sheet_cols_row)

        sheet_padding_row = QHBoxLayout()
        sheet_padding_row.addWidget(QLabel("Padding"))
        self.sheet_padding_spin = QSpinBox()
        self.sheet_padding_spin.setRange(0, 32)
        self.sheet_padding_spin.setValue(0)
        self.sheet_padding_spin.setSuffix(" px")
        self.sheet_padding_spin.setToolTip(
            "Blank pixels between frames in the sheet")
        self.sheet_padding_spin.valueChanged.connect(
            lambda _v: self._mark_video_preset_custom(
                "sheet_padding"))
        sheet_padding_row.addWidget(self.sheet_padding_spin)
        form.addLayout(sheet_padding_row)

        sheet_fps_row = QHBoxLayout()
        sheet_fps_row.addWidget(QLabel("Sheet fps"))
        self.sheet_fps_spin = QSpinBox()
        self.sheet_fps_spin.setRange(1, 30)
        self.sheet_fps_spin.setValue(DEFAULT_FPS)
        self.sheet_fps_spin.setToolTip(
            "Sheet speed — sets every frame to this speed")
        self.sheet_fps_spin.valueChanged.connect(
            self._on_sheet_fps_changed)
        self.sheet_fps_spin.valueChanged.connect(
            lambda _v: self._mark_video_preset_custom("sheet_fps"))
        sheet_fps_row.addWidget(self.sheet_fps_spin)
        form.addLayout(sheet_fps_row)

        # Atlas packing (task 38): MaxRects packs mixed-size
        # sprites tight instead of a grid. Grid is the current
        # behavior, so the combo starts there — that preserves
        # the existing default, it is not a pre-selection the
        # user must make. The atlas controls only apply to
        # MaxRects and stay disabled under Grid; the grid
        # columns and padding stay disabled under MaxRects.
        sheet_pack_row = QHBoxLayout()
        sheet_pack_row.addWidget(QLabel("Packing"))
        self.sheet_pack_combo = QComboBox()
        self.sheet_pack_combo.setToolTip(
            "How the frames are packed — Grid lays same-size "
            "frames in rows, MaxRects packs mixed sizes tight")
        self.sheet_pack_combo.addItem("Grid", "grid")
        self.sheet_pack_combo.addItem("MaxRects", "maxrects")
        self.sheet_pack_combo.currentIndexChanged.connect(
            self._on_sheet_pack_changed)
        sheet_pack_row.addWidget(self.sheet_pack_combo)
        form.addLayout(sheet_pack_row)

        self.sheet_trim_check = QCheckBox("Trim transparent borders")
        self.sheet_trim_check.setToolTip(
            "Cut transparent borders off each frame before "
            "packing — the cut is recorded in the metadata")
        self.sheet_trim_check.setEnabled(False)
        form.addWidget(self.sheet_trim_check)

        sheet_atlas_padding_row = QHBoxLayout()
        sheet_atlas_padding_row.addWidget(QLabel("Atlas padding"))
        self.sheet_atlas_padding_spin = QSpinBox()
        self.sheet_atlas_padding_spin.setRange(0, 64)
        self.sheet_atlas_padding_spin.setValue(DEFAULT_PADDING)
        self.sheet_atlas_padding_spin.setSuffix(" px")
        self.sheet_atlas_padding_spin.setToolTip(
            "Empty ring kept around every sprite in a MaxRects "
            "atlas — the gap between two sprites is twice this")
        self.sheet_atlas_padding_spin.setEnabled(False)
        sheet_atlas_padding_row.addWidget(
            self.sheet_atlas_padding_spin)
        form.addLayout(sheet_atlas_padding_row)

        sheet_extrude_row = QHBoxLayout()
        sheet_extrude_row.addWidget(QLabel("Extrude"))
        self.sheet_extrude_spin = QSpinBox()
        self.sheet_extrude_spin.setRange(0, 16)
        self.sheet_extrude_spin.setValue(0)
        self.sheet_extrude_spin.setSuffix(" px")
        self.sheet_extrude_spin.setToolTip(
            "Repeat each sprite's edge pixels outward into the "
            "padding — stops bleeding when the atlas is scaled")
        self.sheet_extrude_spin.setEnabled(False)
        sheet_extrude_row.addWidget(self.sheet_extrude_spin)
        form.addLayout(sheet_extrude_row)

        self.sheet_preview_btn = QPushButton("Preview spritesheet")
        self.sheet_preview_btn.setToolTip(
            "Preview the spritesheet and play its animation")
        self.sheet_preview_btn.setEnabled(False)
        self.sheet_preview_btn.clicked.connect(
            self._on_preview_spritesheet)
        form.addWidget(self.sheet_preview_btn)

        # Animation tags (task 34): named ranges over the kept
        # frames — walk/idle/attack sets in one sheet. Nothing is
        # selected by default; Play tag and Delete need a pick.
        form.addWidget(self._section("Animation tags"))
        self.video_tag_list = QListWidget()
        self.video_tag_list.setToolTip(
            "Named frame ranges — pick one to play it or export "
            "it as a GIF")
        self.video_tag_list.setMaximumHeight(96)
        self.video_tag_list.itemSelectionChanged.connect(
            self._on_video_tag_selected)
        form.addWidget(self.video_tag_list)
        tag_btn_row = QHBoxLayout()
        self.video_tag_add_btn = QPushButton("Add tag")
        self.video_tag_add_btn.setToolTip(
            "Name a range of kept frames")
        self.video_tag_add_btn.setEnabled(False)
        self.video_tag_add_btn.clicked.connect(self._on_video_tag_add)
        tag_btn_row.addWidget(self.video_tag_add_btn)
        self.video_tag_play_btn = QPushButton("Play tag")
        self.video_tag_play_btn.setToolTip(
            "Play only the picked tag's frames")
        self.video_tag_play_btn.setEnabled(False)
        self.video_tag_play_btn.clicked.connect(
            self._on_video_tag_play)
        tag_btn_row.addWidget(self.video_tag_play_btn)
        self.video_tag_delete_btn = QPushButton("Delete tag")
        self.video_tag_delete_btn.setToolTip("Delete the picked tag")
        self.video_tag_delete_btn.setEnabled(False)
        self.video_tag_delete_btn.clicked.connect(
            self._on_video_tag_delete)
        tag_btn_row.addWidget(self.video_tag_delete_btn)
        form.addLayout(tag_btn_row)

        form.addStretch(1)
        self.video_info_label = QLabel("No video loaded")
        self.video_info_label.setWordWrap(True)
        form.addWidget(self.video_info_label)

        self.video_export_btn = QPushButton("Export frames")
        self.video_export_btn.setObjectName("export")
        self.video_export_btn.setToolTip(
            "Export the kept frames as numbered PNGs")
        self.video_export_btn.setEnabled(False)
        self.video_export_btn.clicked.connect(self._on_export_frames)
        form.addWidget(self.video_export_btn)

        self.sheet_export_btn = QPushButton("Export spritesheet")
        self.sheet_export_btn.setObjectName("export")
        self.sheet_export_btn.setToolTip(
            "Export the kept frames as one spritesheet PNG and "
            "metadata")
        self.sheet_export_btn.setEnabled(False)
        self.sheet_export_btn.clicked.connect(
            self._on_export_spritesheet)
        form.addWidget(self.sheet_export_btn)

        self.video_gif_btn = QPushButton("Export GIF")
        self.video_gif_btn.setObjectName("export")
        self.video_gif_btn.setToolTip(
            "Export the animation as a GIF — the picked tag, or "
            "all kept frames when no tag is picked")
        self.video_gif_btn.setEnabled(False)
        self.video_gif_btn.clicked.connect(self._on_export_gif)
        form.addWidget(self.video_gif_btn)

        self.engine_export_btn = QPushButton("Export for engine")
        self.engine_export_btn.setObjectName("export")
        self.engine_export_btn.setToolTip(
            "Export the kept frames in a game engine's format — "
            "Aseprite, TexturePacker, Godot, Unity, GameMaker, "
            "Phaser, or Pixi")
        self.engine_export_btn.setEnabled(False)
        self.engine_export_btn.clicked.connect(self._on_export_engine)
        form.addWidget(self.engine_export_btn)

        self.sheet_library_btn = QPushButton("Save sheet to library")
        self.sheet_library_btn.setToolTip(
            "Save the spritesheet to the library")
        self.sheet_library_btn.setEnabled(False)
        self.sheet_library_btn.clicked.connect(
            self._on_save_sheet_to_library)
        form.addWidget(self.sheet_library_btn)

        main_layout.addWidget(
            self._scrollable_column(controls))

        # ------------------------------------------- preview and strip
        right = QSplitter(Qt.Orientation.Vertical)

        preview_pane = QWidget()
        preview_layout = QVBoxLayout(preview_pane)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self._section("Animation preview"))
        self.video_preview_label = QLabel(
            "Import a video to see its frames here")
        self.video_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.video_preview_label.setWordWrap(True)
        self.video_preview_scroll = QScrollArea()
        self.video_preview_scroll.setWidgetResizable(True)
        self.video_preview_scroll.setWidget(self.video_preview_label)
        preview_layout.addWidget(self.video_preview_scroll, 1)

        # Scrubber (task 34): jump to any kept frame. Dragging
        # pauses playback — it resumes only when Play is pressed
        # again, never on its own. The slider tracks the current
        # frame while playing (see _refresh_video_preview).
        self.video_scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.video_scrub_slider.setToolTip(
            "Scrub through the kept frames")
        self.video_scrub_slider.setEnabled(False)
        self.video_scrub_slider.valueChanged.connect(
            self._on_video_scrub)
        preview_layout.addWidget(self.video_scrub_slider)

        playback_row = QHBoxLayout()
        self.video_play_btn = QPushButton("Play")
        self.video_play_btn.setToolTip(
            "Play or pause the animation preview")
        self.video_play_btn.setEnabled(False)
        self.video_play_btn.clicked.connect(self._on_play_pause)
        playback_row.addWidget(self.video_play_btn)
        playback_row.addWidget(QLabel("Preview fps"))
        self.video_play_fps_spin = QSpinBox()
        self.video_play_fps_spin.setRange(1, 30)
        self.video_play_fps_spin.setValue(DEFAULT_FPS)
        self.video_play_fps_spin.setToolTip(
            "Playback speed — sets every frame to this speed")
        self.video_play_fps_spin.valueChanged.connect(
            self._on_play_fps_changed)
        self.video_play_fps_spin.valueChanged.connect(
            lambda _v: self._mark_video_preset_custom(
                "preview_fps"))
        playback_row.addWidget(self.video_play_fps_spin)
        self.video_loop_check = QCheckBox("Loop")
        self.video_loop_check.setToolTip(
            "Loop the animation preview")
        self.video_loop_check.setChecked(True)
        playback_row.addWidget(self.video_loop_check)
        # Per-frame duration (task 34): how long the CURRENT frame
        # shows, in milliseconds. A long duration holds the frame.
        playback_row.addWidget(QLabel("Frame duration"))
        self.video_duration_spin = QSpinBox()
        self.video_duration_spin.setRange(10, 5000)
        self.video_duration_spin.setSuffix(" ms")
        self.video_duration_spin.setToolTip(
            "How long the current frame shows — a long duration "
            "holds the frame")
        self.video_duration_spin.setEnabled(False)
        self.video_duration_spin.valueChanged.connect(
            self._on_video_duration_changed)
        playback_row.addWidget(self.video_duration_spin)
        # Onion skin (task 34): the previous kept frame ghosted
        # behind the current one. Off by default; never leaks into
        # exports — the preview composites it, the frames stay
        # plain.
        self.video_onion_check = QCheckBox("Onion skin")
        self.video_onion_check.setToolTip(
            "Show the previous frame ghosted behind the current "
            "one")
        self.video_onion_check.setEnabled(False)
        self.video_onion_check.toggled.connect(
            self._refresh_video_preview)
        playback_row.addWidget(self.video_onion_check)
        self.video_counter_label = QLabel("")
        playback_row.addWidget(self.video_counter_label)
        playback_row.addStretch(1)
        preview_layout.addLayout(playback_row)
        right.addWidget(preview_pane)

        strip_pane = QWidget()
        strip_layout = QVBoxLayout(strip_pane)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_head = QHBoxLayout()
        strip_head.addWidget(self._section(
            "Frames — uncheck to drop"))
        strip_head.addStretch(1)
        self.video_keep_all_btn = QPushButton("Keep all")
        self.video_keep_all_btn.setToolTip("Keep every frame")
        self.video_keep_all_btn.setEnabled(False)
        self.video_keep_all_btn.clicked.connect(self._on_keep_all)
        strip_head.addWidget(self.video_keep_all_btn)
        self.video_drop_all_btn = QPushButton("Drop all")
        self.video_drop_all_btn.setToolTip("Drop every frame")
        self.video_drop_all_btn.setEnabled(False)
        self.video_drop_all_btn.clicked.connect(self._on_drop_all)
        strip_head.addWidget(self.video_drop_all_btn)
        self.video_keep_selected_btn = QPushButton("Keep selected")
        self.video_keep_selected_btn.setToolTip(
            "Keep the selected frames")
        self.video_keep_selected_btn.setEnabled(False)
        self.video_keep_selected_btn.clicked.connect(
            self._on_keep_selected)
        strip_head.addWidget(self.video_keep_selected_btn)
        self.video_drop_selected_btn = QPushButton("Drop selected")
        self.video_drop_selected_btn.setToolTip(
            "Drop the selected frames")
        self.video_drop_selected_btn.setEnabled(False)
        self.video_drop_selected_btn.clicked.connect(
            self._on_drop_selected)
        strip_head.addWidget(self.video_drop_selected_btn)
        strip_layout.addLayout(strip_head)
        self.video_strip = QListWidget()
        self.video_strip.setToolTip(
            "Frames — click one to preview it, uncheck to drop "
            "it, drag to reorder")
        self.video_strip.setIconSize(
            QSize(STRIP_THUMB, STRIP_THUMB))
        self.video_strip.setFlow(QListWidget.Flow.LeftToRight)
        self.video_strip.setWrapping(True)
        self.video_strip.setResizeMode(
            QListWidget.ResizeMode.Adjust)
        # Multi-select + drag reorder (task 25): ExtendedSelection
        # gives Ctrl+click and Shift+click ranges; InternalMove
        # with Snap movement lets a frame be dragged to a new
        # position, and rowsMoved records the new strip order.
        # Nothing is selected by default — a rebuild leaves the
        # strip with no selection, as before.
        self.video_strip.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.video_strip.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self.video_strip.setMovement(
            QListWidget.Movement.Snap)
        self.video_strip.itemClicked.connect(
            self._on_strip_item_clicked)
        self.video_strip.itemChanged.connect(
            self._on_strip_item_changed)
        self.video_strip.itemSelectionChanged.connect(
            self._on_strip_selection_changed)
        self.video_strip.model().rowsMoved.connect(
            self._on_strip_reordered)
        strip_layout.addWidget(self.video_strip, 1)
        right.addWidget(strip_pane)

        right.setSizes([380, 220])
        main_layout.addWidget(right, 1)
        return root


    # ---------------------------------------------------------- input

    def load_video(self, path, keep_range=False):
        """Import a video: start a background extract + pixelize
        run and return True at once — the window stays responsive
        while the frames land in _on_video_run_done. Returns False
        (with a message box) when ffmpeg is missing; that check is
        cheap and stays synchronous so a missing ffmpeg still fails
        fast, exactly as before. Also False when the user declines
        the big-extraction confirm (task 25). keep_range is the
        crash-recovery path (task 55): the range belongs to the
        same clip, so the fresh-import clear is skipped.
        """
        # Autosave (task 55): a new video replaces whatever a
        # pending crash-recovery set was meant for.
        self._autosave_pending_video = None
        self._stop_playback()
        try:
            require_ffmpeg()
        except PixelForgeError as exc:
            self.video_path = None
            self.video_duration = None
            self._refresh_video_estimate()
            gui_module.warn_dialog(self, "Could not import video", str(exc))
            return False
        self.video_path = str(path)
        # The clip's length, for the frame-count estimate (task
        # 25) — ffprobe answers in milliseconds, and a failed
        # probe just means no estimate, never a failure.
        self.video_duration = probe_duration(path)
        # B7: the start/end range belongs to the OLD clip — a stale
        # range beyond the new clip's length used to yield "no frames
        # extracted" with no visible reason. Clear it so the new
        # import always starts from the whole clip (empty = full
        # range, the fresh-tab default). The other settings (fps,
        # size, palette, colors, dither, every-Nth, preset) are the
        # user's working choices and stay. Crash recovery (task
        # 55) keeps the range: it belongs to the same clip.
        if not keep_range:
            self.video_start_edit.clear()
            self.video_end_edit.clear()
        self._refresh_video_estimate()
        started = self._reload_video_frames(fresh=True)
        if started:
            self._record_session(path, "video")
        return started


    def _on_video_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import video", "", video_filter())
        if path:
            self.load_video(path)


    def _on_split_spritesheet(self):
        """Open the sheet splitter (task 40) and land its frames
        in the strip when the user splits."""
        dialog = SheetSplitDialog(
            self, self._video_default_duration())
        if dialog.exec():
            self._load_split_frames(
                dialog.frames, dialog.durations, dialog.tags,
                dialog.source_name)


    def _load_split_frames(self, frames, durations, tags,
                           source_name):
        """Land split sheet frames in the Video tab, the way a
        fresh extraction lands — keep/drop, order, durations,
        and tags all start from the split, and the strip,
        preview, and buttons refresh. The frames are finished
        pixel art, so there is no raw set to re-pixelize and no
        video to re-extract: the pixelize controls do not apply
        to them, and a run in flight is orphaned so its late
        result cannot overwrite the split (the stale guard in
        _on_video_run_done drops it)."""
        self._stop_playback()
        self._video_debounce_timer.stop()
        self._video_pending_reload = False
        if self.video_worker is not None:
            old = self.video_worker
            self._video_orphans.add(old)
            old.finished.connect(
                lambda old=old: self._video_orphans.discard(old))
            self.video_worker = None
        self._cleanup_video_raw()
        self.video_path = None
        self.video_duration = None
        self._refresh_video_estimate()
        self.video_frames = list(frames)
        self.video_keep = [True] * len(frames)
        self.video_order = list(range(len(frames)))
        self.video_durations = list(durations)
        self.video_tags = [dict(tag) for tag in tags]
        self.video_play_tag = None
        self._refresh_video_tag_list()
        kept = self._kept_indices()
        self.video_current = kept[0] if kept else 0
        self._rebuild_strip()
        self._refresh_video_preview()
        self._on_fit_video_zoom()
        self._update_video_info()
        self._update_video_buttons()
        self.statusBar().showMessage(
            "Split %s (%s)"
            % (source_name, plural(len(frames), "frame")))
        self._mark_autosave_dirty()


    def _cleanup_video_raw(self):
        if self.video_raw_dir is not None:
            shutil.rmtree(self.video_raw_dir, ignore_errors=True)
            self.video_raw_dir = None
        self.video_raw_frames = []


    def _video_pixelize_settings(self):
        """The current size/palette/dither controls as a dict —
        the settings a worker run snapshots at start."""
        return {
            "width": self.video_width_spin.value(),
            "height": self.video_height_spin.value(),
            "keep_aspect": self.video_aspect_check.isChecked(),
            "palette": self.video_palette_combo.currentData(),
            "max_colors": self.video_colors_spin.value(),
            "dither": self.video_dither_check.isChecked(),
            "palettes": self.palette_store.user_palettes(),
        }


    def _video_estimate(self):
        """The frame-count estimate at the current settings, or
        None when the clip's length is unknown or the time range
        is unreadable (task 25)."""
        if self.video_path is None or self.video_duration is None:
            return None
        try:
            start_text = self.video_start_edit.text().strip()
            end_text = self.video_end_edit.text().strip()
            start = parse_time(start_text) if start_text else None
            end = parse_time(end_text) if end_text else None
        except PixelForgeError:
            return None
        return estimate_frame_count(
            self.video_duration,
            self.video_fps_combo.currentData(),
            every=self.video_every_spin.value(),
            start=start, end=end)


    def _refresh_video_estimate(self):
        """Show the frame-count estimate ("about 240 frames") or
        clear it when there is nothing to estimate (task 25)."""
        estimate = self._video_estimate()
        if estimate is None:
            self.video_estimate_label.setText("")
        else:
            self.video_estimate_label.setText(
                "about %s" % plural(estimate, "frame"))


    def _reload_video_frames(self, fresh=False):
        """Start a background re-extract with the current
        fps/every/range, then re-pixelize. Returns False when
        there is no video to re-extract, or when the user
        declines the big-extraction confirm; otherwise True —
        the frames land in _on_video_run_done, and a failure
        lands in _on_video_run_failed with a message box, the
        old frames still in place."""
        # Autosave (task 55): a run is a long operation, so the
        # work is written down before it starts.
        self._autosave_write_now()
        if self.video_path is None:
            return False
        # Guardrail (task 25): a long clip at a high fps would
        # write tens of thousands of PNGs — ask first when the
        # estimate is over the threshold. Default No: declining
        # a fresh import clears the path (nothing landed yet);
        # declining a re-extract keeps the old frames in place.
        estimate = self._video_estimate()
        if estimate is not None and estimate > FRAME_COUNT_CONFIRM:
            answer = QMessageBox.question(
                self, "Extract %s?" % plural(estimate, "frame"),
                "About %s would be extracted as PNG files. That "
                "is a lot of files — lower the fps, keep every "
                "Nth frame, or shorten the range to extract "
                "fewer. Extract them anyway?"
                % plural(estimate, "frame"),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                if fresh:
                    self.video_path = None
                    self.video_duration = None
                    self._refresh_video_estimate()
                self.statusBar().showMessage("Extraction cancelled")
                return False
        self._stop_playback()
        # Task 83: a GIF's frames ARE the animation — resampling
        # them to the extraction fps destroys the GIF's own
        # timing, and the per-frame delays only map onto the
        # frames when extraction takes every frame at the source
        # rate. GIFs extract natively (fps None); the worker
        # probes the delays from the file. Videos are untouched.
        fps = self.video_fps_combo.currentData()
        if Path(self.video_path).suffix.lower() == ".gif":
            fps = None
        settings = self._video_pixelize_settings()
        settings.update({
            "mode": "reload",
            "path": self.video_path,
            "fps": fps,
            "every": self.video_every_spin.value(),
            "start": self.video_start_edit.text().strip() or None,
            "end": self.video_end_edit.text().strip() or None,
            "fresh": fresh,
        })
        self._start_video_run(settings)
        return True


    def _repixelize_video(self):
        """Start a background re-pixelize of the raw frames with
        the current size/palette/dither controls. Keep/drop
        choices survive when the frame count is unchanged —
        applied in _on_video_run_done, so toggles made while
        the run is in flight are honoured too."""
        if not self.video_raw_frames:
            return
        settings = self._video_pixelize_settings()
        settings.update({
            "mode": "repixelize",
            "raw_frames": list(self.video_raw_frames),
            "fps": self.video_fps_combo.currentData(),
        })
        self._start_video_run(settings)


    def _start_video_run(self, settings):
        """Run the Video tab's work on a worker thread. A newer
        run supersedes the one in flight — the superseded worker
        keeps going in the background, but its results are
        dropped by the stale guard in _on_video_run_done, so
        runs never stack up behind each other."""
        if settings["mode"] == "reload":
            self.statusBar().showMessage("Extracting frames…")
        else:
            self.statusBar().showMessage("Pixelizing…")
        worker = VideoWorker(settings, parent=self)
        if self.video_worker is not None:
            old = self.video_worker
            self._video_orphans.add(old)
            old.finished.connect(
                lambda old=old: self._video_orphans.discard(old))
        self.video_worker = worker
        worker.done.connect(self._on_video_run_done)
        worker.failed.connect(self._on_video_run_failed)
        worker.start()


    def _schedule_video_run(self, reload):
        """A control changed — wait VIDEO_DEBOUNCE_MS for the
        user to stop changing things, then run once with the
        settled settings. An extract change schedules a
        re-extract (which pixelizes too); a pixelize-only change
        schedules a re-pixelize. A pending re-extract covers a
        later pixelize change, so the flag only ever escalates."""
        if reload:
            self._video_pending_reload = True
        self._video_debounce_timer.start(VIDEO_DEBOUNCE_MS)


    def _start_scheduled_video_run(self):
        reload = self._video_pending_reload
        self._video_pending_reload = False
        if reload:
            self._reload_video_frames()
        elif self.video_raw_frames:
            self._repixelize_video()


    def _on_video_run_done(self, result):
        if self.sender() is not self.video_worker:
            # Superseded — drop the results and clean up the
            # temp dir this run extracted into.
            self._video_orphans.discard(self.sender())
            tmp = result.get("raw_dir")
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
            return
        self.video_worker = None
        if result["mode"] == "reload":
            self._cleanup_video_raw()
            self.video_raw_dir = result["raw_dir"]
            self.video_raw_frames = result["raw"]
            # B8: the three fps controls keep their own values —
            # a reload used to copy the extraction fps into the
            # Preview and Sheet fps controls, silently stomping
            # whatever the user had set there. Nothing copies one
            # fps into another; each control is read only where
            # it applies (extraction, playback, sheet metadata).
            self.video_keep = [True] * len(result["frames"])
            # A new extraction: the strip starts in natural
            # order (task 25). A re-pixelize keeps the user's
            # order — the frame count is unchanged there.
            self.video_order = list(range(len(result["frames"])))
            # Durations + tags (task 34): a new extraction is new
            # frames entirely, so every frame starts at the Sheet
            # fps default and the tags go — they named ranges over
            # frames that no longer exist. A GIF import carries
            # its own per-frame delays instead (task 83): the
            # worker probed them from the file, and they land
            # here when there is exactly one per extracted
            # frame — a frame with no readable delay keeps the
            # default, and a probe that failed or miscounted
            # falls back to the default for every frame.
            durations = result.get("durations")
            default = self._video_default_duration()
            if (isinstance(durations, list)
                    and len(durations) == len(result["frames"])):
                self.video_durations = [
                    d if d is not None else default
                    for d in durations]
            else:
                self.video_durations = [default] \
                    * len(result["frames"])
            self.video_tags = []
            self.video_play_tag = None
            self._refresh_video_tag_list()
        elif len(self.video_keep) != len(result["frames"]):
            self.video_keep = [True] * len(result["frames"])
            self.video_order = list(range(len(result["frames"])))
            # Same reset when a re-pixelize changes the frame
            # count — the old durations and tags named frames by
            # position, and the positions moved.
            self.video_durations = [self._video_default_duration()] \
                * len(result["frames"])
            self.video_tags = []
            self.video_play_tag = None
            self._refresh_video_tag_list()
        self.video_frames = result["frames"]
        # Belt and braces: durations parallel the frames, and any
        # path that changed the frame count without resetting them
        # (none is known, but the list is cheap to rebuild) gets
        # the Sheet fps default rather than an index error later.
        if len(self.video_durations) != len(self.video_frames):
            self.video_durations = [self._video_default_duration()] \
                * len(self.video_frames)
        # Autosave (task 55): a crash-recovery keep/drop set
        # lands here, before the strip is rebuilt from it.
        self._apply_pending_video_restore()
        kept = self._kept_indices()
        self.video_current = kept[0] if kept else 0
        self._rebuild_strip()
        self._refresh_video_preview()
        if result["mode"] == "reload":
            # A new video: fit the frame preview to its pane
            # (task 22). A re-pixelize keeps the user's zoom.
            self._on_fit_video_zoom()
        self._update_video_info()
        self._update_video_buttons()
        if result["fresh"]:
            self.statusBar().showMessage(
                "Loaded %s (%d frames)"
                % (Path(result["path"]).name, len(result["frames"])))
        elif result["mode"] == "reload":
            self.statusBar().showMessage(
                "Extracted %d frames" % len(result["frames"]))
        else:
            self.statusBar().showMessage(
                "Pixelized %d frames" % len(result["frames"]))
        self._show_drop_summary()
        if self._autosave_video_restored:
            self._autosave_video_restored = False
            self.statusBar().showMessage(
                "Brought back your video work from last time.")


    def _show_drop_summary(self):
        """Put a multi-file drop's summary back after a video
        run's own message overwrote it (review B15). A dropped
        video lands asynchronously; without this, the report of
        what the drop did to each file would be lost the moment
        the frames arrived."""
        if self._drop_summary is None:
            return
        current = self.statusBar().currentMessage()
        if current:
            self.statusBar().showMessage(
                "%s — %s" % (current, self._drop_summary))
        else:
            self.statusBar().showMessage(self._drop_summary)
        self._drop_summary = None


    def _on_video_run_failed(self, message):
        worker = self.sender()
        if worker is not self.video_worker:
            self._video_orphans.discard(worker)
            return
        self.video_worker = None
        # Autosave (task 55): no frames landed, so a pending
        # crash-recovery keep/drop set has nothing to land on.
        self._autosave_pending_video = None
        settings = worker.settings
        if settings["mode"] == "repixelize":
            # Same as the old synchronous path: a pixelize
            # failure is a status message, old frames stay.
            self.statusBar().showMessage(message)
            self._show_drop_summary()
            return
        # A failed re-extract shows the dialog, exactly as the
        # old synchronous _reload_video_frames did; the old
        # frames stay in place. The dialog leaves the status bar
        # alone, so a drop summary is still showing — drop the
        # flag without re-showing it.
        self._drop_summary = None
        gui_module.warn_dialog(self, "Video import failed", message)
        if settings.get("fresh"):
            self.video_path = None
            self.video_duration = None
            self._refresh_video_estimate()


    def _stop_video_worker(self):
        if self.video_worker is not None:
            if self.video_worker.isRunning():
                self.video_worker.wait(10000)
            self.video_worker = None
        for orphan in list(self._video_orphans):
            if orphan.isRunning():
                orphan.wait(10000)
        self._video_orphans.clear()


    # -------------------------------------------------------- controls

    def _on_video_extract_changed(self):
        """fps, every-Nth, or time range changed — re-extract,
        debounced: the run starts 300 ms after the last change.
        The frame-count estimate updates at once, not debounced
        (task 25)."""
        self._refresh_video_estimate()
        if self.video_path is not None:
            self._schedule_video_run(reload=True)


    def _on_video_preset(self):
        self._apply_size_preset("video")


    def _on_video_custom_size(self):
        self._reset_size_to_custom("video")


    def _on_video_control_changed(self):
        self._on_tab_control_changed("video")


    def _on_video_zoom_changed(self, value):
        self._on_zoom_changed_for("video", value)


    # ----------------------------------------------------- strip + preview

    def _set_strip_item_dimmed(self, item, dimmed):
        """Swap a strip item's icon between its bright thumbnail
        and a dimmed copy (task 25): a dropped frame reads as
        dropped at a glance. The bright thumbnail rides in the
        item's data, so the swap never re-renders anything."""
        thumb = item.data(Qt.ItemDataRole.UserRole + 1)
        if thumb is None:
            return
        # setIcon emits itemChanged — block the strip's signals
        # so dimming never re-enters _on_strip_item_changed (that
        # recursion blew the stack: the handler sets the icon,
        # the icon change fires the handler again, forever).
        blocked = self.video_strip.signalsBlocked()
        self.video_strip.blockSignals(True)
        try:
            if dimmed:
                item.setIcon(QIcon(dimmed_pixmap(thumb)))
            else:
                item.setIcon(QIcon(thumb))
        finally:
            self.video_strip.blockSignals(blocked)


    def _rebuild_strip(self):
        if len(self.video_order) != len(self.video_frames):
            # A stale order (or none yet) — natural order.
            self.video_order = list(range(len(self.video_frames)))
        self.video_strip.blockSignals(True)
        try:
            self.video_strip.clear()
            # In strip order (task 25), not natural order — the
            # label keeps the ORIGINAL frame number, so a moved
            # frame is still identifiable after a drag.
            for index in self.video_order:
                frame = self.video_frames[index]
                thumb = thumb_pixmap(frame, STRIP_THUMB)
                item = QListWidgetItem(thumb, "Frame %03d" % (index + 1))
                item.setData(Qt.ItemDataRole.UserRole, index)
                item.setData(Qt.ItemDataRole.UserRole + 1, thumb)
                item.setFlags(item.flags()
                              | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if self.video_keep[index]
                    else Qt.CheckState.Unchecked)
                self._set_strip_item_dimmed(
                    item, not self.video_keep[index])
                self.video_strip.addItem(item)
        finally:
            self.video_strip.blockSignals(False)


    def _kept_indices(self):
        """Kept frame indices, in STRIP order — a reordered strip
        reorders the frame export, the sheet, and the playback
        (task 25)."""
        if len(self.video_order) != len(self.video_frames):
            return [i for i, keep in enumerate(self.video_keep)
                    if keep]
        return [i for i in self.video_order if self.video_keep[i]]


    def _on_strip_reordered(self, _parent, _start, _end,
                            _destination, _row):
        """The strip was drag-reordered — record the new strip
        order (task 25). The frame labels keep their original
        numbers; only the order changes."""
        self.video_order = [
            self.video_strip.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.video_strip.count())]


    def _on_strip_selection_changed(self):
        """Keep/Drop selected need a selection to act on."""
        has_selection = bool(self.video_strip.selectedItems())
        self.video_keep_selected_btn.setEnabled(has_selection)
        self.video_drop_selected_btn.setEnabled(has_selection)


    def _refresh_video_preview(self):
        if not self.video_frames:
            return
        frame = self.video_frames[self.video_current]
        if self.video_onion_check.isChecked():
            frame = self._onion_frame()
        pixmap = gui_module.checkerboard_pixmap(frame, self.video_zoom_slider.value(),
                                     smooth=False)
        self.video_preview_label.setPixmap(pixmap)
        kept = self._kept_indices()
        self.video_counter_label.setText(
            "Frame %d of %d (%d kept)"
            % (self.video_current + 1, len(self.video_frames),
               len(kept)))
        # The scrubber tracks the current frame — signals blocked,
        # because the slider's own handler pauses playback, and
        # playback must not pause itself. Its range is the kept
        # list; a dropped frame clicked in the strip is not in it
        # and reads as position 0.
        self.video_scrub_slider.blockSignals(True)
        try:
            self.video_scrub_slider.setRange(0, max(0, len(kept) - 1))
            position = (kept.index(self.video_current)
                        if self.video_current in kept else 0)
            self.video_scrub_slider.setValue(position)
        finally:
            self.video_scrub_slider.blockSignals(False)
        # The duration spin shows the current frame's duration —
        # signals blocked, or setting it would write back.
        self.video_duration_spin.blockSignals(True)
        try:
            self.video_duration_spin.setValue(
                self._video_frame_duration(self.video_current))
        finally:
            self.video_duration_spin.blockSignals(False)


    def _onion_frame(self):
        """The current frame with the previous kept frame ghosted
        behind it (task 34) — the preview only; exports use the
        plain frames. The first kept frame has no previous frame,
        so it shows plain, and so does a dropped frame clicked in
        the strip."""
        kept = self._kept_indices()
        if self.video_current not in kept:
            return self.video_frames[self.video_current]
        position = kept.index(self.video_current)
        if position == 0:
            return self.video_frames[self.video_current]
        return onion_blend(self.video_frames[self.video_current],
                           self.video_frames[kept[position - 1]])


    # --------------------------------------------- timeline: durations

    def _video_default_duration(self):
        """Milliseconds per frame at the Sheet fps (8 fps ->
        125 ms) — the duration a new import starts every frame
        at (task 34)."""
        return duration_for_fps(self.sheet_fps_spin.value())


    def _video_frame_duration(self, index):
        """The duration of one frame, in milliseconds — the stored
        one, or the Sheet fps default when no duration was ever
        stored for it."""
        if 0 <= index < len(self.video_durations):
            return self.video_durations[index]
        return self._video_default_duration()


    def _kept_durations(self):
        """Durations of the kept frames, in strip order — parallel
        to _kept_indices, for the sheet metadata and GIF export."""
        return [self._video_frame_duration(index)
                for index in self._kept_indices()]


    def _on_video_duration_changed(self, value):
        """The duration spin changed — the CURRENT frame's
        duration, and nothing else (task 34). No re-pixelize, no
        worker: timing is metadata, not pixels."""
        if not self.video_frames:
            return
        if len(self.video_durations) != len(self.video_frames):
            self.video_durations = [self._video_default_duration()] \
                * len(self.video_frames)
        self.video_durations[self.video_current] = value
        if self.video_timer.isActive():
            # The frame now showing just got longer or shorter —
            # restart its own interval so the change lands at once.
            self.video_timer.start(value)


    def _on_video_scrub(self, value):
        """The scrubber moved — pause playback and jump to that
        kept frame (task 34). Playback resumes only when Play is
        pressed again, never on its own."""
        kept = self._kept_indices()
        if not kept:
            return
        self._stop_playback()
        self.video_current = kept[max(0, min(value, len(kept) - 1))]
        self._refresh_video_preview()


    # ------------------------------------------------- timeline: tags

    def _video_tag_label(self, tag):
        """How a tag reads in the list — 1-based, what the strip
        shows ("walk — frames 1–4")."""
        return "%s — frames %d–%d" % (tag["name"], tag["from"] + 1,
                                     tag["to"] + 1)


    def _refresh_video_tag_list(self):
        """Rebuild the tag list from video_tags, keeping the
        selection when the picked tag still exists."""
        selected = self._selected_video_tag()
        self.video_tag_list.blockSignals(True)
        try:
            self.video_tag_list.clear()
            for tag in self.video_tags:
                self.video_tag_list.addItem(
                    self._video_tag_label(tag))
        finally:
            self.video_tag_list.blockSignals(False)
        if selected is not None:
            for row, tag in enumerate(self.video_tags):
                if tag["name"] == selected["name"]:
                    self.video_tag_list.setCurrentRow(row)
                    break
        self._on_video_tag_selected()
        self._mark_autosave_dirty()


    def _selected_video_tag(self):
        """The picked tag, or None when nothing is picked."""
        row = self.video_tag_list.currentRow()
        if 0 <= row < len(self.video_tags):
            return self.video_tags[row]
        return None


    def _on_video_tag_selected(self):
        """Play tag and Delete tag need a picked tag."""
        picked = self._selected_video_tag() is not None
        self.video_tag_play_btn.setEnabled(picked)
        self.video_tag_delete_btn.setEnabled(picked)


    def _add_video_tag(self, name, start, end):
        """Add a tag after validating it (task 34): the name must
        be non-empty text and unique, and the range must sit
        inside the kept frames. Returns True when the tag landed;
        on failure shows a plain warning and returns False. The
        dialog handler and the tests both come through here."""
        kept = len(self._kept_indices())
        candidate = {"name": name, "from": start, "to": end}
        try:
            validate_tags(self.video_tags + [candidate], kept)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not add tag", str(exc))
            return False
        self.video_tags.append(candidate)
        self._refresh_video_tag_list()
        self.statusBar().showMessage(
            "Added tag %s" % self._video_tag_label(candidate))
        return True


    def _on_video_tag_add(self):
        kept = len(self._kept_indices())
        if not kept:
            return
        dialog = TagDialog(self, kept)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, start, end = dialog.tag_values()
        self._add_video_tag(name, start, end)


    def _on_video_tag_delete(self):
        tag = self._selected_video_tag()
        if tag is None:
            return
        self.video_tags = [other for other in self.video_tags
                           if other is not tag]
        if self.video_play_tag is tag:
            # The playing tag is gone — playback carries on over
            # all kept frames from the current frame.
            self.video_play_tag = None
        self._refresh_video_tag_list()
        self.statusBar().showMessage("Deleted tag %s" % tag["name"])


    def _on_video_tag_play(self):
        tag = self._selected_video_tag()
        if tag is None:
            return
        kept = self._kept_indices()
        start = max(0, tag["from"])
        end = min(tag["to"], len(kept) - 1)
        if start > end:
            return
        self.video_play_tag = tag
        self.video_current = kept[start]
        self._refresh_video_preview()
        self.video_timer.start(
            self._video_frame_duration(self.video_current))
        self.video_play_btn.setText("Pause")


    def _clamp_video_tags(self):
        """Keep the tags inside the kept list after a keep/drop
        change (task 34): a tag that runs past the end is
        shortened; one that falls entirely outside is dropped,
        and the status bar says so. A reorder needs no clamp —
        tags are positions, and positions survive a reorder."""
        kept = len(self._kept_indices())
        surviving = []
        dropped = []
        for tag in self.video_tags:
            if tag["from"] >= kept:
                dropped.append(tag)
                continue
            tag["to"] = min(tag["to"], kept - 1)
            surviving.append(tag)
        if len(surviving) != len(self.video_tags):
            self.video_tags = surviving
            if (self.video_play_tag is not None
                    and self.video_play_tag not in surviving):
                self.video_play_tag = None
            self._refresh_video_tag_list()
            if dropped:
                self.statusBar().showMessage(
                    "Dropped %s — no frames left in its range"
                    % ", ".join(tag["name"] for tag in dropped))


    def _update_video_info(self):
        if not self.video_frames:
            return
        first = self.video_frames[0]
        self.video_info_label.setText(
            "%d frames, %d x %d, %d kept"
            % (len(self.video_frames), first.width(), first.height(),
               len(self._kept_indices())))


    def _on_strip_item_clicked(self, item):
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self.video_current = index
            self._refresh_video_preview()


    def _on_strip_item_changed(self, item):
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        self.video_keep[index] = (
            item.checkState() == Qt.CheckState.Checked)
        # A checkbox click is one undo step too (task 30): only
        # this frame changed, so the "before" is the current set
        # with this frame flipped back. Signals are blocked
        # during strip rebuilds and bulk keep/drop, so this only
        # fires for a real click.
        if not self._undo_suppress:
            after = list(self.video_keep)
            before = list(after)
            before[index] = not after[index]
            self._push_undo(_KeepDropCommand(
                self, before, after,
                "%s frame %d" % ("Keep" if after[index] else "Drop",
                                 index + 1)))
        self._set_strip_item_dimmed(item, not self.video_keep[index])
        kept = self._kept_indices()
        self._update_video_buttons()
        if not kept:
            self._stop_playback()
        elif self.video_current not in kept:
            self.video_current = kept[0]
        self._clamp_video_tags()
        self._refresh_video_preview()
        self._update_video_info()


    def _update_video_buttons(self):
        """Enable the Video tab's buttons and timeline controls
        for the current frames — one place, so a control cannot be
        enabled in one path and forgotten in another (task 34).
        Exports and the timeline need kept frames; Keep/Drop all
        need frames at all; Play tag and Delete tag need a picked
        tag (see _on_video_tag_selected)."""
        kept = bool(self._kept_indices())
        has_frames = bool(self.video_frames)
        for button in (self.video_export_btn, self.video_play_btn,
                       self.sheet_preview_btn, self.sheet_export_btn,
                       self.sheet_library_btn, self.video_gif_btn,
                       self.engine_export_btn):
            button.setEnabled(kept)
        for button in (self.video_keep_all_btn,
                       self.video_drop_all_btn):
            button.setEnabled(has_frames)
        self.video_tag_add_btn.setEnabled(kept)
        self.video_duration_spin.setEnabled(kept)
        self.video_scrub_slider.setEnabled(kept)
        self.video_onion_check.setEnabled(kept)
        self._on_video_tag_selected()


    def _refresh_after_keep_change(self):
        """Buttons, playback, and preview after any keep/drop
        change — shared by Keep/Drop all and Keep/Drop selected."""
        kept = self._kept_indices()
        self._update_video_buttons()
        if not kept:
            self._stop_playback()
        else:
            self.video_current = kept[0]
        self._clamp_video_tags()
        self._refresh_video_preview()
        self._update_video_info()
        self._mark_autosave_dirty()


    def _set_all_keep(self, keep):
        self.video_keep = [keep] * len(self.video_frames)
        self.video_strip.blockSignals(True)
        try:
            for row in range(self.video_strip.count()):
                item = self.video_strip.item(row)
                item.setCheckState(
                    Qt.CheckState.Checked if keep
                    else Qt.CheckState.Unchecked)
                self._set_strip_item_dimmed(item, not keep)
        finally:
            self.video_strip.blockSignals(False)
        self._refresh_after_keep_change()


    def _on_keep_all(self):
        self._record_keep_drop("Keep all frames",
                               lambda: self._set_all_keep(True))


    def _on_drop_all(self):
        self._record_keep_drop("Drop all frames",
                               lambda: self._set_all_keep(False))


    def _record_keep_drop(self, text, change):
        """Run a keep/drop change as one undo step (task 30):
        the kept set before and after, pushed only when it
        actually changed. A no-op during an undo/redo."""
        if self._undo_suppress:
            change()
            return
        before = list(self.video_keep)
        change()
        after = list(self.video_keep)
        if before != after:
            self._push_undo(_KeepDropCommand(
                self, before, after, text))


    def _set_selected_keep(self, keep):
        """Keep or drop just the selected frames (task 25)."""
        selected = self.video_strip.selectedItems()
        if not selected:
            return
        self.video_strip.blockSignals(True)
        try:
            for item in selected:
                index = item.data(Qt.ItemDataRole.UserRole)
                if index is None:
                    continue
                self.video_keep[index] = keep
                item.setCheckState(
                    Qt.CheckState.Checked if keep
                    else Qt.CheckState.Unchecked)
                self._set_strip_item_dimmed(item, not keep)
        finally:
            self.video_strip.blockSignals(False)
        self._refresh_after_keep_change()


    def _on_keep_selected(self):
        self._record_keep_drop(
            "Keep selected frames",
            lambda: self._set_selected_keep(True))


    def _on_drop_selected(self):
        self._record_keep_drop(
            "Drop selected frames",
            lambda: self._set_selected_keep(False))


    # -------------------------------------------------------- playback

    def _playback_range(self):
        """The kept indices playback walks (task 34): the playing
        tag's slice of the kept list, or all kept frames when no
        tag is playing. A tag that no longer fits the kept list
        (frames dropped since it was made) is clamped, not
        trusted — a range that clamps away entirely plays
        everything instead."""
        kept = self._kept_indices()
        tag = self.video_play_tag
        if tag is None:
            return kept
        start = max(0, tag["from"])
        end = min(tag["to"], len(kept) - 1)
        if start > end:
            return kept
        return kept[start:end + 1]


    def _on_play_pause(self):
        if self.video_timer.isActive():
            self._stop_playback()
            return
        kept = self._kept_indices()
        if not kept:
            return
        # Play means the whole animation, from the current frame:
        # a playing tag is cleared, and "Play tag" is how a tag is
        # played. Each frame shows for its own duration.
        self.video_play_tag = None
        if self.video_current not in kept:
            self.video_current = kept[0]
        self.video_timer.start(
            self._video_frame_duration(self.video_current))
        self.video_play_btn.setText("Pause")


    def _stop_playback(self):
        if self.video_timer.isActive():
            self.video_timer.stop()
        self.video_play_btn.setText("Play")


    def _on_play_fps_changed(self):
        """Preview fps changed — every frame takes the new speed
        (task 34). The fps is the base speed; the per-frame
        duration spin fine-tunes single frames on top of it. A
        playing preview re-arms on the current frame's new
        duration at once."""
        if not self.video_frames:
            return
        duration = duration_for_fps(self.video_play_fps_spin.value())
        self.video_durations = [duration] * len(self.video_frames)
        self._refresh_video_preview()
        if self.video_timer.isActive():
            self.video_timer.start(duration)


    def _on_sheet_fps_changed(self):
        """Sheet fps changed — every frame takes the new speed
        (task 34), the same as the Preview fps. The sheet fps is
        the base speed for the sheet: without this reset, the
        metadata would say fps 12 while every duration_ms still
        said 125, and a game reading one would disagree with a
        game reading the other."""
        if not self.video_frames:
            return
        duration = duration_for_fps(self.sheet_fps_spin.value())
        self.video_durations = [duration] * len(self.video_frames)
        self._refresh_video_preview()
        if self.video_timer.isActive():
            self.video_timer.start(duration)


    def _advance_video_frame(self):
        """One playback step: the next frame in the playback
        range, wrapping when Loop is on, stopping at the last one
        when it is off. The step is by POSITION in the range, not
        by frame number — comparing frame numbers played the
        original order after a strip reorder, not the strip order
        (found while building task 34: the export and the sheet
        followed the strip, the playback did not). Each frame
        shows for its own duration: the timer is re-armed from
        the frame it just landed on, so a long duration holds
        the frame."""
        playing = self._playback_range()
        if not playing:
            self._stop_playback()
            return
        if self.video_current in playing:
            position = playing.index(self.video_current)
        else:
            position = -1
        if position + 1 < len(playing):
            self.video_current = playing[position + 1]
        elif self.video_loop_check.isChecked():
            self.video_current = playing[0]
        else:
            self.video_current = playing[-1]
            self._stop_playback()
            self._refresh_video_preview()
            return
        if self.video_timer.isActive():
            self.video_timer.start(
                self._video_frame_duration(self.video_current))
        self._refresh_video_preview()


    # --------------------------------------------------------- export

    def export_frames(self, directory):
        """Write the kept frames as frame_001.png, ... into
        directory. Returns True on success; on failure shows a
        message box and returns False."""
        kept = self._kept_indices()
        if not kept:
            return False
        out = Path(directory)
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            gui_module.warn_dialog(self, "Could not export frames",
                                "could not create %s: %s" % (out, exc))
            return False
        for number, index in enumerate(kept, start=1):
            dest = out / ("frame_%03d.png" % number)
            if not self.video_frames[index].save(str(dest), "PNG"):
                gui_module.warn_dialog(self, "Could not export frames",
                                    "could not write: %s" % dest)
                return False
        self.statusBar().showMessage(
            "Exported %d frames to %s" % (len(kept), out.name))
        return True


    def _frames_export_targets(self, directory):
        """The frame_001.png, ... paths export_frames would write."""
        return [Path(directory) / ("frame_%03d.png" % number)
                for number in range(1, len(self._kept_indices()) + 1)]


    def _on_export_frames(self):
        if not self._kept_indices():
            return
        # Next to the video, else the last-used export folder
        # (task 22) — the old "" opened wherever Qt felt like.
        if self.video_path:
            start_dir = str(Path(self.video_path).parent)
        else:
            start_dir = self._last_export_dir() or ""
        directory = QFileDialog.getExistingDirectory(
            self, "Export frames to folder", start_dir)
        if not directory:
            return
        existing = [p for p in self._frames_export_targets(directory)
                    if p.exists()]
        if existing:
            # Never silently overwrite: a folder that already holds
            # frame_001.png etc. asks first (review B15).
            # Default No — declining leaves the files untouched.
            names = ", ".join(p.name for p in existing[:3])
            if len(existing) > 3:
                names += ", …"
            # One file or many, the sentence agrees with the
            # count — never a parenthesized s.
            if len(existing) == 1:
                question = ("1 frame file already exists in %s (%s). "
                            "Overwrite it?"
                            % (Path(directory).name, names))
            else:
                question = ("%d frame files already exist in %s (%s). "
                            "Overwrite them?"
                            % (len(existing), Path(directory).name,
                               names))
            answer = QMessageBox.question(
                self, "Overwrite frames?", question,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self.export_frames(directory):
            self._remember_export_dir(directory)


    # ---------------------------------------------------- spritesheet

    def _sheet_settings(self):
        """(columns, padding, fps) from the Spritesheet controls."""
        return (self.sheet_cols_combo.currentData(),
                self.sheet_padding_spin.value(),
                self.sheet_fps_spin.value())


    def _sheet_pack_settings(self):
        """(pack, trim, atlas padding, extrude) from the atlas
        packing controls."""
        return (self.sheet_pack_combo.currentData(),
                self.sheet_trim_check.isChecked(),
                self.sheet_atlas_padding_spin.value(),
                self.sheet_extrude_spin.value())


    def _on_sheet_pack_changed(self):
        """The packing combo changed — the grid controls
        (columns, padding) apply to Grid, the atlas controls
        (trim, atlas padding, extrude) to MaxRects. Each set
        stays disabled under the other packer, so a control
        never looks live while its value is ignored."""
        maxrects = (self.sheet_pack_combo.currentData()
                    == "maxrects")
        self.sheet_cols_combo.setEnabled(not maxrects)
        self.sheet_padding_spin.setEnabled(not maxrects)
        self.sheet_trim_check.setEnabled(maxrects)
        self.sheet_atlas_padding_spin.setEnabled(maxrects)
        self.sheet_extrude_spin.setEnabled(maxrects)


    def _kept_sheet_frames(self):
        """Kept frames as (images, names) for packing — the names
        are the export names (frame_001.png, ...), renumbered from
        1, so the sheet's metadata matches the frame export."""
        kept = self._kept_indices()
        images = [self.video_frames[i] for i in kept]
        names = ["frame_%03d.png" % (number + 1)
                 for number in range(len(kept))]
        return images, names


    def _sheet_default_name(self):
        if self.video_path:
            src = Path(self.video_path)
            return str(src.with_name(src.stem + "-sheet.png"))
        # No video (a sheet from the preview dialog with no source
        # in hand): the last-used export folder, else "sheet.png"
        # in the current directory (task 22).
        return self._export_default("sheet.png")


    def export_spritesheet(self, png_path):
        """Pack the kept frames and write the PNG + JSON pair (the
        JSON takes the PNG's name with .json). Under MaxRects
        the pair is an atlas — one PNG per sheet when the
        frames spill (stem_0.png, stem_1.png) with one JSON
        naming them all. Returns True on success; on failure
        shows a message box and returns False.
        """
        kept = self._kept_indices()
        if not kept:
            return False
        images, names = self._kept_sheet_frames()
        cols, padding, fps = self._sheet_settings()
        path = Path(png_path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        meta_path = path.with_suffix(".json")
        pack, trim, atlas_padding, extrude = \
            self._sheet_pack_settings()
        if pack == "maxrects":
            try:
                metadata, _files = write_atlas(
                    images, names, str(path), str(meta_path),
                    padding=atlas_padding, extrude=extrude,
                    trim=trim, multipack=True, fps=fps,
                    durations=self._kept_durations(),
                    tags=self.video_tags)
            except PixelForgeError as exc:
                gui_module.warn_dialog(self, "Could not export atlas", str(exc))
                return False
            deduped = metadata["deduped"]
            self.statusBar().showMessage(
                "Exported atlas: %s, %s%s"
                % (plural(len(metadata["sheets"]), "sheet"),
                   plural(len(kept), "frame"),
                   (", %d deduped" % deduped) if deduped else ""))
            return True
        try:
            metadata = write_sheet(images, names, str(path),
                                   str(meta_path), cols=cols,
                                   padding=padding, fps=fps,
                                   durations=self._kept_durations(),
                                   tags=self.video_tags)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export spritesheet", str(exc))
            return False
        self.statusBar().showMessage(
            "Exported spritesheet: %d frames, %d x %d grid, %s"
            % (len(kept), metadata["columns"], metadata["rows"],
               path.name))
        return True


    def _on_export_spritesheet(self):
        if not self._kept_indices():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export spritesheet", self._sheet_default_name(),
            "PNG images (*.png)")
        if path:
            self.export_spritesheet(path)


    def _on_preview_spritesheet(self):
        if not self._kept_indices():
            return
        images, names = self._kept_sheet_frames()
        cols, padding, fps = self._sheet_settings()
        pack, trim, atlas_padding, extrude = \
            self._sheet_pack_settings()
        try:
            dialog = SpritesheetDialog(
                self, images, names, cols, padding, fps,
                self._sheet_default_name(),
                durations=self._kept_durations(),
                tags=self.video_tags,
                pack=pack, trim=trim,
                atlas_padding=atlas_padding, extrude=extrude)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not preview spritesheet", str(exc))
            return
        dialog.exec()


    # ------------------------------------------------------- GIF export

    def _gif_default_name(self):
        if self.video_path:
            src = Path(self.video_path)
            return str(src.with_name(src.stem + "-anim.gif"))
        return self._export_default("animation.gif")


    def export_gif(self, path, tag=None):
        """Write the kept frames — or one tag's range — as an
        animated GIF with the per-frame durations, looping
        forever (task 34). Returns True on success; on failure
        shows a message box and returns False. A missing ffmpeg
        refuses loudly, the same as video import."""
        kept = self._kept_indices()
        if not kept:
            return False
        if tag is not None:
            start = max(0, tag["from"])
            end = min(tag["to"], len(kept) - 1)
            if start > end:
                return False
            kept = kept[start:end + 1]
        images = [self.video_frames[i] for i in kept]
        durations = [self._video_frame_duration(i) for i in kept]
        out = Path(path)
        if out.suffix.lower() != ".gif":
            out = out.with_suffix(".gif")
        try:
            write_gif(images, durations, str(out))
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export GIF", str(exc))
            return False
        self.statusBar().showMessage(
            "Exported GIF: %d frames, %s" % (len(kept), out.name))
        return True


    def _on_export_gif(self):
        if not self._kept_indices():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export GIF", self._gif_default_name(),
            "GIF images (*.gif)")
        if path and self.export_gif(path,
                                    self._selected_video_tag()):
            self._remember_export_dir(Path(path).parent)


    # ------------------------------------------------ engine export

    def _on_export_engine(self):
        """Open the engine export dialog for the kept frames
        (task 36): the dialog carries the frames, their
        durations, and the tags into the chosen preset. On
        accept, the status bar names what was written and the
        folder becomes the last-used export folder."""
        if not self._kept_indices():
            return
        images, names = self._kept_sheet_frames()
        cols, padding, fps = self._sheet_settings()
        default = Path(self._sheet_default_name())
        dialog = EngineExportDialog(
            self, images, names, self._kept_durations(),
            self.video_tags, cols, padding, fps,
            str(default.parent.resolve()), default.stem)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = dialog.format_key()
        files = dialog.written
        self.statusBar().showMessage(
            "Exported %s: %s" % (FORMAT_INFO[key][0],
                                 " + ".join(p.name for p in files)))
        self._remember_export_dir(Path(files[0]).parent)
