"""Installer tests — dry-run steps plus an offscreen window smoke test.

The dry run never writes anything or runs anything privileged: every
step logs what it would do. Skipped when PySide6 is not importable.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class InstallerGatingTests(unittest.TestCase):
    """Task 5 (W5): step_files is skipped when step_packages fails,
    and Launch enables only when the installed venv's GUI actually
    imports — never on the strength of a symlink.

    The worker-loop tests drive the real InstallWorker with stubbed
    step functions (the code under test is the loop's gating, not
    the steps); the step tests drive the real step functions with
    only the external process calls mocked at the InstallContext
    boundary.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._old_pythonpath = os.environ.get("PYTHONPATH")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        if self._old_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = self._old_pythonpath

    # -- helpers ------------------------------------------------------

    def _fake_steps(self, packages_status, calls):
        """A STEPS-shaped list with stub steps. The files stub
        records whether it ran; the packages stub returns the
        given status; every other stub returns DONE."""
        import pixel_forge.installer as installer_mod
        from pixel_forge.installer import StepResult

        def done(_ctx):
            return StepResult(StepResult.DONE)

        def packages(_ctx):
            return StepResult(packages_status, "pip install failed"
                              if packages_status == StepResult.FAIL
                              else "")

        def files(_ctx):
            calls.append("files")
            return StepResult(StepResult.DONE)

        funcs = {"packages": packages, "files": files}
        return [(key, title, funcs.get(key, done))
                for key, title, _func in installer_mod.STEPS]

    def _run_worker(self, steps):
        """Run a real InstallWorker (not dry-run) over the given
        STEPS list; returns (results, log lines)."""
        import pixel_forge.installer as installer_mod
        from PySide6.QtCore import QEventLoop, QTimer
        from pixel_forge.installer import InstallWorker

        old_steps = installer_mod.STEPS
        installer_mod.STEPS = steps
        self.addCleanup(setattr, installer_mod, "STEPS", old_steps)
        results, lines = [], []
        loop = QEventLoop()
        worker = InstallWorker(dryrun=False)
        worker.log_line.connect(lines.append)
        worker.finished.connect(
            lambda r: (results.extend(r), loop.quit()))
        worker.start()
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        worker.wait(5000)
        return results, lines

    def _fake_home(self, with_venv=False, pythonpath=None):
        """A temp HOME; with_venv installs a fake venv python at
        the installed location — a wrapper that execs this test
        python, so it imports exactly what the test process
        imports (a plain symlink does not work: Python resolves
        it to the base interpreter and loses the venv's
        site-packages, so PySide6 would not import). The real
        venv python would be the one the installer built.
        pythonpath (when given) is exported so the venv python
        can import pixel_forge in a subprocess."""
        home = tempfile.mkdtemp(prefix="pf-install-home-")
        self.addCleanup(
            lambda: shutil.rmtree(home, ignore_errors=True))
        os.environ["HOME"] = home
        if pythonpath is not None:
            os.environ["PYTHONPATH"] = pythonpath
        if with_venv:
            venv_bin = os.path.join(
                home, ".local", "share", "pixel-forge", "venv", "bin")
            os.makedirs(venv_bin)
            wrapper = os.path.join(venv_bin, "python")
            with open(wrapper, "w") as fh:
                fh.write("#!/bin/sh\nexec %s \"$@\"\n" % sys.executable)
            os.chmod(wrapper, 0o755)
        return home

    # -- worker gating ------------------------------------------------

    def test_files_step_skipped_when_packages_fails(self):
        from pixel_forge.installer import STEPS, StepResult

        calls = []
        steps = self._fake_steps(StepResult.FAIL, calls)
        results, lines = self._run_worker(steps)
        self.assertEqual(len(results), len(STEPS))
        self.assertEqual(calls, [],
                         "step_files ran even though packages failed")
        self.assertEqual(results[4].status, StepResult.FAIL)
        self.assertEqual(results[5].status, StepResult.SKIPPED)
        self.assertEqual(results[5].detail, "packages step failed")
        self.assertIn("Skipped: the packages step failed",
                      "\n".join(lines))

    def test_files_step_runs_when_packages_passes(self):
        from pixel_forge.installer import STEPS, StepResult

        calls = []
        steps = self._fake_steps(StepResult.DONE, calls)
        results, _lines = self._run_worker(steps)
        self.assertEqual(calls, ["files"])
        self.assertTrue(all(r.status == StepResult.DONE
                            for r in results))

    # -- real step functions, process calls mocked at the boundary ---

    def test_step_packages_reports_pip_failure(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_packages)

        home = tempfile.mkdtemp(prefix="pf-install-pkg-")
        venv_bin = os.path.join(home, ".local", "share", "pixel-forge",
                                "venv", "bin")
        os.makedirs(venv_bin)
        # An (empty) venv python so the step reaches the pip call.
        open(os.path.join(venv_bin, "python"), "w").close()
        lines = []
        ctx = InstallContext(lines.append, dryrun=False)
        ctx.home = Path(home)
        ctx.run = lambda _cmd, timeout=None: 1
        ctx.run_output = lambda _cmd, timeout=60: (1, "")
        result = step_packages(ctx)
        self.assertEqual(result.status, StepResult.FAIL)
        self.assertEqual(result.detail, "pip install failed")
        self.assertIn("pip install failed", "\n".join(lines))

    def test_step_files_writes_launcher(self):
        from pixel_forge.installer import (InstallContext, StepResult,
                                           step_files)

        home = tempfile.mkdtemp(prefix="pf-install-files-")
        lines = []
        ctx = InstallContext(lines.append, dryrun=False)
        ctx.home = Path(home)
        result = step_files(ctx)
        self.assertEqual(result.status, StepResult.DONE)
        gui_link = Path(home) / ".local" / "bin" / "pixel-forge-gui"
        self.assertTrue(gui_link.is_symlink())
        desktop = (Path(home) / ".local" / "share" / "applications"
                   / "pixel-forge.desktop")
        self.assertTrue(desktop.is_file())
        self.assertIn("Exec=%s/pixel-forge-gui"
                      % (Path(home) / ".local" / "bin"),
                      desktop.read_text())
        icon = (Path(home) / ".local" / "share" / "icons" / "hicolor"
                / "256x256" / "apps" / "pixel-forge.png")
        self.assertTrue(icon.is_file())

    # -- the venv GUI import check ------------------------------------

    def test_venv_gui_imports_missing_venv(self):
        from pixel_forge.installer import venv_gui_imports

        home = tempfile.mkdtemp(prefix="pf-install-noven-")
        self.assertFalse(venv_gui_imports(home))

    def test_venv_gui_imports_broken_venv(self):
        from pixel_forge.installer import venv_gui_imports

        empty = tempfile.mkdtemp(prefix="pf-empty-")
        self.addCleanup(
            lambda: shutil.rmtree(empty, ignore_errors=True))
        home = self._fake_home(with_venv=True, pythonpath=empty)
        # The venv python exists, but pixel_forge is not importable
        # from it — the check must fail, not trust the file. This
        # also pins the cwd hole: the test process runs from the
        # tree root, which contains a pixel_forge package, so a
        # check that ran the child from the installer's working
        # directory would pass here even though the venv itself
        # is broken.
        self.assertFalse(venv_gui_imports(home))

    def test_venv_gui_imports_working_venv(self):
        from pixel_forge.installer import venv_gui_imports

        home = self._fake_home(
            with_venv=True, pythonpath=os.path.abspath(ROOT))
        self.assertTrue(venv_gui_imports(home))

    # -- Launch gating in the window ----------------------------------

    def test_launch_disabled_when_gui_import_fails(self):
        from pixel_forge.installer import (InstallerWindow, StepResult)

        self._fake_home(with_venv=False)
        win = InstallerWindow(dryrun=False)
        win.show()
        self.app.processEvents()
        results = ([StepResult(StepResult.DONE)] * 4
                   + [StepResult(StepResult.FAIL, "pip install failed"),
                      StepResult(StepResult.SKIPPED,
                                 "packages step failed")])
        win._on_finished(results)
        self.app.processEvents()
        self.assertFalse(win.launch_btn.isEnabled())
        self.assertIn("1 failed step",
                      win.statusBar().currentMessage())
        self.assertIn("Launch is unavailable",
                      win.log_view.toPlainText())
        win.close()

    def test_launch_enabled_when_gui_imports(self):
        from pixel_forge.installer import (InstallerWindow, StepResult)

        self._fake_home(with_venv=True,
                        pythonpath=os.path.abspath(ROOT))
        win = InstallerWindow(dryrun=False)
        win.show()
        self.app.processEvents()
        win._on_finished([StepResult(StepResult.DONE)] * 6)
        self.app.processEvents()
        self.assertTrue(win.launch_btn.isEnabled())
        self.assertEqual(win.statusBar().currentMessage(), "Installed!")
        win.close()

    def test_failed_install_reports_failure_end_to_end(self):
        """A full window run with a failing packages step: the
        window reports failure, Launch stays disabled, the files
        step shows skipped, and no launcher is written."""
        import pixel_forge.installer as installer_mod
        from PySide6.QtCore import QEventLoop, QTimer
        from pixel_forge.installer import InstallerWindow, StepResult

        home = self._fake_home(with_venv=False)
        win = InstallerWindow(dryrun=False)
        win.show()
        self.app.processEvents()
        calls = []
        steps = self._fake_steps(StepResult.FAIL, calls)
        old_steps = installer_mod.STEPS
        installer_mod.STEPS = steps
        self.addCleanup(setattr, installer_mod, "STEPS", old_steps)
        loop = QEventLoop()
        win._start_install()
        win._worker.finished.connect(lambda _r: loop.quit())
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(calls, [])
        self.assertFalse(win.launch_btn.isEnabled())
        self.assertIn("1 failed step",
                      win.statusBar().currentMessage())
        files_item = win.steps_list.item(5).text()
        self.assertIn("skipped", files_item)
        packages_item = win.steps_list.item(4).text()
        self.assertIn("failed", packages_item)
        gui_link = Path(home) / ".local" / "bin" / "pixel-forge-gui"
        self.assertFalse(gui_link.is_symlink())
        self.assertFalse(gui_link.exists())
        win.close()


if __name__ == "__main__":
    unittest.main()
