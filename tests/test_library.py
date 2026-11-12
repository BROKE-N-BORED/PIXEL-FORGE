"""Library tests — plain-file storage, index, search, tags."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import ensure_app, make_solid_image, write_image  # noqa: E402
from pixel_forge.library import (Library, default_library_dir,  # noqa: E402
                                 normalize_tags, slugify)
from pixel_forge.pixelize import PixelForgeError  # noqa: E402


class SlugTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Red Knight"), "red-knight")
        self.assertEqual(slugify("  spaced   out  "), "spaced-out")
        self.assertEqual(slugify("under_score"), "under-score")
        self.assertEqual(slugify("!!!"), "sprite")
        self.assertEqual(slugify(""), "sprite")

    def test_normalize_tags(self):
        self.assertEqual(normalize_tags("hero, red, hero ,"),
                         ["hero", "red"])
        self.assertEqual(normalize_tags(["a", " b ", "a", ""]),
                         ["a", "b"])
        self.assertEqual(normalize_tags(""), [])


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = Library(os.path.join(self.tmp, "lib"))

    def _image(self, w=32, h=32, r=255, g=0, b=0):
        return make_solid_image(w, h, r, g, b)

    def test_default_dir_honors_env(self):
        os.environ["PIXELFORGE_LIBRARY"] = "/tmp/somewhere-else"
        try:
            self.assertEqual(str(default_library_dir()),
                             "/tmp/somewhere-else")
        finally:
            del os.environ["PIXELFORGE_LIBRARY"]
        self.assertEqual(str(default_library_dir()),
                         os.path.join(os.path.expanduser("~"),
                                      "pixel-forge-library"))

    def test_empty_library_lists_nothing_and_creates_nothing(self):
        self.assertEqual(self.lib.entries(), [])
        self.assertEqual(self.lib.search(), [])
        self.assertEqual(self.lib.all_tags(), [])
        self.assertIsNone(self.lib.find("nope"))
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "lib")))

    def test_add_image_creates_file_and_index_entry(self):
        entry = self.lib.add_image(self._image(), "Red Knight",
                                   tags=["hero", "red"],
                                   source="image", palette="pico8")
        self.assertEqual(entry["name"], "Red Knight")
        self.assertEqual(entry["file"], "red-knight.png")
        self.assertEqual(entry["id"], "red-knight")
        self.assertEqual(entry["kind"], "sprite")
        self.assertEqual(entry["tags"], ["hero", "red"])
        self.assertEqual(entry["source"], "image")
        self.assertEqual(entry["palette"], "pico8")
        self.assertEqual((entry["width"], entry["height"]),
                         (32, 32))
        self.assertTrue(entry["created"])
        path = os.path.join(self.tmp, "lib", "red-knight.png")
        self.assertTrue(os.path.isfile(path))
        with open(os.path.join(self.tmp, "lib",
                               "library.json")) as handle:
            index = json.load(handle)
        self.assertEqual(index["format"], "pixel-forge-library")
        self.assertEqual(index["version"], 1)
        self.assertEqual(len(index["entries"]), 1)

    def test_same_name_twice_gets_unique_filenames(self):
        first = self.lib.add_image(self._image(), "Knight")
        second = self.lib.add_image(self._image(), "Knight")
        third = self.lib.add_image(self._image(), "Knight")
        self.assertEqual(first["file"], "knight.png")
        self.assertEqual(second["file"], "knight-2.png")
        self.assertEqual(third["file"], "knight-3.png")
        self.assertEqual(len({first["id"], second["id"],
                              third["id"]}), 3)

    def test_add_image_rejects_blank_name_and_null_image(self):
        with self.assertRaises(PixelForgeError):
            self.lib.add_image(self._image(), "   ")
        from PySide6.QtGui import QImage
        with self.assertRaises(PixelForgeError):
            self.lib.add_image(QImage(), "Empty")

    def test_add_file_copies_and_converts_to_png(self):
        src = write_image(self._image(16, 16), os.path.join(
            self.tmp, "source.png"))
        entry = self.lib.add_file(src, "From Disk", tags="disk")
        self.assertEqual(entry["file"], "from-disk.png")
        self.assertEqual(entry["source"], "file")
        self.assertEqual((entry["width"], entry["height"]),
                         (16, 16))
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "lib", "from-disk.png")))

    def test_add_file_rejects_missing_and_unreadable(self):
        with self.assertRaises(PixelForgeError):
            self.lib.add_file(os.path.join(self.tmp, "nope.png"),
                              "Nope")
        bad = os.path.join(self.tmp, "bad.png")
        with open(bad, "w") as f:
            f.write("not an image")
        with self.assertRaises(PixelForgeError):
            self.lib.add_file(bad, "Bad")

    def test_add_sheet_copies_png_and_json(self):
        png = write_image(self._image(64, 64),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as f:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "frames": []}, f)
        entry = self.lib.add_sheet(png, meta, "Walk cycle",
                                   tags=["walk"], frames=14)
        self.assertEqual(entry["kind"], "sheet")
        self.assertEqual(entry["file"], "walk-cycle.png")
        self.assertEqual(entry["meta"], "walk-cycle.json")
        self.assertEqual(entry["frames"], 14)
        self.assertEqual((entry["width"], entry["height"]),
                         (64, 64))
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "lib", "walk-cycle.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "lib", "walk-cycle.json")))

    def test_add_sheet_requires_both_files(self):
        png = write_image(self._image(),
                          os.path.join(self.tmp, "walk.png"))
        with self.assertRaises(PixelForgeError):
            self.lib.add_sheet(png, os.path.join(self.tmp, "no.json"),
                               "Walk")
        with self.assertRaises(PixelForgeError):
            self.lib.add_sheet(os.path.join(self.tmp, "no.png"),
                               os.path.join(self.tmp, "no.json"),
                               "Walk")

    def test_search_by_name_and_tag(self):
        self.lib.add_image(self._image(), "Red Knight",
                           tags=["hero", "red"])
        self.lib.add_image(self._image(), "Blue Slime",
                           tags=["enemy", "blue"])
        self.lib.add_image(self._image(), "Red Dragon",
                           tags=["enemy", "red"])
        self.assertEqual([e["name"] for e in
                          self.lib.search(text="knight")],
                         ["Red Knight"])
        self.assertEqual([e["name"] for e in
                          self.lib.search(text="RED")],
                         ["Red Knight", "Red Dragon"])
        self.assertEqual([e["name"] for e in
                          self.lib.search(tag="enemy")],
                         ["Blue Slime", "Red Dragon"])
        self.assertEqual([e["name"] for e in
                          self.lib.search(text="red", tag="enemy")],
                         ["Red Dragon"])
        self.assertEqual(self.lib.search(text="zzz"), [])

    def test_all_tags_sorted_unique(self):
        self.lib.add_image(self._image(), "A", tags=["zebra", "hero"])
        self.lib.add_image(self._image(), "B", tags=["hero", "apple"])
        self.assertEqual(self.lib.all_tags(),
                         ["apple", "hero", "zebra"])

    def test_rename_changes_name_only(self):
        entry = self.lib.add_image(self._image(), "Old Name")
        updated = self.lib.rename(entry["id"], "New Name")
        self.assertEqual(updated["name"], "New Name")
        self.assertEqual(updated["file"], "old-name.png")
        self.assertEqual(self.lib.find(entry["id"])["name"],
                         "New Name")
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "lib", "old-name.png")))
        with self.assertRaises(PixelForgeError):
            self.lib.rename(entry["id"], "   ")
        with self.assertRaises(PixelForgeError):
            self.lib.rename("missing-id", "X")

    def test_remove_deletes_files_and_index(self):
        png = write_image(self._image(),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as f:
            json.dump({"format": "x"}, f)
        sheet = self.lib.add_sheet(png, meta, "Walk", frames=3)
        sprite = self.lib.add_image(self._image(), "Knight")
        self.lib.remove(sheet["id"])
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "lib", "walk.png")))
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "lib", "walk.json")))
        self.assertIsNone(self.lib.find(sheet["id"]))
        self.assertEqual(len(self.lib.entries()), 1)
        self.lib.remove(sprite["id"])
        self.assertEqual(self.lib.entries(), [])
        with self.assertRaises(PixelForgeError):
            self.lib.remove("missing-id")

    def test_remove_tolerates_missing_files(self):
        entry = self.lib.add_image(self._image(), "Knight")
        os.remove(os.path.join(self.tmp, "lib", "knight.png"))
        self.lib.remove(entry["id"])  # no raise
        self.assertEqual(self.lib.entries(), [])

    def test_persistence_across_instances(self):
        self.lib.add_image(self._image(), "Knight", tags=["hero"])
        self.lib.add_image(self._image(16, 16), "Slime")
        other = Library(os.path.join(self.tmp, "lib"))
        self.assertEqual([e["name"] for e in other.entries()],
                         ["Knight", "Slime"])
        self.assertEqual(other.all_tags(), ["hero"])

    def test_corrupt_index_raises_readably(self):
        lib_dir = os.path.join(self.tmp, "lib")
        os.makedirs(lib_dir)
        with open(os.path.join(lib_dir, "library.json"), "w") as f:
            f.write("{not json")
        with self.assertRaises(PixelForgeError) as ctx:
            Library(lib_dir).entries()
        self.assertIn("unreadable", str(ctx.exception))
        with open(os.path.join(lib_dir, "library.json"), "w") as f:
            json.dump({"format": "something-else", "entries": []}, f)
        with self.assertRaises(PixelForgeError) as ctx:
            Library(lib_dir).entries()
        self.assertIn("not a Pixel Forge library",
                      str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
