"""Polish pass tests (task 22) — no Qt needed.

The contrast ratio is computed from the stylesheet colors parsed
out of gui_shared.py's source (the stylesheets moved there when
gui.py was split by tab in task 58), and the "(s)" ban is checked
by tokenizing every module in the package. Both run on bare python.
"""
import io
import os
import re
import sys
import tokenize
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..")
GUI_SHARED_PATH = os.path.join(ROOT, "pixel_forge", "gui_shared.py")
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


def _stylesheet(name="DARK_STYLESHEET"):
    """A stylesheet, parsed out of gui_shared.py's source."""
    with open(GUI_SHARED_PATH, encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(r'%s = """(.*?)"""' % name,
                      source, re.DOTALL)
    assert match is not None, "%s not found in gui_shared.py" % name
    return match.group(1)


def _selectors(sheet):
    """The selector set a stylesheet styles, one per rule."""
    found = set()
    for match in re.finditer(r'^([A-Za-z][^\{]*)\{',
                             sheet, re.MULTILINE):
        found.add(match.group(1).strip())
    return found


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

    def test_warning_heading_and_status_meet_wcag_aa(self):
        # Task 67: the warning color (preset warnings, the
        # over-limit prompt counter) and the headings live in
        # the stylesheets now, so they are measured like the
        # export buttons — against the background they sit on.
        sheet = _stylesheet()
        bg = re.search(
            r'QMainWindow, QWidget \{ background-color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        heading = re.search(
            r'QLabel#heading \{[^}]*color: (#[0-9a-f]{6});',
            sheet).group(1)
        self.assertGreaterEqual(contrast_ratio(heading, bg), 4.5)
        warning = re.search(
            r'QLabel#warning \{ color: (#[0-9a-f]{6});',
            sheet).group(1)
        self.assertGreaterEqual(contrast_ratio(warning, bg), 4.5)
        over = re.search(
            r'QLabel\[overLimit="true"\] \{ color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        self.assertEqual(over, warning,
                         "the over-limit counter must use the "
                         "warning color")
        status = re.search(
            r'QStatusBar \{ background-color: (#[0-9a-f]{6}); '
            r'color: (#[0-9a-f]{6});', sheet)
        self.assertGreaterEqual(
            contrast_ratio(status.group(2), status.group(1)), 4.5)

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


class LightThemeContrastTests(unittest.TestCase):
    def test_light_theme_meets_wcag_aa(self):
        # Task 67: the light theme must meet the same bar as
        # task 22's polish pass — WCAG AA 4.5:1 for normal
        # text. The colors are parsed out of LIGHT_STYLESHEET
        # itself, so a future color change re-measures here.
        sheet = _stylesheet("LIGHT_STYLESHEET")
        main = re.search(
            r'QMainWindow, QWidget \{ background-color: '
            r'(#[0-9a-f]{6}); color: (#[0-9a-f]{6});',
            sheet)
        bg, text = main.group(1), main.group(2)
        pairs = [("text", text, "background", bg)]
        button = re.search(
            r'QPushButton \{ background-color: (#[0-9a-f]{6});',
            sheet).group(1)
        pairs.append(("text", text, "button", button))
        hover = re.search(
            r'QPushButton:hover \{ background-color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        pairs.append(("text", text, "button hover", hover))
        # The export button carries its own light text color —
        # the main text color is dark now and would vanish on
        # the orange. Read the color from the export rule itself.
        export_rule = re.search(
            r'QPushButton#export \{ background-color: '
            r'(#[0-9a-f]{6});[^}]*color: (#[0-9a-f]{6});',
            sheet)
        export_text = export_rule.group(2)
        pairs.append(("export text", export_text,
                      "export", export_rule.group(1)))
        export_hover = re.search(
            r'QPushButton#export:hover \{ background-color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        pairs.append(("export text", export_text,
                      "export hover", export_hover))
        heading = re.search(
            r'QLabel#heading \{[^}]*color: (#[0-9a-f]{6});',
            sheet).group(1)
        pairs.append(("heading", heading, "background", bg))
        warning = re.search(
            r'QLabel#warning \{ color: (#[0-9a-f]{6});',
            sheet).group(1)
        pairs.append(("warning", warning, "background", bg))
        status = re.search(
            r'QStatusBar \{ background-color: (#[0-9a-f]{6}); '
            r'color: (#[0-9a-f]{6});', sheet)
        pairs.append(("status text", status.group(2),
                      "status bar", status.group(1)))
        field = re.search(
            r'QComboBox, QSpinBox, QLineEdit \{ background-color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        pairs.append(("text", text, "input", field))
        list_bg = re.search(
            r'QListWidget \{ background-color: (#[0-9a-f]{6});',
            sheet).group(1)
        pairs.append(("text", text, "list", list_bg))
        selected = re.search(
            r'QListWidget::item:selected \{ background-color: '
            r'(#[0-9a-f]{6});', sheet).group(1)
        pairs.append(("text", text, "selected item", selected))
        for fg_name, fg, bg_name, bg_color in pairs:
            self.assertGreaterEqual(
                contrast_ratio(fg, bg_color), 4.5,
                "light %s %s on %s %s is %.2f:1, under WCAG AA "
                "4.5:1" % (fg_name, fg, bg_name, bg_color,
                           contrast_ratio(fg, bg_color)))

    def test_no_comment_lines_inside_light_stylesheet(self):
        # Same rule as the dark sheet: a # line inside the
        # string is parsed as a selector.
        for line in _stylesheet("LIGHT_STYLESHEET").splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith("# "),
                "comment line inside LIGHT_STYLESHEET: %r" % line)

    def test_both_themes_cover_the_same_selectors(self):
        # A rule forgotten in one theme falls back to the
        # default style in that theme — usually unreadable.
        # The two sheets must style the same selector set.
        dark = _selectors(_stylesheet())
        light = _selectors(_stylesheet("LIGHT_STYLESHEET"))
        self.assertEqual(dark, light,
                         "dark-only: %s; light-only: %s"
                         % (sorted(dark - light),
                            sorted(light - dark)))


class IdentityTests(unittest.TestCase):
    """Task 88 — the pixel-native identity, pinned in the strings."""

    def test_square_corners_everywhere(self):
        # Pixel-native means square: every border-radius in both
        # themes is 0. A radius that creeps back in is a
        # regression, not a preference.
        for name in ("DARK_STYLESHEET", "LIGHT_STYLESHEET"):
            sheet = _stylesheet(name)
            radii = re.findall(r'border-radius: ([^;]+);', sheet)
            self.assertTrue(radii,
                            "%s styles no radius at all" % name)
            for radius in radii:
                self.assertIn(radius.strip(), ("0", "0px"),
                              "%s has a rounded corner: %s"
                              % (name, radius))

    def test_accent_present_in_both_themes(self):
        # One accent per theme: dark #e07020 for shapes and
        # borders, light #8a4513 throughout. It must show up in
        # the slider handle, the checked border, the selected
        # tab bar, and the ticked checkbox — the same accent in
        # every place, not a different orange each time.
        accents = {"DARK_STYLESHEET": "#e07020",
                   "LIGHT_STYLESHEET": "#8a4513"}
        rules = ("QSlider::handle:horizontal",
                 "QPushButton:checked",
                 "QTabBar::tab:selected",
                 "QCheckBox::indicator:checked")
        for name, accent in accents.items():
            sheet = _stylesheet(name)
            for selector in rules:
                rule = re.search(
                    r'%s \{[^}]*\}' % re.escape(selector), sheet)
                self.assertIsNotNone(
                    rule, "%s has no %s rule" % (name, selector))
                self.assertIn(
                    accent, rule.group(0),
                    "%s %s does not carry the accent %s"
                    % (name, selector, accent))

    def test_identity_selectors_present_in_both_themes(self):
        # The identity styles the tabs, menus, tooltips,
        # checkboxes, progress bars, and text edits — in both
        # themes, not just the dark one.
        wanted = ("QTabBar::tab", "QTabBar::tab:hover",
                  "QTabBar::tab:selected", "QTabWidget::pane",
                  "QMenuBar", "QMenuBar::item:selected", "QMenu",
                  "QMenu::item:selected", "QToolTip",
                  "QCheckBox::indicator",
                  "QCheckBox::indicator:checked", "QProgressBar",
                  "QProgressBar::chunk", "QDoubleSpinBox",
                  "QTextEdit")
        for name in ("DARK_STYLESHEET", "LIGHT_STYLESHEET"):
            selectors = _selectors(_stylesheet(name))
            for selector in wanted:
                self.assertIn(selector, selectors,
                              "%s does not style %s"
                              % (name, selector))

    def test_new_text_pairs_meet_wcag_aa(self):
        # The new rules carry text too — menus, tabs, tooltips,
        # progress text on its chunk. Same 4.5:1 bar as task 22.
        for name in ("DARK_STYLESHEET", "LIGHT_STYLESHEET"):
            sheet = _stylesheet(name)
            main = re.search(
                r'QMainWindow, QWidget \{ background-color: '
                r'(#[0-9a-f]{6}); color: (#[0-9a-f]{6});',
                sheet)
            text = main.group(2)
            pairs = []

            def rule_color(selector, prop="background-color"):
                rule = re.search(
                    r'%s \{[^}]*%s: (#[0-9a-f]{6});'
                    % (re.escape(selector), prop), sheet)
                self.assertIsNotNone(
                    rule, "%s has no %s %s"
                    % (name, selector, prop))
                return rule.group(1)

            menu_bg = rule_color("QMenu")
            menu_text = rule_color("QMenu", "color")
            pairs.append(("menu text", menu_text,
                          "menu", menu_bg))
            pairs.append(("menu text", menu_text, "menu selected",
                          rule_color("QMenu::item:selected")))
            pairs.append(("tab text", text, "tab",
                          rule_color("QTabBar::tab")))
            pairs.append(("tab text", text, "tab hover",
                          rule_color("QTabBar::tab:hover")))
            pairs.append(("tab text", text, "tab selected",
                          rule_color("QTabBar::tab:selected")))
            tip_bg = rule_color("QToolTip")
            tip_text = rule_color("QToolTip", "color")
            pairs.append(("tooltip text", tip_text,
                          "tooltip", tip_bg))
            bar_bg = rule_color("QMenuBar")
            bar_text = rule_color("QMenuBar", "color")
            pairs.append(("menu bar text", bar_text,
                          "menu bar", bar_bg))
            progress_text = rule_color("QProgressBar", "color")
            pairs.append(("progress text", progress_text,
                          "progress bar",
                          rule_color("QProgressBar")))
            pairs.append(("progress text", progress_text,
                          "progress chunk",
                          rule_color("QProgressBar::chunk")))
            for fg_name, fg, bg_name, bg in pairs:
                self.assertGreaterEqual(
                    contrast_ratio(fg, bg), 4.5,
                    "%s %s %s on %s %s is %.2f:1, under WCAG AA "
                    "4.5:1" % (name, fg_name, fg, bg_name, bg,
                               contrast_ratio(fg, bg)))


class ExplanationContrastTests(unittest.TestCase):
    """Task 89 — the under-field explanation lines are small
    and subdued, but they are still text a user reads, so the
    explanation color must clear the task-22 bar (WCAG AA
    4.5:1) against the main background in both themes."""

    def test_explanation_color_meets_wcag_aa(self):
        for name in ("DARK_STYLESHEET", "LIGHT_STYLESHEET"):
            sheet = _stylesheet(name)
            bg = re.search(
                r'QMainWindow, QWidget \{ background-color: '
                r'(#[0-9a-f]{6});', sheet).group(1)
            rule = re.search(
                r'QLabel#explanation \{[^}]*color: (#[0-9a-f]{6});',
                sheet)
            self.assertIsNotNone(
                rule, "%s styles no explanation color" % name)
            color = rule.group(1)
            self.assertGreaterEqual(
                contrast_ratio(color, bg), 4.5,
                "%s explanation %s on %s is %.2f:1, under WCAG "
                "AA 4.5:1" % (name, color, bg,
                              contrast_ratio(color, bg)))

    def test_explanation_rule_present_in_both_themes(self):
        # The selector-set test pins the two sheets equal, but
        # a rule in neither sheet would pass it — pin that the
        # explanation rule exists at all.
        for name in ("DARK_STYLESHEET", "LIGHT_STYLESHEET"):
            self.assertIn("QLabel#explanation",
                          _selectors(_stylesheet(name)),
                          "%s has no explanation rule" % name)


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


class GridTileTests(unittest.TestCase):
    """Task 88 (v0.81) — the pixel-grid tiles: real files in
    icons/, decoded with the project's own stdlib PNG reader,
    so the test needs no Qt and no image library."""

    TILES = {"DARK_STYLESHEET": "grid-tile-dark.png",
             "LIGHT_STYLESHEET": "grid-tile-light.png"}

    def _tile_path(self, name):
        # ROOT is tests/.. — normalize, so the path compares
        # equal to the normalized paths build_zip collects.
        return os.path.normpath(os.path.join(
            ROOT, "icons", self.TILES[name]))

    def _tile(self, name):
        from pixel_forge.pngio import decode_png
        with open(self._tile_path(name), "rb") as handle:
            return decode_png(handle.read())

    def _require_tiles(self):
        # The tiles are PNGs, so they ship in the release zip
        # and not in the git push — a fresh clone has no tiles,
        # and the tile-dependent tests skip there instead of
        # failing (same pattern as test_installer's icon skip).
        for name in self.TILES:
            path = self._tile_path(name)
            if not os.path.isfile(path):
                self.skipTest(
                    "%s not present (tiles ship in the release "
                    "zip, not the git push)" % self.TILES[name])

    @staticmethod
    def _rgb(pixel):
        """A decoded pixel (packed AARRGGBB int) as #rrggbb."""
        return "#%02x%02x%02x" % ((pixel >> 16) & 0xFF,
                                  (pixel >> 8) & 0xFF,
                                  pixel & 0xFF)

    def test_tiles_exist_and_are_16x16_transparent_grids(self):
        # One tile per theme: 16x16, transparent except the
        # 1 px grid lines along the right column and bottom
        # row, so tiling forms a continuous grid.
        self._require_tiles()
        for name in self.TILES:
            tile = self._tile(name)
            self.assertEqual(tile.width(), 16,
                             "%s is not 16 px wide" % name)
            self.assertEqual(tile.height(), 16,
                             "%s is not 16 px tall" % name)
            self.assertEqual(tile.pixel(0, 0) >> 24, 0,
                             "%s has an opaque corner" % name)
            self.assertEqual(tile.pixel(15, 0) >> 24, 0xFF,
                             "%s has no vertical grid line"
                             % name)
            self.assertEqual(tile.pixel(0, 15) >> 24, 0xFF,
                             "%s has no horizontal grid line"
                             % name)

    def test_text_on_grid_lines_meets_wcag_aa(self):
        # The grid lines pass behind text, so every text color
        # that can sit on a grid surface must clear the task-22
        # bar against the LINE color itself, not just the flat
        # background. The line color is read out of the tile.
        self._require_tiles()
        for name in self.TILES:
            sheet = _stylesheet(name)
            line = self._rgb(self._tile(name).pixel(15, 0))
            main = re.search(
                r'QMainWindow, QWidget \{ background-color: '
                r'(#[0-9a-f]{6}); color: (#[0-9a-f]{6});',
                sheet)
            pairs = [("text", main.group(2))]
            heading = re.search(
                r'QLabel#heading \{[^}]*color: (#[0-9a-f]{6});',
                sheet).group(1)
            pairs.append(("heading", heading))
            explanation = re.search(
                r'QLabel#explanation \{[^}]*color: '
                r'(#[0-9a-f]{6});', sheet).group(1)
            pairs.append(("explanation", explanation))
            for label, color in pairs:
                self.assertGreaterEqual(
                    contrast_ratio(color, line), 4.5,
                    "%s %s %s on grid line %s is %.2f:1, under "
                    "WCAG AA 4.5:1" % (name, label, color, line,
                                       contrast_ratio(color, line)))

    def test_grid_rules_present_in_both_themes(self):
        # The grid lives in three rules per theme (lists,
        # scroll viewports, tab columns) plus the transparent
        # section rules that let the column grid show through
        # the Video tab's sections. The selector-set test pins
        # the two sheets equal, but a rule in neither sheet
        # would pass it — pin that the rules exist at all.
        for name in self.TILES:
            sheet = _stylesheet(name)
            selectors = _selectors(sheet)
            for selector in ("QWidget#column", "QWidget#section",
                             "QWidget#sectionBody"):
                self.assertIn(selector, selectors,
                              "%s has no %s rule" % (name, selector))
            for selector in ("QScrollArea", "QListWidget"):
                rule = re.search(
                    r'%s \{[^}]*\}' % re.escape(selector), sheet)
                self.assertIsNotNone(
                    rule, "%s has no %s rule" % (name, selector))
                self.assertIn("background-image:",
                              rule.group(0),
                              "%s %s carries no grid"
                              % (name, selector))

    def test_grid_tile_urls_are_placeholders_in_source(self):
        # Qt resolves stylesheet url() paths against the
        # working directory, so a relative tile path fails
        # with "Could not create pixmap" whenever the app
        # starts from another folder — the v0.79 failure. The
        # source therefore carries a placeholder that the
        # module replaces with the ABSOLUTE tile path at
        # import time (pinned evaluated in test_gui).
        for name, placeholder in (
                ("DARK_STYLESHEET", "__GRID_TILE_DARK__"),
                ("LIGHT_STYLESHEET", "__GRID_TILE_LIGHT__")):
            sheet = _stylesheet(name)
            self.assertIn(placeholder, sheet,
                          "%s has no tile placeholder" % name)
            self.assertNotIn("grid-tile-", sheet.replace(
                placeholder, ""),
                "%s names a tile file directly — the path "
                "must come from the import-time placeholder"
                % name)

    def test_grid_tiles_ship_in_the_zip(self):
        # The tiles are PNGs, so they are not in the git push
        # (the manifest excludes *.png) — but they MUST be in
        # the release zip, or the installed app has no grid.
        self._require_tiles()
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_zip",
            os.path.join(ROOT, "tools", "build_zip.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        collected = module.collect()
        for name in self.TILES:
            self.assertIn(self._tile_path(name), collected,
                          "%s is not collected by build_zip.py"
                          % self.TILES[name])


if __name__ == "__main__":
    unittest.main()
