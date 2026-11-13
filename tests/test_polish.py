"""Polish pass tests (task 22) — no Qt needed.

The contrast ratio is computed from the stylesheet colors parsed
out of gui.py's source, and the "(s)" ban is checked by tokenizing
every module in the package. Both run on bare python.
"""
import io
import os
import re
import sys
import tokenize
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..")
GUI_PATH = os.path.join(ROOT, "pixel_forge", "gui.py")
PACKAGE = os.path.join(ROOT, "pixel_forge")


def _luminance(hex_color):
    """WCAG relative luminance of a #rrggbb color."""
    rgb = [int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928
              else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg, bg):
    """WCAG contrast ratio between two #rrggbb colors."""
    light = _luminance(fg)
    dark = _luminance(bg)
    if light < dark:
        light, dark = dark, light
    return (light + 0.05) / (dark + 0.05)


def _stylesheet():
    """DARK_STYLESHEET, parsed out of gui.py's source."""
    with open(GUI_PATH, encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(r'DARK_STYLESHEET = """(.*?)"""',
                      source, re.DOTALL)
    assert match is not None, "DARK_STYLESHEET not found in gui.py"
    return match.group(1)


class ContrastTests(unittest.TestCase):
    def test_export_buttons_meet_wcag_aa(self):
        # Review W19: the export buttons' text measured 3.84:1
        # against their background — under WCAG AA's 4.5:1 for
        # normal text. The colors are parsed out of the stylesheet
        # itself, so a future color change re-measures here.
        sheet = _stylesheet()
        text = re.search(
            r'QMainWindow, QWidget \{[^}]*; color: (#[0-9a-f]{6});',
            sheet).group(1)
        export = re.search(
            r'QPushButton#export \{ background-color: (#[0-9a-f]{6});',
            sheet).group(1)
        hover = re.search(
            r'QPushButton#export:hover \{ background-color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        self.assertGreaterEqual(
            contrast_ratio(text, export), 4.5,
            "export text %s on %s is %.2f:1, under WCAG AA 4.5:1"
            % (text, export, contrast_ratio(text, export)))
        self.assertGreaterEqual(
            contrast_ratio(text, hover), 4.5,
            "export hover %s on %s is %.2f:1, under WCAG AA 4.5:1"
            % (text, hover, contrast_ratio(text, hover)))
        # The old failing orange must not come back.
        self.assertNotIn("#b35a1b", sheet)

    def test_no_comment_lines_inside_stylesheet(self):
        # Qt stylesheets have no # comments: a # line inside the
        # string is parsed as a selector and can swallow the rules
        # after it. (A WCAG comment was written inside the string
        # during this task and caught before shipping.) Valid
        # selectors are #export / #heading — # with no space after
        # it. A # followed by a space is a smuggled comment.
        for line in _stylesheet().splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith("# "),
                "comment line inside DARK_STYLESHEET: %r" % line)


class NoProgrammerPhrasingTests(unittest.TestCase):
    def test_no_parenthesized_s_in_package_strings(self):
        # "7 asset(s)" reads as programmer phrasing. Every string
        # literal in the package is checked by tokenizing — code
        # like int(s) is NAME tokens, not strings, so it is fine.
        offenders = []
        for name in sorted(os.listdir(PACKAGE)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(PACKAGE, name)
            with open(path, "rb") as handle:
                tokens = tokenize.tokenize(handle.readline)
                for token in tokens:
                    if (token.type == tokenize.STRING
                            and "(s)" in token.string):
                        offenders.append("%s:%d: %s"
                                         % (name, token.start[0],
                                            token.string[:60]))
        self.assertEqual(offenders, [])


class PluralTests(unittest.TestCase):
    def test_plural(self):
        from pixel_forge.pixelize import plural
        self.assertEqual(plural(1, "asset"), "1 asset")
        self.assertEqual(plural(7, "asset"), "7 assets")
        self.assertEqual(plural(0, "asset"), "0 assets")
        self.assertEqual(plural(2, "child", "children"),
                         "2 children")
        self.assertEqual(plural(1, "child", "children"),
                         "1 child")


class SourceLabelTests(unittest.TestCase):
    def test_every_source_key_has_a_display_label(self):
        # The Library tab shows display labels, not stored keys
        # ("Skin", not "skin"). Every key the library can hold
        # must have one — an unmapped key would show raw.
        from pixel_forge.library import SOURCE_LABELS, SOURCES
        for key in SOURCES:
            self.assertIn(key, SOURCE_LABELS,
                          "no display label for source %r" % key)
            self.assertNotEqual(SOURCE_LABELS[key], key)
            self.assertTrue(SOURCE_LABELS[key][:1].isupper(),
                            "label %r is not capitalized"
                            % SOURCE_LABELS[key])

    def test_every_palette_key_has_a_display_label(self):
        # PALETTE_LABELS predates this task; the Library tab only
        # started using it now, so pin its coverage too. The keys
        # are the named palettes plus "image" (from the image).
        from pixel_forge.palettes import NAMED_PALETTES, PALETTE_LABELS
        for key in set(NAMED_PALETTES) | {"image"}:
            self.assertIn(key, PALETTE_LABELS,
                          "no display label for palette %r" % key)


if __name__ == "__main__":
    unittest.main()
