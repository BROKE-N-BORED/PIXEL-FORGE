"""Prompt history — past generations and past prompts.

Task 20 (prompt iteration): a generation used to live in a temp
dir and vanish the moment the next batch started. Batches now
live in <library>/batches/, and this file records them — prompt,
negative, seeds, keeper, timestamp, batch dir — so the Prompt
tab can list past batches and reopen one. Past prompt strings
are kept alongside, most recent first, so the prompt field can
offer them back.

The file is prompt-history.json in the library folder, format
"pixel-forge-prompt-history" v1. It loads fail-soft, the same
way the library handles a bad entry: a corrupt or wrong-shaped
file reads as empty and reports `corrupt=True`, so the GUI can
warn once in the status bar instead of breaking startup. The
file is never rewritten on load — a bad file stays on disk,
untouched, until the next successful generation records into a
fresh one.

Stdlib-only: the history loads on bare python, with no Qt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .pixelize import PixelForgeError

HISTORY_FILENAME = "prompt-history.json"
BATCHES_DIRNAME = "batches"
HISTORY_FORMAT = "pixel-forge-prompt-history"
HISTORY_VERSION = 1
MAX_BATCHES = 50
MAX_PROMPTS = 20


def history_path(directory) -> Path:
    """The history file for a library folder."""
    return Path(directory) / HISTORY_FILENAME


def batches_dir(directory) -> Path:
    """Where GUI prompt batches live — a stable home under the
    library folder, so history entries stay valid across
    sessions (batches used to live in a temp dir that the next
    generation deleted)."""
    return Path(directory) / BATCHES_DIRNAME


def empty_history() -> dict:
    return {"format": HISTORY_FORMAT, "version": HISTORY_VERSION,
            "batches": [], "prompts": []}


def _valid_batch(entry) -> bool:
    """A batch entry is usable when it names a prompt and a
    batch dir. Anything else is dropped on load, not an error
    — one bad entry must not lose the rest."""
    return (isinstance(entry, dict)
            and isinstance(entry.get("prompt"), str)
            and entry.get("prompt") != ""
            and isinstance(entry.get("dir"), str)
            and entry.get("dir") != "")


def load_history(directory) -> tuple:
    """(history, corrupt) for a library folder. Missing file
    reads as empty, not corrupt. A file that is not JSON, or
    not the right shape, reads as empty AND corrupt — the
    caller warns once and carries on with an empty history.
    Never raises, never writes."""
    path = history_path(directory)
    if not path.exists():
        return empty_history(), False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_history(), True
    if (not isinstance(data, dict)
            or data.get("format") != HISTORY_FORMAT
            or not isinstance(data.get("batches"), list)
            or not isinstance(data.get("prompts"), list)):
        return empty_history(), True
    batches = [e for e in data["batches"] if _valid_batch(e)]
    prompts = [p for p in data["prompts"]
               if isinstance(p, str) and p != ""]
    return {"format": HISTORY_FORMAT, "version": HISTORY_VERSION,
            "batches": batches[:MAX_BATCHES],
            "prompts": prompts[:MAX_PROMPTS]}, False


def save_history(directory, history) -> None:
    """Write the history atomically (temp file + fsync +
    os.replace), creating the library folder if needed. Raises
    PixelForgeError with a readable message on failure — the
    GUI catches it and shows the status bar, it never breaks
    a generation."""
    path = history_path(directory)
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(history, indent=2) + "\n"
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
            "could not write the prompt history %s: %s" % (path, exc))


def record_batch(history: dict, entry: dict) -> None:
    """Add a batch entry, most recent first, capped at
    MAX_BATCHES — the oldest entries drop off the list (their
    batch folders stay on disk; nothing is ever deleted)."""
    history["batches"].insert(0, dict(entry))
    del history["batches"][MAX_BATCHES:]


def record_prompt(history: dict, prompt: str) -> None:
    """Add a prompt string, most recent first, no duplicates,
    capped at MAX_PROMPTS. Blank prompts are not recorded."""
    text = (prompt or "").strip()
    if not text:
        return
    prompts = history["prompts"]
    if text in prompts:
        prompts.remove(text)
    prompts.insert(0, text)
    del prompts[MAX_PROMPTS:]
