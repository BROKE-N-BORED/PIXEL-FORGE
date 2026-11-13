"""UndoStack tests — stdlib only, no Qt (task 30)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.undo import Command, UndoStack  # noqa: E402


class _RecordingCommand(Command):
    """A command that logs every call, so the stack's order and
    its discard contract can be asserted."""

    def __init__(self, text, log):
        super().__init__(text)
        self.log = log

    def undo(self):
        self.log.append("undo %s" % self.text)

    def redo(self):
        self.log.append("redo %s" % self.text)

    def discard(self):
        self.log.append("discard %s" % self.text)


class UndoStackTests(unittest.TestCase):
    def test_empty_stack_cannot_undo_or_redo(self):
        stack = UndoStack()
        self.assertFalse(stack.can_undo)
        self.assertFalse(stack.can_redo)
        self.assertIsNone(stack.undo_text)
        self.assertIsNone(stack.redo_text)
        self.assertFalse(stack.undo())
        self.assertFalse(stack.redo())
        self.assertEqual(len(stack), 0)

    def test_push_undo_redo_order(self):
        log = []
        stack = UndoStack()
        stack.push(_RecordingCommand("one", log))
        stack.push(_RecordingCommand("two", log))
        self.assertTrue(stack.can_undo)
        self.assertFalse(stack.can_redo)
        self.assertEqual(stack.undo_text, "two")
        self.assertEqual(len(stack), 2)
        self.assertTrue(stack.undo())
        self.assertEqual(log, ["undo two"])
        self.assertEqual(stack.undo_text, "one")
        self.assertEqual(stack.redo_text, "two")
        self.assertTrue(stack.undo())
        self.assertEqual(log, ["undo two", "undo one"])
        self.assertFalse(stack.can_undo)
        self.assertTrue(stack.can_redo)
        self.assertTrue(stack.redo())
        self.assertEqual(log, ["undo two", "undo one", "redo one"])
        self.assertTrue(stack.redo())
        self.assertEqual(
            log, ["undo two", "undo one", "redo one", "redo two"])
        self.assertFalse(stack.can_redo)

    def test_push_clears_redo_tail_and_discards_it(self):
        # A new change makes the undone future unreachable —
        # and the discarded entries get their discard() call
        # (that is what permanently deletes trashed files).
        log = []
        stack = UndoStack()
        stack.push(_RecordingCommand("one", log))
        stack.push(_RecordingCommand("two", log))
        stack.undo()
        self.assertTrue(stack.can_redo)
        stack.push(_RecordingCommand("three", log))
        self.assertFalse(stack.can_redo)
        self.assertEqual(log, ["undo two", "discard two"])
        self.assertEqual(stack.undo_text, "three")

    def test_cap_evicts_oldest_and_discards_it(self):
        log = []
        stack = UndoStack(cap=2)
        stack.push(_RecordingCommand("one", log))
        stack.push(_RecordingCommand("two", log))
        stack.push(_RecordingCommand("three", log))
        self.assertEqual(len(stack), 2)
        self.assertEqual(log, ["discard one"])
        self.assertEqual(stack.undo_text, "three")
        stack.undo()
        stack.undo()
        self.assertEqual(stack.undo_text, None)
        self.assertFalse(stack.can_undo)

    def test_clear_discards_everything(self):
        log = []
        stack = UndoStack()
        stack.push(_RecordingCommand("one", log))
        stack.push(_RecordingCommand("two", log))
        stack.undo()
        stack.clear()
        self.assertFalse(stack.can_undo)
        self.assertFalse(stack.can_redo)
        self.assertEqual(len(stack), 0)
        self.assertEqual(sorted(log),
                         ["discard one", "discard two", "undo two"])

    def test_default_cap_is_fifty(self):
        from pixel_forge.undo import UNDO_STACK_CAP
        self.assertEqual(UNDO_STACK_CAP, 50)
        self.assertEqual(UndoStack().cap, 50)


if __name__ == "__main__":
    unittest.main()
