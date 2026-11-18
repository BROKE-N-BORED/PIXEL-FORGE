"""Tests for the update check (task 48) and the ask-first
auto-update (task 103).

No test here touches the real network: every fetch goes
through a stub urlopen, and the install step runs through a
stub runner, never a real pip. The GUI tests cover the menu,
the setting, the popups, and the restart question; these
cover the version math, the fetch's failure paths, the
download, the checksum, and the install.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from pixel_forge.pixelize import PixelForgeError
from pixel_forge.updates import (CHECK_TIMEOUT_S,
                                 DOWNLOAD_CHUNK_SIZE,
                                 LATEST_RELEASE_URL,
                                 RELEASES_PAGE_URL,
                                 DownloadCancelled,
                                 check_for_update,
                                 download_file,
                                 fetch_latest_release,
                                 fetch_text,
                                 find_installed_root,
                                 install_update, is_newer,
                                 parse_sidecar, parse_version,
                                 restart_program, sha256_of,
                                 shorten_notes, verify_zip)


class FakeResponse:
    """A stub urlopen result: a context manager holding bytes."""

    def __init__(self, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def stub_urlopen(payload=None, error=None):
    """A urlopen stub: returns FakeResponse(payload), or raises
    error. Records the URL and timeout it was called with."""
    calls = []

    def opener(url, timeout=None):
        calls.append((url, timeout))
        if error is not None:
            raise error
        return FakeResponse(payload)

    opener.calls = calls
    return opener


def release_json(tag, url="https://github.com/BROKE-N-BORED/"
                          "PIXEL-FORGE/releases/tag/v0.70"):
    return json.dumps({"tag_name": tag, "html_url": url})


class ParseVersionTests(unittest.TestCase):
    def test_plain_version(self):
        self.assertEqual(parse_version("0.69"), (0, 69))

    def test_leading_v(self):
        self.assertEqual(parse_version("v0.69"), (0, 69))

    def test_three_parts(self):
        self.assertEqual(parse_version("0.69.1"), (0, 69, 1))

    def test_single_number(self):
        self.assertEqual(parse_version("v1"), (1,))

    def test_surrounding_whitespace(self):
        self.assertEqual(parse_version("  0.69  "), (0, 69))

    def test_prerelease_suffix_refused(self):
        self.assertIsNone(parse_version("v0.69-beta"))

    def test_trailing_words_refused(self):
        self.assertIsNone(parse_version("0.69 beta"))

    def test_empty_refused(self):
        self.assertIsNone(parse_version(""))

    def test_words_refused(self):
        self.assertIsNone(parse_version("abc"))

    def test_bare_v_refused(self):
        self.assertIsNone(parse_version("v"))

    def test_non_string_refused(self):
        self.assertIsNone(parse_version(None))
        self.assertIsNone(parse_version(69))

    def test_double_dot_refused(self):
        self.assertIsNone(parse_version("0..69"))


class IsNewerTests(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(is_newer("0.70", "0.69"))

    def test_same_is_not_newer(self):
        self.assertFalse(is_newer("0.69", "0.69"))

    def test_older_is_not_newer(self):
        self.assertFalse(is_newer("0.68", "0.69"))

    def test_patch_release_is_newer(self):
        self.assertTrue(is_newer("0.69.1", "0.69"))

    def test_trailing_zero_compares_equal(self):
        self.assertFalse(is_newer("0.69", "0.69.0"))
        self.assertFalse(is_newer("0.69.0", "0.69"))

    def test_leading_v_on_both_sides(self):
        self.assertTrue(is_newer("v0.70", "v0.69"))

    def test_unparsable_latest_is_never_newer(self):
        self.assertFalse(is_newer("bogus", "0.69"))

    def test_unparsable_current_is_never_newer(self):
        self.assertFalse(is_newer("0.70", "bogus"))

    def test_major_version_jump(self):
        self.assertTrue(is_newer("1.0", "0.99"))


class FetchLatestTests(unittest.TestCase):
    def test_success_reads_tag_and_url(self):
        opener = stub_urlopen(release_json("v0.70"))
        release = fetch_latest_release(urlopen=opener)
        self.assertEqual(
            release,
            {"version": "0.70",
             "url": "https://github.com/BROKE-N-BORED/"
                    "PIXEL-FORGE/releases/tag/v0.70",
             "notes": "",
             "assets": {}})

    def test_success_reads_notes_and_assets(self):
        payload = json.dumps({
            "tag_name": "v0.70",
            "html_url": "https://github.com/BROKE-N-BORED/"
                        "PIXEL-FORGE/releases/tag/v0.70",
            "body": "Bug fixes and a faster grid.\n\nSee the log.",
            "assets": [
                {"name": "pixel-forge-v0.70.zip",
                 "browser_download_url":
                     "https://example.test/v0.70.zip",
                 "size": 12345},
                {"name": "pixel-forge-v0.70.zip.sha256",
                 "browser_download_url":
                     "https://example.test/v0.70.zip.sha256",
                 "size": 80},
            ]})
        release = fetch_latest_release(
            urlopen=stub_urlopen(payload))
        self.assertEqual(release["notes"],
                         "Bug fixes and a faster grid.\n\n"
                         "See the log.")
        self.assertEqual(
            release["assets"]["pixel-forge-v0.70.zip"],
            {"url": "https://example.test/v0.70.zip",
             "size": 12345})
        self.assertEqual(
            release["assets"]["pixel-forge-v0.70.zip.sha256"],
            {"url": "https://example.test/v0.70.zip.sha256",
             "size": 80})

    def test_malformed_assets_are_skipped(self):
        payload = json.dumps({
            "tag_name": "v0.70",
            "assets": [
                {"name": "pixel-forge-v0.70.zip",
                 "browser_download_url":
                     "https://example.test/v0.70.zip",
                 "size": 12345},
                {"name": "no-url.zip"},
                {"browser_download_url":
                    "https://example.test/no-name.zip"},
                "not a dict at all",
                {"name": "pixel-forge-v0.70.zip.sha256",
                 "browser_download_url":
                     "https://example.test/v0.70.zip.sha256",
                 "size": "not a number"},
            ]})
        release = fetch_latest_release(
            urlopen=stub_urlopen(payload))
        self.assertEqual(
            set(release["assets"]),
            {"pixel-forge-v0.70.zip",
             "pixel-forge-v0.70.zip.sha256"})
        self.assertEqual(
            release["assets"]["pixel-forge-v0.70.zip.sha256"]
            ["size"], 0)

    def test_non_list_assets_read_as_none(self):
        payload = json.dumps({"tag_name": "v0.70",
                              "assets": "not a list"})
        release = fetch_latest_release(
            urlopen=stub_urlopen(payload))
        self.assertEqual(release["assets"], {})

    def test_success_asks_the_releases_endpoint_with_timeout(self):
        opener = stub_urlopen(release_json("v0.70"))
        fetch_latest_release(urlopen=opener)
        self.assertEqual(opener.calls,
                         [(LATEST_RELEASE_URL, CHECK_TIMEOUT_S)])

    def test_missing_page_url_falls_back_to_releases_page(self):
        payload = json.dumps({"tag_name": "v0.70"})
        release = fetch_latest_release(
            urlopen=stub_urlopen(payload))
        self.assertEqual(release["url"], RELEASES_PAGE_URL)

    def test_url_error_is_plain(self):
        opener = stub_urlopen(
            error=urllib.error.URLError("no route to host"))
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("could not reach GitHub", str(caught.exception))

    def test_timeout_is_plain(self):
        opener = stub_urlopen(
            error=urllib.error.URLError(TimeoutError()))
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("timed out", str(caught.exception))

    def test_direct_timeout_is_plain(self):
        opener = stub_urlopen(error=TimeoutError())
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("timed out", str(caught.exception))

    def test_bad_json_is_plain(self):
        opener = stub_urlopen("this is not json {")
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("did not answer in a form",
                      str(caught.exception))

    def test_json_list_is_plain(self):
        opener = stub_urlopen("[1, 2, 3]")
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("did not answer in a form",
                      str(caught.exception))

    def test_missing_tag_is_plain(self):
        opener = stub_urlopen(json.dumps({"html_url": "x"}))
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("did not name a version",
                      str(caught.exception))

    def test_unparsable_tag_is_plain(self):
        opener = stub_urlopen(release_json("release-candidate"))
        with self.assertRaises(PixelForgeError) as caught:
            fetch_latest_release(urlopen=opener)
        self.assertIn("did not look like a version",
                      str(caught.exception))


class CheckForUpdateTests(unittest.TestCase):
    def test_newer_release(self):
        opener = stub_urlopen(release_json("v0.70"))
        result = check_for_update("0.69", urlopen=opener)
        self.assertEqual(result["state"], "newer")
        self.assertEqual(result["latest"], "0.70")
        self.assertIn("releases/tag/v0.70", result["url"])

    def test_same_release_is_current(self):
        opener = stub_urlopen(release_json("v0.69"))
        result = check_for_update("0.69", urlopen=opener)
        self.assertEqual(result["state"], "current")
        self.assertEqual(result["latest"], "0.69")

    def test_local_newer_than_latest_is_current(self):
        # A dev tree ahead of the latest release is not an
        # update and not an error — just current.
        opener = stub_urlopen(release_json("v0.70"))
        result = check_for_update("0.99", urlopen=opener)
        self.assertEqual(result["state"], "current")

    def test_fetch_failure_is_unknown_with_reason(self):
        opener = stub_urlopen(
            error=urllib.error.URLError("no route to host"))
        result = check_for_update("0.69", urlopen=opener)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["latest"])
        self.assertIsNone(result["url"])
        self.assertIn("could not reach GitHub", result["reason"])

    def test_unparsable_tag_is_unknown_with_reason(self):
        opener = stub_urlopen(release_json("release-candidate"))
        result = check_for_update("0.69", urlopen=opener)
        self.assertEqual(result["state"], "unknown")
        self.assertIn("did not look like a version",
                      result["reason"])

    def test_newer_result_carries_notes_and_assets(self):
        payload = json.dumps({
            "tag_name": "v0.70",
            "html_url": "https://github.com/BROKE-N-BORED/"
                        "PIXEL-FORGE/releases/tag/v0.70",
            "body": "The grid is fixed.",
            "assets": [
                {"name": "pixel-forge-v0.70.zip",
                 "browser_download_url":
                     "https://example.test/v0.70.zip",
                 "size": 12345},
            ]})
        result = check_for_update("0.69",
                                  urlopen=stub_urlopen(payload))
        self.assertEqual(result["state"], "newer")
        self.assertEqual(result["notes"], "The grid is fixed.")
        self.assertEqual(
            result["assets"]["pixel-forge-v0.70.zip"]["size"],
            12345)


class ShortenNotesTests(unittest.TestCase):
    def test_first_three_lines(self):
        body = "One\nTwo\nThree\nFour\nFive"
        self.assertEqual(shorten_notes(body),
                         "One\nTwo\nThree")

    def test_blank_lines_skipped(self):
        body = "\nOne\n\nTwo\n\n"
        self.assertEqual(shorten_notes(body), "One\nTwo")

    def test_long_notes_truncated(self):
        body = "x" * 400
        shortened = shorten_notes(body)
        self.assertTrue(shortened.endswith("…"))
        self.assertLessEqual(len(shortened), 301)

    def test_empty_and_non_string(self):
        self.assertEqual(shorten_notes(""), "")
        self.assertEqual(shorten_notes(None), "")
        self.assertEqual(shorten_notes(42), "")


class ParseSidecarTests(unittest.TestCase):
    DIGEST = "a" * 64

    def test_standard_line_with_filename(self):
        text = "%s  pixel-forge-v0.83.zip\n" % self.DIGEST
        self.assertEqual(parse_sidecar(text), self.DIGEST)

    def test_bare_digest(self):
        self.assertEqual(parse_sidecar(self.DIGEST), self.DIGEST)

    def test_uppercase_hex_lowercased(self):
        self.assertEqual(parse_sidecar("B" * 64), "b" * 64)

    def test_leading_blank_lines_skipped(self):
        text = "\n\n%s  pixel-forge-v0.83.zip\n" % self.DIGEST
        self.assertEqual(parse_sidecar(text), self.DIGEST)

    def test_short_digest_refused(self):
        self.assertIsNone(parse_sidecar("a" * 63))

    def test_non_hex_refused(self):
        self.assertIsNone(parse_sidecar("g" * 64))

    def test_empty_refused(self):
        self.assertIsNone(parse_sidecar(""))
        self.assertIsNone(parse_sidecar("   \n  \n"))

    def test_non_string_refused(self):
        self.assertIsNone(parse_sidecar(None))


class FakeDownloadResponse:
    """A stub download result: bytes, a status, headers."""

    def __init__(self, data, status=200, headers=None):
        self._data = data
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def download_stub(data, honor_range=True, error=None):
    """A download urlopen stub serving data. Honors Range
    when asked (206 + the rest); otherwise serves the whole
    file with 200. Raises error instead when given."""
    calls = []

    def opener(request, timeout=None):
        calls.append((request, timeout))
        if error is not None:
            raise error
        range_header = None
        if hasattr(request, "get_header"):
            range_header = request.get_header("Range")
        if range_header and honor_range:
            start = int(range_header.split("=")[1].split("-")[0])
            rest = data[start:]
            return FakeDownloadResponse(
                rest, status=206,
                headers={"Content-Length": str(len(rest))})
        return FakeDownloadResponse(
            data, status=200,
            headers={"Content-Length": str(len(data))})

    opener.calls = calls
    return opener


class DownloadTests(unittest.TestCase):
    URL = "https://example.test/pixel-forge-v0.83.zip"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dest = Path(self.tmp) / "pixel-forge-v0.83.zip"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_download(self):
        data = b"x" * 1000
        seen = []
        got = download_file(
            self.URL, self.dest, expected_size=len(data),
            progress=lambda d, t: seen.append((d, t)),
            urlopen=download_stub(data))
        self.assertEqual(got, 1000)
        self.assertEqual(self.dest.read_bytes(), data)
        self.assertEqual(seen[-1], (1000, 1000))

    def test_resume_from_partial(self):
        data = b"y" * 5000
        self.dest.write_bytes(data[:2000])
        opener = download_stub(data)
        got = download_file(
            self.URL, self.dest, expected_size=len(data),
            urlopen=opener)
        self.assertEqual(got, 5000)
        self.assertEqual(self.dest.read_bytes(), data)
        request = opener.calls[0][0]
        self.assertEqual(request.get_header("Range"),
                         "bytes=2000-")

    def test_server_ignoring_range_restarts(self):
        data = b"z" * 3000
        self.dest.write_bytes(b"stale bytes")
        got = download_file(
            self.URL, self.dest, expected_size=len(data),
            urlopen=download_stub(data, honor_range=False))
        self.assertEqual(got, 3000)
        self.assertEqual(self.dest.read_bytes(), data)

    def test_already_complete_skips_download(self):
        data = b"w" * 100
        self.dest.write_bytes(data)
        opener = download_stub(data)
        got = download_file(
            self.URL, self.dest, expected_size=len(data),
            urlopen=opener)
        self.assertEqual(got, 100)
        self.assertEqual(opener.calls, [])

    def test_oversized_partial_restarts(self):
        data = b"v" * 100
        self.dest.write_bytes(b"q" * 500)
        got = download_file(
            self.URL, self.dest, expected_size=len(data),
            urlopen=download_stub(data))
        self.assertEqual(got, 100)
        self.assertEqual(self.dest.read_bytes(), data)

    def test_cancel_mid_download_keeps_partial(self):
        data = b"c" * (DOWNLOAD_CHUNK_SIZE * 3)
        event = threading.Event()
        seen = []

        def progress(done, total):
            seen.append(done)
            if done > 0:
                event.set()

        with self.assertRaises(DownloadCancelled):
            download_file(
                self.URL, self.dest,
                expected_size=len(data), cancel_event=event,
                progress=progress, urlopen=download_stub(data))
        # One chunk landed before the cancel was seen; the
        # partial file stays for the next try to resume.
        self.assertEqual(self.dest.stat().st_size,
                         DOWNLOAD_CHUNK_SIZE)
        self.assertEqual(self.dest.read_bytes(),
                         data[:DOWNLOAD_CHUNK_SIZE])

    def test_cancel_before_start_downloads_nothing(self):
        data = b"c" * 100
        event = threading.Event()
        event.set()
        opener = download_stub(data)
        with self.assertRaises(DownloadCancelled):
            download_file(
                self.URL, self.dest, expected_size=len(data),
                cancel_event=event, urlopen=opener)
        self.assertEqual(opener.calls, [])
        self.assertFalse(self.dest.exists())

    def test_truncated_download_refused(self):
        data = b"t" * 50
        with self.assertRaises(PixelForgeError) as caught:
            download_file(
                self.URL, self.dest, expected_size=100,
                urlopen=download_stub(data))
        self.assertIn("stopped short", str(caught.exception))

    def test_http_error_is_plain(self):
        error = urllib.error.HTTPError(
            self.URL, 404, "Not Found", {}, None)
        with self.assertRaises(PixelForgeError) as caught:
            download_file(
                self.URL, self.dest,
                urlopen=download_stub(b"", error=error))
        self.assertIn("answered 404", str(caught.exception))

    def test_range_not_satisfiable_restarts(self):
        data = b"s" * 100
        self.dest.write_bytes(b"stale")
        attempts = []

        def opener(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(
                    self.URL, 416, "Range Not Satisfiable", {},
                    None)
            return FakeDownloadResponse(
                data, status=200,
                headers={"Content-Length": str(len(data))})

        got = download_file(
            self.URL, self.dest, expected_size=len(data),
            urlopen=opener)
        self.assertEqual(got, 100)
        self.assertEqual(self.dest.read_bytes(), data)

    def test_url_error_is_plain(self):
        error = urllib.error.URLError("no route to host")
        with self.assertRaises(PixelForgeError) as caught:
            download_file(
                self.URL, self.dest,
                urlopen=download_stub(b"", error=error))
        self.assertIn("could not reach the download server",
                      str(caught.exception))


class FetchTextTests(unittest.TestCase):
    def test_success(self):
        opener = stub_urlopen("a" * 64 + "  f.zip\n")
        self.assertEqual(fetch_text("https://example.test/f.sha256",
                                    urlopen=opener),
                         "a" * 64 + "  f.zip\n")

    def test_http_error_is_plain(self):
        error = urllib.error.HTTPError(
            "https://example.test/f.sha256", 404, "Not Found", {},
            None)
        with self.assertRaises(PixelForgeError) as caught:
            fetch_text("https://example.test/f.sha256",
                       urlopen=stub_urlopen(error=error))
        self.assertIn("answered 404", str(caught.exception))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "file.zip"
        self.path.write_bytes(b"the release bytes")
        self.digest = hashlib.sha256(
            b"the release bytes").hexdigest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sha256_of(self):
        self.assertEqual(sha256_of(self.path), self.digest)

    def test_verify_match_passes(self):
        verify_zip(self.path, self.digest)

    def test_verify_mismatch_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            verify_zip(self.path, "0" * 64)
        self.assertIn("does not match the checksum",
                      str(caught.exception))

    def test_verify_missing_file_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            verify_zip(Path(self.tmp) / "nope.zip", self.digest)
        self.assertIn("missing", str(caught.exception))


def stub_runner(results):
    """A run seam for install_update: returns the next
    (returncode, stdout, stderr) per call, and records the
    commands it was given."""
    calls = []

    def run(cmd, cwd):
        calls.append((list(cmd), str(cwd)))
        return results.pop(0)

    run.calls = calls
    return run


class InstallTests(unittest.TestCase):
    VERSION = "0.99"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.zip_path = Path(self.tmp) / "pixel-forge-v0.99.zip"
        root = Path(self.tmp) / "pixel-forge-v0.99"
        (root / "pixel_forge").mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            "[project]\nname = \"pixel-forge\"\n")
        (root / "pixel_forge" / "__init__.py").write_text(
            "__version__ = \"0.99\"\n")
        with zipfile.ZipFile(self.zip_path, "w") as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(
                        Path(self.tmp)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_success_runs_pip_and_checks_version(self):
        runner = stub_runner([(0, "", ""),
                              (0, "0.99\n", "")])
        install_update(self.zip_path, self.VERSION,
                       python="/fake/venv/bin/python",
                       run=runner)
        self.assertEqual(len(runner.calls), 2)
        pip_cmd, pip_cwd = runner.calls[0]
        self.assertEqual(
            pip_cmd[:7],
            ["/fake/venv/bin/python", "-m", "pip", "install",
             "--quiet", "--no-deps", "--force-reinstall"])
        self.assertTrue(pip_cmd[7].endswith("pixel-forge-v0.99"))
        check_cmd, check_cwd = runner.calls[1]
        self.assertEqual(check_cmd[0], "/fake/venv/bin/python")
        self.assertEqual(check_cmd[1], "-c")
        self.assertIn("__version__", check_cmd[2])
        # The version check runs from an empty dir, never the
        # source tree (the v0.11 lesson).
        self.assertNotEqual(check_cwd, str(Path(self.tmp)))

    def test_pip_failure_refuses_with_tail(self):
        runner = stub_runner(
            [(1, "", "pip broke\nlast line of the error\n")])
        with self.assertRaises(PixelForgeError) as caught:
            install_update(self.zip_path, self.VERSION,
                           python="/fake/venv/bin/python",
                           run=runner)
        self.assertIn("could not be installed",
                      str(caught.exception))
        self.assertIn("last line of the error",
                      str(caught.exception))

    def test_version_mismatch_after_install_refuses(self):
        runner = stub_runner([(0, "", ""),
                              (0, "0.98\n", "")])
        with self.assertRaises(PixelForgeError) as caught:
            install_update(self.zip_path, self.VERSION,
                           python="/fake/venv/bin/python",
                           run=runner)
        self.assertIn("does not report itself as 0.99",
                      str(caught.exception))

    def test_bad_zip_refuses(self):
        bad = Path(self.tmp) / "bad.zip"
        bad.write_bytes(b"not a zip at all")
        with self.assertRaises(PixelForgeError) as caught:
            install_update(bad, self.VERSION,
                           python="/fake/venv/bin/python",
                           run=stub_runner([]))
        self.assertIn("not a usable release file",
                      str(caught.exception))

    def test_zip_without_a_release_root_refuses(self):
        plain = Path(self.tmp) / "plain.zip"
        with zipfile.ZipFile(plain, "w") as archive:
            archive.writestr("notes.txt", "nothing here")
        with self.assertRaises(PixelForgeError) as caught:
            install_update(plain, self.VERSION,
                           python="/fake/venv/bin/python",
                           run=stub_runner([]))
        self.assertIn("not a Pixel Forge release",
                      str(caught.exception))

    def test_missing_zip_refuses(self):
        with self.assertRaises(PixelForgeError) as caught:
            install_update(Path(self.tmp) / "nope.zip",
                           self.VERSION,
                           python="/fake/venv/bin/python",
                           run=stub_runner([]))
        self.assertIn("missing", str(caught.exception))


class FindInstalledRootTests(unittest.TestCase):
    def test_site_packages_is_an_install(self):
        root = find_installed_root(
            "/home/x/.local/share/pixel-forge/venv/lib/"
            "python3.12/site-packages/pixel_forge")
        self.assertEqual(str(root),
                         "/home/x/.local/share/pixel-forge/venv/"
                         "lib/python3.12/site-packages/pixel_forge")

    def test_dist_packages_is_an_install(self):
        self.assertIsNotNone(find_installed_root(
            "/usr/lib/python3/dist-packages/pixel_forge"))

    def test_source_tree_is_not_an_install(self):
        self.assertIsNone(find_installed_root(
            "/home/x/pixel-forge/pixel_forge"))
        self.assertIsNone(find_installed_root(
            "/home/x/workspace/sprite-engine/pixel_forge"))


class RestartTests(unittest.TestCase):
    def test_restart_replays_the_command_line(self):
        seen = []
        restart_program(starter=seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0],
                         [sys.executable, sys.argv[0]]
                         + sys.argv[1:])

    def test_restart_without_argv_refuses(self):
        with mock.patch.object(sys, "argv", []):
            with self.assertRaises(PixelForgeError) as caught:
                restart_program(starter=lambda cmd: None)
        self.assertIn("could not restart itself",
                      str(caught.exception))


if __name__ == "__main__":
    unittest.main()
