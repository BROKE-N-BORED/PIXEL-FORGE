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
from pixel_forge.presets import (BUILTIN_PRESETS, PresetStore,  # noqa: E402
                                 validate_settings)


GOOD = {"width": 32, "height": 48, "keep_aspect": True,
        "palette": "pico8", "max_colors": 8, "dither": False}


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
        with self.assertRaises(PixelForgeError):
            validate_settings(dict(GOOD, palette="sepia"))

    def test_bad_colors_rejected(self):
        for colors in (1, 257, 8.5):
            with self.assertRaises(PixelForgeError):
                validate_settings(dict(GOOD, max_colors=colors))

    def test_bad_booleans_rejected(self):
        with self.assertRaises(PixelForgeError):
            validate_settings(dict(GOOD, dither=1))
        with self.assertRaises(PixelForgeError):
            validate_settings(dict(GOOD, keep_aspect="yes"))


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
        found = self.store.all_presets()
        self.assertEqual([name for name, _s, b in found],
                         ["Game Boy 32", "PICO-8 64", "Detailed 64"])
        self.assertTrue(all(b for _n, _s, b in found))
        self.assertEqual(self.store.user_presets(), {})

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
        self.assertEqual(
            [(name, builtin) for name, _s, builtin in found],
            [("Game Boy 32", True), ("PICO-8 64", True),
             ("Detailed 64", True), ("Apple", False),
             ("Zebra", False)])

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
            self.store.save("Bad", dict(GOOD, palette="sepia"))
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
        self.assertEqual(
            [(name, builtin)
             for name, _s, builtin in store.all_presets()],
            [("Game Boy 32", True), ("PICO-8 64", True),
             ("Detailed 64", True), ("Apple", False),
             ("Zebra", False)])

    def test_all_bad_presets_fall_back_to_builtins(self):
        lib_dir = self._write_presets(
            {"Broken": dict(GOOD, width=0),
             "Also Broken": dict(GOOD, palette="sepia")})
        store = PresetStore(lib_dir)
        self.assertEqual(store.user_presets(), {})
        self.assertEqual([name for name, _r in store.skipped_presets()],
                         ["Broken", "Also Broken"])
        self.assertEqual([name for name, _s, _b in store.all_presets()],
                         ["Game Boy 32", "PICO-8 64", "Detailed 64"])

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


if __name__ == "__main__":
    unittest.main()
