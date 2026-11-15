"""Tests for install-pixel-forge.py, the GUI installer launcher.

Task 59: the apt/pip fallback chain was entirely untested. The
launcher is standard-library only, so these tests run in both
suites (no Qt needed). Nothing here runs a real apt or pip
install — every subprocess call is stubbed or mocked, except
_run_quiet's own tests, which run the current Python with
trivial commands.

What test_installer.py's LauncherTests already cover (not
duplicated here): have_pyside6's exception set, the pip path's
apt update running before the python3-pip install, _has_network
on answer / HTTP error / unreachable host, and main's offline
message.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_launcher():
    """install-pixel-forge.py as a module — it lives at the tree
    root, not in the package. A fresh module object each time, so
    mocks on one load never leak into another."""
    path = os.path.join(ROOT, "install-pixel-forge.py")
    spec = importlib.util.spec_from_file_location(
        "install_pixel_forge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _which_map(mapping):
    """A shutil.which stand-in: names in the mapping resolve to
    their value, everything else to None."""

    def _which(name):
        return mapping.get(name)

    return _which


class RunQuietTests(unittest.TestCase):
    """_run_quiet: True only when the command runs and exits 0."""

    def test_exit_zero_is_true(self):
        launcher = _load_launcher()
        self.assertTrue(launcher._run_quiet(
            [sys.executable, "-c", "pass"], timeout=60))

    def test_nonzero_exit_is_false(self):
        launcher = _load_launcher()
        self.assertFalse(launcher._run_quiet(
            [sys.executable, "-c", "raise SystemExit(1)"],
            timeout=60))

    def test_missing_command_is_false(self):
        launcher = _load_launcher()
        self.assertFalse(launcher._run_quiet(
            ["pixel-forge-no-such-command-xyz"], timeout=60))

    def test_timeout_is_false(self):
        launcher = _load_launcher()
        self.assertFalse(launcher._run_quiet(
            [sys.executable, "-c",
             "import time; time.sleep(30)"], timeout=1))


class ZenityProgressTests(unittest.TestCase):
    """_zenity_progress: a pulsating dialog, or None when zenity
    is missing or will not start."""

    def test_no_zenity_returns_none(self):
        launcher = _load_launcher()
        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map({})):
            self.assertIsNone(launcher._zenity_progress("working…"))

    def test_zenity_present_returns_the_dialog(self):
        launcher = _load_launcher()
        proc = mock.Mock()
        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(
                                   {"zenity": "/usr/bin/zenity"})), \
                mock.patch.object(launcher.subprocess, "Popen",
                                  return_value=proc) as popen:
            self.assertIs(launcher._zenity_progress("working…"),
                          proc)
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], "zenity")
        self.assertIn("--progress", cmd)
        self.assertIn("--pulsate", cmd)
        self.assertIn("--text=working…", cmd)

    def test_zenity_that_will_not_start_returns_none(self):
        launcher = _load_launcher()
        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(
                                   {"zenity": "/usr/bin/zenity"})), \
                mock.patch.object(launcher.subprocess, "Popen",
                                  side_effect=OSError("no exec")):
            self.assertIsNone(launcher._zenity_progress("working…"))


class AptInstallTests(unittest.TestCase):
    """_install_pyside6_apt: pkexec + apt, then the import check."""

    def _run(self, launcher, which, run_ok, have):
        """Run the apt path with stubbed pieces; returns
        (result, calls, progress)."""
        calls = []
        progress = mock.Mock()

        def _run_quiet(cmd, timeout=900):
            calls.append(cmd)
            return run_ok

        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(which)), \
                mock.patch.object(launcher, "_zenity_progress",
                                  return_value=progress), \
                mock.patch.object(launcher, "_run_quiet",
                                  side_effect=_run_quiet), \
                mock.patch.object(launcher, "have_pyside6",
                                  return_value=have):
            return launcher._install_pyside6_apt(), calls, progress

    def test_no_pkexec_returns_false_without_running_anything(self):
        launcher = _load_launcher()
        result, calls, _progress = self._run(
            launcher, {}, True, True)
        self.assertFalse(result)
        self.assertEqual(calls, [])

    def test_apt_success_and_importable_returns_true(self):
        launcher = _load_launcher()
        result, calls, progress = self._run(
            launcher, {"pkexec": "/usr/bin/pkexec"}, True, True)
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ["pkexec", "bash", "-c"])
        self.assertIn("apt-get install -y python3-pyside6",
                      calls[0][3])
        progress.terminate.assert_called_once_with()
        progress.wait.assert_called_once_with(timeout=5)
        progress.kill.assert_not_called()

    def test_apt_success_but_still_not_importable_returns_false(self):
        launcher = _load_launcher()
        result, _calls, _progress = self._run(
            launcher, {"pkexec": "/usr/bin/pkexec"}, True, False)
        self.assertFalse(result)

    def test_apt_failure_returns_false(self):
        launcher = _load_launcher()
        result, _calls, _progress = self._run(
            launcher, {"pkexec": "/usr/bin/pkexec"}, False, True)
        self.assertFalse(result)

    def test_a_stuck_progress_dialog_is_killed(self):
        launcher = _load_launcher()
        progress = mock.Mock()
        progress.wait.side_effect = subprocess.TimeoutExpired(
            "zenity", 5)
        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(
                                   {"pkexec": "/usr/bin/pkexec"})), \
                mock.patch.object(launcher, "_zenity_progress",
                                  return_value=progress), \
                mock.patch.object(launcher, "_run_quiet",
                                  return_value=True), \
                mock.patch.object(launcher, "have_pyside6",
                                  return_value=True):
            self.assertTrue(launcher._install_pyside6_apt())
        progress.terminate.assert_called_once_with()
        progress.kill.assert_called_once_with()

    def test_no_progress_dialog_is_fine(self):
        # zenity missing: _zenity_progress returns None, and the
        # apt path must not touch it.
        launcher = _load_launcher()
        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(
                                   {"pkexec": "/usr/bin/pkexec"})), \
                mock.patch.object(launcher, "_zenity_progress",
                                  return_value=None), \
                mock.patch.object(launcher, "_run_quiet",
                                  return_value=True), \
                mock.patch.object(launcher, "have_pyside6",
                                  return_value=True):
            self.assertTrue(launcher._install_pyside6_apt())


class PipInstallTests(unittest.TestCase):
    """_install_pyside6_pip: pip --user, with the apt fallback
    for a missing pip."""

    def _run(self, launcher, which, pip_version_ok, apt_ok,
             install_ok, have):
        """Run the pip path with stubbed pieces; returns
        (result, calls)."""
        calls = []

        def _run_quiet(cmd, timeout=900):
            calls.append(cmd)
            if cmd == [sys.executable, "-m", "pip", "--version"]:
                return pip_version_ok
            if cmd and cmd[0] == "pkexec":
                return apt_ok
            return install_ok

        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(which)), \
                mock.patch.object(launcher, "_run_quiet",
                                  side_effect=_run_quiet), \
                mock.patch.object(launcher, "have_pyside6",
                                  return_value=have):
            return launcher._install_pyside6_pip(), calls

    def test_pip_present_goes_straight_to_install(self):
        launcher = _load_launcher()
        result, calls = self._run(
            launcher, {"pkexec": "/usr/bin/pkexec"}, True, True,
            True, True)
        self.assertTrue(result)
        # pip --version, then the install — no pkexec/apt call.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:5],
                         [sys.executable, "-m", "pip", "install",
                          "--user"])
        self.assertIn("PySide6-Essentials", calls[1])

    def test_pip_install_failure_returns_false(self):
        launcher = _load_launcher()
        result, _calls = self._run(
            launcher, {}, True, True, False, True)
        self.assertFalse(result)

    def test_pip_install_success_but_still_not_importable(self):
        launcher = _load_launcher()
        result, _calls = self._run(
            launcher, {}, True, True, True, False)
        self.assertFalse(result)

    def test_no_pip_and_no_pkexec_skips_apt_and_still_tries(self):
        launcher = _load_launcher()
        result, calls = self._run(
            launcher, {}, False, True, True, True)
        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(c and c[0] == "pkexec"
                             for c in calls))

    def test_a_failed_apt_for_pip_does_not_stop_the_install(self):
        # The apt result is ignored: pip may have appeared by
        # another route, or the install may still work — the
        # launcher tries it either way.
        launcher = _load_launcher()
        result, calls = self._run(
            launcher, {"pkexec": "/usr/bin/pkexec"}, False, False,
            True, True)
        self.assertTrue(result)
        self.assertEqual(len(calls), 3)
        self.assertIn("apt-get install -y python3-pip",
                      calls[1][3])


class ShowErrorTests(unittest.TestCase):
    """_show_error: zenity, else notify-send, else stderr — and
    the message always reaches stderr too."""

    def _run(self, launcher, which):
        calls = []
        err = io.StringIO()
        with mock.patch.object(launcher.shutil, "which",
                               side_effect=_which_map(which)), \
                mock.patch.object(launcher, "_run_quiet",
                                  side_effect=lambda c,
                                  timeout=None: calls.append(c)
                                  or True), \
                contextlib.redirect_stderr(err):
            launcher._show_error("something broke")
        return calls, err.getvalue()

    def test_zenity_shows_the_error_dialog(self):
        launcher = _load_launcher()
        calls, err = self._run(
            launcher, {"zenity": "/usr/bin/zenity",
                       "notify-send": "/usr/bin/notify-send"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3],
                         ["zenity", "--error", "--title=Pixel Forge"])
        self.assertIn("--text=something broke", calls[0])
        self.assertIn("something broke", err)

    def test_notify_send_when_zenity_is_missing(self):
        launcher = _load_launcher()
        calls, err = self._run(
            launcher, {"notify-send": "/usr/bin/notify-send"})
        self.assertEqual(calls, [["notify-send", "Pixel Forge",
                                  "something broke"]])
        self.assertIn("something broke", err)

    def test_stderr_only_when_neither_is_present(self):
        launcher = _load_launcher()
        calls, err = self._run(launcher, {})
        self.assertEqual(calls, [])
        self.assertIn("something broke", err)


class EnsurePyside6Tests(unittest.TestCase):
    """ensure_pyside6: apt first, then pip, then one last check."""

    def test_already_importable_tries_nothing(self):
        launcher = _load_launcher()
        with mock.patch.object(launcher, "have_pyside6",
                               return_value=True), \
                mock.patch.object(launcher, "_install_pyside6_apt",
                                  side_effect=AssertionError(
                                      "apt must not run")), \
                mock.patch.object(launcher, "_install_pyside6_pip",
                                  side_effect=AssertionError(
                                      "pip must not run")):
            self.assertTrue(launcher.ensure_pyside6())

    def test_apt_success_means_pip_never_runs(self):
        launcher = _load_launcher()
        with mock.patch.object(launcher, "have_pyside6",
                               return_value=False), \
                mock.patch.object(launcher, "_install_pyside6_apt",
                                  return_value=True), \
                mock.patch.object(launcher, "_install_pyside6_pip",
                                  side_effect=AssertionError(
                                      "pip must not run")):
            self.assertTrue(launcher.ensure_pyside6())

    def test_apt_failure_falls_back_to_pip(self):
        launcher = _load_launcher()
        with mock.patch.object(launcher, "have_pyside6",
                               return_value=False), \
                mock.patch.object(launcher, "_install_pyside6_apt",
                                  return_value=False), \
                mock.patch.object(launcher, "_install_pyside6_pip",
                                  return_value=True):
            self.assertTrue(launcher.ensure_pyside6())

    def test_both_fail_returns_false(self):
        launcher = _load_launcher()
        with mock.patch.object(launcher, "have_pyside6",
                               return_value=False), \
                mock.patch.object(launcher, "_install_pyside6_apt",
                                  return_value=False), \
                mock.patch.object(launcher, "_install_pyside6_pip",
                                  return_value=False):
            self.assertFalse(launcher.ensure_pyside6())

    def test_installed_by_another_route_counts(self):
        # Both install attempts report failure, but the final
        # check finds PySide6 importable — installed by another
        # route in the meantime — so ensure still succeeds.
        launcher = _load_launcher()
        with mock.patch.object(launcher, "have_pyside6",
                               side_effect=[False, True]), \
                mock.patch.object(launcher, "_install_pyside6_apt",
                                  return_value=False), \
                mock.patch.object(launcher, "_install_pyside6_pip",
                                  return_value=False):
            self.assertTrue(launcher.ensure_pyside6())


class MainTests(unittest.TestCase):
    """main: the offline message is covered in test_installer.py;
    here are the install-failure message and the handoff to the
    installer window."""

    def _fake_installer(self):
        """A stand-in pixel_forge.installer module in sys.modules
        plus the package attribute, restored afterwards — the
        real installer needs Qt, which the bare suite has not."""
        import pixel_forge

        fake = types.ModuleType("pixel_forge.installer")
        calls = []
        fake.main = lambda argv=None: calls.append(argv) or 7
        saved_module = sys.modules.get("pixel_forge.installer")
        saved_attr = getattr(pixel_forge, "installer", None)
        had_attr = hasattr(pixel_forge, "installer")
        sys.modules["pixel_forge.installer"] = fake
        pixel_forge.installer = fake
        self.addCleanup(self._restore_installer, saved_module,
                        saved_attr, had_attr)
        return fake, calls

    @staticmethod
    def _restore_installer(saved_module, saved_attr, had_attr):
        import pixel_forge

        if saved_module is None:
            sys.modules.pop("pixel_forge.installer", None)
        else:
            sys.modules["pixel_forge.installer"] = saved_module
        if had_attr:
            pixel_forge.installer = saved_attr
        else:
            try:
                delattr(pixel_forge, "installer")
            except AttributeError:
                pass

    def test_install_failure_names_the_software_manager(self):
        launcher = _load_launcher()
        messages = []
        with mock.patch.object(launcher, "have_pyside6",
                               return_value=False), \
                mock.patch.object(launcher, "_has_network",
                                  return_value=True), \
                mock.patch.object(launcher, "ensure_pyside6",
                                  return_value=False), \
                mock.patch.object(launcher, "_show_error",
                                  side_effect=messages.append):
            self.assertEqual(launcher.main(), 1)
        self.assertEqual(len(messages), 1)
        self.assertIn("could not install it", messages[0])
        self.assertIn("Software Manager", messages[0])
        self.assertIn("python3-pyside6", messages[0])
        self.assertIn("bash install.sh", messages[0])

    def test_success_hands_off_to_the_installer(self):
        launcher = _load_launcher()
        _fake, calls = self._fake_installer()
        with mock.patch.object(launcher, "ensure_pyside6",
                               return_value=True), \
                mock.patch("time.sleep"):
            self.assertEqual(launcher.main(["--dry-run"]), 7)
        self.assertEqual(calls, [["--dry-run"]])

    def test_offline_with_qt_present_still_starts_the_installer(self):
        # The offline check only fires when Qt is missing: with
        # PySide6 importable, no download is needed, so main
        # proceeds even with no network.
        launcher = _load_launcher()
        _fake, calls = self._fake_installer()
        with mock.patch.object(launcher, "have_pyside6",
                               return_value=True), \
                mock.patch.object(launcher, "_has_network",
                                  return_value=False), \
                mock.patch("time.sleep"):
            self.assertEqual(launcher.main(), 7)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0])


if __name__ == "__main__":
    unittest.main()
