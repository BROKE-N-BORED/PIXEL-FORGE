#!/usr/bin/env python3
"""Pixel Forge GUI installer launcher (standard library only).

The installer window needs PySide6 (Qt). This launcher makes sure
it is importable first — installing it via pkexec + apt, or pip
--user as a fallback — then starts the installer window from
pixel_forge/installer.py. No terminal needed at any point: pkexec
shows a GUI password prompt, and progress shows in a zenity dialog
while the packages install (zenity ships with Linux Mint; if it is
missing, the install just runs quietly in the background).
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def have_pyside6():
    try:
        import PySide6  # noqa: F401
        return True
    except (ImportError, OSError):
        # ImportError: not installed, or a native library it needs
        # is missing. OSError: a shared library exists but cannot
        # be loaded (bad file, wrong architecture). Either way the
        # answer is the same — PySide6 is not usable (review B15).
        return False


def _zenity_progress(text):
    """A pulsating zenity progress dialog, or None if zenity is
    missing. Call .terminate() on the returned process when done."""
    if not shutil.which("zenity"):
        return None
    try:
        return subprocess.Popen(
            ["zenity", "--progress", "--pulsate", "--no-cancel",
             "--auto-close", "--title=Pixel Forge",
             "--text=%s" % text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
    except OSError:
        return None


def _run_quiet(cmd, timeout=900):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              timeout=timeout)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _install_pyside6_apt():
    """pkexec + apt. Returns True if PySide6 imports afterwards."""
    if not shutil.which("pkexec"):
        return False
    progress = _zenity_progress(
        "Installing the bits Pixel Forge needs (one moment)…")
    ok = _run_quiet(["pkexec", "bash", "-c",
                     "export DEBIAN_FRONTEND=noninteractive; "
                     "apt-get update && "
                     "apt-get install -y python3-pyside6"],
                    timeout=900)
    if progress is not None:
        progress.terminate()
        try:
            progress.wait(timeout=5)
        except subprocess.TimeoutExpired:
            progress.kill()
    return ok and have_pyside6()


def _install_pyside6_pip():
    """pip --user (no admin rights needed)."""
    if not _run_quiet([sys.executable, "-m", "pip", "--version"],
                      timeout=60):
        # No pip at all — try to get it, then retry once.
        if shutil.which("pkexec"):
            _run_quiet(["pkexec", "bash", "-c",
                        "export DEBIAN_FRONTEND=noninteractive; "
                        # Update first: on a stale cache the
                        # install below can fail for a package
                        # that is right there (review B15).
                        "apt-get update && "
                        "apt-get install -y python3-pip"],
                       timeout=600)
    ok = _run_quiet([sys.executable, "-m", "pip", "install", "--user",
                     "--break-system-packages", "PySide6-Essentials"],
                    timeout=900)
    return ok and have_pyside6()


def _show_error(message):
    if shutil.which("zenity"):
        _run_quiet(["zenity", "--error", "--title=Pixel Forge",
                    "--text=%s" % message], timeout=60)
    elif shutil.which("notify-send"):
        _run_quiet(["notify-send", "Pixel Forge", message], timeout=30)
    print(message, file=sys.stderr)


def ensure_pyside6():
    """Make sure PySide6 imports. Returns True on success."""
    if have_pyside6():
        return True
    if _install_pyside6_apt():
        return True
    if _install_pyside6_pip():
        return True
    return have_pyside6()


def main(argv=None):
    if not ensure_pyside6():
        _show_error(
            "Pixel Forge needs PySide6 (Qt) and could not install it.\n\n"
            "Install it from the Software Manager (search for "
            "python3-pyside6), then double-click Install Pixel Forge "
            "again — or run this in a terminal instead:\n"
            "  bash install.sh")
        return 1
    # Small delay so a zenity progress dialog has time to close
    # before the installer window appears.
    time.sleep(0.5)
    from pixel_forge import installer
    return installer.main(argv)


if __name__ == "__main__":
    sys.exit(main())
