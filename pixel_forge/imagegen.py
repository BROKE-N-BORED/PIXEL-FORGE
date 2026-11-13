"""Prompt to sprite — local image generation, then the Item 1 pixelizer.

A prompt goes in, 3 candidate pictures come out; Joshua picks the
keeper, and the keeper goes through the same pixelizer the Image tab
uses. Nothing here is networked at run time: the engine is
leejet's stable-diffusion.cpp (binary name `sd-cli`) driving an
SD 1.5 LCM model — the class that fits an 8 GB, CPU-only machine:
~2.3 GB RAM and minutes per picture instead of the half-hour a
full-step model takes. Pixel Forge shells out to it exactly the way
it shells out to ffmpeg: an external tool, a loud failure when it
is missing, and nothing faked when it is not there.

Pins (verified live 2026-11-11 — do not substitute):
  engine release master-1086-465adf3 — NOTE the prebuilt Linux
  binary needs glibc 2.43 (Ubuntu 26.04); on Mint (glibc 2.39)
  the engine is built from source instead, which install.sh
  --with-image-engine does (and the GUI installer offers).
  model: LCM_Dreamshaper_v7-f16.gguf (2,135 MB) from Hugging Face
  repo Steward/lcm-dreamshaper-v7-gguf (sole file in the repo).
  LCM = 4 steps, 512x512, cfg 1.5. The installers check the engine
  out at the pinned release and SHA-256-check the model download
  (MODEL_SHA256) — a file that does not match is deleted, never
  accepted as the model.

The model home is SHARED with the Design Shell: ~/design-shell-models/
is checked first, so if Joshua already pulled the model for the
Design Shell, Pixel Forge uses the same file and downloads nothing.
~/pixel-forge-models/ is the Pixel Forge home if the shared one is
empty, and PIXELFORGE_SD_MODEL overrides both.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .pixelize import PixelForgeError, pixelize_file

MODEL_FILENAME = "LCM_Dreamshaper_v7-f16.gguf"
MODEL_URL = ("https://huggingface.co/Steward/lcm-dreamshaper-v7-gguf/"
             "resolve/main/" + MODEL_FILENAME)
ENGINE_REPO = "https://github.com/leejet/stable-diffusion.cpp"
ENGINE_RELEASE = "master-1086-465adf3"
# SHA-256 of the pinned model file, recomputed 2026-11-12 from the
# verified copy (2,134,675,232 bytes). Both installers check every
# model download against this and refuse a file that does not
# match — a partial or corrupt download must never pass as the
# model. This is the ONE place the hash lives: the GUI installer
# imports it, and install.sh carries a literal copy that the
# installer tests check against this value.
MODEL_SHA256 = ("0642060766117fdeb8cae0ccdc73ee974ac5779be1112d0a"
                "82f2b965bbd00e15")

DEFAULT_COUNT = 3
DEFAULT_SIZE = 512
LCM_STEPS = 4
LCM_CFG = 1.5
DEFAULT_NEGATIVE = "blurry, deformed, distorted, low quality, watermark"
PER_IMAGE_TIMEOUT_S = 1800
MAX_PROMPT_LEN = 1000


class GenerationCancelled(Exception):
    """The user cancelled a generation run. Not an error — the batch
    keeps whatever candidates already landed."""


# ------------------------------------------------------------------ finding

def _is_executable(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_engine() -> Path | None:
    """The sd-cli binary, or None. Env override wins, like ffmpeg's."""
    override = os.environ.get("PIXELFORGE_SD_CLI")
    if override and _is_executable(override):
        return Path(override)
    for name in ("sd-cli", "sd"):
        found = shutil.which(name)
        if found:
            return Path(found)
    home = Path.home()
    for candidate in (Path("/opt/sd.cpp/bin/sd-cli"),
                      Path("/opt/sd.cpp/sd-cli"),
                      home / ".local" / "bin" / "sd-cli",
                      # The sd.cpp build puts sd-cli in build/bin —
                      # install.sh already checks that path, and
                      # this used to check build/sd-cli, which the
                      # build never produces (review B15).
                      home / "sd.cpp" / "build" / "bin" / "sd-cli"):
        if _is_executable(candidate):
            return candidate
    return None


def model_homes() -> list:
    """Model folders in search order: shared Design Shell home first,
    then Pixel Forge's own home."""
    home = Path.home()
    return [home / "design-shell-models", home / "pixel-forge-models"]


def find_model() -> Path | None:
    """The model file, or None. Env override wins; then the model
    homes — the exact pinned file first, then any .gguf in the home
    (a renamed or newer LCM build still works)."""
    override = os.environ.get("PIXELFORGE_SD_MODEL")
    if override and Path(override).is_file():
        return Path(override)
    for home_dir in model_homes():
        exact = home_dir / MODEL_FILENAME
        if exact.is_file():
            return exact
        if home_dir.is_dir():
            ggufs = sorted(home_dir.glob("*.gguf"))
            if ggufs:
                return ggufs[0]
    return None


def engine_status() -> dict:
    """What the GUI status line shows: the engine and model paths
    (or None), plus the places that were checked."""
    engine = find_engine()
    model = find_model()
    home = Path.home()
    engine_checked = [
        "PIXELFORGE_SD_CLI", "PATH (sd-cli, sd)",
        "/opt/sd.cpp/bin/sd-cli", "/opt/sd.cpp/sd-cli",
        str(home / ".local" / "bin" / "sd-cli"),
        str(home / "sd.cpp" / "build" / "bin" / "sd-cli"),
    ]
    model_checked = ["PIXELFORGE_SD_MODEL"] + [
        str(h / MODEL_FILENAME) for h in model_homes()]
    return {"engine": engine, "model": model,
            "engine_checked": engine_checked,
            "model_checked": model_checked}


def engine_missing_text() -> str:
    return ("the image engine (stable-diffusion.cpp) is not installed "
            "— nothing was generated and nothing was faked. Fix: tick "
            "\"Install the image engine\" in the Pixel Forge installer "
            "and press Install again, or run ./install.sh "
            "--with-image-engine in the pixel-forge folder (it builds "
            "the engine from source; the prebuilt Linux binary needs "
            "a newer system library than Mint ships).")


def model_missing_text() -> str:
    return (f"the image model is not downloaded — nothing was "
            f"generated. Fix: download {MODEL_FILENAME} (about "
            f"2.1 GB) from {MODEL_URL} and put it in "
            f"~/design-shell-models/ or ~/pixel-forge-models/, or "
            f"run the installer with the image engine ticked (it "
            f"downloads the model for you).")


def require_engine() -> Path:
    engine = find_engine()
    if engine is None:
        raise PixelForgeError(engine_missing_text())
    return engine


def require_model() -> Path:
    model = find_model()
    if model is None:
        raise PixelForgeError(model_missing_text())
    return model


# ------------------------------------------------------- install pins

def engine_clone_command(dest) -> list:
    """The git clone that fetches the engine at the pinned release.

    --branch pins the tag: a plain clone fetches whatever master
    HEAD happens to be that day, which is how the installers used
    to fetch it — the pin was defined here and referenced nowhere.
    --recursive pulls the ggml submodule; without it the build
    fails. Kept separate, like build_command, so the installers
    and the tests share the one command."""
    return ["git", "clone", "--depth", "1",
            "--branch", ENGINE_RELEASE,
            "--recursive", ENGINE_REPO, str(dest)]


def model_download_command(dest) -> list:
    """The curl download for the model. -C - resumes a partial
    file instead of restarting a 2.1 GB download from scratch."""
    return ["curl", "-fSL", "-C", "-", MODEL_URL, "-o", str(dest)]


def file_sha256(path) -> str:
    """SHA-256 of a file, read in 1 MB chunks — the model is
    2.1 GB, so it is never read into memory whole."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_model_file(path) -> None:
    """Raise PixelForgeError unless the file at `path` is exactly
    the pinned model — its SHA-256 must equal MODEL_SHA256. The
    message names the expected and actual hashes, so a corrupt or
    partial download is never mistaken for the real model."""
    actual = file_sha256(path)
    if actual != MODEL_SHA256:
        raise PixelForgeError(
            "the image model at %s failed its SHA-256 check — "
            "expected %s, got %s (the file is not the pinned "
            "model: delete it and download again)"
            % (path, MODEL_SHA256, actual))


# ------------------------------------------------------------------- prompt

def validate_prompt(prompt: str) -> str:
    """A prompt, stripped, or a loud refusal for an empty or absurd
    one. Nothing is generated from a blank prompt."""
    text = (prompt or "").strip()
    if not text:
        raise PixelForgeError(
            "type a prompt first — describe the sprite you want "
            "(for example: a knight with a red shield)")
    if len(text) > MAX_PROMPT_LEN:
        raise PixelForgeError(
            "that prompt is %d characters — keep it under %d"
            % (len(text), MAX_PROMPT_LEN))
    return text


def slugify(text: str) -> str:
    words = [w.lower() for w in text.replace("_", " ").split()
             if any(c.isalnum() for c in w)]
    cleaned = ["".join(c for c in w if c.isalnum()) for w in words]
    return "-".join(w for w in cleaned if w)[:40] or "image"


# -------------------------------------------------------------------- batch

def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_batch(batch_dir: str | Path) -> dict:
    """Read a batch record. Raises PixelForgeError — never a raw
    json error or KeyError — on a missing, unreadable, or
    wrong-shaped batch.json, naming the file and the problem
    (review B5: a corrupt batch used to raise an uncaught
    exception out of a GUI slot). The file is left alone:
    never deleted, never "repaired"."""
    path = Path(batch_dir) / "batch.json"
    if not path.exists():
        raise PixelForgeError("no prompt batch at %s" % batch_dir)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise PixelForgeError(
            "prompt batch is unreadable: %s (%s)" % (path, exc))
    if not isinstance(data, dict):
        raise PixelForgeError(
            "prompt batch has the wrong shape: %s "
            "(expected an object, got %s)"
            % (path, type(data).__name__))
    if not isinstance(data.get("prompt"), str):
        raise PixelForgeError(
            "prompt batch has the wrong shape: %s (no prompt)"
            % path)
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        raise PixelForgeError(
            "prompt batch has the wrong shape: %s "
            "(no candidate list)" % path)
    for record in candidates:
        if not isinstance(record, dict) or \
                not isinstance(record.get("file"), str) or \
                not isinstance(record.get("status"), str) or \
                not isinstance(record.get("seed"), int):
            raise PixelForgeError(
                "prompt batch has the wrong shape: %s "
                "(a candidate is missing its file, seed, or status)"
                % path)
    return data


def save_batch(batch_dir: str | Path, batch: dict) -> None:
    """Write the batch record atomically: the full content goes to
    a temp file in the same directory, is flushed + fsynced, then
    os.replace moves it over batch.json. A crash or failure
    mid-save leaves the previous batch.json intact (or absent, if
    there was none) — never a half-written file. Raises
    PixelForgeError with a readable message on any write failure;
    the temp file is cleaned up."""
    path = Path(batch_dir) / "batch.json"
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(batch, indent=2) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PixelForgeError("could not write %s: %s" % (path, exc))


def new_batch(out_dir: str | Path, prompt: str,
              negative: str = DEFAULT_NEGATIVE,
              count: int = DEFAULT_COUNT,
              size: int = DEFAULT_SIZE) -> tuple:
    """Create the batch record; generation is a separate step.

    The batch lives in <out_dir>/<slug>-<HHMMSS>/ so a keeper stays
    reproducible: prompt, negative, seeds, engine, and model are all
    in batch.json next to the pictures.
    """
    prompt = validate_prompt(prompt)
    if count < 1:
        raise PixelForgeError("count must be at least 1")
    slug = slugify(prompt) + "-" + time.strftime("%H%M%S")
    parent = Path(out_dir)
    bdir = parent / slug
    suffix = 1
    while bdir.exists():
        suffix += 1
        bdir = parent / ("%s-%d" % (slug, suffix))
    bdir.mkdir(parents=True, exist_ok=True)
    batch = {
        "slug": bdir.name,
        "prompt": prompt,
        "negative": negative,
        "count_per_round": count,
        "size": size,
        "steps": LCM_STEPS,
        "cfg": LCM_CFG,
        "created": _stamp(),
        "engine": None,
        "model": None,
        "candidates": [],
        "keeper": None,
        "cancelled": False,
    }
    save_batch(bdir, batch)
    return batch, bdir


def set_keeper(batch_dir: str | Path, filename: str) -> dict:
    """Mark one landed candidate as the keeper. The keeper is the
    one that gets pixelized and exported."""
    batch = load_batch(batch_dir)
    record = next((c for c in batch["candidates"]
                   if c["file"] == filename), None)
    if record is None:
        raise PixelForgeError(
            "no candidate %r in this batch" % filename)
    if record["status"] != "pending":
        raise PixelForgeError(
            "candidate %r did not generate (%s)"
            % (filename, record["status"]))
    for candidate in batch["candidates"]:
        if candidate["status"] == "kept":
            candidate["status"] = "pending"
    record["status"] = "kept"
    batch["keeper"] = filename
    save_batch(batch_dir, batch)
    return batch


def _fresh_seed(batch: dict, rng: random.Random) -> int:
    used = {c["seed"] for c in batch["candidates"]}
    seed = rng.randint(1, 2**31 - 1)
    while seed in used:
        seed = rng.randint(1, 2**31 - 1)
    return seed


# ---------------------------------------------------------------- generation

def build_command(engine, model, prompt, negative, seed, size, out):
    """The exact sd-cli invocation for one candidate. Kept separate
    so tests can check the command without running the engine."""
    return [str(engine), "-m", str(model),
            "-p", prompt, "-n", negative,
            "--steps", str(LCM_STEPS), "--cfg-scale", str(LCM_CFG),
            "--sampling-method", "lcm",
            "-W", str(size), "-H", str(size),
            "-s", str(seed), "-o", str(out)]


def _run_one(cmd, out, timeout_s, cancel_event):
    """Run one sd-cli command. Returns (ok, error_text).

    Polls so a cancel event can kill the process mid-generation
    instead of waiting out the timeout — a 1–5 minute CPU run must
    stop when the user presses Cancel. The engine's output goes to
    a temp file, not a pipe: a pipe nobody reads fills up and
    deadlocks a long run, while the file keeps the last lines for
    the failure message.
    """
    try:
        log = tempfile.TemporaryFile(mode="w+")
    except OSError as exc:
        return False, "could not start the image engine: %s" % exc
    try:
        try:
            proc = subprocess.Popen(cmd, stdout=log,
                                    stderr=subprocess.STDOUT,
                                    text=True)
        except OSError as exc:
            return False, "could not run the image engine: %s" % exc
        deadline = time.monotonic() + timeout_s
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait()
                out.unlink(missing_ok=True)
                raise GenerationCancelled()
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                out.unlink(missing_ok=True)
                return False, "timed out after %ds" % timeout_s
            time.sleep(0.2)
        log.seek(0)
        output = log.read()
    finally:
        log.close()
    if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
        return True, ""
    out.unlink(missing_ok=True)
    detail = (output or "").strip().splitlines()
    return False, "; ".join(detail[-3:]) if detail else (
        "the image engine exited with code %d" % proc.returncode)


def generate(batch_dir: str | Path, count: int | None = None,
             timeout_s: int = PER_IMAGE_TIMEOUT_S,
             cancel_event=None, progress=None) -> dict:
    """Generate one round of candidates into the batch.

    progress, when given, is called after each image as
    progress(done, total, record) — the GUI worker uses it to emit
    signals; the CLI uses it for its line of output. A candidate
    that fails or times out is recorded as failed, and the round
    carries on with the next seed. Raises GenerationCancelled when
    the cancel event fires (the batch keeps what landed).
    """
    batch = load_batch(batch_dir)
    engine = require_engine()
    model = require_model()
    batch["engine"] = str(engine)
    batch["model"] = model.name
    n = count if count is not None else int(
        batch.get("count_per_round", DEFAULT_COUNT))
    size = int(batch.get("size", DEFAULT_SIZE))
    rng = random.Random()
    try:
        for done in range(n):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            seed = _fresh_seed(batch, rng)
            index = len(batch["candidates"]) + 1
            fname = "cand-%02d.png" % index
            out = Path(batch_dir) / fname
            cmd = build_command(engine, model, batch["prompt"],
                                batch.get("negative", ""), seed,
                                size, out)
            record = {"file": fname, "seed": seed, "status": "pending"}
            ok, error = _run_one(cmd, out, timeout_s, cancel_event)
            if not ok:
                record["status"] = "failed"
                record["error"] = error[-400:]
            batch["candidates"].append(record)
            save_batch(batch_dir, batch)
            if progress is not None:
                progress(done + 1, n, record)
    except GenerationCancelled:
        batch["cancelled"] = True
        save_batch(batch_dir, batch)
        raise
    return batch


def pixelize_candidates(batch_dir: str | Path, width=32, height=None,
                        keep_aspect=False, palette="image",
                        max_colors=16, dither=True) -> list:
    """Pixelize every landed candidate through the Item 1 core.

    Writes <stem>-pixel.png next to each candidate and records it in
    the batch. Failed candidates are skipped. Returns the list of
    (candidate file, pixel file) pairs.
    """
    batch = load_batch(batch_dir)
    pairs = []
    for record in batch["candidates"]:
        if record["status"] not in ("pending", "kept"):
            continue
        src = Path(batch_dir) / record["file"]
        pixel_name = "%s-pixel.png" % Path(record["file"]).stem
        dest = Path(batch_dir) / pixel_name
        pixelize_file(src, dest, width=width, height=height,
                      keep_aspect=keep_aspect, palette=palette,
                      max_colors=max_colors, dither=dither)
        record["pixel_file"] = pixel_name
        pairs.append((record["file"], pixel_name))
    save_batch(batch_dir, batch)
    return pairs


def prompt_to_sprites(prompt: str, out_dir: str | Path,
                      negative: str = DEFAULT_NEGATIVE,
                      count: int = DEFAULT_COUNT,
                      width=32, height=None, keep_aspect=False,
                      palette="image", max_colors=16, dither=True,
                      progress=None) -> tuple:
    """Full CLI pipeline: prompt in, candidates + pixelized versions
    out. Returns (batch, batch_dir)."""
    _batch, bdir = new_batch(out_dir, prompt, negative=negative,
                             count=count)
    batch = generate(bdir, progress=progress)
    landed = [c for c in batch["candidates"]
              if c["status"] == "pending"]
    if not landed:
        raise PixelForgeError(
            "all %d candidates failed — %s"
            % (len(batch["candidates"]),
               batch["candidates"][0].get("error", "unknown error")
               if batch["candidates"] else "no candidates"))
    pixelize_candidates(bdir, width=width, height=height,
                        keep_aspect=keep_aspect, palette=palette,
                        max_colors=max_colors, dither=dither)
    return load_batch(bdir), bdir
