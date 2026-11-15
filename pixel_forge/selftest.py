"""Live generation verification harness (task 70) — Qt-free,
stdlib-only.

One command that proves the Prompt tab's live path end to end
on the machine it runs on: check the engine and the model are
really there, run one real 4-step generation through the same
imagegen path the Prompt tab uses, pixelize the result the
same way, and report timing + output in plain words.

The live path was proven once, by hand, on 2026-11-12 (a real
sd.cpp build from source, the real 2.1 GB model, prompt "a
wooden stool", 3 candidates + pixelized outputs). This module
makes that proof repeatable: the same prompt, the same 4
steps, a fixed seed, one candidate — so the same machine
produces the same picture every time, and a failed run says
exactly which stage failed and why.

run_self_test never raises for an expected failure — a missing
engine, a missing or partial model, a failed generation, a
failed pixelize all come back as a report with state "failed"
and a plain reason, so the command line can print the report
and exit 1 instead of crashing. The report is plain JSON-safe
values (strings, numbers, booleans, None), so --json can emit
it as-is.

Outputs live under ~/.local/share/pixel-forge/self-test/, the
installer's own home (where the engine build folder and the
venv already live) — a dedicated folder, not the install dir
and not a litter of files next to the invocation. Each run is
a timestamped batch folder inside it, named the way every
prompt batch is named, so old runs are a proof record the
user can delete when they want the space back.
"""

import time
from pathlib import Path

from .imagegen import (DEFAULT_SIZE, GENERATION_MIN_RAM_BYTES,
                       LCM_STEPS, MODEL_FILENAME, MODEL_SIZE_BYTES,
                       available_ram_bytes, engine_missing_text,
                       find_engine, find_model, format_bytes,
                       generate, is_partial_model, model_missing_text,
                       new_batch, pixelize_candidates)
from .pixelize import PixelForgeError

# The known-good proof prompt from 2026-11-12, the LCM pins, and
# a fixed seed: the same machine produces the same picture every
# time, which is what makes the proof repeatable.
SELF_TEST_PROMPT = "a wooden stool"
SELF_TEST_SEED = 42
SELF_TEST_STEPS = LCM_STEPS
SELF_TEST_SIZE = DEFAULT_SIZE
SELF_TEST_WIDTH = 32


def self_test_home():
    """Where self-test runs live: the installer's home, under a
    self-test folder of its own."""
    return (Path.home() / ".local" / "share" / "pixel-forge"
            / "self-test")


def format_duration(seconds):
    """A duration in plain words: "0.4 s", "12 s", "3 min 12 s".
    Under a minute it is seconds (one decimal when under 10);
    over a minute it is whole minutes and seconds."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        if seconds < 10:
            return "%.1f s" % seconds
        return "%d s" % round(seconds)
    minutes = int(seconds // 60)
    rest = int(round(seconds - minutes * 60))
    if rest == 60:
        minutes += 1
        rest = 0
    return "%d min %d s" % (minutes, rest)


def _failed(checks, timings, reason, batch_dir=None):
    """A failed report: the checks and timings gathered so far,
    the plain reason, and the batch folder when one was made."""
    return {"state": "failed", "checks": checks,
            "timings": timings, "reason": reason,
            "batch_dir": str(batch_dir) if batch_dir else None,
            "candidate": None, "pixel_file": None,
            "seed": SELF_TEST_SEED, "steps": SELF_TEST_STEPS,
            "prompt": SELF_TEST_PROMPT}


def run_self_test(out_dir=None, progress=None):
    """Run the live generation self-test and return its report.

    out_dir is where the run's batch folder goes (the self-test
    home when None). progress, when given, is called as
    progress(stage, message) as each stage lands — the command
    line prints the messages so the user sees the run working
    through its minutes-long generation.
    """
    def tell(stage, message):
        if progress is not None:
            progress(stage, message)

    started = time.monotonic()
    checks = {"engine": None, "model": None,
              "model_size": None, "model_is_pinned": None,
              "model_partial": None, "ram_bytes": None,
              "ram_warning": None}
    timings = {"checks": None, "generation": None,
               "pixelize": None, "total": None}

    # Stage 1: the prerequisites. A missing engine or model is
    # a plain report, never a traceback — and a partial model is
    # not a model (v0.51: a file named like the model at the
    # wrong size is an interrupted download).
    engine = find_engine()
    model = find_model()
    checks["engine"] = str(engine) if engine is not None else None
    checks["model"] = str(model) if model is not None else None
    checks["ram_bytes"] = available_ram_bytes()
    if checks["ram_bytes"] is not None and \
            checks["ram_bytes"] < GENERATION_MIN_RAM_BYTES:
        checks["ram_warning"] = (
            "only %s of memory is free; the image model needs "
            "about 2.1 GB, so generation may be slow or fail"
            % format_bytes(checks["ram_bytes"]))
    timings["checks"] = time.monotonic() - started
    # The total so far: a run that fails here ends here, so the
    # report still carries a real total. The later stages
    # overwrite it with the full elapsed time.
    timings["total"] = timings["checks"]
    if engine is None:
        return _failed(checks, timings, engine_missing_text())
    if model is None:
        return _failed(checks, timings, model_missing_text())
    checks["model_size"] = model.stat().st_size
    checks["model_is_pinned"] = model.name == MODEL_FILENAME
    checks["model_partial"] = is_partial_model(model)
    if checks["model_partial"]:
        return _failed(
            checks, timings,
            "the image model at %s is only %s of the %s it should "
            "be: the download stopped partway. Delete it and "
            "download again, or run the installer with the image "
            "engine ticked (it resumes the download)."
            % (model, format_bytes(checks["model_size"]),
               format_bytes(MODEL_SIZE_BYTES)))
    tell("checks", "engine: %s" % engine)
    tell("checks", "model: %s (%s)"
         % (model, format_bytes(checks["model_size"])))
    if not checks["model_is_pinned"]:
        tell("checks", "note: this is not the pinned LCM file %s"
             % MODEL_FILENAME)
    if checks["ram_warning"] is not None:
        tell("checks", "warning: %s" % checks["ram_warning"])

    # Stage 2: one real generation through the Prompt tab's own
    # path — the same new_batch + generate the GUI and the
    # prompt command use, with the fixed prompt, seed, and
    # steps. No short timeout: a CPU-only generation takes
    # minutes, and the engine's own per-image timeout (30 min)
    # is the bound.
    out_home = Path(out_dir) if out_dir is not None \
        else self_test_home()
    tell("generate",
         "generating one picture from %r (%d steps, seed %d). "
         "This takes a few minutes on a CPU-only machine"
         % (SELF_TEST_PROMPT, SELF_TEST_STEPS, SELF_TEST_SEED))
    gen_started = time.monotonic()
    bdir = None
    try:
        _batch, bdir = new_batch(
            out_home, SELF_TEST_PROMPT, count=1,
            size=SELF_TEST_SIZE, steps=SELF_TEST_STEPS,
            seeds=[SELF_TEST_SEED])
        batch = generate(bdir)
    except (PixelForgeError, OSError) as exc:
        timings["generation"] = time.monotonic() - gen_started
        timings["total"] = time.monotonic() - started
        return _failed(checks, timings, str(exc),
                       batch_dir=bdir)
    timings["generation"] = time.monotonic() - gen_started
    landed = [c for c in batch["candidates"]
              if c["status"] == "pending"]
    if not landed:
        error = batch["candidates"][0].get("error", "unknown error") \
            if batch["candidates"] else "no candidates"
        timings["total"] = time.monotonic() - started
        return _failed(
            checks, timings,
            "the generation failed: %s" % error,
            batch_dir=bdir)
    candidate = landed[0]["file"]
    tell("generate", "generated %s in %s"
         % (candidate, format_duration(timings["generation"])))

    # Stage 3: pixelize the result the way the Prompt tab does
    # (32 px, the image's own palette, 16 colors, dithered).
    pix_started = time.monotonic()
    try:
        pairs, failures = pixelize_candidates(
            bdir, width=SELF_TEST_WIDTH)
    except PixelForgeError as exc:
        timings["pixelize"] = time.monotonic() - pix_started
        timings["total"] = time.monotonic() - started
        return _failed(checks, timings, str(exc), batch_dir=bdir)
    timings["pixelize"] = time.monotonic() - pix_started
    if failures:
        timings["total"] = time.monotonic() - started
        return _failed(
            checks, timings,
            "could not pixelize %s: %s"
            % (failures[0][0], failures[0][1]),
            batch_dir=bdir)
    pixel_file = pairs[0][1] if pairs else None
    tell("pixelize", "pixelized %s in %s"
         % (pixel_file, format_duration(timings["pixelize"])))

    timings["total"] = time.monotonic() - started
    cand_path = bdir / candidate
    pix_path = bdir / pixel_file if pixel_file else None
    return {"state": "ok", "checks": checks, "timings": timings,
            "reason": None, "batch_dir": str(bdir),
            "candidate": candidate,
            "candidate_bytes": cand_path.stat().st_size,
            "pixel_file": pixel_file,
            "pixel_bytes": pix_path.stat().st_size
            if pix_path is not None and pix_path.exists() else None,
            "seed": SELF_TEST_SEED, "steps": SELF_TEST_STEPS,
            "prompt": SELF_TEST_PROMPT}
