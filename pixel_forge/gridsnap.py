"""Pixel-grid snap — clean up AI-generated pixel art (task 42).

AI-generated pixel art has no real grid: cells are a fractional
number of pixels wide, edges are blurred, stray colors creep in.
The fix follows the sprite-forge toolkit (MIT,
github.com/isabellagreco1997/sprite-forge, `spriteforge snap`):
find the native pixel grid, take the median color per cell,
quantise the palette — a real 1x sprite, sharper than the source.
This module is Pixel Forge's own stdlib-only implementation of
that approach (no numpy, no PIL): the detector below is ours, the
median-per-cell snap and the quantise step follow sprite-forge's.

Detection — second-derivative profiles, not variance:
  sprite-forge brute-forces every (size, phase) by within-cell
  variance, which needs numpy to be fast. In pure Python the
  same idea is scored from edge profiles instead, computed in
  one pass per direction. Two profiles per direction: H, the
  mean color step across each boundary, and D2, the mean change
  in that step from one boundary to the next. A sharp grid
  (nearest upscale) reads in H: steps are large on the grid
  lines and zero inside the cells. A blurred grid (bilinear
  upscale, AI output) does NOT read in H — the blur spreads
  each edge into a smooth ramp, so the step is nearly the same
  everywhere — but it reads in D2: the ramp's slope changes
  only at the cell centres, so D2 spikes there with the grid's
  period. The detector therefore scores every size on BOTH
  profiles, each picking its own winner (smallest in its
  band), and H wins when it finds a grid at or above the
  threshold: H reads real edges, so when it is confident the
  grid is real, and it is the only signal at cell 2 (D2's
  ratio is ~1 by construction there). D2 wins only when H
  finds nothing: D2 reads blurred grids (H is ~1 across a
  bilinear ramp), but it also reads substructure — a soft
  edge's two blend edges score as a cell-2 grid — so it never
  overrules H. A photo scores near 1 on both at every size.
  Once the PERIOD is found, the ORIGIN comes from H: when H
  peaks somewhere, the grid is sharp and
  the lines sit on H's peak; when H is flat, the grid is
  blurred and the lines sit half a cell past D2's peak (the
  centres). Candidates run from 2.0 to 64.0 pixels in 0.25
  steps (AI grids are fractional — a 32 px sprite rendered at
  512 px is exactly 16, but a 48 px sprite at 512 px is
  10.67), with a coarse phase search, a fine phase scan per
  size, and a fine refinement of the top candidates BEFORE the
  winner is picked (a fractional true size never lands on the
  coarse grid, and drift over dozens of cells knocks its
  coarse score below a multiple's). Each top candidate's
  halves, thirds and quarters are scored from scratch too:
  a fractional true size scores poorly at its nearest coarse
  size while its multiples score well, so the divisors are
  how it gets found at all. A score under
  GRID_SCORE_THRESHOLD means
  no grid: the function returns None rather than a guess.
  Scores are weighted by evidence — a size with only two grid
  lines in view cannot score highly, however strong those two
  lines look, so a small image cannot hallucinate a huge grid.
  And multiples of the true grid score nearly as well as the
  grid itself (their lines are a subset of the true lines), so
  among the sizes scoring within NEAR_TIE_FRACTION of the best
  the SMALLEST wins: the smallest grid explaining the edges is
  the true one. The clean test runs BEFORE the grid search: a
  1x sprite's hard edges score highly at small spurious sizes
  (a flat sprite measures ~42 at cell 2.75), so searching
  first would snap it to a grid it does not have — and a
  nearest upscale, which passes the clean test too, is caught
  by the strong-grid override (a score at or above
  STRONG_GRID_SCORE that repeats at most lines). Each
  direction votes only if it is not flat
  (a direction whose largest step is under FLAT_MAX_STEP
  carries no grid information — a vertically striped image is
  flat top-to-bottom, and its horizontal grid is still real);
  a non-flat direction always votes, even at 0.0, because
  steps that avoid the grid lines are evidence against it.
  An image that is already a clean 1x sprite reports (1.0, 0, 0)
  instead: few distinct colors and hard edges between
  neighbours (is_pixel_clean). A photo fails both tests — many
  colors, soft neighbours — and a noisy image fails on colors.

Snap — median per cell:
  each output pixel is the per-channel median of its cell's
  CORE — the middle half of the cell in each axis (the median,
  not the mean: one stray pixel must not drag the cell's
  color). The core, not the whole cell: across a blurred cell
  the pixels form a ramp from one neighbour's color to the
  other, and the median of the whole ramp is the ramp's
  midpoint, not the cell's own color; the core's median is the
  value at the cell centre, which is the cell's color (on a
  flat cell the two agree). A cell under half opaque reads as
  transparent, which trims blurred fringes, and the result is
  cropped to the solid bounding box, like sprite-forge's. The
  snap runs on the full image; only the detector samples rows,
  so a big photo stays fast.

Quantise reuses pixelize.resolve_palette + quantize — no second
quantiser — with dithering OFF: dithering would put back the
noise the snap just removed.

Limits, stated plainly:
  - A grid under 2 px cannot exist (1 px IS the grid), and a
    grid over 64 px is not searched — a 16 px sprite rendered
    at 1024 px is exactly 64, so that covers the realistic
    range.
  - Detection needs at least two grid lines in each direction
    to have anything to score, so a tiny image with a huge
    grid (a 48 px image at cell 16) may report no grid.
  - A photo of something genuinely regular (a brick wall, a
    tiled floor) HAS a grid, and the detector will find it —
    that is the detector working, not failing.
  - The 1x test is a heuristic (color count + edge sharpness):
    a 1x sprite dithered into hundreds of colors, or a photo
    posterised down to a few flat colors, can be misread.
    The snap itself never guesses — it only runs on a grid
    the detector scored above the threshold.
  - On a blurred grid the origin carries about 1 px of
    uncertainty: the D2 spike marking a cell centre is 2 px
    wide, so the origin can land a pixel off. The snap's core
    median tolerates that (the core is the middle half of
    the cell), so a 1 px origin error does not move the
    snapped colors. The size can read about 1% small on a
    small blurred image (7.92 for an 8 px grid on a 24-cell
    sprite — a divisor candidate wins the smallest-in-band
    rule there); the core median absorbs that too, and the
    size reads exactly on larger images.
  - Heavy per-pixel noise defeats detection: about 1% random
    speckle still detects, 2% reports no grid. Real AI
    output noise is smooth, not per-pixel random, so this
    is a harsh-case limit, not a common one.
  - A blurred grid at cell 2 is nearly undetectable (D2's
    ratio is ~1 by construction there, H is ~1.2 across the
    ramp), so a 2x bilinear upscale reports no grid rather
    than a wrong one. A sharp 2x upscale detects fine.
"""
from __future__ import annotations

import math

from .pixelize import PixelForgeError, quantize, resolve_palette
from .image import Image

# Candidate cell sizes, in pixels. Step 0.25, refined to 0.05
# around the winner (see detect_grid).
MIN_CELL = 2.0
MAX_CELL = 64.0
CELL_STEP = 0.25
# Phase step for the coarse search, as a fraction of the cell
# size (never finer than 0.5 px): a big cell needs fewer phases
# to find its peak, and the refinement nails the phase after.
PHASE_STEP_FRACTION = 1.0 / 16.0
PHASE_STEP_MIN = 0.5
# A candidate grid must score at least this well to count —
# calibrated on blurred upscales vs photos (see
# tests/test_gridsnap.py). A photo scores near 1.0; a blurred
# grid scores 2.0 and up (a sharp grid scores far higher).
GRID_SCORE_THRESHOLD = 2.0
# The 1x test: at most this many distinct opaque colors, and a
# mean NONZERO neighbour step of at least this many channel
# levels. Nonzero, not overall: a mostly-flat sprite has a low
# mean step (most neighbours are identical) but hard edges
# where it changes (a flat 32x32 sprite measures 5.7 overall,
# ~200 over its nonzero steps), while a blur's steps are small
# everywhere (a bilinear x2 blur measures ~5 over its nonzero
# steps, and 314 colors). A 32x32 sprite measures 8 colors /
# ~80 nonzero.
MAX_CLEAN_COLORS = 256
MIN_CLEAN_SHARPNESS = 20.0
# The floor under the interior mean in the direction ratio:
# small, because a heavily blurred grid's D2 spikes are only
# a few levels high and a floor of 1.0 would bury them.
RATIO_FLOOR = 0.25
# The detector samples at most this many rows (for the column
# profile) and columns (for the row profile), spread evenly —
# a 1024 px photo is scored from 256 rows, not 1024, so
# detection stays near a second on a big image.
DETECT_MAX_SAMPLES = 256
# A candidate size needs at least this many grid lines in each
# direction to be scored at all — fewer is not evidence.
MIN_GRID_LINES = 2
# Evidence weighting: a direction's ratio is multiplied by
# min(1, lines / FULL_EVIDENCE_LINES), so a size with only two
# lines in view scores at most a sixth of its raw ratio — a
# small image cannot hallucinate a huge grid off two lines.
# 12, not 8: with 8, a 7-line size (cell 32 on a 256 px image)
# keeps 87% of a cherry-picked score and beats the true size;
# at 12 it keeps 58%, and the true size wins.
FULL_EVIDENCE_LINES = 12
# A direction whose LARGEST step is under this many channel
# levels is flat: no edge anywhere, so it cannot vote for or
# against a grid (a vertically striped image is flat
# top-to-bottom, and its horizontal grid is still real). The
# max, not the mean: a heavily blurred grid has a tiny MEAN
# second derivative (its spikes are rare) but real spikes, and
# the mean would wrongly call it flat. A non-flat direction
# always votes, even when its ratio is 0 — steps that avoid the
# grid lines are evidence against that grid.
FLAT_MAX_STEP = 1.0
# The smallest-size rule: multiples of the true grid score
# nearly as well as the grid itself (their lines are a subset
# of the true lines), so among the sizes scoring within this
# fraction of the best, the smallest wins — the smallest grid
# explaining the edges is the true one. 0.70: the true size
# measures ~74% of its best multiple (phase cherry-picking
# inflates the multiple's fewer lines), while a half-size
# scores ~50% (half its lines fall inside cells), so 0.70
# includes the true size and excludes its half.
NEAR_TIE_FRACTION = 0.70
# How many distinct coarse sizes each signal refines before
# picking its winner (see _signal_winner). 8 covers the true
# size plus its multiples and the usual spurious peaks.
REFINE_CANDIDATES = 8
# A refinement must beat its input winner by this factor to
# count — below it, the "gain" is the flat score landscape
# wandering the phase by ~1 px (see _refine).
REFINE_MARGIN = 1.05
# The strong-grid bar for the clean override (see
# detect_grid): a nearest upscale of a clean sprite scores
# 100+ on H, while the strongest spurious grid on a 1x sprite
# measures ~42 — 50 sits between them, and the hit-rate check
# below is the real guard.
STRONG_GRID_SCORE = 50.0
# The hit-rate bar for the clean override: at a real grid,
# at least half the grid lines carry a real edge (above
# HIT_LINE_MIN) — the grid repeats at most lines. A spurious
# grid rides on a few strong lines (a flat sprite's spurious
# 2.75 grid: 2 of 11 lines). The level is fixed, not relative
# to the mean: line values on a real grid are skewed (a few
# busy boundaries, many quiet ones), so half the mean would
# fail real grids.
MIN_HIT_RATE = 0.5
# The H peak that marks a sharp grid: when the best H ratio at
# the winning size clears this, the grid lines sit on H's peak
# (sharp grid); otherwise the grid is blurred and the lines sit
# half a cell past D2's peak (the cell centres). 2.0, not 1.5:
# a blurred grid's H ratio can reach ~1.5 at a lucky phase now
# that the ratio floor is 0.25, while a sharp grid measures
# 30 and up.
SHARP_H_RATIO = 2.0
# The hit-rate line level: a grid line "carries" the grid when
# its H value is above this many channel levels — a real edge
# somewhere along the line. (See _grid_hit_rate.)
HIT_LINE_MIN = 5.0


def _scan_lines(image, along_rows):
    """One direction's (H, D2) profiles. H[k] is the mean RGB
    step across the boundary before index k, over a sample of
    lines; D2[k] is the mean change in that step from boundary
    k to boundary k+1 — the second derivative, which spikes
    where a blurred grid's ramps bend (the cell centres).
    Only pairs where both pixels are opaque count — transparent
    regions carry no grid information. along_rows=True scans
    across columns (H over x); False scans down rows (H over y).
    D2 has one fewer entry than H: index k needs the steps at
    k and k+1, so the last boundary has no second derivative."""
    width, height = image.width(), image.height()
    data = image.data
    length = width if along_rows else height
    other = height if along_rows else width
    if other <= DETECT_MAX_SAMPLES:
        lines = range(other)
    else:
        step = other / DETECT_MAX_SAMPLES
        lines = [int(i * step) for i in range(DETECT_MAX_SAMPLES)]
    h_sum = [0.0] * length
    h_cnt = [0] * length
    d2_sum = [0.0] * max(0, length - 1)
    d2_cnt = [0] * max(0, length - 1)
    for line in lines:
        prev = None
        prev_step = None
        for k in range(length):
            if along_rows:
                off = (line * width + k) * 4
            else:
                off = (k * width + line) * 4
            if data[off + 3] == 0:
                prev = None
                prev_step = None
                continue
            rgb = (data[off], data[off + 1], data[off + 2])
            if prev is not None:
                step = (abs(rgb[0] - prev[0])
                        + abs(rgb[1] - prev[1])
                        + abs(rgb[2] - prev[2])) / 3.0
                h_sum[k] += step
                h_cnt[k] += 1
                if prev_step is not None:
                    d2_sum[k - 1] += abs(step - prev_step)
                    d2_cnt[k - 1] += 1
                prev_step = step
            prev = rgb
    h = [h_sum[k] / h_cnt[k] if h_cnt[k] else 0.0
         for k in range(length)]
    d2 = [d2_sum[k] / d2_cnt[k] if d2_cnt[k] else 0.0
          for k in range(max(0, length - 1))]
    return h, d2


def _edge_profiles(image):
    """((h_h, d2_h), (h_v, d2_v)): the horizontal and vertical
    (first-derivative, second-derivative) profile pairs."""
    return (_scan_lines(image, True), _scan_lines(image, False))


def _grid_lines(length, size, phase):
    """Boundary indices for a grid of the given size and phase
    along one axis of the given length: round(j * size + phase)
    for j = 0, 1, ..., keeping only real boundaries (1..length-1).
    Position 0 is the image edge, not a boundary."""
    lines = []
    j = 0
    while True:
        pos = int(round(j * size + phase))
        if pos >= length:
            break
        if pos >= 1:
            lines.append(pos)
        j += 1
    return lines


def _direction_score(profile, size, phase, total, count):
    """How grid-like one direction reads at this size + phase:
    the mean step ON the grid lines divided by the mean step
    inside the cells (+RATIO_FLOOR, so a perfectly flat
    interior reads as a strong grid instead of dividing by
    zero), weighted by evidence — a size with only two lines
    in view scores at most a sixth of its raw ratio. 0.0
    when there are too few lines, or too few interior steps,
    to judge. The floor is small (0.25): a heavily blurred
    grid's D2 spikes are only a few levels high, and a floor
    of 1.0 would bury them under the interior noise."""
    lines = _grid_lines(len(profile), size, phase)
    if len(lines) < MIN_GRID_LINES:
        return 0.0
    on_lines = sum(profile[k] for k in lines)
    inside = count - len(lines)
    if inside < MIN_GRID_LINES:
        return 0.0
    on_mean = on_lines / len(lines)
    in_mean = (total - on_lines) / inside
    ratio = on_mean / (in_mean + RATIO_FLOOR)
    evidence = min(1.0, len(lines) / FULL_EVIDENCE_LINES)
    return ratio * evidence


def _phase_steps(size):
    """Coarse phases for a cell size: 0, step, 2*step, ... < size."""
    step = max(PHASE_STEP_MIN, size * PHASE_STEP_FRACTION)
    phases = []
    phase = 0.0
    while phase < size:
        phases.append(phase)
        phase += step
    return phases


def _best_phase(profile, total, count, size, phases):
    """The best (score, phase) for one direction at one size,
    over the given phases — (0.0, 0.0) when no phase scores.
    The two directions separate: the combined score is the mean
    of the two directions' ratios, so each direction's best
    phase is found on its own instead of searching every
    (ox, oy) pair — 34 scores per size, not 289."""
    best_score = 0.0
    best_phase = 0.0
    for phase in phases:
        score = _direction_score(profile, size, phase,
                                 total, count)
        if score > best_score:
            best_score = score
            best_phase = phase
    return best_score, best_phase


def _score_size(profile_h, total_h, count_h, flat_h,
                profile_v, total_v, count_v, flat_v,
                size, h_phases, v_phases):
    """The best (score, ox, oy) for one cell size, or None when
    no phase scores. The combined score is the mean of the
    directions that vote: a flat direction abstains (it has no
    edges to judge by), a non-flat one always votes, even at
    0.0 — steps that avoid the grid lines are evidence against
    that size."""
    h_score, ox = _best_phase(profile_h, total_h, count_h,
                              size, h_phases)
    v_score, oy = _best_phase(profile_v, total_v, count_v,
                              size, v_phases)
    votes = []
    if not flat_h:
        votes.append(h_score)
    if not flat_v:
        votes.append(v_score)
    if not votes:
        return None
    return (sum(votes) / len(votes), ox, oy)


def _score_one_size(profile_h, profile_v, size):
    """The best (score, ox, oy) at one size, or None when no
    phase scores: a coarse phase search, then a fine phase
    scan around the coarse winner (±1 px in 0.25 steps). The
    fine scan matters because the coarse phase step is up to
    1 px at big sizes and a D2 spike is 1 px wide — a coarse
    phase half a pixel off misses half the spikes, and a
    cell-16 blurred grid scores below the threshold on phase
    misses alone. Used by the curve search and, for off-grid
    sizes, by the divisor candidates in _signal_winner."""
    total_h = sum(profile_h[1:])
    count_h = len(profile_h) - 1
    total_v = sum(profile_v[1:])
    count_v = len(profile_v) - 1
    if count_h < 1 or count_v < 1:
        return None
    flat_h = (max(profile_h[1:]) < FLAT_MAX_STEP
              if count_h else True)
    flat_v = (max(profile_v[1:]) < FLAT_MAX_STEP
              if count_v else True)
    phases = _phase_steps(size)
    found = _score_size(profile_h, total_h, count_h, flat_h,
                        profile_v, total_v, count_v, flat_v,
                        size, phases, phases)
    if found is None or found[0] <= 0.0:
        return None
    ox_phases = [p % size for p in
                 _frange(found[1] - 1.0, found[1] + 1.0, 0.25)]
    oy_phases = [p % size for p in
                 _frange(found[2] - 1.0, found[2] + 1.0, 0.25)]
    fine = _score_size(profile_h, total_h, count_h, flat_h,
                       profile_v, total_v, count_v, flat_v,
                       size, ox_phases, oy_phases)
    if fine is not None and fine[0] > found[0]:
        found = fine
    return found


def _score_curve(profile_h, profile_v):
    """{size: (score, ox, oy)} — the best score per candidate
    size for one profile pair (H or D2), via _score_one_size."""
    curve = {}
    size = MIN_CELL
    while size <= MAX_CELL + 1e-9:
        found = _score_one_size(profile_h, profile_v, size)
        if found is not None:
            curve[size] = found
        size = round(size + CELL_STEP, 2)
    return curve


def _signal_winner(profile_h, profile_v):
    """One signal's winner as (score, size, ox, oy), or None
    when the signal scores nothing. The coarse curve is
    searched first, then the top REFINE_CANDIDATES distinct
    sizes (at least 1.0 apart) are each refined before the
    winner is picked — a fractional true size (10.67) never
    lands on the coarse 0.25 grid, and the drift over dozens
    of cells knocks its coarse score below a multiple's, so
    picking from coarse scores alone would pick the multiple.
    Each candidate's halves, thirds and quarters are refined
    too, for the same reason: the true size is often a divisor
    of a multiple that scored well. Among the refined
    candidates, the smallest within NEAR_TIE_FRACTION of the
    best wins: multiples of the true grid score nearly as well
    as the grid itself (their lines are a subset of the true
    lines), so the smallest grid explaining the edges is the
    true one."""
    curve = _score_curve(profile_h, profile_v)
    if not curve:
        return None
    ranked = sorted(curve.items(), key=lambda item: -item[1][0])
    picks = []
    for size, _entry in ranked:
        if all(abs(size - picked) >= 1.0 for picked in picks):
            picks.append(size)
        if len(picks) >= REFINE_CANDIDATES:
            break
    refined = {}

    def _add(found):
        if (found[1] not in refined
                or found[0] > refined[found[1]][0]):
            refined[found[1]] = found

    for size in picks:
        entry = curve[size]
        found = _refine(profile_h, profile_v,
                        (entry[0], size, entry[1], entry[2]))
        _add(found)
        # The true size is often a divisor of a multiple that
        # scored well: a fractional grid (10.67) scores only
        # 1.76 at its nearest coarse size (10.75 — drift over
        # 48 cells) and never makes the top picks, while its
        # multiples (32.0, 42.75, 21.25) do. So each refined
        # candidate's halves, thirds and quarters are scored
        # (at the exact divisor size, where there is no drift)
        # and refined too. The divisor is scored from scratch,
        # not from the parent's phase: the parent's D2 phase
        # marks the parent's cell centres, which are the
        # divisor's BOUNDARIES, half a divisor cell away from
        # the divisor's centres. Spurious divisors just score
        # low and never win.
        for divisor in (2, 3, 4):
            small = found[1] / divisor
            if small < MIN_CELL:
                continue
            entry = _score_one_size(profile_h, profile_v, small)
            if entry is not None:
                _add(_refine(profile_h, profile_v,
                             (entry[0], small,
                              entry[1], entry[2])))
    entries = list(refined.values())
    top = max(entry[0] for entry in entries)
    floor = top * NEAR_TIE_FRACTION
    band = [entry for entry in entries if entry[0] >= floor]
    return min(band, key=lambda entry: entry[1])


def _refine(profile_h, profile_v, winner):
    """Refine a (score, size, ox, oy) winner: size in 0.05
    steps within ±CELL_STEP, phase in 0.25 steps around the
    coarse phase, on the given profile pair. The refined
    result is returned only when it beats the input winner by
    REFINE_MARGIN — the score landscape is flat near the
    optimum (a D2 spike is 2 px wide), so a marginal "gain"
    is noise wandering the phase by ~1 px, which would shift
    the grid origin by the same; a real refinement (a
    fractional size, a divisor candidate starting at 0.0)
    gains far more than the margin. Phases wrap with % size:
    a phase at the far edge of a slightly smaller cell is the
    same grid line as a phase near 0."""
    total_h = sum(profile_h[1:])
    count_h = len(profile_h) - 1
    total_v = sum(profile_v[1:])
    count_v = len(profile_v) - 1
    flat_h = (max(profile_h[1:]) < FLAT_MAX_STEP
              if count_h else True)
    flat_v = (max(profile_v[1:]) < FLAT_MAX_STEP
              if count_v else True)
    best = winner
    _score0, size0, ox0, oy0 = winner
    size = max(MIN_CELL, round(size0 - CELL_STEP, 2))
    end = min(MAX_CELL, round(size0 + CELL_STEP, 2))
    while size <= end + 1e-9:
        ox_phases = [p % size for p in
                     _frange(ox0 - PHASE_STEP_MIN,
                             ox0 + PHASE_STEP_MIN, 0.25)]
        oy_phases = [p % size for p in
                     _frange(oy0 - PHASE_STEP_MIN,
                             oy0 + PHASE_STEP_MIN, 0.25)]
        found = _score_size(profile_h, total_h, count_h, flat_h,
                            profile_v, total_v, count_v, flat_v,
                            size, ox_phases, oy_phases)
        if found is not None and found[0] > best[0]:
            best = (found[0], size, found[1], found[2])
        size = round(size + 0.05, 2)
    if best[0] > winner[0] * REFINE_MARGIN:
        return best
    return winner


def _best_grid(h_h, d2_h, h_v, d2_v):
    """The best-scoring (score, size, ox, oy) over all candidate
    sizes, or None when no candidate scores at all. Each signal
    picks its own winner (smallest in its band), then H wins
    when it finds a grid at or above the threshold — H reads
    real edges, so when it is confident the grid is real, and
    it is the only signal at cell 2 (D2's ratio is ~1 by
    construction there). D2 wins only when H finds nothing:
    D2 reads blurred grids (H is ~1 across a bilinear ramp),
    but it also reads SUBSTRUCTURE — a soft edge's two blend
    edges score as a cell-2 grid — so it never overrules H.
    The winner is refined on its own signal (size to 0.05,
    phase to 0.25) before it is picked — see _signal_winner.
    The phases returned are D2's when D2 won (the cell centres
    for a blurred grid) and H's when H won; detect_grid fixes
    the origin from H either way, and only uses the returned
    phases for a blurred grid. D2 is computed lazily: when H
    finds a grid at or above the threshold, the D2 search is
    skipped outright (it can never overrule H), which nearly
    halves detection time on sharp images."""
    h_winner = _signal_winner(h_h, h_v)
    if h_winner is not None and h_winner[0] >= GRID_SCORE_THRESHOLD:
        return h_winner
    d2_winner = _signal_winner(d2_h, d2_v)
    if d2_winner is not None:
        return d2_winner
    return h_winner


def _grid_hit_rate(profile_h, profile_v, size, ox, oy):
    """How consistently a grid repeats: the fraction of grid
    lines carrying a real edge (H above HIT_LINE_MIN), averaged
    over the directions that are not flat. A real grid repeats
    at most lines; a spurious grid rides on a few strong ones
    (a flat sprite's spurious 2.75 grid: 2 of 11 lines).
    0.0 when no direction votes."""
    rates = []
    for profile, phase in ((profile_h, ox), (profile_v, oy)):
        if len(profile) < 2 or max(profile[1:]) < FLAT_MAX_STEP:
            continue
        lines = _grid_lines(len(profile), size, phase)
        if len(lines) < MIN_GRID_LINES:
            continue
        hits = sum(1 for k in lines
                   if profile[k] > HIT_LINE_MIN)
        rates.append(hits / len(lines))
    if not rates:
        return 0.0
    return sum(rates) / len(rates)


def _origin_from_h(profile_h, profile_v, size, d2_ox, d2_oy):
    """The grid origin (ox, oy) at the winning size: when H
    peaks somewhere, the grid is sharp and the lines sit on
    H's peak (searched over every phase in 0.25 steps, per
    direction); when H is flat, the grid is blurred and the
    lines sit half a cell past D2's peak — the cell centres —
    plus half a pixel, because D2's spike index sits half a
    pixel before the kink it marks (the kink between steps j
    and j+1 is at continuous position j + 0.5)."""
    total_h = sum(profile_h[1:])
    count_h = len(profile_h) - 1
    total_v = sum(profile_v[1:])
    count_v = len(profile_v) - 1
    phases = [round(p, 2)
              for p in _frange(0.0, size, 0.25)]
    h_score, ox = _best_phase(profile_h, total_h, count_h,
                              size, phases)
    v_score, oy = _best_phase(profile_v, total_v, count_v,
                              size, phases)
    if h_score < SHARP_H_RATIO:
        ox = (d2_ox + size / 2.0 + 0.5) % size
    if v_score < SHARP_H_RATIO:
        oy = (d2_oy + size / 2.0 + 0.5) % size
    return ox, oy


def _frange(start, end, step):
    """start, start+step, ..., up to end inclusive (float-safe)."""
    value = start
    while value <= end + 1e-9:
        yield round(value, 2)
        value += step


def _distinct_opaque_colors(image, limit):
    """How many distinct opaque RGB colors the image uses, up
    to limit — stops counting past the limit, so a photo bails
    out within its first rows instead of scanning a megapixel."""
    colors = set()
    data = image.data
    for i in range(0, len(data), 4):
        if data[i + 3]:
            colors.add((data[i], data[i + 1], data[i + 2]))
            if len(colors) > limit:
                return len(colors)
    return len(colors)


def is_pixel_clean(image, profiles=None):
    """Whether the image already reads as a clean 1x sprite:
    few distinct opaque colors AND hard edges between
    neighbours — the sharpness is the mean over NONZERO steps,
    so a mostly-flat sprite (low mean step, hard edges where
    it changes) still passes, while a blur (small steps
    everywhere) fails. A photo fails on colors (thousands of
    them); random noise fails on colors too (hard steps but a
    different color at nearly every pixel). profiles is the
    ((h_h, d2_h), (h_v, d2_v)) pair from _edge_profiles, when
    the caller already computed it. Note a nearest upscale of
    a clean sprite passes this test too (same colors, same
    edge hardness) — detect_grid runs the grid search anyway
    and lets a strong, consistent grid overrule it."""
    if image.isNull():
        return False
    if profiles is None:
        profiles = _edge_profiles(image)
    (horizontal, _d2_h), (vertical, _d2_v) = profiles
    steps = [step for step in horizontal[1:] + vertical[1:]
             if step > 0.0]
    if not steps:
        return False
    sharpness = sum(steps) / len(steps)
    if sharpness < MIN_CLEAN_SHARPNESS:
        return False
    return (_distinct_opaque_colors(image, MAX_CLEAN_COLORS)
            <= MAX_CLEAN_COLORS)


def detect_grid(image):
    """The image's native pixel grid as (cell, ox, oy) — cell
    size in pixels (fractional when the grid is), ox/oy the
    grid origin — or None when no grid scores above the
    threshold. An already-clean 1x sprite reports
    (1.0, 0.0, 0.0). A 0-size image raises PixelForgeError.
    Never guesses: below the threshold, the answer is None.
    The clean test runs FIRST: a 1x sprite's hard edges score
    highly at small spurious sizes (a flat sprite measures
    ~42 at cell 2.75), so searching first would snap it to a
    grid it does not have. A nearest upscale passes the clean
    test too, so a strong grid (score at or above
    STRONG_GRID_SCORE) that repeats at most lines (hit-rate
    at or above MIN_HIT_RATE) overrules the clean result —
    a nearest x2 measures 126 with a high hit-rate, while the
    spurious grids on 1x sprites are either weak or ride on
    a few lines."""
    if image.isNull():
        raise PixelForgeError(
            "cannot find a pixel grid in an image with no size")
    width, height = image.width(), image.height()
    profiles = _edge_profiles(image)
    (h_h, d2_h), (h_v, d2_v) = profiles
    clean = is_pixel_clean(image, profiles=profiles)
    if width >= 4 and height >= 4:
        best = _best_grid(h_h, d2_h, h_v, d2_v)
        if best is not None and best[0] >= GRID_SCORE_THRESHOLD:
            _score, size, px, py = best
            ox, oy = _origin_from_h(h_h, h_v, size, px, py)
            if not clean or (
                    best[0] >= STRONG_GRID_SCORE and
                    _grid_hit_rate(h_h, h_v, size, ox, oy)
                    >= MIN_HIT_RATE):
                return (size, ox, oy)
    if clean:
        return (1.0, 0.0, 0.0)
    return None


def _median(values):
    """The upper median of a non-empty list: the middle value
    once sorted, so the result is always a value that was
    actually in the cell (no invented colors)."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def snap_to_grid(image, cell, ox=0.0, oy=0.0, min_cover=0.5):
    """Snap an image to the given grid: one output pixel per
    cell, the per-channel median of the cell's opaque pixels.
    A cell under min_cover opaque reads as transparent (which
    trims blurred fringes), and the result is cropped to the
    solid bounding box. The input is left alone. Raises
    PixelForgeError for a 0-size image or a cell under 1.
    The median is taken over the cell's CORE — the middle half
    of the cell in each axis (widened for small cells so the
    core always holds at least 2x2 pixels) — not the whole
    cell: across a blurred cell the pixels form a ramp from
    one neighbour's color to the other, and the median of the
    whole ramp is the ramp's midpoint, not the cell's own
    color; the core's median is the value at the cell centre,
    which is the cell's color. On a flat cell the core median
    and the whole-cell median agree. Coverage is still judged
    on the whole cell (opaque pixels over the nominal cell
    area), so a cell whose core is a hole but whose edges are
    solid still counts as covered — it just takes its color
    from the core pixels it has, and reads as transparent when
    its core holds no opaque pixel at all."""
    if image.isNull():
        raise PixelForgeError(
            "cannot snap an image with no size to a pixel grid")
    if cell < 1:
        raise PixelForgeError(
            "cell size must be at least 1 pixel (got %r)" % cell)
    width, height = image.width(), image.height()
    data = image.data
    gx0 = math.floor((0 - ox) / cell)
    gy0 = math.floor((0 - oy) / cell)
    gw = math.floor((width - 1 - ox) / cell) - gx0 + 1
    gh = math.floor((height - 1 - oy) / cell) - gy0 + 1
    col_cell = [math.floor((x - ox) / cell) - gx0
                for x in range(width)]
    row_cell = [math.floor((y - oy) / cell) - gy0
                for y in range(height)]
    # The core fraction per axis: the middle half, widened so
    # a small cell's core still holds at least 2x2 pixels.
    core = max(0.5, 2.0 / cell)
    lo = (1.0 - core) / 2.0
    hi = 1.0 - lo
    col_core = [lo <= ((x - ox) / cell) % 1.0 < hi
                for x in range(width)]
    row_core = [lo <= ((y - oy) / cell) % 1.0 < hi
                for y in range(height)]
    # cells[gy][gx] = [reds, greens, blues, alphas, opaque
    # count]; the lists hold the CORE pixels only (the color),
    # the count holds every opaque pixel in the cell (the
    # coverage). The coverage denominator is the NOMINAL cell
    # area (cell x cell), not the pixels the cell happens to
    # hold — a sliver cell at the image edge holds few pixels,
    # but they are all opaque, so judging it against its own
    # count would keep a one-pixel strip as a whole row of the
    # sprite.
    cell_area = cell * cell
    cells = [[None] * gw for _ in range(gh)]
    for y in range(height):
        gy = row_cell[y]
        base = y * width * 4
        row = cells[gy]
        in_core_y = row_core[y]
        for x in range(width):
            gx = col_cell[x]
            off = base + x * 4
            alpha = data[off + 3]
            if not alpha:
                continue
            bucket = row[gx]
            if bucket is None:
                bucket = row[gx] = [[], [], [], [], 0]
            bucket[4] += 1
            if in_core_y and col_core[x]:
                bucket[0].append(data[off])
                bucket[1].append(data[off + 1])
                bucket[2].append(data[off + 2])
                bucket[3].append(alpha)
    out = Image(gw, gh)
    out_data = out.data
    top = left = None
    bottom = right = -1
    for gy in range(gh):
        for gx in range(gw):
            bucket = cells[gy][gx]
            if bucket is None:
                continue
            reds, greens, blues, alphas, opaque = bucket
            if not reds or opaque / cell_area < min_cover:
                continue
            off = (gy * gw + gx) * 4
            out_data[off] = _median(reds)
            out_data[off + 1] = _median(greens)
            out_data[off + 2] = _median(blues)
            out_data[off + 3] = _median(alphas)
            if top is None:
                top = gy
            bottom = gy
            left = gx if left is None else min(left, gx)
            right = max(right, gx)
    if top is None:
        # Nothing solid survived — a 1x1 transparent image,
        # not a crash, and not a guess at a size.
        return Image(1, 1)
    if (top == 0 and left == 0 and bottom == gh - 1
            and right == gw - 1):
        return out
    return out.copy(left, top, right - left + 1, bottom - top + 1)


def snap_and_quantise(image, grid, palette="image",
                      max_colors=16, palettes=None):
    """Snap an image to a KNOWN grid, then quantise it: the
    second half of clean_image, split out so the GUI worker
    can detect first (a clean sprite and a gridless image need
    different messages, and neither gets quantised) and only
    snap when there is a grid to snap to. Quantising runs with
    dithering OFF: dithering would put back the noise the snap
    just removed."""
    cell, ox, oy = grid
    snapped = snap_to_grid(image, cell, ox, oy)
    working = resolve_palette(snapped, palette, max_colors,
                              palettes=palettes)
    return quantize(snapped, working, dither=False)


def clean_image(image, palette="image", max_colors=16,
                palettes=None):
    """detect → snap → quantise, sprite-forge's clean pipeline.
    Returns (sprite_1x, (cell, ox, oy)). Raises PixelForgeError
    when no grid is found. An already-clean 1x sprite skips the
    median step (each pixel is already its own cell) and is
    only quantised. Quantising runs with dithering OFF:
    dithering would put back the noise the snap just removed."""
    grid = detect_grid(image)
    if grid is None:
        raise PixelForgeError(
            "could not find a pixel grid in this image, it may "
            "not be pixel art, or the grid may be too faint to "
            "detect")
    cell, ox, oy = grid
    if cell <= 1.0:
        working = resolve_palette(image, palette, max_colors,
                                  palettes=palettes)
        return quantize(image, working, dither=False), grid
    return snap_and_quantise(image, grid, palette, max_colors,
                             palettes), grid
