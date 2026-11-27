'''Synthetic spectral-calibration backend.

make_frame returns simulated calibration images only. These images do not
represent real Newton DU920P image-mode acquisitions.
'''
from __future__ import annotations

import math
import numpy as np
from .calibration_models import (WIDTH, HEIGHT, CaptureRequest, ExtractRequest,
                                 LinePair, MappingRequest, ApplyRequest)

_PIXEL_ORIGIN = 511.5
_PIXEL_SCALE = 511.5
_MAX_COUNTS = 65535.0
_BIAS = 75.0
_READ_NOISE = 3.0
_SYNTHETIC_PEAK_PIXELS = (115.23, 282.65, 449.38, 655.14, 851.72)
_SYNTHETIC_GAUSSIAN_SIGMA = 2.1
_MAX_CONDITION_NUMBER = 1.0e10


def _coerce_exposure(value: object) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise ValueError('exposure_s must be a finite number from 0.001 to 3600')
    try:result = float(value)
    except (OverflowError,ValueError,TypeError) as exc:raise ValueError('exposure_s must be a finite number from 0.001 to 3600') from exc
    if not math.isfinite(result) or result < 0.001 or result > 3600.0:
        raise ValueError('exposure_s must be a finite number from 0.001 to 3600')
    return result


def _coerce_seed(value: object) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError('seed must be an integer from 0 to 2147483647')
    result = int(value)
    if result < 0 or result > 2147483647:
        raise ValueError('seed must be an integer from 0 to 2147483647')
    return result


def _strict_int_in_range(value: object, name: str, lower: int, upper: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f'{name} must be an integer in [{lower}, {upper}]')
    result = int(value)
    if result < lower or result > upper:
        raise ValueError(f'{name} must be an integer in [{lower}, {upper}]')
    return result


def _validated_frame(frame: object) -> np.ndarray:
    if not isinstance(frame, np.ndarray):
        raise ValueError('frame must be a NumPy array')
    if frame.ndim != 2 or frame.shape not in {(HEIGHT, WIDTH), (1, WIDTH)}:
        raise ValueError(f'frame must have shape ({HEIGHT}, {WIDTH}) or (1, {WIDTH})')
    if not (
        np.issubdtype(frame.dtype, np.integer)
        or np.issubdtype(frame.dtype, np.floating)
    ):
        raise ValueError('frame must contain real integer or floating-point counts')
    values = frame.astype(np.float64, copy=False)
    if not np.all(np.isfinite(values)):
        raise ValueError('frame must not contain NaN or infinity')
    if values.size:
        if float(values.min()) < 0.0 or float(values.max()) > _MAX_COUNTS:
            raise ValueError('frame counts must be within 0 to 65535')
    return values


def _polyval_ascending(coefficients: np.ndarray, x: np.ndarray) -> np.ndarray:
    values = np.zeros_like(x, dtype=np.float64)
    for coefficient in coefficients[::-1]:
        values = values * x + float(coefficient)
    return values


def make_frame(exposure_s: float, seed: int) -> np.ndarray:
    '''Return a reproducible simulated calibration image.'''
    exposure = _coerce_exposure(exposure_s)
    random_seed = _coerce_seed(seed)

    x_coordinates = np.arange(WIDTH, dtype=np.float64)
    peak_signal = np.zeros(WIDTH, dtype=np.float64)
    for index, peak_pixel in enumerate(_SYNTHETIC_PEAK_PIXELS):
        amplitude = 3000.0 if index == 2 else 1900.0 + 180.0 * index
        peak_signal += amplitude * np.exp(
            -0.5 * ((x_coordinates - peak_pixel) / _SYNTHETIC_GAUSSIAN_SIGMA) ** 2
        )

    stripe_centers = 124.0 + 3.0 * np.sin(x_coordinates / 170.0)
    rows = np.arange(HEIGHT, dtype=np.float64)[:, None]
    stripe = np.exp(-0.5 * ((rows - stripe_centers[None, :]) / 25.0) ** 2)
    mean_signal = peak_signal[None, :] * stripe * exposure

    rng = np.random.default_rng(random_seed)
    counts = rng.poisson(mean_signal).astype(np.float64)
    counts += rng.normal(0.0, _READ_NOISE, size=(HEIGHT, WIDTH))
    counts += _BIAS
    np.clip(counts, 0.0, _MAX_COUNTS, out=counts)
    return np.rint(counts).astype(np.int32)


# A calibration lamp's lines differ in strength by orders of magnitude, so a line is judged against the noise and by
# how far it stands out from its surroundings, not as a fraction of the strongest one.
PEAK_SIGMAS = 8.0          # prominence of a line, in noise standard deviations
PEAK_MIN_FRACTION = 0.01   # ... and at least this fraction of the strongest line above the baseline
PEAK_SEPARATION = 6.0      # pixels between two lines


def _profile_statistics(profile: np.ndarray) -> tuple[np.ndarray, float, float]:
    """(profile with one-pixel spikes removed, baseline, noise sigma). A hot pixel or cosmic ray is one pixel wide and
    is not a lamp line; a 3-point median removes it. The baseline is the 20th percentile (lines fill a small part of
    the chip) and the noise comes from pixel-to-pixel differences, which slow features do not affect."""
    values = np.asarray(profile, dtype=np.float64)
    padded = np.concatenate(([values[0]], values, [values[-1]]))
    smooth = np.median(np.stack((padded[:-2], padded[1:-1], padded[2:])), axis=0)
    baseline = float(np.percentile(smooth, 20))
    differences = np.diff(values)
    noise = float(1.4826 * np.median(np.abs(differences - np.median(differences))) / math.sqrt(2.0)) if differences.size else 0.0
    return smooth, baseline, noise


def _find_peaks(profile: np.ndarray) -> list[float]:
    values = np.asarray(profile, dtype=np.float64)
    if values.size < 5 or not np.all(np.isfinite(values)):
        return []
    smooth, baseline, noise = _profile_statistics(values)
    amplitude = float(np.max(smooth)) - baseline
    if not math.isfinite(amplitude) or amplitude <= 0.0:
        return []
    needed = max(PEAK_SIGMAS * noise, PEAK_MIN_FRACTION * amplitude)

    candidates: list[tuple[float, float]] = []
    for index in range(1, values.size - 1):
        y1 = smooth[index]
        # Local maximum of the despiked profile (the last pixel of a flat, e.g. saturated, top).
        if y1 < smooth[index - 1] or y1 <= smooth[index + 1]:
            continue
        # Prominence: height above the higher of the two lowest points between it and the next higher ground.
        left = index
        while left > 0 and smooth[left - 1] <= y1:
            left -= 1
        right = index
        while right < values.size - 1 and smooth[right + 1] <= y1:
            right += 1
        prominence = y1 - max(float(np.min(smooth[left:index + 1])), float(np.min(smooth[index:right + 1])))
        if prominence < needed:
            continue
        # Sub-pixel centre from the measured counts: the despiked maximum can sit a pixel off the measured top, so
        # take the highest measured pixel nearby (a spike there was already removed from the candidates) first.
        lo, hi = max(1, index - 2), min(values.size - 2, index + 2)
        index = lo + int(np.argmax(values[lo:hi + 1]))
        top = float(values[index])
        first = index
        while first > 0 and values[first - 1] == top:
            first -= 1
        last = index
        while last < values.size - 1 and values[last + 1] == top:
            last += 1
        if last > first:
            centre = 0.5 * (first + last)  # flat (saturated) top: its middle
        else:
            # Least-squares parabola through 7 pixels: noise moves the highest pixel by one now and then, which
            # throws a 3-point vertex off by up to a pixel on lines a few pixels wide.
            width = min(3, index, values.size - 1 - index)
            offsets = np.arange(-width, width + 1, dtype=np.float64)
            curvature, slope, _ = np.polyfit(offsets, values[index - width:index + width + 1], 2)
            delta = -slope / (2.0 * curvature) if curvature < 0 else 0.0
            centre = float(index) + (max(-1.5, min(1.5, delta)) if math.isfinite(delta) else 0.0)
        candidates.append((centre, prominence))

    selected: list[tuple[float, float]] = []
    for pixel_position, height in sorted(candidates, key=lambda item: item[1], reverse=True):
        if all(abs(pixel_position - selected_pixel) >= PEAK_SEPARATION for selected_pixel, _ in selected):
            selected.append((pixel_position, height))
    selected.sort(key=lambda item: item[0])
    return [float(pixel_position) for pixel_position, _ in selected]


def _peak_prominences(profile: np.ndarray, peaks: list[float]) -> list[float]:
    """Height above the local baseline of each found peak, for ranking (strongest first when lines are fewer)."""
    smooth, baseline, _ = _profile_statistics(profile)
    out = []
    for pixel in peaks:
        index = int(round(pixel))
        window = smooth[max(0, index - 25):index + 26]
        out.append(float(smooth[index] - min(float(np.min(window)), float(np.percentile(smooth, 50)))))
    return out


def extract_profile(frame: np.ndarray, row_start: int, row_end: int) -> dict:
    values = _validated_frame(frame)
    start = _strict_int_in_range(row_start, 'row_start', 0, values.shape[0] - 1)
    end = _strict_int_in_range(row_end, 'row_end', 0, values.shape[0] - 1)
    if end < start:
        raise ValueError('row_end must be greater than or equal to row_start')

    roi = values[start : end + 1, :]
    profile = np.mean(roi, axis=0, dtype=np.float64)
    if not np.all(np.isfinite(profile)):
        raise ValueError('extracted profile contains non-finite values')
    peaks = _find_peaks(profile)
    saturated_pixels = int(np.count_nonzero(roi >= _MAX_COUNTS))
    _, baseline, noise = _profile_statistics(profile)
    return {
        'row_start': start,
        'row_end': end,
        'profile_counts': [float(value) for value in profile],
        'peaks_pixels': peaks,
        'peak_prominences': _peak_prominences(profile, peaks),
        # For a "no lines found" explanation: how far the profile rises above its baseline, against its noise.
        'baseline_counts': baseline,
        'noise_counts': noise,
        'rise_counts': float(np.max(profile) - baseline),
        'saturated_pixels': saturated_pixels,
    }


def _derivative_has_no_fold(coefficients: np.ndarray, direction: float) -> bool:
    derivative = np.asarray(
        [
            float(index) * float(coefficients[index])
            for index in range(1, coefficients.size)
        ],
        dtype=np.float64,
    )
    if derivative.size == 0:
        return False

    samples = [-1.0, 1.0]
    if derivative.size >= 3:
        linear = float(derivative[1])
        quadratic = float(derivative[2])
        if quadratic != 0.0:
            critical = -linear / (2.0 * quadratic)
            if -1.0 < critical < 1.0:
                samples.append(critical)

    values = _polyval_ascending(
        derivative, np.asarray(samples, dtype=np.float64)
    )
    if not np.all(np.isfinite(values)):
        return False
    if direction > 0.0:
        return bool(np.all(values >= 0.0) and np.any(values > 0.0))
    return bool(np.all(values <= 0.0) and np.any(values < 0.0))


def fit_mapping(request: MappingRequest) -> dict:
    if not isinstance(request, MappingRequest):
        raise ValueError('request must be a MappingRequest')

    degree_value = request.degree
    if isinstance(degree_value, (bool, np.bool_)) or not isinstance(
        degree_value, (int, np.integer)
    ):
        raise ValueError('degree must be an integer from 1 to 3')
    degree = int(degree_value)
    if degree < 1 or degree > 3:
        raise ValueError('degree must be an integer from 1 to 3')

    if not isinstance(request.pairs, list):
        raise ValueError('pairs must be a list')

    active: list[tuple[int, LinePair, float, float]] = []
    for index, pair in enumerate(request.pairs):
        if not isinstance(pair, LinePair):
            raise ValueError('each pair must be a LinePair model')
        if not isinstance(pair.enabled, (bool, np.bool_)):
            raise ValueError('enabled must be a boolean')
        if not bool(pair.enabled):
            continue
        if isinstance(pair.pixel, (bool, np.bool_)) or isinstance(
            pair.wavelength_nm, (bool, np.bool_)
        ):
            raise ValueError('enabled line values must be numeric')
        try:
            pixel = float(pair.pixel)
            wavelength = float(pair.wavelength_nm)
        except (TypeError, ValueError) as exc:
            raise ValueError('enabled line values must be finite numbers') from exc
        if not math.isfinite(pixel) or pixel < 0.0 or pixel > float(WIDTH - 1):
            raise ValueError('enabled pixels must be finite values from 0 to 1023')
        if not math.isfinite(wavelength) or wavelength <= 0.0:
            raise ValueError('enabled wavelengths must be positive finite values')
        active.append((index, pair, pixel, wavelength))

    if len(active) < degree + 2:
        raise ValueError(
            f'at least {degree + 2} enabled reference lines are required'
        )

    pixel_values = [entry[2] for entry in active]
    wavelength_values = [entry[3] for entry in active]
    if len(set(pixel_values)) != len(pixel_values):
        raise ValueError('enabled pixel positions must be distinct')
    if len(set(wavelength_values)) != len(wavelength_values):
        raise ValueError('enabled reference wavelengths must be distinct')

    ordered = sorted(active, key=lambda entry: entry[2])
    pixels = np.asarray([entry[2] for entry in ordered], dtype=np.float64)
    wavelengths = np.asarray([entry[3] for entry in ordered], dtype=np.float64)
    differences = np.diff(wavelengths)
    if bool(np.all(differences > 0.0)):
        direction = 1.0
    elif bool(np.all(differences < 0.0)):
        direction = -1.0
    else:
        raise ValueError(
            'reference wavelengths must progress strictly monotonically with pixel position'
        )

    x_fit = (pixels - _PIXEL_ORIGIN) / _PIXEL_SCALE
    design = np.vander(x_fit, N=degree + 1, increasing=True)

    try:
        rank = int(np.linalg.matrix_rank(design))
    except np.linalg.LinAlgError as exc:
        raise ValueError('could not determine polynomial rank') from exc
    if rank != degree + 1:
        raise ValueError('reference line matrix is rank deficient')

    try:
        condition_number = float(np.linalg.cond(design))
    except np.linalg.LinAlgError as exc:
        raise ValueError('could not determine polynomial conditioning') from exc
    if not math.isfinite(condition_number):
        raise ValueError('reference line matrix has non-finite conditioning')
    if condition_number > _MAX_CONDITION_NUMBER:
        raise ValueError(
            'reference line matrix is ill-conditioned; spread the lines across the detector'
        )

    try:
        coefficients, _, least_squares_rank, _ = np.linalg.lstsq(
            design, wavelengths, rcond=None
        )
    except np.linalg.LinAlgError as exc:
        raise ValueError('polynomial least-squares fit failed') from exc

    coefficients = np.asarray(coefficients, dtype=np.float64).reshape(-1)
    if coefficients.size != degree + 1:
        raise ValueError('polynomial least-squares fit returned the wrong size')
    if int(least_squares_rank) != degree + 1:
        raise ValueError('reference line matrix is rank deficient')
    if not np.all(np.isfinite(coefficients)):
        raise ValueError('polynomial coefficients are non-finite')

    axis_pixels = np.arange(WIDTH, dtype=np.float64)
    x_axis = (axis_pixels - _PIXEL_ORIGIN) / _PIXEL_SCALE
    axis = _polyval_ascending(coefficients, x_axis)
    if not np.all(np.isfinite(axis)) or not np.all(axis > 0.0):
        raise ValueError('wavelength axis must contain only positive finite values')

    axis_differences = np.diff(axis)
    if direction > 0.0:
        if not np.all(axis_differences > 0.0):
            raise ValueError('wavelength axis is not strictly increasing over the CCD')
    else:
        if not np.all(axis_differences < 0.0):
            raise ValueError('wavelength axis is not strictly decreasing over the CCD')

    if not _derivative_has_no_fold(coefficients, direction):
        raise ValueError('wavelength mapping folds or reverses direction on the continuous CCD axis')

    input_pixels = np.asarray([entry[2] for entry in active], dtype=np.float64)
    input_wavelengths = np.asarray([entry[3] for entry in active], dtype=np.float64)
    input_x = (input_pixels - _PIXEL_ORIGIN) / _PIXEL_SCALE
    predicted = _polyval_ascending(coefficients, input_x)
    if not np.all(np.isfinite(predicted)):
        raise ValueError('predicted reference wavelengths are non-finite')
    residual_values = input_wavelengths - predicted
    if not np.all(np.isfinite(residual_values)):
        raise ValueError('fit residuals are non-finite')

    residuals = [float(value) for value in residual_values]
    rms_nm = math.sqrt(float(np.mean(residual_values * residual_values)))
    max_error_nm = float(np.max(np.abs(residual_values)))
    if not math.isfinite(rms_nm) or not math.isfinite(max_error_nm):
        raise ValueError('fit error statistics are non-finite')

    pixel_min = float(ordered[0][2])
    pixel_max = float(ordered[-1][2])
    bound_pixels = np.asarray([pixel_min, pixel_max], dtype=np.float64)
    bound_x = (bound_pixels - _PIXEL_ORIGIN) / _PIXEL_SCALE
    bound_wavelengths = _polyval_ascending(coefficients, bound_x)
    if not np.all(np.isfinite(bound_wavelengths)) or not np.all(
        bound_wavelengths > 0.0
    ):
        raise ValueError('wavelength range at the reference-line limits is invalid')

    warnings: list[str] = []
    if pixel_min > 0.0 or pixel_max < float(WIDTH - 1):
        warnings.append(
            f'Wavelengths outside the reference-line pixel range '
            f'[{pixel_min:.10g}, {pixel_max:.10g}] are extrapolated and '
            f'not covered by reference lines.'
        )
    if request.reference_source == 'synthetic':
        warnings.append(
            'reference_source=synthetic: simulated calibration reference lines '
            'only; not a lamp database or measured wavelengths.'
        )

    return {
        'degree': degree,
        'coefficients': [float(value) for value in coefficients],
        'pixel_origin': _PIXEL_ORIGIN,
        'pixel_scale': _PIXEL_SCALE,
        'wavelengths_nm': [float(value) for value in axis],
        'residuals_nm': residuals,
        'rms_nm': rms_nm,
        'max_error_nm': max_error_nm,
        'pixel_range': [pixel_min, pixel_max],
        'wavelength_range_nm': [
            float(bound_wavelengths[0]),
            float(bound_wavelengths[1]),
        ],
        'condition_number': condition_number,
        'convention': request.convention,
        'reference_name': request.reference_name,
        'reference_source': request.reference_source,
        'line_pairs': [pair.model_dump() for pair in request.pairs],
        'warnings': warnings,
    }
