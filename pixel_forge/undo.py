"""Undo/redo for the Pixel Forge window (task 30).

A small custom stack, not QUndoStack: a library-delete entry owns
files in the library's trash folder, and those files must be
deleted for real the moment the entry leaves the stack (cap
reached, redo tail cleared, window closed). QUndoStack gives no
reliable hook for that in PySide6 — the command object is deleted
by Qt, and a Python __del__ is not a cleanup contract. Here every
entry is dropped through one method that calls its discard().

The stack itself is stdlib-only and knows nothing about Qt: a
command is any object with text, undo(), redo(), and discard().
The window's concrete commands live in gui.py, where the state
they restore lives. Undo is per-window-session only — nothing
here is written to disk, and closing the window clears the stack
(which is what permanently deletes trashed files).
"""

UNDO_STACK_CAP = 50


class Command:
    """One undoable change. undo() restores the state from before
    the change, redo() re-applies the change, discard() frees
    anything the entry owns (trashed files) when it leaves the
    stack for good. text names the change for the Edit menu
    (\"Delete knight.png\", \"Keep all frames\") — the menu shows
    it as \"Undo <text>\" / \"Redo <text>\"."""

    def __init__(self, text):
        self.text = text

    def undo(self):
        raise NotImplementedError

    def redo(self):
        raise NotImplementedError

    def discard(self):
        """Free what this entry owns. Called once, when the entry
        leaves the stack without any chance of coming back — cap
        eviction, a cleared redo tail, or clear(). The default
        owns nothing."""

    def __repr__(self):
        return "%s(%r)" % (type(self).__name__, self.text)


class UndoStack:
    """A capped undo/redo stack. push() takes a command whose
    change has ALREADY happened (the window performs the change,
    then pushes) and clears the redo tail — a new change makes
    the undone future unreachable. The oldest entry drops off
    when the cap is exceeded, and its discard() runs."""

    def __init__(self, cap=UNDO_STACK_CAP):
        self._cap = max(1, int(cap))
        self._done = []    # commands that happened, oldest first
        self._undone = []  # commands undone, newest first

    def __len__(self):
        return len(self._done)

    @property
    def cap(self):
        return self._cap

    @property
    def can_undo(self):
        return bool(self._done)

    @property
    def can_redo(self):
        return bool(self._undone)

    @property
    def undo_text(self):
        """The text of the next undo, or None."""
        return self._done[-1].text if self._done else None

    @property
    def redo_text(self):
        """The text of the next redo, or None."""
        return self._undone[-1].text if self._undone else None

    def push(self, command):
        """Record a change that already happened. The redo tail
        is discarded first — the undone commands can never come
        back once a new change lands."""
        while self._undone:
            self._undone.pop().discard()
        self._done.append(command)
        while len(self._done) > self._cap:
            self._done.pop(0).discard()

    def undo(self):
        """Undo the newest change. Returns False when there is
        nothing to undo."""
        if not self._done:
            return False
        command = self._done.pop()
        command.undo()
        self._undone.append(command)
        return True

    def redo(self):
        """Re-apply the newest undone change. Returns False when
        there is nothing to redo."""
        if not self._undone:
            return False
        command = self._undone.pop()
        command.redo()
        self._done.append(command)
        return True

    def clear(self):
        """Drop every entry, discarding each one. The window
        calls this on close — it is what permanently deletes
        trashed files."""
        while self._done:
            self._done.pop().discard()
        while self._undone:
            self._undone.pop().discard()
