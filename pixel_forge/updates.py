"""Version check and ask-first auto-update (tasks 48 + 103) —
Qt-free, stdlib-only.

The check (task 48): the app asks GitHub's public releases
endpoint for the latest Pixel Forge release and compares its
tag against the running version. The request is a plain GET to
a public endpoint — it sends no identifying information and
needs no token. Since task 103 the check runs on launch by
default, silently: up to date means nothing happens at all,
and the Help menu switch still turns it off.

The update (task 103): when a newer release is out, the app
asks first — Update or Not now, with Not now the safe
default. Only on Update does the app pull anything, and the
pull is verified: the release zip is downloaded (resumable,
cancellable), its SHA-256 is checked against the checksum
sidecar published with the release (pixel-forge-v0.NN.zip
.sha256, the standard `sha256  filename` line), and only then
is it installed over the current install with the venv's own
pip. A missing or unparseable sidecar refuses the update just
like a mismatch does — never install a blind download. The
venv and the user's library and models are untouched. After
the install the app asks about restarting: Yes restarts the
program on its own, No waits for the next launch.

check_for_update is the one-call API the GUI worker uses: it
never raises for a network or parsing problem — a failed check
comes back as state "unknown" with a plain reason, so the GUI
can say so quietly instead of crashing. Version tags that do
not look like versions (anything but an optional "v" followed
by numbers and dots) are treated the same way, and a local
version newer than the latest release (a dev tree) is simply
"current" — neither is an error.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

LATEST_RELEASE_URL = ("https://api.github.com/repos/BROKE-N-BORED/"
                      "PIXEL-FORGE/releases/latest")
RELEASES_PAGE_URL = "https://github.com/BROKE-N-BORED/PIXEL-FORGE/releases"
CHECK_TIMEOUT_S = 8
DOWNLOAD_TIMEOUT_S = 30
DOWNLOAD_CHUNK_SIZE = 65536
SIDECAR_SUFFIX = ".sha256"

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


def parse_version(text):
    """A version string as a tuple of ints — "0.69" becomes
    (0, 69), "v0.69" the same — or None when it is not a
    version at all. Anything but an optional "v" followed by
    numbers and dots is refused, so a tag like "v0.69-beta"
    reads as no usable version rather than a wrong one."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest, current):
    """True when the latest version is newer than the running
    one. Either side unparsable means no — an unknown version
    is never reported as an update. Shorter tuples are padded
    with zeros, so 0.69 and 0.69.0 compare equal."""
    latest_parsed = parse_version(latest)
    current_parsed = parse_version(current)
    if latest_parsed is None or current_parsed is None:
        return False
    width = max(len(latest_parsed), len(current_parsed))
    latest_padded = latest_parsed + (0,) * (width - len(latest_parsed))
    current_padded = current_parsed + (0,) * (width - len(current_parsed))
    return latest_padded > current_padded


def _release_assets(payload):
    """The release's file assets as {name: {"url": ..., "size":
    ...}}, from the endpoint's "assets" list. Only entries
    with a name and a download URL are kept; a missing or
    malformed list reads as no assets, never an error — the
    updater refuses plainly when the file it needs is not
    there."""
    assets = {}
    entries = payload.get("assets")
    if not isinstance(entries, list):
        return assets
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        url = entry.get("browser_download_url")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(url, str) or not url:
            continue
        size = entry.get("size")
        if not isinstance(size, int) or size < 0:
            size = 0
        assets[name] = {"url": url, "size": size}
    return assets


def shorten_notes(body, max_lines=3, max_chars=300):
    """The release notes cut down for the update popup: the
    first few non-empty lines, at most max_chars characters.
    Empty or missing notes read as "" — the popup then shows
    just the version line. Never raises for a non-string."""
    if not isinstance(body, str):
        return ""
    lines = [line.strip() for line in body.splitlines()
             if line.strip()]
    text = "\n".join(lines[:max_lines])
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def fetch_latest_release(urlopen=None):
    """The latest release from GitHub's public endpoint, as
    {"version": "0.70", "url": "<release page>",
    "notes": "<release notes, possibly empty>",
    "assets": {name: {"url": ..., "size": ...}}}. Raises
    PixelForgeError with a plain reason when the network fails,
    the answer is not JSON, or it carries no tag: the caller
    turns every one of those into "unknown", never a crash.
    urlopen is a test seam — pass a stub and no network is
    touched."""
    from .pixelize import PixelForgeError

    opener = urlopen or urllib.request.urlopen
    try:
        with opener(LATEST_RELEASE_URL,
                    timeout=CHECK_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise PixelForgeError("the update check timed out")
        raise PixelForgeError(
            "could not reach GitHub: %s" % reason)
    except TimeoutError:
        raise PixelForgeError("the update check timed out")
    except (ValueError, UnicodeDecodeError):
        raise PixelForgeError(
            "the releases page did not answer in a form "
            "Pixel Forge understands")
    if not isinstance(payload, dict):
        raise PixelForgeError(
            "the releases page did not answer in a form "
            "Pixel Forge understands")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise PixelForgeError(
            "the releases page did not name a version")
    version = parse_version(tag)
    if version is None:
        raise PixelForgeError(
            "the latest release tag did not look like a version: %s"
            % tag)
    url = payload.get("html_url") or RELEASES_PAGE_URL
    return {"version": ".".join(str(part) for part in version),
            "url": url,
            "notes": payload.get("body") or "",
            "assets": _release_assets(payload)}


def check_for_update(current_version, urlopen=None):
    """One call, three outcomes, never an exception for a
    network or parsing problem:

    {"state": "newer", "latest": "0.70",
     "url": "<release page>", "notes": "<release notes>",
     "assets": {...}} — a newer release is out.
    {"state": "current", "latest": "0.69",
     "url": "<release page>", "notes": "", "assets": {...}} —
     the running version is the newest (or newer than the
     latest release, a dev tree).
    {"state": "unknown", "latest": None, "url": None,
     "reason": "<plain words>"} — the check could not run.
    """
    from .pixelize import PixelForgeError

    try:
        release = fetch_latest_release(urlopen=urlopen)
    except PixelForgeError as exc:
        return {"state": "unknown", "latest": None, "url": None,
                "reason": str(exc)}
    result = {"latest": release["version"],
              "url": release["url"],
              "notes": release["notes"],
              "assets": release["assets"]}
    if is_newer(release["version"], current_version):
        result["state"] = "newer"
    else:
        result["state"] = "current"
    return result


def updates_home():
    """The update work folder: ~/.local/share/pixel-forge
    /updates/. Downloaded zips live here, so a cancelled or
    interrupted download resumes on the next try instead of
    starting over."""
    return (Path.home() / ".local" / "share" / "pixel-forge"
            / "updates")


def zip_name_for(version):
    """The release zip's asset name for a version."""
    return "pixel-forge-v%s.zip" % version


def sidecar_name_for(version):
    """The checksum sidecar's asset name for a version."""
    return zip_name_for(version) + SIDECAR_SUFFIX


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def parse_sidecar(text):
    """The SHA-256 hex digest from a checksum sidecar — the
    standard `sha256  filename` line — lowercased, or None
    when the text is not a sidecar at all (empty, or the first
    token of the first non-empty line is not 64 hex
    characters). A missing or unparseable sidecar refuses the
    update, exactly like a mismatch: never install a blind
    download."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0]
        if _SHA256_RE.match(token):
            return token.lower()
        return None
    return None


class DownloadCancelled(Exception):
    """The user cancelled a download. Not an error — the
    partial file stays in the updates folder for the next try
    to resume, and the old version is untouched. Mirrors
    video.ExtractionCancelled."""


def fetch_text(url, urlopen=None):
    """A small GET (the checksum sidecar) as text. Raises
    PixelForgeError with a plain reason on any failure.
    urlopen is a test seam."""
    from .pixelize import PixelForgeError

    opener = urlopen or urllib.request.urlopen
    try:
        with opener(url, timeout=CHECK_TIMEOUT_S) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise PixelForgeError(
            "could not fetch %s: the server answered %s"
            % (url, exc.code))
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise PixelForgeError("the download timed out")
        raise PixelForgeError(
            "could not reach the download server: %s" % reason)
    except TimeoutError:
        raise PixelForgeError("the download timed out")
    except (ValueError, UnicodeDecodeError):
        raise PixelForgeError(
            "the download server did not answer in a form "
            "Pixel Forge understands")


def _open_download(opener, url, offset):
    """Open a download, resuming at offset when there is one.
    Returns (response, resumed): resumed is True only when
    the server answered 206 and the bytes continue the
    partial file — a 200 means start over from zero."""
    if offset > 0:
        request = urllib.request.Request(
            url, headers={"Range": "bytes=%d-" % offset})
    else:
        request = urllib.request.Request(url)
    response = opener(request, timeout=DOWNLOAD_TIMEOUT_S)
    status = getattr(response, "status", 200)
    return response, (offset > 0 and status == 206)


def download_file(url, dest, expected_size=None,
                  cancel_event=None, progress=None, urlopen=None):
    """Download a file with Range resume (task 103, U24).

    dest is the path to write. A partial file already there
    resumes where it stopped — the server is asked for the
    rest with a Range header, and a server that answers 200
    instead of 206 restarts the file from zero. progress, when
    given, is called as progress(downloaded, total) after
    every chunk, with total 0 when the size is unknown.
    cancel_event stops the download between chunks: it raises
    DownloadCancelled and the partial file is kept for the
    next try. Raises PixelForgeError with a plain reason on a
    network failure, and when the finished file is shorter
    than expected_size — a truncated download never reaches
    the checksum step. Returns the bytes on disk. urlopen is
    a test seam — pass a stub and no network is touched."""
    from .pixelize import PixelForgeError

    opener = urlopen or urllib.request.urlopen
    dest = Path(dest)
    offset = 0
    if dest.exists():
        offset = dest.stat().st_size
        if expected_size is not None and offset == expected_size:
            if progress is not None:
                progress(offset, expected_size)
            return offset
        if expected_size is not None and offset > expected_size:
            # Longer than the release says: not a partial
            # download of this file. Start over.
            offset = 0
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled()
    try:
        response, resumed = _open_download(opener, url, offset)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            # The range makes no sense to the server (a stale
            # partial, or a changed file): start over once. A
            # partial already at the expected size never gets
            # here — it returns before the request.
            dest.unlink(missing_ok=True)
            return download_file(url, dest, expected_size,
                                 cancel_event, progress, urlopen)
        raise PixelForgeError(
            "could not download the update: the server "
            "answered %s" % exc.code)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise PixelForgeError("the download timed out")
        raise PixelForgeError(
            "could not reach the download server: %s" % reason)
    except TimeoutError:
        raise PixelForgeError("the download timed out")
    if not resumed:
        offset = 0
    total = expected_size or 0
    if total == 0:
        headers = getattr(response, "headers", None)
        length = headers.get("Content-Length") if headers else None
        try:
            total = int(length) + offset if length else 0
        except (TypeError, ValueError):
            total = 0
    downloaded = offset
    if progress is not None:
        progress(downloaded, total)
    try:
        with response:
            mode = "ab" if resumed else "wb"
            with open(dest, mode) as handle:
                while True:
                    if cancel_event is not None \
                            and cancel_event.is_set():
                        raise DownloadCancelled()
                    try:
                        chunk = response.read(
                            DOWNLOAD_CHUNK_SIZE)
                    except TimeoutError:
                        raise PixelForgeError(
                            "the download timed out")
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
    except DownloadCancelled:
        raise
    except PixelForgeError:
        raise
    except OSError as exc:
        raise PixelForgeError(
            "could not write the download: %s" % exc)
    if expected_size is not None and downloaded != expected_size:
        raise PixelForgeError(
            "the download stopped short (%d of %d bytes) — "
            "try the update again and it will resume"
            % (downloaded, expected_size))
    return downloaded


def sha256_of(path):
    """The SHA-256 hex digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_zip(zip_path, expected_hex):
    """Check a downloaded zip against its checksum. Raises
    PixelForgeError with a plain reason when the file is
    missing or the digest does not match — the caller never
    installs a file this refuses. Returns None on a match."""
    from .pixelize import PixelForgeError

    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise PixelForgeError(
            "the downloaded update is missing — try the update "
            "again")
    actual = sha256_of(zip_path)
    if actual != expected_hex.lower():
        raise PixelForgeError(
            "the downloaded file does not match the checksum "
            "published with the release, so it was not "
            "installed. Try the update again.")


def find_installed_root(package_dir=None):
    """The installed pixel_forge package dir, or None when
    Pixel Forge is running from a source tree instead of an
    install. Installed means the package lives under a
    site-packages (or dist-packages) dir — the pip install
    the installer runs. A source tree cannot be updated in
    place: it has no install to replace. package_dir is a
    test seam — pass a fake package dir and no filesystem
    layout is assumed."""
    if package_dir is None:
        package_dir = Path(__file__).resolve().parent
    else:
        package_dir = Path(package_dir)
    if "site-packages" in package_dir.parts \
            or "dist-packages" in package_dir.parts:
        return package_dir
    return None


def _default_runner(cmd, cwd):
    """Run a command, capturing its output. Returns
    (returncode, stdout, stderr). The pip install and the
    version check share it; install_update's run seam
    replaces it in tests."""
    proc = subprocess.run(cmd, cwd=str(cwd),
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


def install_update(zip_path, expected_version, python=None,
                   run=None):
    """Install a verified release zip over the current install
    (task 103).

    The zip is extracted to a temp dir and the venv's own pip
    installs the tree, the same way the installer does —
    --no-deps (the dependencies are already installed; the
    update must not touch them) and --force-reinstall (the
    tree's pyproject version is stale at 0.76, so without it
    pip would call the install "already satisfied" and skip
    it). The running process keeps its already-loaded modules,
    so replacing the files under it is safe; the new code
    loads on the next start. Afterwards the installed version
    is checked by running the venv python from an empty dir
    (the v0.11 lesson: never from the source tree) and
    comparing __version__ against expected_version.

    python is the interpreter to run pip with (default:
    sys.executable — the running venv python). run is a test
    seam — run(cmd, cwd) returning (returncode, stdout,
    stderr) — pass a stub and no pip ever runs. Raises
    PixelForgeError with a plain reason on any failure; the
    temp dir is always cleaned up."""
    from .pixelize import PixelForgeError

    runner = run or _default_runner
    interpreter = python or sys.executable
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise PixelForgeError(
            "the downloaded update is missing — try the update "
            "again")
    tmp = Path(tempfile.mkdtemp(prefix="pixel-forge-update-"))
    try:
        try:
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(tmp)
        except (zipfile.BadZipFile, OSError) as exc:
            raise PixelForgeError(
                "the downloaded update is not a usable release "
                "file: %s" % exc)
        roots = [child for child in tmp.iterdir()
                 if child.is_dir()
                 and (child / "pyproject.toml").exists()]
        if len(roots) != 1:
            raise PixelForgeError(
                "the downloaded update is not a Pixel Forge "
                "release — it was not installed")
        root = roots[0]
        returncode, _out, stderr = runner(
            [interpreter, "-m", "pip", "install", "--quiet",
             "--no-deps", "--force-reinstall", str(root)], tmp)
        if returncode != 0:
            tail = [line for line in stderr.splitlines()
                    if line.strip()][-3:]
            detail = ("\n" + "\n".join(tail)) if tail else ""
            raise PixelForgeError(
                "the update could not be installed%s" % detail)
        check_dir = Path(tempfile.mkdtemp(
            prefix="pixel-forge-version-check-"))
        try:
            returncode, stdout, _err = runner(
                [interpreter, "-c",
                 "from pixel_forge import __version__; "
                 "print(__version__)"],
                check_dir)
        finally:
            shutil.rmtree(check_dir, ignore_errors=True)
        installed = stdout.strip() if returncode == 0 else ""
        if installed != expected_version:
            raise PixelForgeError(
                "the update installed, but the new version does "
                "not report itself as %s — the install may be "
                "incomplete" % expected_version)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_program(starter=None):
    """Start a fresh copy of Pixel Forge the way this one was
    started, then let the caller quit: [sys.executable,
    sys.argv[0], *sys.argv[1:]]. The new process is detached
    (its own session, no pipes to this one), so quitting the
    old process cannot take it down. starter is a test seam —
    starter(cmd) instead of spawning for real. Raises
    PixelForgeError when there is no command to re-run."""
    from .pixelize import PixelForgeError

    if not sys.argv or not sys.argv[0]:
        raise PixelForgeError(
            "Pixel Forge could not restart itself.")
    cmd = [sys.executable, sys.argv[0]] + sys.argv[1:]
    if starter is not None:
        return starter(cmd)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL,
                     start_new_session=True)
    return None
