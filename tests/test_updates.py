"""Tests for the opt-in update check (task 48).

No test here touches the real network: every fetch goes
through a stub urlopen. The GUI tests cover the menu, the
setting, and the dialogs; these cover the version math and
the fetch's failure paths.
"""

import json
import unittest
import urllib.error

from pixel_forge.pixelize import PixelForgeError
from pixel_forge.updates import (CHECK_TIMEOUT_S, LATEST_RELEASE_URL,
                                 RELEASES_PAGE_URL, check_for_update,
                                 fetch_latest_release, is_newer,
                                 parse_version)


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
                    "PIXEL-FORGE/releases/tag/v0.70"})

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


if __name__ == "__main__":
    unittest.main()
