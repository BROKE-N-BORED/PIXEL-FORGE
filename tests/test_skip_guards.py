"""Skip-guard tests (task 63) — the Qt-dependent test modules must
SKIP, not error, when PySide6 cannot be imported.

Each guarded module is imported in a fresh subprocess with PySide6
blocked (sys.modules["PySide6"] = None makes every PySide6 import
fail), so the check is hermetic: it does not depend on which
interpreter runs the suite. A module that imports cleanly without
Qt, or errors instead of skipping, fails here — so a guard that
goes missing, or a module that stops needing Qt, is caught.

If a module ever stops needing Qt, take it out of GUARDED_MODULES
(and take its guard out with it) instead of letting it import
cleanly past a dead guard.
"""
import os
import subprocess
import sys
import unittest

# Test modules that need Qt: only the GUI tests still do (task
# 64 decoupled the core, so test_cli, test_codeinput,
# test_imagegen, test_library, test_pixelize, test_presets,
# test_skins, test_spritesheet and test_video all run without
# PySide6 now). Each guarded module must raise
# unittest.SkipTest when PySide6 is unavailable.
GUARDED_MODULES = (
    "test_gui",
)

PROBE = r"""
import sys
sys.modules["PySide6"] = None  # every PySide6 import now fails
sys.path.insert(0, sys.argv[1])  # tree root (pixel_forge)
sys.path.insert(0, sys.argv[2])  # tests dir (helpers + test modules)

import unittest
try:
    __import__(sys.argv[3])
except unittest.SkipTest as exc:
    print("SKIP: %s" % exc)
    sys.exit(0)
except ImportError as exc:
    print("IMPORT ERROR: %s" % exc)
    sys.exit(1)
print("IMPORTED: no skip raised")
sys.exit(2)
"""


class SkipGuardTests(unittest.TestCase):
    def test_guarded_modules_skip_without_pyside6(self):
        root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
        tests_dir = os.path.abspath(os.path.dirname(__file__))
        for module in GUARDED_MODULES:
            with self.subTest(module=module):
                proc = subprocess.run(
                    [sys.executable, "-c", PROBE, root, tests_dir,
                     module],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(
                    proc.returncode, 0,
                    "%s: exit %d: %s%s" % (
                        module, proc.returncode,
                        proc.stdout.strip(),
                        proc.stderr.strip()[-300:]))
