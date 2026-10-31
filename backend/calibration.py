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
    if frame.ndim != 2 or frame.shape != (HEIGHT, WIDTH):
        raise ValueError(f'frame must have shape ({HEIGHT}, {WIDTH})')
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


def _find_peaks(profile: np.ndarray) -> list[float]:
    if profile.size < 3:
        return []
    maximum = float(np.max(profile))
    if not math.isfinite(maximum) or maximum <= 0.0:
        return []
    baseline = float(np.median(profile))
    amplitude = maximum - baseline
    if not math.isfinite(amplitude) or amplitude <= 0.0:
        return []
    threshold = baseline + 0.12 * amplitude

    candidates: list[tuple[float, float]] = []
    for index in range(1, profile.size - 1):
        y0 = float(profile[index - 1])
        y1 = float(profile[index])
        y2 = float(profile[index + 1])
        if y1 <= threshold:
            continue
        if y1 < y0 or y1 <= y2:
            continue
        denominator = y0 - 2.0 * y1 + y2
        if abs(denominator) <= 1.0e-15:
            delta = 0.0
        else:
            delta = 0.5 * (y0 - y2) / denominator
        if not math.isfinite(delta):
            delta = 0.0
        delta = max(-0.5, min(0.5, delta))
        candidates.append((float(index) + delta, y1))

    selected: list[tuple[float, float]] = []
    for pixel_position, height in sorted(
        candidates, key=lambda item: item[1], reverse=True
    ):
        if all(
            abs(pixel_position - selected_pixel) >= 6.0
            for selected_pixel, _ in selected
        ):
            selected.append((pixel_position, height))
    selected.sort(key=lambda item: item[0])
    return [float(pixel_position) for pixel_position, _ in selected]


def extract_profile(frame: np.ndarray, row_start: int, row_end: int) -> dict:
    values = _validated_frame(frame)
    start = _strict_int_in_range(row_start, 'row_start', 0, HEIGHT - 1)
    end = _strict_int_in_range(row_end, 'row_end', 0, HEIGHT - 1)
    if end < start:
        raise ValueError('row_end must be greater than or equal to row_start')

    roi = values[start : end + 1, :]
    profile = np.mean(roi, axis=0, dtype=np.float64)
    if not np.all(np.isfinite(profile)):
        raise ValueError('extracted profile contains non-finite values')
    peaks = _find_peaks(profile)
    saturated_pixels = int(np.count_nonzero(roi >= _MAX_COUNTS))
    return {
        'row_start': start,
        'row_end': end,
        'profile_counts': [float(value) for value in profile],
        'peaks_pixels': peaks,
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
