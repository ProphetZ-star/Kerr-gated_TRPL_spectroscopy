"""Intensity (spectral) response of the detection: R(lambda), relative, 1 at its peak.

Counts measured at a pixel are offset + R(lambda) x light, where the offset (bias, dark current, stray light) is what
a background frame records. The response-corrected spectrum is therefore (raw - B) / R, with B the recorded
background, or without one a constant offset estimated from the spectrum's lowest pixels. Raw frames are never
changed: R is stored beside them (/calibration) and applied when spectra are shown, fitted or exported.

R comes from one of two sources:
  imported  vendor curves (CCD quantum efficiency, grating efficiency, filter transmission, ...) multiplied together;
            they depend on wavelength only, so they stay valid when the spectrometer settings change;
  measured  a lamp of known spectrum recorded through the whole detection path: R = (lamp - B) / known spectrum.
            It includes every optic and the pixel-dependent throughput, so it belongs to one spectrometer setting.
"""
import io
import re
import numpy as np

# Below this fraction of its peak the response is left undefined (NaN): dividing there would amplify noise.
RESPONSE_FLOOR = 0.05
LAMP_SMOOTH_PIXELS = 9
BASELINE_PERCENTILE = 5


def read_curve(text, name='curve'):
    """(wavelengths_nm, values) from CSV / TXT text: the first two numeric columns of every numeric row, any of
    , ; tab or space as separator; header and comment lines are skipped. Sorted by wavelength."""
    rows = []
    for line in io.StringIO(text):
        line = line.strip()
        if not line or line[0] in '#;%':
            continue
        fields = [f for f in re.split(r'[,;\t ]+', line) if f]
        try:
            numbers = [float(f) for f in fields[:2]]
        except ValueError:
            continue
        if len(numbers) == 2 and all(np.isfinite(numbers)):
            rows.append(numbers)
    if len(rows) < 2:
        raise ValueError(f'{name}: needs at least two rows of "wavelength, value"')
    data = np.asarray(rows, dtype=float)
    data = data[np.argsort(data[:, 0])]
    if np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError(f'{name}: wavelengths must be distinct')
    if not (data[:, 0] > 0).all():
        raise ValueError(f'{name}: wavelengths must be positive (nm)')
    if (data[:, 1] < 0).any():
        raise ValueError(f'{name}: values must not be negative')
    return data[:, 0], data[:, 1]


def on_axis(wavelengths, values, axis):
    """A curve sampled at the axis wavelengths; NaN outside the curve's range."""
    wavelengths = np.asarray(wavelengths, dtype=float); values = np.asarray(values, dtype=float)
    axis = np.asarray(axis, dtype=float)
    out = np.interp(axis, wavelengths, values)
    out[(axis < wavelengths.min()) | (axis > wavelengths.max())] = np.nan
    return out


def normalized(values):
    """Scaled to 1 at the peak; below RESPONSE_FLOOR of the peak, NaN."""
    values = np.asarray(values, dtype=float)
    peak = np.nanmax(values) if np.isfinite(values).any() else np.nan
    if not np.isfinite(peak) or peak <= 0:
        raise ValueError('The response has no positive values on this wavelength axis')
    out = values / peak
    out[~(out >= RESPONSE_FLOOR)] = np.nan
    return out


def combine(curves, axis):
    """Product of imported curves [(name, wavelengths, values)] on the axis, normalized."""
    if not curves:
        raise ValueError('Import at least one response curve')
    product = np.ones(len(axis))
    for name, wavelengths, values in curves:
        sampled = on_axis(wavelengths, values, axis)
        if not np.isfinite(sampled).any():
            raise ValueError(f'{name} does not overlap the calibrated range {min(axis):.0f}–{max(axis):.0f} nm')
        product = product * sampled
    return normalized(product)


def baseline(spectra):
    """Constant offset per spectrum when no background is recorded: the mean of its lowest pixels."""
    spectra = np.asarray(spectra, dtype=float)
    return np.percentile(spectra, BASELINE_PERCENTILE, axis=-1)


def _running_mean(values, width):
    values = np.asarray(values, dtype=float)
    if width <= 1 or len(values) < width:
        return values
    valid = np.isfinite(values); filled = np.where(valid, values, 0.0)
    kernel = np.ones(width)
    total = np.convolve(np.pad(filled, width // 2, mode='edge'), kernel, mode='valid')
    count = np.convolve(np.pad(valid.astype(float), width // 2, mode='edge'), kernel, mode='valid')
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(count > 0, total / count, np.nan)[:len(values)]


def lamp_response(lamp, axis, reference_wavelengths, reference_values, background=None):
    """R from a recorded lamp spectrum (mean counts per pixel) and the lamp's known spectrum: (lamp - B) / known,
    lightly smoothed (the known spectrum is smooth; the recording carries shot noise), normalized."""
    lamp = np.asarray(lamp, dtype=float)
    offset = np.asarray(background, dtype=float) if background is not None else baseline(lamp)
    signal = lamp - offset
    known = on_axis(reference_wavelengths, reference_values, axis)
    if not np.isfinite(known).any():
        raise ValueError(f'The reference spectrum does not overlap the calibrated range {min(axis):.0f}–{max(axis):.0f} nm')
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = np.where((known > 0) & np.isfinite(known), signal / known, np.nan)
    ratio[ratio <= 0] = np.nan
    return normalized(_running_mean(ratio, LAMP_SMOOTH_PIXELS))


def correct(spectra, response, background=None):
    """Response-corrected spectra (raw - B) / R, NaN where R is undefined. spectra: one spectrum or a stack;
    background: the recorded background spectrum, or None for a per-spectrum baseline offset."""
    spectra = np.asarray(spectra, dtype=float); response = np.asarray(response, dtype=float)
    offset = np.asarray(background, dtype=float) if background is not None else baseline(spectra)[..., None] if spectra.ndim > 1 else baseline(spectra)
    with np.errstate(invalid='ignore', divide='ignore'):
        return (spectra - offset) / response
