"""Engine-ready export tests — the JSON shapes, the .aseprite
round-trip, the GameMaker strip, and the dispatcher (task 36).

The .aseprite writer is verified by parsing its output back
with a minimal reader (read_aseprite below): magic, frame
count, per-frame durations, chunk types, tag names and ranges,
palette entries, and the cel pixels decompressed and compared
against the source frames. No Aseprite binary runs here, so
this is a structural verification against the public spec —
the README says so.

Qt-free (task 64): runs without PySide6.
"""
import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers import make_solid_image, write_image  # noqa: E402
from pixel_forge.engine_export import (  # noqa: E402
    ASE_CHUNK_CEL, ASE_CHUNK_LAYER, ASE_CHUNK_PALETTE, ASE_CHUNK_TAGS,
    ASE_FRAME_MAGIC, ASE_MAGIC, ENGINE_FORMATS, FORMAT_INFO,
    export_engine, format_files, format_note, write_aseprite_file,
    write_aseprite_json, write_gamemaker_strip,
    write_texturepacker_json)
from pixel_forge.image import Image  # noqa: E402
from pixel_forge.pixelize import PixelForgeError, load_image  # noqa: E402

# The QApplication must exist before the first load_image
# call takes the Qt path (see helpers.ensure_app_if_qt).
from helpers import ensure_app_if_qt  # noqa: E402
ensure_app_if_qt()

# Distinct frame colors: (r, g, b) per frame index.
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
          (255, 0, 255), (0, 255, 255), (128, 64, 32), (10, 20, 30)]


def make_frames(count, size=32):
    return [make_solid_image(size, size, *COLORS[i % len(COLORS)])
            for i in range(count)]


def frame_names(count):
    return ["frame_%03d.png" % (i + 1) for i in range(count)]


# ------------------------------------------------- minimal .aseprite reader
# A structural parser for the files write_aseprite_file writes:
# header fields, per-frame durations and chunks, and per-chunk
# parsers for the four chunk types the writer emits. It is a
# test tool, not product code — the product only writes.

def _read_string(data, offset):
    length = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    return data[offset:offset + length].decode("utf-8"), offset + length


def _parse_layer(data):
    flags, layer_type = struct.unpack_from("<HH", data, 0)
    name, _ = _read_string(data, 16)
    return {"flags": flags, "type": layer_type, "name": name}


def _parse_cel(data):
    layer, x, y, opacity, cel_type = struct.unpack_from(
        "<HhhBH", data, 0)
    width, height = struct.unpack_from("<HH", data, 16)
    pixels = zlib.decompress(data[20:])
    return {"layer": layer, "x": x, "y": y, "opacity": opacity,
            "type": cel_type, "width": width, "height": height,
            "pixels": pixels}


def _parse_tags(data):
    count = struct.unpack_from("<H", data, 0)[0]
    offset = 10
    tags = []
    for _ in range(count):
        start, end, direction, repeat = struct.unpack_from(
            "<HHBH", data, offset)
        offset += 7 + 6 + 3 + 1
        name, offset = _read_string(data, offset)
        tags.append({"name": name, "from": start, "to": end,
                     "direction": direction, "repeat": repeat})
    return tags


def _parse_palette(data):
    size, first, last = struct.unpack_from("<III", data, 0)
    offset = 20
    entries = []
    for _ in range(last - first + 1):
        _flags, red, green, blue, alpha = struct.unpack_from(
            "<HBBBB", data, offset)
        offset += 6
        entries.append((red, green, blue, alpha))
    return {"size": size, "first": first, "last": last,
            "entries": entries}


_CHUNK_PARSERS = {ASE_CHUNK_LAYER: _parse_layer,
                  ASE_CHUNK_CEL: _parse_cel,
                  ASE_CHUNK_TAGS: _parse_tags,
                  ASE_CHUNK_PALETTE: _parse_palette}


def read_aseprite(path):
    """Parse a .aseprite file into header fields + frames.
    Raises AssertionError (not a product error) on any
    structural problem — a malformed file is a test failure,
    not user input."""
    with open(path, "rb") as handle:
        data = handle.read()
    assert len(data) >= 128, "file shorter than the 128-byte header"
    (file_size, magic, frames, width, height, depth, flags,
     _speed, _zero1, _zero2, _transparent, _ignore, colors,
     _px, _py, _gx, _gy, _gw, _gh) = struct.unpack_from(
        "<IHHHHHIHIIB3sHBBhhHH", data, 0)
    assert magic == ASE_MAGIC, "bad magic: %#x" % magic
    assert file_size == len(data), (
        "size field %d but the file is %d bytes"
        % (file_size, len(data)))
    parsed = {"file_size": file_size, "frames": frames,
              "width": width, "height": height, "depth": depth,
              "flags": flags, "colors": colors, "frame_list": []}
    offset = 128
    for _ in range(frames):
        size, frame_magic, old_count, duration, _res, new_count = \
            struct.unpack_from("<IHHH2sI", data, offset)
        assert frame_magic == ASE_FRAME_MAGIC, (
            "bad frame magic: %#x" % frame_magic)
        count = new_count if new_count else old_count
        frame = {"duration": duration, "chunks": []}
        chunk_offset = offset + 16
        for _ in range(count):
            chunk_size, chunk_type = struct.unpack_from(
                "<IH", data, chunk_offset)
            body = data[chunk_offset + 6:
                        chunk_offset + chunk_size]
            parser = _CHUNK_PARSERS.get(chunk_type)
            frame["chunks"].append(
                (chunk_type, parser(body) if parser else body))
            chunk_offset += chunk_size
        assert chunk_offset == offset + size, (
            "frame size field %d but the chunks span %d"
            % (size, chunk_offset - offset))
        parsed["frame_list"].append(frame)
        offset += size
    assert offset == len(data), (
        "frames end at byte %d but the file is %d bytes"
        % (offset, len(data)))
    return parsed


def chunks_of_type(frame, chunk_type):
    return [body for type_, body in frame["chunks"]
            if type_ == chunk_type]


class AsepriteJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_shape_and_rects(self):
        # 4 frames of 32x32, 2 columns, padding 2: the JSON
        # rects must match the packed grid exactly.
        images = make_frames(4)
        names = frame_names(4)
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        written = write_aseprite_json(images, names, png, meta,
                                      cols=2, padding=2, fps=8,
                                      durations=[500, 100, 100, 100])
        self.assertEqual([str(p) for p in written], [png, meta])
        with open(meta) as handle:
            data = json.load(handle)
        self.assertEqual(set(data), {"frames", "meta"})
        self.assertEqual(set(data["frames"]), set(names))
        entry = data["frames"]["frame_001.png"]
        self.assertEqual(entry["frame"],
                         {"x": 0, "y": 0, "w": 32, "h": 32})
        self.assertEqual(entry["duration"], 500)
        self.assertFalse(entry["rotated"])
        self.assertFalse(entry["trimmed"])
        self.assertEqual(entry["spriteSourceSize"],
                         {"x": 0, "y": 0, "w": 32, "h": 32})
        self.assertEqual(entry["sourceSize"], {"w": 32, "h": 32})
        # Frame 3 sits in the second row: y = 32 + padding.
        self.assertEqual(data["frames"]["frame_003.png"]["frame"],
                         {"x": 0, "y": 34, "w": 32, "h": 32})
        self.assertEqual(data["frames"]["frame_004.png"]["duration"],
                         100)
        meta_block = data["meta"]
        self.assertEqual(meta_block["app"], "Pixel Forge")
        self.assertEqual(meta_block["image"], "sheet.png")
        self.assertEqual(meta_block["format"], "RGBA8888")
        self.assertEqual(meta_block["size"], {"w": 66, "h": 66})
        self.assertEqual(meta_block["scale"], "1")
        self.assertIsInstance(meta_block["scale"], str)
        self.assertNotIn("frameTags", meta_block)
        # The PNG is the packed sheet: frame 2's color at its
        # top-left corner.
        sheet = load_image(png)
        self.assertEqual((sheet.width(), sheet.height()), (66, 66))
        pixel = sheet.pixel(34, 0)
        self.assertEqual(((pixel >> 16) & 0xFF,
                          (pixel >> 8) & 0xFF, pixel & 0xFF),
                         (0, 255, 0))

    def test_tags_carried(self):
        images = make_frames(4)
        tags = [{"name": "walk", "from": 0, "to": 1},
                {"name": "idle", "from": 2, "to": 3}]
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        write_aseprite_json(images, frame_names(4), png, meta,
                            fps=8, tags=tags)
        with open(meta) as handle:
            data = json.load(handle)
        self.assertEqual(data["meta"]["frameTags"], [
            {"name": "walk", "from": 0, "to": 1,
             "direction": "forward"},
            {"name": "idle", "from": 2, "to": 3,
             "direction": "forward"}])

    def test_default_durations_come_from_fps(self):
        images = make_frames(2)
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        write_aseprite_json(images, frame_names(2), png, meta, fps=8)
        with open(meta) as handle:
            data = json.load(handle)
        durations = [entry["duration"]
                     for entry in data["frames"].values()]
        self.assertEqual(durations, [125, 125])

    def test_bad_durations_and_tags_refuse(self):
        images = make_frames(2)
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        with self.assertRaises(PixelForgeError):
            write_aseprite_json(images, frame_names(2), png, meta,
                                durations=[100])
        with self.assertRaises(PixelForgeError):
            write_aseprite_json(images, frame_names(2), png, meta,
                                durations=[100, 0])
        with self.assertRaises(PixelForgeError):
            write_aseprite_json(images, frame_names(2), png, meta,
                                tags=[{"name": "bad", "from": 1,
                                       "to": 0}])

    def test_mixed_sizes_name_the_odd_frame(self):
        images = [make_solid_image(32, 32, 1, 2, 3),
                  make_solid_image(16, 16, 1, 2, 3)]
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        with self.assertRaises(PixelForgeError) as caught:
            write_aseprite_json(images, ["a.png", "b.png"], png,
                                meta)
        self.assertIn("b.png", str(caught.exception))


class TexturePackerJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_shape_and_no_durations(self):
        images = make_frames(3)
        png = os.path.join(self.tmp, "sheet.png")
        meta = os.path.join(self.tmp, "sheet.json")
        write_texturepacker_json(images, frame_names(3), png, meta,
                                 cols=3)
        with open(meta) as handle:
            data = json.load(handle)
        self.assertEqual(set(data), {"frames", "meta"})
        for entry in data["frames"].values():
            self.assertNotIn("duration", entry)
            self.assertFalse(entry["rotated"])
            self.assertFalse(entry["trimmed"])
        meta_block = data["meta"]
        self.assertEqual(meta_block["app"], "Pixel Forge")
        self.assertEqual(meta_block["format"], "RGBA8888")
        self.assertEqual(meta_block["scale"], 1)
        self.assertIsInstance(meta_block["scale"], int)
        self.assertNotIn("frameTags", meta_block)
        # smartupdate is the PNG's content hash.
        with open(png, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(meta_block["smartupdate"], digest)

    def test_phaser_and_pixi_share_the_writer(self):
        images = make_frames(2)
        for app, expected in (("Pixel Forge (Phaser)", "phaser"),
                              ("Pixel Forge (Pixi)", "pixi")):
            png = os.path.join(self.tmp, expected + ".png")
            meta = os.path.join(self.tmp, expected + ".json")
            write_texturepacker_json(images, frame_names(2), png,
                                     meta, app=app)
            with open(meta) as handle:
                data = json.load(handle)
            self.assertEqual(data["meta"]["app"], app)
            self.assertEqual(data["meta"]["image"],
                             expected + ".png")


class AsepriteFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, count=3, durations=None, tags=None, size=16):
        images = make_frames(count, size=size)
        path = os.path.join(self.tmp, "anim.aseprite")
        write_aseprite_file(images, path, durations=durations,
                            fps=8, tags=tags)
        return images, path

    def test_header_and_frame_durations(self):
        _images, path = self._write(
            durations=[500, 100, 250])
        parsed = read_aseprite(path)
        self.assertEqual(parsed["frames"], 3)
        self.assertEqual((parsed["width"], parsed["height"]),
                         (16, 16))
        self.assertEqual(parsed["depth"], 32)
        self.assertEqual(parsed["flags"], 1)
        self.assertEqual([frame["duration"]
                          for frame in parsed["frame_list"]],
                         [500, 100, 250])
        self.assertEqual(parsed["colors"], 3)

    def test_chunks_present_in_first_frame(self):
        tags = [{"name": "walk", "from": 0, "to": 2}]
        _images, path = self._write(tags=tags)
        parsed = read_aseprite(path)
        first = parsed["frame_list"][0]
        types = [type_ for type_, _ in first["chunks"]]
        self.assertEqual(types, [ASE_CHUNK_LAYER, ASE_CHUNK_PALETTE,
                                 ASE_CHUNK_TAGS, ASE_CHUNK_CEL])
        layer = chunks_of_type(first, ASE_CHUNK_LAYER)[0]
        self.assertEqual(layer["name"], "Layer 1")
        self.assertEqual(layer["type"], 0)
        self.assertTrue(layer["flags"] & 1)  # visible
        palette = chunks_of_type(first, ASE_CHUNK_PALETTE)[0]
        self.assertEqual(palette["size"], 3)
        self.assertEqual(palette["entries"], [
            (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)])
        # Later frames carry only their cel.
        for frame in parsed["frame_list"][1:]:
            self.assertEqual([type_ for type_, _ in frame["chunks"]],
                             [ASE_CHUNK_CEL])

    def test_tags_round_trip(self):
        tags = [{"name": "walk", "from": 0, "to": 1},
                {"name": "idle", "from": 2, "to": 2}]
        _images, path = self._write(tags=tags)
        parsed = read_aseprite(path)
        found = chunks_of_type(parsed["frame_list"][0],
                               ASE_CHUNK_TAGS)[0]
        self.assertEqual(found, [
            {"name": "walk", "from": 0, "to": 1, "direction": 0,
             "repeat": 0},
            {"name": "idle", "from": 2, "to": 2, "direction": 0,
             "repeat": 0}])

    def test_no_tags_means_no_tags_chunk(self):
        _images, path = self._write()
        parsed = read_aseprite(path)
        for frame in parsed["frame_list"]:
            self.assertEqual(chunks_of_type(frame, ASE_CHUNK_TAGS),
                             [])

    def test_cel_pixels_round_trip(self):
        images, path = self._write()
        parsed = read_aseprite(path)
        for image, frame in zip(images, parsed["frame_list"]):
            cel = chunks_of_type(frame, ASE_CHUNK_CEL)[0]
            self.assertEqual(cel["type"], 2)  # compressed image
            self.assertEqual(cel["layer"], 0)
            self.assertEqual((cel["x"], cel["y"]), (0, 0))
            self.assertEqual(cel["opacity"], 255)
            self.assertEqual((cel["width"], cel["height"]),
                             (image.width(), image.height()))
            self.assertEqual(cel["pixels"], bytes(image.data))

    def test_default_durations_come_from_fps(self):
        _images, path = self._write()
        parsed = read_aseprite(path)
        self.assertEqual([frame["duration"]
                          for frame in parsed["frame_list"]],
                         [125, 125, 125])

    def test_header_is_128_bytes(self):
        self.assertEqual(struct.calcsize(
            "<IHHHHHIHIIB3sHBBhhHH84s"), 128)

    def test_limits_refuse_plainly(self):
        images = make_frames(2)
        path = os.path.join(self.tmp, "anim.aseprite")
        with self.assertRaises(PixelForgeError) as caught:
            write_aseprite_file(images, path, durations=[100, 70000])
        self.assertIn("65535", str(caught.exception))
        many = [make_solid_image(1, 1, 1, 2, 3)] * 65536
        with self.assertRaises(PixelForgeError) as caught:
            write_aseprite_file(many, path)
        self.assertIn("65535", str(caught.exception))
        with self.assertRaises(PixelForgeError):
            write_aseprite_file([], path)

    def test_all_transparent_frames_still_get_a_palette(self):
        image = Image(4, 4)
        image.fill(0x00000000)
        path = os.path.join(self.tmp, "clear.aseprite")
        write_aseprite_file([image], path)
        parsed = read_aseprite(path)
        palette = chunks_of_type(parsed["frame_list"][0],
                                 ASE_CHUNK_PALETTE)[0]
        self.assertEqual(palette["entries"], [(0, 0, 0, 0)])


class StripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_strip_is_one_row_in_order(self):
        images = make_frames(4, size=16)
        path = os.path.join(self.tmp, "hero_strip4.png")
        written = write_gamemaker_strip(images, path)
        self.assertEqual(str(written), path)
        strip = load_image(path)
        self.assertEqual((strip.width(), strip.height()), (64, 16))
        for index, color in enumerate(COLORS[:4]):
            pixel = strip.pixel(index * 16, 0)
            self.assertEqual(((pixel >> 16) & 0xFF,
                              (pixel >> 8) & 0xFF, pixel & 0xFF),
                             color)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.images = make_frames(3)
        self.names = frame_names(3)
        self.tags = [{"name": "walk", "from": 0, "to": 2}]

    def test_every_format_writes_its_files(self):
        expected = {
            "aseprite": ["sheet.png", "sheet.json"],
            "texturepacker": ["sheet.png", "sheet.json"],
            "godot": ["sheet.png", "sheet.json"],
            "unity": ["sheet.aseprite", "sheet.png"],
            "gamemaker": ["sheet_strip3.png"],
            "phaser": ["sheet.png", "sheet.json"],
            "pixi": ["sheet.png", "sheet.json"],
        }
        for key in ENGINE_FORMATS:
            folder = os.path.join(self.tmp, key)
            files = export_engine(key, self.images, self.names,
                                  folder, "sheet", durations=[500, 100,
                                                              100],
                                  tags=self.tags)
            self.assertEqual([p.name for p in files], expected[key])
            for path in files:
                self.assertTrue(path.is_file(), str(path))

    def test_godot_json_is_aseprite_shaped(self):
        folder = os.path.join(self.tmp, "godot")
        files = export_engine("godot", self.images, self.names,
                              folder, "sheet", durations=[500, 100,
                                                          100],
                              tags=self.tags)
        with open(files[1]) as handle:
            data = json.load(handle)
        self.assertEqual(data["meta"]["app"], "Pixel Forge (Godot)")
        self.assertEqual(data["frames"]["frame_001.png"]["duration"],
                         500)
        self.assertEqual(data["meta"]["frameTags"], [
            {"name": "walk", "from": 0, "to": 2,
             "direction": "forward"}])

    def test_unity_aseprite_parses_back(self):
        folder = os.path.join(self.tmp, "unity")
        files = export_engine("unity", self.images, self.names,
                              folder, "sheet", durations=[500, 100,
                                                          100],
                              tags=self.tags)
        parsed = read_aseprite(files[0])
        self.assertEqual(parsed["frames"], 3)
        self.assertEqual([frame["duration"]
                          for frame in parsed["frame_list"]],
                         [500, 100, 100])
        found = chunks_of_type(parsed["frame_list"][0],
                               ASE_CHUNK_TAGS)[0]
        self.assertEqual(found[0]["name"], "walk")

    def test_meta_path_override(self):
        folder = os.path.join(self.tmp, "meta")
        custom = os.path.join(self.tmp, "atlas.json")
        files = export_engine("aseprite", self.images, self.names,
                              folder, "sheet", meta_path=custom)
        self.assertEqual([str(p) for p in files],
                         [os.path.join(folder, "sheet.png"), custom])
        self.assertTrue(os.path.isfile(custom))
        self.assertFalse(os.path.isfile(
            os.path.join(folder, "sheet.json")))

    def test_meta_refused_where_there_is_no_json(self):
        for key in ("unity", "gamemaker"):
            with self.assertRaises(PixelForgeError):
                export_engine(key, self.images, self.names,
                              self.tmp, "sheet",
                              meta_path=os.path.join(self.tmp,
                                                     "x.json"))

    def test_unknown_format_and_empty_frames(self):
        with self.assertRaises(PixelForgeError):
            export_engine("nope", self.images, self.names,
                          self.tmp, "sheet")
        with self.assertRaises(PixelForgeError):
            export_engine("aseprite", [], [], self.tmp, "sheet")

    def test_format_files_and_notes(self):
        self.assertEqual(format_files("gamemaker", "hero", 4),
                         ["hero_strip4.png"])
        self.assertEqual(format_files("unity", "hero", 4),
                         ["hero.aseprite", "hero.png"])
        self.assertEqual(format_files("aseprite", "hero", 4),
                         ["hero.png", "hero.json"])
        for key in ENGINE_FORMATS:
            self.assertIn(key, FORMAT_INFO)
            self.assertTrue(format_note(key))
        # The drops are stated, not hidden.
        self.assertIn("dropped", format_note("texturepacker"))
        self.assertIn("dropped", format_note("phaser"))
        self.assertIn("dropped", format_note("pixi"))
        self.assertIn("carried", format_note("aseprite"))
        self.assertIn("Nearest", format_note("godot"))


if __name__ == "__main__":
    unittest.main()
