"""Local background removal — rembg's u2net model, on CPU.

The Image tab's "Remove background" button runs the loaded image
through rembg's u2net model: the subject stays, the rest goes
transparent, and the result replaces the loaded image. Everything
runs on this machine — no network at run time, no paid service.

Pins (verified live 2026-11-14 — do not substitute):
  model: u2net.onnx (175,997,641 bytes) from rembg's own release
  https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx
  SHA-256 8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491
  (rembg itself also checks the file's md5,
  60024c5c889badc19c04ad937298a77b, when it downloads one.)
  This is the SMALL model — the task's ~176 MB. The installers
  pin its exact size and hash, so no bigger model can slip in
  under this name.

The model lives where rembg looks for it, so rembg never
downloads anything at run time: the current layout is
~/.rembg/models/u2net/u2net.onnx, and the old flat layout
~/.u2net/u2net.onnx is still read (rembg 2.x checks both before
it downloads). The installers download it there with curl -C -
(resume) and SHA-256-check it, the same way the image model is
handled. PIXELFORGE_BGREMOVE_MODEL overrides both homes.

The packages (rembg + onnxruntime) are OPTIONAL, never core:
the app launches and does everything else without them. The
installer offers them as a ticked-off-by-default step; without
them the button says plainly what to install instead of failing
cryptically. The removal itself runs in a subprocess (the venv's
python running this module) so the GUI never blocks, Cancel
kills the run at once, and the model's memory is freed when the
child exits — the same shape as the image engine, which the app
also shells out to.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .image import Image
from .pixelize import PixelForgeError

MODEL_FILENAME = "u2net.onnx"
MODEL_URL = ("https://github.com/danielgatis/rembg/releases/"
             "download/v0.0.0/" + MODEL_FILENAME)
# Exact size of the pinned model file (175,997,641 bytes, the
# verified copy). A file named MODEL_FILENAME at any other size
# is a partial or corrupt download, never the model — the
# installers use this to tell an interrupted download (resume
# it) from a finished one (keep it). install.sh carries a
# literal copy (BGREMOVE_MODEL_SIZE) that the installer tests
# check against this value, like the SD model size.
MODEL_SIZE_BYTES = 175997641
# SHA-256 of the pinned model file, computed from the verified
# copy. Both installers check every model download against this
# and refuse a file that does not match — a partial or corrupt
# download must never pass as the model. This is the ONE place
# the hash lives: the GUI installer imports it, and install.sh
# carries a literal copy that the installer tests check against
# this value.
MODEL_SHA256 = ("8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a"
                "739a7a728a912b491")
# The packages the installer pip-installs for this feature, and
# the ones require_deps checks for. rembg brings numpy, Pillow,
# and the rest with it; onnxruntime is named separately because
# it is the piece that actually runs the model, and the GUI
# installer verifies the pair after installing them.
BGREMOVE_PACKAGES = ["rembg", "onnxruntime"]
# How long one removal may run before it is killed (10 minutes):
# a CPU run is seconds to about a minute on a photo, so this is
# a runaway guard, not a deadline the user is expected to hit.
REMOVAL_TIMEOUT_S = 600
# Env override for the Python that runs the removal subprocess:
# the installed venv's python by default (sys.executable of the
# GUI process). Point it at another interpreter only when that
# one is the one with rembg installed.
PYTHON_ENV = "PIXELFORGE_BGREMOVE_PYTHON"
MODEL_ENV = "PIXELFORGE_BGREMOVE_MODEL"


class RemovalCancelled(Exception):
    """The user cancelled a background removal. Not an error —
    the image is left exactly as it was."""


# ------------------------------------------------------------------ finding

def model_homes() -> list:
    """Model folders in search order, mirroring rembg 2.x's own
    layout: the current per-model directory first (REMBG_HOME
    wins when set, then XDG_DATA_HOME, then ~/.rembg), then the
    old flat ~/.u2net layout (U2NET_HOME wins when set), which
    rembg still reads. The folders hold u2net.onnx directly in
    the legacy case and under models/u2net/ in the current one —
    the list carries the full file paths, not the roots, so
    callers never rebuild them."""
    home = Path.home()
    rembg_home = os.environ.get("REMBG_HOME")
    if rembg_home:
        current = Path(rembg_home) / "models" / "u2net"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            current = Path(xdg) / "rembg" / "models" / "u2net"
        else:
            current = home / ".rembg" / "models" / "u2net"
    legacy = os.environ.get("U2NET_HOME")
    legacy_dir = Path(legacy) if legacy else home / ".u2net"
    return [current / MODEL_FILENAME, legacy_dir / MODEL_FILENAME]


def find_model() -> Path | None:
    """The model file, or None. Env override wins; then rembg's
    own homes, current layout first."""
    override = os.environ.get(MODEL_ENV)
    if override and Path(override).is_file():
        return Path(override)
    for candidate in model_homes():
        if candidate.is_file():
            return candidate
    return None


def model_missing_text() -> str:
    return (f"the background-removal model is not downloaded — "
            f"nothing was removed. Fix: download {MODEL_FILENAME} "
            f"(about 176 MB) from {MODEL_URL} and put it in "
            f"~/.rembg/models/u2net/ or ~/.u2net/, or run the "
            f"installer with background removal ticked (it "
            f"downloads the model for you).")


def require_model() -> Path:
    model = find_model()
    if model is None:
        raise PixelForgeError(model_missing_text())
    return model


def is_partial_model(path) -> bool:
    """True when `path` is the pinned model file at the wrong
    size — a download interrupted partway (or a corrupt file),
    never the finished model. A file with any other name is not
    judged here. A file that vanished between the find and the
    stat is not partial — the caller treats it as missing on its
    own."""
    candidate = Path(path)
    if candidate.name != MODEL_FILENAME:
        return False
    try:
        return candidate.stat().st_size != MODEL_SIZE_BYTES
    except OSError:
        return False


def model_download_command(dest) -> list:
    """The curl download for the model. -C - resumes a partial
    file instead of restarting a 176 MB download from scratch."""
    return ["curl", "-fSL", "-C", "-", MODEL_URL, "-o", str(dest)]


def check_model_file(path) -> None:
    """Raise PixelForgeError unless the file at `path` is exactly
    the pinned model — its SHA-256 must equal MODEL_SHA256. The
    message names the expected and actual hashes, so a corrupt or
    partial download is never mistaken for the real model."""
    from .imagegen import file_sha256

    actual = file_sha256(path)
    if actual != MODEL_SHA256:
        raise PixelForgeError(
            "the background-removal model at %s failed its "
            "SHA-256 check — expected %s, got %s (the file is "
            "not the pinned model: delete it and download again)"
            % (path, MODEL_SHA256, actual))


# ------------------------------------------------------------- dependencies

def deps_available() -> tuple:
    """(ok, missing): whether the background-removal packages
    import, and which of them do not. rembg is the only one
    whose import is slow (it pulls onnxruntime with it), so
    callers check once per run, never per frame. The check is
    import-only — nothing is loaded and no model is touched."""
    missing = []
    for name in BGREMOVE_PACKAGES:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return (not missing, missing)


def deps_missing_text(missing=None) -> str:
    if missing is None:
        _ok, missing = deps_available()
    names = ", ".join(missing) if missing else "rembg"
    return ("background removal needs %s, which is not installed "
            "— nothing was removed. Fix: tick \"Install "
            "background removal\" in the Pixel Forge installer "
            "and press Install again, or run ./install.sh "
            "--with-bgremove in the pixel-forge folder (it "
            "installs %s and downloads the model for you)."
            % (names, names))


def require_deps() -> None:
    """Raise unless the packages import in THIS process — the
    check for remove_background, which imports rembg here. The
    GUI does not use this one: the removal runs in a subprocess
    under child_python(), which may be another interpreter (the
    PIXELFORGE_BGREMOVE_PYTHON override), so the GUI checks the
    child instead — require_child_ready, below."""
    ok, missing = deps_available()
    if not ok:
        raise PixelForgeError(deps_missing_text(missing))


def _same_interpreter(a, b) -> bool:
    """Whether two interpreter paths name the same interpreter.
    Compares abspath, NOT realpath: a venv's bin/python is a
    symlink to the system python, so two different venvs
    realpath to the same file while having different packages
    installed — realpath would call them the same interpreter
    and check the wrong one's imports. The same interpreter
    spelled two ways (bin/python vs bin/python3) reads as
    different here and takes the child path — a spawn, but
    always the right answer."""
    return os.path.abspath(a) == os.path.abspath(b)


def child_deps_available() -> tuple:
    """(ok, missing): whether the background-removal packages
    import under the interpreter the removal actually runs
    under (child_python), and which of them do not. When that
    interpreter is this process's own, the check is in-process
    (no spawn); otherwise the child is asked directly with a
    small -c script that prints the missing names. Raises
    PixelForgeError when the child python cannot be run or
    does not answer — the GUI must not start a run it cannot
    check."""
    python = child_python()
    if _same_interpreter(python, sys.executable):
        return deps_available()
    script = ("import sys\n"
              "missing = []\n"
              "for name in %r:\n"
              "    try:\n"
              "        __import__(name)\n"
              "    except ImportError:\n"
              "        missing.append(name)\n"
              "print(' '.join(missing))\n"
              % (BGREMOVE_PACKAGES,))
    try:
        proc = subprocess.run(
            [python, "-c", script],
            capture_output=True, text=True, timeout=120)
    except OSError as exc:
        raise PixelForgeError(
            "could not run the background-removal python at %s "
            "(%s) — check PIXELFORGE_BGREMOVE_PYTHON points at "
            "a working python" % (python, exc))
    except subprocess.TimeoutExpired:
        raise PixelForgeError(
            "the background-removal python at %s did not answer "
            "in time — check PIXELFORGE_BGREMOVE_PYTHON points "
            "at a working python" % python)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise PixelForgeError(
            "could not check the background-removal packages "
            "under %s — %s"
            % (python, detail[-1] if detail else
               "the check exited with code %d" % proc.returncode))
    missing = [name for name in BGREMOVE_PACKAGES
               if name in proc.stdout.split()]
    return (not missing, missing)


def require_child_deps() -> None:
    """Raise unless the packages import under the interpreter
    the removal runs under — the GUI's half of the pre-flight
    check (see require_deps for why it is not that one)."""
    ok, missing = child_deps_available()
    if not ok:
        raise PixelForgeError(deps_missing_text(missing))


def require_child_ready() -> Path:
    """The GUI's pre-flight check before starting a removal:
    the packages import under the interpreter the removal runs
    under, and the model file is there. Raises PixelForgeError
    with the plain fix otherwise. Returns the model path."""
    require_child_deps()
    return require_model()


# ------------------------------------------------------------------ removal

def _image_to_pil(image):
    """A Pixel Forge Image as a PIL RGBA image. PIL is imported
    here, not at module level: this module must import without
    the optional packages installed."""
    from PIL import Image as PILImage

    return PILImage.frombytes("RGBA",
                              (image.width(), image.height()),
                              bytes(image.data))


def _pil_to_image(pil_image):
    """A PIL image back to a Pixel Forge Image (RGBA)."""
    rgba = pil_image.convert("RGBA")
    return Image(rgba.width, rgba.height,
                 bytearray(rgba.tobytes()))


def _new_session(model_path):
    """A rembg session on the pinned model, CPU only.

    rembg finds a pre-downloaded model on its own (it checks its
    homes before downloading), so a model in one of the standard
    homes needs no help. A model anywhere else — the env
    override pointing at a shared folder, for example — is
    reached by pointing U2NET_HOME at its parent folder first:
    rembg reads that folder flat, so the file is found there.
    Providers are pinned to CPU: this machine has no GPU, and
    the default provider order would silently pick a GPU
    provider on a machine that has one."""
    import rembg

    standard = [Path(p) for p in model_homes()]
    if Path(model_path) not in standard:
        os.environ["U2NET_HOME"] = str(Path(model_path).parent)
    return rembg.new_session("u2net",
                             providers=["CPUExecutionProvider"])


def remove_background(image, model=None, session=None):
    """Remove the background from `image` (a Pixel Forge Image),
    in this process. Returns a new Image: the subject's pixels
    keep their color, the background's alpha is 0. The input is
    left alone.

    `model` is the model file to use (find_model() when None);
    `session` is a rembg session to reuse, for callers that
    remove more than one image in a row. Raises PixelForgeError
    for missing packages, a missing model, an empty image, or a
    model rembg cannot load — never a raw onnxruntime error.
    """
    if image.isNull():
        raise PixelForgeError(
            "cannot remove the background from an image with no "
            "size")
    require_deps()
    if session is None:
        # The model is resolved only when a session has to be
        # built — a caller passing its own session has already
        # settled where the model lives.
        model_path = (Path(model) if model is not None
                      else require_model())
        try:
            session = _new_session(model_path)
        except Exception as exc:  # noqa: BLE001 - plain words
            raise PixelForgeError(
                "could not load the background-removal model at "
                "%s (%s) — the file may be damaged; delete it "
                "and download it again" % (model_path, exc))
    try:
        import rembg

        result = rembg.remove(_image_to_pil(image),
                              session=session)
    except Exception as exc:  # noqa: BLE001 - plain words
        raise PixelForgeError(
            "background removal failed: %s" % exc)
    return _pil_to_image(result)


# ------------------------------------------------------- subprocess removal

def child_python() -> str:
    """The interpreter the removal subprocess runs under: the
    PIXELFORGE_BGREMOVE_PYTHON override when it names an
    existing file, else this process's own interpreter (the
    installed venv's python, which is where the installer puts
    rembg)."""
    override = os.environ.get(PYTHON_ENV)
    if override and Path(override).is_file():
        return override
    return sys.executable


def child_command(src, dst) -> list:
    """The subprocess invocation that removes the background
    from the image at `src` and writes the result to `dst`.
    Kept separate so tests can check the command without
    running it."""
    return [child_python(), "-m", "pixel_forge.bgremove",
            str(src), str(dst)]


def _child_env(model):
    """The child's environment: this process's, plus the model
    override when a model path was given, plus the package's
    parent on PYTHONPATH so `python -m pixel_forge.bgremove`
    finds the package even when the GUI was launched from
    another folder (a plain `python -m` only puts the working
    directory first)."""
    env = dict(os.environ)
    if model is not None:
        env[MODEL_ENV] = str(model)
    package_parent = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (package_parent if not existing else
                         package_parent + os.pathsep + existing)
    return env


def remove_background_file(src, dst, model=None,
                           timeout_s=REMOVAL_TIMEOUT_S,
                           cancel_event=None) -> None:
    """Remove the background from the image file at `src`,
    writing the result to `dst`, in a subprocess — the GUI's
    path. The child is the venv's python running this module,
    so the model's memory lives in the child and is freed when
    it exits, and Cancel kills the run at once instead of
    waiting for a long CPU run to finish.

    Returns None on success. Raises RemovalCancelled when the
    cancel event fires (dst is removed, if it landed) and
    PixelForgeError for a failed or timed-out run, with the
    child's own last lines in the message. A run that exits 0
    but leaves no output file counts as failed, not done."""
    cmd = child_command(src, dst)
    try:
        log = tempfile.TemporaryFile(mode="w+")
    except OSError as exc:
        raise PixelForgeError(
            "could not start background removal: %s" % exc)
    try:
        try:
            proc = subprocess.Popen(cmd, stdout=log,
                                    stderr=subprocess.STDOUT,
                                    text=True,
                                    env=_child_env(model))
        except OSError as exc:
            raise PixelForgeError(
                "could not run background removal: %s" % exc)
        deadline = time.monotonic() + timeout_s
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait()
                Path(dst).unlink(missing_ok=True)
                raise RemovalCancelled()
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                Path(dst).unlink(missing_ok=True)
                raise PixelForgeError(
                    "background removal timed out after %ds"
                    % timeout_s)
            time.sleep(0.2)
        log.seek(0)
        output = log.read()
    finally:
        log.close()
    if proc.returncode == 0 and Path(dst).is_file() and \
            Path(dst).stat().st_size > 0:
        return
    Path(dst).unlink(missing_ok=True)
    detail = (output or "").strip().splitlines()
    raise PixelForgeError(
        "background removal failed — %s"
        % ("; ".join(detail[-3:]) if detail else
           "the removal process exited with code %d"
           % proc.returncode))


# ------------------------------------------------------------- child entry

def main(argv=None) -> int:
    """The removal subprocess: `python -m pixel_forge.bgremove
    <src> <dst>`. Loads the image, removes the background,
    writes the result. Exit 0 on success, 1 with a plain
    message on stderr otherwise. Nothing is printed on success
    — the parent reads the result from the file, not the
    output."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m pixel_forge.bgremove "
              "<input image> <output image>", file=sys.stderr)
        return 1
    src, dst = args
    try:
        from .pixelize import load_image
        from .pngio import write_png

        result = remove_background(load_image(src))
        write_png(result, dst)
    except PixelForgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - child must not die raw
        print("background removal failed: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
