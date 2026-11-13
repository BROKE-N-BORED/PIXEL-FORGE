"""Preset tests — built-ins, user presets, validation.

Qt-free (task 64): runs without PySide6."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pixel_forge.pixelize import PixelForgeError  # noqa: E402
from pixel_forge.presets import (BUILTIN_PRESETS, PRESET_EXPORT_FORMAT,  # noqa: E402
                                 PresetStore, read_preset_export,
                                 validate_settings,
                                 write_preset_export)


GOOD = {"width": 32, "height": 48, "keep_aspect": True,
        "palette": "pico8", "max_colors": 8, "dither": False}

GOOD_VIDEO = dict(GOOD, extract_fps=4, preview_fps=12,
                  sheet_fps=8, sheet_cols=4, sheet_padding=2)


class ValidateTests(unittest.TestCase):
    def test_good_settings_pass_through(self):
        self.assertEqual(validate_settings(dict(GOOD)), GOOD)

    def test_missing_key_rejected(self):
        bad = dict(GOOD)
        del bad["dither"]
        with self.assertRaises(PixelForgeError):
            validate_settings(bad)

    def test_bad_size_rejected(self):
        for width, height in ((0, 32), (32, 0), (513, 32),
                              (32.5, 32), ("32", 32)):
            bad = dict(GOOD, width=width, height=height)
            with self.assertRaises(PixelForgeError):
                validate_settings(bad)

    def test_bad_palette_rejected(self):
        # A palette must be a non-empty string — but any name is
        # allowed, since a preset may name a user palette
        # (task 39). Existence is checked where it is used.
        for palette in ("", "   ", None, 42):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, palette=palette))

    def test_user_palette_name_accepted(self):
        settings = validate_settings(
            dict(GOOD, palette="My Sunset"))
        self.assertEqual(settings["palette"], "My Sunset")

    def test_bad_colors_rejected(self):
        for colors in (1, 257, 8.5):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, max_colors=colors))

    def test_bad_booleans_rejected(self):
        with self.assertRaises(PixelForgeError):
            validate_settings(dict(GOOD, dither=1))
        with self.assertRaises(PixelForgeError):
            validate_settings(dict(GOOD, keep_aspect="yes"))

    def test_video_fields_pass_through_when_present(self):
        self.assertEqual(validate_settings(dict(GOOD_VIDEO)),
                         GOOD_VIDEO)

    def test_video_fields_optional_and_independent(self):
        # One video field alone is fine; the rest stay absent.
        clean = validate_settings(dict(GOOD, sheet_fps=12))
        self.assertEqual(clean["sheet_fps"], 12)
        self.assertNotIn("extract_fps", clean)
        self.assertNotIn("preview_fps", clean)
        self.assertNotIn("sheet_cols", clean)
        self.assertNotIn("sheet_padding", clean)

    def test_sheet_cols_none_means_auto(self):
        clean = validate_settings(dict(GOOD, sheet_cols=None))
        self.assertIsNone(clean["sheet_cols"])

    def test_bad_video_fields_rejected(self):
        # extract fps must be one of the extraction combo values.
        for fps in (0, 1, 3, 7, 30, 8.5, "8", True):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, extract_fps=fps))
        # preview/sheet fps are whole numbers from 1 to 30.
        for fps in (0, -1, 31, 8.5, "12", True):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, preview_fps=fps))
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, sheet_fps=fps))
        # sheet columns is Auto or one of the columns combo values.
        for cols in (1, 7, 9, 17, 4.5, "auto", True):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, sheet_cols=cols))
        # sheet padding is a whole number from 0 to 32.
        for padding in (-1, 33, 1.5, "2", True):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, sheet_padding=padding))

    def test_unknown_keys_still_dropped(self):
        clean = validate_settings(dict(GOOD, surprise="nope"))
        self.assertNotIn("surprise", clean)


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = PresetStore(os.path.join(self.tmp, "lib"))

    def test_builtins_present_with_exact_settings(self):
        self.assertEqual(BUILTIN_PRESETS["Game Boy 32"],
                         {"width": 32, "height": 32,
                          "keep_aspect": False, "palette": "gameboy",
                          "max_colors": 4, "dither": False})
        self.assertEqual(BUILTIN_PRESETS["PICO-8 64"]["palette"],
                         "pico8")
        self.assertEqual(BUILTIN_PRESETS["Detailed 64"]["palette"],
                         "image")
        # Task 23: five more built-ins — a 16x16, a 128x128,
        # two non-square, and a Game Boy at 64.
        self.assertEqual(BUILTIN_PRESETS["Tiny 16"],
                         {"width": 16, "height": 16,
                          "keep_aspect": False, "palette": "pico8",
                          "max_colors": 16, "dither": True})
        self.assertEqual(BUILTIN_PRESETS["Large 128"],
                         {"width": 128, "height": 128,
                          "keep_aspect": False, "palette": "image",
                          "max_colors": 16, "dither": True})
        self.assertEqual(BUILTIN_PRESETS["Tall 32x64"],
                         {"width": 32, "height": 64,
                          "keep_aspect": False, "palette": "pico8",
                          "max_colors": 16, "dither": True})
        self.assertEqual(BUILTIN_PRESETS["Wide 64x32"],
                         {"width": 64, "height": 32,
                          "keep_aspect": False, "palette": "pico8",
                          "max_colors": 16, "dither": True})
        self.assertEqual(BUILTIN_PRESETS["Game Boy 64"],
                         {"width": 64, "height": 64,
                          "keep_aspect": False, "palette": "gameboy",
                          "max_colors": 4, "dither": False})
        found = self.store.all_presets()
        self.assertEqual([name for name, _s, b in found],
                         ["Game Boy 32", "PICO-8 64", "Detailed 64",
                          "Tiny 16", "Large 128", "Tall 32x64",
                          "Wide 64x32", "Game Boy 64"])
        self.assertTrue(all(b for _n, _s, b in found))
        self.assertEqual(self.store.user_presets(), {})

    def test_builtins_validate_clean(self):
        # Every built-in passes its own validation — a typo in
        # the table would surface here, not in a user's picker.
        for name, settings in BUILTIN_PRESETS.items():
            self.assertEqual(validate_settings(dict(settings)),
                             settings, name)

    def test_save_with_video_fields_round_trips(self):
        self.store.save("Video Look", dict(GOOD_VIDEO))
        self.assertEqual(self.store.get("Video Look"), GOOD_VIDEO)
        fresh = PresetStore(os.path.join(self.tmp, "lib"))
        self.assertEqual(fresh.get("Video Look"), GOOD_VIDEO)

    def test_bad_video_field_in_file_skips_only_that_preset(self):
        # Task 61 fail-soft, extended to the video fields: one
        # bad video field skips its preset with a reason and
        # leaves the good ones alone.
        os.makedirs(os.path.join(self.tmp, "lib"), exist_ok=True)
        with open(os.path.join(self.tmp, "lib", "presets.json"),
                  "w") as handle:
            json.dump({"format": "pixel-forge-presets",
                       "version": 1,
                       "presets": {"Good One": GOOD,
                                   "Bad Video": dict(
                                       GOOD, extract_fps=7),
                                   "Good Two": dict(GOOD,
                                                    width=16)}},
                      handle)
        self.assertEqual(
            sorted(self.store.user_presets()),
            ["Good One", "Good Two"])
        skipped = self.store.skipped_presets()
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], "Bad Video")
        self.assertIn("extract fps", skipped[0][1])

    def test_save_and_get_round_trip(self):
        self.store.save("My Look", GOOD)
        self.assertEqual(self.store.get("My Look"), GOOD)
        self.assertEqual(self.store.get("My Look", builtin=True),
                         None)
        path = os.path.join(self.tmp, "lib", "presets.json")
        with open(path) as handle:
            data = json.load(handle)
        self.assertEqual(data["format"], "pixel-forge-presets")
        self.assertEqual(data["presets"]["My Look"], GOOD)

    def test_all_presets_lists_builtins_then_user_sorted(self):
        self.store.save("Zebra", GOOD)
        self.store.save("Apple", GOOD)
        found = self.store.all_presets()
        expected = [(name, True) for name in BUILTIN_PRESETS]
        expected.extend([("Apple", False), ("Zebra", False)])
        self.assertEqual(
            [(name, builtin) for name, _s, builtin in found],
            expected)

    def test_user_preset_may_share_a_builtin_name(self):
        self.store.save("Game Boy 32", GOOD)
        found = self.store.all_presets()
        gb = [(name, builtin) for name, _s, builtin in found
              if name == "Game Boy 32"]
        self.assertEqual(gb, [("Game Boy 32", True),
                              ("Game Boy 32", False)])
        # The built-in slot is untouched.
        self.assertEqual(
            self.store.get("Game Boy 32", builtin=True),
            BUILTIN_PRESETS["Game Boy 32"])
        self.assertEqual(
            self.store.get("Game Boy 32", builtin=False), GOOD)

    def test_save_overwrites_same_name_user_preset(self):
        self.store.save("Look", GOOD)
        other = dict(GOOD, width=64)
        self.store.save("Look", other)
        self.assertEqual(self.store.get("Look"), other)
        self.assertEqual(len(self.store.user_presets()), 1)

    def test_save_rejects_blank_name_and_bad_settings(self):
        with self.assertRaises(PixelForgeError):
            self.store.save("   ", GOOD)
        with self.assertRaises(PixelForgeError):
            self.store.save("Bad", dict(GOOD, palette=""))
        self.assertEqual(self.store.user_presets(), {})

    def test_delete_user_preset(self):
        self.store.save("Temp", GOOD)
        self.store.delete("Temp")
        self.assertEqual(self.store.user_presets(), {})

    def test_delete_builtin_name_without_user_preset_refuses(self):
        with self.assertRaises(PixelForgeError) as ctx:
            self.store.delete("Game Boy 32")
        self.assertIn("built-in", str(ctx.exception))
        self.assertIsNotNone(
            self.store.get("Game Boy 32", builtin=True))

    def test_delete_shadowed_builtin_name_removes_user_only(self):
        self.store.save("Game Boy 32", GOOD)
        self.store.delete("Game Boy 32")
        self.assertEqual(self.store.user_presets(), {})
        self.assertEqual(
            self.store.get("Game Boy 32", builtin=True),
            BUILTIN_PRESETS["Game Boy 32"])

    def test_delete_unknown_raises(self):
        with self.assertRaises(PixelForgeError):
            self.store.delete("No Such Preset")

    def test_persistence_across_instances(self):
        self.store.save("Keep Me", GOOD)
        other = PresetStore(os.path.join(self.tmp, "lib"))
        self.assertEqual(other.get("Keep Me"), GOOD)

    def test_corrupt_presets_file_raises_readably(self):
        lib_dir = os.path.join(self.tmp, "lib")
        os.makedirs(lib_dir)
        with open(os.path.join(lib_dir, "presets.json"), "w") as f:
            f.write("{not json")
        with self.assertRaises(PixelForgeError) as ctx:
            PresetStore(lib_dir).all_presets()
        self.assertIn("unreadable", str(ctx.exception))

    def _write_presets(self, presets):
        """A presets.json holding exactly `presets` (a name ->
        settings dict, kept in order). Returns the library dir."""
        lib_dir = os.path.join(self.tmp, "lib")
        os.makedirs(lib_dir, exist_ok=True)
        with open(os.path.join(lib_dir, "presets.json"), "w") as f:
            json.dump({"format": "pixel-forge-presets",
                       "version": 1, "presets": presets}, f, indent=2)
        return lib_dir

    def test_one_bad_preset_skipped_good_ones_load(self):
        # Task 61: one bad entry used to drop EVERY user preset.
        lib_dir = self._write_presets(
            {"Apple": dict(GOOD), "Broken": dict(GOOD, width=0),
             "Zebra": dict(GOOD, width=64)})
        store = PresetStore(lib_dir)
        self.assertEqual(sorted(store.user_presets()),
                         ["Apple", "Zebra"])
        self.assertEqual(store.get("Apple"), GOOD)
        skipped = store.skipped_presets()
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], "Broken")
        self.assertIn("size", skipped[0][1])
        # The built-ins still list ahead of the good user presets.
        expected = [(name, True) for name in BUILTIN_PRESETS]
        expected.extend([("Apple", False), ("Zebra", False)])
        self.assertEqual(
            [(name, builtin)
             for name, _s, builtin in store.all_presets()],
            expected)

    def test_all_bad_presets_fall_back_to_builtins(self):
        lib_dir = self._write_presets(
            {"Broken": dict(GOOD, width=0),
             "Also Broken": dict(GOOD, palette="")})
        store = PresetStore(lib_dir)
        self.assertEqual(store.user_presets(), {})
        self.assertEqual([name for name, _r in store.skipped_presets()],
                         ["Broken", "Also Broken"])
        self.assertEqual([name for name, _s, _b in store.all_presets()],
                         list(BUILTIN_PRESETS))

    def test_save_preserves_skipped_entry_verbatim(self):
        bad = dict(GOOD, width=0)
        lib_dir = self._write_presets(
            {"Apple": dict(GOOD), "Broken": bad,
             "Zebra": dict(GOOD, width=64)})
        path = os.path.join(lib_dir, "presets.json")
        with open(path, "rb") as handle:
            before = handle.read()
        store = PresetStore(lib_dir)
        store.user_presets()
        # Loading alone never rewrites the file.
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        # And a later save keeps the bad entry verbatim, in its
        # original position — nothing lost.
        store.save("Newcomer", dict(GOOD, width=16))
        with open(path) as f:
            saved = json.load(f)["presets"]
        self.assertEqual(list(saved),
                         ["Apple", "Broken", "Zebra", "Newcomer"])
        self.assertEqual(saved["Broken"], bad)
        self.assertEqual(saved["Newcomer"], dict(GOOD, width=16))
        # The skip survives the save, still reported.
        self.assertEqual([name for name, _r in store.skipped_presets()],
                         ["Broken"])

    def test_save_same_name_as_skipped_replaces_it(self):
        lib_dir = self._write_presets(
            {"Broken": dict(GOOD, width=0)})
        store = PresetStore(lib_dir)
        self.assertEqual(len(store.skipped_presets()), 1)
        store.save("Broken", dict(GOOD, width=16))
        self.assertEqual(store.skipped_presets(), [])
        self.assertEqual(store.get("Broken"), dict(GOOD, width=16))
        with open(os.path.join(lib_dir, "presets.json")) as f:
            saved = json.load(f)["presets"]
        self.assertEqual(saved["Broken"], dict(GOOD, width=16))


class ExportImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = PresetStore(os.path.join(self.tmp, "lib"))
        self.file = os.path.join(self.tmp, "shared.json")

    def test_export_writes_valid_file(self):
        self.store.export_preset("PICO-8 64", self.file,
                                 builtin=True)
        with open(self.file) as handle:
            data = json.load(handle)
        self.assertEqual(data["format"], PRESET_EXPORT_FORMAT)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["name"], "PICO-8 64")
        self.assertEqual(data["settings"],
                         BUILTIN_PRESETS["PICO-8 64"])

    def test_export_unknown_name_fails_plainly(self):
        with self.assertRaises(PixelForgeError) as caught:
            self.store.export_preset("No Such Preset", self.file)
        self.assertIn("no preset named", str(caught.exception))
        self.assertFalse(os.path.exists(self.file))

    def test_export_import_round_trip_identical(self):
        self.store.save("Video Look", dict(GOOD_VIDEO))
        self.store.export_preset("Video Look", self.file)
        other = PresetStore(os.path.join(self.tmp, "other-lib"))
        name = other.import_preset(self.file)
        self.assertEqual(name, "Video Look")
        self.assertEqual(other.get("Video Look"), GOOD_VIDEO)

    def test_import_shadowing_a_builtin_becomes_custom(self):
        write_preset_export(self.file, "Game Boy 32",
                            dict(GOOD, width=16))
        name = self.store.import_preset(self.file)
        self.assertEqual(name, "Game Boy 32")
        # The user slot holds the import; the built-in is intact.
        self.assertEqual(self.store.get("Game Boy 32"),
                         dict(GOOD, width=16))
        self.assertEqual(
            self.store.get("Game Boy 32", builtin=True),
            BUILTIN_PRESETS["Game Boy 32"])

    def test_import_clash_refuses_without_overwrite(self):
        self.store.save("Mine", dict(GOOD))
        write_preset_export(self.file, "Mine",
                            dict(GOOD, width=16))
        with self.assertRaises(PixelForgeError) as caught:
            self.store.import_preset(self.file)
        self.assertIn("already exists", str(caught.exception))
        # The store is untouched — the old preset survives.
        self.assertEqual(self.store.get("Mine"), GOOD)

    def test_import_clash_with_overwrite_replaces(self):
        self.store.save("Mine", dict(GOOD))
        write_preset_export(self.file, "Mine",
                            dict(GOOD, width=16))
        self.store.import_preset(self.file, overwrite=True)
        self.assertEqual(self.store.get("Mine"),
                         dict(GOOD, width=16))

    def test_read_refuses_bad_files_without_touching_store(self):
        cases = {
            "not json": "{oops",
            "wrong format": json.dumps(
                {"format": "pixel-forge-presets", "version": 1,
                 "name": "X", "settings": GOOD}),
            "no format": json.dumps(
                {"name": "X", "settings": GOOD}),
            "no name": json.dumps(
                {"format": PRESET_EXPORT_FORMAT, "version": 1,
                 "settings": GOOD}),
            "blank name": json.dumps(
                {"format": PRESET_EXPORT_FORMAT, "version": 1,
                 "name": "   ", "settings": GOOD}),
            "bad settings": json.dumps(
                {"format": PRESET_EXPORT_FORMAT, "version": 1,
                 "name": "X", "settings": dict(GOOD, width=0)}),
            "bad video field": json.dumps(
                {"format": PRESET_EXPORT_FORMAT, "version": 1,
                 "name": "X",
                 "settings": dict(GOOD, extract_fps=7)}),
            "not an object": json.dumps(["a", "list"]),
        }
        for label, content in cases.items():
            path = os.path.join(self.tmp, "bad.json")
            with open(path, "w") as handle:
                handle.write(content)
            with self.assertRaises(PixelForgeError, msg=label):
                read_preset_export(path)
            with self.assertRaises(PixelForgeError, msg=label):
                self.store.import_preset(path)
        self.assertEqual(self.store.user_presets(), {})
        self.assertFalse(
            os.path.exists(self.store.presets_path))

    def test_read_missing_file_fails_plainly(self):
        with self.assertRaises(PixelForgeError) as caught:
            read_preset_export(os.path.join(self.tmp, "gone.json"))
        self.assertIn("cannot read preset file",
                      str(caught.exception))


if __name__ == "__main__":
    unittest.main()
