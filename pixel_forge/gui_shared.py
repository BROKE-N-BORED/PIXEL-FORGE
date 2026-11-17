"""Shared GUI pieces: helpers, workers, dialogs,
undo commands, stylesheets, and constants.

One of the gui.py split (task 58). The tab mixins
and the window import from here; this module
imports no tab module.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import (QPoint, QRect, QSettings, QSize, Qt, QThread,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont,
                           QFontDatabase, QFontMetrics, QIcon, QImage,
                           QImageReader, QKeySequence, QPainter, QPixmap,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup,
                               QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QSplitter, QStackedWidget,
                               QStatusBar, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from . import __version__
from . import bgremove
from . import gridsnap
from . import normalmap
from . import procedural
from .atlas import (DEFAULT_MAX_SIZE, DEFAULT_PADDING,
                    build_atlas_metadata, pack_atlas, write_atlas)
from .codeinput import CODE_EXTENSIONS, parse_paths
from .editor import (distinct_colors, draw_line, draw_rect_outline,
                     flood_fill, line_points, pick_color, rect_points)
from .engine_export import (ENGINE_FORMATS, FORMAT_INFO, export_engine,
                            format_files, format_note)
from .history import (batches_dir, load_history, record_batch,
                      record_prompt, save_history)
from .image import Image, argb
from .imagegen import (DEFAULT_COUNT, DEFAULT_NEGATIVE, DEFAULT_SIZE,
                       LCM_CFG, LCM_STEPS, MAX_PROMPT_LEN,
                       NEGATIVE_PRESETS, GenerationCancelled,
                       GENERATION_MIN_RAM_BYTES, MODEL_FILENAME,
                       MODEL_URL, available_ram_bytes, engine_status,
                       find_model, format_bytes, generate,
                       is_pinned_model, load_batch, new_batch,
                       regenerate_candidate, require_engine,
                       require_model, set_keeper, slugify,
                       validate_prompt)
from .layers import Layer, composite_layers, has_content
from .library import SOURCE_LABELS, Library, unreadable_reason
from .palette_io import (PaletteStore, extract_palette_from_image)
from .palettes import NAMED_PALETTES, PALETTE_LABELS
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError,
                       count_opaque_colors, load_image, pixelize_image,
                       plural)
from .autosave import (autosave_home, crashed, discard_recovery,
                       empty_recovery, load_recovery,
                       load_recovery_image, mark_clean_exit,
                       mark_running, recovery_image_path,
                       recovery_path, save_recovery,
                       save_recovery_image)
from .pngio import write_png
from .presets import (BUILTIN_PRESETS, SHEET_COLS_VALUES, PresetStore,
                      read_preset_export, validate_settings)
from .qtimage import from_qimage, to_qimage
from .session import (load_session, record_recent, save_session,
                      set_last_code, set_last_image, set_last_video)
from .skins import (detect_skin, extract_skin, pixelize_texture,
                    reassemble_skin_file, skin_figure, skin_parts,
                    texture_target_size)
from .skinview import SkinPreviewDialog
from .spritesheet import (build_metadata, duration_for_fps,
                          extract_frame, find_sibling_metadata,
                          frames_from_grid, frames_from_sheet,
                          grid_layout, load_metadata, pack_frames,
                          split_with_metadata, validate_tags,
                          write_sheet)
from .textstamp import (MAX_SCALE, MIN_SCALE, render_text,
                        stamp_image, unsupported_characters)
from .tileset import (BLOB_SHAPES, DEFAULT_BLOB_SHAPE, MODE_NAMES,
                      SHAPE_NAMES, TILESET_MODES, blob_tiles,
                      export_tileset, wang_tiles)
from .undo import Command as UndoCommand
from .undo import UndoStack
from .updates import check_for_update
from .video import (DEFAULT_FPS, FPS_PRESETS, FRAME_COUNT_CONFIRM,
                    VIDEO_EXTENSIONS, estimate_frame_count,
                    extract_frames, onion_blend, parse_time,
                    probe_duration, probe_frame_durations,
                    require_ffmpeg, write_gif)


class _GuiModuleProxy:
    """pixel_forge.gui, resolved lazily at first use.

    The tab modules cannot import pixel_forge.gui at the top
    level — gui.py imports them, so the import would be
    circular. Tests patch names on the gui module
    (mock.patch.object(gui_mod, "warn_dialog") and friends),
    so code that calls those names must resolve them through
    the gui module at call time, not through a binding taken
    at import time. This proxy does exactly that: every
    attribute read goes through sys.modules at call time, so
    a patch on pixel_forge.gui is visible to the moved code.
    """

    def __getattr__(self, name):
        import sys
        return getattr(sys.modules["pixel_forge.gui"], name)


gui_module = _GuiModuleProxy()



SIZE_PRESETS = [16, 32, 48, 64, 128]
MAX_DISPLAY = 1600  # longest side of a preview pixmap, in pixels
ORIGINAL_FIT = 352  # longest side of the Original preview, in pixels
# (each preview pane is ~360 px wide at the default window size)
MAX_CUSTOM_SIZE = 512
STRIP_THUMB = 64  # frame strip thumbnail size, in pixels
VIDEO_DEBOUNCE_MS = 300  # ms of no control changes before a Video
# tab re-extract / re-pixelize runs — a dragged slider fires
# dozens of changes, and only the settled one needs a run.
TEXTURE_DEBOUNCE_MS = 300  # same idea for the Code tab's
# texture controls (task 26): a dragged size spin fires dozens
# of changes, and only the settled one needs a re-render.
AUTOSAVE_DEBOUNCE_MS = 2000  # ms of no changes before the
# autosave writes (task 55): a dragged slider or fast typing
# marks the state dirty again and again, and only the settled
# state is written.

# Export-button contrast (task 22): the text is #e8e6e3 on the
# button background. The old orange (#b35a1b) measured 3.84:1 —
# under WCAG AA's 4.5:1 for normal text (review W19). The darker
# orange below measures 5.73:1, the hover 4.93:1 — both pass.
# (The disabled state is exempt from the ratio: WCAG does not
# require contrast for inactive controls.) This comment lives
# OUTSIDE the stylesheet string: Qt stylesheets have no # comments,
# and a # line inside the string would be parsed as a selector.
#
# Pixel-native identity (task 88, U15): square corners on
# everything (buttons, inputs, sliders, tabs, checkboxes —
# square reads as pixel-native), one accent per theme (dark:
# #e07020 for shapes and borders, #ffb066 for text), crisp
# 1 px borders, and square tabs with an accent bar on the
# selected one. No blur, no translucency, no per-frame
# painting (entry 007 Liquid Glass stays rejected for frame
# cost). Both themes style the same selector set (pinned in
# tests/test_polish.py), and every text color still clears
# the task-22 bar. The pixel-grid motif (v0.81, the rest of
# task 88) is a stylesheet background-image on the lists,
# the scroll-area viewports, and the tab columns — the
# documented Qt mechanism, proven by experiment (see
# pf-verify/v81/grid-research.md). The v0.79 attempt failed
# for two lesser reasons, not a Qt limitation: its tile path
# was relative, so the stylesheet engine could not load the
# pixmap ("Could not create pixmap" — Qt resolves url()
# against the working directory), and its scroll-area rule
# painted a viewport the tab columns' content widgets cover.
# The tile path below is therefore ABSOLUTE, and the columns
# carry the grid on the content widget itself (#column).
# The other two v0.79 dead ends stay dead for real reasons:
# a texture brush in the viewport palette is overwritten by
# QStyleSheetStyle::polish on every re-polish (first show,
# theme switch), and a stylesheet set directly on a viewport
# is refused (viewports are unstylable — their rules come
# from the container). Do not retry either.
#
# The tiles live in icons/ next to the app icons and ship in
# the release zip (like the icons, they are not in the git
# push — PNGs are excluded from the manifest). When a tile
# is missing (a fresh clone has no icons/), the url() is left
# empty and the grid simply does not render — no warning, no
# crash, everything else works.
_ICONS_DIR = Path(__file__).resolve().parent.parent / "icons"


def _grid_tile(name):
    """The absolute tile path for the stylesheet, or ""."""
    path = _ICONS_DIR / name
    return str(path) if path.is_file() else ""


_GRID_TILE_DARK = _grid_tile("grid-tile-dark.png")
_GRID_TILE_LIGHT = _grid_tile("grid-tile-light.png")
DARK_STYLESHEET = """
QMainWindow, QWidget { background-color: #232329; color: #e8e6e3; }
QLabel { background-color: transparent; }
QLabel#heading { font-weight: bold; font-size: 13px; color: #ffb066; }
QPushButton#sectionHeader { font-weight: bold; font-size: 13px; color: #ffb066; background: transparent; border: none; text-align: left; padding: 4px 0; }
QLabel#warning { color: #ffb066; }
QLabel#explanation { font-size: 12px; color: #b8b5b0; }
QLabel[overLimit="true"] { color: #ffb066; }
QPushButton { background-color: #3a3a42; border: 1px solid #55555e;
    border-radius: 0px; padding: 7px 14px; }
QPushButton:hover { background-color: #4a4a54; }
QPushButton:pressed { background-color: #2e2e35; }
QPushButton:checked { background-color: #4a4a54;
    border-color: #e07020; }
QPushButton:disabled { color: #77777f; border-color: #3a3a42; }
QPushButton#export { background-color: #8a4513; border-color: #d97a2e;
    font-weight: bold; }
QPushButton#export:hover { background-color: #9a4c15; }
QPushButton#export:disabled { background-color: #4a3226; color: #77777f; }
QComboBox, QSpinBox, QLineEdit { background-color: #2e2e35;
    border: 1px solid #55555e; border-radius: 0px; padding: 4px; }
QDoubleSpinBox { background-color: #2e2e35; border: 1px solid #55555e;
    border-radius: 0px; padding: 4px; }
QComboBox::drop-down { border: none; }
QSlider::groove:horizontal { height: 6px; background: #3a3a42;
    border-radius: 0px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -4px 0;
    background: #e07020; border-radius: 0px; }
QScrollArea { background-color: #1a1a1f; background-image: url("__GRID_TILE_DARK__"); border: 1px solid #3a3a42; }
QListWidget { background-color: #1a1a1f; background-image: url("__GRID_TILE_DARK__"); border: 1px solid #3a3a42; }
QListWidget::item:selected { background-color: #3a3a42; }
QWidget#column { background-color: #232329; background-image: url("__GRID_TILE_DARK__"); }
QWidget#section { background-color: transparent; }
QWidget#sectionBody { background-color: transparent; }
QSplitter::handle { background-color: #3a3a42; }
QStatusBar { background-color: #1a1a1f; color: #b8b5b0; }
QTabWidget::pane { border: 1px solid #3a3a42; }
QTabBar::tab { background-color: #2e2e35; border: 1px solid #3a3a42;
    border-bottom: none; padding: 8px 16px; margin-right: 2px; }
QTabBar::tab:hover { background-color: #3a3a42; }
QTabBar::tab:selected { background-color: #232329;
    border-top: 3px solid #e07020; }
QMenuBar { background-color: #1a1a1f; color: #e8e6e3; }
QMenuBar::item:selected { background-color: #3a3a42; }
QMenu { background-color: #2e2e35; border: 1px solid #55555e;
    color: #e8e6e3; }
QMenu::item:selected { background-color: #3a3a42; }
QToolTip { background-color: #1a1a1f; color: #e8e6e3;
    border: 1px solid #55555e; }
QCheckBox::indicator { width: 14px; height: 14px;
    border: 1px solid #55555e; background-color: #2e2e35; }
QCheckBox::indicator:checked { background-color: #e07020;
    border-color: #e07020; }
QProgressBar { background-color: #2e2e35; border: 1px solid #55555e;
    color: #e8e6e3; }
QProgressBar::chunk { background-color: #8a4513; }
QTextEdit { background-color: #2e2e35; border: 1px solid #55555e; }
""".replace("__GRID_TILE_DARK__", _GRID_TILE_DARK)


# Light theme (task 67): the same rules as the dark sheet, with
# the colors flipped. The export button keeps the dark orange
# background, so it carries its own light text color — the main
# text color is dark now and would vanish on the orange. Measured
# against the task-22 bar (WCAG AA 4.5:1 for normal text): main
# text 13.94:1, button text 12.03:1, export text 6.37:1, export
# hover 5.48:1, heading and warning 6.37:1, status text 6.06:1,
# input text 15.63:1, menu text 15.63:1, tab text 12.03:1,
# tooltip text 15.63:1, progress text on its chunk 5.04:1,
# list text 15.63:1, explanation text 6.58:1 (dark 7.65:1).
# Text on the grid lines themselves (v0.81): light list text
# 10.92:1, heading 4.99:1, explanation 5.15:1; dark list text
# 10.81:1, heading 7.47:1, explanation 6.59:1 — the grid can
# never push a pair under the bar.
# All pinned in tests/test_polish.py. The
# light accent is #8a4513 throughout (headings, slider, checked
# borders, selected tab bar, ticked checkboxes); the progress
# chunk is the lighter #d97a2e so the dark text stays readable
# on it.
LIGHT_STYLESHEET = """
QMainWindow, QWidget { background-color: #f2f2f0; color: #232329; }
QLabel { background-color: transparent; }
QLabel#heading { font-weight: bold; font-size: 13px; color: #8a4513; }
QPushButton#sectionHeader { font-weight: bold; font-size: 13px; color: #8a4513; background: transparent; border: none; text-align: left; padding: 4px 0; }
QLabel#warning { color: #8a4513; }
QLabel#explanation { font-size: 12px; color: #55555e; }
QLabel[overLimit="true"] { color: #8a4513; }
QPushButton { background-color: #e2e2de; border: 1px solid #b8b8b2;
    border-radius: 0px; padding: 7px 14px; }
QPushButton:hover { background-color: #d4d4cf; }
QPushButton:pressed { background-color: #c8c8c2; }
QPushButton:checked { background-color: #d4d4cf;
    border-color: #8a4513; }
QPushButton:disabled { color: #9a9a94; border-color: #d4d4cf; }
QPushButton#export { background-color: #8a4513; border-color: #d97a2e;
    color: #f2f2f0; font-weight: bold; }
QPushButton#export:hover { background-color: #9a4c15; }
QPushButton#export:disabled { background-color: #d8c8bb; color: #9a9a94; }
QComboBox, QSpinBox, QLineEdit { background-color: #ffffff;
    border: 1px solid #b8b8b2; border-radius: 0px; padding: 4px; }
QDoubleSpinBox { background-color: #ffffff; border: 1px solid #b8b8b2;
    border-radius: 0px; padding: 4px; }
QComboBox::drop-down { border: none; }
QSlider::groove:horizontal { height: 6px; background: #d4d4cf;
    border-radius: 0px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -4px 0;
    background: #8a4513; border-radius: 0px; }
QScrollArea { background-color: #e9e9e6; background-image: url("__GRID_TILE_LIGHT__"); border: 1px solid #d4d4cf; }
QListWidget { background-color: #ffffff; background-image: url("__GRID_TILE_LIGHT__"); border: 1px solid #d4d4cf; }
QListWidget::item:selected { background-color: #d4d4cf; }
QWidget#column { background-color: #f2f2f0; background-image: url("__GRID_TILE_LIGHT__"); }
QWidget#section { background-color: transparent; }
QWidget#sectionBody { background-color: transparent; }
QSplitter::handle { background-color: #d4d4cf; }
QStatusBar { background-color: #e9e9e6; color: #55555e; }
QTabWidget::pane { border: 1px solid #d4d4cf; }
QTabBar::tab { background-color: #e2e2de; border: 1px solid #d4d4cf;
    border-bottom: none; padding: 8px 16px; margin-right: 2px; }
QTabBar::tab:hover { background-color: #d4d4cf; }
QTabBar::tab:selected { background-color: #f2f2f0;
    border-top: 3px solid #8a4513; }
QMenuBar { background-color: #e9e9e6; color: #232329; }
QMenuBar::item:selected { background-color: #d4d4cf; }
QMenu { background-color: #ffffff; border: 1px solid #b8b8b2;
    color: #232329; }
QMenu::item:selected { background-color: #d4d4cf; }
QToolTip { background-color: #ffffff; color: #232329;
    border: 1px solid #b8b8b2; }
QCheckBox::indicator { width: 14px; height: 14px;
    border: 1px solid #b8b8b2; background-color: #ffffff; }
QCheckBox::indicator:checked { background-color: #8a4513;
    border-color: #8a4513; }
QProgressBar { background-color: #ffffff; border: 1px solid #b8b8b2;
    color: #232329; }
QProgressBar::chunk { background-color: #d97a2e; }
QTextEdit { background-color: #ffffff; border: 1px solid #b8b8b2; }
""".replace("__GRID_TILE_LIGHT__", _GRID_TILE_LIGHT)


THEMES = {"dark": DARK_STYLESHEET, "light": LIGHT_STYLESHEET}


_IMAGE_EXTENSIONS = None


def warn_dialog(parent, title, message):
    """A warning dialog whose title names the failure (task 22).

    The title says what failed — never the generic "Pixel Forge"
    — and the message starts with a capital letter. Exception
    text is usually lowercase, so the first letter is raised
    here rather than at every call site.
    """
    text = str(message)
    if text:
        text = text[0].upper() + text[1:]
    QMessageBox.warning(parent, title, text)


def image_extensions():
    """Extensions QImage can read, as lowercase .ext strings —
    the same set supported_filter() offers, for drag-and-drop."""
    global _IMAGE_EXTENSIONS
    if _IMAGE_EXTENSIONS is None:
        _IMAGE_EXTENSIONS = {
            ".%s" % fmt.data().decode("ascii").lower()
            for fmt in QImageReader.supportedImageFormats()}
    return _IMAGE_EXTENSIONS


def supported_filter():
    """A QFileDialog filter built from what QImage can actually read."""
    formats = sorted(
        fmt.data().decode("ascii")
        for fmt in QImageReader.supportedImageFormats())
    patterns = " ".join("*.%s" % fmt for fmt in formats)
    return "Images (%s);;All files (*)" % patterns


def video_filter():
    """A QFileDialog filter for the video extensions ffmpeg reads."""
    patterns = " ".join("*%s" % ext for ext in sorted(VIDEO_EXTENSIONS))
    return "Videos (%s);;All files (*)" % patterns


def code_filter():
    """A QFileDialog filter for the code files Pixel Forge parses."""
    patterns = " ".join("*%s" % ext for ext in sorted(CODE_EXTENSIONS))
    return "Code (%s);;All files (*)" % patterns


def checkerboard_pixmap(image, scale, smooth):
    """Scale an image and composite it over a checkerboard so
    transparency reads clearly in the preview.

    Takes a buffer Image (pixel_forge.image.Image) — the core's
    type — and converts to QImage at the display boundary, so the
    smooth/fast scaling stays Qt's exactly as before.
    """
    width = max(1, int(image.width() * scale))
    height = max(1, int(image.height() * scale))
    longest = max(width, height)
    if longest > MAX_DISPLAY:
        factor = MAX_DISPLAY / longest
        width = max(1, int(width * factor))
        height = max(1, int(height * factor))
    mode = (Qt.TransformationMode.SmoothTransformation if smooth else
            Qt.TransformationMode.FastTransformation)
    scaled = to_qimage(image).scaled(
        width, height, Qt.AspectRatioMode.IgnoreAspectRatio, mode)
    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(canvas)
    cell = 12
    light = Qt.GlobalColor.lightGray
    dark = Qt.GlobalColor.darkGray
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            color = light if ((x // cell) + (y // cell)) % 2 == 0 else dark
            painter.fillRect(x, y, cell, cell, color)
    painter.drawImage(0, 0, scaled)
    painter.end()
    return QPixmap.fromImage(canvas)


def thumb_pixmap(image, size):
    """A list-thumbnail pixmap for a buffer Image, fitted inside
    a size x size box, nearest-neighbour (the same Qt scaling the
    strips used when the core held QImages)."""
    return QPixmap.fromImage(to_qimage(image).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation))


def dimmed_pixmap(pixmap):
    """A copy of a pixmap drawn at ~35% opacity — a dropped
    frame's thumbnail (task 25), so kept and dropped read
    apart at a glance."""
    dimmed = QPixmap(pixmap.size())
    dimmed.fill(Qt.GlobalColor.transparent)
    painter = QPainter(dimmed)
    painter.setOpacity(0.35)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return dimmed


class _PixelCanvas(QLabel):
    """The Image tab's pixel preview, clickable for editing
    (task 33).

    A plain QLabel showing the checkboarded result pixmap, with
    left-button mouse events forwarded to the window as
    image-pixel coordinates. The pixmap is centered in the label
    (AlignCenter) and scaled by the zoom slider — capped at
    MAX_DISPLAY on its longest side — so the mapping is computed
    from the actual pixmap size, never assumed from the zoom
    value.
    """

    def __init__(self, window):
        super().__init__("Your pixel sprite appears here")
        self._window = window
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)

    def _pixmap_origin(self):
        """Where the pixmap's top-left corner sits inside the
        label, or None when no pixmap is set."""
        pixmap = self.pixmap()
        if pixmap is None:
            return None
        return ((self.width() - pixmap.width()) // 2,
                (self.height() - pixmap.height()) // 2)

    def pixel_at(self, pos):
        """The image pixel under a widget position, or None when
        the position is off the image (or no image is showing)."""
        image = self._window.result_image
        pixmap = self.pixmap()
        if image is None or pixmap is None:
            return None
        origin = self._pixmap_origin()
        x = int((pos.x() - origin[0]) * image.width()
                / pixmap.width())
        y = int((pos.y() - origin[1]) * image.height()
                / pixmap.height())
        if 0 <= x < image.width() and 0 <= y < image.height():
            return (x, y)
        return None

    def pos_for_pixel(self, x, y):
        """The widget position at the center of one image pixel
        — the inverse of pixel_at, for tests and proof scripts."""
        image = self._window.result_image
        pixmap = self.pixmap()
        if image is None or pixmap is None:
            return QPoint(0, 0)
        origin = self._pixmap_origin()
        px = origin[0] + (x + 0.5) * pixmap.width() / image.width()
        py = origin[1] + (y + 0.5) * pixmap.height() / image.height()
        return QPoint(int(px), int(py))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._window._canvas_press(
                self.pixel_at(event.position().toPoint()))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._window._canvas_move(
                self.pixel_at(event.position().toPoint()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._window._canvas_release(
                self.pixel_at(event.position().toPoint()))
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class PromptWorker(QThread):
    """Runs a prompt generation off the GUI thread.

    A 1–5 minute CPU generation must never freeze the window —
    the video tab's worker (VideoWorker) follows the same
    pattern for extraction + pixelize. Signals cross back
    to the GUI thread; the worker never touches a widget itself.
    (Named `done`, not `finished` — QThread already owns `finished`.)

    Two modes (task 20): "generate" runs a full round into the
    batch; "regenerate" re-rolls one candidate (`target`, a file
    name) and emits the updated batch. The GUI slots read
    `mode` off the sender to tell them apart — a re-roll must
    not auto-pick the first candidate as keeper the way a fresh
    batch does.
    """
    progress = Signal(int, int, dict)  # done, total, candidate record
    done = Signal(dict)                # the finished batch
    failed = Signal(str)               # readable error message
    cancelled = Signal()

    def __init__(self, batch_dir, parent=None, mode="generate",
                 target=None):
        super().__init__(parent)
        self.batch_dir = Path(batch_dir)
        self.mode = mode
        self.target = target
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        try:
            if self.mode == "regenerate":
                record = regenerate_candidate(
                    self.batch_dir, self.target,
                    cancel_event=self._cancel_event)
                self.progress.emit(1, 1, record)
                batch = load_batch(self.batch_dir)
            else:
                batch = generate(
                    self.batch_dir,
                    cancel_event=self._cancel_event,
                    progress=lambda done, total, record:
                        self.progress.emit(done, total, record))
        except GenerationCancelled:
            self.cancelled.emit()
            return
        except PixelForgeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            self.failed.emit("generation failed: %s" % exc)
            return
        self.done.emit(batch)


class TextureWorker(QThread):
    """Pixelizes a game-asset texture off the GUI thread.

    A big photo as a texture used to freeze the window for
    minutes (209 s for a 4000x3000 image at native size). The
    512 px cap in pixelize_texture bounds the work to seconds,
    and the worker keeps the window responsive while it runs.
    Signals cross back to the GUI thread; the worker never
    touches a widget itself. Only a buffer Image crosses the
    thread boundary (QPixmap is not thread-safe) — a plain
    Python object, safer to pass than a QImage was.

    Settings arrive as one dict (width, height, palette,
    max_colors, dither), snapshotted at start like the Video
    worker's — a later control change cannot rewrite the run
    in flight, it starts a newer run instead, and the stale
    guard in _on_texture_done drops the older result. The
    settings ride back in the result too, so the detail line
    describes the render that actually landed, not the
    controls as they stand when it lands.
    """
    done = Signal(dict)   # path, source size, texture, color count
    failed = Signal(str)  # readable error message

    def __init__(self, image, path, settings=None, parent=None):
        super().__init__(parent)
        self.image = image
        self.path = str(path)
        self.settings = dict(settings) if settings else {}

    def run(self):
        try:
            texture = pixelize_texture(self.image, **self.settings)
            colors = count_opaque_colors(texture)
        except PixelForgeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            self.failed.emit("could not load texture: %s" % exc)
            return
        self.done.emit({
            "path": self.path,
            "source_width": self.image.width(),
            "source_height": self.image.height(),
            "image": texture,
            "colors": colors,
            "settings": self.settings,
        })


class VideoWorker(QThread):
    """Extracts and pixelizes video frames off the GUI thread.

    Every Video tab control change used to re-run ffmpeg
    extraction + the pixelize pass synchronously — ~10 s per
    extract-setting change on a 30 s clip, the window frozen
    solid on every slider move. The worker does both in the
    background; the debounce timer in MainWindow makes sure
    rapid control changes run once, with the settled settings.
    Only buffer Images cross the thread boundary (QPixmap is
    not thread-safe) — plain Python objects, safer to pass
    than QImages were.

    Settings arrive as one dict so a run snapshots the
    controls at start: a later control change cannot rewrite
    the run in flight — it starts a newer run instead, and the
    stale guard in _on_video_run_done drops the older result.
    Two modes: "reload" extracts with ffmpeg then pixelizes
    (owns a fresh temp dir, handed over in the result), and
    "repixelize" pixelizes the raw frames it was handed.
    """
    done = Signal(dict)   # mode, frames, raw, raw_dir, fps, fresh,
                          # path, durations
    failed = Signal(str)  # readable error message

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)

    def run(self):
        settings = self.settings
        tmp = None
        durations = None
        try:
            if settings["mode"] == "reload":
                tmp = tempfile.mkdtemp(prefix="pixel-forge-frames-")
                raw = extract_frames(
                    settings["path"], tmp,
                    fps=settings["fps"], every=settings["every"],
                    start=settings["start"], end=settings["end"])
                # Task 83: a GIF's own per-frame delays, probed
                # from the file — the GUI applies them when the
                # count matches the extracted frames, and falls
                # back to the Sheet fps default otherwise. A
                # failed probe is None, never an error.
                if Path(settings["path"]).suffix.lower() == ".gif":
                    durations = gui_module.probe_frame_durations(
                        settings["path"])
            else:
                raw = settings["raw_frames"]
            frames = [gui_module.pixelize_image(
                gui_module.load_image(raw_path),
                width=settings["width"], height=settings["height"],
                keep_aspect=settings["keep_aspect"],
                palette=settings["palette"],
                max_colors=settings["max_colors"],
                dither=settings["dither"],
                palettes=settings.get("palettes"))
                for raw_path in raw]
        except PixelForgeError as exc:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
            self.failed.emit("video failed: %s" % exc)
            return
        self.done.emit({
            "mode": settings["mode"],
            "frames": frames,
            "raw": list(raw),
            "raw_dir": tmp,
            "fps": settings["fps"],
            "fresh": settings.get("fresh", False),
            "path": settings.get("path"),
            "durations": durations,
        })


class BgRemoveWorker(QThread):
    """Removes the background from one image off the GUI thread.

    A CPU removal takes seconds to about a minute, and the model
    load alone is seconds — far too long to run on the GUI
    thread. The worker writes the source to a temp PNG, runs
    the removal in a subprocess (bgremove.remove_background_file),
    and reads the result back. Only a buffer Image crosses the
    thread boundary (QPixmap is not thread-safe) — a plain
    Python object, like the Video worker's frames. Cancel kills
    the subprocess at once; the source image is carried along
    so the done slot can tell a stale result from the current
    image (a new import mid-run must not be overwritten by the
    old image's result).
    """
    done = Signal(dict)   # image, source
    failed = Signal(str)  # readable error message
    cancelled = Signal()

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        tmp = tempfile.mkdtemp(prefix="pixel-forge-bgremove-")
        src = os.path.join(tmp, "source.png")
        dst = os.path.join(tmp, "result.png")
        try:
            write_png(self.image, src)
            bgremove.remove_background_file(
                src, dst, cancel_event=self._cancel_event)
            result = gui_module.load_image(dst)
        except bgremove.RemovalCancelled:
            self.cancelled.emit()
            return
        except PixelForgeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            self.failed.emit("background removal failed: %s" % exc)
            return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.done.emit({"image": result, "source": self.image})


class GridSnapWorker(QThread):
    """Snaps one image to its pixel grid off the GUI thread.

    Detection alone takes about 3 seconds on a 1024x1024
    image (measured), the snap about 1.5 more — far too long
    to run on the GUI thread. The worker detects first, then
    snaps + quantises with the palette settings it was given
    (the GUI's current palette, color count and custom
    palettes). Only a buffer Image crosses the thread boundary
    (QPixmap is not thread-safe) — a plain Python object, like
    the background-removal worker's result. Cancel is checked
    between the stages (detect, snap, quantise): a cancel
    during detection takes effect when detection returns, a
    second or two later. The source image is carried along so
    the done slot can tell a stale result from the current
    image (a new import mid-run must not be overwritten by the
    old image's result). No grid and already-clean are their
    own signals, not failures — the image is left alone and
    the status bar says so in plain words.
    """
    done = Signal(dict)   # image, source, grid
    failed = Signal(str)  # readable error message
    cancelled = Signal()
    no_grid = Signal()
    already_clean = Signal()

    def __init__(self, image, palette, max_colors, palettes,
                 parent=None):
        super().__init__(parent)
        self.image = image
        self.palette = palette
        self.max_colors = max_colors
        self.palettes = palettes
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        try:
            grid = gridsnap.detect_grid(self.image)
        except PixelForgeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            self.failed.emit("pixel grid snap failed: %s" % exc)
            return
        if self._cancel_event.is_set():
            self.cancelled.emit()
            return
        if grid is None:
            self.no_grid.emit()
            return
        if grid[0] <= 1.0:
            self.already_clean.emit()
            return
        try:
            result = gridsnap.snap_and_quantise(
                self.image, grid, self.palette, self.max_colors,
                self.palettes)
        except PixelForgeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            self.failed.emit("pixel grid snap failed: %s" % exc)
            return
        if self._cancel_event.is_set():
            self.cancelled.emit()
            return
        self.done.emit({"image": result, "source": self.image,
                        "grid": grid})


class NormalMapWorker(QThread):
    """Generates one normal map off the GUI thread.

    Pure-Python Sobel on a 512 x 512 image takes about a second
    (measured) — too slow for the GUI thread, and the machine it
    runs on is slower than the sandbox. Small sprites take
    milliseconds, but the worker costs nothing there. Cancel is
    checked when generation returns: the Sobel itself is one
    call and cannot be interrupted mid-row, so a cancel during
    generation lands a second or two later, like the pixel-grid
    snap's cancel during detection. Only a buffer Image crosses
    the thread boundary. The source result is carried along so
    the done slot can tell a stale result from the current one
    (a control change mid-run re-pixelizes, and the old
    result's map must not be kept for the new result).
    """
    done = Signal(dict)   # image, source
    failed = Signal(str)  # readable error message
    cancelled = Signal()

    def __init__(self, image, strength, parent=None):
        super().__init__(parent)
        self.image = image
        self.strength = strength
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        try:
            result = normalmap.generate_normal_map(
                self.image, strength=self.strength)
        except PixelForgeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - window must stay up
            self.failed.emit("normal map failed: %s" % exc)
            return
        if self._cancel_event.is_set():
            self.cancelled.emit()
            return
        self.done.emit({"image": result, "source": self.image})


class UpdateCheckWorker(QThread):
    """Asks GitHub for the latest Pixel Forge release, off the
    GUI thread (task 48).

    The check is one HTTP GET with an 8 second timeout — short,
    but the window must never wait on the network. The result
    is a plain dict (see updates.check_for_update): "newer",
    "current", or "unknown" with a plain reason. check_for_update
    never raises for a network or parsing problem, and the
    blanket except is belt and braces — a worker must never die
    silently. There is no cancel: the fetch is one blocking
    call that cannot be interrupted mid-flight, so a cancel
    event would only be read at the same moment the thread
    ends anyway; closeEvent waits for the thread instead (at
    most the timeout plus a little). Only the dict crosses the
    thread boundary. manual says whether the user asked for the
    check — the done slot tells a manual check from the
    automatic startup one by it.
    """
    done = Signal(dict)  # state, latest, url, reason?, manual

    def __init__(self, current_version, manual, parent=None):
        super().__init__(parent)
        self.current_version = current_version
        self.manual = manual

    def run(self):
        try:
            result = gui_module.check_for_update(self.current_version)
        except Exception as exc:  # noqa: BLE001 - window must stay up
            result = {"state": "unknown", "latest": None,
                      "url": None,
                      "reason": "the update check failed: %s" % exc}
        result["manual"] = self.manual
        self.done.emit(result)


class SpritesheetDialog(QDialog):
    """Preview a packed spritesheet before exporting it.

    Shows the whole sheet (checkerboard behind transparency) and
    plays the animation FROM the sheet — the frames are cut back
    out with the metadata, so what plays is exactly what a game
    would play from the PNG + JSON pair. Export writes that pair.

    The packer is the caller's choice (task 38): "grid" packs
    the old way, "maxrects" packs through the atlas packer with
    the caller's trim/padding/extrude settings. Under maxrects
    the playback frames are cut per INPUT frame — a deduped
    frame shares its twin's rect but keeps its own place in
    the animation, so the count and the timing never change.
    """

    def __init__(self, parent, images, names, cols, padding, fps,
                 default_name, durations=None, tags=None,
                 pack="grid", trim=False,
                 atlas_padding=DEFAULT_PADDING, extrude=0):
        super().__init__(parent)
        self.setWindowTitle("Spritesheet preview")
        self.resize(560, 660)
        self.images = list(images)
        self.names = list(names)
        self.cols = cols
        self.padding = padding
        self.fps = fps
        self.default_name = default_name
        self.pack = pack
        self.trim = trim
        self.atlas_padding = atlas_padding
        self.extrude = extrude
        # Per-frame durations + tags (task 34), when the caller
        # has them: the preview plays the metadata's own
        # duration_ms, and Export writes the same pair the Video
        # tab's direct export writes — the dialog must never
        # silently drop them.
        self.durations = (list(durations) if durations is not None
                          else None)
        self.tags = (list(tags) if tags is not None else None)
        if pack == "maxrects":
            result = pack_atlas(
                list(zip(self.names, self.images)),
                max_size=DEFAULT_MAX_SIZE, padding=atlas_padding,
                extrude=extrude, trim=trim, multipack=False)
            self.sheet = result["sheets"][0]
            self.layout = None
            self.atlas_result = result
            self.metadata = build_atlas_metadata(
                result, [""], fps=fps, durations=durations,
                tags=tags)
            # Playback runs over the INPUT frames, not the
            # placed ones: an aliased frame is cut from the
            # rect it shares, so a deduped frame still plays
            # in its own slot at its own duration.
            by_name = {}
            for frame in result["frames"]:
                by_name[frame["name"]] = frame
                for alias in frame["aliases"]:
                    by_name[alias] = frame
            self.frames = [self.sheet.copy(
                by_name[name]["x"], by_name[name]["y"],
                by_name[name]["w"], by_name[name]["h"])
                for name in self.names]
        else:
            self.sheet, self.layout = pack_frames(
                self.images, cols=cols, padding=padding)
            self.atlas_result = None
            frame_w, frame_h = (self.images[0].width(),
                                self.images[0].height())
            self.metadata = build_metadata(
                "", self.names, frame_w, frame_h, self.layout, fps,
                durations=durations, tags=tags)
            self.frames = frames_from_sheet(self.sheet,
                                            self.metadata)
        self.current = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)

        layout = QVBoxLayout(self)
        if pack == "maxrects":
            deduped = self.atlas_result["deduped"]
            info = ("%d frames, MaxRects atlas, sheet %d x %d, "
                    "%s fps, %d px padding"
                    % (len(self.images), self.sheet.width(),
                       self.sheet.height(), fps, atlas_padding))
            if deduped:
                info += ", %d deduped" % deduped
        else:
            info = ("%d frames, %d x %d grid, sheet %d x %d, "
                    "%s fps, %d px padding"
                    % (len(self.images), self.layout["columns"],
                       self.layout["rows"], self.sheet.width(),
                       self.sheet.height(), fps, padding))
        self.info_label = QLabel(info)
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        layout.addWidget(self._section("Sheet"))
        self.sheet_label = QLabel()
        self.sheet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sheet_scroll = QScrollArea()
        sheet_scroll.setWidgetResizable(True)
        # The sheet used to sit hard against the scrollbar (task
        # 22): the label was the scroll area's widget directly,
        # with zero margins. A container with 8 px of breathing
        # room around the label instead.
        sheet_holder = QWidget()
        holder_layout = QVBoxLayout(sheet_holder)
        holder_layout.setContentsMargins(8, 8, 8, 8)
        holder_layout.addWidget(self.sheet_label)
        sheet_scroll.setWidget(sheet_holder)
        layout.addWidget(sheet_scroll, 1)
        longest = max(self.sheet.width(), self.sheet.height())
        if longest > 480:
            scale = 480 / longest
            smooth = True
        else:
            scale = max(1, 480 // longest)
            smooth = False
        self.sheet_label.setPixmap(gui_module.checkerboard_pixmap(
            self.sheet, scale, smooth=smooth))

        layout.addWidget(self._section("Animation from the sheet"))
        play_row = QHBoxLayout()
        self.frame_label = QLabel()
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setFixedSize(200, 200)
        play_row.addWidget(self.frame_label)
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self._on_play_pause)
        play_row.addWidget(self.play_btn)
        self.counter_label = QLabel("")
        play_row.addWidget(self.counter_label)
        play_row.addStretch(1)
        layout.addLayout(play_row)
        self._refresh_frame()

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.export_btn = QPushButton("Export spritesheet")
        self.export_btn.setObjectName("export")
        self.export_btn.clicked.connect(self._on_export)
        button_row.addWidget(self.export_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

    @staticmethod
    def _section(text):
        label = QLabel(text)
        label.setObjectName("heading")
        return label

    def _refresh_frame(self):
        frame = self.frames[self.current]
        longest = max(frame.width(), frame.height())
        scale = max(1, 192 // longest)
        self.frame_label.setPixmap(gui_module.checkerboard_pixmap(
            frame, scale, smooth=False))
        self.counter_label.setText(
            "Frame %d of %d" % (self.current + 1, len(self.frames)))

    def _frame_duration(self, index):
        """How long one frame shows, from the metadata's own
        duration_ms (task 34) — the preview plays the real
        timing, not the uniform fps. Under maxrects the
        metadata holds one entry per PLACED sprite, so a
        deduped frame has no entry of its own — the playback
        durations come from the caller's list instead, which
        runs over the input frames."""
        if self.pack == "maxrects" and self.durations is not None:
            return self.durations[index]
        return self.metadata["frames"][index]["duration_ms"]

    def _advance(self):
        self.current = (self.current + 1) % len(self.frames)
        if self.timer.isActive():
            self.timer.start(self._frame_duration(self.current))
        self._refresh_frame()

    def _on_play_pause(self):
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setText("Play")
            return
        self.timer.start(self._frame_duration(self.current))
        self.play_btn.setText("Pause")

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export spritesheet", self.default_name,
            "PNG images (*.png)")
        if not path:
            return
        png_path = Path(path)
        if png_path.suffix.lower() != ".png":
            png_path = png_path.with_suffix(".png")
        meta_path = png_path.with_suffix(".json")
        if self.pack == "maxrects":
            try:
                metadata, _files = write_atlas(
                    self.images, self.names,
                    str(png_path), str(meta_path),
                    padding=self.atlas_padding,
                    extrude=self.extrude, trim=self.trim,
                    multipack=False, fps=self.fps,
                    durations=self.durations, tags=self.tags)
            except PixelForgeError as exc:
                gui_module.warn_dialog(self, "Could not export atlas", str(exc))
                return
            deduped = metadata["deduped"]
            message = ("Exported atlas: %s, %s%s"
                       % (plural(len(metadata["sheets"]), "sheet"),
                          plural(len(self.images), "frame"),
                          (", %d deduped" % deduped)
                          if deduped else ""))
        else:
            try:
                write_sheet(self.images, self.names,
                            str(png_path), str(meta_path),
                            cols=self.cols, padding=self.padding,
                            fps=self.fps, durations=self.durations,
                            tags=self.tags)
            except PixelForgeError as exc:
                gui_module.warn_dialog(self, "Could not export spritesheet",
                            str(exc))
                return
            message = ("Exported spritesheet: %d frames, %d x %d "
                       "grid, %s"
                       % (len(self.images), self.layout["columns"],
                          self.layout["rows"], png_path.name))
        parent = self.parent()
        if parent is not None and hasattr(parent, "statusBar"):
            parent.statusBar().showMessage(message)
            # The sheet's folder becomes the last-used export
            # folder, like every other export (task 22).
            if hasattr(parent, "_remember_export_dir"):
                parent._remember_export_dir(png_path.parent)
        self.accept()


class SheetSplitDialog(QDialog):
    """Split a spritesheet PNG back into frames (task 40) — the
    round-trip path: a sheet exported by Pixel Forge (or any
    sheet with a matching metadata JSON) comes back as its own
    frames, with its durations and tags; a bare-grid sheet
    (Kenney / itch.io / OpenGameArt style, no metadata) splits
    on a grid the user describes.

    Nothing is picked by default: the dialog opens with no
    sheet, no metadata, and every grid number unset (0 means
    "not given" — Split stays disabled until the numbers
    describe a whole grid). Picking a sheet fills the metadata
    in automatically when a JSON with the same stem sits next
    to it; clearing the metadata switches to the grid form.
    The frame-count estimate updates live as the grid numbers
    change, and a grid that does not divide the sheet evenly
    names the problem instead of splitting a partial grid.

    On Split, the frames are cut here (fast, in memory) and
    exposed as .frames / .durations / .tags / .source_name for
    the caller to land. Errors never open a message box — they
    show in the info line, and Split stays disabled until the
    dialog is in a splittable state.
    """

    def __init__(self, parent, default_duration):
        super().__init__(parent)
        self.setWindowTitle("Split spritesheet")
        self.resize(500, 470)
        self.default_duration = default_duration
        self.sheet = None
        self.sheet_path = None
        self.metadata = None
        self.frames = []
        self.durations = []
        self.tags = []
        self.source_name = ""
        self._sheet_error = None

        layout = QVBoxLayout(self)

        layout.addWidget(self._section("Sheet"))
        sheet_row = QHBoxLayout()
        self.sheet_edit = QLineEdit()
        self.sheet_edit.setToolTip(
            "The spritesheet PNG to split back into frames")
        self.sheet_edit.editingFinished.connect(
            self._on_sheet_typed)
        sheet_row.addWidget(self.sheet_edit, 1)
        self.sheet_browse_btn = QPushButton("Browse")
        self.sheet_browse_btn.setToolTip(
            "Pick the spritesheet PNG to split")
        self.sheet_browse_btn.clicked.connect(
            self._on_browse_sheet)
        sheet_row.addWidget(self.sheet_browse_btn)
        layout.addLayout(sheet_row)

        layout.addWidget(self._section("Metadata"))
        meta_row = QHBoxLayout()
        self.meta_edit = QLineEdit()
        self.meta_edit.setToolTip(
            "The sheet's metadata JSON — filled in automatically "
            "when one sits next to the sheet; clear it to split "
            "as a grid instead")
        self.meta_edit.editingFinished.connect(self._refresh)
        meta_row.addWidget(self.meta_edit, 1)
        self.meta_browse_btn = QPushButton("Browse")
        self.meta_browse_btn.setToolTip(
            "Pick the metadata JSON for this sheet")
        self.meta_browse_btn.clicked.connect(
            self._on_browse_meta)
        meta_row.addWidget(self.meta_browse_btn)
        self.meta_clear_btn = QPushButton("Clear")
        self.meta_clear_btn.setToolTip(
            "Forget the metadata and split as a grid instead")
        self.meta_clear_btn.clicked.connect(self._on_clear_meta)
        meta_row.addWidget(self.meta_clear_btn)
        layout.addLayout(meta_row)

        self.info_label = QLabel(
            "Pick a spritesheet PNG to split it back into frames.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        layout.addWidget(self._section("Grid"))
        grid_note = QLabel(
            "For a sheet with no metadata: give the frame size, "
            "or the number of columns and rows — either pair "
            "splits the grid.")
        grid_note.setWordWrap(True)
        layout.addWidget(grid_note)
        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel("Frame width"))
        self.frame_w_spin = QSpinBox()
        self.frame_w_spin.setRange(0, 4096)
        self.frame_w_spin.setSuffix(" px")
        self.frame_w_spin.setSpecialValueText("not set")
        self.frame_w_spin.setToolTip(
            "Width of one frame in pixels — 0 means not given")
        self.frame_w_spin.valueChanged.connect(self._refresh)
        frame_row.addWidget(self.frame_w_spin)
        frame_row.addWidget(QLabel("Frame height"))
        self.frame_h_spin = QSpinBox()
        self.frame_h_spin.setRange(0, 4096)
        self.frame_h_spin.setSuffix(" px")
        self.frame_h_spin.setSpecialValueText("not set")
        self.frame_h_spin.setToolTip(
            "Height of one frame in pixels — 0 means not given")
        self.frame_h_spin.valueChanged.connect(self._refresh)
        frame_row.addWidget(self.frame_h_spin)
        layout.addLayout(frame_row)
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Columns"))
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(0, 4096)
        self.cols_spin.setSpecialValueText("not set")
        self.cols_spin.setToolTip(
            "Columns in the grid — 0 means not given")
        self.cols_spin.valueChanged.connect(self._refresh)
        count_row.addWidget(self.cols_spin)
        count_row.addWidget(QLabel("Rows"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(0, 4096)
        self.rows_spin.setSpecialValueText("not set")
        self.rows_spin.setToolTip(
            "Rows in the grid — 0 means not given")
        self.rows_spin.valueChanged.connect(self._refresh)
        count_row.addWidget(self.rows_spin)
        layout.addLayout(count_row)
        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel("Spacing"))
        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 64)
        self.spacing_spin.setSuffix(" px")
        self.spacing_spin.setToolTip(
            "Blank pixels between frames in the grid")
        self.spacing_spin.valueChanged.connect(self._refresh)
        gap_row.addWidget(self.spacing_spin)
        gap_row.addWidget(QLabel("Margin"))
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 64)
        self.margin_spin.setSuffix(" px")
        self.margin_spin.setToolTip(
            "Blank border around the whole grid")
        self.margin_spin.valueChanged.connect(self._refresh)
        gap_row.addWidget(self.margin_spin)
        layout.addLayout(gap_row)

        self.estimate_label = QLabel("")
        self.estimate_label.setWordWrap(True)
        layout.addWidget(self.estimate_label)

        layout.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.split_btn = QPushButton("Split")
        self.split_btn.setObjectName("export")
        self.split_btn.setToolTip(
            "Split the sheet and load its frames into the Video "
            "tab")
        self.split_btn.setEnabled(False)
        self.split_btn.clicked.connect(self._on_split)
        btn_row.addWidget(self.split_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Close without splitting")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)
        self._refresh()

    @staticmethod
    def _section(text):
        label = QLabel(text)
        label.setObjectName("heading")
        return label

    def _set_grid_enabled(self, enabled):
        for widget in (self.frame_w_spin, self.frame_h_spin,
                       self.cols_spin, self.rows_spin,
                       self.spacing_spin, self.margin_spin):
            widget.setEnabled(enabled)

    def _on_browse_sheet(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a spritesheet", "", "PNG images (*.png)")
        if path:
            self._set_sheet(path)

    def _on_sheet_typed(self):
        path = self.sheet_edit.text().strip()
        if path and path != self.sheet_path:
            self._set_sheet(path)
        elif not path:
            self.sheet = None
            self.sheet_path = None
            self._sheet_error = None
            self.meta_edit.clear()
            self._refresh()

    def _set_sheet(self, path):
        """Load a sheet PNG and auto-fill its sibling metadata
        (same stem, .json) when one exists. A bad file leaves
        the error in the info line — never a message box."""
        self.sheet_path = path
        self.sheet_edit.setText(path)
        try:
            self.sheet = gui_module.load_image(path)
            self._sheet_error = None
        except PixelForgeError as exc:
            self.sheet = None
            self._sheet_error = str(exc)
        sibling = find_sibling_metadata(path)
        self.meta_edit.setText(str(sibling) if sibling else "")
        self._refresh()

    def _on_browse_meta(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the metadata JSON", "",
            "JSON files (*.json)")
        if path:
            self.meta_edit.setText(path)
            self._refresh()

    def _on_clear_meta(self):
        self.meta_edit.clear()
        self._refresh()

    def _grid_values(self):
        """The grid numbers as grid_layout takes them — 0 means
        not given, so it becomes None."""
        def given(spin):
            value = spin.value()
            return value if value >= 1 else None
        return {
            "frame_w": given(self.frame_w_spin),
            "frame_h": given(self.frame_h_spin),
            "cols": given(self.cols_spin),
            "rows": given(self.rows_spin),
            "spacing": self.spacing_spin.value(),
            "margin": self.margin_spin.value(),
        }

    def _refresh(self):
        """Recompute the dialog's state from the widgets: the
        info line, the live estimate, and whether Split is
        allowed. The metadata file is re-read here (it is a
        small JSON), the sheet image is not."""
        self.metadata = None
        if self._sheet_error is not None:
            self.info_label.setText(self._sheet_error)
            self.estimate_label.setText("")
            self._set_grid_enabled(False)
            self.split_btn.setEnabled(False)
            return
        if self.sheet is None:
            self.info_label.setText(
                "Pick a spritesheet PNG to split it back into "
                "frames.")
            self.estimate_label.setText("")
            self._set_grid_enabled(False)
            self.split_btn.setEnabled(False)
            return
        sheet = self.sheet
        meta_path = self.meta_edit.text().strip()
        if meta_path:
            try:
                metadata = load_metadata(meta_path)
            except PixelForgeError as exc:
                self.info_label.setText(str(exc))
                self.estimate_label.setText("")
                self._set_grid_enabled(False)
                self.split_btn.setEnabled(False)
                return
            if (sheet.width() != metadata["sheet_width"]
                    or sheet.height() != metadata["sheet_height"]):
                self.info_label.setText(
                    "The sheet image is %d x %d but its metadata "
                    "says %d x %d — this PNG and JSON are not a "
                    "pair" % (sheet.width(), sheet.height(),
                              metadata["sheet_width"],
                              metadata["sheet_height"]))
                self.estimate_label.setText("")
                self._set_grid_enabled(False)
                self.split_btn.setEnabled(False)
                return
            self.metadata = metadata
            info = ("Metadata: %s, %d x %d each"
                    % (plural(len(metadata["frames"]), "frame"),
                       metadata["frame_width"],
                       metadata["frame_height"]))
            tags = metadata.get("tags") or []
            if tags:
                info += ", tags: %s" % ", ".join(
                    tag["name"] for tag in tags)
            self.info_label.setText(info)
            self.estimate_label.setText("")
            self._set_grid_enabled(False)
            self.split_btn.setEnabled(True)
            return
        # No metadata — the grid form. The estimate is live:
        # blank until a pair of numbers is given, the problem
        # named when the grid does not divide the sheet.
        self.info_label.setText(
            "Sheet: %d x %d, no metadata — split as a grid."
            % (sheet.width(), sheet.height()))
        self._set_grid_enabled(True)
        values = self._grid_values()
        size_given = (values["frame_w"] is not None
                      and values["frame_h"] is not None)
        count_given = (values["cols"] is not None
                       and values["rows"] is not None)
        if not size_given and not count_given:
            self.estimate_label.setText("")
            self.split_btn.setEnabled(False)
            return
        try:
            layout = grid_layout(sheet.width(), sheet.height(),
                                 **values)
        except PixelForgeError as exc:
            self.estimate_label.setText(str(exc))
            self.split_btn.setEnabled(False)
            return
        self.estimate_label.setText(
            "%s (%d columns x %d rows), each %d x %d"
            % (plural(len(layout["positions"]), "frame"),
               layout["columns"], layout["rows"],
               layout["frame_width"], layout["frame_height"]))
        self.split_btn.setEnabled(True)

    def _on_split(self):
        """Cut the frames and accept. The dialog was already
        validated by _refresh, so a failure here is belt and
        braces — it lands in the info line and the dialog stays
        open."""
        try:
            if self.metadata is not None:
                frames, durations, tags = split_with_metadata(
                    self.sheet, self.metadata)
            else:
                values = self._grid_values()
                frames = frames_from_grid(self.sheet, **values)
                durations = [self.default_duration] * len(frames)
                tags = []
        except PixelForgeError as exc:
            self.info_label.setText(str(exc))
            self.split_btn.setEnabled(False)
            return
        self.frames = frames
        self.durations = durations
        self.tags = tags
        self.source_name = Path(self.sheet_path).name
        self.accept()


def _render_system_font(text, family, size):
    """A system font's text as a mask Image: white opaque where
    the ink goes, transparent elsewhere. Antialiasing is off and
    the alpha is thresholded at half, so the stamp is crisp
    whole pixels — the same binary mask the pixel font renders,
    and the same stamp_image blit serves both."""
    font = QFont(family)
    font.setPixelSize(size)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    metrics = QFontMetrics(font)
    width = max(1, metrics.horizontalAdvance(text))
    height = max(1, metrics.height())
    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(0, metrics.ascent(), text)
    painter.end()
    raw = from_qimage(canvas)
    mask = Image(width, height)
    ink = argb(255, 255, 255)
    for row in range(height):
        for col in range(width):
            if (raw.pixel(col, row) >> 24) >= 128:
                mask.setPixel(col, row, ink)
    return mask


class StampTextDialog(QDialog):
    """Stamp text onto the sprite (task 47) — a name, a number,
    a sign. The Text tool opens it at the clicked pixel; the
    position spins nudge it from there.

    Two fonts: the built-in pixel font (a 5 x 7 bitmap font,
    pixel art by construction — scale 1-4) and any system
    font installed on the machine (size 6-64 px, rendered
    without antialiasing and thresholded to whole pixels, so
    a system-font stamp is exactly as crisp as a pixel-font
    one). The preview shows the stamp on the sprite as the
    settings change, so the color and the placement are judged
    before anything is stamped.

    Stamp stays disabled until some text is typed. A stamp
    that would land nothing — the text sits outside the sprite,
    or it has no letters in it — says so in the info line and
    the dialog stays open. On accept, the caller reads
    text_value / font_family (None for the pixel font) / size /
    color / x / y / mask and stamps once onto the active layer.
    """

    # System-font sizes in pixels, and the pixel font's scale
    # range (from textstamp). A 64-character cap keeps the
    # render small — 64 characters at scale 4 is already
    # wider than any sprite Pixel Forge makes.
    SYSTEM_SIZE_MIN = 6
    SYSTEM_SIZE_MAX = 64
    SYSTEM_SIZE_DEFAULT = 12
    TEXT_MAX_LENGTH = 64

    def __init__(self, parent, image, x, y, color):
        super().__init__(parent)
        self.setWindowTitle("Stamp text")
        self.resize(460, 560)
        self._image = image
        self.text_value = ""
        self.font_family = None
        self.size = MIN_SCALE
        self.color = color
        self.x = x
        self.y = y
        self.mask = None
        self._pixel_size = MIN_SCALE
        self._system_size = self.SYSTEM_SIZE_DEFAULT
        self._painted = 0
        self._ink = 0

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Text"))
        self.text_edit = QLineEdit()
        self.text_edit.setMaxLength(self.TEXT_MAX_LENGTH)
        self.text_edit.setPlaceholderText(
            "Type the word or number to stamp")
        self.text_edit.setToolTip(
            "The word, number, or sign to stamp onto the sprite")
        self.text_edit.textChanged.connect(self._refresh)
        layout.addWidget(self.text_edit)

        layout.addWidget(QLabel("Font"))
        self.font_combo = QComboBox()
        self.font_combo.setToolTip(
            "Pixel font is the built-in 5 x 7 bitmap font; "
            "the rest are the fonts installed on this machine")
        self.font_combo.addItem("Pixel font", None)
        for family in QFontDatabase.families():
            self.font_combo.addItem(family, family)
        self.font_combo.currentIndexChanged.connect(
            self._on_font_changed)
        layout.addWidget(self.font_combo)

        size_color_row = QHBoxLayout()
        self.size_label = QLabel("Scale")
        size_color_row.addWidget(self.size_label)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(MIN_SCALE, MAX_SCALE)
        self.size_spin.setValue(MIN_SCALE)
        self.size_spin.setSuffix("x")
        self.size_spin.setToolTip(
            "How many sprite pixels each font pixel becomes")
        self.size_spin.valueChanged.connect(self._refresh)
        size_color_row.addWidget(self.size_spin)
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(20, 20)
        self.color_swatch.setToolTip("The color the text stamps in")
        size_color_row.addWidget(self.color_swatch)
        self.color_btn = QPushButton("Pick a color")
        self.color_btn.setToolTip(
            "Pick the color the text stamps in")
        self.color_btn.clicked.connect(self._on_pick_color)
        size_color_row.addWidget(self.color_btn)
        size_color_row.addStretch(1)
        layout.addLayout(size_color_row)

        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X"))
        self.x_spin = QSpinBox()
        self.x_spin.setRange(0, max(0, image.width() - 1))
        self.x_spin.setValue(x)
        self.x_spin.setSuffix(" px")
        self.x_spin.setToolTip(
            "How far from the sprite's left edge the text starts")
        self.x_spin.valueChanged.connect(self._refresh)
        pos_row.addWidget(self.x_spin)
        pos_row.addWidget(QLabel("Y"))
        self.y_spin = QSpinBox()
        self.y_spin.setRange(0, max(0, image.height() - 1))
        self.y_spin.setValue(y)
        self.y_spin.setSuffix(" px")
        self.y_spin.setToolTip(
            "How far from the sprite's top edge the text starts")
        self.y_spin.valueChanged.connect(self._refresh)
        pos_row.addWidget(self.y_spin)
        pos_row.addStretch(1)
        layout.addLayout(pos_row)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview_label)

        self.info_label = QLabel(
            "Type the text to stamp, then pick where it goes.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        layout.addStretch(1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.stamp_btn = QPushButton("Stamp")
        self.stamp_btn.setObjectName("export")
        self.stamp_btn.setToolTip(
            "Stamp the text onto the sprite")
        self.stamp_btn.setEnabled(False)
        self.stamp_btn.clicked.connect(self._on_stamp)
        button_row.addWidget(self.stamp_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setToolTip("Close without stamping anything")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        self._paint_swatch()
        self._refresh()

    def _paint_swatch(self):
        """The color swatch shows the stamp color."""
        self.color_swatch.setStyleSheet(
            "QLabel { background-color: #%06x; "
            "border: 1px solid #55555e; border-radius: 2px; }"
            % (self.color & 0xFFFFFF))

    def _on_font_changed(self):
        """The size spin follows the font: a scale for the pixel
        font, a pixel size for a system font. Each mode keeps its
        own last value, so switching back restores it."""
        if self.font_combo.currentData() is None:
            self._system_size = self.size_spin.value()
            self.size_label.setText("Scale")
            self.size_spin.setRange(MIN_SCALE, MAX_SCALE)
            self.size_spin.setSuffix("x")
            self.size_spin.setValue(self._pixel_size)
            self.size_spin.setToolTip(
                "How many sprite pixels each font pixel becomes")
        else:
            self._pixel_size = self.size_spin.value()
            self.size_label.setText("Size")
            self.size_spin.setRange(self.SYSTEM_SIZE_MIN,
                                    self.SYSTEM_SIZE_MAX)
            self.size_spin.setSuffix(" px")
            self.size_spin.setValue(self._system_size)
            self.size_spin.setToolTip(
                "How tall the text is, in sprite pixels")
        self._refresh()

    def _on_pick_color(self):
        """The color picker: a cancelled pick keeps the color."""
        picked = QColorDialog.getColor(
            QColor((self.color >> 16) & 0xFF,
                   (self.color >> 8) & 0xFF,
                   self.color & 0xFF),
            self, "Pick a color")
        if not picked.isValid():
            return
        self.color = argb(picked.red(), picked.green(),
                          picked.blue())
        self._paint_swatch()
        self._refresh()

    def _render_mask(self, text):
        """The current text as a mask Image, from whichever font
        is picked. The pixel font renders through the core; a
        system font renders through Qt, thresholded to a mask."""
        if self.font_combo.currentData() is None:
            return render_text(text, self.size_spin.value())
        return _render_system_font(
            text, self.font_combo.currentData(),
            self.size_spin.value())

    def _refresh(self):
        """Every control change re-renders the mask, re-stamps
        the preview copy, and rewrites the info line. The public
        fields (text_value, font_family, size, color, x, y,
        mask) always hold the current settings, so the caller
        reads them after accept without asking the widgets."""
        text = self.text_edit.text()
        self.text_value = text
        self.font_family = self.font_combo.currentData()
        self.size = self.size_spin.value()
        self.x = self.x_spin.value()
        self.y = self.y_spin.value()
        self.stamp_btn.setEnabled(bool(text))
        if not text:
            self.mask = None
            self._painted = 0
            self._ink = 0
            self.info_label.setText(
                "Type the text to stamp, then pick where it goes.")
            self._show_preview(self._image)
            return
        self.mask = self._render_mask(text)
        self._ink = sum(
            1 for row in range(self.mask.height())
            for col in range(self.mask.width())
            if (self.mask.pixel(col, row) >> 24) != 0)
        preview = self._image.copy()
        self._painted = stamp_image(preview, self.mask, self.x,
                                    self.y, self.color)
        self._show_preview(preview)
        width, height = self.mask.width(), self.mask.height()
        if self._ink == 0:
            self.info_label.setText(
                "The text has no letters to stamp.")
            return
        if self._painted == 0:
            self.info_label.setText(
                "The text sits outside the sprite. "
                "Move it inside to stamp it.")
            return
        message = "Stamp is %d x %d pixels." % (width, height)
        if self._painted < self._ink:
            message += (" Some of the text falls outside the "
                        "sprite and will be cut off.")
        if self.font_family is None:
            missing = unsupported_characters(text)
            if missing:
                message += (" The pixel font cannot draw: %s."
                            % ", ".join(missing))
        self.info_label.setText(message)

    def _show_preview(self, image):
        """The preview pane: the sprite with the stamp on it,
        fitted to the dialog, nearest-neighbour so the pixels
        read exactly as they will stamp."""
        longest = max(image.width(), image.height())
        scale = 280.0 / max(1, longest)
        self.preview_label.setPixmap(
            gui_module.checkerboard_pixmap(image, scale, False))

    def _on_stamp(self):
        """Stamp, unless the stamp would land nothing — then the
        info line says why and the dialog stays open."""
        if self._painted == 0:
            self._refresh()
            return
        self.accept()


class CollapsibleSection(QWidget):
    """One collapsible section: a header button above a body.

    Task 87: the Video tab's control column is six of these
    (Frames, Size, Palette, Preset, Animation, Export). The
    header is a flat button in the heading color with an arrow
    (down when open, right when closed); clicking it shows or
    hides the body.
    Several sections can stay open at once — a user mid-task
    needs Frames and Animation together, which a tool box
    (one open at a time) would not allow. Add the section's
    widgets to body_layout(). The toggled signal carries the
    new state, so the caller can persist it.
    """

    toggled = Signal(bool)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._title = title
        # Task 88 (v0.81): the section and its body are
        # transparent (QWidget#section / #sectionBody in the
        # theme stylesheets), so the tab column's pixel grid
        # shows through as one continuous grid instead of
        # stopping at every section edge.
        self.setObjectName("section")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._header = QPushButton()
        self._header.setObjectName("sectionHeader")
        self._header.setToolTip(
            "Show or hide the %s section" % title)
        self._header.clicked.connect(self._on_header_clicked)
        layout.addWidget(self._header)
        self._body = QWidget()
        self._body.setObjectName("sectionBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._body)
        self._expanded = True
        self._refresh_header()

    def body_layout(self):
        """The body's layout: add the section's widgets here."""
        return self._body_layout

    def header_button(self):
        """The header button (tests click it)."""
        return self._header

    def title(self):
        """The section title, without the arrow."""
        return self._title

    def isExpanded(self):
        """True when the body is showing."""
        return self._expanded

    def setExpanded(self, expanded, emit=True):
        """Show or hide the body. Emits toggled only when the
        state actually changes, and never while the caller is
        restoring a saved state (emit=False)."""
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._refresh_header()
        # The section's size hint just changed (body in or out):
        # tell the parent layout, or the freed space sits empty
        # until something else resizes the window. Hiding the
        # body alone does not reliably re-activate the parent
        # layout (proven under offscreen: the heights froze).
        self.updateGeometry()
        if emit:
            self.toggled.emit(expanded)

    def _refresh_header(self):
        arrow = "\u25be" if self._expanded else "\u25b8"
        self._header.setText("%s %s" % (arrow, self._title))

    def _on_header_clicked(self):
        self.setExpanded(not self._expanded)


HOWTO_SECTIONS = (
    ("First steps",
     "Pixel Forge turns pictures, videos, words, and code into "
     "pixel art, all on your own machine. Nothing leaves the "
     "computer and there is no account.\n"
     "\n"
     "Start on the Image tab: open a picture, pick a size, and "
     "the pixel art appears next to it. Export saves it as a "
     "PNG with a transparent background.\n"
     "\n"
     "The tabs along the top are the six ways in: Image, Video, "
     "Prompt, Code, Library, Tileset. Press Ctrl+1 to Ctrl+6 "
     "to switch between them.\n"
     "\n"
     "If the app ever closes unexpectedly, the next launch "
     "offers to bring your work back. That is autosave, and it "
     "is always on. There is nothing to turn on."),
    ("Image",
     "Open a picture: File menu, Open image, or the Import "
     "button on the Image tab. PNG, JPG, and other common "
     "pictures all work.\n"
     "\n"
     "Pick a size: the size controls on the Image tab, under "
     "Pixelize. Smaller sizes read more like pixel art. A "
     "preset sets size, palette, colors, and dithering "
     "together.\n"
     "\n"
     "Pick a palette: the palette controls on the Image tab, "
     "under Pixelize. From image takes the colors from your "
     "picture. Custom palettes live in the palette picker.\n"
     "\n"
     "Fix the pixels by hand: the editor tools above the Pixel "
     "art preview on the Image tab. Pencil paints one pixel, "
     "Fill fills an area, Text stamps a word. Pick a color "
     "from the strip first.\n"
     "\n"
     "Layers: the layer list on the Image tab stacks edits "
     "over the pixel art. The bottom layer is the pixel art "
     "itself, and new layers go on top.\n"
     "\n"
     "Remove the background: Tools menu, Remove background, "
     "or the Background section on the Image tab. The subject "
     "stays and the rest goes transparent. It needs the "
     "background removal model, which the installer offers.\n"
     "\n"
     "Snap to the pixel grid: Tools menu, Snap to pixel grid, "
     "or the Pixel grid section on the Image tab. It cleans up "
     "an AI image that is meant to be pixel art.\n"
     "\n"
     "Normal map: Tools menu, Generate normal map, or the "
     "Normal map section on the Image tab. It makes a lighting "
     "map from the sprite for 2D lights.\n"
     "\n"
     "Make a sprite from nothing: Tools menu, Generate a "
     "sprite, or the Generate section on the Image tab. Pick a "
     "kind and a seed, and the same seed always makes the "
     "same sprite.\n"
     "\n"
     "Undo and redo: Edit menu, or Ctrl+Z and Ctrl+Y. They "
     "undo control changes, edits, and library moves."),
    ("Video",
     "Open a clip: File menu, Open video, or the Import button "
     "on the Video tab. MP4 and other common videos work, and "
     "animated GIFs keep their own frame times.\n"
     "\n"
     "Extract frames: the Extract section on the Video tab. "
     "Choose how many frames per second, or every Nth frame, "
     "then Extract.\n"
     "\n"
     "Keep or drop frames: the frame strip on the Video tab. "
     "Click a frame to keep or drop it. Only kept frames "
     "export.\n"
     "\n"
     "Watch it play: the preview on the Video tab plays the "
     "kept frames as an animation. Change the speed with the "
     "playback controls.\n"
     "\n"
     "Export: the Export section on the Video tab saves the "
     "kept frames as numbered PNGs, or packs them into a "
     "spritesheet with a data file a game can play. GIF export "
     "keeps each frame's own delay."),
    ("Prompt",
     "Describe a sprite: the Prompt tab. Type what you want, "
     "pick a size, and Generate makes three pictures to choose "
     "from. Click one to keep it.\n"
     "\n"
     "The image engine: generation needs the image engine, "
     "which the installer offers as an extra step. Without "
     "it, Generate says so plainly and nothing is faked.\n"
     "\n"
     "Negative prompt: the Negative prompt box on the Prompt "
     "tab lists what to avoid, like blurry or low quality.\n"
     "\n"
     "Keepers: the kept picture pixelizes with the same "
     "controls as the Image tab. File menu, Export, saves it "
     "as a PNG.\n"
     "\n"
     "Batches: every generation is saved as a batch in the "
     "library, so you can go back to earlier pictures."),
    ("Code",
     "Import code: the Import button on the Code tab. Python, "
     "JavaScript, TypeScript, JSON, C#, GDScript, and Lua "
     "files work. The code is read, never run.\n"
     "\n"
     "What it finds: pixel maps, palettes, single colors, "
     "JSON sprites, and image references. The Help menu, What "
     "the Code tab detects, lists them all.\n"
     "\n"
     "Game assets: a 64x64 or 64x32 picture is read as a game "
     "skin and split into its parts. Anything else pixelizes "
     "as a texture at its own size.\n"
     "\n"
     "3D preview: the preview button on the Code tab shows a "
     "skin on a 3D figure. Drag to turn it.\n"
     "\n"
     "Export: the export buttons on the Code tab save the "
     "parts, the whole skin, or the texture as PNGs."),
    ("Library",
     "Save to the library: the Save to library button on the "
     "Image, Prompt, Code, and Tileset tabs. Give it a name "
     "and tags.\n"
     "\n"
     "Find a sprite: the search box and tag list on the "
     "Library tab. Click a sprite to see it big.\n"
     "\n"
     "Load it back: the Load button on the Library tab opens "
     "a saved sprite on the Image tab.\n"
     "\n"
     "Organize: the Library tab renames, retags, and deletes "
     "sprites. Deleted sprites go to the trash folder inside "
     "the library.\n"
     "\n"
     "Where it lives: Help menu, Library location, shows the "
     "folder on disk and opens it."),
    ("Tileset",
     "Build a tileset: the Tileset tab. Pick one base tile "
     "for a blob set, or two for a Wang set, and Generate "
     "builds the whole set.\n"
     "\n"
     "Blob shape: the Blob shape section on the Tileset tab "
     "picks square or rounded corners for a blob set.\n"
     "\n"
     "Export: File menu, Export, or the export buttons on the "
     "Tileset tab save the set as a PNG, or with the files a "
     "game editor needs."),
)


class HowToUseDialog(QDialog):
    """The built-in How to Use index (task 86, U22): one window,
    a section per tab plus first steps, in plain words. Every
    entry names where the thing lives, so no manual is needed.

    Modeless: it stays open while the user works, and the
    window keeps one instance and re-shows it after close.
    The section list starts with nothing selected — the full
    text is visible from the top, and picking a section jumps
    to it. Geometry is remembered like the main window (U5)
    when settings are passed; with no settings it opens at
    its default size and remembers nothing.
    """

    def __init__(self, parent, settings=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("How to Use Pixel Forge")
        layout = QVBoxLayout(self)

        split = QSplitter(self)
        self.section_list = QListWidget()
        self.section_list.setToolTip(
            "Pick a section to jump to it")
        self.section_list.currentRowChanged.connect(
            self._on_section_picked)
        split.addWidget(self.section_list)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setToolTip(
            "How to use Pixel Forge, section by section")
        split.addWidget(self.text)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        layout.addWidget(split)

        close_btn = QPushButton("Close")
        close_btn.setToolTip("Close this window")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        # One document, all sections in order. The block number
        # of each heading is recorded while building it, so a
        # section pick jumps by block number — a text search
        # would land on the first mention of a word like
        # "Image" in an earlier section instead of its heading,
        # and visual line numbers shift with word wrap.
        lines = []
        self._section_blocks = []
        for title, body in HOWTO_SECTIONS:
            self._section_blocks.append(len(lines))
            lines.append(title)
            lines.append("-" * len(title))
            lines.append("")
            lines.extend(body.split("\n"))
            lines.append("")
            lines.append("")
            self.section_list.addItem(title)
        self.text.setPlainText("\n".join(lines))
        # The text takes the opening focus, not the list: a list
        # that takes focus with no current item picks its first
        # row, which would select a section by default.
        self.text.setFocus()
        self._restore_geometry()

    def _on_section_picked(self, row):
        """Jump the text to the picked section's heading, at
        the top of the pane — ensureCursorVisible alone would
        leave the heading at the bottom edge, showing the tail
        of the previous section instead."""
        if row < 0 or row >= len(self._section_blocks):
            return
        block = self.text.document().findBlockByNumber(
            self._section_blocks[row])
        if not block.isValid():
            return
        cursor = self.text.textCursor()
        cursor.setPosition(block.position())
        self.text.setTextCursor(cursor)
        rect = self.text.document().documentLayout().blockBoundingRect(
            block)
        self.text.verticalScrollBar().setValue(int(rect.top()))

    def _restore_geometry(self):
        if self._settings is None:
            self.resize(640, 480)
            return
        values = [self._settings.value("howto/%s" % key,
                                       type=int)
                  for key in ("x", "y", "width", "height")]
        if any(value is None for value in values):
            self.resize(640, 480)
            return
        x, y, width, height = values
        if width < 1 or height < 1:
            self.resize(640, 480)
            return
        screen = self.screen() or QApplication.primaryScreen()
        rect = QRect(x, y, width, height)
        if screen is not None:
            rect = clamp_geometry(rect, screen.availableGeometry())
        self.setGeometry(rect)

    def _save_geometry(self):
        if self._settings is None:
            return
        rect = self.geometry()
        self._settings.setValue("howto/x", rect.x())
        self._settings.setValue("howto/y", rect.y())
        self._settings.setValue("howto/width", rect.width())
        self._settings.setValue("howto/height", rect.height())
        self._settings.sync()

    def closeEvent(self, event):
        self._save_geometry()
        super().closeEvent(event)


class SaveToLibraryDialog(QDialog):
    """Ask for a name and tags when saving a sprite to the
    library. Save stays disabled until a name is typed."""

    def __init__(self, parent, default_name):
        super().__init__(parent)
        self.setWindowTitle("Save to library")
        self.resize(360, 180)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(default_name)
        self.name_edit.selectAll()
        self.name_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Tags (comma separated, optional)"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("hero, red, walk")
        layout.addWidget(self.tags_edit)
        layout.addStretch(1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.ok_btn = QPushButton("Save")
        self.ok_btn.setObjectName("export")
        self.ok_btn.clicked.connect(self.accept)
        button_row.addWidget(self.ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)
        self._on_text_changed()

    def _on_text_changed(self):
        self.ok_btn.setEnabled(bool(self.name_edit.text().strip()))

    def name_text(self):
        return self.name_edit.text().strip()

    def tags_list(self):
        return [tag.strip() for tag in
                self.tags_edit.text().split(",") if tag.strip()]


class EditTagsDialog(QDialog):
    """Change a saved sprite's tags and collection (task 24).
    Both fields come pre-filled from the entry; clearing the
    collection field takes the sprite out of its collection."""

    def __init__(self, parent, entry):
        super().__init__(parent)
        self.setWindowTitle("Edit tags")
        self.resize(360, 200)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tags (comma separated)"))
        self.tags_edit = QLineEdit(
            ", ".join(entry.get("tags", [])))
        self.tags_edit.setPlaceholderText("hero, red, walk")
        layout.addWidget(self.tags_edit)
        layout.addWidget(QLabel("Collection (optional)"))
        self.collection_edit = QLineEdit(
            entry.get("collection") or "")
        self.collection_edit.setPlaceholderText("No collection")
        layout.addWidget(self.collection_edit)
        layout.addStretch(1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.ok_btn = QPushButton("Save")
        self.ok_btn.setObjectName("export")
        self.ok_btn.clicked.connect(self.accept)
        button_row.addWidget(self.ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def tags_list(self):
        return [tag.strip() for tag in
                self.tags_edit.text().split(",") if tag.strip()]

    def collection_text(self):
        return self.collection_edit.text().strip()


class SheetFrameDialog(QDialog):
    """Pick a frame from a library sheet to load into the Image
    tab (task 24). Nothing is picked by default — "Load frame"
    stays disabled until the user picks a row (a click, or the
    arrow keys). "Load sheet" loads the packed sheet, the old
    behavior; frame_index() returns None for that choice.
    Double-clicking a row (or Enter on one) loads it at once."""

    def __init__(self, parent, name, metadata):
        super().__init__(parent)
        self.setWindowTitle("Load from %s" % name)
        self.resize(360, 320)
        self._choice = None
        # Qt moves the list's current row to 0 when the dialog
        # is first shown — that is not a pick, and must not
        # enable "Load frame". The first currentRowChanged while
        # visible is the auto-set; later ones are the user's.
        self._row_change_seen = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Pick a frame to load, or load the packed sheet."))
        self.frame_list = QListWidget()
        self.frame_list.setToolTip(
            "Frames in the sheet, in playback order")
        for frame in metadata["frames"]:
            self.frame_list.addItem(frame["name"])
        self.frame_list.currentRowChanged.connect(
            self._on_row_changed)
        self.frame_list.itemClicked.connect(
            self._on_frame_clicked)
        self.frame_list.itemActivated.connect(
            self._on_frame_activated)
        layout.addWidget(self.frame_list, 1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.load_frame_btn = QPushButton("Load frame")
        self.load_frame_btn.setObjectName("export")
        self.load_frame_btn.setEnabled(False)
        self.load_frame_btn.setToolTip(
            "Load the picked frame into the Image tab")
        self.load_frame_btn.clicked.connect(self._on_load_frame)
        button_row.addWidget(self.load_frame_btn)
        self.load_sheet_btn = QPushButton("Load sheet")
        self.load_sheet_btn.setToolTip(
            "Load the packed sheet, as before")
        self.load_sheet_btn.clicked.connect(self._on_load_sheet)
        button_row.addWidget(self.load_sheet_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def _on_row_changed(self, row):
        if row < 0:
            return
        if self.isVisible() and not self._row_change_seen:
            # The auto-set to row 0 on first show — not a pick.
            self._row_change_seen = True
            return
        self._row_change_seen = True
        self.load_frame_btn.setEnabled(True)

    def _on_frame_clicked(self, _item):
        # A click on the already-current row fires no
        # currentRowChanged — but it is still a pick.
        self.load_frame_btn.setEnabled(True)

    def _on_frame_activated(self, item):
        self._choice = self.frame_list.row(item)
        self.accept()

    def _on_load_frame(self):
        self._choice = self.frame_list.currentRow()
        self.accept()

    def _on_load_sheet(self):
        self._choice = None
        self.accept()

    def frame_index(self):
        """The picked frame's index, or None for the packed
        sheet."""
        return self._choice


class TagDialog(QDialog):
    """Name one animation tag and its frame range (task 34).

    "From" and "to" are 1-based positions in the kept order —
    what the strip shows — so frame 1 is the first kept frame.
    The window converts to 0-based for storage and validates the
    name (non-empty, unique) and the range; this dialog only
    collects the values. Nothing is pre-filled except the range,
    which starts at the whole animation (1 to the last kept
    frame) — the only range that is always valid.
    """

    def __init__(self, parent, frame_count):
        super().__init__(parent)
        self.setWindowTitle("Add tag")
        self.resize(320, 200)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tag name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("walk")
        self.name_edit.setToolTip("A name for this set of frames")
        layout.addWidget(self.name_edit)
        from_row = QHBoxLayout()
        from_row.addWidget(QLabel("From frame"))
        self.from_spin = QSpinBox()
        self.from_spin.setRange(1, max(1, frame_count))
        self.from_spin.setValue(1)
        self.from_spin.setToolTip(
            "First kept frame of the tag, counting from 1")
        from_row.addWidget(self.from_spin)
        layout.addLayout(from_row)
        to_row = QHBoxLayout()
        to_row.addWidget(QLabel("To frame"))
        self.to_spin = QSpinBox()
        self.to_spin.setRange(1, max(1, frame_count))
        self.to_spin.setValue(max(1, frame_count))
        self.to_spin.setToolTip(
            "Last kept frame of the tag, counting from 1")
        to_row.addWidget(self.to_spin)
        layout.addLayout(to_row)
        layout.addStretch(1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.ok_btn = QPushButton("Add")
        self.ok_btn.setObjectName("export")
        self.ok_btn.setToolTip("Add this tag")
        self.ok_btn.clicked.connect(self.accept)
        button_row.addWidget(self.ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def tag_values(self):
        """(name, from, to) — from and to 0-based, for storage."""
        return (self.name_edit.text().strip(),
                self.from_spin.value() - 1,
                self.to_spin.value() - 1)


class EngineExportDialog(QDialog):
    """Export the kept frames in a game engine's format (task 36).

    The preset combo starts with nothing picked — Export stays
    disabled until one is chosen. The note under the combo names
    what the chosen preset writes and what it drops (frame
    durations and animation tags have no place in some formats).
    The folder picker chooses where the files go; the file names
    come from the video (sheet.png, sheet.json, and friends).
    written holds the paths the export wrote, once accepted.
    """

    def __init__(self, parent, images, names, durations, tags, cols,
                 padding, fps, folder, stem):
        super().__init__(parent)
        self.setWindowTitle("Export for engine")
        self.resize(420, 250)
        self._images = images
        self._names = names
        self._durations = durations
        self._tags = tags
        self._cols = cols
        self._padding = padding
        self._fps = fps
        self._stem = stem
        self.written = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Engine format"))
        self.engine_format_combo = QComboBox()
        self.engine_format_combo.setToolTip(
            "The engine format to export")
        for key in ENGINE_FORMATS:
            self.engine_format_combo.addItem(FORMAT_INFO[key][0],
                                             key)
        self.engine_format_combo.setCurrentIndex(-1)
        self.engine_format_combo.currentIndexChanged.connect(
            self._on_format_changed)
        layout.addWidget(self.engine_format_combo)
        self.engine_note_label = QLabel(
            "Pick a preset to see what it writes.")
        self.engine_note_label.setWordWrap(True)
        layout.addWidget(self.engine_note_label)
        layout.addWidget(QLabel("Folder"))
        folder_row = QHBoxLayout()
        self.engine_folder_edit = QLineEdit(str(folder))
        self.engine_folder_edit.setToolTip(
            "Where the exported files go")
        folder_row.addWidget(self.engine_folder_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.setToolTip(
            "Pick the folder for the exported files")
        browse_btn.clicked.connect(self._on_browse)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)
        layout.addStretch(1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.engine_export_btn = QPushButton("Export")
        self.engine_export_btn.setObjectName("export")
        self.engine_export_btn.setToolTip("Write the exported files")
        self.engine_export_btn.setEnabled(False)
        self.engine_export_btn.clicked.connect(self._on_export)
        button_row.addWidget(self.engine_export_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setToolTip("Close without exporting")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def format_key(self):
        """The picked preset's key, or None when nothing is picked."""
        return self.engine_format_combo.currentData()

    def _on_format_changed(self, _index):
        key = self.format_key()
        self.engine_export_btn.setEnabled(key is not None)
        if key is None:
            self.engine_note_label.setText(
                "Pick a preset to see what it writes.")
            return
        files = format_files(key, self._stem, len(self._images))
        self.engine_note_label.setText(
            "Writes %s. %s" % (" + ".join(files), format_note(key)))
        # The note grows from one line to several (the Godot
        # note is the longest) — a word-wrapped label does not
        # push its dialog taller on its own, so without this the
        # text overflows the label and is clipped. Recompute the
        # dialog's size from the layout after every change.
        self.adjustSize()

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Export folder", self.engine_folder_edit.text())
        if folder:
            self.engine_folder_edit.setText(folder)

    def _on_export(self):
        key = self.format_key()
        if key is None:
            return
        folder = self.engine_folder_edit.text().strip()
        if not folder:
            gui_module.warn_dialog(self, "Could not export",
                        "Pick a folder for the exported files first.")
            return
        try:
            self.written = export_engine(
                key, self._images, self._names, folder, self._stem,
                cols=self._cols, padding=self._padding, fps=self._fps,
                durations=self._durations, tags=self._tags)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export", str(exc))
            return
        self.accept()


# ------------------------------------------------------- undo commands
# Task 30: the window's undoable changes, one class per kind.
# Each command is pushed AFTER its change already happened; undo()
# restores the state from before, redo() re-applies the change,
# discard() frees what the entry owns when it leaves the stack
# for good. The window drives them through _push_undo().

class _LibraryDeleteCommand(UndoCommand):
    """A library delete, undone from the trash folder: the
    entry's files were moved to trash/<id>/ instead of being
    unlinked, so undo moves them back and re-inserts the entry
    at its old index. discard() deletes the trashed files for
    real — that is the moment the delete becomes permanent."""

    def __init__(self, win, entry, index, trash_dir):
        super().__init__("Delete %s" % entry["name"])
        self.win = win
        self.entry = dict(entry)
        self.index = index
        self.trash_dir = Path(trash_dir)

    def _delete(self):
        win = self.win
        for key in ("file", "meta"):
            name = self.entry.get(key)
            if not name:
                continue
            src = win.library.directory / name
            if src.is_file():
                self.trash_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(self.trash_dir / name))
        win.library.remove_index_only(self.entry["id"])
        win._refresh_library()

    def _restore(self):
        win = self.win
        win.library.directory.mkdir(parents=True, exist_ok=True)
        entry = dict(self.entry)
        # A new sprite may have taken the stem while this one sat
        # in the trash — give the restored files a fresh stem
        # instead of clobbering the new sprite. One stem for both
        # files, so a sheet pair stays a pair.
        fresh_stem = None
        file_name = entry.get("file")
        if file_name:
            src = self.trash_dir / file_name
            if (src.is_file()
                    and (win.library.directory / file_name).exists()):
                fresh_stem = win.library._unique_stem(
                    Path(file_name).stem)
        for key in ("file", "meta"):
            name = entry.get(key)
            if not name:
                continue
            src = self.trash_dir / name
            if not src.is_file():
                continue  # an orphan had no file to trash
            if fresh_stem is not None:
                name = fresh_stem + Path(name).suffix
                if key == "file":
                    entry["file"] = name
                    entry["id"] = fresh_stem
                else:
                    entry["meta"] = name
            dest = win.library.directory / name
            if dest.exists():
                # Only the meta collided (a stray file with the
                # same name): give it its own fresh stem.
                stem = win.library._unique_stem(Path(name).stem)
                name = stem + Path(name).suffix
                entry["meta"] = name
                dest = win.library.directory / name
            shutil.move(str(src), str(dest))
        # The entry's files are back in the library — the now
        # empty trash subdir goes too, so a restore leaves no
        # empty folders behind. redo() re-creates it via
        # mkdir() in _delete() when the delete runs again.
        self._clean_trash()
        win.library.restore_entry(entry, self.index)
        win._refresh_library()

    def _clean_trash(self):
        # Remove the entry's trash subdir and the trash folder
        # itself, but only while they are empty (rmdir refuses
        # otherwise — a sibling entry's files are untouched).
        try:
            self.trash_dir.rmdir()
        except OSError:
            pass
        try:
            self.trash_dir.parent.rmdir()
        except OSError:
            pass

    def undo(self):
        self._restore()

    def redo(self):
        self._delete()

    def discard(self):
        shutil.rmtree(self.trash_dir, ignore_errors=True)
        self._clean_trash()


class _PresetDeleteCommand(UndoCommand):
    """A preset delete: the settings are small, so the command
    keeps them in memory and saves them back on undo."""

    def __init__(self, win, name, settings, tab):
        super().__init__("Delete preset %s" % name)
        self.win = win
        self.name = name
        self.settings = dict(settings)
        self.tab = tab

    def undo(self):
        self.win.preset_store.save(self.name, self.settings)
        self.win._refresh_preset_combos()
        self.win._select_preset(self.tab, "user:%s" % self.name)

    def redo(self):
        self.win.preset_store.delete(self.name)
        self.win._refresh_preset_combos()


class _KeepDropCommand(UndoCommand):
    """A keep/drop change on the Video strip: the kept set
    before and after, as plain lists of booleans indexed by
    frame number."""

    def __init__(self, win, before, after, text):
        super().__init__(text)
        self.win = win
        self.before = list(before)
        self.after = list(after)

    def undo(self):
        self.win._apply_keep(self.before)

    def redo(self):
        self.win._apply_keep(self.after)


class _ControlChangeCommand(UndoCommand):
    """A control change on one generation tab: the control state
    before and after (the six pixelize fields plus the picker
    selection — see _read_control_state)."""

    def __init__(self, win, tab, before, after, text):
        super().__init__(text)
        self.win = win
        self.tab = tab
        self.before = dict(before)
        self.after = dict(after)

    def undo(self):
        self.win._apply_control_state(self.tab, self.before)

    def redo(self):
        self.win._apply_control_state(self.tab, self.after)


class _PixelEditCommand(UndoCommand):
    """One pixel edit on one layer of the Image tab's result
    (task 33, extended for layers in task 35): the whole layer
    image before and after. Results are small (at most
    512 x 512), so a full snapshot per step is cheap — and it
    keeps undo exact for every tool, including fill. The
    command holds the LAYER object, not an index — a reorder
    moves the object in the list, so the entry still points at
    the right layer. The window installs a COPY of the
    snapshot, so a later edit never mutates what this entry
    holds. When the layer is gone from the stack — its delete
    entry was discarded at the cap while this entry survived —
    undo and redo do nothing: the edit died with the layer."""

    def __init__(self, win, layer, before, after, text):
        super().__init__(text)
        self.win = win
        self.layer = layer
        self.before = before
        self.after = after

    def undo(self):
        self.win._set_layer_image(self.layer, self.before)

    def redo(self):
        self.win._set_layer_image(self.layer, self.after)


class _LayerStackCommand(UndoCommand):
    """One layer-stack change (task 35): add, delete, or move
    a layer. The snapshots are the layer LISTS before and
    after, with the active layer each time — shallow copies
    holding the same Layer objects, so no image is copied.
    That is safe because the stack is last-in-first-out: any
    pixel edit made after the stack change must be undone
    before this entry is reached, so the layers' images are
    exactly as they were when the stack change landed."""

    def __init__(self, win, before, after, before_active,
                 after_active, text):
        super().__init__(text)
        self.win = win
        self.before = list(before)
        self.after = list(after)
        self.before_active = before_active
        self.after_active = after_active

    def undo(self):
        self.win._install_layer_stack(self.before,
                                      self.before_active)

    def redo(self):
        self.win._install_layer_stack(self.after,
                                      self.after_active)


def _parse_hex_color(text):
    """A typed hex color ("#rrggbb" or "rrggbb") as an (r, g, b)
    tuple, or None when it is not one."""
    text = text.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16),
                int(text[4:6], 16))
    except ValueError:
        return None


class PaletteEditorDialog(QDialog):
    """Create, edit, import, and export palettes (task 39).

    The left list holds every palette — built-ins first, then
    saved palettes — with a swatch strip under it showing the
    picked palette's colors. The right side edits the picked
    palette: its name, and its colors (add via the color picker,
    remove, move up/down, or edit a hex value by typing it).
    Built-ins are read-only here — they can be duplicated and
    the copy edited, or exported, but not changed or deleted.

    Nothing is picked by default: the dialog opens with no
    palette picked, and the right side stays disabled until the
    user picks one or starts a new palette. `changed` is True
    when anything was saved, renamed, deleted, or imported, so
    the window knows to refresh its palette pickers.
    """

    def __init__(self, parent, store, prefill_colors=None,
                 prefill_name=None):
        super().__init__(parent)
        self.setWindowTitle("Palettes")
        self.resize(640, 420)
        self.store = store
        self.changed = False
        # The picked palette: None, or ("builtin", key) /
        # ("user", name). The working copy being edited: None,
        # or {"name": str, "colors": [(r, g, b)], "original":
        # name-or-None (None for a brand-new palette),
        # "builtin": bool}.
        self._picked = None
        self._editing = None
        # Qt moves the list's current row to 0 when the dialog
        # is first shown — that is not a pick (see
        # SheetFrameDialog for the same guard).
        self._row_change_seen = False

        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("Palettes"))
        self.palette_list = QListWidget()
        self.palette_list.setToolTip(
            "Built-in palettes, then your saved palettes")
        self.palette_list.currentRowChanged.connect(
            self._on_row_changed)
        self.palette_list.itemClicked.connect(
            self._on_palette_clicked)
        left.addWidget(self.palette_list, 1)
        self.swatch_label = QLabel()
        self.swatch_label.setFixedHeight(28)
        self.swatch_label.setToolTip(
            "The picked palette's colors")
        left.addWidget(self.swatch_label)
        left_buttons = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.new_btn.setToolTip("Create a new palette")
        self.new_btn.clicked.connect(self._on_new)
        left_buttons.addWidget(self.new_btn)
        self.duplicate_btn = QPushButton("Duplicate")
        self.duplicate_btn.setToolTip(
            "Copy the picked palette into a new one")
        self.duplicate_btn.clicked.connect(self._on_duplicate)
        left_buttons.addWidget(self.duplicate_btn)
        self.import_btn = QPushButton("Import")
        self.import_btn.setToolTip(
            "Import a palette file (.hex, .gpl, .pal, .ase, .txt)")
        self.import_btn.clicked.connect(self._on_import)
        left_buttons.addWidget(self.import_btn)
        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip(
            "Export the picked palette to .hex or .gpl")
        self.export_btn.clicked.connect(self._on_export)
        left_buttons.addWidget(self.export_btn)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setToolTip("Delete the picked palette")
        self.delete_btn.clicked.connect(self._on_delete)
        left_buttons.addWidget(self.delete_btn)
        left.addLayout(left_buttons)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit()
        self.name_edit.setToolTip("The palette's name")
        self.name_edit.textChanged.connect(self._update_buttons)
        right.addWidget(self.name_edit)
        self.start_from_label = QLabel("Start from")
        right.addWidget(self.start_from_label)
        self.start_from_combo = QComboBox()
        self.start_from_combo.setToolTip(
            "Copy the colors of an existing palette, or start "
            "empty")
        self.start_from_combo.currentIndexChanged.connect(
            self._on_start_from)
        right.addWidget(self.start_from_combo)
        right.addWidget(QLabel("Colors"))
        self.color_list = QListWidget()
        self.color_list.setToolTip(
            "The palette's colors, in order")
        self.color_list.currentRowChanged.connect(
            self._on_color_row_changed)
        right.addWidget(self.color_list, 1)
        color_btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.setToolTip("Add a color with the color picker")
        self.add_btn.clicked.connect(self._on_add_color)
        color_btn_row.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setToolTip("Remove the picked color")
        self.remove_btn.clicked.connect(self._on_remove_color)
        color_btn_row.addWidget(self.remove_btn)
        self.up_btn = QPushButton("Move up")
        self.up_btn.setToolTip("Move the picked color up one place")
        self.up_btn.clicked.connect(lambda: self._on_move_color(-1))
        color_btn_row.addWidget(self.up_btn)
        self.down_btn = QPushButton("Move down")
        self.down_btn.setToolTip(
            "Move the picked color down one place")
        self.down_btn.clicked.connect(lambda: self._on_move_color(1))
        color_btn_row.addWidget(self.down_btn)
        right.addLayout(color_btn_row)
        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("Hex"))
        self.hex_edit = QLineEdit()
        self.hex_edit.setToolTip(
            "The picked color as a hex value — type a new one "
            "and press Set")
        self.hex_edit.setPlaceholderText("#rrggbb")
        hex_row.addWidget(self.hex_edit, 1)
        self.set_hex_btn = QPushButton("Set")
        self.set_hex_btn.setToolTip(
            "Set the picked color to the typed hex value")
        self.set_hex_btn.clicked.connect(self._on_set_hex)
        hex_row.addWidget(self.set_hex_btn)
        right.addLayout(hex_row)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("export")
        self.save_btn.setToolTip("Save this palette")
        self.save_btn.clicked.connect(self._on_save)
        right.addWidget(self.save_btn)
        layout.addLayout(right, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        right.addLayout(close_row)

        self._refresh_palette_list()
        self._update_buttons()
        if prefill_colors is not None:
            # The extract flow: open straight in the create flow
            # with the extracted colors and suggested name.
            self._on_new()
            self.name_edit.setText(prefill_name or "")
            self._editing["colors"] = list(prefill_colors)
            self._refresh_color_list()
            self._update_buttons()

    # ---------------------------------------------------------- list

    def _refresh_palette_list(self):
        """Rebuild the palette list from the store, keeping the
        pick when the palette still exists."""
        picked = self._picked
        self.palette_list.blockSignals(True)
        try:
            self.palette_list.clear()
            try:
                palettes = self.store.all_palettes()
            except PixelForgeError as exc:
                gui_module.warn_dialog(self, "Could not list palettes",
                            str(exc))
                palettes = []
            for name, colors, builtin in palettes:
                label = PALETTE_LABELS.get(name, name) if builtin \
                    else name
                item = QListWidgetItem(
                    "%s — %d %s" % (
                        label, len(colors),
                        "color" if len(colors) == 1 else "colors"))
                item.setData(Qt.ItemDataRole.UserRole,
                             ("builtin" if builtin else "user", name))
                self.palette_list.addItem(item)
        finally:
            self.palette_list.blockSignals(False)
        if picked is not None:
            for row in range(self.palette_list.count()):
                item = self.palette_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == picked:
                    self._select_palette(picked)
                    break
            else:
                # The picked palette is gone (renamed or
                # deleted) — clear the pick and the editor.
                self._picked = None
                self._editing = None
                self.name_edit.clear()
                self._paint_swatches([])
                self._refresh_color_list()
                self._update_buttons()
        else:
            self.palette_list.setCurrentRow(-1)
            self._paint_swatches([])

    def _select_palette(self, picked):
        """Pick a palette by ("builtin"/"user", name) — the
        programmatic pick used after a save or import. Moves
        the list row and fills the right side."""
        for row in range(self.palette_list.count()):
            item = self.palette_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == picked:
                self.palette_list.blockSignals(True)
                self.palette_list.setCurrentRow(row)
                self.palette_list.blockSignals(False)
                break
        self._picked = picked
        self._row_change_seen = True
        self._load_editing()

    def _on_row_changed(self, row):
        if row < 0:
            return
        if self.isVisible() and not self._row_change_seen:
            # The auto-set to row 0 on first show — not a pick.
            self._row_change_seen = True
            return
        self._row_change_seen = True
        item = self.palette_list.item(row)
        if item is not None:
            self._picked = item.data(Qt.ItemDataRole.UserRole)
            self._load_editing()

    def _on_palette_clicked(self, item):
        # A click on the already-current row fires no
        # currentRowChanged — but it is still a pick.
        self._picked = item.data(Qt.ItemDataRole.UserRole)
        self._row_change_seen = True
        self._load_editing()

    def _load_editing(self):
        """Fill the right side from the picked palette — a
        working copy for a user palette, a read-only view for a
        built-in."""
        if self._picked is None:
            self._editing = None
            self.name_edit.clear()
            self._refresh_color_list()
            self._update_buttons()
            return
        kind, name = self._picked
        builtin = kind == "builtin"
        colors = self.store.get(name, builtin=builtin)
        if colors is None:
            self._picked = None
            self._editing = None
            self._refresh_color_list()
            self._update_buttons()
            return
        self._editing = {"name": PALETTE_LABELS.get(name, name)
                         if builtin else name,
                         "colors": list(colors),
                         "original": None if builtin else name,
                         "builtin": builtin}
        self.name_edit.blockSignals(True)
        self.name_edit.setText(self._editing["name"])
        self.name_edit.blockSignals(False)
        self._paint_swatches(colors)
        self._refresh_color_list()
        self._update_buttons()

    def _paint_swatches(self, colors):
        """The swatch strip: the palette's colors side by side,
        or blank when nothing is picked."""
        width = max(1, self.swatch_label.width())
        height = self.swatch_label.height() or 28
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(0, 0, 0, 0))
        if colors:
            painter = QPainter(pixmap)
            try:
                step = width / len(colors)
                for i, (r, g, b) in enumerate(colors):
                    painter.fillRect(int(i * step), 0,
                                     max(1, int(step) + 1), height,
                                     QColor(r, g, b))
            finally:
                painter.end()
        self.swatch_label.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._editing is not None:
            self._paint_swatches(self._editing["colors"])

    # -------------------------------------------------------- editing

    def _refresh_color_list(self):
        """Rebuild the color rows from the working copy."""
        self.color_list.blockSignals(True)
        try:
            self.color_list.clear()
            if self._editing is None:
                return
            for rgb in self._editing["colors"]:
                pixmap = QPixmap(16, 16)
                pixmap.fill(QColor(*rgb))
                item = QListWidgetItem(
                    QIcon(pixmap), "#%02x%02x%02x" % rgb)
                item.setData(Qt.ItemDataRole.UserRole, rgb)
                self.color_list.addItem(item)
        finally:
            self.color_list.blockSignals(False)
        self._on_color_row_changed(self.color_list.currentRow())

    def _on_color_row_changed(self, row):
        if row >= 0 and self._editing is not None:
            rgb = self._editing["colors"][row]
            self.hex_edit.setText("#%02x%02x%02x" % rgb)
        else:
            self.hex_edit.clear()
        self._update_buttons()

    def _update_buttons(self):
        """Enable what the current state allows. Nothing picked:
        only New, Import, and Close. A built-in picked: Duplicate
        and Export, nothing that changes it. A user palette or a
        new one: the full editor, with Save needing a name and
        at least one color."""
        editing = self._editing
        picked = self._picked
        is_new = editing is not None \
            and editing["original"] is None \
            and not editing["builtin"]
        self.start_from_label.setVisible(is_new)
        self.start_from_combo.setVisible(is_new)
        self.name_edit.setEnabled(editing is not None
                                  and not editing["builtin"])
        self.color_list.setEnabled(editing is not None)
        row = self.color_list.currentRow()
        has_row = editing is not None and row >= 0
        editable = editing is not None and not editing["builtin"]
        self.add_btn.setEnabled(editable
                                and len(editing["colors"]) < 256)
        self.remove_btn.setEnabled(editable and has_row)
        self.up_btn.setEnabled(editable and row > 0)
        self.down_btn.setEnabled(
            editable and has_row
            and row < len(editing["colors"]) - 1)
        self.set_hex_btn.setEnabled(editable and has_row)
        self.hex_edit.setEnabled(editable and has_row)
        self.save_btn.setEnabled(
            editable and bool(self.name_edit.text().strip())
            and len(editing["colors"]) >= 1)
        self.duplicate_btn.setEnabled(picked is not None)
        self.export_btn.setEnabled(picked is not None)
        self.delete_btn.setEnabled(
            picked is not None and picked[0] == "user")

    def _on_new(self):
        """Start a new palette — empty, or copied from the
        "Start from" picker."""
        self._picked = None
        self.palette_list.blockSignals(True)
        self.palette_list.setCurrentRow(-1)
        self.palette_list.blockSignals(False)
        self._editing = {"name": "", "colors": [],
                         "original": None, "builtin": False}
        self.name_edit.blockSignals(True)
        self.name_edit.clear()
        self.name_edit.blockSignals(False)
        self.start_from_combo.blockSignals(True)
        self.start_from_combo.clear()
        self.start_from_combo.addItem("Empty", None)
        try:
            palettes = self.store.all_palettes()
        except PixelForgeError:
            palettes = []
        for name, colors, builtin in palettes:
            label = PALETTE_LABELS.get(name, name) if builtin \
                else name
            self.start_from_combo.addItem(
                "%s — %d %s" % (
                    label, len(colors),
                    "color" if len(colors) == 1 else "colors"),
                ("builtin" if builtin else "user", name))
        self.start_from_combo.blockSignals(False)
        self._paint_swatches([])
        self._refresh_color_list()
        self._update_buttons()
        self.name_edit.setFocus()

    def _on_start_from(self, index):
        if self._editing is None or index < 0:
            return
        picked = self.start_from_combo.itemData(index)
        if picked is None:
            self._editing["colors"] = []
        else:
            kind, name = picked
            colors = self.store.get(name, builtin=(kind == "builtin"))
            if colors is not None:
                self._editing["colors"] = list(colors)
        self._paint_swatches(self._editing["colors"])
        self._refresh_color_list()
        self._update_buttons()

    def _on_duplicate(self):
        """Copy the picked palette into a new palette — the name
        gets " copy" (numbered when that is taken), and the copy
        is what gets edited."""
        if self._picked is None or self._editing is None:
            return
        base = self._editing["name"]
        taken = {name.casefold() for name, _c, _b
                 in self.store.all_palettes()}
        taken.add("image")
        name = "%s copy" % base
        number = 2
        while name.casefold() in taken:
            name = "%s copy %d" % (base, number)
            number += 1
        colors = list(self._editing["colors"])
        self._on_new()
        self.name_edit.setText(name)
        self._editing["colors"] = colors
        self._paint_swatches(colors)
        self._refresh_color_list()
        self._update_buttons()

    def _on_add_color(self):
        if self._editing is None or self._editing["builtin"]:
            return
        color = QColorDialog.getColor(QColor(0, 0, 0), self,
                                      "Pick a color")
        if not color.isValid():
            return
        self._editing["colors"].append(
            (color.red(), color.green(), color.blue()))
        self._paint_swatches(self._editing["colors"])
        self._refresh_color_list()
        self.color_list.setCurrentRow(
            len(self._editing["colors"]) - 1)
        self._update_buttons()

    def _on_remove_color(self):
        if self._editing is None or self._editing["builtin"]:
            return
        row = self.color_list.currentRow()
        if row < 0:
            return
        del self._editing["colors"][row]
        self._paint_swatches(self._editing["colors"])
        self._refresh_color_list()
        self._update_buttons()

    def _on_move_color(self, step):
        if self._editing is None or self._editing["builtin"]:
            return
        row = self.color_list.currentRow()
        colors = self._editing["colors"]
        if row < 0 or row + step < 0 or row + step >= len(colors):
            return
        colors[row], colors[row + step] = colors[row + step], \
            colors[row]
        self._paint_swatches(colors)
        self._refresh_color_list()
        self.color_list.setCurrentRow(row + step)
        self._update_buttons()

    def _on_set_hex(self):
        if self._editing is None or self._editing["builtin"]:
            return
        row = self.color_list.currentRow()
        if row < 0:
            return
        rgb = _parse_hex_color(self.hex_edit.text())
        if rgb is None:
            gui_module.warn_dialog(self, "Not a color",
                        "%r is not a hex color — use #rrggbb"
                        % self.hex_edit.text().strip())
            return
        self._editing["colors"][row] = rgb
        self._paint_swatches(self._editing["colors"])
        self._refresh_color_list()
        self.color_list.setCurrentRow(row)
        self._update_buttons()

    def _on_save(self):
        """Commit the working copy — a new palette, or an edit
        (and possible rename) of a user palette. Saving over
        another palette's name asks first."""
        if self._editing is None or self._editing["builtin"]:
            return
        name = self.name_edit.text().strip()
        colors = self._editing["colors"]
        original = self._editing["original"]
        if original is None:
            existing = self.store.find(name)
            if existing is not None:
                answer = QMessageBox.question(
                    self, "Replace palette?",
                    "A palette named %r already exists. Replace "
                    "it?" % existing,
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
        try:
            if original is not None and name != original:
                self.store.rename(original, name)
            self.store.save(name, colors)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not save palette", str(exc))
            return
        self.changed = True
        self._refresh_palette_list()
        self._select_palette(("user", name))

    def _on_delete(self):
        """Delete the picked user palette — Yes/No, default No."""
        if self._picked is None or self._picked[0] != "user":
            return
        name = self._picked[1]
        answer = QMessageBox.question(
            self, "Delete palette?",
            "Delete the palette %r?" % name,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(name)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not delete palette", str(exc))
            return
        self.changed = True
        self._picked = None
        self._editing = None
        self.name_edit.clear()
        self._refresh_palette_list()
        self._refresh_color_list()
        self._update_buttons()

    def _on_import(self):
        """Import a palette file — the five formats — into a
        saved palette. A name clash asks Yes/No to replace."""
        from .palette_io import read_palette_file

        path, _ = QFileDialog.getOpenFileName(
            self, "Import palette", "",
            "Palette files (*.hex *.gpl *.pal *.ase *.txt)")
        if not path:
            return
        try:
            file_name, _colors = read_palette_file(path)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import palette", str(exc))
            return
        overwrite = False
        existing = self.store.find(file_name)
        if existing is not None:
            answer = QMessageBox.question(
                self, "Replace palette?",
                "A palette named %r already exists. Replace it?"
                % existing,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        try:
            name = self.store.import_file(path, overwrite=overwrite)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not import palette", str(exc))
            return
        self.changed = True
        self._refresh_palette_list()
        self._select_palette(("user", name))

    def _on_export(self):
        """Export the picked palette to .hex or .gpl."""
        if self._picked is None:
            return
        kind, name = self._picked
        builtin = kind == "builtin"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export palette", "%s.hex" % name,
            "Lospec hex (*.hex);;GIMP palette (*.gpl)")
        if not path:
            return
        try:
            self.store.export_palette(name, path, builtin=builtin)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not export palette", str(exc))
            return


def clamp_geometry(rect, available):
    """A saved window rectangle pulled back onto the screen (task
    66). The size is capped at the available size and the position
    is moved so the whole window is visible — a saved rectangle
    from a bigger or second monitor never strands the window
    off-screen. An empty available rectangle (no screen) leaves
    the saved rectangle alone."""
    if available.width() < 1 or available.height() < 1:
        return QRect(rect)
    width = min(rect.width(), available.width())
    height = min(rect.height(), available.height())
    x = min(max(rect.x(), available.left()),
            available.left() + available.width() - width)
    y = min(max(rect.y(), available.top()),
            available.top() + available.height() - height)
    return QRect(x, y, width, height)