"""Pixel Forge window — native Qt GUI, dark theme.

Five tabs. Image: import a picture, pick a size and a palette, watch
the before and after side by side, export a transparent PNG.
Conversion is live: every control change re-pixelizes at once.
Video: import a clip, extract frames with ffmpeg, keep or drop frames
in the strip, watch the animation play, export the kept frames as
numbered PNGs — or pack them into a spritesheet PNG + metadata JSON
a game can play. Frames pixelize through the same Item 1 core.
Prompt: describe a sprite, generate 3 candidates with the local
image engine, click one to keep it, pixelize it with the same
controls, export a transparent PNG. Generation runs in a worker
thread — a minutes-long CPU run never freezes the window.
Code: import a .py / .js / .ts file (or a folder of them) and the
pixel art the code defines — pixel maps, palettes — is rebuilt as
real sprites, parsed statically and never executed. Or import a
game asset: a Minecraft skin splits into its parts, anything else
pixelizes as a texture. Export PNG and Save to library work like
the other tabs.
Library: every sprite saved from the other tabs, with tags and
search — click one to preview it, load it back into the Image tab,
rename it, delete it, or export a copy.
Each of the Image, Video, and Prompt tabs also has a preset picker:
one pick sets size, palette, colors, dithering, and aspect together,
and the current controls can be saved as a named preset.

No bare "&" in any visible label text anywhere in this file — Qt eats
a bare & as a mnemonic marker and renders "_" in its place while the
underlying text() still returns "&", so a string check alone would
stay green on a wrong label. Write "and" instead.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QDesktopServices, QImage, QImageReader,
                           QKeySequence, QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                               QDialog, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSlider,
                               QSpinBox, QSplitter, QStackedWidget,
                               QStatusBar, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from . import __version__
from .codeinput import CODE_EXTENSIONS, parse_paths
from .history import (batches_dir, load_history, record_batch,
                      record_prompt, save_history)
from .imagegen import (DEFAULT_COUNT, DEFAULT_NEGATIVE, DEFAULT_SIZE,
                       LCM_CFG, LCM_STEPS, MAX_PROMPT_LEN,
                       NEGATIVE_PRESETS, GenerationCancelled,
                       MODEL_FILENAME, MODEL_URL, engine_status,
                       generate, load_batch, new_batch,
                       regenerate_candidate, require_engine,
                       require_model, set_keeper, validate_prompt)
from .library import SOURCE_LABELS, Library
from .palettes import PALETTE_LABELS
from .pixelize import (MAX_COLORS, MIN_COLORS, PixelForgeError,
                       count_opaque_colors, load_image, pixelize_image,
                       plural)
from .presets import (BUILTIN_PRESETS, SHEET_COLS_VALUES, PresetStore,
                      read_preset_export)
from .qtimage import to_qimage
from .skins import (detect_skin, extract_skin, pixelize_texture,
                    skin_figure, skin_parts)
from .spritesheet import (build_metadata, frames_from_sheet,
                          pack_frames, write_sheet)
from .video import (DEFAULT_FPS, FPS_PRESETS, VIDEO_EXTENSIONS,
                    extract_frames, require_ffmpeg)

SIZE_PRESETS = [16, 32, 48, 64, 128]
MAX_DISPLAY = 1600  # longest side of a preview pixmap, in pixels
ORIGINAL_FIT = 352  # longest side of the Original preview, in pixels
# (each preview pane is ~360 px wide at the default window size)
MAX_CUSTOM_SIZE = 512
STRIP_THUMB = 64  # frame strip thumbnail size, in pixels
VIDEO_DEBOUNCE_MS = 300  # ms of no control changes before a Video
# tab re-extract / re-pixelize runs — a dragged slider fires
# dozens of changes, and only the settled one needs a run.

# Export-button contrast (task 22): the text is #e8e6e3 on the
# button background. The old orange (#b35a1b) measured 3.84:1 —
# under WCAG AA's 4.5:1 for normal text (review W19). The darker
# orange below measures 5.73:1, the hover 4.93:1 — both pass.
# (The disabled state is exempt from the ratio: WCAG does not
# require contrast for inactive controls.) This comment lives
# OUTSIDE the stylesheet string: Qt stylesheets have no # comments,
# and a # line inside the string would be parsed as a selector.
DARK_STYLESHEET = """
QMainWindow, QWidget { background-color: #232329; color: #e8e6e3; }
QLabel { background-color: transparent; }
QLabel#heading { font-weight: bold; font-size: 13px; color: #ffb066; }
QPushButton { background-color: #3a3a42; border: 1px solid #55555e;
    border-radius: 4px; padding: 7px 14px; }
QPushButton:hover { background-color: #4a4a54; }
QPushButton:pressed { background-color: #2e2e35; }
QPushButton:disabled { color: #77777f; border-color: #3a3a42; }
QPushButton#export { background-color: #8a4513; border-color: #d97a2e;
    font-weight: bold; }
QPushButton#export:hover { background-color: #9a4c15; }
QPushButton#export:disabled { background-color: #4a3226; color: #77777f; }
QComboBox, QSpinBox, QLineEdit { background-color: #2e2e35;
    border: 1px solid #55555e; border-radius: 4px; padding: 4px; }
QComboBox::drop-down { border: none; }
QSlider::groove:horizontal { height: 6px; background: #3a3a42;
    border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -5px 0;
    background: #e07020; border-radius: 8px; }
QScrollArea { border: 1px solid #3a3a42; background-color: #1a1a1f; }
QListWidget { background-color: #1a1a1f; border: 1px solid #3a3a42; }
QListWidget::item:selected { background-color: #3a3a42; }
QSplitter::handle { background-color: #3a3a42; }
QStatusBar { background-color: #1a1a1f; color: #b8b5b0; }
"""


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
    """
    done = Signal(dict)   # path, source size, texture, color count
    failed = Signal(str)  # readable error message

    def __init__(self, image, path, parent=None):
        super().__init__(parent)
        self.image = image
        self.path = str(path)

    def run(self):
        try:
            texture = pixelize_texture(self.image)
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
    done = Signal(dict)   # mode, frames, raw, raw_dir, fps, fresh, path
    failed = Signal(str)  # readable error message

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)

    def run(self):
        settings = self.settings
        tmp = None
        try:
            if settings["mode"] == "reload":
                tmp = tempfile.mkdtemp(prefix="pixel-forge-frames-")
                raw = extract_frames(
                    settings["path"], tmp,
                    fps=settings["fps"], every=settings["every"],
                    start=settings["start"], end=settings["end"])
            else:
                raw = settings["raw_frames"]
            frames = [pixelize_image(
                load_image(raw_path),
                width=settings["width"], height=settings["height"],
                keep_aspect=settings["keep_aspect"],
                palette=settings["palette"],
                max_colors=settings["max_colors"],
                dither=settings["dither"])
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
        })


class SpritesheetDialog(QDialog):
    """Preview a packed spritesheet before exporting it.

    Shows the whole sheet (checkerboard behind transparency) and
    plays the animation FROM the sheet — the frames are cut back
    out with the metadata, so what plays is exactly what a game
    would play from the PNG + JSON pair. Export writes that pair.
    """

    def __init__(self, parent, images, names, cols, padding, fps,
                 default_name):
        super().__init__(parent)
        self.setWindowTitle("Spritesheet preview")
        self.resize(560, 660)
        self.images = list(images)
        self.names = list(names)
        self.cols = cols
        self.padding = padding
        self.fps = fps
        self.default_name = default_name
        self.sheet, self.layout = pack_frames(
            self.images, cols=cols, padding=padding)
        frame_w, frame_h = (self.images[0].width(),
                            self.images[0].height())
        self.metadata = build_metadata(
            "", self.names, frame_w, frame_h, self.layout, fps)
        self.frames = frames_from_sheet(self.sheet, self.metadata)
        self.current = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)

        layout = QVBoxLayout(self)
        info = ("%d frames, %d x %d grid, sheet %d x %d, %s fps, "
                "%d px padding"
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
        self.sheet_label.setPixmap(checkerboard_pixmap(
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
        self.frame_label.setPixmap(checkerboard_pixmap(
            frame, scale, smooth=False))
        self.counter_label.setText(
            "Frame %d of %d" % (self.current + 1, len(self.frames)))

    def _advance(self):
        self.current = (self.current + 1) % len(self.frames)
        self._refresh_frame()

    def _on_play_pause(self):
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setText("Play")
            return
        interval = max(1, int(round(1000 / self.fps)))
        self.timer.start(interval)
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
        try:
            write_sheet(self.images, self.names,
                        str(png_path), str(meta_path),
                        cols=self.cols, padding=self.padding,
                        fps=self.fps)
        except PixelForgeError as exc:
            warn_dialog(self, "Could not export spritesheet", str(exc))
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "statusBar"):
            parent.statusBar().showMessage(
                "Exported spritesheet: %d frames, %d x %d grid, %s"
                % (len(self.images), self.layout["columns"],
                   self.layout["rows"], png_path.name))
            # The sheet's folder becomes the last-used export
            # folder, like every other export (task 22).
            if hasattr(parent, "_remember_export_dir"):
                parent._remember_export_dir(png_path.parent)
        self.accept()


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


class MainWindow(QMainWindow):
    def __init__(self, library_dir=None):
        super().__init__()
        self.setWindowTitle("Pixel Forge %s" % __version__)
        self.resize(1020, 640)
        self.setAcceptDrops(True)
        # Library + presets. library_dir is a test hook — the real
        # window uses ~/pixel-forge-library/ (or PIXELFORGE_LIBRARY).
        # Nothing is created on disk until the first save.
        self.library = Library(library_dir)
        self.preset_store = PresetStore(self.library.directory)
        self._applying_preset = False
        # The malformed-entries warning shows in the status bar
        # once per window; the Library tab's count label carries
        # it permanently (see _refresh_library).
        self._library_skip_warned = False
        self.source_image = None
        self.source_path = None
        self.result_image = None
        self._updating_size = False

        # Video tab state. Raw frames live on disk in a temp dir;
        # only the small pixelized frames are held in memory.
        self.video_path = None
        self.video_raw_dir = None
        self.video_raw_frames = []
        self.video_frames = []
        self.video_keep = []
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

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_image_tab(), "Image")
        self.tabs.addTab(self._build_video_tab(), "Video")
        self.tabs.addTab(self._build_prompt_tab(), "Prompt")
        self.tabs.addTab(self._build_code_tab(), "Code")
        self.tabs.addTab(self._build_library_tab(), "Library")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_menu_bar()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready — import an image to begin.")
        self._refresh_preset_combos()
        self._refresh_library()
        self._refresh_prompt_status()
        self._load_prompt_history()

    # ================================================ menus, shortcuts, help

    def _build_menu_bar(self):
        """File + Help menus and the keyboard shortcuts (task 21).

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

        # Ctrl+1..5 switch tabs in their current order (Image,
        # Video, Prompt, Code, Library). Kept on self: a QShortcut
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
            "Source code (.py, .js, .ts) is read statically — it "
            "is never run, imported, or executed. Only literal "
            "values in the code are rendered.\n"
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
            "as a strip, one block per color.\n"
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
            "- sprites loaded from image files at run time, and "
            "anything drawn by a game engine, cannot be detected\n"
            "- skins are detected by size only: a non-skin "
            "64x64 image would be misread as a skin\n")

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
        warning.setStyleSheet("color: #ffb066;")
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

        form.addWidget(self._section("Size"))
        self.size_combo = QComboBox()
        self.size_combo.setToolTip("Sprite size in pixels")
        for preset in SIZE_PRESETS:
            self.size_combo.addItem("%d x %d" % (preset, preset), preset)
        self.size_combo.addItem("Custom", None)
        self.size_combo.setCurrentIndex(1)  # 32 x 32
        self.size_combo.currentIndexChanged.connect(self._on_preset)
        form.addWidget(self.size_combo)

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

        self.aspect_check = QCheckBox("Keep aspect ratio")
        self.aspect_check.setToolTip(
            "Keep the original proportions when resizing")
        self.aspect_check.toggled.connect(self._on_control_changed)
        form.addWidget(self.aspect_check)

        form.addWidget(self._section("Palette"))
        self.palette_combo = QComboBox()
        self.palette_combo.setToolTip(
            "Colors the sprite may use")
        for key in ("image", "pico8", "gameboy", "grayscale"):
            self.palette_combo.addItem(PALETTE_LABELS[key], key)
        self.palette_combo.currentIndexChanged.connect(
            self._on_control_changed)
        form.addWidget(self.palette_combo)

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

        self.dither_check = QCheckBox("Dithering")
        self.dither_check.setToolTip("Ordered (Bayer) dithering")
        self.dither_check.setChecked(True)
        self.dither_check.toggled.connect(self._on_control_changed)
        form.addWidget(self.dither_check)

        self._build_preset_section(form, "image")

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
        self.after_label = QLabel("Your pixel sprite appears here")
        self.after_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.after_label.setWordWrap(True)
        self.after_scroll = QScrollArea()
        self.after_scroll.setWidgetResizable(True)
        self.after_scroll.setWidget(self.after_label)
        after_layout.addWidget(self.after_scroll)
        previews.addWidget(after_pane)

        previews.setSizes([500, 500])
        main_layout.addWidget(previews, 1)
        return root

    # ---------------------------------------------------------- input

    def load_image(self, path):
        """Load an image from disk and refresh the previews.

        Returns True on success. On failure shows a message box and
        returns False — the window stays up either way.
        """
        try:
            image = load_image(path)
        except PixelForgeError as exc:
            warn_dialog(self, "Could not import image", str(exc))
            return False
        self.source_image = image
        self.source_path = str(path)
        self.update_preview()
        # The preview zoom fits the pane on load (task 22) — the
        # old fixed 8x left a 16x16 sprite tiny and a 128x128 one
        # overflowing. A later control change does not re-fit.
        self._on_fit_image_zoom()
        self.statusBar().showMessage("Loaded %s (%d x %d)"
                                     % (Path(path).name, image.width(),
                                        image.height()))
        return True

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import image", "", supported_filter())
        if path:
            self.load_image(path)

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
        # silently ignore the rest (review B15). Every supported
        # file is imported now — code files all go to the Code
        # tab; the Image and Video tabs hold one thing at a time,
        # so they take the first and the rest are reported, not
        # dropped quietly. The last tab switched to stays visible.
        imported = []
        skipped = []
        video_started = False
        if images:
            self.tabs.setCurrentIndex(0)
            if self.load_image(images[0]):
                imported.append("%s (image)" % Path(images[0]).name)
            for extra in images[1:]:
                skipped.append("%s (only the first image was loaded)"
                               % Path(extra).name)
        if videos:
            self.tabs.setCurrentIndex(1)
            if self.load_video(videos[0]):
                imported.append("%s (video)" % Path(videos[0]).name)
                video_started = True
            for extra in videos[1:]:
                skipped.append("%s (only the first video was loaded)"
                               % Path(extra).name)
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
                warn_dialog(
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
        preset = self.size_combo.currentData()
        if preset is None:
            return
        self._updating_size = True
        try:
            self.width_spin.setValue(preset)
            self.height_spin.setValue(preset)
        finally:
            self._updating_size = False
        self._on_control_changed()

    def _on_custom_size(self):
        if self._updating_size:
            return
        index = self.size_combo.findData(None)
        if index >= 0 and self.size_combo.currentIndex() != index:
            self.size_combo.blockSignals(True)
            self.size_combo.setCurrentIndex(index)
            self.size_combo.blockSignals(False)
        self._on_control_changed()

    def _on_control_changed(self):
        self._mark_preset_custom("image")
        if self.source_image is not None:
            self.update_preview()

    def _on_zoom_changed(self, value):
        self.zoom_label.setText("%dx" % value)
        if self.source_image is not None:
            self._refresh_pixmaps()

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
        try:
            result = pixelize_image(
                self.source_image,
                width=self.width_spin.value(),
                height=self.height_spin.value(),
                keep_aspect=self.aspect_check.isChecked(),
                palette=self.palette_combo.currentData(),
                max_colors=self.colors_spin.value(),
                dither=self.dither_check.isChecked())
        except PixelForgeError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self.result_image = result
        self._refresh_pixmaps()
        self.info_label.setText("%d x %d, %d colors"
                                % (result.width(), result.height(),
                                   count_opaque_colors(result)))
        self.export_btn.setEnabled(True)
        self.save_library_btn.setEnabled(True)

    def _refresh_pixmaps(self):
        # The original always fits its pane whole — zoom is for
        # inspecting pixels in the result, not for cropping the
        # original down to one corner of sky.
        source = self.source_image
        fit = ORIGINAL_FIT / max(source.width(), source.height())
        before = checkerboard_pixmap(source, fit, smooth=True)
        self.before_label.setPixmap(before)
        if self.result_image is not None:
            after = checkerboard_pixmap(self.result_image,
                                        self.zoom_slider.value(),
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
            warn_dialog(self, "Could not export PNG",
                                "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
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
        for key in ("image", "pico8", "gameboy", "grayscale"):
            self.video_palette_combo.addItem(PALETTE_LABELS[key], key)
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
            "Playback speed stored in the sheet metadata")
        self.sheet_fps_spin.valueChanged.connect(
            lambda _v: self._mark_video_preset_custom("sheet_fps"))
        sheet_fps_row.addWidget(self.sheet_fps_spin)
        form.addLayout(sheet_fps_row)

        self.sheet_preview_btn = QPushButton("Preview spritesheet")
        self.sheet_preview_btn.setToolTip(
            "Preview the spritesheet and play its animation")
        self.sheet_preview_btn.setEnabled(False)
        self.sheet_preview_btn.clicked.connect(
            self._on_preview_spritesheet)
        form.addWidget(self.sheet_preview_btn)

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
            "Playback speed of the preview")
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
        strip_layout.addLayout(strip_head)
        self.video_strip = QListWidget()
        self.video_strip.setToolTip(
            "Frames — click one to preview it, uncheck to drop it")
        self.video_strip.setIconSize(
            QSize(STRIP_THUMB, STRIP_THUMB))
        self.video_strip.setFlow(QListWidget.Flow.LeftToRight)
        self.video_strip.setWrapping(True)
        self.video_strip.setResizeMode(
            QListWidget.ResizeMode.Adjust)
        self.video_strip.setMovement(
            QListWidget.Movement.Static)
        self.video_strip.itemClicked.connect(
            self._on_strip_item_clicked)
        self.video_strip.itemChanged.connect(
            self._on_strip_item_changed)
        strip_layout.addWidget(self.video_strip, 1)
        right.addWidget(strip_pane)

        right.setSizes([380, 220])
        main_layout.addWidget(right, 1)
        return root

    # ---------------------------------------------------------- input

    def load_video(self, path):
        """Import a video: start a background extract + pixelize
        run and return True at once — the window stays responsive
        while the frames land in _on_video_run_done. Returns False
        (with a message box) when ffmpeg is missing; that check is
        cheap and stays synchronous so a missing ffmpeg still fails
        fast, exactly as before.
        """
        self._stop_playback()
        try:
            require_ffmpeg()
        except PixelForgeError as exc:
            self.video_path = None
            warn_dialog(self, "Could not import video", str(exc))
            return False
        self.video_path = str(path)
        # B7: the start/end range belongs to the OLD clip — a stale
        # range beyond the new clip's length used to yield "no frames
        # extracted" with no visible reason. Clear it so the new
        # import always starts from the whole clip (empty = full
        # range, the fresh-tab default). The other settings (fps,
        # size, palette, colors, dither, every-Nth, preset) are the
        # user's working choices and stay.
        self.video_start_edit.clear()
        self.video_end_edit.clear()
        self._reload_video_frames(fresh=True)
        return True

    def _on_video_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import video", "", video_filter())
        if path:
            self.load_video(path)

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
        }

    def _reload_video_frames(self, fresh=False):
        """Start a background re-extract with the current
        fps/every/range, then re-pixelize. Returns False when
        there is no video to re-extract; otherwise True — the
        frames land in _on_video_run_done, and a failure lands
        in _on_video_run_failed with a message box, the old
        frames still in place."""
        if self.video_path is None:
            return False
        self._stop_playback()
        settings = self._video_pixelize_settings()
        settings.update({
            "mode": "reload",
            "path": self.video_path,
            "fps": self.video_fps_combo.currentData(),
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
        elif len(self.video_keep) != len(result["frames"]):
            self.video_keep = [True] * len(result["frames"])
        self.video_frames = result["frames"]
        kept = self._kept_indices()
        self.video_current = kept[0] if kept else 0
        self._rebuild_strip()
        self._refresh_video_preview()
        if result["mode"] == "reload":
            # A new video: fit the frame preview to its pane
            # (task 22). A re-pixelize keeps the user's zoom.
            self._on_fit_video_zoom()
        self._update_video_info()
        self.video_export_btn.setEnabled(bool(kept))
        self.video_play_btn.setEnabled(bool(kept))
        self.sheet_preview_btn.setEnabled(bool(kept))
        self.sheet_export_btn.setEnabled(bool(kept))
        self.sheet_library_btn.setEnabled(bool(kept))
        self.video_keep_all_btn.setEnabled(bool(result["frames"]))
        self.video_drop_all_btn.setEnabled(bool(result["frames"]))
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
        warn_dialog(self, "Video import failed", message)
        if settings.get("fresh"):
            self.video_path = None

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
        debounced: the run starts 300 ms after the last change."""
        if self.video_path is not None:
            self._schedule_video_run(reload=True)

    def _on_video_preset(self):
        preset = self.video_size_combo.currentData()
        if preset is None:
            return
        self._updating_video_size = True
        try:
            self.video_width_spin.setValue(preset)
            self.video_height_spin.setValue(preset)
        finally:
            self._updating_video_size = False
        self._on_video_control_changed()

    def _on_video_custom_size(self):
        if self._updating_video_size:
            return
        index = self.video_size_combo.findData(None)
        if index >= 0 and self.video_size_combo.currentIndex() != index:
            self.video_size_combo.blockSignals(True)
            self.video_size_combo.setCurrentIndex(index)
            self.video_size_combo.blockSignals(False)
        self._on_video_control_changed()

    def _on_video_control_changed(self):
        self._mark_preset_custom("video")
        if self.video_raw_frames:
            self._schedule_video_run(reload=False)

    def _on_video_zoom_changed(self, value):
        self.video_zoom_label.setText("%dx" % value)
        if self.video_frames:
            self._refresh_video_preview()

    # ----------------------------------------------------- strip + preview

    def _rebuild_strip(self):
        self.video_strip.blockSignals(True)
        try:
            self.video_strip.clear()
            for index, frame in enumerate(self.video_frames):
                thumb = thumb_pixmap(frame, STRIP_THUMB)
                item = QListWidgetItem(thumb, "Frame %03d" % (index + 1))
                item.setData(Qt.ItemDataRole.UserRole, index)
                item.setFlags(item.flags()
                              | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if self.video_keep[index]
                    else Qt.CheckState.Unchecked)
                self.video_strip.addItem(item)
        finally:
            self.video_strip.blockSignals(False)

    def _kept_indices(self):
        return [i for i, keep in enumerate(self.video_keep) if keep]

    def _refresh_video_preview(self):
        if not self.video_frames:
            return
        frame = self.video_frames[self.video_current]
        pixmap = checkerboard_pixmap(frame, self.video_zoom_slider.value(),
                                     smooth=False)
        self.video_preview_label.setPixmap(pixmap)
        kept = len(self._kept_indices())
        self.video_counter_label.setText(
            "Frame %d of %d (%d kept)"
            % (self.video_current + 1, len(self.video_frames), kept))

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
        kept = self._kept_indices()
        self.video_export_btn.setEnabled(bool(kept))
        self.video_play_btn.setEnabled(bool(kept))
        self.sheet_preview_btn.setEnabled(bool(kept))
        self.sheet_export_btn.setEnabled(bool(kept))
        self.sheet_library_btn.setEnabled(bool(kept))
        if not kept:
            self._stop_playback()
        elif self.video_current not in kept:
            self.video_current = kept[0]
        self._refresh_video_preview()
        self._update_video_info()

    def _set_all_keep(self, keep):
        self.video_keep = [keep] * len(self.video_frames)
        self.video_strip.blockSignals(True)
        try:
            for row in range(self.video_strip.count()):
                item = self.video_strip.item(row)
                item.setCheckState(
                    Qt.CheckState.Checked if keep
                    else Qt.CheckState.Unchecked)
        finally:
            self.video_strip.blockSignals(False)
        kept = self._kept_indices()
        self.video_export_btn.setEnabled(bool(kept))
        self.video_play_btn.setEnabled(bool(kept))
        self.sheet_preview_btn.setEnabled(bool(kept))
        self.sheet_export_btn.setEnabled(bool(kept))
        self.sheet_library_btn.setEnabled(bool(kept))
        if not kept:
            self._stop_playback()
        else:
            self.video_current = kept[0]
        self._refresh_video_preview()
        self._update_video_info()

    def _on_keep_all(self):
        self._set_all_keep(True)

    def _on_drop_all(self):
        self._set_all_keep(False)

    # -------------------------------------------------------- playback

    def _on_play_pause(self):
        if self.video_timer.isActive():
            self._stop_playback()
            return
        if not self._kept_indices():
            return
        if self.video_current not in self._kept_indices():
            self.video_current = self._kept_indices()[0]
        interval = max(1, 1000 // self.video_play_fps_spin.value())
        self.video_timer.start(interval)
        self.video_play_btn.setText("Pause")

    def _stop_playback(self):
        if self.video_timer.isActive():
            self.video_timer.stop()
        self.video_play_btn.setText("Play")

    def _on_play_fps_changed(self):
        if self.video_timer.isActive():
            interval = max(1, 1000 // self.video_play_fps_spin.value())
            self.video_timer.start(interval)

    def _advance_video_frame(self):
        """One playback step: next kept frame, wrapping when Loop is
        on, stopping at the last kept frame when it is off."""
        kept = self._kept_indices()
        if not kept:
            self._stop_playback()
            return
        later = [i for i in kept if i > self.video_current]
        if later:
            self.video_current = later[0]
        elif self.video_loop_check.isChecked():
            self.video_current = kept[0]
        else:
            self.video_current = kept[-1]
            self._stop_playback()
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
            warn_dialog(self, "Could not export frames",
                                "could not create %s: %s" % (out, exc))
            return False
        for number, index in enumerate(kept, start=1):
            dest = out / ("frame_%03d.png" % number)
            if not self.video_frames[index].save(str(dest), "PNG"):
                warn_dialog(self, "Could not export frames",
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
        JSON takes the PNG's name with .json). Returns True on
        success; on failure shows a message box and returns False.
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
        try:
            metadata = write_sheet(images, names, str(path),
                                   str(meta_path), cols=cols,
                                   padding=padding, fps=fps)
        except PixelForgeError as exc:
            warn_dialog(self, "Could not export spritesheet", str(exc))
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
        try:
            dialog = SpritesheetDialog(
                self, images, names, cols, padding, fps,
                self._sheet_default_name())
        except PixelForgeError as exc:
            warn_dialog(self, "Could not preview spritesheet", str(exc))
            return
        dialog.exec()

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
        for key in ("image", "pico8", "gameboy", "grayscale"):
            self.prompt_palette_combo.addItem(PALETTE_LABELS[key], key)
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

    def _on_prompt_generate(self):
        if self._prompt_generating():
            return
        self._refresh_prompt_status()
        try:
            prompt = validate_prompt(self.prompt_edit.text())
            require_engine()
            require_model()
        except PixelForgeError as exc:
            warn_dialog(self, "Could not generate", str(exc))
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
            warn_dialog(self, "Could not generate", str(exc))
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
        else:
            self.prompt_info_label.setText("No candidates generated")
            detail = failed[0].get("error", "") if failed else ""
            warn_dialog(
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

    def _on_prompt_failed(self, message):
        # See _on_prompt_progress: only the current worker counts.
        if (self.sender() is not self.prompt_worker
                or self.prompt_batch_dir is None):
            return
        self._prompt_elapsed_timer.stop()
        self.prompt_progress.setFormat("%v of %m candidates")
        self.prompt_generate_btn.setEnabled(True)
        self.prompt_cancel_btn.setEnabled(False)
        warn_dialog(self, "Generation failed", message)
        self.statusBar().showMessage("Generation failed")
        self._update_prompt_iter_buttons()

    def _on_prompt_cancelled(self):
        # See _on_prompt_progress: only the current worker counts.
        if (self.sender() is not self.prompt_worker
                or self.prompt_batch_dir is None):
            return
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
                warn_dialog(self, "Could not read the batch record", str(exc))
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
            warn_dialog(self, "Could not read the batch", str(exc))
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
            warn_dialog(self, "Could not generate", str(exc))
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
            warn_dialog(
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
            warn_dialog(self, "Could not read the batch", str(exc))
            return
        landed = [c for c in batch["candidates"]
                  if c["status"] in ("pending", "kept")]
        if not landed:
            warn_dialog(
                self, "No usable candidates",
                "that batch has no usable candidates left")
            return
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
        self.statusBar().showMessage(
            "Reopened batch from History: %s" % batch["prompt"])

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
        # Past the limit the counter turns the warning orange —
        # the same color as the preset warning under the pickers.
        if length > MAX_PROMPT_LEN:
            self.prompt_counter_label.setStyleSheet(
                "color: #ffb066;")
        else:
            self.prompt_counter_label.setStyleSheet("")

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
                    image = load_image(path)
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
            set_keeper(self.prompt_batch_dir, filename)
            image = load_image(self.prompt_batch_dir / filename)
        except PixelForgeError as exc:
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
        preset = self.prompt_size_combo.currentData()
        if preset is None:
            return
        self._updating_prompt_size = True
        try:
            self.prompt_width_spin.setValue(preset)
            self.prompt_height_spin.setValue(preset)
        finally:
            self._updating_prompt_size = False
        self._on_prompt_control_changed()

    def _on_prompt_custom_size(self):
        if self._updating_prompt_size:
            return
        index = self.prompt_size_combo.findData(None)
        if index >= 0 and self.prompt_size_combo.currentIndex() != index:
            self.prompt_size_combo.blockSignals(True)
            self.prompt_size_combo.setCurrentIndex(index)
            self.prompt_size_combo.blockSignals(False)
        self._on_prompt_control_changed()

    def _on_prompt_control_changed(self):
        self._mark_preset_custom("prompt")
        if self.prompt_keeper_image is not None:
            self._update_prompt_preview()

    def _on_prompt_zoom_changed(self, value):
        self.prompt_zoom_label.setText("%dx" % value)
        if self.prompt_keeper_image is not None:
            self._refresh_prompt_pixmaps()

    # -------------------------------------------------------- preview

    def _update_prompt_preview(self):
        """Re-run the Item 1 pixelizer on the keeper with the
        current controls."""
        if self.prompt_keeper_image is None:
            return
        try:
            result = pixelize_image(
                self.prompt_keeper_image,
                width=self.prompt_width_spin.value(),
                height=self.prompt_height_spin.value(),
                keep_aspect=self.prompt_aspect_check.isChecked(),
                palette=self.prompt_palette_combo.currentData(),
                max_colors=self.prompt_colors_spin.value(),
                dither=self.prompt_dither_check.isChecked())
        except PixelForgeError as exc:
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
        before = checkerboard_pixmap(source, fit, smooth=True)
        self.prompt_before_label.setPixmap(before)
        if self.prompt_result_image is not None:
            after = checkerboard_pixmap(self.prompt_result_image,
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
            warn_dialog(self, "Could not export PNG",
                                "could not write: %s" % path)
            return False
        self.statusBar().showMessage("Exported %s" % Path(path).name)
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

    def load_code(self, paths):
        """Parse code files or folders and list the assets
        found. Returns True on success. On failure shows a message
        box and returns False — the window stays up either way."""
        if isinstance(paths, (str, Path)):
            paths = [paths]
        self._stop_texture_worker()
        try:
            result = parse_paths(paths)
        except PixelForgeError as exc:
            warn_dialog(self, "Could not import code", str(exc))
            return False
        if not result["assets"] and not result["not_rendered"]:
            warn_dialog(
                self, "No pixel-art assets found",
                "no pixel-art assets found in %s — the code "
                "defines no pixel maps or palettes Pixel Forge "
                "can read (see the README for what is detected)"
                % ", ".join(Path(p).name for p in paths))
            return False
        self.code_assets = result["assets"]
        self.code_not_rendered = result["not_rendered"]
        self.code_skipped = result["skipped"]
        self.code_source = ", ".join(str(p) for p in paths)
        self.code_source_first = str(paths[0]) if paths else None
        self.code_kind = "code"
        self.code_skin_path = None
        self.code_skin_layout = None
        self._set_parts_button(
            False, "Only Minecraft skins have parts to export")
        self._rebuild_code_list()
        message = ("%s from %s"
                   % (plural(len(result["assets"]), "asset"),
                      plural(len(result["files"]), "file")))
        if result["skipped"]:
            message += (", %s skipped"
                        % plural(len(result["skipped"]), "file"))
        self.statusBar().showMessage(message)
        return True

    def load_game_asset(self, path):
        """Load a game asset: a Minecraft skin splits into its
        parts (plus the combined figure) right away; anything else
        pixelizes as a texture on a worker thread (see
        TextureWorker), so a big photo never freezes the window.
        Returns True when the load started (or finished, for a
        skin); False when the file cannot be read at all."""
        try:
            image = load_image(path)
        except PixelForgeError as exc:
            warn_dialog(self, "Could not import game asset", str(exc))
            return False
        layout = detect_skin(image)
        self.code_source = str(path)
        self.code_source_first = str(path)
        self.code_not_rendered = []
        self.code_skipped = []
        self._stop_texture_worker()
        if layout is not None:
            try:
                parts = skin_parts(image)
                figure = skin_figure(image)
            except PixelForgeError as exc:
                warn_dialog(self, "Could not split the skin", str(exc))
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
            self._rebuild_code_list()
            self.statusBar().showMessage(
                "Minecraft skin (%s): %d parts"
                % (layout, len(parts)))
            return True
        # Texture: pixelize off the GUI thread. The worker hands
        # the finished texture to _on_texture_done.
        self.texture_worker = TextureWorker(image, path, parent=self)
        self.texture_worker.done.connect(self._on_texture_done)
        self.texture_worker.failed.connect(self._on_texture_failed)
        self.statusBar().showMessage("Loading texture…")
        self.texture_worker.start()
        return True

    def _on_texture_done(self, result):
        # See _on_prompt_progress: only the current worker counts
        # — a texture load superseded by a newer import is ignored.
        if self.sender() is not self.texture_worker:
            return
        self.texture_worker = None
        texture = result["image"]
        self.code_kind = "texture"
        self.code_skin_path = None
        self.code_skin_layout = None
        self._set_parts_button(
            False, "Only Minecraft skins have parts to export")
        src_w, src_h = (result["source_width"],
                        result["source_height"])
        capped = ((texture.width(), texture.height())
                  != (src_w, src_h))
        if capped:
            detail = ("downscaled from %d x %d to %d x %d — "
                      "pixelized as a texture"
                      % (src_w, src_h, texture.width(),
                         texture.height()))
        else:
            detail = ("not a Minecraft skin (%d x %d) — "
                      "pixelized as a texture" % (src_w, src_h))
        self.code_assets = [{
            "name": Path(result["path"]).stem, "kind": "texture",
            "source_file": result["path"],
            "width": texture.width(), "height": texture.height(),
            "colors": result["colors"],
            "image": texture,
            "detail": detail}]
        self._rebuild_code_list()
        if capped:
            self.statusBar().showMessage(
                "Not a skin — pixelized as a texture "
                "(downscaled from %d x %d)" % (src_w, src_h))
        else:
            self.statusBar().showMessage(
                "Not a skin — pixelized as a texture")

    def _on_texture_failed(self, message):
        # See _on_prompt_progress: only the current worker counts.
        if self.sender() is not self.texture_worker:
            return
        self.texture_worker = None
        warn_dialog(self, "Texture import failed", message)
        self.statusBar().showMessage("Texture load failed")

    def _stop_texture_worker(self):
        if self.texture_worker is not None:
            if self.texture_worker.isRunning():
                self.texture_worker.wait(10000)
            self.texture_worker = None

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
        self.code_save_library_btn.setEnabled(True)

    def _refresh_code_pixmap(self):
        if self.code_image is None:
            return
        pixmap = checkerboard_pixmap(
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
            warn_dialog(self, "Could not export PNG",
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
            warn_dialog(self, "Could not export skin parts", str(exc))
            return
        self._remember_export_dir(folder)
        self.statusBar().showMessage(
            "Exported %s to %s"
            % (plural(len(written), "skin part"), folder))

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
            warn_dialog(self, "Could not apply preset", str(exc))
            return
        if settings is None:
            return
        self._apply_settings_to_tab(tab, settings)
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
            warn_dialog(self, "Could not save preset", str(exc))
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
        try:
            self.preset_store.delete(name)
        except PixelForgeError as exc:
            warn_dialog(self, "Could not delete preset", str(exc))
            return
        self._refresh_preset_combos()
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
            warn_dialog(self, "Could not export preset", str(exc))
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
            warn_dialog(self, "Could not import preset", str(exc))
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
            warn_dialog(self, "Could not import preset", str(exc))
            return
        self._refresh_preset_combos()
        self._select_preset(tab, "user:%s" % name)
        self.statusBar().showMessage("Imported preset %s" % name)

    # ======================================================= library tab

    def _build_library_tab(self):
        root = QWidget()
        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # --------------------------------------- search, filter, grid
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.library_search_edit = QLineEdit()
        self.library_search_edit.setPlaceholderText(
            "Search name or tags")
        self.library_search_edit.setToolTip(
            "Search the library by name or tags")
        self.library_search_edit.textChanged.connect(
            self._refresh_library)
        left_layout.addWidget(self.library_search_edit)

        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("Tag"))
        self.library_tag_combo = QComboBox()
        self.library_tag_combo.setToolTip(
            "Show only sprites with this tag")
        self.library_tag_combo.addItem("All tags", None)
        self.library_tag_combo.currentIndexChanged.connect(
            self._refresh_library)
        tag_row.addWidget(self.library_tag_combo, 1)
        left_layout.addLayout(tag_row)

        self.library_list = QListWidget()
        self.library_list.setToolTip(
            "Sprites in the library — click one to preview it")
        self.library_list.setIconSize(QSize(64, 64))
        self.library_list.setFlow(QListWidget.Flow.LeftToRight)
        self.library_list.setWrapping(True)
        self.library_list.setResizeMode(
            QListWidget.ResizeMode.Adjust)
        self.library_list.setMovement(QListWidget.Movement.Static)
        self.library_list.itemClicked.connect(
            self._on_library_item_clicked)
        # The grid area is a stack: the list, or — when the
        # library is empty — a centered "No sprites yet" label,
        # so the tab is never a void (review B1).
        self.library_stack = QStackedWidget()
        self.library_stack.addWidget(self.library_list)
        self.library_empty_label = QLabel("No sprites yet")
        self.library_empty_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.library_empty_label.setWordWrap(True)
        self.library_stack.addWidget(self.library_empty_label)
        left_layout.addWidget(self.library_stack, 1)

        self.library_count_label = QLabel("")
        left_layout.addWidget(self.library_count_label)
        main_layout.addWidget(left, 1)

        # ------------------------------------------------------ preview
        # No fixed width here: the column lives in a scroll
        # area (see _scrollable_column), like the other tabs.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._section("Preview"))
        self.library_preview_label = QLabel(
            "Select a sprite to preview it")
        self.library_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.library_preview_label.setWordWrap(True)
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setWidget(self.library_preview_label)
        right_layout.addWidget(preview_scroll, 1)

        self.library_info_label = QLabel("")
        self.library_info_label.setWordWrap(True)
        right_layout.addWidget(self.library_info_label)

        self.library_load_btn = QPushButton("Load into Image tab")
        self.library_load_btn.setToolTip(
            "Load the selected sprite into the Image tab")
        self.library_load_btn.setEnabled(False)
        self.library_load_btn.clicked.connect(self._on_library_load)
        right_layout.addWidget(self.library_load_btn)

        self.library_rename_btn = QPushButton("Rename")
        self.library_rename_btn.setToolTip(
            "Rename the selected sprite")
        self.library_rename_btn.setEnabled(False)
        self.library_rename_btn.clicked.connect(
            self._on_library_rename)
        right_layout.addWidget(self.library_rename_btn)

        self.library_delete_btn = QPushButton("Delete")
        self.library_delete_btn.setToolTip(
            "Delete the selected sprite from the library")
        self.library_delete_btn.setEnabled(False)
        self.library_delete_btn.clicked.connect(
            self._on_library_delete)
        right_layout.addWidget(self.library_delete_btn)

        self.library_export_btn = QPushButton("Export a copy")
        self.library_export_btn.setToolTip(
            "Export a copy of the selected sprite")
        self.library_export_btn.setEnabled(False)
        self.library_export_btn.clicked.connect(
            self._on_library_export)
        right_layout.addWidget(self.library_export_btn)

        main_layout.addWidget(
            self._scrollable_column(right))
        return root

    def _on_tab_changed(self, index):
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
        }
        hint = hints.get(self.tabs.tabText(index))
        if hint is not None and not warned:
            self.statusBar().showMessage(hint)

    def _refresh_library(self):
        """Rebuild the Library tab grid from the index, honoring
        the search box and tag filter. The tag list rebuilds too,
        keeping the selection when the tag still exists, and the
        selected entry stays selected when it is still visible.
        Returns True while malformed entries exist — the warning
        shows once per window (at construction, usually), and the
        caller (a tab switch) must not overwrite it with a tab
        hint while it still applies."""
        if not hasattr(self, "library_list"):
            return False  # tab still being built
        try:
            entries = self.library.search(
                text=self.library_search_edit.text().strip() or None,
                tag=self.library_tag_combo.currentData())
            tags = self.library.all_tags()
        except PixelForgeError as exc:
            self.library_list.clear()
            self.library_count_label.setText(str(exc))
            # Not empty — unreadable. Keep the list page (blank)
            # so the "No sprites yet" label never claims an
            # unreadable library has nothing in it.
            self.library_stack.setCurrentWidget(self.library_list)
            self._clear_library_selection()
            return False
        selected = self._selected_library_id()
        skipped = self.library.skipped_entries()
        if skipped and not self._library_skip_warned:
            # Same pattern as a corrupt presets file: say so in
            # the status bar and carry on with the good entries.
            # The count label below keeps the note permanently.
            self._library_skip_warned = True
            self.statusBar().showMessage(
                "Library: skipped %s — left in library.json, "
                "not deleted"
                % ("1 malformed entry" if len(skipped) == 1 else
                   "%d malformed entries" % len(skipped)))
        combo = self.library_tag_combo
        current_tag = combo.currentData()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("All tags", None)
            for tag in tags:
                combo.addItem(tag, tag)
            if current_tag is not None:
                index = combo.findData(current_tag)
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)
        self.library_list.blockSignals(True)
        try:
            self.library_list.clear()
            for entry in entries:
                try:
                    image = load_image(self.library.path_for(entry))
                except PixelForgeError:
                    thumb = QPixmap(64, 64)
                    thumb.fill(Qt.GlobalColor.darkGray)
                else:
                    thumb = thumb_pixmap(image, 64)
                item = QListWidgetItem(thumb, entry["name"])
                item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                item.setToolTip(", ".join(entry.get("tags", []))
                                or entry["name"])
                self.library_list.addItem(item)
        finally:
            self.library_list.blockSignals(False)
        total = len(self.library.entries())
        if skipped:
            note = (" — %s skipped, left in library.json"
                    % ("1 malformed entry" if len(skipped) == 1
                       else "%d malformed entries" % len(skipped)))
        else:
            note = ""
        if len(entries) == total:
            if total:
                self.library_count_label.setText(
                    "%s in the library%s"
                    % (plural(total, "sprite"), note))
            elif skipped:
                self.library_count_label.setText(
                    "No usable sprites in the library%s" % note)
            else:
                self.library_count_label.setText(
                    "Library is empty — save a sprite from the "
                    "Image, Video, Prompt, or Code tab.")
        else:
            self.library_count_label.setText(
                "%d of %s%s"
                % (len(entries), plural(total, "sprite"), note))
        # The grid area shows a centered "No sprites yet" label
        # while the library is empty (review B1); once sprites
        # exist, the list shows instead.
        if total == 0:
            self.library_stack.setCurrentWidget(
                self.library_empty_label)
        else:
            self.library_stack.setCurrentWidget(self.library_list)
        if selected is not None:
            for row in range(self.library_list.count()):
                item = self.library_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == selected:
                    self.library_list.setCurrentItem(item)
                    self._show_library_entry(selected)
                    return bool(skipped)
        self._clear_library_selection()
        return bool(skipped)

    def _selected_library_id(self):
        item = self.library_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_library_entry(self):
        entry_id = self._selected_library_id()
        if entry_id is None:
            return None
        return self.library.find(entry_id)

    def _clear_library_selection(self):
        # Order matters: setting a pixmap — even a null one —
        # wipes the label's text, so clear the pixmap FIRST and
        # set the text after it (review B1).
        self.library_preview_label.setPixmap(QPixmap())
        self.library_preview_label.setText(
            "Select a sprite to preview it")
        self.library_info_label.setText("")
        self.library_load_btn.setEnabled(False)
        self.library_rename_btn.setEnabled(False)
        self.library_delete_btn.setEnabled(False)
        self.library_export_btn.setEnabled(False)

    def _show_library_entry(self, entry_id):
        entry = self.library.find(entry_id)
        if entry is None:
            self._clear_library_selection()
            return
        path = self.library.path_for(entry)
        try:
            image = load_image(path)
        except PixelForgeError:
            self.library_preview_label.setText(
                "File missing: %s" % entry["file"])
        else:
            scale = 230 / max(image.width(), image.height())
            self.library_preview_label.setPixmap(
                checkerboard_pixmap(image, scale, smooth=False))
        lines = [entry["name"]]
        if entry["kind"] == "sheet":
            lines.append("%d x %d spritesheet, %d frames"
                         % (entry["width"], entry["height"],
                            entry.get("frames", 0)))
        else:
            lines.append("%d x %d sprite" % (entry["width"],
                                             entry["height"]))
        if entry.get("palette"):
            # Display labels, not stored keys: "From image", not
            # "image" (task 22). An unknown key still shows as-is
            # rather than vanishing.
            lines.append("Palette: %s" % PALETTE_LABELS.get(
                entry["palette"], entry["palette"]))
        if entry.get("tags"):
            lines.append("Tags: %s" % ", ".join(entry["tags"]))
        lines.append("From: %s" % SOURCE_LABELS.get(
            entry["source"], entry["source"]))
        lines.append("Saved: %s" % entry["created"][:10])
        self.library_info_label.setText("\n".join(lines))
        self.library_load_btn.setEnabled(True)
        self.library_rename_btn.setEnabled(True)
        self.library_delete_btn.setEnabled(True)
        self.library_export_btn.setEnabled(True)

    def _on_library_item_clicked(self, item):
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        if entry_id is not None:
            self._show_library_entry(entry_id)

    def _on_library_load(self):
        entry = self._selected_library_entry()
        if entry is None:
            return
        path = self.library.path_for(entry)
        self.tabs.setCurrentIndex(0)
        if self.load_image(str(path)):
            self.statusBar().showMessage(
                "Loaded %s from the library" % entry["name"])

    def _on_library_rename(self):
        entry = self._selected_library_entry()
        if entry is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename sprite", "Name:", text=entry["name"])
        if not ok or not name.strip() \
                or name.strip() == entry["name"]:
            return
        try:
            self.library.rename(entry["id"], name.strip())
        except PixelForgeError as exc:
            warn_dialog(self, "Could not rename", str(exc))
            return
        self._refresh_library()
        self.statusBar().showMessage("Renamed to %s" % name.strip())

    def _on_library_delete(self):
        entry = self._selected_library_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self, "Delete sprite",
            "Delete %s from the library? The file is removed too."
            % entry["name"],
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.remove(entry["id"])
        except PixelForgeError as exc:
            warn_dialog(self, "Could not delete", str(exc))
            return
        self._refresh_library()
        self.statusBar().showMessage(
            "Deleted %s from the library" % entry["name"])

    def _on_library_export(self):
        entry = self._selected_library_entry()
        if entry is None:
            return
        src = self.library.path_for(entry)
        # The library folder is the store — exporting next to the
        # stored file would pollute it. The last-used export
        # folder instead (task 22).
        default = self._export_default(entry["file"])
        path, _ = QFileDialog.getSaveFileName(
            self, "Export a copy", default, "PNG image (*.png)")
        if not path:
            return
        dest = Path(path)
        if dest.suffix.lower() != ".png":
            dest = dest.with_suffix(".png")
        try:
            shutil.copyfile(src, dest)
            if entry["kind"] == "sheet" and entry.get("meta"):
                shutil.copyfile(self.library.directory / entry["meta"],
                                dest.with_suffix(".json"))
        except OSError as exc:
            warn_dialog(self, "Could not export",
                                "could not write: %s" % exc)
            return
        self._remember_export_dir(dest.parent)
        self.statusBar().showMessage("Exported %s" % dest.name)

    # ------------------------------------------------ save to library

    def save_image_result_to_library(self, name, tags):
        """Save the Image tab's current result. Returns the entry;
        raises PixelForgeError when there is nothing to save."""
        if self.result_image is None:
            raise PixelForgeError("nothing to save to the library")
        entry = self.library.add_image(
            self.result_image, name, tags, source="image",
            palette=self.palette_combo.currentData())
        self._refresh_library()
        return entry

    def save_prompt_keeper_to_library(self, name, tags):
        """Save the Prompt tab's pixelized keeper. Returns the
        entry; raises PixelForgeError when there is nothing."""
        if self.prompt_result_image is None:
            raise PixelForgeError("nothing to save to the library")
        entry = self.library.add_image(
            self.prompt_result_image, name, tags, source="prompt",
            palette=self.prompt_palette_combo.currentData())
        self._refresh_library()
        return entry

    def save_sheet_to_library(self, name, tags):
        """Pack the Video tab's kept frames into a sheet and save
        the PNG + JSON pair as one library entry. Returns the
        entry; raises PixelForgeError when nothing is kept."""
        kept = self._kept_indices()
        if not kept:
            raise PixelForgeError("nothing to save to the library")
        images, names = self._kept_sheet_frames()
        cols, padding, fps = self._sheet_settings()
        tmp = tempfile.mkdtemp(prefix="pixel-forge-sheet-")
        try:
            png_path = os.path.join(tmp, "sheet.png")
            meta_path = os.path.join(tmp, "sheet.json")
            write_sheet(images, names, png_path, meta_path,
                        cols=cols, padding=padding, fps=fps)
            entry = self.library.add_sheet(
                png_path, meta_path, name, tags, source="video",
                frames=len(kept))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._refresh_library()
        return entry

    def _save_to_library_via_dialog(self, default_name, saver):
        """Ask for a name + tags, then run saver(name, tags).
        Returns the entry, or None when cancelled or failed."""
        dialog = SaveToLibraryDialog(self, default_name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        try:
            entry = saver(dialog.name_text(), dialog.tags_list())
        except PixelForgeError as exc:
            warn_dialog(self, "Could not save to the library", str(exc))
            return None
        self.statusBar().showMessage(
            "Saved %s to the library" % entry["name"])
        return entry

    def _on_save_image_to_library(self):
        if self.result_image is None:
            return
        stem = ""
        if self.source_path:
            stem = Path(self.source_path).stem
        self._save_to_library_via_dialog(
            ("%s pixel" % stem).strip() or "Untitled sprite",
            self.save_image_result_to_library)

    def _on_save_prompt_keeper_to_library(self):
        if self.prompt_result_image is None:
            return
        stem = ""
        if self.prompt_batch_dir is not None:
            stem = self.prompt_batch_dir.name
        self._save_to_library_via_dialog(
            ("%s pixel" % stem).strip() or "Untitled sprite",
            self.save_prompt_keeper_to_library)

    def _on_save_sheet_to_library(self):
        if not self._kept_indices():
            return
        stem = ""
        if self.video_path:
            stem = Path(self.video_path).stem
        self._save_to_library_via_dialog(
            ("%s sheet" % stem).strip() or "Untitled sheet",
            self.save_sheet_to_library)

    def save_code_asset_to_library(self, name, tags):
        """Save the Code tab's selected asset. Returns the entry;
        raises PixelForgeError when nothing is selected."""
        if self.code_image is None:
            raise PixelForgeError("nothing to save to the library")
        source = "code" if self.code_kind == "code" else "skin"
        entry = self.library.add_image(
            self.code_image, name, tags, source=source)
        self._refresh_library()
        return entry

    def _on_save_code_to_library(self):
        if self.code_image is None:
            return
        asset = self.code_assets[self.code_current]
        self._save_to_library_via_dialog(
            asset["name"] or "Untitled sprite",
            self.save_code_asset_to_library)

    # --------------------------------------------------------- cleanup

    def closeEvent(self, event):
        self._stop_playback()
        self._stop_prompt_worker()
        self._stop_texture_worker()
        self._stop_video_worker()
        self._cleanup_video_raw()
        self._cleanup_prompt_batch()
        event.accept()


def main(argv=None):
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else argv)
    app.setStyleSheet(DARK_STYLESHEET)
    icon_path = Path(__file__).resolve().parent.parent / "icons" / \
        "pixel-forge-256.png"
    if icon_path.is_file():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    args = list(sys.argv[1:] if argv is None else argv[1:])
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


if __name__ == "__main__":
    sys.exit(main())
