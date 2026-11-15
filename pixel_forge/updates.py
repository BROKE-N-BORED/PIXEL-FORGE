"""Opt-in version check (task 48) — Qt-free, stdlib-only.

Pixel Forge never phones home by default. When the user turns
the check on (Help, Check for updates on startup) or asks for
one by hand (Help, Check for updates), the app asks GitHub's
public releases endpoint for the latest Pixel Forge release
and compares its tag against the running version. The request
is a plain GET to a public endpoint — it sends no identifying
information and needs no token — and it never downloads or
installs anything: when a newer release is out, the app says
so and offers the release page, and the user downloads it
themselves.

check_for_update is the one-call API the GUI worker uses: it
never raises for a network or parsing problem — a failed check
comes back as state "unknown" with a plain reason, so the GUI
can say so quietly instead of crashing. Version tags that do
not look like versions (anything but an optional "v" followed
by numbers and dots) are treated the same way, and a local
version newer than the latest release (a dev tree) is simply
"current" — neither is an error.
"""

import json
import re
import urllib.error
import urllib.request

LATEST_RELEASE_URL = ("https://api.github.com/repos/BROKE-N-BORED/"
                      "PIXEL-FORGE/releases/latest")
RELEASES_PAGE_URL = "https://github.com/BROKE-N-BORED/PIXEL-FORGE/releases"
CHECK_TIMEOUT_S = 8

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


def fetch_latest_release(urlopen=None):
    """The latest release from GitHub's public endpoint, as
    {"version": "0.70", "url": "<release page>"}. Raises
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
            "url": url}


def check_for_update(current_version, urlopen=None):
    """One call, three outcomes, never an exception for a
    network or parsing problem:

    {"state": "newer", "latest": "0.70",
     "url": "<release page>"} — a newer release is out.
    {"state": "current", "latest": "0.69",
     "url": "<release page>"} — the running version is the
     newest (or newer than the latest release, a dev tree).
    {"state": "unknown", "latest": None, "url": None,
     "reason": "<plain words>"} — the check could not run.
    """
    from .pixelize import PixelForgeError

    try:
        release = fetch_latest_release(urlopen=urlopen)
    except PixelForgeError as exc:
        return {"state": "unknown", "latest": None, "url": None,
                "reason": str(exc)}
    if is_newer(release["version"], current_version):
        return {"state": "newer", "latest": release["version"],
                "url": release["url"]}
    return {"state": "current", "latest": release["version"],
            "url": release["url"]}
