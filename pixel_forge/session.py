"""Session — recent files and the last-loaded image, video, and code.

Task 29 (recent files + session restore): every launch used to start
empty. The session file records what was loaded — the last image,
the last video, the last code paths, and a combined recent-files
list — so the window can offer them back in the File menu and
restore them on startup.

The file is session.json in the library folder, format
"pixel-forge-session" v1. It loads fail-soft, the same way the
prompt history does: a corrupt or wrong-shaped file reads as empty
and reports `corrupt=True`, so the GUI can warn once in the status
bar instead of breaking startup. The file is never rewritten on
load — a bad file stays on disk, untouched, until the next
successful load records into a fresh one.

Stdlib-only: the session loads on bare python, with no Qt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .pixelize import PixelForgeError

SESSION_FILENAME = "session.json"
SESSION_FORMAT = "pixel-forge-session"
SESSION_VERSION = 1
MAX_RECENT = 10
SESSION_KINDS = ("image", "video", "code", "skin")


def session_path(directory) -> Path:
    """The session file for a library folder."""
    return Path(directory) / SESSION_FILENAME


def empty_session() -> dict:
    return {"format": SESSION_FORMAT, "version": SESSION_VERSION,
            "last_image": None, "last_video": None, "last_code": [],
            "recent": []}


def _valid_recent(entry) -> bool:
    """A recent entry is usable when it names a file path and one of
    the four kinds. Anything else is dropped on load, not an error
    — one bad entry must not lose the rest."""
    return (isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and entry.get("path") != ""
            and entry.get("kind") in SESSION_KINDS)


def load_session(directory) -> tuple:
    """(session, corrupt) for a library folder. Missing file reads
    as empty, not corrupt. A file that is not JSON, or not the right
    shape, reads as empty AND corrupt — the caller warns once and
    carries on with an empty session. Never raises, never writes."""
    path = session_path(directory)
    if not path.exists():
        return empty_session(), False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_session(), True
    if (not isinstance(data, dict)
            or data.get("format") != SESSION_FORMAT
            or not (data.get("last_image") is None
                    or isinstance(data.get("last_image"), str))
            or not (data.get("last_video") is None
                    or isinstance(data.get("last_video"), str))
            or not isinstance(data.get("last_code"), list)
            or not isinstance(data.get("recent"), list)):
        return empty_session(), True
    last_code = [p for p in data["last_code"]
                 if isinstance(p, str) and p != ""]
    recent = [dict(e) for e in data["recent"] if _valid_recent(e)]
    return {"format": SESSION_FORMAT, "version": SESSION_VERSION,
            "last_image": data["last_image"],
            "last_video": data["last_video"],
            "last_code": last_code,
            "recent": recent[:MAX_RECENT]}, False


def save_session(directory, session) -> None:
    """Write the session atomically (temp file + fsync +
    os.replace), creating the library folder if needed. Raises
    PixelForgeError with a readable message on failure — the GUI
    catches it and shows the status bar, it never breaks a load."""
    path = session_path(directory)
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(session, indent=2) + "\n"
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
            "could not write the session %s: %s" % (path, exc))


def record_recent(session: dict, path, kind: str) -> None:
    """Add a file to the recent list, most recent first, no
    duplicates (a re-loaded file moves to the top, under its new
    kind), capped at MAX_RECENT. Blank paths and unknown kinds
    are ignored."""
    text = str(path) if path is not None else ""
    if not text or kind not in SESSION_KINDS:
        return
    recent = session["recent"]
    recent[:] = [e for e in recent if e["path"] != text]
    recent.insert(0, {"path": text, "kind": kind})
    del recent[MAX_RECENT:]


def set_last_image(session: dict, path) -> None:
    """The image to restore on the next startup."""
    session["last_image"] = str(path)


def set_last_video(session: dict, path) -> None:
    """The video to restore on the next startup."""
    session["last_video"] = str(path)


def set_last_code(session: dict, paths) -> None:
    """The code paths to restore on the next startup — the list
    load_code was given, files or folders, in order."""
    session["last_code"] = [str(p) for p in paths]
