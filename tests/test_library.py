"""Library tests — plain-file storage, index, search, tags.

Qt-free (task 64): runs without PySide6."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import make_solid_image, write_image  # noqa: E402
from pixel_forge.image import Image  # noqa: E402
from pixel_forge.library import (LIBRARY_FORMAT, Library,  # noqa: E402
                                 default_library_dir,
                                 normalize_tags, slugify,
                                 validate_entry)
from pixel_forge.pixelize import PixelForgeError  # noqa: E402

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()


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
        with self.assertRaises(PixelForgeError):
            self.lib.add_image(Image(0, 0), "Empty")

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


class LibraryDepthTests(unittest.TestCase):
    """Library depth (task 24): filters, collections, orphans,
    duplicates, export/import, tag editing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = Library(os.path.join(self.tmp, "lib"))

    def _image(self, w=32, h=32, r=255, g=0, b=0):
        return make_solid_image(w, h, r, g, b)

    def _sheet(self, name="Walk cycle"):
        png = write_image(self._image(64, 64),
                          os.path.join(self.tmp, "walk.png"))
        meta = os.path.join(self.tmp, "walk.json")
        with open(meta, "w") as f:
            json.dump({"format": "pixel-forge-spritesheet",
                       "version": 1, "frames": []}, f)
        return self.lib.add_sheet(png, meta, name, frames=14)

    def _names(self, entries):
        return [e["name"] for e in entries]

    # ------------------------------------------------------------ search

    def test_search_filters_compose(self):
        self.lib.add_image(self._image(), "Knight",
                           tags=["hero"], source="image")
        self.lib.add_image(self._image(), "Slime",
                           tags=["hero"], source="code")
        self._sheet("Walk cycle")
        self.assertEqual(
            self._names(self.lib.search(kind="sheet")),
            ["Walk cycle"])
        self.assertEqual(
            self._names(self.lib.search(source="code")),
            ["Slime"])
        self.assertEqual(
            self._names(self.lib.search(tag="hero",
                                        source="code")),
            ["Slime"])
        self.assertEqual(
            self._names(self.lib.search(text="knight",
                                        kind="sheet")),
            [])
        self.assertEqual(len(self.lib.search()), 3)

    def test_search_collection_filter(self):
        knight = self.lib.add_image(self._image(), "Knight")
        self.lib.add_image(self._image(), "Slime")
        self.lib.update(knight["id"], collection="heroes")
        self.assertEqual(
            self._names(self.lib.search(collection="heroes")),
            ["Knight"])
        self.assertEqual(
            self._names(self.lib.search(collection="")),
            ["Slime"])
        self.assertEqual(
            self._names(self.lib.search(collection="nobody")),
            [])

    def test_all_collections_sorted(self):
        first = self.lib.add_image(self._image(), "Knight")
        second = self.lib.add_image(self._image(), "Slime")
        self.lib.update(first["id"], collection="walk cycles")
        self.lib.update(second["id"], collection="Heroes")
        self.assertEqual(self.lib.all_collections(),
                         ["Heroes", "walk cycles"])
        self.lib.update(second["id"], collection="")
        self.assertEqual(self.lib.all_collections(),
                         ["walk cycles"])

    def test_collection_is_optional_and_old_indexes_load(self):
        # An index written before collections existed has no
        # "collection" key anywhere — it loads unchanged, and the
        # validator accepts entries with and without one.
        entry = self._good_entry()
        self.assertIsNone(validate_entry(entry))
        self.assertNotIn("collection", entry)
        with_collection = self._good_entry()
        with_collection["collection"] = "heroes"
        self.assertIsNone(validate_entry(with_collection))
        bad = self._good_entry()
        bad["collection"] = 7
        self.assertIsNotNone(validate_entry(bad))

    def _good_entry(self, name="Knight", stem="knight"):
        return {"id": stem, "name": name, "file": stem + ".png",
                "kind": "sprite", "tags": ["hero"],
                "source": "image", "width": 32, "height": 32,
                "created": "2026-11-12T00:00:00+00:00"}

    # ------------------------------------------------------------ update

    def test_update_tags_and_collection_round_trip(self):
        entry = self.lib.add_image(self._image(), "Knight",
                                   tags=["hero"])
        updated = self.lib.update(entry["id"],
                                  tags="hero, red, hero ,",
                                  collection="heroes")
        self.assertEqual(updated["tags"], ["hero", "red"])
        self.assertEqual(updated["collection"], "heroes")
        # The change survives a fresh load from disk.
        fresh = Library(os.path.join(self.tmp, "lib"))
        found = fresh.find(entry["id"])
        self.assertEqual(found["tags"], ["hero", "red"])
        self.assertEqual(found["collection"], "heroes")
        # Clearing the collection removes the key; leaving a
        # field out leaves it alone.
        cleared = fresh.update(entry["id"], collection="")
        self.assertNotIn("collection", cleared)
        self.assertEqual(cleared["tags"], ["hero", "red"])

    def test_update_unknown_id_fails_plainly(self):
        with self.assertRaises(PixelForgeError):
            self.lib.update("nope", tags=["x"])

    # ----------------------------------------------------------- orphans

    def test_orphans_and_index_only_removal(self):
        knight = self.lib.add_image(self._image(), "Knight")
        self.lib.add_image(self._image(), "Slime")
        os.unlink(os.path.join(self.tmp, "lib", "knight.png"))
        self.assertEqual(self._names(self.lib.orphans()),
                         ["Knight"])
        # Repair removes the index entry only — and never
        # auto-runs: the orphan is still listed until removed.
        self.assertEqual(self._names(self.lib.orphans()),
                         ["Knight"])
        self.lib.remove_index_only(knight["id"])
        self.assertEqual(self.lib.orphans(), [])
        self.assertEqual(self._names(self.lib.entries()),
                         ["Slime"])
        with self.assertRaises(PixelForgeError):
            self.lib.remove_index_only("nope")

    def test_orphan_repair_leaves_other_files_alone(self):
        sheet = self._sheet()
        os.unlink(os.path.join(self.tmp, "lib", sheet["file"]))
        meta_path = os.path.join(self.tmp, "lib", sheet["meta"])
        self.assertTrue(os.path.isfile(meta_path))
        self.lib.remove_index_only(sheet["id"])
        # The leftover metadata JSON is not ours to delete.
        self.assertTrue(os.path.isfile(meta_path))

    # -------------------------------------------------------- duplicates

    def test_duplicates_found_by_content_hash(self):
        self.lib.add_image(self._image(), "Knight")
        self.lib.add_image(self._image(), "Knight copy")
        self.lib.add_image(self._image(r=0, g=0, b=255), "Slime")
        groups = self.lib.duplicates()
        self.assertEqual(len(groups), 1)
        self.assertEqual(self._names(groups[0]),
                         ["Knight", "Knight copy"])
        # Same name, different pixels: not a duplicate.
        self.assertNotIn("Slime", self._names(groups[0]))

    def test_duplicates_skip_missing_files(self):
        self.lib.add_image(self._image(), "Knight")
        self.lib.add_image(self._image(), "Knight copy")
        os.unlink(os.path.join(self.tmp, "lib", "knight.png"))
        # One file gone: no group of two remains.
        self.assertEqual(self.lib.duplicates(), [])

    # ---------------------------------------------------- export / import

    def test_export_import_round_trip(self):
        self.lib.add_image(self._image(), "Knight",
                           tags=["hero"], source="image")
        self._sheet("Walk cycle")
        backup = os.path.join(self.tmp, "backup")
        self.assertEqual(self.lib.export_to(backup), 2)
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "knight.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "walk-cycle.png")))
        self.assertTrue(os.path.isfile(
            os.path.join(backup, "walk-cycle.json")))
        with open(os.path.join(backup, "library.json")) as f:
            index = json.load(f)
        self.assertEqual(index["format"], LIBRARY_FORMAT)
        self.assertEqual(len(index["entries"]), 2)
        fresh = Library(os.path.join(self.tmp, "lib2"))
        report = fresh.import_from(backup)
        self.assertEqual(sorted(report["added"]),
                         ["Knight", "Walk cycle"])
        self.assertEqual(report["skipped"], [])
        self.assertEqual(report["missing"], [])
        self.assertEqual(self._names(fresh.entries()),
                         ["Knight", "Walk cycle"])
        sheet = fresh.find("walk-cycle")
        self.assertEqual(sheet["kind"], "sheet")
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "lib2", sheet["meta"])))

    def test_import_skips_existing_ids_and_reports(self):
        self.lib.add_image(self._image(), "Knight")
        backup = os.path.join(self.tmp, "backup")
        self.lib.export_to(backup)
        report = self.lib.import_from(backup)
        self.assertEqual(report["added"], [])
        self.assertEqual(report["skipped"], ["Knight"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(len(self.lib.entries()), 1)

    def test_import_never_overwrites_a_colliding_file(self):
        # An incoming entry with a new id but a file name this
        # library already uses: the file gets a fresh name, the
        # old one stays byte-identical.
        backup = os.path.join(self.tmp, "backup")
        os.makedirs(backup)
        write_image(self._image(r=0, g=0, b=255),
                    os.path.join(backup, "knight.png"))
        with open(os.path.join(backup, "library.json"), "w") as f:
            json.dump({"format": LIBRARY_FORMAT, "version": 1,
                       "entries": [
                           {"id": "knight-blue", "name": "Knight",
                            "file": "knight.png", "kind": "sprite",
                            "tags": [], "source": "image",
                            "width": 32, "height": 32,
                            "created": "2026-11-12T00:00:00+00:00"}]},
                      f)
        self.lib.add_image(self._image(), "Knight")
        before = open(os.path.join(self.tmp, "lib",
                                  "knight.png"), "rb").read()
        report = self.lib.import_from(backup)
        self.assertEqual(report["added"], ["Knight"])
        self.assertEqual(len(self.lib.entries()), 2)
        after = open(os.path.join(self.tmp, "lib",
                                 "knight.png"), "rb").read()
        self.assertEqual(after, before)
        files = sorted(e["file"] for e in self.lib.entries())
        self.assertEqual(files, ["knight-2.png", "knight.png"])

    def test_import_reports_missing_files_and_bad_folders(self):
        self.lib.add_image(self._image(), "Knight")
        backup = os.path.join(self.tmp, "backup")
        self.lib.export_to(backup)
        os.unlink(os.path.join(backup, "knight.png"))
        fresh = Library(os.path.join(self.tmp, "lib2"))
        report = fresh.import_from(backup)
        self.assertEqual(report["added"], [])
        self.assertEqual(report["missing"], ["Knight"])
        with self.assertRaises(PixelForgeError):
            fresh.import_from(os.path.join(self.tmp, "nope"))
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        with self.assertRaises(PixelForgeError):
            fresh.import_from(empty)

    def test_export_skips_orphans(self):
        self.lib.add_image(self._image(), "Knight")
        self.lib.add_image(self._image(), "Slime")
        os.unlink(os.path.join(self.tmp, "lib", "knight.png"))
        backup = os.path.join(self.tmp, "backup")
        self.assertEqual(self.lib.export_to(backup), 1)
        self.assertFalse(os.path.isfile(
            os.path.join(backup, "knight.png")))


if __name__ == "__main__":
    unittest.main()
