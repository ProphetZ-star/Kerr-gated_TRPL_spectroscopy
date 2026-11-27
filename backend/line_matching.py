'''Pair the peaks found on a calibration frame with a lamp's reference wavelengths.

The reference list is only standard wavelengths, and usually spans far more than the CCD sees; the spectrograph's
centre wavelength is roughly right, while the dispersion and its direction are not assumed to be known (the grating
setting may be wrong, and the spectrum may run either way across the chip).

Method: every pair of two strong peaks with two reference lines gives a linear pixel -> wavelength guess. Guesses
whose chip centre is far from the set centre wavelength, or whose dispersion is implausible, are dropped. Each of the
rest is scored by how many peaks fall on a reference line. The best guesses are refined with a polynomial and a
shrinking tolerance, and the one that explains the most peaks wins. Pairs typed by hand are kept, and a guess must
agree with them.
'''
from __future__ import annotations

import math
import numpy as np

ORIGIN = 511.5                 # pixel at the chip centre
CENTRE_SLACK_NM = 25.0         # how far the chip centre may be from the set centre wavelength
DISPERSION_NM_PER_PX = (0.03, 1.5)
ANCHOR_PEAKS = 10              # strongest peaks used to form guesses
REFINE_CANDIDATES = 24
MIN_PAIRS = 3


def _nearest(lines: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    '''Index of the nearest line to each value, and the distance.'''
    index = np.clip(np.searchsorted(lines, values), 1, len(lines) - 1)
    left, right = lines[index - 1], lines[index]
    index = np.where(np.abs(values - left) <= np.abs(right - values), index - 1, index)
    return index, np.abs(lines[index] - values)


def _model(coefficients: np.ndarray, pixels) -> np.ndarray:
    return np.polyval(coefficients, (np.asarray(pixels, float) - ORIGIN) / ORIGIN)


def _fit(pixels, wavelengths, degree):
    x = (np.asarray(pixels, float) - ORIGIN) / ORIGIN
    return np.polyfit(x, np.asarray(wavelengths, float), degree)


def _degree(count: int) -> int:
    return 1 if count < 5 else 2 if count < 9 else 3


def _assign(coefficients, peaks, weights, lines, tolerance):
    '''One-to-one pairs of peaks and lines within the tolerance (closest first).'''
    index, distance = _nearest(lines, _model(coefficients, peaks))
    order = np.argsort(distance)
    used, pairs = set(), {}
    for k in order:
        if distance[k] > tolerance:
            break
        if int(index[k]) in used:
            continue
        used.add(int(index[k]))
        pairs[int(k)] = int(index[k])
    return pairs


def match_lines(peaks, wavelengths, centre_nm, strengths=None, fixed=(), width: int = 1024) -> dict:
    '''Pair peak pixels with reference wavelengths.

    peaks: pixels of the found peaks (not counting the pixels of `fixed`); strengths: their prominences (optional).
    wavelengths: reference lines still free to pair. fixed: (pixel, wavelength) pairs typed or picked by hand.
    Returns dict(pairs=[(pixel, wavelength)], coefficients, dispersion_nm_per_px, chip_nm=(at pixel 0, at last pixel),
    rms_nm, ambiguous, reason). pairs is empty when no convincing pairing exists (reason says why).'''
    peaks = np.asarray(list(peaks), float)
    lines = np.unique(np.asarray([w for w in wavelengths if w is not None and math.isfinite(w)], float))
    fixed = [(float(p), float(w)) for p, w in fixed]
    empty = dict(pairs=[], coefficients=None, dispersion_nm_per_px=None, chip_nm=None, rms_nm=None, ambiguous=False)
    if len(peaks) + len(fixed) < MIN_PAIRS or len(lines) + len(fixed) < MIN_PAIRS or len(lines) < 1 or len(peaks) < 1:
        return empty | dict(reason='too few peaks or reference lines')
    strengths = np.ones(len(peaks)) if strengths is None or len(strengths) != len(peaks) else np.asarray(strengths, float)
    strengths = np.where(np.isfinite(strengths) & (strengths > 0), strengths, np.nanmedian(strengths[strengths > 0]) if np.any(strengths > 0) else 1.0)
    # A strong line counts more, but a few weak extra peaks still matter; weights 1 .. ~2.
    weights = 1.0 + np.log10(1.0 + strengths / np.median(strengths)) / 2.0
    fixed_px = np.array([p for p, _ in fixed]); fixed_nm = np.array([w for _, w in fixed])
    low, high = DISPERSION_NM_PER_PX

    # 1. Linear guesses from two anchor peaks and two lines (or from the hand-typed pairs).
    if len(fixed) >= 2:
        c = np.polyfit(fixed_px - ORIGIN, fixed_nm, 1)
        guesses = np.array([[c[0], c[1]]])
    else:
        anchors = np.argsort(-strengths)[:ANCHOR_PEAKS]
        i, j = np.triu_indices(len(anchors), 1)
        pi, span = peaks[anchors[i]], peaks[anchors[j]] - peaks[anchors[i]]
        keep = np.abs(span) >= 3
        pi, span = pi[keep], span[keep]
        # Peak i on line k, peak j on line l: b[n, k, l] in nm per pixel, c = wavelength at the chip centre.
        b = (lines[None, :] - lines[:, None])[None, :, :] / span[:, None, None]
        c = lines[None, :, None] - b * (pi[:, None, None] - ORIGIN)
        b, c = b.ravel(), c.ravel()
        if len(fixed) == 1:  # also: the typed pair plus any peak on any line
            span1 = peaks - fixed_px[0]; span1 = span1[np.abs(span1) >= 3]
            b1 = (lines[None, :] - fixed_nm[0]) / span1[:, None]
            b = np.concatenate([b, b1.ravel()]); c = np.concatenate([c, (fixed_nm[0] - b1 * (fixed_px[0] - ORIGIN)).ravel()])
        ok = (np.abs(b) >= low) & (np.abs(b) <= high) & (np.abs(c - centre_nm) <= CENTRE_SLACK_NM)
        guesses = np.unique(np.round(np.stack([b[ok], c[ok]], axis=1), 6), axis=0)
    if not len(guesses):
        return empty | dict(reason='no pairing fits the centre wavelength')

    # 2. Score the guesses: weighted peaks within 3 px of a line.
    predicted = guesses[:, 1:2] + guesses[:, 0:1] * (peaks[None, :] - ORIGIN)
    _, distance = _nearest(lines, predicted.ravel())
    tolerance = 3.0 * np.abs(guesses[:, 0:1])
    near = distance.reshape(predicted.shape) / tolerance
    score = np.sum(np.where(near < 1, weights[None, :] * (1 - near ** 2), 0.0), axis=1)
    if len(fixed):
        fixed_miss = np.abs(guesses[:, 1:2] + guesses[:, 0:1] * (fixed_px[None, :] - ORIGIN) - fixed_nm[None, :]) / tolerance
        score += np.sum(np.where(fixed_miss < 1, 3.0 * (1 - fixed_miss ** 2), -5.0), axis=1)
    order = np.argsort(-score)[:REFINE_CANDIDATES * 8]

    # 3. Refine the best distinct guesses with a polynomial and a shrinking tolerance (in pixels).
    results, seen = [], []
    for g in order:
        b, c0 = guesses[g]
        if any(abs(b - b2) < 0.02 * abs(b) and abs(c0 - c2) < 2 * abs(b) for b2, c2 in seen):
            continue
        seen.append((b, c0))
        coefficients = np.array([b * ORIGIN, c0])
        pairs = {}
        # Each tolerance is repeated until the pairs stop changing: a curved mapping is picked up from the centre outwards.
        for pixels_tolerance in (3.0, 2.0, 1.5, 1.0):
            for _ in range(6):
                found = _assign(coefficients, peaks, weights, lines, pixels_tolerance * abs(b))
                px = [peaks[k] for k in found] + list(fixed_px); nm = [lines[l] for l in found.values()] + list(fixed_nm)
                if len(px) < MIN_PAIRS:
                    break
                coefficients = _fit(px, nm, min(_degree(len(px)), len(px) - 2) if len(px) > 2 else 1)
                b = float(np.polyval(np.polyder(coefficients), 0.0)) / ORIGIN
                if found == pairs:
                    break
                pairs = found
            if len(pairs) + len(fixed) < MIN_PAIRS:
                break
        pairs = _assign(coefficients, peaks, weights, lines, 1.0 * abs(b))
        if len(pairs) + len(fixed) < MIN_PAIRS:
            continue
        px = np.array([peaks[k] for k in pairs] + list(fixed_px)); nm = np.array([lines[l] for l in pairs.values()] + list(fixed_nm))
        residual = nm - _model(coefficients, px)
        rms = float(np.sqrt(np.mean(residual ** 2)))
        ends = _model(coefficients, [0, width - 1])
        derivative = np.polyval(np.polyder(coefficients), np.linspace(-1, 1, 64)) / ORIGIN
        if abs(np.mean(ends) - centre_nm) > CENTRE_SLACK_NM * 1.5 or not (np.all(derivative > 0) or np.all(derivative < 0)):
            continue
        if fixed and np.any(np.abs(fixed_nm - _model(coefficients, fixed_px)) > 1.5 * abs(b)):
            continue
        # Explain more peaks, closely; less what chance gives: a peak lands within 1 px of some line with probability
        # 2 x (lines per pixel), which grows with the dispersion (a coarse grating packs more lines onto the chip).
        miss_px = np.abs(residual[:len(pairs)]) / abs(b)
        on_chip = np.sum((lines >= ends.min()) & (lines <= ends.max())) + len(fixed)
        chance = min(1.0, 2.0 * on_chip / width)
        total = float(sum(weights[k] * (1 - 0.5 * d ** 2) for k, d in zip(pairs, miss_px)) - chance * weights.sum())
        results.append(dict(score=total, pairs=pairs, coefficients=coefficients, b=b, rms=rms, ends=ends))
        if len(results) >= REFINE_CANDIDATES:
            break
    if not results:
        return empty | dict(reason='no pairing explains enough peaks')
    results.sort(key=lambda r: -r['score'])
    best = results[0]
    # Ambiguous: a genuinely different pairing (sharing under half of the best one's pairs) explains nearly as much.
    chosen = set(best['pairs'].items())
    rival = next((r for r in results[1:] if len(chosen & set(r['pairs'].items())) < 0.5 * len(chosen)), None)
    ambiguous = bool(rival is not None and rival['score'] >= 0.8 * best['score'])
    if len(best['pairs']) + len(fixed) < MIN_PAIRS + 1 and len(peaks) > MIN_PAIRS + 1:
        ambiguous = True
    pairs = sorted((float(peaks[k]), float(lines[l])) for k, l in best['pairs'].items())
    return dict(pairs=pairs, coefficients=[float(v) for v in best['coefficients']], dispersion_nm_per_px=float(best['b']),
                chip_nm=(float(best['ends'][0]), float(best['ends'][1])), rms_nm=best['rms'], ambiguous=ambiguous, reason='')
