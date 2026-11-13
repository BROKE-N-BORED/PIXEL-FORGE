"""Preset tests — built-ins, user presets, validation.

Skipped when PySide6 is not importable."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from pixel_forge.pixelize import PixelForgeError  # noqa: E402
    from pixel_forge.presets import (BUILTIN_PRESETS, PresetStore,  # noqa: E402
                                     validate_settings)
except ImportError as exc:
    # Task 63: every test here needs Qt, so skip the module when
    # PySide6 is what is missing — but a genuinely broken import
    # still fails loudly instead of hiding behind the skip.
    # (The preset logic itself is plain JSON — but presets.py
    # imports the Qt-only pixelize core, so the module cannot
    # even load without PySide6. Decoupling that is task 64.)
    if exc.name and exc.name.startswith("PySide6"):
        raise unittest.SkipTest("PySide6 not available")
    raise


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

    def test_invalid_stored_preset_raises_with_name(self):
        lib_dir = os.path.join(self.tmp, "lib")
        os.makedirs(lib_dir)
        with open(os.path.join(lib_dir, "presets.json"), "w") as f:
            json.dump({"format": "pixel-forge-presets",
                       "version": 1,
                       "presets": {"Broken": dict(GOOD, width=0)}},
                      f)
        with self.assertRaises(PixelForgeError) as ctx:
            PresetStore(lib_dir).all_presets()
        self.assertIn("Broken", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
