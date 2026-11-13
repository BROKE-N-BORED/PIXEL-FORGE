"""Installer tests — dry-run steps plus an offscreen window smoke test.

The dry run never writes anything or runs anything privileged: every
step logs what it would do. Skipped when PySide6 is not importable.
"""
import contextlib
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_log_view_is_capped(self):
        # B15: the log view was unbounded — a 30-minute cmake
        # build appends tens of thousands of lines, slowing the
        # window and growing memory.
        from pixel_forge.installer import InstallerWindow

        win = InstallerWindow(dryrun=True)
        self.assertEqual(win.log_view.maximumBlockCount(), 5000)
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
        # The failure path under test is "ffmpeg was chosen but the
        # install failed" — choose it explicitly: the default is
        # off since v0.12 (nothing selected by default, ever).
        ctx.install_ffmpeg = True
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
        # Unticked by default since v0.12 — nothing selected by
        # default, ever (task 6). It used to be checked.
        self.assertFalse(win.ffmpeg_check.isChecked())
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
class ImageEnginePinTests(unittest.TestCase):
    """Task 31 (core): the GUI installer's image-engine step clones
    at the pinned release and SHA-256-checks the model download.
    The subprocess calls are stubbed at the InstallContext
    boundary — no test clones or downloads anything for real."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _ctx(self, lines, run):
        from pixel_forge.installer import InstallContext

        ctx = InstallContext(lines.append, dryrun=False)
        ctx.install_image_engine = True
        ctx.home = Path(self.tmp)
        # Build tools present, so the step reaches the clone.
        ctx.which = lambda _name: "/usr/bin/" + _name
        ctx.run = run
        ctx.run_admin = lambda _script, timeout=None: 0
        return ctx

    def _drive_step(self, run, engine_values, model_values,
                    check=None, release=None):
        """Drive the real step_image_engine with stubbed process
        calls. engine_values / model_values are the find_* return
        values, consumed in call order; check replaces
        check_model_file (default: records the path it was given);
        release patches ENGINE_RELEASE. Returns (result, lines,
        commands, checked_paths)."""
        import pixel_forge.imagegen as imagegen
        from pixel_forge.installer import step_image_engine

        lines = []
        commands = []
        checked = []

        def recording_run(cmd, timeout=None):
            commands.append(list(cmd))
            return run(cmd, timeout)

        ctx = self._ctx(lines, recording_run)
        engine_iter = iter(engine_values)
        model_iter = iter(model_values)
        patches = [
            mock.patch.object(imagegen, "find_engine",
                              lambda: next(engine_iter)),
            mock.patch.object(imagegen, "find_model",
                              lambda: next(model_iter)),
            mock.patch.object(
                imagegen, "check_model_file",
                check if check is not None
                else lambda p: checked.append(Path(p))),
        ]
        if release is not None:
            patches.append(mock.patch.object(
                imagegen, "ENGINE_RELEASE", release))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            result = step_image_engine(ctx)
        return result, lines, commands, checked

    def _curl_lands_a_file(self, cmd, timeout=None):
        """A stub run: curl writes a small file at its -o target,
        everything else succeeds silently."""
        if cmd[0] == "curl":
            dest = Path(cmd[cmd.index("-o") + 1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake model bytes")
        return 0

    def test_step_clones_pinned_release_and_checks_model(self):
        import pixel_forge.imagegen as imagegen
        from pixel_forge.installer import StepResult

        result, _lines, commands, checked = self._drive_step(
            self._curl_lands_a_file,
            engine_values=[None, Path("/opt/sd.cpp/bin/sd-cli")],
            model_values=[None, None])
        self.assertEqual(result.status, StepResult.DONE)
        clones = [c for c in commands
                  if len(c) > 1 and c[1] == "clone"]
        self.assertEqual(len(clones), 1)
        clone = clones[0]
        self.assertEqual(clone[clone.index("--branch") + 1],
                         imagegen.ENGINE_RELEASE)
        self.assertIn("--recursive", clone)
        self.assertEqual(clone[clone.index("--depth") + 1], "1")
        self.assertTrue(any(c[0] == "cmake" for c in commands),
                        "cmake never ran after the clone")
        curls = [c for c in commands if c[0] == "curl"]
        self.assertEqual(len(curls), 1)
        self.assertIn("-C", curls[0])
        self.assertEqual(curls[0][curls[0].index("-C") + 1], "-")
        self.assertEqual(
            checked,
            [Path(self.tmp) / "design-shell-models"
             / imagegen.MODEL_FILENAME],
            "the model download was never hash-checked")

    def test_step_uses_the_pinned_constant(self):
        # The bug being fixed was a defined-but-unreferenced pin:
        # change the constant and the clone command must follow it,
        # or the step carries a duplicated literal.
        result, _lines, commands, _checked = self._drive_step(
            self._curl_lands_a_file,
            engine_values=[None, Path("/opt/sd.cpp/bin/sd-cli")],
            model_values=[None, None],
            release="sentinel-release-2")
        from pixel_forge.installer import StepResult

        self.assertEqual(result.status, StepResult.DONE)
        clones = [c for c in commands
                  if len(c) > 1 and c[1] == "clone"]
        self.assertEqual(len(clones), 1)
        self.assertIn("sentinel-release-2", clones[0])

    def test_clone_failure_fails_loudly_with_the_reason(self):
        import pixel_forge.imagegen as imagegen
        from pixel_forge.installer import StepResult

        def run(cmd, timeout=None):
            if len(cmd) > 1 and cmd[1] == "clone":
                return 128
            return 0

        result, lines, commands, _checked = self._drive_step(
            run, engine_values=[None],
            model_values=[Path("/m/model.gguf")])
        self.assertEqual(result.status, StepResult.WARN)
        self.assertIn("checkout failed", result.detail)
        log = "\n".join(lines)
        self.assertIn(imagegen.ENGINE_RELEASE, log)
        self.assertIn("git clone exit 128", log)
        self.assertFalse(any(c[0] == "cmake" for c in commands),
                         "cmake ran after a failed clone")

    def test_checksum_mismatch_deletes_the_file(self):
        import pixel_forge.imagegen as imagegen
        from pixel_forge.installer import StepResult
        from pixel_forge.pixelize import PixelForgeError

        expected = "a" * 64
        actual = "b" * 64

        def bad_check(path):
            raise PixelForgeError(
                "the image model at %s failed its SHA-256 check — "
                "expected %s, got %s" % (path, expected, actual))

        result, lines, _commands, _checked = self._drive_step(
            self._curl_lands_a_file,
            engine_values=[Path("/opt/sd.cpp/bin/sd-cli")],
            model_values=[None, None], check=bad_check)
        self.assertEqual(result.status, StepResult.WARN)
        self.assertIn("checksum failed", result.detail)
        model_path = (Path(self.tmp) / "design-shell-models"
                      / imagegen.MODEL_FILENAME)
        self.assertFalse(model_path.exists(),
                         "the bad model file was kept")
        log = "\n".join(lines)
        self.assertIn(expected, log)
        self.assertIn(actual, log)
        self.assertIn("removed", log)

    def test_failed_download_keeps_the_partial_file(self):
        import pixel_forge.imagegen as imagegen
        from pixel_forge.installer import StepResult

        def run(cmd, timeout=None):
            if cmd[0] == "curl":
                # A partial download from an earlier interrupted run.
                dest = Path(cmd[cmd.index("-o") + 1])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"partial")
                return 22
            return 0

        result, lines, _commands, checked = self._drive_step(
            run, engine_values=[Path("/opt/sd.cpp/bin/sd-cli")],
            model_values=[None, None])
        self.assertEqual(result.status, StepResult.WARN)
        self.assertIn("download failed", result.detail)
        model_path = (Path(self.tmp) / "design-shell-models"
                      / imagegen.MODEL_FILENAME)
        self.assertTrue(model_path.exists(),
                        "the partial file was deleted — resume lost")
        self.assertEqual(checked, [],
                         "the hash check ran on a failed download")
        # B13: the failure warning once named only the shared
        # home — a model in either home makes prompt import work.
        log = "\n".join(lines)
        self.assertIn("~/design-shell-models/", log)
        self.assertIn("~/pixel-forge-models/", log)


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

    def _stub_ctx(self, venv_rc=0, pip_rc=0, which=None,
                  install_ffmpeg=False, install_image_engine=False):
        """A minimal InstallContext-shaped stub for
        _admin_needed_steps: run_output answers the venv/pip
        checks, which answers tool lookups."""
        class StubCtx:
            def __init__(self):
                self.install_ffmpeg = install_ffmpeg
                self.install_image_engine = install_image_engine
                self._which = which or {}

            def run_output(self, cmd, timeout=60):
                if "-m" in cmd and "venv" in cmd:
                    return venv_rc, ""
                if "-m" in cmd and "pip" in cmd:
                    return pip_rc, ""
                return 0, ""

            def which(self, name):
                return self._which.get(name)

        return StubCtx()

    def test_admin_needed_steps(self):
        # B15: the up-front pkexec check mirrors the steps' own
        # conditions — system when venv or pip is missing, ffmpeg
        # when requested and missing, image-engine when requested
        # and (build tools missing or the engine missing).
        from pixel_forge import imagegen as imagegen_mod
        from pixel_forge.installer import _admin_needed_steps

        self.assertEqual(_admin_needed_steps(self._stub_ctx()), [])
        self.assertEqual(
            _admin_needed_steps(self._stub_ctx(pip_rc=1)),
            ["system"])
        self.assertEqual(
            _admin_needed_steps(self._stub_ctx(venv_rc=1)),
            ["system"])
        self.assertEqual(
            _admin_needed_steps(self._stub_ctx(
                install_ffmpeg=True, which={})),
            ["ffmpeg"])
        self.assertEqual(
            _admin_needed_steps(self._stub_ctx(
                install_ffmpeg=True,
                which={"ffmpeg": "/usr/bin/ffmpeg"})),
            [])
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=None):
            self.assertEqual(
                _admin_needed_steps(self._stub_ctx(
                    install_image_engine=True,
                    which={"git": "g", "cmake": "c", "g++": "x"})),
                ["image-engine"])
            self.assertEqual(
                _admin_needed_steps(self._stub_ctx(
                    install_image_engine=True, which={})),
                ["image-engine"])
        with mock.patch.object(imagegen_mod, "find_engine",
                               return_value=Path("/opt/sd.cpp/bin/"
                                                 "sd-cli")):
            self.assertEqual(
                _admin_needed_steps(self._stub_ctx(
                    install_image_engine=True,
                    which={"git": "g", "cmake": "c", "g++": "x"})),
                [])

    def test_worker_fails_fast_when_pkexec_missing(self):
        # B15: pkexec missing used to fail step by step. With the
        # system step needing admin, the worker says so once and
        # stops — no step runs.
        import pixel_forge.installer as installer_mod
        from pixel_forge.installer import (
            InstallContext, STEPS, StepResult)

        calls = []
        steps = self._fake_steps(StepResult.DONE, calls)
        real_which = shutil.which

        def _no_pkexec(name):
            if name == "pkexec":
                return None
            return real_which(name)

        def _no_venv_or_pip(self, cmd, timeout=60):
            return 1, ""

        with mock.patch.object(installer_mod.shutil, "which",
                               side_effect=_no_pkexec), \
                mock.patch.object(InstallContext, "run_output",
                                  _no_venv_or_pip):
            results, lines = self._run_worker(steps)
        log = "\n".join(lines)
        self.assertEqual(log.count("pkexec is not installed"), 1,
                         "the pkexec message must appear once: %r"
                         % log)
        self.assertEqual(calls, [], "a step ran after the fail-fast")
        self.assertEqual(len(results), len(STEPS))
        self.assertEqual(results[0].status, StepResult.FAIL)
        self.assertIn("pkexec", results[0].detail)
        for result in results[1:]:
            self.assertEqual(result.status, StepResult.SKIPPED)

    def test_run_admin_stays_quiet_after_upfront_warning(self):
        # B15: once the worker's up-front check has said pkexec
        # is missing, run_admin does not repeat it after every
        # step — and without the up-front check it still says so
        # exactly once.
        import pixel_forge.installer as installer_mod
        from pixel_forge.installer import InstallContext

        real_which = shutil.which

        def _no_pkexec(name):
            if name == "pkexec":
                return None
            return real_which(name)

        with mock.patch.object(installer_mod.shutil, "which",
                               side_effect=_no_pkexec):
            lines = []
            ctx = InstallContext(lines.append, dryrun=False)
            ctx.pkexec_warned = True
            self.assertEqual(ctx.run_admin("true"), 127)
            self.assertNotIn("pkexec", "\n".join(lines),
                             "run_admin repeated the pkexec line")
            lines2 = []
            ctx2 = InstallContext(lines2.append, dryrun=False)
            self.assertEqual(ctx2.run_admin("true"), 127)
            self.assertEqual(ctx2.run_admin("true"), 127)
            joined = "\n".join(lines2)
            self.assertEqual(joined.count("pkexec not found"), 1)


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class InstallerDefaultsTests(unittest.TestCase):
    """Task 6: nothing selected by default, ever. The ffmpeg
    option used to be pre-selected in the GUI installer (checked
    checkbox) and in the worker/context defaults, so an install
    went ahead and installed ffmpeg unless the user opted out."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self.addCleanup(self._restore_env)
        # _on_finished runs the venv import check against HOME;
        # point it at an empty temp dir so nothing real is read.
        os.environ["HOME"] = tempfile.mkdtemp(prefix="pf-install-def-")

    def _restore_env(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def test_fresh_window_has_nothing_checked(self):
        from pixel_forge.installer import InstallerWindow

        win = InstallerWindow(dryrun=True)
        self.assertFalse(win.ffmpeg_check.isChecked(),
                         "ffmpeg checkbox is checked by default")
        self.assertFalse(win.image_engine_check.isChecked(),
                         "image-engine checkbox is checked by default")
        win.close()

    def test_defaults_are_off_one_layer_down(self):
        """A bare worker or context must not install ffmpeg either
        — no caller can trigger the install without the user's
        choice. Mirrors test_image_engine_step_skipped_by_default."""
        import inspect

        from pixel_forge.installer import (InstallContext, InstallWorker,
                                           StepResult, step_ffmpeg)

        ctx = InstallContext(lambda _msg: None, dryrun=True)
        self.assertFalse(ctx.install_ffmpeg)
        default = inspect.signature(
            InstallWorker.__init__).parameters["install_ffmpeg"].default
        self.assertIs(default, False)
        ctx.which = lambda _name: None  # ffmpeg missing
        result = step_ffmpeg(ctx)
        self.assertEqual(result.status, StepResult.DONE)
        self.assertEqual(result.detail, "skipped")

    def _steps_recording_ffmpeg_flag(self, seen):
        """STEPS-shaped stubs; the ffmpeg stub records the flag the
        worker handed its context and does nothing else."""
        import pixel_forge.installer as installer_mod
        from pixel_forge.installer import StepResult

        def done(_ctx):
            return StepResult(StepResult.DONE)

        def ffmpeg(ctx):
            seen.append(ctx.install_ffmpeg)
            return StepResult(StepResult.DONE)

        funcs = {"ffmpeg": ffmpeg}
        return [(key, title, funcs.get(key, done))
                for key, title, _func in installer_mod.STEPS]

    def _run_window_install(self, win):
        """Drive the real _start_install to completion over the
        stub steps; returns when the worker finishes."""
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        win._start_install()
        win._worker.finished.connect(lambda _r: loop.quit())
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        self.app.processEvents()
        self.app.processEvents()

    def test_ticking_ffmpeg_checkbox_passes_flag_to_worker(self):
        """The checkbox still works: ticked, the flag reaches the
        ffmpeg step as True through the real window -> worker ->
        context wiring. No real install runs (stub steps)."""
        import pixel_forge.installer as installer_mod
        from pixel_forge.installer import InstallerWindow

        seen = []
        steps = self._steps_recording_ffmpeg_flag(seen)
        old_steps = installer_mod.STEPS
        installer_mod.STEPS = steps
        self.addCleanup(setattr, installer_mod, "STEPS", old_steps)
        win = InstallerWindow(dryrun=False)
        win.show()
        self.app.processEvents()
        win.ffmpeg_check.setChecked(True)
        self._run_window_install(win)
        self.assertEqual(seen, [True],
                         "ticked checkbox did not reach the ffmpeg step")
        win.close()

    def test_unticked_ffmpeg_checkbox_passes_false_to_worker(self):
        """The default, end to end: a fresh window (unticked) runs
        the ffmpeg step with the flag False."""
        import pixel_forge.installer as installer_mod
        from pixel_forge.installer import InstallerWindow

        seen = []
        steps = self._steps_recording_ffmpeg_flag(seen)
        old_steps = installer_mod.STEPS
        installer_mod.STEPS = steps
        self.addCleanup(setattr, installer_mod, "STEPS", old_steps)
        win = InstallerWindow(dryrun=False)
        win.show()
        self.app.processEvents()
        self._run_window_install(win)
        self.assertEqual(seen, [False],
                         "unticked checkbox reached the step as True")
        win.close()


def _ffmpeg_prompt_block():
    """The exact ffmpeg prompt lines from install.sh, verbatim —
    from the `read` line through the closing `esac`. The tests
    below run these lines, not a paraphrase of them."""
    path = os.path.join(ROOT, "install.sh")
    with open(path) as fh:
        text = fh.read()
    start = text.index('read -rp "Install ffmpeg now?')
    end = text.index("    esac", start) + len("    esac")
    return text[start:end]


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class InstallScriptTests(unittest.TestCase):
    """Task 6, terminal installer: install.sh's ffmpeg prompt
    defaults to No. The tests run the exact shipped lines with a
    stubbed `sudo` — never a real install."""

    def _run_prompt_block(self, stdin_text):
        block = _ffmpeg_prompt_block()
        script = ("sudo() { echo \"SUDO-CALLED: $*\"; return 0; }\n"
                  + block + "\n")
        return subprocess.run(["bash", "-c", script],
                              input=stdin_text, capture_output=True,
                              text=True, timeout=30)

    def test_prompt_shows_no_default(self):
        block = _ffmpeg_prompt_block()
        self.assertIn("[y/N]", block)
        self.assertIn('${_answer:-N}', block)

    def test_empty_answer_defaults_to_no(self):
        proc = self._run_prompt_block("\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Skipped ffmpeg", proc.stdout)
        self.assertNotIn("SUDO-CALLED", proc.stdout)

    def test_eof_defaults_to_no(self):
        proc = self._run_prompt_block("")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Skipped ffmpeg", proc.stdout)
        self.assertNotIn("SUDO-CALLED", proc.stdout)

    def test_explicit_yes_still_installs(self):
        proc = self._run_prompt_block("y\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUDO-CALLED", proc.stdout)
        self.assertNotIn("Skipped ffmpeg", proc.stdout)

    def test_explicit_no_skips(self):
        proc = self._run_prompt_block("n\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Skipped ffmpeg", proc.stdout)
        self.assertNotIn("SUDO-CALLED", proc.stdout)

    def _pip_check_block(self):
        """The exact pip-availability lines from install.sh,
        verbatim — from the `if` through its closing `fi`."""
        text = _install_sh_text()
        start = text.index("if ! python3 -m pip --help")
        end = text.index("\nfi", start) + len("\nfi")
        return text[start:end]

    def _run_pip_block(self, pip_ok):
        # A stubbed `python3`: -m pip fails when pip_ok is False.
        stub = ("python3() { case \"$*\" in *\"-m pip\"*) "
                "return %d;; *) return 0;; esac; }\n"
                % (0 if pip_ok else 1))
        return subprocess.run(["bash", "-c",
                               stub + self._pip_check_block()],
                              capture_output=True, text=True,
                              timeout=30)

    def test_pip_check_fails_plainly_when_pip_missing(self):
        # B15: install.sh checked venv but not pip.
        proc = self._run_pip_block(pip_ok=False)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("python3-pip is missing", proc.stdout)
        self.assertIn("sudo apt install python3-pip", proc.stdout)

    def test_pip_check_passes_when_pip_present(self):
        proc = self._run_pip_block(pip_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")


def _install_sh_text():
    path = os.path.join(ROOT, "install.sh")
    with open(path) as fh:
        return fh.read()


def _imagegen_constant(name):
    """A module-level constant from pixel_forge/imagegen.py, read
    from the source with ast — no import, so the test pins the
    literal the installers must match."""
    import ast

    path = os.path.join(ROOT, "pixel_forge", "imagegen.py")
    with open(path) as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("no %s in imagegen.py" % name)


def _model_download_block():
    """The exact model download + hash-check lines from
    install.sh, verbatim — from the curl line through the closing
    `fi` of the checksum check. The tests below run these lines,
    not a paraphrase of them."""
    text = _install_sh_text()
    start = text.index("curl -fSL -C -")
    end = text.index('echo "Model downloaded to', start)
    return text[start:end]


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class InstallScriptPinTests(unittest.TestCase):
    """Task 31 (core): install.sh pins the same release and model
    hash as imagegen.py, and its shipped lines delete a bad
    download. Bash runs use a stubbed `curl` — never a real
    download."""

    def test_pinned_tag_matches_imagegen(self):
        match = re.search(r'^SD_RELEASE="([^"]+)"$',
                          _install_sh_text(), re.MULTILINE)
        self.assertIsNotNone(match, "install.sh has no SD_RELEASE")
        self.assertEqual(match.group(1),
                         _imagegen_constant("ENGINE_RELEASE"))

    def test_pinned_hash_matches_imagegen(self):
        match = re.search(r'^SD_MODEL_SHA256="([0-9a-f]{64})"$',
                          _install_sh_text(), re.MULTILINE)
        self.assertIsNotNone(match,
                             "install.sh has no SD_MODEL_SHA256")
        self.assertEqual(match.group(1),
                         _imagegen_constant("MODEL_SHA256"))

    def test_clone_uses_the_pinned_tag(self):
        text = _install_sh_text()
        self.assertIn('git clone --depth 1 --branch "$SD_RELEASE" '
                      '--recursive "$SD_REPO"', text)

    def test_download_resumes(self):
        self.assertIn("curl -fSL -C -", _install_sh_text())

    def _run_download_block(self, content, sha):
        """Run the shipped download + check lines with a stubbed
        curl that writes `content`, and SD_MODEL_SHA256 set to
        `sha`. Returns (proc, model_path)."""
        home = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(home, ignore_errors=True))
        script = (
            "set -euo pipefail\n"
            "SD_MODEL_HOME=%s\n"
            "SD_MODEL_FILE=LCM_Dreamshaper_v7-f16.gguf\n"
            "SD_MODEL_URL=https://example.invalid/model.gguf\n"
            "SD_MODEL_SHA256=%s\n"
            "curl() { mkdir -p \"$SD_MODEL_HOME\"; "
            "printf '%%s' %s > \"$SD_MODEL_HOME/$SD_MODEL_FILE\"; }\n"
            % (shlex.quote(home), sha, shlex.quote(content))
            + _model_download_block()
        )
        proc = subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True,
                              timeout=30)
        return proc, os.path.join(home,
                                  "LCM_Dreamshaper_v7-f16.gguf")

    def test_bad_download_is_deleted(self):
        proc, path = self._run_download_block(
            "corrupt-model-bytes", "0" * 64)
        output = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, output)
        self.assertFalse(os.path.exists(path),
                         "the bad file was kept")
        self.assertIn("failed its SHA-256 check", output)
        self.assertIn("expected: " + "0" * 64, output)
        self.assertIn("got:      ", output)
        self.assertIn("removed", output)

    def test_good_download_is_kept(self):
        content = "good-model-bytes"
        sha = hashlib.sha256(content.encode()).hexdigest()
        proc, path = self._run_download_block(content, sha)
        output = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, output)
        self.assertTrue(os.path.exists(path))
        with open(path) as fh:
            self.assertEqual(fh.read(), content)


def _load_launcher():
    """install-pixel-forge.py as a module — it lives at the tree
    root, not in the package."""
    import importlib.util

    path = os.path.join(ROOT, "install-pixel-forge.py")
    spec = importlib.util.spec_from_file_location(
        "install_pixel_forge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HAS_QT, "PySide6 not available")
class LauncherTests(unittest.TestCase):
    """install-pixel-forge.py (the GUI installer launcher):
    have_pyside6's exception set and the pip path's apt update."""

    def test_have_pyside6_true_when_importable(self):
        launcher = _load_launcher()
        self.assertTrue(launcher.have_pyside6())

    def test_have_pyside6_false_when_import_raises_oserror(self):
        # B15: have_pyside6 caught ImportError only — a PySide6
        # whose shared library cannot be loaded escaped the
        # check instead of reading as "not usable".
        import builtins

        launcher = _load_launcher()
        real_import = builtins.__import__

        def _raising_import(name, *args, **kwargs):
            if name == "PySide6":
                raise OSError("cannot load shared library")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__",
                               side_effect=_raising_import):
            self.assertFalse(launcher.have_pyside6())

    def test_have_pyside6_false_when_import_raises_importerror(self):
        import builtins

        launcher = _load_launcher()
        real_import = builtins.__import__

        def _raising_import(name, *args, **kwargs):
            if name == "PySide6":
                raise ImportError("No module named 'PySide6'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__",
                               side_effect=_raising_import):
            self.assertFalse(launcher.have_pyside6())

    def test_pip_path_updates_before_installing_python3_pip(self):
        # B15: the pip fallback ran `apt-get install -y
        # python3-pip` with no `apt-get update` first, so it
        # could fail on a stale cache.
        launcher = _load_launcher()
        calls = []
        real_which = shutil.which

        def _fake_run(cmd, timeout=900):
            calls.append(cmd)
            if cmd[:4] == [sys.executable, "-m", "pip", "--version"]:
                return False  # no pip at all — take the apt path
            return True

        def _with_pkexec(name):
            if name == "pkexec":
                return "/usr/bin/pkexec"
            return real_which(name)

        with mock.patch.object(launcher, "_run_quiet",
                               side_effect=_fake_run), \
                mock.patch.object(launcher.shutil, "which",
                                  side_effect=_with_pkexec):
            self.assertTrue(launcher._install_pyside6_pip())
        apt_cmds = [c for c in calls if c and c[0] == "pkexec"]
        self.assertEqual(len(apt_cmds), 1)
        script = apt_cmds[0][-1]
        self.assertIn("apt-get install -y python3-pip", script)
        self.assertLess(script.index("apt-get update"),
                        script.index("apt-get install -y python3-pip"),
                        "update must run before the install")


if __name__ == "__main__":
    unittest.main()
