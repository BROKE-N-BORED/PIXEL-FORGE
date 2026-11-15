"""Autosave — crash recovery for in-progress work (task 55).

Two files live in the autosave folder, under the app's own data
dir (~/.local/share/pixel-forge/autosave/, never the library and
never the user's folders):

* ``running`` — the crash marker. Written when a window starts
  (after checking for a previous one) and removed on a clean
  exit. A marker left behind means the last run crashed or was
  killed. It names the pid that wrote it, so a second window in
  the SAME process does not mistake its sibling for a crash.
* ``recovery.json`` — the last autosaved state, format
  "pixel-forge-recovery" v1, written atomically (temp file +
  fsync + os.replace, the session's pattern).
* ``recovery-result.png`` — the Image tab's result pixels, but
  ONLY when the result carries edits no file can reproduce (see
  below). It is removed again when the edits go away.

The payload is references and settings, never pixel data —
except the edited result. What each tab saves:

* Image: the source path (or the procedural kind + seed + size,
  which regenerate the sprite exactly), the six pixelize
  settings + the picked preset, and whether the result is
  edited. An unedited result is reproducible from the source +
  settings, so no pixels are stored for it; an edited result
  writes recovery-result.png (the composited result — the
  layer structure is NOT recovered, only the pixels).
* Video: the video path, the pixelize settings + the video fps
  and sheet settings, the start/end range, every-Nth, and the
  keep/drop, order, durations, and tags. The frames themselves
  are re-extracted from the video on restore (raw frames live
  in a temp dir), and the keep/drop set is applied only when
  the re-extraction yields the same frame count. Frames from
  the sheet splitter have no video to re-extract from, so they
  are not recovered.
* Prompt: the batch dir (batches live in the library and
  survive a crash; the keeper is already persisted to
  batch.json by set_keeper), the prompt/negative text, the
  steps/cfg/size, and the pixelize settings + preset. The edit
  boxes may hold a newer prompt than the batch, so their text
  is saved too.
* Code: the import paths, the kind ("code", "skin", or
  "texture"), and the current asset index. Texture control
  tweaks re-derive from the asset and are not stored.
* Tileset: the mode, the blob shape, and the base tile paths.
  The set itself regenerates from the bases on restore.
* current_tab: the tab the user was on.

Recovery is offered once, on the first show after a crash, and
only when the state actually holds work. The recovery file is
kept until the user saves or explicitly discards it: restoring
never deletes it (a second crash during restore must not lose
it), and a clean exit discards it. A corrupt recovery file
reads as empty and corrupt — the window warns once in the
status bar and starts fresh, never bricking startup (the B2
lesson).

Autosave is always on — no toggle, no setting (U35: the
backend configures itself; crash recovery is not a
preference).

Stdlib-only: the module loads on bare python, with no Qt.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .pixelize import PixelForgeError

MARKER_FILENAME = "running"
RECOVERY_FILENAME = "recovery.json"
RESULT_FILENAME = "recovery-result.png"
RECOVERY_FORMAT = "pixel-forge-recovery"
RECOVERY_VERSION = 1
TAB_KEYS = ("image", "video", "prompt", "code", "tileset")


def autosave_home() -> Path:
    """The autosave folder: ~/.local/share/pixel-forge/autosave/."""
    return (Path.home() / ".local" / "share" / "pixel-forge"
            / "autosave")


def marker_path(directory) -> Path:
    return Path(directory) / MARKER_FILENAME


def recovery_path(directory) -> Path:
    return Path(directory) / RECOVERY_FILENAME


def recovery_image_path(directory) -> Path:
    return Path(directory) / RESULT_FILENAME


def empty_recovery() -> dict:
    return {"format": RECOVERY_FORMAT, "version": RECOVERY_VERSION,
            "saved_at": None, "current_tab": 0,
            "image": None, "video": None, "prompt": None,
            "code": None, "tileset": None}


def mark_running(directory, pid=None) -> None:
    """Write the crash marker for this run (atomically, so a
    crash mid-write never leaves a half-written marker). The
    pid lets a later window tell a crash apart from a sibling
    window in the same process."""
    path = marker_path(directory)
    tmp = path.with_suffix(".tmp")
    text = json.dumps({"pid": os.getpid() if pid is None else pid,
                       "started": datetime.now(timezone.utc)
                       .isoformat()}) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def mark_clean_exit(directory) -> None:
    """Remove the crash marker: this run ended cleanly."""
    try:
        marker_path(directory).unlink(missing_ok=True)
    except OSError:
        pass


def crashed(directory) -> bool:
    """True when a previous run left its marker behind. A marker
    from this process is a sibling window, not a crash — a
    crashed window takes its process down with it. An unreadable
    marker counts as a crash: this process always writes valid
    JSON, so a broken one is from a previous run."""
    path = marker_path(directory)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    return data.get("pid") != os.getpid()


def save_recovery(directory, state) -> None:
    """Write the recovery state atomically (temp file + fsync +
    os.replace), creating the folder if needed. Raises
    PixelForgeError with a readable message on failure — the
    GUI catches it and shows the status bar, it never breaks
    the edit the write rode along with."""
    path = recovery_path(directory)
    tmp = path.with_suffix(".json.tmp")
    try:
        text = json.dumps(state, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise PixelForgeError(
            "could not write the recovery file: %s" % exc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError(
            "could not write the recovery file %s: %s" % (path, exc))


def load_recovery(directory) -> tuple:
    """(state, corrupt) for the autosave folder. A missing file
    reads as (None, False) — nothing to recover, nothing wrong.
    A file that is not JSON, or not the right shape, reads as
    (None, True) — the caller warns once and starts fresh. Never
    raises, never writes."""
    path = recovery_path(directory)
    if not path.exists():
        return None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, True
    if (not isinstance(data, dict)
            or data.get("format") != RECOVERY_FORMAT
            or data.get("version") != RECOVERY_VERSION):
        return None, True
    for key in TAB_KEYS:
        value = data.get(key)
        if value is not None and not isinstance(value, dict):
            return None, True
    state = empty_recovery()
    state.update({key: data.get(key) for key in TAB_KEYS})
    state["saved_at"] = data.get("saved_at")
    state["current_tab"] = data.get("current_tab", 0)
    return state, False


def discard_recovery(directory) -> None:
    """Remove the recovery file and the recovery image, if they
    exist — after a clean exit, or when the user explicitly
    discards the recovered work. Never raises."""
    for path in (recovery_path(directory),
                 recovery_image_path(directory)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def has_recovery(directory) -> bool:
    """True when a recovery file is on disk."""
    return recovery_path(directory).exists()


def save_recovery_image(directory, image) -> None:
    """Write the edited result pixels (atomic, like the state).
    Raises PixelForgeError on failure."""
    from .pngio import write_png

    path = recovery_image_path(directory)
    tmp = path.with_suffix(".png.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_png(image, tmp)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError(
            "could not write the recovery image %s: %s"
            % (path, exc))


def load_recovery_image(directory):
    """The saved result pixels, or None when there is no recovery
    image or it cannot be read. Never raises."""
    from .pngio import read_png

    path = recovery_image_path(directory)
    if not path.exists():
        return None
    try:
        return read_png(path)
    except (OSError, ValueError):
        return None
