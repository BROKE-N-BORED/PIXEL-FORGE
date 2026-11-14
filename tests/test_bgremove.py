"""Tests for pixel_forge.bgremove (task 41) — no Qt needed.

The rembg/onnxruntime packages are optional, so every test that
needs them injects fake modules into sys.modules (the suite envs
do not have rembg installed, and must not need it). PIL is real
in both suite envs, so the Image <-> PIL conversion is tested
for real. The one real-model test skips unless the model file
and the packages are actually present — a suite run never
downloads 176 MB.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from pixel_forge.bgremove import (
    BGREMOVE_PACKAGES, MODEL_FILENAME, MODEL_SHA256,
    MODEL_SIZE_BYTES, MODEL_URL, RemovalCancelled,
    check_model_file, child_command, child_python,
    deps_available, deps_missing_text, find_model,
    is_partial_model, main, model_download_command,
    model_homes, remove_background, remove_background_file,
    require_deps, require_model)
from pixel_forge.image import Image
from pixel_forge.pixelize import PixelForgeError
from pixel_forge.pngio import write_png


def make_disc(size=320, radius=110):
    """A red disc on a white background — the synthetic subject
    for the real-model test. u2net segments it as a salient
    object (verified live 2026-11-14)."""
    image = Image(size, size)
    data = image.data
    cx = cy = size // 2
    for y in range(size):
        for x in range(size):
            off = (y * size + x) * 4
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                data[off:off + 4] = bytes((200, 0, 0, 255))
            else:
                data[off:off + 4] = bytes((255, 255, 255, 255))
    return image


class FakeSession:
    """A stand-in rembg session: records what it was given."""

    def __init__(self):
        self.calls = []


def hide_modules(testcase, *names):
    """Hide modules from sys.modules for one test (an entry of
    None makes the import raise ImportError), restored after."""
    saved = {name: sys.modules.get(name) for name in names}
    for name in names:
        sys.modules[name] = None

    def restore():
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    testcase.addCleanup(restore)


def install_fake_rembg(testcase, remove_fn=None):
    """Inject fake rembg + onnxruntime modules into sys.modules
    (removed again after the test). remove_fn, when given, is
    the fake rembg.remove; otherwise a default that makes the
    right half of the image transparent."""
    import pixel_forge.bgremove as bgremove

    calls = {"new_session": [], "remove": []}

    def default_remove(pil_image, session=None, **kwargs):
        from PIL import Image as PILImage

        calls["remove"].append((pil_image, session))
        rgba = pil_image.convert("RGBA")
        pixels = rgba.load()
        width, height = rgba.size
        for y in range(height):
            for x in range(width // 2, width):
                r, g, b, _a = pixels[x, y]
                pixels[x, y] = (r, g, b, 0)
        return rgba

    fake_rembg = types.ModuleType("rembg")

    def new_session(name, **kwargs):
        calls["new_session"].append((name, kwargs))
        session = FakeSession()
        session.calls = calls["new_session"]
        return session

    fake_rembg.new_session = new_session
    fake_rembg.remove = remove_fn or default_remove
    fake_onnx = types.ModuleType("onnxruntime")

    saved = {}
    for name in ("rembg", "onnxruntime"):
        saved[name] = sys.modules.get(name, None)
        sys.modules[name] = (fake_rembg if name == "rembg"
                             else fake_onnx)

    def restore():
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    testcase.addCleanup(restore)
    return calls


class ModelPinTests(unittest.TestCase):
    """The pinned model is the small u2net, by exact size and
    hash — a test that fails if the code ever points at the big
    model instead."""

    def test_pinned_filename_and_url(self):
        self.assertEqual(MODEL_FILENAME, "u2net.onnx")
        self.assertEqual(
            MODEL_URL,
            "https://github.com/danielgatis/rembg/releases/"
            "download/v0.0.0/u2net.onnx")

    def test_pinned_size_is_the_small_model(self):
        self.assertEqual(MODEL_SIZE_BYTES, 175997641)
        # The ~1 GB class is an order of magnitude bigger — this
        # is the tripwire if the pin ever drifts to it.
        self.assertLess(MODEL_SIZE_BYTES, 200_000_000)

    def test_pinned_sha256(self):
        self.assertEqual(
            MODEL_SHA256,
            "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a"
            "739a7a728a912b491")

    def test_packages(self):
        self.assertEqual(BGREMOVE_PACKAGES, ["rembg", "onnxruntime"])


class FindModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env = mock.patch.dict(os.environ, {
            "HOME": self.tmp,
            "REMBG_HOME": "",
            "XDG_DATA_HOME": "",
            "U2NET_HOME": "",
            "PIXELFORGE_BGREMOVE_MODEL": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        # Empty-string env vars must not count as set.
        for key in ("REMBG_HOME", "XDG_DATA_HOME", "U2NET_HOME",
                    "PIXELFORGE_BGREMOVE_MODEL"):
            os.environ.pop(key, None)

    def _write(self, *parts, content=b"x"):
        path = Path(self.tmp, *parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_env_override_wins(self):
        override = self._write("elsewhere", "custom.onnx")
        self._write(".rembg", "models", "u2net", "u2net.onnx")
        with mock.patch.dict(
                os.environ,
                {"PIXELFORGE_BGREMOVE_MODEL": str(override)}):
            self.assertEqual(find_model(), override)

    def test_env_override_missing_falls_through(self):
        self._write(".rembg", "models", "u2net", "u2net.onnx")
        with mock.patch.dict(
                os.environ,
                {"PIXELFORGE_BGREMOVE_MODEL":
                 str(Path(self.tmp) / "nope.onnx")}):
            self.assertEqual(
                find_model(),
                Path(self.tmp) / ".rembg" / "models" / "u2net"
                / "u2net.onnx")

    def test_new_layout_first(self):
        new = self._write(".rembg", "models", "u2net",
                          "u2net.onnx")
        self._write(".u2net", "u2net.onnx")
        self.assertEqual(find_model(), new)

    def test_legacy_layout_found(self):
        legacy = self._write(".u2net", "u2net.onnx")
        self.assertEqual(find_model(), legacy)

    def test_missing_is_none(self):
        self.assertIsNone(find_model())

    def test_rembg_home_env_wins(self):
        custom = Path(self.tmp) / "custom-home"
        target = custom / "models" / "u2net" / "u2net.onnx"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")
        with mock.patch.dict(os.environ,
                             {"REMBG_HOME": str(custom)}):
            self.assertEqual(find_model(), target)

    def test_model_homes_order(self):
        homes = model_homes()
        self.assertEqual(
            homes[0],
            Path(self.tmp) / ".rembg" / "models" / "u2net"
            / "u2net.onnx")
        self.assertEqual(homes[1],
                         Path(self.tmp) / ".u2net" / "u2net.onnx")

    def test_is_partial_model(self):
        partial = self._write("u2net.onnx", content=b"short")
        self.assertTrue(is_partial_model(partial))
        # A full-size file is not partial — build one by size,
        # not by content (176 MB of zeros is cheap as a sparse
        # write through truncate).
        full = self._write("full", "u2net.onnx", content=b"x")
        with open(full, "wb") as handle:
            handle.truncate(MODEL_SIZE_BYTES)
        self.assertFalse(is_partial_model(full))
        other = self._write("other.onnx", content=b"short")
        self.assertFalse(is_partial_model(other))
        self.assertFalse(is_partial_model(
            Path(self.tmp) / "gone" / "u2net.onnx"))

    def test_require_model_missing(self):
        with self.assertRaises(PixelForgeError) as caught:
            require_model()
        self.assertIn("not downloaded", str(caught.exception))
        self.assertIn("176 MB", str(caught.exception))


class DownloadCommandTests(unittest.TestCase):
    def test_model_download_command_resumes(self):
        dest = Path("/tmp/x/u2net.onnx")
        self.assertEqual(
            model_download_command(dest),
            ["curl", "-fSL", "-C", "-", MODEL_URL, "-o",
             str(dest)])


class CheckModelFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_good_file_passes(self):
        import pixel_forge.bgremove as bgremove

        path = Path(self.tmp) / "u2net.onnx"
        path.write_bytes(b"fake model bytes")
        digest = hashlib.sha256(b"fake model bytes").hexdigest()
        with mock.patch.object(bgremove, "MODEL_SHA256", digest):
            check_model_file(path)  # must not raise

    def test_bad_file_raises_naming_hashes(self):
        import pixel_forge.bgremove as bgremove

        path = Path(self.tmp) / "u2net.onnx"
        path.write_bytes(b"fake model bytes")
        with mock.patch.object(bgremove, "MODEL_SHA256",
                               "0" * 64):
            with self.assertRaises(PixelForgeError) as caught:
                check_model_file(path)
        message = str(caught.exception)
        self.assertIn("0" * 64, message)
        self.assertIn(hashlib.sha256(b"fake model bytes")
                      .hexdigest(), message)


class DepsTests(unittest.TestCase):
    def test_missing_deps_reported(self):
        hide_modules(self, "rembg", "onnxruntime")
        ok, missing = deps_available()
        self.assertFalse(ok)
        self.assertEqual(missing, ["rembg", "onnxruntime"])

    def test_present_deps_reported(self):
        install_fake_rembg(self)
        ok, missing = deps_available()
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_require_deps_raises_with_fix(self):
        hide_modules(self, "rembg", "onnxruntime")
        with self.assertRaises(PixelForgeError) as caught:
            require_deps()
        message = str(caught.exception)
        self.assertIn("rembg", message)
        self.assertIn("Install background removal", message)
        self.assertIn("--with-bgremove", message)

    def test_deps_missing_text_names_missing(self):
        text = deps_missing_text(["onnxruntime"])
        self.assertIn("onnxruntime", text)
        self.assertNotIn("rembg, onnxruntime", text)


class ChildDepsTests(unittest.TestCase):
    """The child-python dependency check (the GUI's pre-flight
    check): the removal runs under child_python(), which may be
    another interpreter than the GUI's own, so the check asks
    that interpreter — never the GUI's own imports."""

    def _completed(self, stdout="", stderr="", returncode=0):
        import subprocess

        return subprocess.CompletedProcess(
            args=["other-python", "-c", "script"],
            returncode=returncode, stdout=stdout, stderr=stderr)

    def test_same_interpreter_checks_in_process(self):
        import pixel_forge.bgremove as bgremove

        hide_modules(self, "rembg", "onnxruntime")
        with mock.patch.object(
                bgremove, "child_python",
                return_value=sys.executable), \
                mock.patch("subprocess.run",
                           side_effect=AssertionError(
                               "the in-process check must not "
                               "spawn")):
            ok, missing = bgremove.child_deps_available()
        self.assertFalse(ok)
        self.assertEqual(missing, ["rembg", "onnxruntime"])

    def test_symlinked_python_still_asks_the_child(self):
        import pixel_forge.bgremove as bgremove

        # A venv's bin/python is a symlink to the system python,
        # so two different venvs realpath to the same file while
        # having different packages installed: a path that
        # realpaths to this interpreter but is spelled
        # differently must still take the child path, or the
        # check reads the wrong venv's imports.
        link = os.path.join(tempfile.mkdtemp(), "other-python")
        os.symlink(sys.executable, link)
        self.assertEqual(os.path.realpath(link),
                         os.path.realpath(sys.executable))
        with mock.patch.object(bgremove, "child_python",
                               return_value=link), \
                mock.patch("subprocess.run",
                           return_value=self._completed(
                               stdout="")) as ran:
            ok, missing = bgremove.child_deps_available()
        self.assertTrue(ran.called)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_child_reports_one_missing(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_python",
                               return_value="/other/python"), \
                mock.patch("subprocess.run",
                           return_value=self._completed(
                               stdout="rembg\n")) as ran:
            ok, missing = bgremove.child_deps_available()
        self.assertFalse(ok)
        self.assertEqual(missing, ["rembg"])
        self.assertEqual(ran.call_args[0][0][0], "/other/python")

    def test_child_reports_none_missing(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_python",
                               return_value="/other/python"), \
                mock.patch("subprocess.run",
                           return_value=self._completed(
                               stdout="\n")):
            ok, missing = bgremove.child_deps_available()
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_child_reports_both_missing(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_python",
                               return_value="/other/python"), \
                mock.patch("subprocess.run",
                           return_value=self._completed(
                               stdout="rembg onnxruntime\n")):
            ok, missing = bgremove.child_deps_available()
        self.assertFalse(ok)
        self.assertEqual(missing, ["rembg", "onnxruntime"])

    def test_unrunnable_child_is_plain(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_python",
                               return_value="/other/python"), \
                mock.patch("subprocess.run",
                           side_effect=OSError("no such file")):
            with self.assertRaises(PixelForgeError) as caught:
                bgremove.child_deps_available()
        self.assertIn("/other/python", str(caught.exception))
        self.assertIn("PIXELFORGE_BGREMOVE_PYTHON",
                      str(caught.exception))

    def test_failed_child_check_is_plain(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_python",
                               return_value="/other/python"), \
                mock.patch("subprocess.run",
                           return_value=self._completed(
                               stderr="boom\n", returncode=1)):
            with self.assertRaises(PixelForgeError) as caught:
                bgremove.child_deps_available()
        self.assertIn("boom", str(caught.exception))

    def test_require_child_ready_returns_model(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_deps_available",
                               return_value=(True, [])), \
                mock.patch.object(bgremove, "find_model",
                                  return_value=Path(
                                      "/m/u2net.onnx")):
            self.assertEqual(bgremove.require_child_ready(),
                             Path("/m/u2net.onnx"))

    def test_require_child_ready_missing_deps_raises(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_deps_available",
                               return_value=(False, ["rembg"])):
            with self.assertRaises(PixelForgeError) as caught:
                bgremove.require_child_ready()
        self.assertIn("rembg", str(caught.exception))

    def test_require_child_ready_missing_model_raises(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_deps_available",
                               return_value=(True, [])), \
                mock.patch.object(bgremove, "find_model",
                                  return_value=None):
            with self.assertRaises(PixelForgeError) as caught:
                bgremove.require_child_ready()
        self.assertIn("not downloaded", str(caught.exception))


class RemoveBackgroundTests(unittest.TestCase):
    def _solid(self, width=4, height=4, color=(200, 0, 0, 255)):
        image = Image(width, height)
        data = image.data
        for i in range(0, len(data), 4):
            data[i:i + 4] = bytes(color)
        return image

    def test_removes_right_half(self):
        install_fake_rembg(self)
        source = self._solid()
        before = bytes(source.data)
        result = remove_background(source, session=FakeSession())
        self.assertEqual(result.width(), 4)
        self.assertEqual(result.height(), 4)
        data = result.data
        # Left half opaque, right half transparent; RGB kept.
        for y in range(4):
            for x in range(4):
                off = (y * 4 + x) * 4
                self.assertEqual(tuple(data[off:off + 3]),
                                 (200, 0, 0))
                self.assertEqual(data[off + 3],
                                 255 if x < 2 else 0)
        # The input is left alone.
        self.assertEqual(bytes(source.data), before)

    def test_passed_session_is_reused(self):
        calls = install_fake_rembg(self)
        session = FakeSession()
        remove_background(self._solid(), session=session)
        self.assertEqual(calls["new_session"], [])
        self.assertEqual(len(calls["remove"]), 1)
        self.assertIs(calls["remove"][0][1], session)

    def test_new_session_is_cpu_only(self):
        import pixel_forge.bgremove as bgremove

        calls = install_fake_rembg(self)
        model = Path(tempfile.mkdtemp()) / "u2net.onnx"
        model.write_bytes(b"x")
        self.addCleanup(lambda: shutil.rmtree(
            model.parent, ignore_errors=True))
        with mock.patch.object(bgremove, "find_model",
                               return_value=model):
            remove_background(self._solid())
        self.assertEqual(len(calls["new_session"]), 1)
        name, kwargs = calls["new_session"][0]
        self.assertEqual(name, "u2net")
        self.assertEqual(kwargs.get("providers"),
                         ["CPUExecutionProvider"])

    def test_model_outside_standard_homes_sets_u2net_home(self):
        import pixel_forge.bgremove as bgremove

        install_fake_rembg(self)
        tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(tmp, ignore_errors=True))
        model = Path(tmp) / "u2net.onnx"
        model.write_bytes(b"x")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("U2NET_HOME", None)
            with mock.patch.object(bgremove, "find_model",
                                   return_value=model):
                remove_background(self._solid())
            self.assertEqual(os.environ.get("U2NET_HOME"), tmp)

    def test_empty_image_refused(self):
        install_fake_rembg(self)
        with self.assertRaises(PixelForgeError) as caught:
            remove_background(Image(0, 0),
                              session=FakeSession())
        self.assertIn("no size", str(caught.exception))

    def test_missing_deps_refused(self):
        hide_modules(self, "rembg")
        with self.assertRaises(PixelForgeError):
            remove_background(self._solid(),
                              session=FakeSession())

    def test_missing_model_refused(self):
        import pixel_forge.bgremove as bgremove

        install_fake_rembg(self)
        with mock.patch.object(bgremove, "find_model",
                               return_value=None):
            with self.assertRaises(PixelForgeError) as caught:
                remove_background(self._solid())
        self.assertIn("not downloaded", str(caught.exception))

    def test_unloadable_model_is_plain(self):
        import pixel_forge.bgremove as bgremove

        def broken_session(name, **kwargs):
            raise RuntimeError("onnx load failed")

        fake_rembg = types.ModuleType("rembg")
        fake_rembg.new_session = broken_session
        fake_rembg.remove = lambda *a, **k: None
        saved = {name: sys.modules.get(name)
                 for name in ("rembg", "onnxruntime")}
        sys.modules["rembg"] = fake_rembg
        sys.modules["onnxruntime"] = types.ModuleType(
            "onnxruntime")

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.addCleanup(restore)
        model = Path(tempfile.mkdtemp()) / "u2net.onnx"
        model.write_bytes(b"junk")
        self.addCleanup(lambda: shutil.rmtree(
            model.parent, ignore_errors=True))
        with mock.patch.object(bgremove, "find_model",
                               return_value=model):
            with self.assertRaises(PixelForgeError) as caught:
                remove_background(self._solid())
        message = str(caught.exception)
        self.assertIn("could not load", message)
        self.assertIn(str(model), message)

    def test_failed_remove_is_plain(self):
        def failing_remove(pil_image, session=None, **kwargs):
            raise RuntimeError("inference exploded")

        install_fake_rembg(self, remove_fn=failing_remove)
        with self.assertRaises(PixelForgeError) as caught:
            remove_background(self._solid(),
                              session=FakeSession())
        self.assertIn("background removal failed",
                      str(caught.exception))


class ChildCommandTests(unittest.TestCase):
    def test_default_command(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIXELFORGE_BGREMOVE_PYTHON", None)
            self.assertEqual(
                child_command("in.png", "out.png"),
                [sys.executable, "-m", "pixel_forge.bgremove",
                 "in.png", "out.png"])

    def test_python_override(self):
        python = Path(sys.executable)
        with mock.patch.dict(
                os.environ,
                {"PIXELFORGE_BGREMOVE_PYTHON": str(python)}):
            self.assertEqual(child_python(), str(python))
        with mock.patch.dict(
                os.environ,
                {"PIXELFORGE_BGREMOVE_PYTHON": "/no/such/python"}):
            self.assertEqual(child_python(), sys.executable)


class RemoveBackgroundFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.src = Path(self.tmp) / "in.png"
        self.dst = Path(self.tmp) / "out.png"
        write_png(Image(2, 2), self.src)

    def _copy_command(self, src, dst):
        return [sys.executable, "-c",
                "import shutil, sys; "
                "shutil.copy(sys.argv[1], sys.argv[2])",
                str(src), str(dst)]

    def test_success_writes_output(self):
        import pixel_forge.bgremove as bgremove

        with mock.patch.object(bgremove, "child_command",
                               self._copy_command):
            remove_background_file(self.src, self.dst)
        self.assertTrue(self.dst.is_file())
        self.assertGreater(self.dst.stat().st_size, 0)

    def test_failure_surfaces_child_output(self):
        import pixel_forge.bgremove as bgremove

        def failing(src, dst):
            return [sys.executable, "-c",
                    "import sys; print('child blew up', "
                    "file=sys.stderr); sys.exit(1)"]

        with mock.patch.object(bgremove, "child_command",
                               failing):
            with self.assertRaises(PixelForgeError) as caught:
                remove_background_file(self.src, self.dst)
        self.assertIn("child blew up", str(caught.exception))
        self.assertFalse(self.dst.exists())

    def test_cancel_kills_the_child(self):
        import pixel_forge.bgremove as bgremove

        def sleeping(src, dst):
            return [sys.executable, "-c",
                    "import time; time.sleep(30)"]

        event = threading.Event()
        timer = threading.Timer(0.5, event.set)
        timer.start()
        try:
            with mock.patch.object(bgremove, "child_command",
                                   sleeping):
                with self.assertRaises(RemovalCancelled):
                    remove_background_file(self.src, self.dst,
                                           cancel_event=event)
        finally:
            timer.cancel()
        self.assertFalse(self.dst.exists())

    def test_timeout_is_plain(self):
        import pixel_forge.bgremove as bgremove

        def sleeping(src, dst):
            return [sys.executable, "-c",
                    "import time; time.sleep(30)"]

        with mock.patch.object(bgremove, "child_command",
                               sleeping):
            with self.assertRaises(PixelForgeError) as caught:
                remove_background_file(self.src, self.dst,
                                       timeout_s=1)
        self.assertIn("timed out", str(caught.exception))

    def test_exit_zero_without_output_is_failure(self):
        import pixel_forge.bgremove as bgremove

        def silent(src, dst):
            return [sys.executable, "-c", "pass"]

        with mock.patch.object(bgremove, "child_command",
                               silent):
            with self.assertRaises(PixelForgeError):
                remove_background_file(self.src, self.dst)

    def test_unrunnable_command_is_plain(self):
        import pixel_forge.bgremove as bgremove

        def missing(src, dst):
            return ["/no/such/python", "-c", "pass"]

        with mock.patch.object(bgremove, "child_command",
                               missing):
            with self.assertRaises(PixelForgeError) as caught:
                remove_background_file(self.src, self.dst)
        self.assertIn("could not run", str(caught.exception))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.src = Path(self.tmp) / "in.png"
        self.dst = Path(self.tmp) / "out.png"
        write_png(Image(2, 2), self.src)

    def test_wrong_arg_count(self):
        self.assertEqual(main(["only-one"]), 1)
        self.assertEqual(main([]), 1)

    def test_success(self):
        import pixel_forge.bgremove as bgremove

        result = Image(2, 2)
        with mock.patch.object(bgremove, "remove_background",
                               return_value=result):
            self.assertEqual(
                main([str(self.src), str(self.dst)]), 0)
        self.assertTrue(self.dst.is_file())

    def test_missing_source_fails(self):
        self.assertEqual(
            main([str(Path(self.tmp) / "nope.png"),
                  str(self.dst)]), 1)
        self.assertFalse(self.dst.exists())


@unittest.skipUnless(
    find_model() is not None and deps_available()[0],
    "background-removal model or packages not installed")
class RealModelTests(unittest.TestCase):
    """The real u2net model on a synthetic disc. Skipped unless
    the model and the packages are actually installed — a suite
    run never downloads anything."""

    def test_disc_segments(self):
        result = remove_background(make_disc())
        self.assertEqual(result.width(), 320)
        self.assertEqual(result.height(), 320)
        data = result.data
        total = 320 * 320
        transparent = sum(1 for i in range(3, len(data), 4)
                          if data[i] == 0)
        opaque = sum(1 for i in range(3, len(data), 4)
                     if data[i] == 255)
        # Most of the frame is background (transparent); the
        # disc itself stays mostly opaque.
        self.assertGreater(transparent / total, 0.4)
        self.assertGreater(opaque / total, 0.05)
        # Corners are background, the center is the disc.
        self.assertLess(data[3], 64)
        center = data[((160 * 320) + 160) * 4 + 3]
        self.assertGreater(center, 192)


if __name__ == "__main__":
    unittest.main()
