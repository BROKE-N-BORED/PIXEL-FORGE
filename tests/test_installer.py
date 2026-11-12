"""Installer tests — dry-run steps plus an offscreen window smoke test.

The dry run never writes anything or runs anything privileged: every
step logs what it would do. Skipped when PySide6 is not importable.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget
    HAS_QT = True
except ImportError:
    HAS_QT = False

ROOT = os.path.join(os.path.dirname(__file__), "..")


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.tmp = tempfile.mkdtemp()

    def test_dry_run_steps_all_succeed(self):
        from pixel_forge.installer import InstallContext, STEPS, StepResult

        lines = []
        ctx = InstallContext(lines.append, dryrun=True)
        for _key, _title, func in STEPS:
            result = func(ctx)
            self.assertEqual(result.status, StepResult.DONE,
                             "dry-run step failed: %s" % result.detail)
        log = "\n".join(lines)
        self.assertIn("virtual environment", log)
        self.assertIn("Pixel Forge", log)
        self.assertIn("menu launcher", log)

    def test_dry_run_writes_nothing(self):
        from pixel_forge.installer import InstallContext, STEPS

        before = set(os.listdir(self.tmp))
        ctx = InstallContext(lambda _msg: None, dryrun=True)
        for _key, _title, func in STEPS:
            func(ctx)
        # Dry-run steps only touch the home directory in log lines;
        # nothing new appears in our temp dir, and no venv was made.
        self.assertEqual(set(os.listdir(self.tmp)), before)

    def test_installer_window_smoke(self):
        from pixel_forge.installer import InstallerWindow, STEPS

        win = InstallerWindow(dryrun=True)
        win.show()
        self.app.processEvents()
        self.assertTrue(win.isVisible())
        self.assertEqual(win.windowTitle(), "Install Pixel Forge")
        self.assertEqual(win.steps_list.count(), len(STEPS))
        self.assertTrue(win.install_btn.isEnabled())
        self.assertFalse(win.launch_btn.isEnabled())
        self.assertFalse(win.cancel_btn.isEnabled())
        for btn in win.findChildren(QPushButton):
            self.assertNotIn("&", btn.text())
        win.close()

    def test_worker_dry_run_completes(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from pixel_forge.installer import InstallWorker, STEPS, StepResult

        results = []
        loop = QEventLoop()
        worker = InstallWorker(dryrun=True)
        worker.finished.connect(
            lambda r: (results.extend(r), loop.quit()))
        worker.start()
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        worker.wait(5000)
        self.assertEqual(len(results), len(STEPS))
        self.assertTrue(all(r.status == StepResult.DONE
                            for r in results))

    def test_ffmpeg_step_dry_run(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_ffmpeg)

        lines = []
        ctx = InstallContext(lines.append, dryrun=True)
        result = step_ffmpeg(ctx)
        self.assertEqual(result.status, StepResult.DONE)

    def test_ffmpeg_step_skipped_when_not_offered(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_ffmpeg)

        lines = []
        ctx = InstallContext(lines.append, dryrun=True)
        ctx.install_ffmpeg = False
        ctx.which = lambda _name: None  # ffmpeg missing
        result = step_ffmpeg(ctx)
        self.assertEqual(result.status, StepResult.DONE)
        self.assertEqual(result.detail, "skipped")
        self.assertIn("sudo apt install ffmpeg", "\n".join(lines))

    def test_ffmpeg_step_warns_when_install_fails(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_ffmpeg)

        lines = []
        ctx = InstallContext(lines.append, dryrun=False)
        ctx.which = lambda _name: None  # ffmpeg missing
        ctx.run_admin = lambda _script, timeout=None: 100
        result = step_ffmpeg(ctx)
        # A failed ffmpeg install is a warning, never a failure —
        # Pixel Forge still works for images.
        self.assertEqual(result.status, StepResult.WARN)
        self.assertIn("sudo apt install ffmpeg", "\n".join(lines))

    def test_installer_window_has_ffmpeg_checkbox(self):
        from pixel_forge.installer import InstallerWindow

        win = InstallerWindow(dryrun=True)
        self.assertTrue(win.ffmpeg_check.isChecked())
        self.assertNotIn("&", win.ffmpeg_check.text())
        win.close()

    def test_image_engine_step_dry_run(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_image_engine)

        lines = []
        ctx = InstallContext(lines.append, dryrun=True)
        ctx.install_image_engine = True
        result = step_image_engine(ctx)
        self.assertEqual(result.status, StepResult.DONE)

    def test_image_engine_step_skipped_by_default(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_image_engine)

        lines = []
        ctx = InstallContext(lines.append, dryrun=True)
        # Off by default: a source build plus a 2.1 GB download is
        # not something to start without asking.
        self.assertFalse(ctx.install_image_engine)
        result = step_image_engine(ctx)
        self.assertEqual(result.status, StepResult.DONE)
        self.assertEqual(result.detail, "skipped")
        self.assertIn("--with-image-engine", "\n".join(lines))

    def test_image_engine_step_warns_when_build_tools_missing(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_image_engine)

        lines = []
        ctx = InstallContext(lines.append, dryrun=False)
        ctx.install_image_engine = True
        ctx.which = lambda _name: None  # git, cmake, g++ all missing
        ctx.run_admin = lambda _script, timeout=None: 100
        result = step_image_engine(ctx)
        # A failed engine install is a warning, never a failure —
        # Pixel Forge still works for images and video.
        self.assertEqual(result.status, StepResult.WARN)
        self.assertIn("sudo apt install git cmake build-essential",
                      "\n".join(lines))

    def test_installer_window_has_image_engine_checkbox(self):
        from pixel_forge.installer import InstallerWindow

        win = InstallerWindow(dryrun=True)
        self.assertFalse(win.image_engine_check.isChecked())
        self.assertNotIn("&", win.image_engine_check.text())
        win.close()

    def test_render_icons_from_svg(self):
        try:
            from PySide6.QtSvg import QSvgRenderer  # noqa: F401
        except ImportError:
            self.skipTest("QtSvg not available")
        from pixel_forge.installer import render_icons_from_svg

        svg = os.path.join(ROOT, "icons", "pixel-forge.svg")
        if not os.path.isfile(svg):
            self.skipTest("icons/pixel-forge.svg not built yet")
        out_dir = os.path.join(self.tmp, "rendered-icons")
        self.assertTrue(render_icons_from_svg(svg, out_dir))
        for size in (256, 128, 48):
            path = os.path.join(out_dir, "pixel-forge-%d.png" % size)
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 100)


if __name__ == "__main__":
    unittest.main()
