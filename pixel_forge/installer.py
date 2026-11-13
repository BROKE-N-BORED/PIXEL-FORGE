#!/usr/bin/env python3
"""Pixel Forge GUI installer — native Qt window, no terminal needed.

Double-click "Install Pixel Forge" in the extracted folder. It checks
for an internet connection first (the install downloads packages), then
for python3-venv and pip, creates a venv under
~/.local/share/pixel-forge, pip-installs Pixel Forge + PySide6, and
writes the menu launcher, icons, and PATH shortcuts.

Warn-never-refuse, like install.sh: a failed step is logged and the
install carries on; the window says plainly what worked and what did
not. Safe to run again — it redoes the failed steps.

One exception: the shortcuts/menu-launcher step is skipped when the
packages step failed — writing a launcher then would point at a
broken install (a dangling symlink plus a Launch button that cannot
work). And Launch enables only when the installed venv's GUI
actually imports, never on the strength of a symlink.

Dry run (smoke tests only): PIXEL_FORGE_INSTALLER_DRYRUN=1 makes every
step log what it would do without running anything privileged or
writing anything. The shipped installer always does the real steps.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # pixel_forge/
SRC_DIR = HERE.parent                            # extracted folder root
ICON_PATH = SRC_DIR / "icons" / "pixel-forge-256.png"

DRYRUN_ENV = "PIXEL_FORGE_INSTALLER_DRYRUN"

from PySide6.QtCore import Qt, QThread, Signal  # noqa: E402
from PySide6.QtGui import QIcon, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from .pixelize import PixelForgeError, plural  # noqa: E402

NETWORK_CHECK_URL = "https://pypi.org/simple/"
NETWORK_CHECK_TIMEOUT = 10  # seconds — a dead connection must fail fast

# Where the image-engine source build lives (task 85), relative
# to the user's home: a persistent folder under the install home,
# NOT a temp dir. A freeze or failure mid-build leaves the
# checkout and the half-built objects here, and the next run
# resumes from them instead of re-cloning and rebuilding the
# whole ~30 minutes from scratch. It is removed after a
# successful install — the built engine lives in /opt/sd.cpp and
# the build files are multi-GB junk after that. install.sh uses
# the same path (SD_BUILD_ROOT). The uninstaller's --purge
# already removes the whole install home, so this goes with it.
SD_BUILD_SUBDIR = (Path(".local") / "share" / "pixel-forge"
                   / "sd-build")


def has_network(url=NETWORK_CHECK_URL, timeout=NETWORK_CHECK_TIMEOUT):
    """True when an HTTP request to url gets any response. An HTTP
    error status still counts — the server answered, so the machine
    is online. Only a failure to reach the host counts as offline.
    (install-pixel-forge.py carries its own copy: it cannot import
    this module, because PySide6 is not guaranteed yet when it runs.)"""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, method="HEAD")
    try:
        urllib.request.urlopen(request, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def venv_package_name(python=None):
    """The Debian venv package for the system Python:
    python3.<minor>-venv, from the interpreter's own version —
    never hardcoded. Falls back to the unversioned python3-venv
    when the version cannot be detected (no interpreter, or one
    that will not answer)."""
    exe = python or sys.executable
    try:
        proc = subprocess.run(
            [exe, "-c",
             "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True, timeout=30)
        major, minor = (int(part) for part in proc.stdout.split())
        return "python%d.%d-venv" % (major, minor)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "python3-venv"


# ---------------------------------------------------------------- context

class InstallContext:
    """Everything a step needs: logging, command running, dry-run
    stubs. All side effects go through here so a dry run can fake
    them without touching the machine."""

    def __init__(self, log, dryrun=False):
        self._log = log
        self.dryrun = dryrun
        self.cancelled = False
        # Set by the worker's up-front pkexec check: when it has
        # already said pkexec is missing, run_admin stays quiet
        # instead of repeating the message after every step.
        self.pkexec_warned = False
        # Nothing selected by default, ever: ffmpeg is an optional
        # install (needs sudo + a download), so a bare context does
        # not install it — the window passes the checkbox state in.
        self.install_ffmpeg = False
        self.install_image_engine = False
        self.home = Path(os.path.expanduser("~"))

    def log(self, message):
        self._log(message)

    def which(self, name):
        return shutil.which(name)

    # -- commands ---------------------------------------------------

    def run(self, cmd, timeout=None):
        """Run a command, streaming its output to the log.
        Returns the exit code (0 in dry-run)."""
        self.log("  $ %s" % " ".join(str(c) for c in cmd))
        if self.dryrun:
            return 0
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
        except OSError as exc:
            self.log("  !! could not start: %s" % exc)
            return 127
        try:
            for line in proc.stdout:
                self.log("  | " + line.rstrip("\n"))
        except Exception:  # noqa: BLE001 - stream hiccups are not fatal
            pass
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.log("  !! timed out")
            return 124

    def run_output(self, cmd, timeout=60):
        """Run a command, return (exit code, stdout). Dry-run
        returns (0, 'dry-run')."""
        self.log("  $ %s" % " ".join(str(c) for c in cmd))
        if self.dryrun:
            return 0, "dry-run"
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, str(exc)
        return proc.returncode, (proc.stdout or "").strip()

    def run_admin(self, script, timeout=None):
        """Run a shell script as admin via pkexec (GUI password
        prompt). Returns the exit code (0 in dry-run)."""
        self.log("  $ [admin] bash -c ...")
        for line in script.strip().splitlines():
            self.log("  |   %s" % line)
        if self.dryrun:
            return 0
        if not shutil.which("pkexec"):
            # Said once, not once per step: the worker's up-front
            # check covers this when it knew an admin step was
            # coming (review B15).
            if not self.pkexec_warned:
                self.log("  !! pkexec not found — cannot run admin "
                         "steps")
                self.pkexec_warned = True
            return 127
        return self.run(["pkexec", "bash", "-c", script],
                        timeout=timeout)

    # -- filesystem ---------------------------------------------------

    def mkdir(self, path):
        if self.dryrun:
            self.log("  would create %s" % path)
            return True
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            self.log("  !! could not create %s: %s" % (path, exc))
            return False

    def copy(self, src, dest):
        if self.dryrun:
            self.log("  would copy %s -> %s" % (src, dest))
            return True
        try:
            shutil.copy2(str(src), str(dest))
            return True
        except OSError as exc:
            self.log("  !! could not copy %s: %s" % (src, exc))
            return False

    def write_text(self, path, text):
        if self.dryrun:
            self.log("  would write %s" % path)
            return True
        try:
            Path(path).write_text(text, encoding="utf-8")
            return True
        except OSError as exc:
            self.log("  !! could not write %s: %s" % (path, exc))
            return False

    def link(self, target, link_path):
        """Symlink link_path -> target, replacing any existing link."""
        if self.dryrun:
            self.log("  would link %s -> %s" % (link_path, target))
            return True
        try:
            link = Path(link_path)
            if link.is_symlink() or link.exists():
                link.unlink()
            os.symlink(str(target), link)
            return True
        except OSError as exc:
            self.log("  !! could not link %s: %s" % (link_path, exc))
            return False


# ---------------------------------------------------------------- step results

class StepResult:
    DONE = "done"
    WARN = "warning"
    FAIL = "failed"
    # The step was deliberately not run (the files step, when the
    # packages step failed) — not a failure of the step itself.
    SKIPPED = "skipped"

    def __init__(self, status, detail=""):
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------- icons

def render_icons_from_svg(svg_path, out_dir):
    """Render pixel-forge-{256,128,48}.png from the SVG into out_dir.
    Used by the installer when the PNGs are missing (a git clone
    ships only the SVG — the GitHub snapshot push cannot carry
    binary files — while the release zip ships the PNGs too).
    Returns (True, detail) on success, or (False, reason) on
    failure — the caller logs the reason instead of swallowing it,
    because a failed render means a silently icon-less menu entry."""
    try:
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return False, "QtSvg is not available"
    try:
        renderer = QSvgRenderer(str(svg_path))
        if not renderer.isValid():
            return False, "pixel-forge.svg is not a valid SVG file"
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for size in (256, 128, 48):
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            out_path = out_dir / ("pixel-forge-%d.png" % size)
            if not image.save(str(out_path)):
                return False, "could not write %s" % out_path.name
        return True, "rendered 3 icons"
    except Exception as exc:  # noqa: BLE001 - report, never raise
        return False, str(exc)


# ---------------------------------------------------------------- the steps

def step_network(ctx):
    """Internet check — the install downloads packages (apt, pip,
    and the engine build + model when asked for), so an offline run
    stops here with a plain message instead of failing step by step
    with obscure apt/pip/curl errors. The worker skips the remaining
    steps when this one fails."""
    ctx.log("Checking for internet…")
    if ctx.dryrun:
        ctx.log("Would check for internet…")
        return StepResult(StepResult.DONE, "dry-run")
    if has_network():
        ctx.log("OK: internet is available")
        return StepResult(StepResult.DONE)
    needs = ["system packages (apt)", "Python packages (pip)"]
    if getattr(ctx, "install_ffmpeg", False):
        needs.append("ffmpeg")
    if getattr(ctx, "install_image_engine", False):
        needs += ["the image engine build", "the model download"]
    ctx.log("You need internet to install Pixel Forge — the install "
            "downloads %s. Check your connection and press Install "
            "again." % ", ".join(needs))
    return StepResult(StepResult.FAIL, "no internet")


def step_system(ctx):
    """python3-venv and pip — needed to build the venv."""
    ctx.log("Checking python3-venv and pip…")
    venv_rc, _ = ctx.run_output([sys.executable, "-m", "venv", "--help"])
    pip_rc, _ = ctx.run_output([sys.executable, "-m", "pip", "--version"])
    if venv_rc == 0 and pip_rc == 0:
        ctx.log("OK: python3-venv and pip are present")
        return StepResult(StepResult.DONE)
    # The venv package is versioned (python3.12-venv, python3.11-venv
    # ...) — ask the system Python its own version, never hardcode
    # one. Falls back to the unversioned python3-venv when the
    # version cannot be detected.
    venv_pkg = venv_package_name()
    if venv_pkg == "python3-venv":
        ctx.log("Could not detect the Python version — trying the "
                "unversioned python3-venv package.")
    ctx.log("python3-venv or pip is missing — trying to install them "
            "(asks for your password)…")
    code = ctx.run_admin(
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update && apt-get install -y %s python3-pip"
        % venv_pkg, timeout=900)
    venv_rc, _ = ctx.run_output([sys.executable, "-m", "venv", "--help"])
    pip_rc, _ = ctx.run_output([sys.executable, "-m", "pip", "--version"])
    if venv_rc == 0 and pip_rc == 0:
        ctx.log("OK: python3-venv and pip installed")
        return StepResult(StepResult.DONE)
    ctx.log("WARNING: python3-venv or pip is still missing (apt step "
            "exited %s) — the install cannot continue without them. "
            "Install them from the Software Manager (%s, "
            "python3-pip) and press Install again." % (code, venv_pkg))
    return StepResult(StepResult.FAIL, "python3-venv or pip missing")


def step_ffmpeg(ctx):
    """ffmpeg — optional, only needed for video import.

    Offered, not forced: the window has a checkbox for it. A failed
    install is a WARNING, never a failure — Pixel Forge still works
    for images without ffmpeg, and video import just says plainly
    what to install.
    """
    ctx.log("Checking ffmpeg (for video import)…")
    if ctx.which("ffmpeg"):
        ctx.log("OK: ffmpeg is already installed")
        return StepResult(StepResult.DONE, "already installed")
    if not getattr(ctx, "install_ffmpeg", False):
        ctx.log("Skipped: ffmpeg not installed (your choice) — video "
                "import will be unavailable until you install it "
                "(sudo apt install ffmpeg). Image import still works.")
        return StepResult(StepResult.DONE, "skipped")
    if ctx.dryrun:
        ctx.log("Would install ffmpeg (asks for your password)…")
        return StepResult(StepResult.DONE, "dry-run")
    ctx.log("ffmpeg is missing — trying to install it (asks for your "
            "password)…")
    code = ctx.run_admin(
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update && apt-get install -y ffmpeg",
        timeout=900)
    if ctx.which("ffmpeg"):
        ctx.log("OK: ffmpeg installed")
        return StepResult(StepResult.DONE)
    ctx.log("WARNING: ffmpeg could not be installed (apt step exited "
            "%s) — video import will be unavailable until you install "
            "it (sudo apt install ffmpeg). Image import still works."
            % code)
    return StepResult(StepResult.WARN,
                      "ffmpeg missing — video import unavailable")


def _ensure_engine_checkout(ctx, src):
    """Make sure `src` holds the engine at the pinned release.
    Returns True on success, False when the checkout could not be
    had (the caller warns and stops).

    An existing checkout at the pin is reused as-is — no network
    needed, which is what makes a re-run after a freeze cheap.
    A checkout at another commit is fetched and checked out to
    the pin; a damaged checkout (git cannot answer for it) or a
    directory with no .git (an interrupted clone) is wiped and
    cloned again. A failed update falls back to a fresh clone —
    the checkout is never left half-moved."""
    from .imagegen import (ENGINE_RELEASE, engine_checkout_tag_command,
                           engine_clone_command, engine_update_commands)

    if src.is_dir() and (src / ".git").is_dir():
        code, tag = ctx.run_output(engine_checkout_tag_command(src))
        if code == 0 and tag == ENGINE_RELEASE:
            ctx.log("Resuming from the engine checkout in %s "
                    "(already at %s)…" % (src, ENGINE_RELEASE))
            return True
        if code == 0:
            ctx.log("The engine checkout is at %s, not %s — "
                    "updating it…" % (tag or "an unknown commit",
                                      ENGINE_RELEASE))
        else:
            ctx.log("The engine checkout in %s is damaged — "
                    "checking it out again…" % src)
        updated = all(ctx.run(cmd, timeout=900) == 0
                      for cmd in engine_update_commands(src))
        if updated:
            return True
        ctx.log("Could not update the checkout — checking it out "
                "again from scratch…")
        shutil.rmtree(src, ignore_errors=True)
    elif src.exists():
        # A directory with no .git: an interrupted clone left it
        # behind. It is not a checkout — remove it, then clone.
        ctx.log("Removing the unfinished engine checkout in %s…"
                % src)
        shutil.rmtree(src, ignore_errors=True)
    code = ctx.run(engine_clone_command(src), timeout=900)
    if code != 0:
        ctx.log("WARNING: could not check out the image engine "
                "at %s (git clone exit %s) — prompt import will "
                "stay unavailable. Try ./install.sh "
                "--with-image-engine in a terminal to see the git "
                "output." % (ENGINE_RELEASE, code))
        return False
    return True


def _configure_and_build(ctx, src, build_dir, jobs, configure):
    """One configure (when asked) + build pass. Returns the exit
    code of the step that ran last — 0 when the build landed."""
    if configure:
        code = ctx.run(
            ["cmake", "-S", str(src), "-B", str(build_dir),
             "-DCMAKE_BUILD_TYPE=Release"], timeout=600)
        if code != 0:
            return code
    return ctx.run(
        ["cmake", "--build", str(build_dir),
         "-j%d" % jobs], timeout=3600)


def _build_engine(ctx, src, build_dir, jobs):
    """Configure + build the engine in `build_dir`, resuming an
    interrupted build when one is there. Returns True when the
    build landed, False when it failed (the caller warns).

    A build folder with a CMakeCache.txt is a configured build:
    cmake --build reuses the objects already compiled, so a
    freeze at 92% costs the remaining 8%, not the whole half
    hour. A build folder with no cache never finished its
    configure — it is wiped and configured fresh. When a resumed
    build fails, the leftover build state may itself be broken,
    so the folder is wiped and the build is tried once from
    clean before giving up. A failed build never installs
    anything: the install step runs only after this returns True,
    and the build folder is left in place for the next run."""
    resumed = (build_dir / "CMakeCache.txt").is_file()
    if build_dir.is_dir() and not resumed:
        ctx.log("The engine build folder has no finished setup — "
                "clearing it and starting the build fresh…")
        shutil.rmtree(build_dir, ignore_errors=True)
    if resumed:
        ctx.log("Resuming the engine build in %s…" % build_dir)
    code = _configure_and_build(ctx, src, build_dir, jobs,
                                configure=not resumed)
    if code == 0:
        return True
    if resumed:
        ctx.log("The resumed build failed — clearing the build "
                "folder and rebuilding once from clean…")
        shutil.rmtree(build_dir, ignore_errors=True)
        code = _configure_and_build(ctx, src, build_dir, jobs,
                                    configure=True)
        if code == 0:
            return True
    ctx.log("WARNING: the image engine build failed — prompt "
            "import will stay unavailable. Try ./install.sh "
            "--with-image-engine in a terminal to see the build "
            "output.")
    return False


def step_image_engine(ctx):
    """Image engine + model — optional, only needed for prompt
    import.

    Offered, not forced: the window has a checkbox for it, OFF by
    default — this is a source build (tens of minutes) plus a 2.1 GB
    model download. A failed step is a WARNING, never a failure:
    Pixel Forge still works for images and video without the engine,
    and prompt import just says plainly what to install.

    The engine is checked out at the pinned ENGINE_RELEASE, never
    master HEAD, and the model download is SHA-256-checked against
    MODEL_SHA256 (both from imagegen — the one source of truth). A
    download that fails the check is deleted, never kept; a curl
    failure leaves any partial file in place, so the next run
    resumes it instead of starting over — and a partial file is
    never mistaken for the finished model (is_partial_model
    checks the pinned size), so an interrupted download always
    re-runs instead of passing as installed.

    Resumable (task 85): the checkout and the build live in a
    persistent folder under ~/.local/share/pixel-forge/sd-build,
    so a freeze or failure mid-build resumes on the next run
    instead of starting the ~30-minute build over. The build is
    removed after a successful install. The build also runs with
    a capped job count on low-memory machines (task 84), so the
    desktop stays usable while it compiles.
    """
    from .imagegen import (MODEL_FILENAME, MODEL_SIZE_BYTES,
                           available_ram_bytes, build_jobs,
                           check_model_file, find_engine,
                           find_model, format_bytes,
                           is_partial_model, model_download_command)

    ctx.log("Checking the image engine (for prompt import)…")
    engine = find_engine()
    model = find_model()
    if model is not None and is_partial_model(model):
        # An interrupted download: the file is there but short.
        # Treat it as missing so the download below resumes it
        # (curl -C -) instead of the step calling it installed.
        try:
            have = format_bytes(model.stat().st_size)
        except OSError:
            have = "some"
        ctx.log("The model download was interrupted (%s of %s "
                "downloaded) — resuming it instead of starting "
                "over…" % (have, format_bytes(MODEL_SIZE_BYTES)))
        model = None
    if engine is not None and model is not None:
        ctx.log("OK: image engine and model are already installed")
        return StepResult(StepResult.DONE, "already installed")
    if not getattr(ctx, "install_image_engine", False):
        ctx.log("Skipped: image engine not installed (your choice) — "
                "prompt import will be unavailable until you install "
                "it (tick the box and press Install again, or run "
                "./install.sh --with-image-engine). Image and video "
                "import still work.")
        return StepResult(StepResult.DONE, "skipped")
    if ctx.dryrun:
        ctx.log("Would build the image engine from source and "
                "download the model (about 2.1 GB)…")
        return StepResult(StepResult.DONE, "dry-run")
    # Build tools first: the source build needs git, cmake, g++.
    missing = [t for t in ("git", "cmake", "g++") if not ctx.which(t)]
    if missing:
        ctx.log("Build tools missing (%s) — trying to install them "
                "(asks for your password)…" % ", ".join(missing))
        ctx.run_admin(
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update && apt-get install -y git cmake "
            "build-essential", timeout=900)
        missing = [t for t in ("git", "cmake", "g++")
                   if not ctx.which(t)]
        if missing:
            ctx.log("WARNING: build tools still missing (%s) — the "
                    "image engine cannot be built. Install them "
                    "with: sudo apt install git cmake "
                    "build-essential, then press Install again."
                    % ", ".join(missing))
            return StepResult(StepResult.WARN,
                              "build tools missing — prompt import "
                              "unavailable")
    if engine is None:
        # The prebuilt Linux binary needs glibc 2.43 (Ubuntu 26.04);
        # Mint ships 2.39, so the engine is built from source here.
        # The clone MUST be --recursive: ggml is a submodule, and a
        # non-recursive clone fails the build. It is pinned to
        # ENGINE_RELEASE: a plain clone fetches whatever master
        # HEAD happens to be that day.
        ctx.log("Building the image engine from source (one-time "
                "compile — allow up to half an hour)…")
        # The checkout + build live in a persistent folder (task
        # 85), not a temp dir: a freeze or failure mid-build
        # resumes from here on the next run. The job count is
        # capped on low-memory machines (task 84) so the desktop
        # stays usable while the build runs.
        build_root = ctx.home / SD_BUILD_SUBDIR
        src = build_root / "sd"
        build_dir = src / "build"
        available = available_ram_bytes()
        jobs = build_jobs(available=available)
        cpus = os.cpu_count() or 1
        if available is not None and jobs < cpus:
            ctx.log("Building with %d parallel jobs (%s available "
                    "memory — capped so the desktop stays usable)…"
                    % (jobs, format_bytes(available)))
        else:
            ctx.log("Building with %d parallel jobs…" % jobs)
        if not _ensure_engine_checkout(ctx, src):
            return StepResult(StepResult.WARN,
                              "engine checkout failed — prompt "
                              "import unavailable")
        if not _build_engine(ctx, src, build_dir, jobs):
            return StepResult(StepResult.WARN,
                              "engine build failed — prompt "
                              "import unavailable")
        # The old engine is moved aside first, never wiped: a
        # failed build must not destroy the working engine, and
        # the move happens only now, after the new build
        # succeeded. `set -e` stops the script if the backup
        # move fails, so the old engine stays in place.
        if Path("/opt/sd.cpp").is_dir():
            ctx.log("Backing up the existing image engine to "
                    "/opt/sd.cpp.bak…")
        code = ctx.run_admin(
            "set -e; "
            "if [ -d /opt/sd.cpp ]; then "
            "rm -rf /opt/sd.cpp.bak; "
            "mv /opt/sd.cpp /opt/sd.cpp.bak; "
            "echo 'Backed up the old engine to /opt/sd.cpp.bak'; "
            "fi; "
            "mkdir -p /opt/sd.cpp/bin; "
            "cp %s /opt/sd.cpp/bin/; "
            "ln -sf /opt/sd.cpp/bin/sd-cli /usr/local/bin/sd-cli"
            % os.path.join(build_dir, "bin", "*"), timeout=300)
        engine = find_engine()
        if code != 0 or engine is None:
            ctx.log("WARNING: the built engine could not be "
                    "installed to /opt/sd.cpp — prompt import "
                    "will stay unavailable.")
            return StepResult(StepResult.WARN,
                              "engine install failed — prompt "
                              "import unavailable")
        ctx.log("OK: image engine built: %s" % engine)
        # The engine is installed — the build files are multi-GB
        # junk now. Remove the checkout and the object files; the
        # installed engine stays in /opt/sd.cpp, and the model
        # (downloaded below) stays in its own home.
        shutil.rmtree(build_root, ignore_errors=True)
        ctx.log("Removed the engine build files from %s (checkout "
                "and build objects gone; the installed engine "
                "stays in /opt/sd.cpp)." % build_root)
    if model is None:
        # Shared with the Design Shell: one download serves both.
        ctx.log("Downloading the image model (%s, about 2.1 GB)…"
                % MODEL_FILENAME)
        model_home = ctx.home / "design-shell-models"
        ctx.mkdir(model_home)
        model_path = model_home / MODEL_FILENAME
        code = ctx.run(model_download_command(model_path),
                       timeout=3600)
        if code != 0 or not model_path.is_file():
            # A failed download leaves any partial file in place:
            # the next run resumes it (-C -) instead of starting
            # over. Only a completed-but-wrong file is deleted,
            # below.
            # Both homes: find_model checks the shared home first
            # and Pixel Forge's own home second, so a model in
            # either one makes prompt import work (review B13 —
            # the old line named only the shared home).
            ctx.log("WARNING: the model download failed (exit %s) — "
                    "prompt import will stay unavailable until the "
                    "model is in ~/design-shell-models/ or "
                    "~/pixel-forge-models/." % code)
            return StepResult(StepResult.WARN,
                              "model download failed — prompt import "
                              "unavailable")
        # The download landed — prove it is the pinned model before
        # accepting it. A mismatched file is deleted, never kept:
        # a partial or corrupt download must not pass as the model.
        try:
            check_model_file(model_path)
        except PixelForgeError as exc:
            try:
                model_path.unlink()
            except OSError:
                pass
            ctx.log("WARNING: %s — the bad file was removed, so "
                    "prompt import will stay unavailable until the "
                    "model downloads cleanly." % exc)
            return StepResult(StepResult.WARN,
                              "model checksum failed — prompt import "
                              "unavailable")
        ctx.log("OK: image model downloaded")
    return StepResult(StepResult.DONE)


def step_venv(ctx):
    """Virtual environment under ~/.local/share/pixel-forge/venv."""
    ctx.log("Setting up the virtual environment…")
    venv = ctx.home / ".local" / "share" / "pixel-forge" / "venv"
    if not ctx.dryrun and (venv / "bin" / "python").is_file():
        ctx.log("OK: virtual environment already exists at %s" % venv)
        return StepResult(StepResult.DONE, "already exists")
    code = ctx.run([sys.executable, "-m", "venv", str(venv)],
                   timeout=300)
    if code == 0:
        ctx.log("OK: virtual environment created at %s" % venv)
        return StepResult(StepResult.DONE)
    ctx.log("WARNING: could not create the virtual environment")
    return StepResult(StepResult.FAIL, "venv creation failed")


def step_packages(ctx):
    """Pixel Forge + PySide6 into the venv (needs internet)."""
    ctx.log("Installing Pixel Forge and PySide6…")
    venv_python = (ctx.home / ".local" / "share" / "pixel-forge"
                   / "venv" / "bin" / "python")
    if not ctx.dryrun and not venv_python.is_file():
        ctx.log("WARNING: no venv python at %s — the venv step must "
                "have failed" % venv_python)
        return StepResult(StepResult.FAIL, "no venv to install into")
    ctx.run([str(venv_python), "-m", "pip", "install", "--quiet",
             "--upgrade", "pip"], timeout=300)
    code = ctx.run([str(venv_python), "-m", "pip", "install", "--quiet",
                    str(SRC_DIR)], timeout=900)
    rc, _ = ctx.run_output([str(venv_python), "-c",
                            "import pixel_forge, PySide6"])
    if code == 0 and rc == 0:
        ctx.log("OK: Pixel Forge installed in the venv")
        return StepResult(StepResult.DONE)
    ctx.log("WARNING: the pip install failed — Pixel Forge is not "
            "installed yet. The download may have been interrupted, "
            "or the package index may be unreachable. Check your "
            "connection and press Install again.")
    return StepResult(StepResult.FAIL, "pip install failed")


def step_files(ctx):
    """PATH shortcuts, icons, menu launcher."""
    ctx.log("Installing shortcuts, icons and the menu launcher…")
    prefix = ctx.home / ".local" / "share" / "pixel-forge"
    venv_bin = prefix / "venv" / "bin"
    bin_dir = ctx.home / ".local" / "bin"
    ctx.mkdir(bin_dir)
    ok = True
    warnings = []
    ok = ctx.link(venv_bin / "pixel-forge",
                  bin_dir / "pixel-forge") and ok
    ok = ctx.link(venv_bin / "pixel-forge-gui",
                  bin_dir / "pixel-forge-gui") and ok

    icons_src = SRC_DIR / "icons"
    if not ctx.dryrun and not (icons_src / "pixel-forge-256.png").is_file():
        svg = icons_src / "pixel-forge.svg"
        if svg.is_file():
            rendered, detail = render_icons_from_svg(svg, icons_src)
            if rendered:
                ctx.log("OK: App icons rendered from pixel-forge.svg")
            else:
                ctx.log("WARNING: could not render app icons from "
                        "the SVG (%s) — the menu entry will show a "
                        "generic icon" % detail)
                warnings.append("app icons could not be rendered "
                                "(%s)" % detail)
        else:
            ctx.log("WARNING: no app icons found (no PNGs, no SVG) "
                    "— the menu entry will show a generic icon")
            warnings.append("app icons missing")
    for size in (48, 128, 256):
        dest = (ctx.home / ".local" / "share" / "icons" / "hicolor"
                / ("%dx%d" % (size, size)) / "apps")
        ctx.mkdir(dest)
        ok = ctx.copy(icons_src / ("pixel-forge-%d.png" % size),
                      dest / "pixel-forge.png") and ok
    scalable = (ctx.home / ".local" / "share" / "icons" / "hicolor"
                / "scalable" / "apps")
    ctx.mkdir(scalable)
    ok = ctx.copy(icons_src / "pixel-forge.svg",
                  scalable / "pixel-forge.svg") and ok
    if ctx.which("gtk-update-icon-cache"):
        code = ctx.run(["gtk-update-icon-cache", "-f", "-t",
                        str(ctx.home / ".local" / "share" / "icons"
                            / "hicolor")])
        if code != 0:
            ctx.log("WARNING: gtk-update-icon-cache failed (exit %s) "
                    "— the menu icon may not update until you log "
                    "out" % code)
            warnings.append("icon cache update failed")

    apps_dir = ctx.home / ".local" / "share" / "applications"
    ctx.mkdir(apps_dir)
    launcher = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Pixel Forge\n"
        "Comment=Turn images and videos into pixel art and sprites\n"
        "Exec=%s/pixel-forge-gui\n" % bin_dir +
        "Icon=pixel-forge\n"
        "Terminal=false\n"
        "Categories=Graphics;\n"
        "StartupNotify=true\n"
    )
    ok = ctx.write_text(apps_dir / "pixel-forge.desktop", launcher) and ok
    if ctx.which("update-desktop-database"):
        code = ctx.run(["update-desktop-database", str(apps_dir)])
        if code != 0:
            ctx.log("WARNING: update-desktop-database failed "
                    "(exit %s) — the menu entry may not appear until "
                    "you log out" % code)
            warnings.append("desktop database update failed")
    if not ok:
        return StepResult(StepResult.WARN,
                          "some shortcuts or icons could not be written")
    if warnings:
        # The shortcuts and the menu entry are in place, so the
        # install works — but the icons or the menu refresh did not,
        # and the step status should say so instead of reading done.
        return StepResult(StepResult.WARN, "; ".join(warnings))
    ctx.log("OK: Pixel Forge is in your app menu; commands "
            "pixel-forge and pixel-forge-gui are on your PATH")
    return StepResult(StepResult.DONE)


def venv_gui_imports(home):
    """True when the venv installed under home has a GUI that imports.

    Runs the venv's own Python with `import pixel_forge.gui` — the
    module the menu launcher runs. A missing venv, a missing venv
    Python, a failed import, or a hang all count as False. This is
    what the Launch button trusts: never a symlink, which stays
    "present" when it dangles.

    The check runs from a fresh empty directory: `python -c`
    puts the working directory first on sys.path, and the
    installer runs from the extracted tree — which contains a
    pixel_forge package — so without this the import could pass
    from the installer's own folder even on a venv whose pip
    install failed.
    """
    venv_python = (Path(home) / ".local" / "share" / "pixel-forge"
                   / "venv" / "bin" / "python")
    if not venv_python.is_file():
        return False
    try:
        with tempfile.TemporaryDirectory(
                prefix="pf-import-check-") as tmpd:
            proc = subprocess.run(
                [str(venv_python), "-c", "import pixel_forge.gui"],
                capture_output=True, text=True, timeout=120, cwd=tmpd)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


STEPS = [
    ("network", "Internet connection", step_network),
    ("system", "System packages (python3-venv and pip)", step_system),
    ("ffmpeg", "ffmpeg for video import (optional)", step_ffmpeg),
    ("image-engine", "Image engine for prompt import (optional)",
     step_image_engine),
    ("venv", "Virtual environment", step_venv),
    ("packages", "Pixel Forge + PySide6 (pip install)", step_packages),
    ("files", "Shortcuts, icons and menu launcher", step_files),
]


def _admin_needed_steps(ctx):
    """Step keys that will need pkexec, in step order. Mirrors
    the steps' own conditions: system when venv or pip is
    missing, ffmpeg when requested and missing, image-engine
    when requested and (build tools missing or the engine
    itself missing — the built engine installs to /opt)."""
    from .imagegen import find_engine

    needed = []
    venv_rc, _ = ctx.run_output([sys.executable, "-m", "venv", "--help"])
    pip_rc, _ = ctx.run_output([sys.executable, "-m", "pip", "--version"])
    if venv_rc != 0 or pip_rc != 0:
        needed.append("system")
    if getattr(ctx, "install_ffmpeg", False) and not ctx.which("ffmpeg"):
        needed.append("ffmpeg")
    if getattr(ctx, "install_image_engine", False):
        tools_missing = any(not ctx.which(t)
                            for t in ("git", "cmake", "g++"))
        if tools_missing or find_engine() is None:
            needed.append("image-engine")
    return needed


# ---------------------------------------------------------------- worker

class InstallWorker(QThread):
    log_line = Signal(str)
    step_status = Signal(int, str)
    progress = Signal(int)
    finished = Signal(object)  # list of StepResult

    def __init__(self, dryrun=False, install_ffmpeg=False,
                 install_image_engine=False, parent=None):
        super().__init__(parent)
        self._dryrun = dryrun
        self._install_ffmpeg = install_ffmpeg
        self._install_image_engine = install_image_engine
        self._ctx = None

    def cancel(self):
        if self._ctx is not None:
            self._ctx.cancelled = True

    def run(self):
        ctx = InstallContext(self.log_line.emit, dryrun=self._dryrun)
        ctx.install_ffmpeg = self._install_ffmpeg
        ctx.install_image_engine = self._install_image_engine
        self._ctx = ctx
        results = []
        total = len(STEPS)
        packages_failed = False
        network_failed = False
        # pkexec up front (review B15): when the steps that need
        # your password cannot run, say so once before any step
        # fails — not once per step, after each one has already
        # failed. When the system step needs admin, the install
        # cannot continue without venv/pip, so it fails fast
        # here; when only optional steps need it, the loop below
        # runs and they warn as designed, with run_admin staying
        # quiet about pkexec (ctx.pkexec_warned).
        if not ctx.dryrun:
            admin_steps = _admin_needed_steps(ctx)
            if admin_steps and not shutil.which("pkexec"):
                ctx.log("pkexec is not installed, so the steps "
                        "that need your password cannot run (%s). "
                        "Install it with sudo apt install pkexec, "
                        "or install the missing pieces by hand, "
                        "then press Install again."
                        % ", ".join(admin_steps))
                ctx.pkexec_warned = True
                if "system" in admin_steps:
                    for index, (key, _title, _func) in enumerate(
                            STEPS):
                        if key == "system":
                            result = StepResult(
                                StepResult.FAIL,
                                "pkexec missing — python3-venv or "
                                "pip cannot be installed")
                        else:
                            result = StepResult(
                                StepResult.SKIPPED, "pkexec missing")
                        results.append(result)
                        self.step_status.emit(index, result.status)
                        self.progress.emit(
                            int((index + 1) * 100 / total))
                    self.finished.emit(results)
                    return
        for index, (key, title, func) in enumerate(STEPS):
            if ctx.cancelled:
                ctx.log("Cancelled — stopping after %d of %d steps"
                        % (index, total))
                break
            if network_failed:
                # The network step failed: every remaining step
                # downloads something (apt, pip, the engine build,
                # the model), so they would all fail one after
                # another. Stop here instead — the network step's
                # own message says what to do.
                result = StepResult(StepResult.SKIPPED, "no internet")
                results.append(result)
                self.step_status.emit(index, result.status)
                self.progress.emit(int((index + 1) * 100 / total))
                continue
            if key == "files" and packages_failed:
                # No launcher on a broken install: step_files would
                # symlink pixel-forge-gui at a venv whose pip install
                # failed, leaving a dangling launcher and a Launch
                # button that pops "Launch failed" (W5). Skip it and
                # say so plainly; a re-run writes the launcher once
                # the packages step passes.
                ctx.log("")
                ctx.log("=== Step %d of %d: %s ===" % (index + 1, total,
                                                       title))
                ctx.log("Skipped: the packages step failed, so no "
                        "shortcuts or menu launcher are written — a "
                        "launcher would point at a broken install. "
                        "Press Install again once the packages step "
                        "passes.")
                result = StepResult(StepResult.SKIPPED,
                                    "packages step failed")
                results.append(result)
                self.step_status.emit(index, result.status)
                self.progress.emit(int((index + 1) * 100 / total))
                continue
            self.step_status.emit(index, "running…")
            ctx.log("")
            ctx.log("=== Step %d of %d: %s ===" % (index + 1, total,
                                                   title))
            try:
                result = func(ctx)
            except Exception as exc:  # noqa: BLE001 - warn, carry on
                ctx.log("WARNING: step failed with an error: %s" % exc)
                result = StepResult(StepResult.FAIL, str(exc))
            if key == "packages" and result.status == StepResult.FAIL:
                packages_failed = True
            if key == "network" and result.status == StepResult.FAIL:
                network_failed = True
                ctx.log("Stopping here — the remaining steps need an "
                        "internet connection.")
            results.append(result)
            self.step_status.emit(index, result.status)
            self.progress.emit(int((index + 1) * 100 / total))
        self.finished.emit(results)


# ---------------------------------------------------------------- window

STATUS_LABEL = {
    "pending": "pending",
    "running…": "running…",
    StepResult.DONE: "done",
    StepResult.WARN: "warning",
    StepResult.FAIL: "failed",
    StepResult.SKIPPED: "skipped",
}


class InstallerWindow(QMainWindow):
    def __init__(self, dryrun=False):
        super().__init__()
        self._dryrun = dryrun
        self._worker = None
        self._results = None
        self._installing = False
        self.setWindowTitle("Install Pixel Forge")
        self.resize(720, 560)
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        root = QWidget()
        layout = QVBoxLayout(root)

        header = QHBoxLayout()
        if ICON_PATH.is_file():
            icon_label = QLabel()
            icon_label.setPixmap(QPixmap(str(ICON_PATH)).scaled(
                64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            header.addWidget(icon_label)
        title = QLabel(
            "<b>Install Pixel Forge</b><br>"
            "Turn images and videos into pixel art and sprites — the "
            "installer sets up a private Python environment and puts "
            "Pixel Forge in your app menu.<br>"
            "This takes a few minutes and needs internet for the "
            "one-time download. Safe to run again.")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        layout.addLayout(header)

        self.ffmpeg_check = QCheckBox(
            "Install ffmpeg for video support (needs your password)")
        # Nothing selected by default, ever: unticked until the
        # user ticks it, same as the image-engine checkbox below.
        self.ffmpeg_check.setChecked(False)
        self.ffmpeg_check.setToolTip(
            "ffmpeg reads video files. Without it, image import "
            "still works and video import says what to install.")
        layout.addWidget(self.ffmpeg_check)

        self.image_engine_check = QCheckBox(
            "Install the image engine for prompt support (source "
            "build plus a 2.1 GB model download — takes a while)")
        self.image_engine_check.setChecked(False)
        self.image_engine_check.setToolTip(
            "Builds stable-diffusion.cpp from source and downloads "
            "the LCM model. Without it, image and video import "
            "still work and prompt import says what to install.")
        layout.addWidget(self.image_engine_check)

        self.steps_list = QListWidget()
        self.steps_list.setMaximumHeight(140)
        for _key, title_text, _func in STEPS:
            item = QListWidgetItem("%s  —  pending" % title_text)
            self.steps_list.addItem(item)
        layout.addWidget(self.steps_list)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        # Cap the log: a 30-minute cmake build appends tens of
        # thousands of lines, slowing the window and growing
        # memory (review B15). 5000 lines keeps the recent
        # history readable; older lines scroll off the top.
        self.log_view.setMaximumBlockCount(5000)
        layout.addWidget(self.log_view, 1)

        row = QHBoxLayout()
        self.install_btn = QPushButton("Install")
        self.install_btn.clicked.connect(self._start_install)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_install)
        self.cancel_btn.setEnabled(False)
        self.launch_btn = QPushButton("Launch Pixel Forge")
        self.launch_btn.clicked.connect(self._launch_pixel_forge)
        self.launch_btn.setEnabled(False)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        for btn in (self.install_btn, self.cancel_btn,
                    self.launch_btn, self.close_btn):
            row.addWidget(btn)
        row.addStretch()
        layout.addLayout(row)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Ready — press Install when "
                                     "you are ready.")
        self._log("Pixel Forge installer. Press Install to begin.")
        if dryrun:
            self._log("DRY RUN — nothing will be installed or changed.")

    # -- logging ------------------------------------------------------

    def _log(self, message):
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    # -- install flow ---------------------------------------------------

    def _start_install(self):
        if self._installing:
            return
        self._installing = True
        self._results = None
        self.install_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.launch_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        for index in range(self.steps_list.count()):
            _key, title, _func = STEPS[index]
            self.steps_list.item(index).setText("%s  —  pending"
                                               % title)
        self.statusBar().showMessage("Installing…")
        self._worker = InstallWorker(
            dryrun=self._dryrun,
            install_ffmpeg=self.ffmpeg_check.isChecked(),
            install_image_engine=self.image_engine_check.isChecked(),
            parent=self)
        self._worker.log_line.connect(self._log)
        self._worker.step_status.connect(self._on_step_status)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel_install(self):
        if self._worker is not None:
            self._worker.cancel()
            self.statusBar().showMessage(
                "Cancelling after the current step…")
            self.cancel_btn.setEnabled(False)

    def _on_step_status(self, index, status):
        _key, title, _func = STEPS[index]
        label = STATUS_LABEL.get(status, status)
        self.steps_list.item(index).setText("%s  —  %s" % (title, label))

    def _on_finished(self, results):
        self._installing = False
        self._worker = None
        self._results = results
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self.install_btn.setEnabled(True)
        failed = [r for r in results if r.status == StepResult.FAIL]
        warned = [r for r in results if r.status == StepResult.WARN]
        self.progress_bar.setValue(100)
        # Launch trusts only a real import check: the venv's own
        # Python importing pixel_forge.gui. A symlink check is not
        # enough — a dangling symlink passes is_symlink() (W5). In
        # dry-run nothing was installed, so there is no venv to
        # check; Launch follows the step results instead (dry-run
        # is a smoke test of the window flow, not of an install).
        if self._dryrun:
            can_launch = not failed
        else:
            can_launch = venv_gui_imports(os.path.expanduser("~"))
        if not failed:
            if can_launch:
                if warned:
                    self.statusBar().showMessage(
                        "Installed with %s — see the log"
                        % plural(len(warned), "warning"))
                else:
                    self.statusBar().showMessage("Installed!")
                self.launch_btn.setEnabled(True)
                self._log("")
                self._log("Done! Open Pixel Forge from your app menu, or "
                          "run pixel-forge-gui in a terminal.")
                self._log("Command line: pixel-forge convert photo.png "
                          "--size 32 --colors 16")
            else:
                self.statusBar().showMessage(
                    "Installed, but Pixel Forge does not start — "
                    "see the log")
                self._log("")
                self._log("Every step passed, but the installed GUI "
                          "does not import — Launch is unavailable. "
                          "Press Install again to redo the install.")
        else:
            self.statusBar().showMessage(
                "Finished with %s — see the log"
                % plural(len(failed), "failed step"))
            self._log("")
            self._log("Some steps failed. You can press Install again "
                      "— it redoes the failed steps and leaves the "
                      "rest alone.")
            if can_launch:
                self.launch_btn.setEnabled(True)
                self._log("A previous install still works — Launch "
                          "is available.")
            else:
                self._log("Launch is unavailable: the installed GUI "
                          "does not import yet.")

    # -- launch ---------------------------------------------------------

    def _launch_pixel_forge(self):
        gui = (Path(os.path.expanduser("~")) / ".local" / "bin"
               / "pixel-forge-gui")
        if not (gui.is_file() or gui.is_symlink()):
            QMessageBox.warning(
                self, "Pixel Forge is not installed yet",
                "The launcher was not found — the install may "
                "not have finished. Press Install and try again.")
            return
        try:
            subprocess.Popen([str(gui)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True)
            self.statusBar().showMessage("Pixel Forge launched")
        except OSError as exc:
            QMessageBox.warning(self, "Launch failed", str(exc))

    # -- close guard ----------------------------------------------------

    def closeEvent(self, event):
        if self._installing:
            self.statusBar().showMessage(
                "Install in progress — press Cancel to stop after "
                "the current step.")
            event.ignore()
            return
        event.accept()


def main(argv=None):
    app = QApplication(sys.argv if argv is None else argv)
    dryrun = bool(os.environ.get(DRYRUN_ENV))
    window = InstallerWindow(dryrun=dryrun)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
