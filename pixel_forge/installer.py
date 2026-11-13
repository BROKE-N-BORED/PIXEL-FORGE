#!/usr/bin/env python3
"""Pixel Forge GUI installer — native Qt window, no terminal needed.

Double-click "Install Pixel Forge" in the extracted folder. It checks
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
    Returns True on success."""
    try:
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return False
    try:
        renderer = QSvgRenderer(str(svg_path))
        if not renderer.isValid():
            return False
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for size in (256, 128, 48):
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            if not image.save(str(out_dir / ("pixel-forge-%d.png" % size))):
                return False
        return True
    except Exception:  # noqa: BLE001 - caller logs a warning
        return False


# ---------------------------------------------------------------- the steps

def step_system(ctx):
    """python3-venv and pip — needed to build the venv."""
    ctx.log("Checking python3-venv and pip…")
    venv_rc, _ = ctx.run_output([sys.executable, "-m", "venv", "--help"])
    pip_rc, _ = ctx.run_output([sys.executable, "-m", "pip", "--version"])
    if venv_rc == 0 and pip_rc == 0:
        ctx.log("OK: python3-venv and pip are present")
        return StepResult(StepResult.DONE)
    ctx.log("python3-venv or pip is missing — trying to install them "
            "(asks for your password)…")
    code = ctx.run_admin(
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update && apt-get install -y python3.12-venv python3-pip",
        timeout=900)
    venv_rc, _ = ctx.run_output([sys.executable, "-m", "venv", "--help"])
    pip_rc, _ = ctx.run_output([sys.executable, "-m", "pip", "--version"])
    if venv_rc == 0 and pip_rc == 0:
        ctx.log("OK: python3-venv and pip installed")
        return StepResult(StepResult.DONE)
    ctx.log("WARNING: python3-venv or pip is still missing (apt step "
            "exited %s) — the install cannot continue without them. "
            "Install them from the Software Manager (python3.12-venv, "
            "python3-pip) and press Install again." % code)
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
    resumes it instead of starting over.
    """
    from .imagegen import (ENGINE_RELEASE, MODEL_FILENAME,
                           check_model_file, engine_clone_command,
                           find_engine, find_model,
                           model_download_command)

    ctx.log("Checking the image engine (for prompt import)…")
    engine = find_engine()
    model = find_model()
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
        tmpd = tempfile.mkdtemp(prefix="pixel-forge-sd-")
        try:
            src = os.path.join(tmpd, "sd")
            build_dir = os.path.join(src, "build")
            code = ctx.run(engine_clone_command(src), timeout=900)
            if code != 0:
                ctx.log("WARNING: could not check out the image "
                        "engine at %s (git clone exit %s) — prompt "
                        "import will stay unavailable. Try "
                        "./install.sh --with-image-engine in a "
                        "terminal to see the git output."
                        % (ENGINE_RELEASE, code))
                return StepResult(StepResult.WARN,
                                  "engine checkout failed — prompt "
                                  "import unavailable")
            code = ctx.run(
                ["cmake", "-S", src, "-B", build_dir,
                 "-DCMAKE_BUILD_TYPE=Release"], timeout=600)
            if code == 0:
                code = ctx.run(
                    ["cmake", "--build", build_dir,
                     "-j%s" % (os.cpu_count() or 1)], timeout=3600)
            if code != 0:
                ctx.log("WARNING: the image engine build failed — "
                        "prompt import will stay unavailable. Try "
                        "./install.sh --with-image-engine in a "
                        "terminal to see the build output.")
                return StepResult(StepResult.WARN,
                                  "engine build failed — prompt "
                                  "import unavailable")
            code = ctx.run_admin(
                "rm -rf /opt/sd.cpp; mkdir -p /opt/sd.cpp/bin; "
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
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
    if find_model() is None:
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
    ctx.log("WARNING: the pip install failed (no internet?) — Pixel "
            "Forge is not installed yet. Check your connection and "
            "press Install again.")
    return StepResult(StepResult.FAIL, "pip install failed")


def step_files(ctx):
    """PATH shortcuts, icons, menu launcher."""
    ctx.log("Installing shortcuts, icons and the menu launcher…")
    prefix = ctx.home / ".local" / "share" / "pixel-forge"
    venv_bin = prefix / "venv" / "bin"
    bin_dir = ctx.home / ".local" / "bin"
    ctx.mkdir(bin_dir)
    ok = True
    ok = ctx.link(venv_bin / "pixel-forge",
                  bin_dir / "pixel-forge") and ok
    ok = ctx.link(venv_bin / "pixel-forge-gui",
                  bin_dir / "pixel-forge-gui") and ok

    icons_src = SRC_DIR / "icons"
    if not ctx.dryrun and not (icons_src / "pixel-forge-256.png").is_file():
        svg = icons_src / "pixel-forge.svg"
        if svg.is_file() and render_icons_from_svg(svg, icons_src):
            ctx.log("OK: App icons rendered from pixel-forge.svg")
        else:
            ctx.log("WARNING: could not render app icons from the SVG "
                    "— the menu entry will show a generic icon")
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
        ctx.run(["gtk-update-icon-cache", "-f", "-t",
                 str(ctx.home / ".local" / "share" / "icons" / "hicolor")])

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
        ctx.run(["update-desktop-database", str(apps_dir)])
    if not ok:
        return StepResult(StepResult.WARN,
                          "some shortcuts or icons could not be written")
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
