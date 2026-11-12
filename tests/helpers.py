"""Shared test helpers — small synthetic images, no fixtures needed."""
import contextlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtGui import QImage, qRgba  # noqa: E402


STUB_ENGINE_SOURCE = '''#!/usr/bin/env python3
"""Fake sd-cli for tests: writes a small PNG whose color depends on
the -s seed, logs argv, optionally sleeps or fails."""
import os
import struct
import sys
import time
import zlib


def write_png(path, w, h, rgb):
    raw = b"".join(b"\\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)

    png = b"\\x89PNG\\r\\n\\x1a\\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main():
    argv = sys.argv[1:]
    log = os.environ.get("STUB_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(argv) + "\\n")
    if os.environ.get("STUB_FAIL") == "1":
        print("stub engine: failing on purpose", file=sys.stderr)
        return 1
    delay = float(os.environ.get("STUB_DELAY", "0"))
    if delay:
        time.sleep(delay)
    out = None
    seed = 0
    i = 0
    while i < len(argv):
        if argv[i] == "-o" and i + 1 < len(argv):
            out = argv[i + 1]
        if argv[i] == "-s" and i + 1 < len(argv):
            seed = int(argv[i + 1])
        i += 1
    if out is None:
        print("stub engine: no -o given", file=sys.stderr)
        return 1
    rgb = (seed % 251, (seed // 251) % 251,
           (seed // 251 // 251) % 251)
    write_png(out, 64, 64, rgb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def write_stub_engine(directory, name="sd-cli"):
    """Write the fake sd-cli into directory, make it executable,
    and return its path. It answers the real sd-cli flags (-m, -p,
    -n, --steps, --cfg-scale, --sampling-method, -W, -H, -s, -o)
    and writes a seed-colored 64x64 PNG to the -o path.

    Env knobs: STUB_LOG (append argv), STUB_FAIL=1 (exit 1),
    STUB_DELAY (seconds to sleep per image).
    """
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(STUB_ENGINE_SOURCE)
    os.chmod(path, 0o755)
    return path


_STUB_ENV_KEYS = ("PIXELFORGE_SD_CLI", "PIXELFORGE_SD_MODEL",
                  "STUB_LOG", "STUB_FAIL", "STUB_DELAY")


@contextlib.contextmanager
def stub_env(engine, model, extra=None):
    """Point Pixel Forge at a stub engine + dummy model for the
    block, restoring the environment afterwards. extra sets more
    stub env vars (STUB_LOG, STUB_FAIL, STUB_DELAY)."""
    saved = {key: os.environ.get(key) for key in _STUB_ENV_KEYS}
    os.environ["PIXELFORGE_SD_CLI"] = str(engine)
    os.environ["PIXELFORGE_SD_MODEL"] = str(model)
    for key, value in (extra or {}).items():
        os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ensure_app():
    """A QApplication instance, shared by the whole suite.

    It must be a QApplication, not a plain QCoreApplication: the GUI
    tests create widgets, and a QCoreApplication created first cannot
    be upgraded (Qt aborts on "Cannot create a QWidget without
    QApplication"). Offscreen is forced when no platform was chosen,
    so the suite never needs a display.
    """
    from PySide6.QtWidgets import QApplication
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def make_gradient_image(width=200, height=100):
    """Red ramps left to right, green top to bottom, blue fixed."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    for y in range(height):
        for x in range(width):
            image.setPixel(x, y, qRgba(
                x * 255 // max(1, width - 1),
                y * 255 // max(1, height - 1),
                128, 255))
    return image


def make_transparent_image(width=64, height=64):
    """Left half opaque red, right half fully transparent."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    for y in range(height):
        for x in range(width // 2):
            image.setPixel(x, y, qRgba(255, 0, 0, 255))
    return image


def make_solid_image(width, height, r, g, b, a=255):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(qRgba(r, g, b, a))
    return image


def write_image(image, path, fmt="PNG"):
    assert image.save(str(path), fmt), "could not write %s" % path
    return str(path)


def make_test_video(path, duration=2, fps=24, width=160, height=120,
                    fmt="mp4"):
    """A short video with moving shapes, generated by ffmpeg's
    testsrc pattern. Raises if ffmpeg is missing or fails — the
    video tests skip in that case (see test_video.py)."""
    import subprocess
    codec = {"mp4": "libx264", "webm": "libvpx"}.get(fmt, "libx264")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", "testsrc=duration=%s:size=%dx%d:rate=%d"
           % (duration, width, height, fps),
           "-c:v", codec, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, timeout=120)
    return str(path)
