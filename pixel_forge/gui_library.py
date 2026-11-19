"""Library tab: the saved sprites, tags, search..

One of the MainWindow mixins (task 58).
"""

from PySide6.QtCore import QEventLoop, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
from .gui_shared import (SaveToLibraryDialog, _CardLift, _LibraryDeleteCommand, gui_module, thumb_pixmap)
from .library import SOURCE_LABELS, unreadable_reason
from .palettes import PALETTE_LABELS
from pathlib import Path
from .pixelize import PixelForgeError, plural
from .spritesheet import extract_frame, load_metadata, write_sheet
import os
import shutil
import tempfile


class LibraryTabMixin:
    """Library tab: the saved sprites, tags, search.

    One of the MainWindow mixins (task 58): the methods
    moved here verbatim from gui.py. MainWindow
    inherits this class; self is the window.
    """


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

        # Kind + source filters (task 24) — they compose with
        # the search box and the tag filter.
        kind_row = QHBoxLayout()
        kind_row.addWidget(QLabel("Kind"))
        self.library_kind_combo = QComboBox()
        self.library_kind_combo.setToolTip(
            "Show only sprites, only sheets, or everything")
        self.library_kind_combo.addItem("All kinds", None)
        self.library_kind_combo.addItem("Sprites", "sprite")
        self.library_kind_combo.addItem("Sheets", "sheet")
        self.library_kind_combo.currentIndexChanged.connect(
            self._refresh_library)
        kind_row.addWidget(self.library_kind_combo, 1)
        kind_row.addWidget(QLabel("From"))
        self.library_source_combo = QComboBox()
        self.library_source_combo.setToolTip(
            "Show only sprites saved from one tab")
        self.library_source_combo.addItem("All sources", None)
        for key, label in SOURCE_LABELS.items():
            self.library_source_combo.addItem(label, key)
        self.library_source_combo.currentIndexChanged.connect(
            self._refresh_library)
        kind_row.addWidget(self.library_source_combo, 1)
        left_layout.addLayout(kind_row)

        # Collection filter + sort (task 24). The collection
        # list rebuilds with the tag list (see _refresh_library).
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Collection"))
        self.library_collection_combo = QComboBox()
        self.library_collection_combo.setToolTip(
            "Show only sprites in this collection")
        self.library_collection_combo.addItem("All collections",
                                              None)
        self.library_collection_combo.addItem("No collection", "")
        self.library_collection_combo.currentIndexChanged.connect(
            self._refresh_library)
        sort_row.addWidget(self.library_collection_combo, 1)
        sort_row.addWidget(QLabel("Sort"))
        self.library_sort_combo = QComboBox()
        self.library_sort_combo.setToolTip(
            "Order the grid: saved order, name, date, or size")
        self.library_sort_combo.addItem("Saved order", "saved")
        self.library_sort_combo.addItem("Name", "name")
        self.library_sort_combo.addItem("Newest", "newest")
        self.library_sort_combo.addItem("Oldest", "oldest")
        self.library_sort_combo.addItem("Largest", "largest")
        self.library_sort_combo.currentIndexChanged.connect(
            self._refresh_library)
        sort_row.addWidget(self.library_sort_combo, 1)
        left_layout.addLayout(sort_row)

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
        # Card hover lift (task 94): the grid's entries are
        # cards; the hovered one rises 4 px. Painting only:
        # clicks land exactly as before.
        self.library_lift = _CardLift(self.library_list)
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

        self.library_edit_tags_btn = QPushButton("Edit tags")
        self.library_edit_tags_btn.setToolTip(
            "Change the selected sprite's tags and collection")
        self.library_edit_tags_btn.setEnabled(False)
        self.library_edit_tags_btn.clicked.connect(
            self._on_library_edit_tags)
        right_layout.addWidget(self.library_edit_tags_btn)

        self.library_repair_btn = QPushButton("Repair missing")
        self.library_repair_btn.setToolTip(
            "Remove index entries whose files are gone")
        self.library_repair_btn.clicked.connect(
            self._on_library_repair)
        right_layout.addWidget(self.library_repair_btn)

        self.library_duplicates_btn = QPushButton("Find duplicates")
        self.library_duplicates_btn.setToolTip(
            "Find sprites with identical files and remove extras")
        self.library_duplicates_btn.clicked.connect(
            self._on_library_find_duplicates)
        right_layout.addWidget(self.library_duplicates_btn)

        self.library_export_all_btn = QPushButton("Export library")
        self.library_export_all_btn.setToolTip(
            "Copy the whole library to a folder as a backup")
        self.library_export_all_btn.clicked.connect(
            self._on_library_export_all)
        right_layout.addWidget(self.library_export_all_btn)

        self.library_import_btn = QPushButton("Import library")
        self.library_import_btn.setToolTip(
            "Add sprites from a library backup folder")
        self.library_import_btn.clicked.connect(
            self._on_library_import)
        right_layout.addWidget(self.library_import_btn)

        main_layout.addWidget(
            self._scrollable_column(right))
        return root


    def _refresh_library(self):
        """Rebuild the Library tab grid from the index, honoring
        the search box and the tag, kind, source, and collection
        filters, in the chosen sort order. The tag and collection
        lists rebuild too, keeping the selection when it still
        exists, and the selected entry stays selected when it is
        still visible. Returns True while malformed entries exist
        — the warning shows once per window (at construction,
        usually), and the caller (a tab switch) must not
        overwrite it with a tab hint while it still applies."""
        if not hasattr(self, "library_list"):
            return False  # tab still being built
        try:
            entries = self.library.search(
                text=self.library_search_edit.text().strip() or None,
                tag=self.library_tag_combo.currentData(),
                kind=self.library_kind_combo.currentData(),
                source=self.library_source_combo.currentData(),
                collection=self.library_collection_combo
                .currentData())
            tags = self.library.all_tags()
            collections = self.library.all_collections()
        except PixelForgeError as exc:
            self.library_list.clear()
            self.library_count_label.setText(str(exc))
            # Not empty — unreadable. Keep the list page (blank)
            # so the "No sprites yet" label never claims an
            # unreadable library has nothing in it.
            self.library_stack.setCurrentWidget(self.library_list)
            self._clear_library_selection()
            return False
        sort = self.library_sort_combo.currentData()
        if sort == "name":
            entries.sort(key=lambda e: e["name"].lower())
        elif sort == "newest":
            entries.sort(key=lambda e: e["created"], reverse=True)
        elif sort == "oldest":
            entries.sort(key=lambda e: e["created"])
        elif sort == "largest":
            entries.sort(key=lambda e: e["width"] * e["height"],
                         reverse=True)
        # "saved" keeps the index order — no sort at all.
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
        self._rebuild_filter_combo(
            self.library_tag_combo, tags, "All tags")
        self._rebuild_filter_combo(
            self.library_collection_combo, collections,
            "All collections", extra=("No collection", ""))
        # Thumbnails for entries that no longer exist drop out
        # of the cache; filtered-out entries keep theirs.
        live_ids = {entry["id"] for entry in
                    self.library.entries()}
        for key in list(self._thumb_cache):
            if key[0] not in live_ids:
                del self._thumb_cache[key]
        missing_ids = {entry["id"] for entry in
                       self.library.orphans()}
        self.library_list.blockSignals(True)
        try:
            self.library_list.clear()
            for entry in entries:
                thumb = self._library_thumb(entry)
                label = entry["name"]
                tooltip = ", ".join(entry.get("tags", [])) \
                    or entry["name"]
                if entry["kind"] == "sheet":
                    label += " — Sheet"
                if entry["id"] in missing_ids:
                    label += " — Missing"
                else:
                    # Task 68: a file that is there but cannot
                    # be read used to show as a bare gray square.
                    # It now says so, with the reason in the
                    # tooltip. "(broken)" uses parens, not an
                    # em-dash, so the new marker adds no U33
                    # violation.
                    reason = self._library_thumb_broken(entry)
                    if reason is not None:
                        label += " (broken)"
                        tooltip = ("Can't read this image: %s"
                                   % reason)
                item = QListWidgetItem(thumb, label)
                item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                item.setToolTip(tooltip)
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


    def _rebuild_filter_combo(self, combo, values, all_label,
                              extra=None):
        """Rebuild a Library filter combo (tags, collections)
        from the current values, keeping the selection when it
        still exists. `extra` is one extra (label, data) pair
        added after the "All" item, e.g. "No collection"."""
        current = combo.currentData()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(all_label, None)
            if extra is not None:
                combo.addItem(extra[0], extra[1])
            for value in values:
                combo.addItem(value, value)
            if current is not None:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)


    def _library_thumb_key(self, entry):
        """Cache key for an entry's thumbnail: the entry id +
        the file's size and mtime; a missing file keys on
        (id, None, None) (task 24). A repaired or replaced file
        has a new size or mtime, so it rebuilds, and a broken
        marker clears itself (task 68)."""
        path = self.library.path_for(entry)
        try:
            stat = path.stat()
            return (entry["id"], stat.st_size, stat.st_mtime_ns)
        except OSError:
            return (entry["id"], None, None)


    def _library_thumb(self, entry):
        """The grid thumbnail for an entry, from the cache when
        the file is unchanged (task 24). The cache holds the
        pixmap + the reason it is the gray square (None when the
        image loaded), so one load serves both the thumbnail and
        the broken marker (task 68), and an unreadable file does
        not re-attempt a load on every refresh."""
        path = self.library.path_for(entry)
        key = self._library_thumb_key(entry)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached[0]
        self._thumb_builds += 1
        try:
            image = gui_module.load_image(path)
        except PixelForgeError as exc:
            thumb = QPixmap(64, 64)
            thumb.fill(Qt.GlobalColor.darkGray)
            reason = unreadable_reason(path, exc)
        else:
            if image.isNull():
                # Task 77: a 0-size image loads fine but has no
                # pixels to thumb — it reads as broken, the same
                # as in the preview, instead of a blank thumbnail
                # with no marker.
                thumb = QPixmap(64, 64)
                thumb.fill(Qt.GlobalColor.darkGray)
                reason = "the image has no size"
            else:
                thumb = thumb_pixmap(image, 64)
                reason = None
        self._thumb_cache[key] = (thumb, reason)
        return thumb


    def _library_thumb_broken(self, entry):
        """Why an entry's thumbnail is the gray square, or None
        when the image loads (task 68). Builds the thumbnail
        first, so the reason comes from the same single load."""
        self._library_thumb(entry)
        cached = self._thumb_cache.get(
            self._library_thumb_key(entry))
        return cached[1] if cached is not None else None


    def _duplicate_groups(self):
        """Duplicate groups for the library, cached against a
        fingerprint of entry ids + file sizes/mtimes (task 24).
        Hashing every file is the expensive part; the fingerprint
        is stat-only, so a refresh that changed nothing reuses
        the last result."""
        fingerprint = []
        for entry in self.library.entries():
            path = self.library.path_for(entry)
            try:
                stat = path.stat()
                fingerprint.append((entry["id"], stat.st_size,
                                    stat.st_mtime_ns))
            except OSError:
                fingerprint.append((entry["id"], None, None))
        fingerprint = tuple(fingerprint)
        if fingerprint != self._duplicate_fingerprint:
            self._duplicate_fingerprint = fingerprint
            self._duplicate_groups_cache = self.library.duplicates()
        return self._duplicate_groups_cache


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
        self.library_edit_tags_btn.setEnabled(False)


    def _show_library_entry(self, entry_id):
        entry = self.library.find(entry_id)
        if entry is None:
            self._clear_library_selection()
            return
        path = self.library.path_for(entry)
        if not path.is_file():
            self.library_preview_label.setText(
                "File missing: %s" % entry["file"])
        else:
            try:
                image = gui_module.load_image(path)
            except PixelForgeError as exc:
                # Task 68: a present-but-unreadable file used
                # to claim "File missing" here, a lie about a
                # file that is right there. It now says it
                # cannot be read, with the reason where known.
                self.library_preview_label.setText(
                    "Can't read this image\n%s"
                    % unreadable_reason(path, exc))
            else:
                # Task 77: a 0-size image would divide by zero
                # below (same class as task 15's B11). load_image
                # rejects 0-size images from disk, so this is
                # latent — but one that ever gets here reads as a
                # broken entry, not a crash.
                if image.isNull():
                    self.library_preview_label.setText(
                        "Can't read this image\nthe image has no size")
                else:
                    scale = 230 / max(image.width(), image.height())
                    self.library_preview_label.setPixmap(
                        gui_module.checkerboard_pixmap(image, scale, smooth=False))
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
        if entry.get("collection"):
            lines.append("Collection: %s" % entry["collection"])
        for group in self._duplicate_groups():
            ids = [other["id"] for other in group]
            if entry["id"] in ids:
                others = [other["name"] for other in group
                          if other["id"] != entry["id"]]
                lines.append("Duplicate of: %s" % ", ".join(others))
                break
        lines.append("From: %s" % SOURCE_LABELS.get(
            entry["source"], entry["source"]))
        lines.append("Saved: %s" % entry["created"][:10])
        self.library_info_label.setText("\n".join(lines))
        self.library_load_btn.setEnabled(True)
        self.library_rename_btn.setEnabled(True)
        self.library_delete_btn.setEnabled(True)
        self.library_export_btn.setEnabled(True)
        self.library_edit_tags_btn.setEnabled(True)


    def _on_library_item_clicked(self, item):
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        if entry_id is not None:
            self._show_library_entry(entry_id)


    def _on_library_load(self):
        entry = self._selected_library_entry()
        if entry is None:
            return
        path = self.library.path_for(entry)
        if entry["kind"] == "sheet":
            self._load_sheet_entry(entry, path)
            return
        self.tabs.setCurrentIndex(0)
        if self.load_image(str(path)):
            self.statusBar().showMessage(
                "Loaded %s from the library" % entry["name"])


    def _load_sheet_entry(self, entry, path):
        """Load a library sheet into the Image tab: ask which
        frame to load, or load the packed sheet (task 24). A
        sheet with no metadata, or unreadable metadata, loads
        the packed sheet — there are no frames to pick from."""
        meta = entry.get("meta")
        metadata = None
        if meta:
            try:
                metadata = load_metadata(
                    self.library.directory / meta)
            except PixelForgeError as exc:
                gui_module.warn_dialog(self, "Could not read the sheet",
                            str(exc))
                return
        if metadata is None:
            self.tabs.setCurrentIndex(0)
            if self.load_image(str(path)):
                self.statusBar().showMessage(
                    "Loaded %s from the library" % entry["name"])
            return
        dialog = gui_module.SheetFrameDialog(self, entry["name"], metadata)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        index = dialog.frame_index()
        self.tabs.setCurrentIndex(0)
        if index is None:
            if self.load_image(str(path)):
                self.statusBar().showMessage(
                    "Loaded %s from the library" % entry["name"])
            return
        try:
            sheet = gui_module.load_image(path)
            frame = extract_frame(sheet, metadata, index)
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not load the frame",
                        str(exc))
            return
        # A frame load replaces the result like any new load —
        # ask first when the stack carries edits (task 35).
        # This path set source_image directly, bypassing the
        # load_image guard; the ask happens after the frame
        # loads, so a failed load never asks about a load that
        # is not happening.
        if self._result_edited and not self._confirm_discard_edits(
                "Load a new image?",
                "Load a new image and lose your edits?"):
            return
        self.source_image = frame
        self.source_path = str(path)
        self.update_preview()
        self._on_fit_image_zoom()
        self.statusBar().showMessage(
            "Loaded %s from %s"
            % (metadata["frames"][index]["name"], entry["name"]))
        self._record_session(path, "image")


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
            gui_module.warn_dialog(self, "Could not rename", str(exc))
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
        # Undoable (task 30): the files move to the trash folder
        # instead of being unlinked, and the index entry is
        # dropped only — the delete becomes permanent when the
        # undo entry leaves the stack (or the window closes).
        # The confirm stays: undo is a safety net, not a
        # replacement for asking.
        entries = self.library.entries()
        index = next((i for i, e in enumerate(entries)
                      if e["id"] == entry["id"]), len(entries))
        trash_dir = self._trash_dir_for(entry["id"])
        try:
            command = _LibraryDeleteCommand(
                self, entry, index, trash_dir)
            command._delete()
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not delete", str(exc))
            return
        self._push_undo(command)
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
                # Task 75: the meta's "sheet" field names the
                # LIBRARY png — copying it verbatim left the
                # exported pair stale (the same defect class as
                # task 62, different code path). _copy_sheet_meta
                # rewrites "sheet" to the exported PNG's name
                # (adding the key when it is missing), writes
                # atomically, and never touches the library's own
                # meta file; a meta that is not a JSON object is
                # copied verbatim, as before.
                self.library._copy_sheet_meta(
                    self.library.directory / entry["meta"],
                    dest.with_suffix(".json"), dest.name)
        except OSError as exc:
            gui_module.warn_dialog(self, "Could not export",
                                "could not write: %s" % exc)
            return
        self._remember_export_dir(dest.parent)
        self.statusBar().showMessage("Exported %s" % dest.name)


    def _on_library_edit_tags(self):
        entry = self._selected_library_entry()
        if entry is None:
            return
        dialog = gui_module.EditTagsDialog(self, entry)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library.update(
                entry["id"], tags=dialog.tags_list(),
                collection=dialog.collection_text())
        except PixelForgeError as exc:
            gui_module.warn_dialog(self, "Could not edit tags", str(exc))
            return
        self._refresh_library()
        self.statusBar().showMessage(
            "Updated %s" % entry["name"])


    def _on_library_repair(self):
        """Remove index entries whose files are gone (task 24).
        Nothing on disk is deleted — the files are already gone,
        and remove_index_only leaves any leftover files alone."""
        orphans = self.library.orphans()
        if not orphans:
            self.statusBar().showMessage(
                "No missing files in the library")
            return
        names = "\n".join("- %s" % entry["name"]
                          for entry in orphans)
        answer = QMessageBox.question(
            self, "Repair the library",
            "Remove %s from the library? The files are already "
            "gone — only the index entries are removed.\n\n%s"
            % (plural(len(orphans), "stale entry"), names),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for entry in orphans:
            self.library.remove_index_only(entry["id"])
        self._refresh_library()
        self.statusBar().showMessage(
            "Removed %s from the library"
            % plural(len(orphans), "stale entry"))


    def _on_library_find_duplicates(self):
        """List duplicate groups (identical files) and offer to
        remove the extras, keeping the first of each group."""
        # Hashing every file can take seconds on a big library
        # — busy text while it runs (task 90). A paint pass
        # first, or the text would never show: the GUI thread
        # is inside the hash loop. No Cancel: the loop is
        # synchronous and cannot be interrupted.
        self._activity_start("library", "Finding duplicates…")
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            groups = self.library.duplicates()
        finally:
            self._activity_finish("library")
        if not groups:
            self.statusBar().showMessage(
                "No duplicates in the library")
            return
        extras = sum(len(group) - 1 for group in groups)
        lines = []
        for group in groups:
            lines.append("- %s (also saved as %s)"
                         % (group[0]["name"], ", ".join(
                             entry["name"]
                             for entry in group[1:])))
        answer = QMessageBox.question(
            self, "Remove duplicates",
            "Remove %s, keeping the first of each group?\n\n%s"
            % (plural(extras, "duplicate"),
               "\n".join(lines)),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for group in groups:
            for entry in group[1:]:
                self.library.remove(entry["id"])
        self._refresh_library()
        self.statusBar().showMessage(
            "Removed %s from the library" % plural(extras,
                                                   "duplicate"))


    def _on_library_export_all(self):
        dest = QFileDialog.getExistingDirectory(
            self, "Export the library", "")
        if not dest:
            return
        # Copying every sprite can take a while — busy text
        # while it runs (task 90). No Cancel: the copy is
        # synchronous, and stopping would leave a half-copied
        # library behind.
        self._activity_start("library", "Exporting the library…")
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            count = self.library.export_to(dest)
        except PixelForgeError as exc:
            self._activity_finish("library")
            gui_module.warn_dialog(self, "Could not export the library",
                        str(exc))
            return
        self._activity_finish("library")
        self.statusBar().showMessage(
            "Exported %s to %s" % (plural(count, "sprite"), dest))


    def _on_library_import(self):
        src = QFileDialog.getExistingDirectory(
            self, "Import a library", "")
        if not src:
            return
        # Copying a whole library in can take a while — busy
        # text while it runs (task 90). No Cancel: the import
        # is synchronous, and stopping would leave a
        # half-imported library behind.
        self._activity_start("library", "Importing a library…")
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            report = self.library.import_from(src)
        except PixelForgeError as exc:
            self._activity_finish("library")
            gui_module.warn_dialog(self, "Could not import the library",
                        str(exc))
            return
        self._activity_finish("library")
        self._refresh_library()
        parts = ["added %s" % plural(len(report["added"]),
                                     "sprite")]
        if report["skipped"]:
            parts.append("skipped %s already here"
                         % plural(len(report["skipped"]),
                                  "sprite"))
        if report["missing"]:
            parts.append("%s had no file"
                         % plural(len(report["missing"]),
                                  "sprite"))
        self.statusBar().showMessage(
            "Imported from %s — %s"
            % (Path(src).name, ", ".join(parts)))


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
                        cols=cols, padding=padding, fps=fps,
                        durations=self._kept_durations(),
                        tags=self.video_tags)
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
            gui_module.warn_dialog(self, "Could not save to the library", str(exc))
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
        # Task 79: the source follows how the asset is actually
        # being treated — a texture saved as "skin" was a lie in
        # the library. code_kind is one of the three whenever an
        # asset is loaded; "code" is the fallback, never a
        # silent "skin".
        source = self.code_kind if self.code_kind in (
            "code", "skin", "texture") else "code"
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
