"""Library tests — plain-file storage, index, search, tags.

Skipped when PySide6 is not importable."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from helpers import ensure_app, make_solid_image, write_image  # noqa: E402
    from pixel_forge.library import (LIBRARY_FORMAT, Library,  # noqa: E402
                                     default_library_dir,
                                     normalize_tags, slugify,
                                     validate_entry)
    from pixel_forge.pixelize import PixelForgeError  # noqa: E402
except ImportError as exc:
    # Task 63: every test here needs Qt, so skip the module when
    # PySide6 is what is missing — but a genuinely broken import
    # still fails loudly instead of hiding behind the skip.
    if exc.name and exc.name.startswith("PySide6"):
        raise unittest.SkipTest("PySide6 not available")
    raise


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

    def test_add_sheet_rewrites_sheet_field_to_library_png(self):
        """Task 62: the library copy's meta must name the PNG as
        it sits in the library — not the source PNG, which does
        not exist there. The original meta stays byte-identical."""
        png = write_image(self._image(64, 64),
                          os.path.join(self.tmp, "walk-sheet.png"))
        meta = os.path.join(self.tmp, "walk-sheet.json")
        with open(meta, "w") as f:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "sheet": "walk-sheet.png",
                       "frames": [{"name": "frame_001.png",
                                   "index": 0}]}, f)
        with open(meta, "rb") as f:
            original_bytes = f.read()
        entry = self.lib.add_sheet(png, meta, "Walk cycle",
                                   frames=1)
        lib_meta_path = os.path.join(self.tmp, "lib",
                                     entry["meta"])
        with open(lib_meta_path) as f:
            lib_meta = json.load(f)
        self.assertEqual(lib_meta["sheet"], entry["file"])
        self.assertTrue(os.path.isfile(os.path.join(
            self.tmp, "lib", lib_meta["sheet"])))
        # Everything else in the meta survives the rewrite.
        self.assertEqual(lib_meta["format"],
                         "pixel-forge-spritesheet")
        self.assertEqual(lib_meta["frames"],
                         [{"name": "frame_001.png", "index": 0}])
        with open(meta, "rb") as f:
            self.assertEqual(f.read(), original_bytes)

    def test_add_sheet_adds_sheet_field_when_missing(self):
        png = write_image(self._image(64, 64),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as f:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "frames": []}, f)
        entry = self.lib.add_sheet(png, meta, "Walk", frames=0)
        with open(os.path.join(self.tmp, "lib",
                               entry["meta"])) as f:
            self.assertEqual(json.load(f)["sheet"],
                             entry["file"])

    def test_add_sheet_copies_non_json_meta_verbatim(self):
        png = write_image(self._image(64, 64),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as f:
            f.write("not json at all")
        entry = self.lib.add_sheet(png, meta, "Walk", frames=0)
        with open(os.path.join(self.tmp, "lib",
                               entry["meta"])) as f:
            self.assertEqual(f.read(), "not json at all")

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

    # ------------------------------------------- malformed entries (v0.10)

    def _write_index(self, entries):
        lib_dir = os.path.join(self.tmp, "lib")
        os.makedirs(lib_dir, exist_ok=True)
        path = os.path.join(lib_dir, "library.json")
        with open(path, "w") as f:
            json.dump({"format": LIBRARY_FORMAT, "version": 1,
                       "entries": entries}, f)
        return path

    def _good_entry(self, name="Knight", stem="knight"):
        return {"id": stem, "name": name, "file": stem + ".png",
                "kind": "sprite", "tags": ["hero"],
                "source": "image", "width": 32, "height": 32,
                "created": "2026-11-12T00:00:00+00:00"}

    def test_malformed_entries_skipped_not_fatal(self):
        # One malformed entry used to brick startup with an
        # uncaught KeyError (review W4). Now the good entries
        # load and the bad ones are reported, not fatal.
        no_name = self._good_entry(name="Ghost", stem="ghost")
        del no_name["name"]
        no_file = self._good_entry(name="Wisp", stem="wisp")
        del no_file["file"]
        self._write_index([self._good_entry(), no_name,
                           self._good_entry("Slime", "slime"),
                           no_file])
        lib = Library(os.path.join(self.tmp, "lib"))
        entries = lib.entries()
        self.assertEqual([e["name"] for e in entries],
                         ["Knight", "Slime"])
        skipped = lib.skipped_entries()
        self.assertEqual(len(skipped), 2)
        self.assertIn("missing 'name'", skipped[0])
        self.assertIn("missing 'file'", skipped[1])
        # Every read path works on the loaded library — no
        # KeyError from the skipped entries.
        self.assertEqual(len(lib.search()), 2)
        self.assertEqual(lib.all_tags(), ["hero"])
        self.assertEqual(lib.find("knight")["name"], "Knight")
        self.assertIsNone(lib.find("ghost"))

    def test_wrong_typed_fields_skipped(self):
        tags_str = self._good_entry("Tags", "tags")
        tags_str["tags"] = "hero,red"
        width_str = self._good_entry("Width", "width")
        width_str["width"] = "32"
        bad_kind = self._good_entry("Kind", "kind")
        bad_kind["kind"] = "tileset"
        not_an_entry = "just a string"
        self._write_index([self._good_entry(), tags_str, width_str,
                           bad_kind, not_an_entry])
        lib = Library(os.path.join(self.tmp, "lib"))
        self.assertEqual([e["name"] for e in lib.entries()],
                         ["Knight"])
        skipped = lib.skipped_entries()
        self.assertEqual(len(skipped), 4)
        self.assertIn("'tags'", skipped[0])
        self.assertIn("'width'", skipped[1])
        self.assertIn("kind", skipped[2])
        self.assertIn("not an object", skipped[3])
        # The validator itself: a good entry passes, and every
        # field the window reads with [] is required.
        self.assertIsNone(validate_entry(self._good_entry()))
        for key in ("id", "name", "file", "kind", "width",
                    "height", "source", "created"):
            broken = self._good_entry()
            del broken[key]
            self.assertIsNotNone(validate_entry(broken), key)

    def test_malformed_entries_left_untouched_on_disk(self):
        no_name = self._good_entry(name="Ghost", stem="ghost")
        del no_name["name"]
        no_file = self._good_entry(name="Wisp", stem="wisp")
        del no_file["file"]
        index = [self._good_entry(), no_name,
                 self._good_entry("Slime", "slime"), no_file]
        path = self._write_index(index)
        before = open(path, "rb").read()
        lib = Library(os.path.join(self.tmp, "lib"))
        lib.entries()
        # Loading alone never rewrites the file.
        self.assertEqual(open(path, "rb").read(), before)
        # And a later save (a new sprite) keeps the bad entries
        # verbatim, in their original positions — nothing lost.
        lib.add_image(self._image(), "Newcomer")
        with open(path) as f:
            saved = json.load(f)["entries"]
        self.assertEqual(len(saved), 5)
        self.assertEqual(saved[0]["name"], "Knight")
        self.assertEqual(saved[1], no_name)
        self.assertEqual(saved[2]["name"], "Slime")
        self.assertEqual(saved[3], no_file)
        self.assertEqual(saved[4]["name"], "Newcomer")
        # The skipped entries are still reported after the save.
        self.assertEqual(len(lib.skipped_entries()), 2)

    def test_rename_and_remove_work_with_skipped_entries(self):
        no_name = self._good_entry(name="Ghost", stem="ghost")
        del no_name["name"]
        self._write_index([self._good_entry(), no_name,
                           self._good_entry("Slime", "slime")])
        lib = Library(os.path.join(self.tmp, "lib"))
        lib.rename("knight", "Sir Knight")
        lib.remove("slime")
        with open(os.path.join(self.tmp, "lib",
                               "library.json")) as f:
            saved = json.load(f)["entries"]
        self.assertEqual(
            [e.get("name") for e in saved],
            ["Sir Knight", None])
        self.assertEqual(saved[1], no_name)
        self.assertEqual(len(lib.skipped_entries()), 1)


if __name__ == "__main__":
    unittest.main()
