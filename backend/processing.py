"""From a run's frames to a decay curve: the single place where frames are selected and averaged.

The lifetime fit, the Analysis page's decay plot (through the worker's 'kinetics' command) and the per-round
view all call kinetics(), so what is drawn is exactly what is fitted.

Each frame contributes the mean count of the wavelength window's pixels per second of exposure, optionally
after subtracting the run's recorded background (taken at the same exposure and readout). Frames at the same
delay (repeat rounds, several frames per point) are then averaged. Processing options:
  subtract_background   use the run's background spectrum
  exclude_saturated     drop every (round, delay) point that has a pixel at full scale inside the window
  exclude_unstable      drop frames taken while the CCD temperature was not stabilized
                        (not reached, stabilizing or drifting; a cooler that is off is not flagged)
  position_tolerance_um drop frames whose reported stage position is further than this from the plan
  rounds                use only these repeat rounds (None: all)
"""
import numpy as np

UNSTABLE_STATES = (1, 2, 4)  # frame/temperature_state codes: not_reached, not_stabilized, drift


def window_mask(axis, wavelength_nm, half_window_nm):
    if axis is None:raise ValueError('A valid wavelength calibration is required')
    wavelengths = np.asarray(axis, dtype=float)
    if wavelengths.ndim != 1 or not len(wavelengths) or not np.isfinite(wavelengths).all() or np.any(wavelengths <= 0):
        raise ValueError('Wavelength axis must be positive and finite')
    mask = abs(wavelengths - wavelength_nm) <= half_window_nm
    if not mask.any():raise ValueError('No pixels in the selected wavelength window')
    return mask


def _average(delays, values):
    t, inverse = np.unique(delays, return_inverse=True)
    if not len(t):return t, t.copy(), np.zeros(0, int)
    counts = np.bincount(inverse, minlength=len(t))
    return t, np.bincount(inverse, weights=values, minlength=len(t)) / counts, counts


def kinetics(block, axis, options, background=None, full_scale=None, targets_mm=None, delay_range=True, rounds=None):
    """Decay curve of a block of frames (FrameTable.block: spectra, delay_ps, index, temperature_state,
    stage_reported_mm, exposure_s). options is a FitRequest (or anything with the same attributes).

    Returns delays/values/frames (the averaged curve), per_round (the same per repeat round), saturated (the
    excluded saturated points as delay/value pairs), and counts of what was flagged and what was used."""
    spectra = np.asarray(block['spectra']); delays = np.asarray(block['delay_ps'], dtype=float)
    index = np.asarray(block['index']).reshape(-1, 3); n = len(delays)
    exposure = np.broadcast_to(np.asarray(block['exposure_s'], dtype=float), delays.shape)
    mask = window_mask(axis, options.wavelength_nm, options.half_window_nm)
    if n and spectra.shape[1:] != mask.shape:raise ValueError('Fit spectrum must match the axis and contain only finite counts')
    if not np.isfinite(delays).all():raise ValueError('delay_ps must be finite')
    if not (np.isfinite(exposure).all() and np.all(exposure > 0)):raise ValueError('exposure_s must be finite and > 0')
    window = np.asarray(spectra[:, mask], dtype=float)
    if not np.isfinite(window).all():raise ValueError('Fit spectrum must match the axis and contain only finite counts')
    subtract = bool(getattr(options, 'subtract_background', False))
    if subtract:
        if background is None:raise ValueError('This experiment has no recorded background to subtract')
        background = np.asarray(background, dtype=float)
        if background.shape != mask.shape:raise ValueError('The recorded background does not match the spectra')
        values = (window - background[mask]).mean(axis=1) / exposure
    else:
        values = window.mean(axis=1) / exposure
    if not np.isfinite(values).all():raise ValueError('Exposure-normalized counts are non-finite')
    keep = np.ones(n, dtype=bool)
    round_index = index[:, 0]
    chosen = rounds if rounds is not None else getattr(options, 'rounds', None)
    if chosen:keep &= np.isin(round_index, np.asarray(list(chosen)))
    # Temperature and position flags are counted whether or not they exclude anything, so the UI can show them.
    state = np.asarray(block.get('temperature_state', np.full(n, -1)))
    unstable = np.isin(state, UNSTABLE_STATES)
    if getattr(options, 'exclude_unstable', False):keep &= ~unstable
    error_um = np.full(n, np.nan)
    if targets_mm is not None and 'stage_reported_mm' in block and n:
        targets = np.asarray(targets_mm, dtype=float); points = index[:, 1]
        valid = (points >= 0) & (points < len(targets))
        error_um[valid] = np.abs(np.asarray(block['stage_reported_mm'], dtype=float)[valid] - targets[points[valid]]) * 1000
    tolerance = getattr(options, 'position_tolerance_um', None)
    off_position = np.zeros(n, dtype=bool) if tolerance is None else np.nan_to_num(error_um, nan=0.0) > tolerance
    if tolerance is not None:keep &= ~off_position
    # Saturation is judged per (round, delay) point: one saturated frame removes that point of that round.
    saturated_point = np.zeros(n, dtype=bool); saturated_points = 0
    if full_scale is not None and n:
        hit = (spectra[:, mask] >= full_scale).any(axis=1)
        if hit.any():
            pairs = np.stack([round_index.astype(float), delays], axis=1)
            unique, inverse = np.unique(pairs, axis=0, return_inverse=True)
            inverse = inverse.reshape(-1)
            point_hit = np.bincount(inverse, weights=hit.astype(float), minlength=len(unique)) > 0
            saturated_point = point_hit[inverse]; saturated_points = int(point_hit.sum())
    excluded_saturated = saturated_point & keep if getattr(options, 'exclude_saturated', True) else np.zeros(n, dtype=bool)
    keep &= ~excluded_saturated
    in_range = np.ones(n, dtype=bool)
    if delay_range:
        if getattr(options, 'min_delay_ps', None) is not None:in_range &= delays >= options.min_delay_ps
        if getattr(options, 'max_delay_ps', None) is not None:in_range &= delays <= options.max_delay_ps
    used = keep & in_range
    t, y, frames = _average(delays[used], values[used])
    per_round = []
    for r in np.unique(round_index[used]).tolist():
        rt, ry, _ = _average(delays[used & (round_index == r)], values[used & (round_index == r)])
        per_round.append(dict(round=int(r), delays_ps=rt.tolist(), values=ry.tolist()))
    st, sy, _ = _average(delays[excluded_saturated & in_range], values[excluded_saturated & in_range])
    finite_error = error_um[np.isfinite(error_um)]
    return dict(delays_ps=t.tolist(), values=y.tolist(), frames=frames.tolist(), per_round=per_round,
                saturated=dict(delays_ps=st.tolist(), values=sy.tolist()),
                counts=dict(frames_total=int(n), frames_used=int(used.sum()), saturated_points=saturated_points,
                            excluded_saturated_frames=int(excluded_saturated.sum()), unstable_frames=int(unstable.sum()),
                            off_position_frames=int(off_position.sum()),
                            position_error_max_um=float(finite_error.max()) if finite_error.size else None),
                rounds_available=[int(r) for r in np.unique(round_index).tolist()],
                rounds_used=[int(r) for r in np.unique(round_index[used]).tolist()],
                background_available=background is not None)


def processing_of(options):
    """The processing choices of a fit request, as stored with the fit."""
    return dict(subtract_background=bool(getattr(options, 'subtract_background', False)),
                exclude_saturated=bool(getattr(options, 'exclude_saturated', True)),
                exclude_unstable=bool(getattr(options, 'exclude_unstable', False)),
                position_tolerance_um=getattr(options, 'position_tolerance_um', None))
